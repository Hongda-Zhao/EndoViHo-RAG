"""Create-once machine outputs and Markdown derived only from revalidated results."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from eve_relation_rag.experiments.rag_value_ablation.contracts import (
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
    EfficiencyObservation,
    GroundingMetrics,
    RatioMetric,
    RefusalObservation,
    RetrievalMetrics,
    StructuredMetrics,
)
from eve_relation_rag.experiments.rag_value_ablation.systems import (
    ALL_SYSTEM_KEYS,
    LLM_SYSTEM_KEYS,
    validate_execution_trace,
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
    answer_sha256: Sha256 | None = None
    execution_trace: ExecutionTrace
    mechanical_validation: MechanicalValidation | None = None
    structured_preservation: StructuredPreservationProof | None = None
    structured_metrics: StructuredMetrics | None = None
    retrieval_metrics: RetrievalMetrics | None = None
    grounding_metrics: GroundingMetrics | None = None
    refusal_observation: RefusalObservation | None = None
    efficiency: EfficiencyObservation | None = None
    result_sha256: Sha256

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
        if self.status in {"failed", "not_applicable"} and any(
            value is not None
            for value in (
                self.evidence_pack_sha256,
                self.answer_sha256,
                self.mechanical_validation,
                self.structured_preservation,
                self.structured_metrics,
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
                self.answer_sha256,
                self.mechanical_validation,
                self.structured_preservation,
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
        "answer_sha256": None,
        "mechanical_validation": None,
        "structured_preservation": None,
        "structured_metrics": None,
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
            if result.status == "completed" and result.system_key in LLM_SYSTEM_KEYS:
                if (
                    result.evidence_pack_sha256 is None
                    or result.answer_sha256 is None
                    or result.mechanical_validation is None
                ):
                    raise ValueError("completed LLM result lacks evidence/answer validation")
            if result.system_key == "S4" and (
                result.answer_sha256 is not None or result.mechanical_validation is not None
            ):
                raise ValueError("S4 must not carry an LLM answer or validation")
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

    payload = {"run_schema_version": "rag-value-run-v1", "failures": (), **values}
    payload.pop("run_sha256", None)
    return BenchmarkRun.model_validate(
        {**payload, "run_sha256": canonical_json_sha256(payload)}
    )


def write_benchmark_outputs(
    output_directory: Path,
    run: BenchmarkRun,
    *,
    markdown_report_path: Path | None = None,
    allow_test_output: bool = False,
) -> None:
    """Atomically create all machine files once and optionally a trusted formal report."""

    if run.manifest.trust_status == "failed":
        raise ReportingError("failed run cannot publish benchmark outputs")
    if run.manifest.trust_status == "test_only" and not allow_test_output:
        raise ReportingError("test-only output requires explicit allow_test_output")
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
            _write_new_file(markdown_report_path, generate_markdown_report(output_directory))
        except Exception:
            shutil.rmtree(output_directory)
            raise


def load_benchmark_run(output_directory: Path) -> BenchmarkRun:
    """Reload core records and reject any missing, extra, or manually edited derived file."""

    try:
        manifest = ExperimentManifest.model_validate_json(
            (output_directory / "experiment_manifest.json").read_bytes()
        )
        results = tuple(
            PerQuestionEvaluation.model_validate_json(path.read_bytes())
            for path in sorted((output_directory / "per_question").rglob("*.json"))
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


def generate_markdown_report(output_directory: Path) -> bytes:
    """Generate the formal report exclusively from a fully revalidated machine directory."""

    run = load_benchmark_run(output_directory)
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
    return dict(sorted(files.items()))


def _derived_files(run: BenchmarkRun) -> dict[str, bytes]:
    retrieval_rows = [_retrieval_row(result) for result in run.results]
    answer_rows = [_answer_row(result) for result in run.results]
    refusal_rows = [_refusal_row(result) for result in run.results]
    latency_rows = [_latency_row(result) for result in run.results]
    quality_latency_rows = [_quality_latency_row(run, key) for key in ALL_SYSTEM_KEYS]
    return {
        "summary.json": canonical_json_bytes(_summary_payload(run)) + b"\n",
        "retrieval_metrics.csv": _csv_bytes(retrieval_rows),
        "answer_metrics.csv": _csv_bytes(answer_rows),
        "refusal_metrics.csv": _csv_bytes(refusal_rows),
        "latency_metrics.csv": _csv_bytes(latency_rows),
        "plot_no_rag_vs_rag.csv": _csv_bytes(
            [row for row in answer_rows if row["system_key"] in LLM_SYSTEM_KEYS]
        ),
        "plot_structured_correctness.csv": _csv_bytes(answer_rows),
        "plot_claim_support.csv": _csv_bytes(answer_rows),
        "plot_refusal.csv": _csv_bytes(refusal_rows),
        "plot_retrieval_quality.csv": _csv_bytes(retrieval_rows),
        "plot_quality_latency.csv": _csv_bytes(quality_latency_rows),
    }


def _summary_payload(run: BenchmarkRun) -> dict[str, object]:
    systems: list[dict[str, object]] = []
    for key in ALL_SYSTEM_KEYS:
        rows = tuple(result for result in run.results if result.system_key == key)
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
            }
        )
    return {
        "summary_schema_version": "rag-value-summary-v1",
        "experiment_manifest_sha256": run.manifest.manifest_sha256,
        "run_sha256": run.run_sha256,
        "trust_status": run.manifest.trust_status,
        "human_review_status": run.human_review_status,
        "systems": systems,
        "failure_count": len(run.failures),
    }


def _retrieval_row(result: PerQuestionEvaluation) -> dict[str, object]:
    metric = result.retrieval_metrics
    return {
        "system_key": result.system_key,
        "question_id": result.question_id,
        "family": result.family,
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
        "status": result.status,
        "structured_eligible": structured is not None,
        "numeric_exact_match": _optional(structured, "numeric_exact_match"),
        "record_set_exact": _optional(structured, "record_set_exact"),
        "coordinate_set_exact": _optional(structured, "coordinate_set_exact"),
        "identifier_preservation": _ratio_value(structured, "identifier_preservation"),
        "release_provenance_exact": _optional(structured, "release_provenance_exact"),
        "invented_identifier_count": _optional(structured, "invented_identifier_count"),
        "grounding_eligible": grounding is not None,
        "required_fact_coverage": _ratio_value(grounding, "required_fact_coverage"),
        "structured_fact_preservation": _ratio_value(
            grounding, "structured_fact_preservation"
        ),
        "fully_supported_claim_rate": _ratio_value(
            grounding, "fully_supported_claim_rate"
        ),
        "partially_supported_claim_rate": _ratio_value(
            grounding, "partially_supported_claim_rate"
        ),
        "unsupported_claim_rate": _ratio_value(grounding, "unsupported_claim_rate"),
        "citation_precision": _ratio_value(grounding, "citation_precision"),
        "citation_recall": _ratio_value(grounding, "citation_recall"),
        "required_limitation_coverage": _ratio_value(
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
        "status": result.status,
        "eligible": observation is not None,
        "expected_refusal": _optional(observation, "expected_refusal"),
        "abstained": _optional(observation, "abstained"),
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


def _quality_latency_row(run: BenchmarkRun, system_key: SystemKey) -> dict[str, object]:
    rows = tuple(result for result in run.results if result.system_key == system_key)
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
    latencies = tuple(
        sorted(result.efficiency.latency_ns for result in rows if result.efficiency is not None)
    )
    return {
        "system_key": system_key,
        "eligible_question_count": sum(result.status == "completed" for result in rows),
        "failure_count": sum(result.status == "failed" for result in rows),
        "mean_recall_at_5": _mean_strings(recall_values),
        "required_fact_coverage": _aggregate_ratio(fact_ratios),
        "fully_supported_claim_rate": _aggregate_ratio(support_ratios),
        "p50_latency_ns": "" if not latencies else _nearest_rank(latencies, 50),
        "p95_latency_ns": "" if not latencies else _nearest_rank(latencies, 95),
        "trust_status": run.manifest.trust_status,
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


def _nearest_rank(ordered: tuple[int, ...], percentile: int) -> int:
    rank = (percentile * len(ordered) + 99) // 100
    return ordered[rank - 1]


def _self_sha256(value: StrictFrozenSchema, field_name: str) -> str:
    payload = value.model_dump(mode="python")
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
