"""Synthetic, retrieval/model-free checks for immutable cross-run comparison."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from scripts import compare_rag_value_source_runs as comparison


def save(path: Path, value: object) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return {"path": str(path), "sha256": comparison.file_sha(path)}


@pytest.fixture
def run_pair(tmp_path: Path):
    # Resolve macOS /var aliases before exercising strict no-symlink input checks.
    tmp_path = tmp_path.resolve()
    questions = [
        {
            "question_id": f"Q{i:02}",
            "question_text_draft": f"Synthetic café {i}",
            "family": "structured",
        }
        for i in range(53)
    ]
    candidates = [
        {
            "question_id": q["question_id"],
            "oracle_entry": {
                "question_id": q["question_id"],
                "chunk_keys": [],
                "source_queries": [],
            },
        }
        for q in questions
    ]
    files = {}
    for key in comparison.COMMON_INPUT_FILES + comparison.COMMON_RUNTIME_FILES:
        path = tmp_path / "inputs" / f"{key}.json"
        files[key] = save(path, {"synthetic": key})
        if key in {"questions", "gold_oracle_candidates"}:
            rows = questions if key == "questions" else candidates
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            files[key]["sha256"] = comparison.file_sha(path)

    def build(key: str, *, temperature: float = 0) -> Path:
        root = tmp_path / key
        systems = comparison.ALL_SYSTEMS if key == "baseline" else comparison.REVISION_SYSTEMS
        config = {
            "status": "frozen_for_formal_execution",
            "files": files,
            "generation_settings": {"seed": 0, "temperature": temperature},
            "generation_identity": {"synthetic_model": "test-only"},
            "prompt_policy_sha256": "a" * 64,
            "system_definitions": ["synthetic"],
            "applicability": "synthetic",
            "s1_construction": "synthetic",
            "implementation_files": [{"path": "frozen/test-only.py", "sha256": "b" * 64}],
            "raw_sources": [],
        }
        if key != "baseline":
            config.update(
                execution_systems=list(systems), lexical_query_policy="rag-value-lexical-query-v1"
            )
        config_ref = save(root / "runtime_config.frozen.json", config)
        approval_ref = save(
            root / "input_approval.json",
            {
                "schema_version": "rag-value-source-input-approval-v1",
                "runtime_config_sha256": config_ref["sha256"],
                "reference_packet_sha256": files["input_review"]["sha256"],
                "gold_approved": True,
                "oracle_approved": True,
                "oracle_entries": [row["oracle_entry"] for row in candidates],
            },
        )
        save(
            root / "freeze_receipt.json",
            {
                "runtime_config": config_ref,
                "input_approval": approval_ref,
                "questions": 53,
                "expected_cells": 53 * len(systems),
                "common_prompt_sha256": config["prompt_policy_sha256"],
            },
        )
        hashes, counts, generated_count = {}, Counter(), 0
        for q in questions:
            for system in systems:
                status = "context_overflow" if system == "S1" else "completed"
                if q["question_id"] == "Q00":
                    status = "scope_refused"
                if key != "baseline" and q["question_id"] == "Q01" and system == "S3":
                    status = "model_timeout"
                if key != "baseline" and q["question_id"] == "Q02" and system == "S2":
                    status = "evidence_failure"
                generated = status in {"completed", "evidence_failure"} and system != "S4"
                record = {
                    "runtime_config_sha256": config_ref["sha256"],
                    "input_approval_file_sha256": approval_ref["sha256"],
                    "question_id": q["question_id"],
                    "system_key": system,
                    "question_text": q["question_text_draft"],
                    "question_text_sha256": hashlib.sha256(
                        q["question_text_draft"].encode()
                    ).hexdigest(),
                    "run_mode": "formal_machine_execution",
                    "status": status,
                    "generation_executed": generated,
                    "scientific_score": None,
                }
                if generated:
                    record.update(
                        generation_identity=config["generation_identity"],
                        result={
                            "status": status,
                            "raw_answer": "synthetic raw answer only",
                            "evidence": {"citations": [{"chunk_key": "synthetic"}]},
                            "answer": {"cited_chunk_ids": ["synthetic"]},
                        },
                        exchange_receipt={
                            "request_sha256": "c" * 64,
                            "response_sha256": "d" * 64,
                            "sequence": 1,
                            "execution_attestation": {
                                "schema_version": "rag-value-local-exchange-v1",
                                "operation": "generate",
                                "worker_sha256": files["generation_worker"]["sha256"],
                                "model_policy_file_sha256": files["model_policy"]["sha256"],
                                "network_denial_probes_passed": True,
                                "fresh_conversation": True,
                                "seed": 0,
                                "temperature": temperature,
                            },
                        },
                    )
                if status == "model_timeout":
                    record.update(
                        generation_executed=None,
                        generation_attempted=True,
                        automatic_retry=False,
                        worker_terminated=True,
                    )
                if system in comparison.REVISION_SYSTEMS:
                    record["retrieved_chunk_keys"] = ["synthetic"] if generated else []
                cell = f"{q['question_id']}.{system}"
                record["record_sha256"] = comparison.digest(record)
                save(root / "results" / f"{cell}.json", record)
                hashes[cell] = record["record_sha256"]
                counts[status] += 1
                generated_count += generated
        summary = {
            "runtime_config_sha256": config_ref["sha256"],
            "input_approval_file_sha256": approval_ref["sha256"],
            "expected_cells": 53 * len(systems),
            "recorded_cells": len(hashes),
            "execution_complete": True,
            "scientific_scoring_completed": False,
            "records": hashes,
            "status_counts": dict(counts),
            "generation_calls_confirmed": generated_count,
            "generation_requests_timed_out": counts["model_timeout"],
        }
        if key != "baseline":
            summary.update(
                execution_systems=list(systems),
                execution_scope="system_subset",
                full_matrix_expected_cells=371,
                full_matrix_execution_complete=False,
            )
        save(root / "results" / "summary.json", summary)
        return root

    return build("baseline"), build("revision"), build


def test_comparison_retains_original_sources_without_copying_answers(run_pair, tmp_path: Path):
    baseline, revision, _ = run_pair
    output = tmp_path / "comparison"
    report = comparison.write_comparison(baseline, revision, output)
    observations = report["comparison_observations"]
    assert len(observations) == 371
    assert Counter(r["source_run"] for r in observations.values()) == {
        "baseline": 212,
        "revision": 159,
    }
    for cell, row in observations.items():
        assert set(row) == {"source_run", "config_sha256", "record_sha256", "path"}
        expected_run = revision if cell.endswith((".S2", ".S3", ".S5")) else baseline
        assert Path(row["path"]) == expected_run / "results" / f"{cell}.json"
    assert report["six_generation_systems_common_cohort"]["count"] == 0
    assert "synthetic raw answer only" not in (output / "comparison.json").read_text()
    assert (output / "COMPARISON.cn.md").is_file()


def test_pair_cohorts_retain_validation_failure_and_explain_timeout(run_pair):
    baseline, revision, _ = run_pair
    report = comparison.make_comparison(baseline, revision)
    pairs = {pair["pair_id"]: pair for pair in report["predefined_pairs"]}
    s2 = pairs["old_S2__new_S2"]
    assert "Q02" in s2["available_generation_question_ids"]
    assert s2["available_generation_statuses"]["Q02"]["right"] == "evidence_failure"
    s3 = pairs["old_S3__new_S3"]
    timeout = next(row for row in s3["excluded_questions"] if row["question_id"] == "Q01")
    assert timeout["reasons"] == [
        {
            "side": "right",
            "status": "model_timeout",
            "reason": "no_confirmed_generation:model_timeout",
        }
    ]
    assert s3["available_generation_count"] + s3["excluded_count"] == 53


def test_tampered_record_hash_rejected_before_output_creation(run_pair, tmp_path: Path):
    baseline, revision, _ = run_pair
    path = revision / "results" / "Q02.S2.json"
    record = json.loads(path.read_bytes())
    record["result"]["raw_answer"] = "tampered"
    save(path, record)
    output = tmp_path / "must-not-exist"
    with pytest.raises(comparison.ComparisonError, match="record_sha256_mismatch"):
        comparison.write_comparison(baseline, revision, output)
    assert not output.exists()


def test_pinned_config_bytes_tampering_rejected(run_pair):
    baseline, revision, _ = run_pair
    with (revision / "runtime_config.frozen.json").open("a") as handle:
        handle.write("\n")
    with pytest.raises(comparison.ComparisonError, match="pinned_file_sha256_mismatch"):
        comparison.make_comparison(baseline, revision)


def test_valid_but_different_generation_settings_rejected(run_pair):
    baseline, _, build = run_pair
    changed = build("changed", temperature=0.5)
    with pytest.raises(
        comparison.ComparisonError, match="common_config_changed:generation_settings"
    ):
        comparison.make_comparison(baseline, changed)


def test_incomplete_revision_and_existing_output_refused(run_pair, tmp_path: Path):
    baseline, revision, _ = run_pair
    (revision / "results" / "summary.json").unlink()
    with pytest.raises(comparison.IncompleteRun, match="completed_summary_missing"):
        comparison.make_comparison(baseline, revision)
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep")
    with pytest.raises(comparison.ComparisonError, match="output_already_exists"):
        comparison.write_comparison(baseline, revision, output)
    assert sentinel.read_text() == "keep"


def test_canonical_record_hash_normalizes_nfc_and_rejects_key_collisions():
    assert comparison.digest({"cafe\u0301": ["e\u0301"]}) == comparison.digest({"café": ["é"]})
    with pytest.raises(comparison.ComparisonError, match="duplicate_normalized_json_key"):
        comparison.digest({"cafe\u0301": 1, "café": 2})
