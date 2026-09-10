#!/usr/bin/env python3
"""Read-only machine-result audit; no model, database access, or scientific scoring.

Exit codes: 0 = passed, 2 = still incomplete, 1 = invalid or blocked results.
The optional output is a new, separate audit report; experiment files are never written.
A full audit requires a fresh Python process and verifies frozen code before importing it.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.machinery
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any


def normalize(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key = normalize(key)
            if key in result:
                raise ValueError("duplicate_normalized_json_key")
            result[key] = normalize(item)
        return result
    return value


def canonical(value: Any) -> bytes:
    return json.dumps(normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def load_json(path: Path) -> Any:
    if path.is_symlink():
        raise ValueError(f"symlink_not_allowed:{path.name}")
    return json.loads(path.read_bytes())


def pinned(item: dict[str, str]) -> Path:
    path = Path(item["path"])
    if path.is_symlink():
        raise ValueError(f"symlink_not_allowed:{path.name}")
    with path.open("rb") as handle:
        actual = hashlib.file_digest(handle, "sha256").hexdigest()
    if actual != item["sha256"]:
        raise ValueError(f"pinned_file_mismatch:{path.name}")
    return path


def _source_entries(items: Any) -> dict[Path, str]:
    if not isinstance(items, list) or not items:
        raise ValueError("source_snapshot_manifest_invalid")
    entries: dict[Path, str] = {}
    for item in items:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None
        ):
            raise ValueError("source_snapshot_manifest_invalid")
        path = Path(item["path"])
        if not path.is_absolute() or ".." in path.parts or path in entries:
            raise ValueError("source_snapshot_manifest_invalid")
        entries[path] = item["sha256"]
    return entries


def _no_snapshot_symlinks(path: Path) -> None:
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("snapshot_symlink_not_allowed")


def verify_source_snapshot(
    run_dir: Path, config: dict[str, Any], receipt: dict[str, Any],
) -> Path:
    """Verify copied code against original absolute pins without reading live source.

    Legacy freezes have no separate snapshot manifest. Their one package root
    identifies the original repository prefix, which is replaced by code_snapshot.
    """
    snapshot_root = run_dir.absolute() / "code_snapshot"
    _no_snapshot_symlinks(snapshot_root)
    snapshot = snapshot_root / "src"
    if not snapshot.is_dir():
        raise ValueError("frozen_code_snapshot_missing")
    implementation = _source_entries(config.get("implementation_files"))
    roots = [path.parents[2] for path in implementation
             if path.parts[-3:] == ("src", "eve_relation_rag", "__init__.py")]
    if len(roots) != 1:
        raise ValueError("source_snapshot_manifest_invalid")
    try:
        expected = {path.relative_to(roots[0]): sha for path, sha in implementation.items()}
    except ValueError:
        raise ValueError("source_snapshot_manifest_invalid") from None
    replay = Path("src/eve_relation_rag/experiments/rag_value_ablation/source_report_queries.py")
    if replay not in expected:
        raise ValueError("source_snapshot_manifest_invalid")

    if "source_snapshot" in receipt:
        reference = receipt["source_snapshot"]
        _no_snapshot_symlinks(Path(reference["path"]))
        manifest = _source_entries(load_json(pinned(reference)))
        try:
            declared = {path.relative_to(snapshot_root): sha for path, sha in manifest.items()}
        except ValueError:
            raise ValueError("snapshot_manifest_implementation_mismatch") from None
        if declared != expected:
            raise ValueError("snapshot_manifest_implementation_mismatch")

    for relative, expected_sha in expected.items():
        path = snapshot_root / relative
        _no_snapshot_symlinks(path)
        if not path.is_file():
            raise ValueError("snapshot_file_missing")
        with path.open("rb") as handle:
            actual = hashlib.file_digest(handle, "sha256").hexdigest()
        if actual != expected_sha:
            raise ValueError("snapshot_file_sha256_mismatch")

    import_suffixes = tuple(importlib.machinery.all_suffixes()) + (".pyo", ".pth")
    for path in snapshot.rglob("*"):
        _no_snapshot_symlinks(path)
        if path.is_file() and path.name.endswith(import_suffixes):
            relative = path.relative_to(snapshot_root)
            # Bytecode/native extensions can override verified .py files at import time.
            if path.suffix != ".py" or relative not in expected:
                raise ValueError("unexpected_snapshot_import_file")
    return snapshot


def prepare_replay_import(snapshot: Path) -> None:
    """A full audit requires a fresh process; never reuse another run's package cache."""
    if any(name == "eve_relation_rag" or name.startswith("eve_relation_rag.")
           for name in sys.modules):
        raise ValueError("replay_modules_already_loaded_use_fresh_process")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(snapshot))
    importlib.invalidate_caches()


