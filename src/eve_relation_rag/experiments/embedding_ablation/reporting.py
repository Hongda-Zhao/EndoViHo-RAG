"""Deterministic machine outputs and Markdown generated only from verified result files."""

from __future__ import annotations

import csv
import hashlib
import io
import os
import shutil
import tempfile
from pathlib import Path

from eve_relation_rag.experiments.embedding_ablation.metrics import rank_shift
from eve_relation_rag.experiments.embedding_ablation.results import (
    ExperimentManifest,
    ExperimentRun,
    FailureRecord,
    SystemExecutionResult,
    provider_records_from_trust_decision,
)
from eve_relation_rag.experiments.embedding_ablation.trust import (
    RunTrustDecision,
    is_issued_trust_decision,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes


class DeterministicReportError(RuntimeError):
    """Raised when results are untrusted, inconsistent, non-canonical, or would overwrite."""


def write_experiment_outputs(
    output_directory: Path,
    run: ExperimentRun,
    trust_decision: RunTrustDecision,
    *,
    markdown_report_path: Path | None = None,
    allow_test_output: bool = False,
) -> None:
    """Atomically create the requested output tree without overwriting existing artifacts."""

    _validate_reporting_authority(run, trust_decision, allow_test_output=allow_test_output)
    if run.manifest.trust_status == "trusted" and markdown_report_path is None:
        raise DeterministicReportError("trusted run requires a generated Markdown report path")
    if run.manifest.trust_status != "trusted" and markdown_report_path is not None:
        raise DeterministicReportError("only a trusted run may generate the formal Markdown report")
    if output_directory.exists() or output_directory.is_symlink():
        raise DeterministicReportError("experiment output directory already exists")
    if markdown_report_path is not None and (
        markdown_report_path.exists() or markdown_report_path.is_symlink()
    ):
        raise DeterministicReportError("Markdown report path already exists")

    try:
        output_parent = output_directory.parent.resolve(strict=True)
    except OSError as exc:
        raise DeterministicReportError("experiment output parent does not exist") from exc
    files = _all_output_files(run)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_directory.name}.", dir=output_parent))
    try:
        for relative_path, content in files.items():
            target = temporary / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        os.rename(temporary, output_directory)
    except Exception as exc:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise DeterministicReportError("failed to create experiment outputs atomically") from exc

    if markdown_report_path is not None:
        try:
            markdown = generate_markdown_report_bytes(output_directory)
            _write_new_file_atomically(markdown_report_path, markdown)
        except Exception:
            if output_directory.exists():
                shutil.rmtree(output_directory)
            raise


def load_experiment_run(output_directory: Path) -> ExperimentRun:
    """Reconstruct and revalidate a run from canonical manifest/system/failure files."""

    try:
        manifest = ExperimentManifest.model_validate_json(
            (output_directory / "experiment_manifest.json").read_bytes()
        )
        system_results = tuple(
            SystemExecutionResult.model_validate_json(
                (output_directory / "systems" / f"{system.system_key}.json").read_bytes()
            )
            for system in manifest.systems
        )
        failure_path = output_directory / "failures.jsonl"
        failures = tuple(
            FailureRecord.model_validate_json(line)
            for line in failure_path.read_text(encoding="utf-8").splitlines()
            if line
        )
        run = ExperimentRun(
            manifest=manifest,
            system_results=system_results,
            failures=failures,
        )
    except Exception as exc:
        raise DeterministicReportError("machine results cannot be reconstructed exactly") from exc
    expected = _all_output_files(run)
    for relative_path, expected_bytes in expected.items():
        try:
            observed = (output_directory / relative_path).read_bytes()
        except OSError as exc:
            raise DeterministicReportError(f"machine result is missing: {relative_path}") from exc
        if observed != expected_bytes:
            raise DeterministicReportError(f"machine result is not canonical: {relative_path}")
    actual_files = {
        path.relative_to(output_directory).as_posix()
        for path in output_directory.rglob("*")
        if path.is_file()
    }
    if actual_files != set(expected):
        raise DeterministicReportError("machine-result directory contains missing or extra files")
    return run


