"""Create-once machine outputs and Markdown derived only from revalidated results."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import tempfile
import unicodedata
from decimal import Decimal
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    EvaluationAnswer,
    EvaluationQuestion,
    ExecutionTrace,
    ExperimentManifest,
    FailureRecord,
    MechanicalValidation,
    QuestionFamily,
    StructuredPreservationProof,
    SystemKey,
    TrustStatus,
)
from eve_relation_rag.experiments.rag_value_ablation.metrics import (
    AssociationMetrics,
    EfficiencyObservation,
    EfficiencySummary,
    GroundingMetrics,
    RatioMetric,
    RefusalMetrics,
    RefusalObservation,
    RetrievalMetrics,
    StructuredMetrics,
    summarize_efficiency,
    summarize_refusal,
)
from eve_relation_rag.experiments.rag_value_ablation.systems import (
    ALL_SYSTEM_KEYS,
    LLM_SYSTEM_KEYS,
    ComparisonInputRecord,
    validate_execution_trace,
    validate_llm_comparison_inputs,
)
from eve_relation_rag.experiments.rag_value_ablation.trust import (
    RunTrustDecision,
    TrustDecisionError,
    validate_run_authority,
)
from eve_relation_rag.literature.contracts import Sha256, StableToken, StrictFrozenSchema
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256


class ReportingError(RuntimeError):
    """Raised for inconsistent, noncanonical, unsafe, or overwriting report operations."""


class PerQuestionEvaluation(StrictFrozenSchema):
    """All available machine and human metrics for one system/question pair."""

    result_schema_version: Literal["rag-value-per-question-v1"] = (
        "rag-value-per-question-v1"
    )
    system_key: SystemKey
    question_id: StableToken
    family: QuestionFamily
    trust_status: TrustStatus
    status: Literal[
        "completed", "refused", "retrieval_only", "not_applicable", "failed"
    ]
    question_text_sha256: Sha256
    evidence_pack_sha256: Sha256 | None = None
    answer: EvaluationAnswer | None = None
    answer_sha256: Sha256 | None = None
    deterministic_output_text: str | None = Field(
        default=None,
        min_length=1,
        max_length=1_000_000,
    )
    deterministic_output_sha256: Sha256 | None = None
    execution_trace: ExecutionTrace
    mechanical_validation: MechanicalValidation | None = None
    structured_preservation: StructuredPreservationProof | None = None
    structured_metrics: StructuredMetrics | None = None
    source_reported_association_metrics: AssociationMetrics | None = None
    cross_source_association_metrics: AssociationMetrics | None = None
    retrieval_metrics: RetrievalMetrics | None = None
    grounding_metrics: GroundingMetrics | None = None
    refusal_observation: RefusalObservation | None = None
    efficiency: EfficiencyObservation | None = None
    result_sha256: Sha256

    @field_validator("deterministic_output_text")
    @classmethod
    def canonical_multiline_output(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip() or unicodedata.normalize("NFC", value) != value:
            raise ValueError("deterministic output must be nonempty NFC text")
        if any(
            unicodedata.category(character).startswith("C") and character != "\n"
            for character in value
        ):
            raise ValueError("deterministic output permits LF but no other control text")
        return value

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if (
            self.execution_trace.system_key != self.system_key
            or self.execution_trace.question_id != self.question_id
            or self.execution_trace.status != self.status
        ):
            raise ValueError("execution trace does not match per-question result")
        if self.efficiency is not None and (
            self.efficiency.system_key != self.system_key
            or self.efficiency.question_id != self.question_id
        ):
            raise ValueError("efficiency observation does not match per-question result")
        if self.refusal_observation is not None and (
            self.refusal_observation.question_id != self.question_id
        ):
            raise ValueError("refusal observation does not match per-question result")
        if self.refusal_observation is not None and (
            self.refusal_observation.abstained != (self.status == "refused")
        ):
            raise ValueError("refusal observation abstention does not match result status")
        if self.refusal_observation is not None:
            origin = self.refusal_observation.refusal_origin
            generation_called = self.execution_trace.generation_call_count == 1
            if origin == "model_abstention" and not generation_called:
                raise ValueError("model-abstention origin requires one generation call")
            if origin in {"shared_scope_policy", "system_route_policy"} and (
                generation_called
                or self.execution_trace.refusal_stage != "request_validation"
            ):
                raise ValueError(
                    "policy-refusal origin requires request-validation refusal before generation"
                )
        if (self.answer is None) != (self.answer_sha256 is None):
            raise ValueError("answer payload and checksum must be supplied together")
        if self.answer is not None and self.answer_sha256 != canonical_json_sha256(self.answer):
            raise ValueError("answer checksum does not match the persisted answer payload")
        if (self.deterministic_output_text is None) != (
            self.deterministic_output_sha256 is None
        ):
            raise ValueError(
                "deterministic output text and checksum must be supplied together"
            )
        if self.deterministic_output_text is not None and (
            self.deterministic_output_sha256
            != hashlib.sha256(self.deterministic_output_text.encode("utf-8")).hexdigest()
        ):
            raise ValueError("deterministic output checksum does not match its text")
        if self.system_key not in {"S4", "S5"} and (
            self.deterministic_output_text is not None
        ):
            raise ValueError("deterministic output belongs only to S4 or S5")
        if self.status == "completed" and self.answer is not None and self.answer.abstained:
            raise ValueError("completed result cannot contain an abstained answer")
        if self.status == "refused" and self.answer is not None and not self.answer.abstained:
            raise ValueError("refused result requires an abstained answer")
        if self.status in {"failed", "not_applicable"} and any(
            value is not None
            for value in (
                self.evidence_pack_sha256,
                self.answer,
                self.answer_sha256,
                self.deterministic_output_text,
                self.deterministic_output_sha256,
                self.mechanical_validation,
                self.structured_preservation,
                self.structured_metrics,
                self.source_reported_association_metrics,
                self.cross_source_association_metrics,
                self.retrieval_metrics,
                self.grounding_metrics,
                self.refusal_observation,
                self.efficiency,
            )
        ):
            raise ValueError("failed/not-applicable result must not contain scored output")
        if self.status == "retrieval_only" and any(
            value is not None
            for value in (
                self.answer,
                self.answer_sha256,
                self.deterministic_output_text,
                self.deterministic_output_sha256,
                self.mechanical_validation,
                self.structured_preservation,
                self.source_reported_association_metrics,
                self.cross_source_association_metrics,
                self.grounding_metrics,
                self.refusal_observation,
            )
        ):
            raise ValueError("retrieval-only result cannot contain generated-answer output")
        if self.status == "retrieval_only" and self.evidence_pack_sha256 is None:
            raise ValueError("retrieval-only result requires a checksum-bound evidence pack")
        if self.result_sha256 != _self_sha256(self, "result_sha256"):
            raise ValueError("per-question result checksum does not match")
        return self


def build_per_question_evaluation(**values: object) -> PerQuestionEvaluation:
    """Build one canonical self-checksummed evaluation record."""

    payload = {
        "result_schema_version": "rag-value-per-question-v1",
        "evidence_pack_sha256": None,
        "answer": None,
        "answer_sha256": None,
        "deterministic_output_text": None,
        "deterministic_output_sha256": None,
        "mechanical_validation": None,
        "structured_preservation": None,
        "structured_metrics": None,
        "source_reported_association_metrics": None,
        "cross_source_association_metrics": None,
        "retrieval_metrics": None,
        "grounding_metrics": None,
        "refusal_observation": None,
        "efficiency": None,
        **values,
    }
    payload.pop("result_sha256", None)
    return PerQuestionEvaluation.model_validate(
        {**payload, "result_sha256": canonical_json_sha256(payload)}
    )


class BenchmarkRun(StrictFrozenSchema):
    """Complete canonical result set used as the sole report source."""

    run_schema_version: Literal["rag-value-run-v1"] = "rag-value-run-v1"
    manifest: ExperimentManifest
    human_review_status: Literal["pending", "complete", "not_required"]
    results: tuple[PerQuestionEvaluation, ...] = Field(min_length=7)
    comparison_eligible_question_ids: tuple[StableToken, ...] = ()
    comparison_inputs: tuple[ComparisonInputRecord, ...] = ()
    failures: tuple[FailureRecord, ...] = ()
    run_sha256: Sha256

    @model_validator(mode="after")
    def validate_run(self) -> Self:
        order = {key: index for index, key in enumerate(ALL_SYSTEM_KEYS)}
        keys = tuple((result.system_key, result.question_id) for result in self.results)
        expected_order = tuple(sorted(keys, key=lambda item: (order[item[0]], item[1])))
        if keys != expected_order or len(keys) != len(set(keys)):
            raise ValueError("per-question results must use canonical unique system/question order")
        system_by_key = {system.system_key: system for system in self.manifest.systems}
        question_sets = {
            system_key: tuple(
                result.question_id
                for result in self.results
                if result.system_key == system_key
            )
            for system_key in ALL_SYSTEM_KEYS
        }
        first_questions = question_sets["S0"]
        if not first_questions or any(
            question_sets[system_key] != first_questions for system_key in ALL_SYSTEM_KEYS
        ):
            raise ValueError("every system must retain the identical question set")
        for question_id in first_questions:
            checksums = {
                result.question_text_sha256
                for result in self.results
                if result.question_id == question_id
            }
            if len(checksums) != 1:
                raise ValueError("question wording checksum differs between systems")
        if self.comparison_eligible_question_ids != tuple(
            sorted(set(self.comparison_eligible_question_ids))
        ):
            raise ValueError("comparison-eligible question IDs must be sorted and unique")
        if bool(self.comparison_eligible_question_ids) != bool(self.comparison_inputs):
            raise ValueError("comparison eligibility and inputs must be supplied together")
        if set(self.comparison_eligible_question_ids) - set(first_questions):
            raise ValueError("comparison eligibility references an unknown question")
        if self.comparison_inputs:
            validate_llm_comparison_inputs(self.comparison_inputs, self.manifest.systems)
            expected_comparison_order = tuple(
                (question_id, system_key)
                for question_id in self.comparison_eligible_question_ids
                for system_key in LLM_SYSTEM_KEYS
            )
            observed_comparison_order = tuple(
                (record.question_id, record.system_key)
                for record in self.comparison_inputs
            )
            if observed_comparison_order != expected_comparison_order:
                raise ValueError("comparison inputs differ from the declared eligible set")
            result_by_key = {
                (result.system_key, result.question_id): result for result in self.results
            }
            for record in self.comparison_inputs:
                result = result_by_key[(record.system_key, record.question_id)]
                if (
                    result.question_text_sha256 != record.question_text_sha256
                    or result.evidence_pack_sha256 != record.evidence_pack_sha256
                    or result.execution_trace.generation_call_count != 1
                ):
                    raise ValueError("comparison input differs from persisted result identity")
        if self.manifest.phase == "phase2_synthetic" and not self.comparison_inputs:
            raise ValueError("Phase 2 requires checksum-bound comparison inputs")
        if self.manifest.phase == "phase3_retrieval" and self.comparison_inputs:
            raise ValueError("retrieval-only Phase 3 cannot contain LLM comparison inputs")
        if self.manifest.phase == "phase3_retrieval":
            for result in self.results:
                expected = {
                    "S0": {"not_applicable"},
                    "S1": {"retrieval_only", "refused", "failed"},
                    "S2": {"retrieval_only", "refused", "failed"},
                    "S3": {"retrieval_only", "refused", "failed"},
                    "S4": {"completed", "refused", "failed"},
                    "S5": {"not_applicable"},
                    "S6": {"not_applicable"},
                }[result.system_key]
                if result.status not in expected:
                    raise ValueError("Phase 3 result violates the retrieval-only execution matrix")
        elif any(result.status == "retrieval_only" for result in self.results):
            raise ValueError("retrieval-only results belong only to Phase 3")
        for result in self.results:
            if result.trust_status != self.manifest.trust_status:
                raise ValueError("per-question trust status differs from manifest")
            validate_execution_trace(system_by_key[result.system_key], result.execution_trace)
            if result.execution_trace.generation_call_count:
                if (
                    result.evidence_pack_sha256 is None
                    or result.answer is None
                    or result.answer_sha256 is None
                    or result.mechanical_validation is None
                ):
                    raise ValueError("generated result lacks evidence/answer validation")
            elif any(
                value is not None
                for value in (
                    result.answer,
                    result.answer_sha256,
                    result.mechanical_validation,
                )
            ):
                raise ValueError("non-generated result cannot contain an answer payload")
            if result.status == "completed" and result.system_key in LLM_SYSTEM_KEYS:
                if (
                    result.evidence_pack_sha256 is None
                    or result.answer is None
                    or result.answer_sha256 is None
                    or result.mechanical_validation is None
                ):
                    raise ValueError("completed LLM result lacks evidence/answer validation")
            if result.system_key == "S4" and (
                result.answer is not None
                or result.answer_sha256 is not None
                or result.mechanical_validation is not None
            ):
                raise ValueError("S4 must not carry an LLM answer or validation")
            if (
                result.status == "completed"
                and result.system_key in {"S4", "S5"}
                and result.deterministic_output_text is None
            ):
                raise ValueError("completed S4/S5 result lacks deterministic output")
            if result.status == "completed" and result.system_key == "S5" and (
                result.structured_preservation is None
                or not result.structured_preservation.preserved
            ):
                raise ValueError("completed S5 result must preserve the structured result exactly")
            if result.system_key != "S5" and result.structured_preservation is not None:
                raise ValueError("structured preservation proof belongs only to S5")
        failed_results = sum(result.status == "failed" for result in self.results)
        if self.manifest.trust_status == "failed":
            if not self.failures and not failed_results:
                raise ValueError("failed run requires a failure record or failed result")
        elif self.failures or failed_results:
            raise ValueError("non-failed run cannot omit recorded execution failures")
        if self.human_review_status == "complete" and any(
            result.status == "completed"
            and result.system_key in LLM_SYSTEM_KEYS
            and result.grounding_metrics is None
            for result in self.results
        ):
            raise ValueError("complete human review requires grounding metrics for all LLM answers")
        if (
            self.manifest.trust_status == "trusted"
            and self.manifest.phase != "phase3_retrieval"
            and self.human_review_status == "not_required"
        ):
            raise ValueError("trusted results cannot waive human review")
        if (
            self.manifest.phase == "phase6_analysis"
            and self.human_review_status != "complete"
        ):
            raise ValueError("Phase 6 analysis requires complete human review")
        if any(
            result.efficiency is not None and result.efficiency.cost is not None
            for result in self.results
        ) and self.manifest.pricing_manifest_sha256 is None:
            raise ValueError("cost metrics require an approved pricing manifest checksum")
        if self.run_sha256 != _self_sha256(self, "run_sha256"):
            raise ValueError("benchmark run checksum does not match")
        return self


def build_benchmark_run(**values: object) -> BenchmarkRun:
    """Build one canonical self-checksummed benchmark run."""

    payload = {
        "run_schema_version": "rag-value-run-v1",
        "comparison_eligible_question_ids": (),
        "comparison_inputs": (),
        "failures": (),
        **values,
    }
    payload.pop("run_sha256", None)
    return BenchmarkRun.model_validate(
        {**payload, "run_sha256": canonical_json_sha256(payload)}
    )


def write_benchmark_outputs(
    output_directory: Path,
    run: BenchmarkRun,
    trust_decision: RunTrustDecision,
    *,
    markdown_report_path: Path | None = None,
    allow_test_output: bool = False,
) -> None:
    """Atomically create all machine files once and optionally a trusted formal report."""

    if type(run) is not BenchmarkRun:
        raise ReportingError("benchmark output requires an exact BenchmarkRun")
    try:
        run = BenchmarkRun.model_validate_json(run.model_dump_json())
        run = validate_run_authority(run, trust_decision)
    except TrustDecisionError as exc:
        raise ReportingError("benchmark output lacks matching runtime authority") from exc
    except Exception as exc:
        raise ReportingError("benchmark run failed checksum revalidation") from exc
    if run.manifest.trust_status == "test_only" and not allow_test_output:
        raise ReportingError("test-only output requires explicit allow_test_output")
    if run.manifest.trust_status == "failed":
        raise ReportingError("failed runs have no implemented publication authority")
    if run.manifest.trust_status == "trusted" and allow_test_output:
        raise ReportingError("trusted output cannot use a test publication override")
    if markdown_report_path is not None and (
        run.manifest.trust_status != "trusted" or run.human_review_status != "complete"
    ):
        raise ReportingError("formal Markdown requires trusted, complete human-reviewed results")
    if output_directory.exists() or output_directory.is_symlink():
        raise ReportingError("benchmark output directory already exists")
    if markdown_report_path is not None and (
        markdown_report_path.exists() or markdown_report_path.is_symlink()
    ):
        raise ReportingError("Markdown report path already exists")
    try:
        parent = output_directory.parent.resolve(strict=True)
    except OSError as exc:
        raise ReportingError("benchmark output parent does not exist") from exc
    files = _all_output_files(run)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_directory.name}.", dir=parent))
    try:
        for relative_path, content in files.items():
            target = temporary / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        os.rename(temporary, output_directory)
    except Exception as exc:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise ReportingError("failed to create benchmark outputs atomically") from exc
    if markdown_report_path is not None:
        try:
            _write_new_file(
                markdown_report_path,
                generate_markdown_report(output_directory, trust_decision),
            )
        except Exception:
            shutil.rmtree(output_directory)
            raise


def load_benchmark_run(output_directory: Path) -> BenchmarkRun:
    """Reload core records and reject any missing, extra, or manually edited derived file."""

    try:
        manifest = ExperimentManifest.model_validate_json(
            (output_directory / "experiment_manifest.json").read_bytes()
        )
        unordered_results = tuple(
            PerQuestionEvaluation.model_validate_json(path.read_bytes())
            for path in sorted((output_directory / "per_question").rglob("*.json"))
        )
        system_order = {key: index for index, key in enumerate(ALL_SYSTEM_KEYS)}
        results = tuple(
            sorted(
                unordered_results,
                key=lambda result: (system_order[result.system_key], result.question_id),
            )
        )
        failures = tuple(
            FailureRecord.model_validate_json(line)
            for line in (output_directory / "failures.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line
        )
        summary = json.loads((output_directory / "summary.json").read_text(encoding="utf-8"))
        run = BenchmarkRun(
            manifest=manifest,
            human_review_status=summary["human_review_status"],
            results=results,
            comparison_eligible_question_ids=tuple(
                summary["comparison_eligible_question_ids"]
            ),
            comparison_inputs=tuple(
                ComparisonInputRecord.model_validate(value)
                for value in summary["comparison_inputs"]
            ),
            failures=failures,
            run_sha256=summary["run_sha256"],
        )
    except Exception as exc:
        raise ReportingError("benchmark machine results cannot be reconstructed") from exc
    expected_files = _all_output_files(run)
    for relative_path, expected in expected_files.items():
        try:
            observed = (output_directory / relative_path).read_bytes()
        except OSError as exc:
            raise ReportingError(f"benchmark result is missing: {relative_path}") from exc
        if observed != expected:
            raise ReportingError(f"benchmark result is not canonical: {relative_path}")
    actual_files = {
        path.relative_to(output_directory).as_posix()
        for path in output_directory.rglob("*")
        if path.is_file()
    }
    if actual_files != set(expected_files):
        raise ReportingError("benchmark directory contains missing or extra files")
    return run


def generate_markdown_report(
    output_directory: Path,
    trust_decision: RunTrustDecision,
) -> bytes:
    """Generate the formal report exclusively from a fully revalidated machine directory."""

    run = load_benchmark_run(output_directory)
    try:
        run = validate_run_authority(run, trust_decision)
    except TrustDecisionError as exc:
        raise ReportingError("formal report lacks matching runtime authority") from exc
    if run.manifest.trust_status != "trusted" or run.human_review_status != "complete":
        raise ReportingError("formal report requires trusted, complete human-reviewed results")
    summary = _summary_payload(run)
    lines = [
        "# EndoViHo-RAG value ablation",
        "",
        "This report was generated deterministically from revalidated machine-readable results.",
        "",
        f"- Experiment: `{run.manifest.experiment_key}`",
        f"- Source commit: `{run.manifest.source_commit}`",
        f"- Trust status: `{run.manifest.trust_status}`",
        f"- Human review: `{run.human_review_status}`",
        "",
        "## System coverage",
        "",
        "| System | Questions | Completed | Retrieval only | Refused | Not applicable | Failed |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    system_rows = summary["systems"]
    if not isinstance(system_rows, list):
        raise ReportingError("summary systems are invalid")
    for row in system_rows:
        lines.append(
            f"| `{row['system_key']}` | {row['question_count']} | {row['completed_count']} | "
            f"{row['retrieval_only_count']} | {row['refused_count']} | "
            f"{row['not_applicable_count']} | {row['failed_count']} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            "The report does not change production settings. Conclusions require the paired "
            "metrics and human review represented in the machine files.",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def _all_output_files(run: BenchmarkRun) -> dict[str, bytes]:
    files: dict[str, bytes] = {
        "experiment_manifest.json": _json_line(run.manifest),
        "question_schema.json": canonical_json_bytes(EvaluationQuestion.model_json_schema())
        + b"\n",
        "questions_template.jsonl": b"\n",
        "human_review_template.csv": _human_review_template(),
        "failures.jsonl": b"".join(
            _json_line(failure)
            for failure in sorted(
                run.failures,
                key=lambda row: (
                    row.system_key or "",
                    row.question_id or "",
                    row.stage,
                    row.error_code,
                ),
            )
        ),
    }
    for system in run.manifest.systems:
        files[f"systems/{system.system_key}.json"] = _json_line(system)
    for result in run.results:
        digest = hashlib.sha256(result.question_id.encode("utf-8")).hexdigest()
        files[f"per_question/{result.system_key}/{digest}.json"] = _json_line(result)
    files.update(_derived_files(run))
    if run.manifest.trust_status == "test_only":
        files["TEST_ONLY_REPORT.md"] = _test_only_report(run)
    return dict(sorted(files.items()))


def _test_only_report(run: BenchmarkRun) -> bytes:
    """Render a conspicuous synthetic-only summary from the canonical in-memory run."""

    lines = [
        "# TEST ONLY — synthetic RAG-value harness",
        "",
        "This report validates software behavior only. It contains no trusted scientific result, "
        "human Gold, approved Oracle evidence, or production recommendation.",
        "",
        f"- Experiment: `{run.manifest.experiment_key}`",
        f"- Manifest SHA-256: `{run.manifest.manifest_sha256}`",
        f"- Run SHA-256: `{run.run_sha256}`",
        f"- Result records: {len(run.results)}",
        f"- Failures: {len(run.failures)}",
        "- Trust status: `test_only`",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def _derived_files(run: BenchmarkRun) -> dict[str, bytes]:
    retrieval_rows = [_retrieval_row(result) for result in run.results]
    answer_rows = [_answer_row(result) for result in run.results]
    refusal_rows = [_refusal_row(result) for result in run.results]
    latency_rows = [_latency_row(result) for result in run.results]
    paired_question_ids = frozenset(_paired_llm_question_ids(run))
    paired_answer_rows = [
        {"paired_llm_question": True, **_answer_row(result)}
        for result in run.results
        if result.system_key in LLM_SYSTEM_KEYS
        and result.question_id in paired_question_ids
    ]
    paired_answer_fieldnames = (
        "paired_llm_question",
        *tuple(answer_rows[0]),
    )
    refusal_summary_rows = [
        _refusal_summary_row(run, key) for key in ALL_SYSTEM_KEYS
    ]
    quality_latency_rows = [_quality_latency_row(run, key) for key in ALL_SYSTEM_KEYS]
    return {
        "summary.json": canonical_json_bytes(_summary_payload(run)) + b"\n",
        "retrieval_metrics.csv": _csv_bytes(retrieval_rows),
        "answer_metrics.csv": _csv_bytes(answer_rows),
        "refusal_metrics.csv": _csv_bytes(refusal_rows),
        "latency_metrics.csv": _csv_bytes(latency_rows),
        "plot_no_rag_vs_rag.csv": _csv_bytes(
            paired_answer_rows,
            fieldnames=paired_answer_fieldnames,
        ),
        "plot_structured_correctness.csv": _csv_bytes(answer_rows),
        "plot_claim_support.csv": _csv_bytes(answer_rows),
        "plot_refusal.csv": _csv_bytes(refusal_summary_rows),
        "plot_retrieval_quality.csv": _csv_bytes(retrieval_rows),
        "plot_quality_latency.csv": _csv_bytes(quality_latency_rows),
    }


def _summary_payload(run: BenchmarkRun) -> dict[str, object]:
    paired_question_ids = _paired_llm_question_ids(run)
    matched_refusal_question_ids = _matched_llm_system_refusal_question_ids(run)
    paired_efficiency_question_ids = _paired_llm_efficiency_question_ids(run)
    systems: list[dict[str, object]] = []
    for key in ALL_SYSTEM_KEYS:
        rows = tuple(result for result in run.results if result.system_key == key)
        refusal = _summarize_refusal_or_none(rows)
        efficiency = _summarize_efficiency_or_none(rows)
        paired_rows = tuple(
            row
            for row in rows
            if key in LLM_SYSTEM_KEYS and row.question_id in paired_question_ids
        )
        paired_efficiency_rows = tuple(
            row
            for row in rows
            if key in LLM_SYSTEM_KEYS
            and row.question_id in paired_efficiency_question_ids
        )
        matched_refusal_rows = tuple(
            row
            for row in rows
            if key in LLM_SYSTEM_KEYS
            and row.question_id in matched_refusal_question_ids
        )
        systems.append(
            {
                "system_key": key,
                "question_count": len(rows),
                "completed_count": sum(row.status == "completed" for row in rows),
                "retrieval_only_count": sum(
                    row.status == "retrieval_only" for row in rows
                ),
                "refused_count": sum(row.status == "refused" for row in rows),
                "not_applicable_count": sum(row.status == "not_applicable" for row in rows),
                "failed_count": sum(row.status == "failed" for row in rows),
                "paired_llm_question_count": len(paired_rows),
                "paired_llm_completed_answer_count": sum(
                    row.status == "completed" for row in paired_rows
                ),
                "paired_llm_refused_answer_count": sum(
                    row.status == "refused" for row in paired_rows
                ),
                "refusal_observation_count": sum(
                    row.refusal_observation is not None for row in rows
                ),
                "refusal_metrics": _model_payload(refusal),
                "matched_llm_system_refusal_observation_count": sum(
                    row.refusal_observation is not None
                    for row in matched_refusal_rows
                ),
                "matched_llm_system_refusal_metrics": _model_payload(
                    _summarize_refusal_or_none(matched_refusal_rows)
                ),
                "efficiency_observation_count": sum(
                    row.efficiency is not None for row in rows
                ),
                "efficiency_summary": _model_payload(efficiency),
                "paired_llm_efficiency_observation_count": sum(
                    row.efficiency is not None for row in paired_efficiency_rows
                ),
                "paired_llm_efficiency_summary": _model_payload(
                    _summarize_efficiency_or_none(paired_efficiency_rows)
                ),
            }
        )
    return {
        "summary_schema_version": "rag-value-summary-v1",
        "experiment_manifest_sha256": run.manifest.manifest_sha256,
        "run_sha256": run.run_sha256,
        "trust_status": run.manifest.trust_status,
        "human_review_status": run.human_review_status,
        "comparison_eligible_question_ids": run.comparison_eligible_question_ids,
        "comparison_inputs": run.comparison_inputs,
        "paired_llm_question_ids": paired_question_ids,
        "paired_llm_question_count": len(paired_question_ids),
        "matched_llm_system_refusal_question_ids": matched_refusal_question_ids,
        "matched_llm_system_refusal_question_count": len(
            matched_refusal_question_ids
        ),
        "paired_llm_efficiency_question_ids": paired_efficiency_question_ids,
        "paired_llm_efficiency_question_count": len(
            paired_efficiency_question_ids
        ),
        "systems": systems,
        "failure_count": len(run.failures),
    }


def _retrieval_row(result: PerQuestionEvaluation) -> dict[str, object]:
    metric = result.retrieval_metrics
    return {
        "system_key": result.system_key,
        "question_id": result.question_id,
        "family": result.family,
        "trust_status": result.trust_status,
        "status": result.status,
        "eligible": metric is not None,
        "recall_at_1": "" if metric is None else metric.recall_at_1,
        "recall_at_3": "" if metric is None else metric.recall_at_3,
        "recall_at_5": "" if metric is None else metric.recall_at_5,
        "recall_at_10": "" if metric is None else metric.recall_at_10,
        "mrr_at_10": "" if metric is None else metric.mrr_at_10,
        "ndcg_at_10": "" if metric is None else metric.ndcg_at_10,
        "excluded_hit_count_at_10": (
            "" if metric is None else metric.excluded_hit_count_at_10
        ),
    }


def _answer_row(result: PerQuestionEvaluation) -> dict[str, object]:
    structured = result.structured_metrics
    grounding = result.grounding_metrics
    return {
        "system_key": result.system_key,
        "question_id": result.question_id,
        "family": result.family,
        "trust_status": result.trust_status,
        "status": result.status,
        "deterministic_output_available": (
            result.deterministic_output_text is not None
        ),
        "deterministic_output_sha256": (
            ""
            if result.deterministic_output_sha256 is None
            else result.deterministic_output_sha256
        ),
        "structured_eligible": structured is not None,
        "numeric_exact_match": _optional(structured, "numeric_exact_match"),
        "metric_key_exact_match": _optional(structured, "metric_key_exact_match"),
        "record_set_exact": _optional(structured, "record_set_exact"),
        "assembly_set_exact": _optional(structured, "assembly_set_exact"),
        "sequence_set_exact": _optional(structured, "sequence_set_exact"),
        "locus_set_exact": _optional(structured, "locus_set_exact"),
        "coordinate_set_exact": _optional(structured, "coordinate_set_exact"),
        "detection_call_set_exact": _optional(
            structured, "detection_call_set_exact"
        ),
        **_association_columns(
            "exact_association",
            None if structured is None else structured.association_metrics,
        ),
        "relation_contract_exact": _optional(structured, "relation_contract_exact"),
        "relation_assertion_manifest_exact": _optional(
            structured, "relation_assertion_manifest_exact"
        ),
        "missing_record_count": _optional(structured, "missing_record_count"),
        "extra_record_count": _optional(structured, "extra_record_count"),
        "missing_coordinate_count": _optional(
            structured, "missing_coordinate_count"
        ),
        "extra_coordinate_count": _optional(structured, "extra_coordinate_count"),
        "identifier_preservation": _ratio_value(structured, "identifier_preservation"),
        "identifier_preservation_undefined_reason": _ratio_undefined_reason(
            structured, "identifier_preservation"
        ),
        "all_identifiers_exact": _optional(structured, "all_identifiers_exact"),
        "release_provenance_exact": _optional(structured, "release_provenance_exact"),
        "invented_identifier_count": _optional(structured, "invented_identifier_count"),
        "structured_required_limitation_coverage": _ratio_value(
            structured, "required_limitation_coverage"
        ),
        "structured_required_limitation_coverage_undefined_reason": (
            _ratio_undefined_reason(structured, "required_limitation_coverage")
        ),
        **_association_columns(
            "source_reported_association",
            result.source_reported_association_metrics,
        ),
        **_association_columns(
            "cross_source_association",
            result.cross_source_association_metrics,
        ),
        "grounding_eligible": grounding is not None,
        "required_fact_coverage": _ratio_value(grounding, "required_fact_coverage"),
        "required_fact_coverage_undefined_reason": _ratio_undefined_reason(
            grounding, "required_fact_coverage"
        ),
        "structured_fact_preservation": _ratio_value(
            grounding, "structured_fact_preservation"
        ),
        "structured_fact_preservation_undefined_reason": _ratio_undefined_reason(
            grounding, "structured_fact_preservation"
        ),
        "fully_supported_claim_rate": _ratio_value(
            grounding, "fully_supported_claim_rate"
        ),
        "fully_supported_claim_rate_undefined_reason": _ratio_undefined_reason(
            grounding, "fully_supported_claim_rate"
        ),
        "partially_supported_claim_rate": _ratio_value(
            grounding, "partially_supported_claim_rate"
        ),
        "partially_supported_claim_rate_undefined_reason": _ratio_undefined_reason(
            grounding, "partially_supported_claim_rate"
        ),
        "unsupported_claim_rate": _ratio_value(grounding, "unsupported_claim_rate"),
        "unsupported_claim_rate_undefined_reason": _ratio_undefined_reason(
            grounding, "unsupported_claim_rate"
        ),
        "not_assessable_claim_count": _optional(
            grounding, "not_assessable_claim_count"
        ),
        "citation_document_accuracy": _ratio_value(
            grounding, "citation_document_accuracy"
        ),
        "citation_document_accuracy_undefined_reason": _ratio_undefined_reason(
            grounding, "citation_document_accuracy"
        ),
        "citation_passage_accuracy": _ratio_value(
            grounding, "citation_passage_accuracy"
        ),
        "citation_passage_accuracy_undefined_reason": _ratio_undefined_reason(
            grounding, "citation_passage_accuracy"
        ),
        "citation_precision": _ratio_value(grounding, "citation_precision"),
        "citation_precision_undefined_reason": _ratio_undefined_reason(
            grounding, "citation_precision"
        ),
        "citation_recall": _ratio_value(grounding, "citation_recall"),
        "citation_recall_undefined_reason": _ratio_undefined_reason(
            grounding, "citation_recall"
        ),
        "required_limitation_coverage": _ratio_value(
            grounding, "required_limitation_coverage"
        ),
        "required_limitation_coverage_undefined_reason": _ratio_undefined_reason(
            grounding, "required_limitation_coverage"
        ),
        "contradictory_claim_count": _optional(
            grounding, "contradictory_claim_count"
        ),
    }


def _refusal_row(result: PerQuestionEvaluation) -> dict[str, object]:
    observation = result.refusal_observation
    return {
        "system_key": result.system_key,
        "question_id": result.question_id,
        "family": result.family,
        "trust_status": result.trust_status,
        "status": result.status,
        "eligible": observation is not None,
        "expected_refusal": _optional(observation, "expected_refusal"),
        "abstained": _optional(observation, "abstained"),
        "refusal_origin": _optional(observation, "refusal_origin"),
        "refusal_appropriate": _optional(observation, "refusal_appropriate"),
        "unsafe_acceptance": _optional(observation, "unsafe_acceptance"),
        "downstream_call_count_after_refusal": _optional(
            observation, "downstream_call_count_after_refusal"
        ),
    }


def _latency_row(result: PerQuestionEvaluation) -> dict[str, object]:
    observation = result.efficiency
    return {
        "system_key": result.system_key,
        "question_id": result.question_id,
        "family": result.family,
        "trust_status": result.trust_status,
        "status": result.status,
        "eligible": observation is not None,
        "latency_ns": _optional(observation, "latency_ns"),
        "input_tokens": _optional(observation, "input_tokens"),
        "output_tokens": _optional(observation, "output_tokens"),
        "context_tokens": _optional(observation, "context_tokens"),
        "cost": _optional(observation, "cost"),
        "peak_process_rss_bytes": _optional(observation, "peak_process_rss_bytes"),
        "peak_accelerator_memory_bytes": _optional(
            observation, "peak_accelerator_memory_bytes"
        ),
    }


def _refusal_summary_row(
    run: BenchmarkRun,
    system_key: SystemKey,
) -> dict[str, object]:
    rows = tuple(result for result in run.results if result.system_key == system_key)
    overall = _summarize_refusal_or_none(rows)
    matched_ids = frozenset(_matched_llm_system_refusal_question_ids(run))
    matched_rows = tuple(
        result
        for result in rows
        if system_key in LLM_SYSTEM_KEYS and result.question_id in matched_ids
    )
    matched = _summarize_refusal_or_none(matched_rows)
    return {
        "system_key": system_key,
        "trust_status": run.manifest.trust_status,
        "question_count": len(rows),
        "refusal_observation_count": sum(
            result.refusal_observation is not None for result in rows
        ),
        **_refusal_metric_columns("", overall),
        "matched_llm_system_question_count": len(matched_rows),
        "matched_llm_system_refusal_observation_count": sum(
            result.refusal_observation is not None for result in matched_rows
        ),
        **_refusal_metric_columns("matched_llm_system_", matched),
    }


def _quality_latency_row(run: BenchmarkRun, system_key: SystemKey) -> dict[str, object]:
    all_rows = tuple(result for result in run.results if result.system_key == system_key)
    paired_question_ids = frozenset(_paired_llm_question_ids(run))
    rows = tuple(
        result
        for result in all_rows
        if system_key in LLM_SYSTEM_KEYS
        and result.question_id in paired_question_ids
    )
    paired_efficiency_ids = frozenset(_paired_llm_efficiency_question_ids(run))
    paired_efficiency_rows = tuple(
        result
        for result in all_rows
        if system_key in LLM_SYSTEM_KEYS
        and result.question_id in paired_efficiency_ids
    )
    recall_values = tuple(
        result.retrieval_metrics.recall_at_5
        for result in rows
        if result.retrieval_metrics is not None
    )
    fact_ratios = tuple(
        result.grounding_metrics.required_fact_coverage
        for result in rows
        if result.grounding_metrics is not None
    )
    support_ratios = tuple(
        result.grounding_metrics.fully_supported_claim_rate
        for result in rows
        if result.grounding_metrics is not None
    )
    paired_efficiency = _summarize_efficiency_or_none(paired_efficiency_rows)
    observed_efficiency = _summarize_efficiency_or_none(all_rows)
    return {
        "system_key": system_key,
        "comparison_scope": (
            "paired_llm" if system_key in LLM_SYSTEM_KEYS else "not_in_llm_comparison"
        ),
        "comparison_eligible_question_count": (
            len(run.comparison_eligible_question_ids)
            if system_key in LLM_SYSTEM_KEYS
            else 0
        ),
        "paired_llm_question_count": len(rows),
        "paired_completed_answer_count": sum(
            result.status == "completed" for result in rows
        ),
        "paired_refused_answer_count": sum(result.status == "refused" for result in rows),
        "paired_failure_count": sum(result.status == "failed" for result in rows),
        "retrieval_metric_question_count": len(recall_values),
        "grounding_fact_question_count": len(fact_ratios),
        "grounding_support_question_count": len(support_ratios),
        "mean_recall_at_5": _mean_strings(recall_values),
        "required_fact_coverage": _aggregate_ratio(fact_ratios),
        "fully_supported_claim_rate": _aggregate_ratio(support_ratios),
        **_efficiency_summary_columns("paired_", paired_efficiency),
        **_efficiency_summary_columns("observed_", observed_efficiency),
        "trust_status": run.manifest.trust_status,
    }


def _paired_llm_question_ids(run: BenchmarkRun) -> tuple[StableToken, ...]:
    """Return only preregistered, fully executed six-system LLM comparisons."""

    result_by_key = {
        (result.system_key, result.question_id): result for result in run.results
    }
    input_keys = {
        (record.system_key, record.question_id) for record in run.comparison_inputs
    }
    paired: list[StableToken] = []
    for question_id in run.comparison_eligible_question_ids:
        for system_key in LLM_SYSTEM_KEYS:
            key = (system_key, question_id)
            result = result_by_key.get(key)
            if key not in input_keys or result is None:
                raise ReportingError("paired LLM comparison is missing a preregistered input")
            if (
                result.status not in {"completed", "refused"}
                or result.execution_trace.generation_call_count != 1
                or result.answer is None
                or result.answer_sha256 is None
                or result.mechanical_validation is None
            ):
                raise ReportingError("paired LLM comparison did not finish with a scored answer")
        paired.append(question_id)
    return tuple(paired)


def _matched_llm_system_refusal_question_ids(
    run: BenchmarkRun,
) -> tuple[StableToken, ...]:
    """Use identical questions with observations across all six LLM-based systems.

    Unlike answer-quality pairing, end-to-end refusal comparison retains an
    early policy refusal even when that system correctly never calls its LLM.
    """

    result_by_key = {
        (result.system_key, result.question_id): result for result in run.results
    }
    question_ids = tuple(
        result.question_id for result in run.results if result.system_key == "S0"
    )
    return tuple(
        question_id
        for question_id in question_ids
        if all(
            result_by_key[(system_key, question_id)].refusal_observation is not None
            for system_key in LLM_SYSTEM_KEYS
        )
    )


def _paired_llm_efficiency_question_ids(run: BenchmarkRun) -> tuple[StableToken, ...]:
    """Use one shared measured cohort for all paired LLM efficiency summaries."""

    result_by_key = {
        (result.system_key, result.question_id): result for result in run.results
    }
    return tuple(
        question_id
        for question_id in _paired_llm_question_ids(run)
        if all(
            result_by_key[(system_key, question_id)].efficiency is not None
            for system_key in LLM_SYSTEM_KEYS
        )
    )


def _summarize_refusal_or_none(
    rows: tuple[PerQuestionEvaluation, ...],
) -> RefusalMetrics | None:
    observations = tuple(
        result.refusal_observation
        for result in rows
        if result.refusal_observation is not None
    )
    return None if not observations else summarize_refusal(observations)


def _summarize_efficiency_or_none(
    rows: tuple[PerQuestionEvaluation, ...],
) -> EfficiencySummary | None:
    observations = tuple(
        result.efficiency for result in rows if result.efficiency is not None
    )
    return None if not observations else summarize_efficiency(observations)


def _model_payload(value: StrictFrozenSchema | None) -> dict[str, object] | None:
    return None if value is None else value.model_dump(mode="json")


def _refusal_metric_columns(
    prefix: str,
    metric: RefusalMetrics | None,
) -> dict[str, object]:
    return {
        **_flat_ratio_columns(
            f"{prefix}correct_refusal_rate",
            None if metric is None else metric.correct_refusal_rate,
        ),
        **_flat_ratio_columns(
            f"{prefix}false_refusal_rate",
            None if metric is None else metric.false_refusal_rate,
        ),
        **_flat_ratio_columns(
            f"{prefix}unsafe_acceptance_rate",
            None if metric is None else metric.unsafe_acceptance_rate,
        ),
        f"{prefix}downstream_calls_after_refusal": (
            "" if metric is None else metric.downstream_calls_after_refusal
        ),
        **_flat_ratio_columns(
            f"{prefix}downstream_call_violation_rate",
            None if metric is None else metric.downstream_call_violation_rate,
        ),
    }


def _flat_ratio_columns(
    prefix: str,
    metric: RatioMetric | None,
) -> dict[str, object]:
    return {
        prefix: "" if metric is None or metric.value is None else metric.value,
        f"{prefix}_numerator": "" if metric is None else metric.numerator,
        f"{prefix}_denominator": "" if metric is None else metric.denominator,
        f"{prefix}_undefined_reason": (
            "" if metric is None or metric.undefined_reason is None
            else metric.undefined_reason
        ),
    }


def _efficiency_summary_columns(
    prefix: str,
    summary: EfficiencySummary | None,
) -> dict[str, object]:
    return {
        f"{prefix}sample_count": "" if summary is None else summary.sample_count,
        f"{prefix}p50_latency_ns": "" if summary is None else summary.p50_latency_ns,
        f"{prefix}p95_latency_ns": "" if summary is None else summary.p95_latency_ns,
        f"{prefix}total_input_tokens": (
            "" if summary is None else summary.total_input_tokens
        ),
        f"{prefix}total_output_tokens": (
            "" if summary is None else summary.total_output_tokens
        ),
        f"{prefix}total_context_tokens": (
            "" if summary is None else summary.total_context_tokens
        ),
        f"{prefix}total_cost": (
            "" if summary is None or summary.total_cost is None else summary.total_cost
        ),
        f"{prefix}peak_process_rss_bytes": (
            "" if summary is None else summary.peak_process_rss_bytes
        ),
        f"{prefix}peak_accelerator_memory_bytes": (
            ""
            if summary is None or summary.peak_accelerator_memory_bytes is None
            else summary.peak_accelerator_memory_bytes
        ),
    }


def _human_review_template() -> bytes:
    return _csv_bytes(
        [],
        fieldnames=(
            "packet_sha256",
            "reviewer_key",
            "reviewed_at",
            "blind_answer_id",
            "answer_sha256",
            "blind_claim_id",
            "claim_sha256",
            "support_label",
            "citation_id",
            "cited_document_correct",
            "cited_passage_correct",
            "cited_passage_supports_claim",
            "overinterpretation_present",
            "required_limitation_present",
            "refusal_appropriate",
            "review_note",
        ),
    )


def _csv_bytes(
    rows: list[dict[str, object]],
    *,
    fieldnames: tuple[str, ...] | None = None,
) -> bytes:
    if fieldnames is None:
        if not rows:
            raise ReportingError("derived CSV requires rows or explicit columns")
        fieldnames = tuple(rows[0])
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _json_line(value: object) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def _optional(value: object | None, field_name: str) -> object:
    if value is None:
        return ""
    observed = getattr(value, field_name)
    return "" if observed is None else observed


def _ratio_value(value: object | None, field_name: str) -> object:
    if value is None:
        return ""
    metric = getattr(value, field_name)
    return "" if metric.value is None else metric.value


def _ratio_undefined_reason(value: object | None, field_name: str) -> object:
    if value is None:
        return ""
    metric = getattr(value, field_name)
    return "" if metric.undefined_reason is None else metric.undefined_reason


def _association_columns(
    prefix: str,
    metric: AssociationMetrics | None,
) -> dict[str, object]:
    return {
        f"{prefix}_eligible": metric is not None,
        f"{prefix}_set_exact": _optional(metric, "association_set_exact"),
        f"{prefix}_missing_count": _optional(metric, "missing_association_count"),
        f"{prefix}_extra_count": _optional(metric, "extra_association_count"),
        f"{prefix}_class_corrupted_count": _optional(
            metric, "class_corrupted_count"
        ),
        f"{prefix}_role_corrupted_count": _optional(metric, "role_corrupted_count"),
        f"{prefix}_scope_corrupted_count": _optional(
            metric, "scope_corrupted_count"
        ),
    }


def _mean_strings(values: tuple[str, ...]) -> str:
    if not values:
        return ""
    total = sum((Decimal(value) for value in values), start=Decimal(0))
    return f"{(total / Decimal(len(values))).quantize(Decimal('0.000000000001')):.12f}"


def _aggregate_ratio(values: tuple[RatioMetric, ...]) -> str:
    numerator = sum(value.numerator for value in values)
    denominator = sum(value.denominator for value in values)
    if denominator == 0:
        return ""
    return f"{(Decimal(numerator) / Decimal(denominator)).quantize(Decimal('0.000000000001')):.12f}"


def _self_sha256(value: StrictFrozenSchema, field_name: str) -> str:
    payload = value.model_dump(mode="json")
    del payload[field_name]
    return canonical_json_sha256(payload)


def _write_new_file(path: Path, content: bytes) -> None:
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise ReportingError("Markdown report parent does not exist") from exc
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_bytes(content)
        os.rename(temporary, path)
    except Exception as exc:
        if temporary.exists():
            temporary.unlink()
        raise ReportingError("failed to create Markdown report atomically") from exc