def audit(run_dir: Path) -> dict[str, Any]:
    receipt = load_json(run_dir / "freeze_receipt.json")
    config_path = pinned(receipt["runtime_config"])
    approval_path = pinned(receipt["input_approval"])
    config, approval = load_json(config_path), load_json(approval_path)
    config_sha = receipt["runtime_config"]["sha256"]
    approval_sha = receipt["input_approval"]["sha256"]
    problems: list[dict[str, str]] = []

    def check(condition: bool, code: str, cell: str = "") -> None:
        if not condition:
            problems.append({"code": code, **({"cell": cell} if cell else {})})

    check(config["status"] == "frozen_for_formal_execution", "configuration_not_frozen")
    check(approval.get("schema_version") == "rag-value-source-input-approval-v1"
          and approval.get("runtime_config_sha256") == config_sha
          and approval.get("gold_approved") is True
          and approval.get("oracle_approved") is True
          and bool(approval.get("reviewer")) and bool(approval.get("user_approval_message"))
          and approval.get("reference_packet_sha256") == config["files"]["input_review"]["sha256"],
          "input_approval_binding_invalid")
    pinned(config["files"]["input_review"])
    question_path = pinned(config["files"]["questions"])
    questions = [json.loads(line) for line in question_path.read_bytes().splitlines()]
    question_by_id = {q["question_id"]: q for q in questions}
    check(len(questions) == len(question_by_id) == 53, "question_matrix_invalid")
    all_systems = tuple(f"S{i}" for i in range(7))
    systems = tuple(config.get("execution_systems", all_systems))
    if (not systems or len(set(systems)) != len(systems)
        or any(s not in all_systems for s in systems)
        or systems != tuple(s for s in all_systems if s in systems)):
        raise ValueError("invalid_execution_systems")
    expected = {f"{qid}.{system}" for qid in question_by_id for system in systems}
    result_dir = run_dir / "results"
    paths = {p.stem: p for p in result_dir.glob("*.json")
             if p.name != "summary.json" and not p.name.endswith(".started.json")}
    missing, extra = sorted(expected - paths.keys()), sorted(paths.keys() - expected)
    report: dict[str, Any] = {
        "schema_version": "rag-value-independent-machine-audit-v1",
        "runtime_config_sha256": config_sha,
        "input_approval_file_sha256": approval_sha,
        "expected_cells": len(expected), "recorded_cells": len(paths),
        "missing_cells_count": len(missing), "extra_cells_count": len(extra),
        "scientific_scoring_performed": False,
        "database_accessed": False, "generation_calls_performed": 0,
    }
    check(not extra, "unexpected_result_files")
    if extra:
        report["extra_cells"] = extra
    if missing or not (result_dir / "summary.json").exists():
        report.update(status="failed" if problems else "incomplete", passed=False,
                      full_record_audit_performed=False,
                      summary_present=(result_dir / "summary.json").exists(),
                      missing_cells_preview=missing[:10],
                      message="Formal results are still incomplete; rerun after the runner exits.",
                      problems=problems)
        return report

    # Verify before any package import; one full audit per fresh CLI process.
    snapshot = verify_source_snapshot(run_dir, config, receipt)
    prepare_replay_import(snapshot)
    from eve_relation_rag.experiments.rag_value_ablation.source_report_queries import (
        SourceReportQuery,
        SourceReportRepository,
    )
    report["source_snapshot_verified"] = True

    repository = SourceReportRepository(
        Path(config["source_packet_directory"]),
        expected_manifest_file_sha256=config["files"]["source_packet_manifest"]["sha256"],
    )
    candidates_path = pinned(config["files"]["gold_oracle_candidates"])
    candidates = [json.loads(line) for line in candidates_path.read_bytes().splitlines()]
    oracles = {entry["question_id"]: entry for entry in approval["oracle_entries"]}
    check(len(approval["oracle_entries"]) == len(oracles) == 53
          and set(oracles) == set(question_by_id), "oracle_matrix_invalid")
    check(approval["oracle_entries"] == [entry["oracle_entry"] for entry in candidates],
          "approved_oracle_differs_from_frozen_candidates")

    # Cache expected result digests rather than thousands of rows per repeated query.
    query_cache: dict[str, str] = {}
    source_groups_checked = 0

    def query_key(raw: dict[str, Any]) -> tuple[str, Any]:
        # Records are serialized JSON: strict tuple fields must use JSON validation,
        # matching the formal runner, while retaining every schema constraint.
        query = SourceReportQuery.model_validate_json(canonical(raw))
        return digest(query.model_dump(mode="json")), query

    def verify_groups(groups: list[dict[str, Any]], cell: str) -> list[str]:
        nonlocal source_groups_checked
        keys = []
        for group in groups:
            key, query = query_key(group["query"])
            keys.append(key)
            if key not in query_cache:
                query_cache[key] = repository.query(query).result_sha256
            actual = digest({k: v for k, v in group.items() if k != "result_sha256"})
            check(actual == group.get("result_sha256") == query_cache[key],
                  "source_result_incomplete_or_changed", cell)
            source_groups_checked += 1
        return keys

    counts: Counter[str] = Counter()
    record_hashes: dict[str, str] = {}
    record_checksums_verified = 0
    generations = 0
    attestations = 0
    oracle_evidence_checked = 0
    allowed_statuses = {
        "completed", "invalid_answer", "mechanical_failure", "evidence_failure", "output_limit",
        "context_overflow", "model_timeout", "tokenization_timeout", "scope_refused",
        "route_refused", "not_applicable", "infrastructure_failure", "execution_interrupted",
        "input_review_required",
    }
    blockers = {"infrastructure_failure", "execution_interrupted", "input_review_required"}
    hex_sha = re.compile(r"[0-9a-f]{64}").fullmatch
    expected_attestation = {
        "schema_version": "rag-value-local-exchange-v1", "operation": "generate",
        "worker_sha256": config["files"]["generation_worker"]["sha256"],
        "model_policy_file_sha256": config["files"]["model_policy"]["sha256"],
        "network_denial_probes_passed": True, "fresh_conversation": True,
        "seed": 0, "temperature": 0,
    }
    for cell in sorted(expected & paths.keys()):
        record = load_json(paths[cell])
        qid, system = cell.rsplit(".", 1)
        identity = {
            "runtime_config_sha256": config_sha, "input_approval_file_sha256": approval_sha,
            "question_id": qid, "system_key": system, "run_mode": "formal_machine_execution",
        }
        check(all(record.get(k) == v for k, v in identity.items()),
              "record_identity_mismatch", cell)
        actual_hash = digest({k: v for k, v in record.items() if k != "record_sha256"})
        check(record.get("record_sha256") == actual_hash, "record_checksum_mismatch", cell)
        record_checksums_verified += int(record.get("record_sha256") == actual_hash)
        record_hashes[cell] = actual_hash
        claim_path = result_dir / f"{cell}.started.json"
        check(claim_path.exists() and load_json(claim_path) == identity,
              "started_record_identity_mismatch", cell)
        status = record.get("status", "missing_status")
        counts[status] += 1
        check(status in allowed_statuses, "unknown_terminal_status", cell)
        check(status not in blockers, "blocking_terminal_status", cell)
        if status in {"completed", "invalid_answer", "mechanical_failure", "evidence_failure",
                      "output_limit"} and system != "S4":
            check(record.get("generation_executed") is True,
                  "model_terminal_status_without_generation", cell)
        check(record.get("scientific_score") is None, "unexpected_scientific_score", cell)
        wording = question_by_id[qid]["question_text_draft"]
        if status != "execution_interrupted":
            check(record.get("question_text") == wording and record.get("question_text_sha256")
                  == hashlib.sha256(wording.encode()).hexdigest(),
                  "question_wording_mismatch", cell)

        diagnostics = record.get("retrieval_diagnostics")
        if diagnostics is not None:
            check(diagnostics.get("diagnostics_sha256") == digest(
                {k: v for k, v in diagnostics.items() if k != "diagnostics_sha256"}),
                "retrieval_diagnostics_checksum_mismatch", cell)
            plan = diagnostics.get("plan", {})
            check(plan.get("question_text_sha256") == hashlib.sha256(wording.encode()).hexdigest()
                  and plan.get("policy_key") == config.get("lexical_query_policy")
                  and plan.get("plan_sha") == digest(
                      {k: v for k, v in plan.items() if k != "plan_sha"}),
                  "retrieval_plan_binding_mismatch", cell)
            context = diagnostics.get("context")
            if context is not None:
                check(context.get("context_sha") == digest(
                    {k: v for k, v in context.items() if k != "context_sha"})
                    and context.get("lexical_plan_sha") == plan.get("plan_sha"),
                    "retrieval_context_binding_mismatch", cell)

        result = record.get("result")
        if record.get("generation_executed") is True:
            generations += 1
            exchange = record.get("exchange_receipt", {})
            receipt_valid = (
                exchange.get("execution_attestation") == expected_attestation
                and isinstance(exchange.get("request_sha256"), str)
                and bool(hex_sha(exchange["request_sha256"]))
                and isinstance(exchange.get("response_sha256"), str)
                and bool(hex_sha(exchange["response_sha256"]))
                and type(exchange.get("sequence")) is int and exchange["sequence"] > 0
            )
            check(receipt_valid, "generation_exchange_receipt_invalid", cell)
            attestations += int(receipt_valid)
            check(record.get("generation_identity") == config["generation_identity"],
                  "generation_identity_mismatch", cell)
            check(isinstance(result, dict) and isinstance(result.get("raw_answer"), str)
                  and result.get("status") == status, "generation_result_missing_or_changed", cell)
        if status == "model_timeout":
            check(record.get("generation_executed") is None
                  and record.get("generation_attempted") is True
                  and record.get("automatic_retry") is False
                  and record.get("worker_terminated") is True,
                  "timeout_provenance_invalid", cell)

        top_groups = record.get("source_results", [])
        top_keys = verify_groups(top_groups, cell)
        evidence = (result.get("evidence") if isinstance(result, dict) else None)
        evidence = evidence or record.get("evidence") or record.get("provisional_evidence")
        evidence_keys: list[str] = []
        if evidence is not None:
            check(evidence.get("pack_sha256") == digest(
                {k: v for k, v in evidence.items() if k != "pack_sha256"}),
                "evidence_checksum_mismatch", cell)
            check(evidence.get("question_id") == qid and evidence.get("question_text") == wording,
                  "evidence_question_mismatch", cell)
            evidence_keys = verify_groups(evidence.get("source_report_groups", []), cell)
        if system in {"S4", "S5"} and status not in blockers | {
                "scope_refused", "route_refused", "not_applicable"}:
            required_keys = [digest(q.model_dump(mode="json"))
                             for q in repository.core53_queries(qid)]
            check(top_keys == required_keys, "structured_query_set_mismatch", cell)
            if system == "S5":
                check(evidence is not None and evidence_keys == required_keys,
                      "hybrid_source_evidence_missing", cell)
        if system == "S6" and status not in blockers | {"scope_refused"}:
            oracle = oracles[qid]
            required_keys = [query_key(q)[0] for q in oracle["source_queries"]]
            check(evidence is not None and evidence_keys == required_keys,
                  "oracle_source_queries_mismatch", cell)
            if evidence is not None:
                check(evidence.get("oracle_entry_sha256") == digest(oracle)
                      and [c["chunk_key"] for c in evidence.get("citations", [])]
                      == oracle["chunk_keys"], "oracle_evidence_binding_mismatch", cell)
                oracle_evidence_checked += 1

    summary_path = result_dir / "summary.json"
    summary = load_json(summary_path)
    expected_summary = {
        "schema_version": "rag-value-source-machine-summary-v1",
        "runtime_config_sha256": config_sha, "input_approval_file_sha256": approval_sha,
        "expected_cells": len(expected), "recorded_cells": len(record_hashes),
        "status_counts": dict(counts), "generation_calls_confirmed": generations,
        "generation_requests_timed_out": counts["model_timeout"],
        "scientific_scoring_completed": False, "public_publication": False,
        "execution_complete": len(record_hashes) == len(expected)
        and not any(counts[s] for s in blockers),
        "records": record_hashes,
    }
    if "execution_systems" in config:
        expected_summary.update(
            execution_systems=list(systems),
            execution_scope="full_matrix" if systems == all_systems else "system_subset",
            full_matrix_expected_cells=371,
            full_matrix_execution_complete=(systems == all_systems
                                            and expected_summary["execution_complete"]),
        )
    check(summary == expected_summary, "summary_differs_from_verified_journal")
    report.update(
        status="failed" if problems else "passed", passed=not problems,
        full_record_audit_performed=True, status_counts=dict(counts),
        record_checksums_verified=record_checksums_verified,
        generation_calls_confirmed=generations, generation_receipts_verified=attestations,
        oracle_evidence_records_checked=oracle_evidence_checked,
        source_result_groups_checked=source_groups_checked,
        distinct_source_queries_replayed=len(query_cache),
        summary_file_sha256=hashlib.sha256(summary_path.read_bytes()).hexdigest(),
        problems=problems,
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True,
                        help="Formal run directory containing freeze_receipt.json and results/.")
    parser.add_argument("--output", type=Path,
                        help="Create a separate audit JSON report; refuses existing files.")
    args = parser.parse_args()
    run_dir = args.run_dir.absolute()
    try:
        report = audit(run_dir)
    except Exception as exc:
        # Do not echo arbitrary exception text or any source/answer content.
        report = {"schema_version": "rag-value-independent-machine-audit-v1",
                  "status": "failed", "passed": False,
                  "error_type": type(exc).__name__,
                  "message": "Audit could not finish; inspect input files and the audit checks.",
                  "scientific_scoring_performed": False, "database_accessed": False,
                  "generation_calls_performed": 0}
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if args.output:
        target = args.output.absolute()
        if target.resolve().is_relative_to((run_dir / "results").resolve()):
            parser.error("--output must be outside the immutable results directory")
        with target.open("x", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    print(rendered)
    return 0 if report["status"] == "passed" else 2 if report["status"] == "incomplete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