def generate_markdown_report_bytes(output_directory: Path) -> bytes:
    """Generate the formal report only from reloaded, fully revalidated machine results."""

    run = load_experiment_run(output_directory)
    if run.manifest.trust_status != "trusted":
        raise DeterministicReportError("formal Markdown report requires trusted machine results")
    lines = [
        "# Embedding 与 Reranker Ablation 结果",
        "",
        "本报告由 checksum-bound 机器结果确定性生成；未重新调用模型或数据库。",
        "",
        "## 实验身份",
        "",
        f"- Experiment: `{run.manifest.experiment_key}`",
        f"- Source commit: `{run.manifest.source_commit}`",
        f"- Corpus: `{run.manifest.corpus_release_key}`",
        f"- Corpus fingerprint: `{run.manifest.corpus_fingerprint_sha256}`",
        f"- Gold SHA-256: `{run.manifest.gold_sha256}`",
        f"- Approved questions: {run.manifest.approved_question_count}",
        f"- Hardware SHA-256: `{run.manifest.hardware_record_sha256}`",
        f"- Trust status: `{run.manifest.trust_status}`",
        "",
        "## Quality–latency comparison",
        "",
        "| System | Recall@5 | MRR@10 | nDCG@10 | E2E p50 (ms) | E2E p95 (ms) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for result in run.system_results:
        quality = result.quality.overall
        latency = result.latency.end_to_end
        lines.append(
            "| "
            f"`{result.system.system_key}` | {quality.recall_at_5} | {quality.mrr_at_10} | "
            f"{quality.ndcg_at_10} | {_milliseconds(latency.p50_ns)} | "
            f"{_milliseconds(latency.p95_ns)} |"
        )
    lines.extend(
        [
            "",
            "模型选择应联合考虑 Recall@5、MRR@10 与 latency；不得只依据 Recall@10。",
            "",
            "## Resource comparison",
            "",
            "| System | Peak RSS (bytes) | Model bytes | Index bytes | Truncations |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for result in run.system_results:
        resources = result.resources
        model_size = resources.embedding_model_size_bytes + (
            resources.reranker_model_size_bytes or 0
        )
        truncations = resources.passage_embedding_truncation_count + sum(
            question.truncation.embedding_query_count
            + question.truncation.reranker_query_count
            + question.truncation.reranker_passage_count
            for question in result.question_results
        )
        lines.append(
            f"| `{result.system.system_key}` | {resources.peak_process_rss_bytes} | "
            f"{model_size} | {resources.index_size_bytes} | {truncations} |"
        )
    lines.extend(
        [
            "",
            "## Failures",
            "",
            ("无。" if not run.failures else f"共 {len(run.failures)} 条结构化 failure。"),
            "",
        ]
    )
    return ("\n".join(lines)).encode("utf-8")


def _validate_reporting_authority(
    run: ExperimentRun,
    trust_decision: RunTrustDecision,
    *,
    allow_test_output: bool,
) -> None:
    if not is_issued_trust_decision(trust_decision):
        raise DeterministicReportError("reporting requires a trust-gate-issued decision")
    if (
        run.manifest.trust_status != trust_decision.status
        or run.manifest.trust_reasons != trust_decision.reasons
        or run.manifest.providers != provider_records_from_trust_decision(trust_decision)
        or run.manifest.corpus_release_key != trust_decision.corpus_release_key
        or run.manifest.corpus_manifest_sha256 != trust_decision.corpus_manifest_sha256
        or run.manifest.annotation_manifest_sha256
        != trust_decision.annotation_manifest_sha256
        or run.manifest.gold_sha256 != trust_decision.gold_sha256
        or run.manifest.approved_question_count
        != trust_decision.approved_question_count
    ):
        raise DeterministicReportError("run manifest does not match the trust decision")
    if trust_decision.status == "failed":
        raise DeterministicReportError("failed experiment cannot create result artifacts")
    if trust_decision.status == "test_only" and not allow_test_output:
        raise DeterministicReportError("fake/test providers cannot create a trusted report")


def _all_output_files(run: ExperimentRun) -> dict[str, bytes]:
    files: dict[str, bytes] = {
        "experiment_manifest.json": _json_line(run.manifest),
        "failures.jsonl": b"".join(
            _json_line(failure)
            for failure in sorted(
                run.failures,
                key=lambda row: (
                    row.system_key,
                    row.question_id or "",
                    row.stage,
                    row.error_code,
                ),
            )
        ),
    }
    for result in run.system_results:
        files[f"systems/{result.system.system_key}.json"] = _json_line(result)
        for question in result.question_results:
            question_hash = hashlib.sha256(question.question_id.encode()).hexdigest()
            files[
                f"per_question/{result.system.system_key}/{question_hash}.json"
            ] = _json_line(question)
    files.update(_derived_output_files(run))
    return dict(sorted(files.items()))


def _derived_output_files(run: ExperimentRun) -> dict[str, bytes]:
    summary_rows = [_summary_row(run, result) for result in run.system_results]
    summary_payload = {
        "summary_schema_version": "embedding-ablation-summary-v1",
        "experiment_manifest_sha256": run.manifest.experiment_manifest_sha256,
        "trust_status": run.manifest.trust_status,
        "systems": summary_rows,
    }
    latency_rows: list[dict[str, object]] = []
    for result in run.system_results:
        for question in result.question_results:
            for iteration in range(len(question.latency.embedding_ns)):
                latency_rows.append(
                    {
                        "system_key": result.system.system_key,
                        "question_id": question.question_id,
                        "iteration": iteration + 1,
                        "embedding_ns": question.latency.embedding_ns[iteration],
                        "retrieval_ns": question.latency.retrieval_ns[iteration],
                        "reranking_ns": (
                            ""
                            if question.latency.reranking_ns is None
                            else question.latency.reranking_ns[iteration]
                        ),
                        "end_to_end_ns": question.latency.end_to_end_ns[iteration],
                    }
                )
    resource_rows = [_resource_row(result) for result in run.system_results]
    quality_rows: list[dict[str, object]] = [
        {
            "system_key": result.system.system_key,
            "rerank_candidate_depth": result.system.rerank_candidate_depth or "",
            "reranker_batch_size": result.system.reranker_batch_size or "",
            "recall_at_1": result.quality.overall.recall_at_1,
            "recall_at_3": result.quality.overall.recall_at_3,
            "recall_at_5": result.quality.overall.recall_at_5,
            "recall_at_10": result.quality.overall.recall_at_10,
            "mrr_at_10": result.quality.overall.mrr_at_10,
            "ndcg_at_10": result.quality.overall.ndcg_at_10,
        }
        for result in run.system_results
    ]
    category_rows: list[dict[str, object]] = [
        {
            "system_key": result.system.system_key,
            "rerank_candidate_depth": result.system.rerank_candidate_depth or "",
            "reranker_batch_size": result.system.reranker_batch_size or "",
            "category": category,
            "question_count": quality.question_count,
            "recall_at_1": quality.recall_at_1,
            "recall_at_3": quality.recall_at_3,
            "recall_at_5": quality.recall_at_5,
            "recall_at_10": quality.recall_at_10,
            "mrr_at_10": quality.mrr_at_10,
            "ndcg_at_10": quality.ndcg_at_10,
        }
        for result in run.system_results
        for category, quality in sorted(result.quality.by_category.items())
    ]
    latency_comparison_rows: list[dict[str, object]] = [
        {
            "system_key": result.system.system_key,
            "rerank_candidate_depth": result.system.rerank_candidate_depth or "",
            "reranker_batch_size": result.system.reranker_batch_size or "",
            "embedding_p50_ns": result.latency.embedding.p50_ns,
            "embedding_p95_ns": result.latency.embedding.p95_ns,
            "retrieval_p50_ns": result.latency.retrieval.p50_ns,
            "retrieval_p95_ns": result.latency.retrieval.p95_ns,
            "reranking_p50_ns": (
                "" if result.latency.reranking is None else result.latency.reranking.p50_ns
            ),
            "reranking_p95_ns": (
                "" if result.latency.reranking is None else result.latency.reranking.p95_ns
            ),
            "end_to_end_p50_ns": result.latency.end_to_end.p50_ns,
            "end_to_end_p95_ns": result.latency.end_to_end.p95_ns,
        }
        for result in run.system_results
    ]
    rank_rows: list[dict[str, object]] = []
    for result in run.system_results:
        if result.system.rerank_candidate_depth is None:
            continue
        for question in result.question_results:
            shifts = rank_shift(
                question.pre_rerank_chunk_keys,
                question.ranked_candidate_chunk_keys,
            )
            post_ranks = {
                key: rank
                for rank, key in enumerate(question.ranked_candidate_chunk_keys, start=1)
            }
            for pre_rank, chunk_key in enumerate(question.pre_rerank_chunk_keys, start=1):
                rank_rows.append(
                    {
                        "system_key": result.system.system_key,
                        "question_id": question.question_id,
                        "chunk_key": chunk_key,
                        "pre_rerank_rank": pre_rank,
                        "post_rerank_rank": post_ranks[chunk_key],
                        "rank_shift": shifts[chunk_key],
                        "in_final_top_10": post_ranks[chunk_key] <= 10,
                    }
                )
    return {
        "summary.json": _json_line(summary_payload),
        "summary.csv": _csv_bytes(summary_rows),
        "latency.csv": _csv_bytes(latency_rows),
        "resource_usage.csv": _csv_bytes(resource_rows),
        "retrieval_quality.csv": _csv_bytes(quality_rows),
        "retrieval_by_category.csv": _csv_bytes(category_rows),
        "latency_comparison.csv": _csv_bytes(latency_comparison_rows),
        "resource_comparison.csv": _csv_bytes(resource_rows),
        "rank_shift_after_reranking.csv": _csv_bytes(
            rank_rows,
            fieldnames=(
                "system_key",
                "question_id",
                "chunk_key",
                "pre_rerank_rank",
                "post_rerank_rank",
                "rank_shift",
                "in_final_top_10",
            ),
        ),
    }


def _summary_row(run: ExperimentRun, result: SystemExecutionResult) -> dict[str, object]:
    return {
        "system_key": result.system.system_key,
        "trust_status": run.manifest.trust_status,
        "rerank_candidate_depth": result.system.rerank_candidate_depth or "",
        "reranker_batch_size": result.system.reranker_batch_size or "",
        "question_count": result.quality.overall.question_count,
        "recall_at_1": result.quality.overall.recall_at_1,
        "recall_at_3": result.quality.overall.recall_at_3,
        "recall_at_5": result.quality.overall.recall_at_5,
        "recall_at_10": result.quality.overall.recall_at_10,
        "mrr_at_10": result.quality.overall.mrr_at_10,
        "ndcg_at_10": result.quality.overall.ndcg_at_10,
        "end_to_end_p50_ns": result.latency.end_to_end.p50_ns,
        "end_to_end_p95_ns": result.latency.end_to_end.p95_ns,
    }


def _resource_row(result: SystemExecutionResult) -> dict[str, object]:
    resources = result.resources
    embedding_query_count = sum(
        question.truncation.embedding_query_count for question in result.question_results
    )
    embedding_query_tokens = sum(
        question.truncation.embedding_query_tokens for question in result.question_results
    )
    reranker_query_count = sum(
        question.truncation.reranker_query_count for question in result.question_results
    )
    reranker_query_tokens = sum(
        question.truncation.reranker_query_tokens for question in result.question_results
    )
    reranker_passage_count = sum(
        question.truncation.reranker_passage_count for question in result.question_results
    )
    reranker_passage_tokens = sum(
        question.truncation.reranker_passage_tokens for question in result.question_results
    )
    return {
        "system_key": result.system.system_key,
        "rerank_candidate_depth": result.system.rerank_candidate_depth or "",
        "reranker_batch_size": result.system.reranker_batch_size or "",
        "peak_process_rss_bytes": resources.peak_process_rss_bytes,
        "peak_accelerator_memory_bytes": (
            ""
            if resources.peak_accelerator_memory_bytes is None
            else resources.peak_accelerator_memory_bytes
        ),
        "embedding_model_size_bytes": resources.embedding_model_size_bytes,
        "reranker_model_size_bytes": (
            ""
            if resources.reranker_model_size_bytes is None
            else resources.reranker_model_size_bytes
        ),
        "index_size_bytes": resources.index_size_bytes,
        "passage_embedding_truncation_count": resources.passage_embedding_truncation_count,
        "passage_embedding_truncated_tokens": resources.passage_embedding_truncated_tokens,
        "embedding_query_truncation_count": embedding_query_count,
        "embedding_query_truncated_tokens": embedding_query_tokens,
        "reranker_query_truncation_count": reranker_query_count,
        "reranker_query_truncated_tokens": reranker_query_tokens,
        "reranker_passage_truncation_count": reranker_passage_count,
        "reranker_passage_truncated_tokens": reranker_passage_tokens,
    }


def _csv_bytes(
    rows: list[dict[str, object]],
    *,
    fieldnames: tuple[str, ...] | None = None,
) -> bytes:
    if fieldnames is None:
        if not rows:
            raise DeterministicReportError("CSV rows require explicit fields when empty")
        fieldnames = tuple(rows[0])
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _json_line(value: object) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def _milliseconds(nanoseconds: int) -> str:
    return f"{nanoseconds / 1_000_000:.3f}"


def _write_new_file_atomically(path: Path, value: bytes) -> None:
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise DeterministicReportError("Markdown report parent does not exist") from exc
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_bytes(value)
        os.rename(temporary, path)
    except Exception as exc:
        if temporary.exists():
            temporary.unlink()
        raise DeterministicReportError("failed to create Markdown report atomically") from exc
