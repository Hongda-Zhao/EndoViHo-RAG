#!/usr/bin/env python3
"""Check a versioned lexical policy against pinned local evidence without an LLM."""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from eve_relation_rag.experiments.rag_value_ablation.lexical_query import LEXICAL_QUERY_POLICY_KEY
from eve_relation_rag.experiments.rag_value_ablation.source_corpus import (
    SourceCorpusEvidenceAdapter,
    read_pinned,
)
from eve_relation_rag.experiments.rag_value_ablation.source_experiment import (
    SourceExperiment,
    atomic_json,
)
from eve_relation_rag.literature.hashing import canonical_json_sha256
from eve_relation_rag.literature.validation import RebuildValidationReport
from eve_relation_rag.planning.scope_policy import contains_forbidden_topic

ROOT = Path(__file__).resolve().parents[1]


def run_checks(args: argparse.Namespace) -> dict[str, Any]:
    config = json.loads(read_pinned(args.config, args.config_sha256))
    cases = json.loads(read_pinned(args.cases, args.cases_sha256))
    if args.output.exists():
        raise ValueError("choose a new directory; existing validation runs are retained")
    args.output.mkdir(parents=True)
    implementation = [
        ROOT / "src/eve_relation_rag/experiments/rag_value_ablation" / f
        for f in ("lexical_query.py", "lexical_context.py", "planned_fts.py",
                  "source_corpus.py", "source_experiment.py")
    ] + [ROOT / "src/eve_relation_rag/retrieval/literature/repository.py", Path(__file__)]

    def implementation_hashes() -> dict[str, str]:
        return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in implementation}

    code_at_start = implementation_hashes()
    atomic_json(args.output / "implementation_at_start.json", code_at_start)

    def config_bytes(key: str) -> bytes:
        item = config["files"][key]
        return read_pinned(Path(item["path"]), item["sha256"])

    artifacts: dict[str, Any] = {}
    for key, item in cases["artifacts"].items():
        p = Path(item["path"])
        if not p.is_absolute():
            p = ROOT / p
        raw = read_pinned(p, item["sha256"])
        # Only the grading side reads expected evidence. The retriever gets question text alone.
        if key == "adopted_evidence_groups":
            artifacts[key] = json.loads(raw)
    url = config_bytes("database_url").decode().strip()
    parsed = make_url(url)
    if (
        parsed.host != "127.0.0.1" or parsed.port != 5432
        or parsed.database != config["database_name"]
        or parsed.username != config["database_reader_role"]
    ):
        raise ValueError("database differs from the isolated read-only experiment")
    engine = create_engine(
        url, connect_args={"connect_timeout": 5, "options": (
            "-c default_transaction_read_only=on -c statement_timeout=30000"
        )}, hide_parameters=True,
    )
    with engine.connect() as conn:
        if conn.scalar(text("SHOW transaction_read_only")) != "on":
            raise ValueError("retrieval validation requires a read-only database connection")
    adapter = SourceCorpusEvidenceAdapter(
        engine,
        RebuildValidationReport.model_validate_json(config_bytes("corpus_rebuild")),
        chunks_path=Path(config["files"]["chunks"]["path"]),
        chunks_sha256=config["files"]["chunks"]["sha256"],
        lexical_query_policy=LEXICAL_QUERY_POLICY_KEY,
    )
    results = []
    for case in cases["cases"]:
        question = case["input"]["question_text"]
        if hashlib.sha256(question.encode()).hexdigest() != case["input"]["question_text_sha256"]:
            raise ValueError("case question checksum differs")
        expected = case["expected"]
        problems: list[str] = []
        record: dict[str, Any] = {"case_id": case["case_id"], "question_text": question}
        if contains_forbidden_topic(question):
            runtime = SourceExperiment.__new__(SourceExperiment)
            refusal = runtime.execute_cell(
                AnyProvider(),
                {"question_id": case["case_id"], "question_text_draft": question,
                 "family": "unsupported"},
                "S2",
            )
            record.update(status=refusal["status"], retrieval_calls=0, result=refusal)
            if expected["outcome"] != "scope_refused":
                problems.append("unexpected_scope_refusal")
        else:
            result = adapter.retrieve_detailed(question)
            record.update(status="retrieved" if result.keys else "no_chunks_retrieved",
                          retrieval_calls=1, result=asdict(result))
            if len(result.keys) > 100 or len(result.citations) > 8:
                problems.append("candidate_or_evidence_budget_exceeded")
            if len(set(result.keys)) != len(result.keys):
                problems.append("duplicate_candidate_keys")
            evidence_keys = {c.chunk_key for c in result.citations}
            if len(evidence_keys) != len(result.citations):
                problems.append("duplicate_evidence_keys")
            group_checks = []
            for group in expected.get("all_required_groups", []):
                reference = group["allowed_chunk_keys_reference"]
                value = artifacts[reference["artifact_id"]]
                for part in reference["json_pointer"].strip("/").split("/"):
                    value = value[part.replace("~1", "/").replace("~0", "~")]
                allowed = set(value)
                if (len(allowed) != reference["count"]
                    or canonical_json_sha256(sorted(allowed)) != reference["sorted_keys_sha256"]):
                    raise ValueError("expected evidence group checksum differs")
                valid = {
                    k for k in allowed
                    if adapter.chunks[k].document_key in group["expected_document_keys"]
                }
                candidate_hits = len(valid.intersection(result.keys))
                evidence_hits = len(valid.intersection(evidence_keys))
                passed = (
                    candidate_hits >= group["minimum_matches_in_candidate_top_100"]
                    and evidence_hits >= group["minimum_matches_in_evidence_top_8"]
                )
                group_checks.append({"group_id": group["group_id"],
                                     "candidate_matches": candidate_hits,
                                     "evidence_matches": evidence_hits, "passed": passed})
                if not passed:
                    problems.append(f"required_evidence_missing:{group['group_id']}")
            record["group_checks"] = group_checks
            if expected["outcome"] == "no_chunks_retrieved" and (
                result.keys or result.citations
                or expected["required_warning"] not in result.warnings
            ):
                problems.append("unknown_entity_did_not_return_explicit_empty_result")
            if expected["outcome"] == "scope_refused":
                problems.append("scope_refusal_did_not_precede_retrieval")
        record.update(passed=not problems, problems=problems)
        atomic_json(args.output / f"{case['case_id']}.json", record)
        results.append(record)
    scan = []
    if args.scan_questions:
        for line in config_bytes("questions").splitlines():
            q = json.loads(line)
            wording = q["question_text_draft"]
            if contains_forbidden_topic(wording):
                record = {"question_id": q["question_id"], "status": "scope_refused"}
            else:
                result = adapter.retrieve_detailed(wording)
                record = {"question_id": q["question_id"],
                          "status": "retrieved" if result.keys else "no_chunks_retrieved",
                          "candidate_count": len(result.keys),
                          "evidence_count": len(result.citations),
                          "warnings": result.warnings, "diagnostics": result.diagnostics}
            scan.append(record)
        atomic_json(args.output / "question_scan.json", scan)
    engine.dispose()
    if implementation_hashes() != code_at_start:
        raise ValueError("implementation changed while retrieval checks were running")
    summary = {
        "schema_version": "rag-value-lexical-retrieval-check-v1",
        "mode": "development_retrieval_only", "policy_key": LEXICAL_QUERY_POLICY_KEY,
        "cases_file_sha256": args.cases_sha256,
        "baseline_data_config_sha256": args.config_sha256,
        "case_count": len(results), "passed": all(r["passed"] for r in results),
        "cases": [{"case_id": r["case_id"], "passed": r["passed"],
                   "problems": r["problems"]} for r in results],
        "scan_status_counts": {status: sum(r["status"] == status for r in scan)
                               for status in {r["status"] for r in scan}},
        "model_calls": 0, "database_mutations": False, "scientific_scoring_completed": False,
        "implementation_sha256": code_at_start,
    }
    atomic_json(args.output / "summary.json", summary)
    return summary


class AnyProvider:
    """A sentinel proving scope-refused cells do not need generation dependencies."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError("retrieval-only validation must not access a generation provider")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--cases-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scan-questions", action="store_true")
    args = parser.parse_args()
    try:
        summary = run_checks(args)
    except Exception as exc:
        # Connection exceptions may embed private connection details.
        print(json.dumps({"passed": False, "error_type": type(exc).__name__}))
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
