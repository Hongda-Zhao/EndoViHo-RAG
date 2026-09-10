#!/usr/bin/env python3
"""Compare completed source runs without a model, database, or scientific scoring.

Exit codes: 0 = written, 2 = incomplete, 1 = invalid input or existing output.
The output contains aggregate operations and provenance references, never copied answers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ALL_SYSTEMS = tuple(f"S{i}" for i in range(7))
REVISION_SYSTEMS = ("S2", "S3", "S5")
GENERATION_SYSTEMS = ("S0", "S1", "S2", "S3", "S5", "S6")
COMMON_INPUT_FILES = (
    "questions",
    "gold_oracle_candidates",
    "input_review",
    "source_packet_manifest",
    "chunks",
    "corpus_manifest",
    "anchor_manifest",
    "row_bindings",
    "source_selection",
)
COMMON_RUNTIME_FILES = (
    "model_policy",
    "generation_worker",
    "bge_manifest",
    "bge_worker",
    "dependency_lock",
)
COMMON_CONFIG_KEYS = (
    "generation_settings",
    "generation_identity",
    "prompt_policy_sha256",
    "system_definitions",
    "applicability",
    "s1_construction",
)
TERMINAL_STATUSES = {
    "completed",
    "invalid_answer",
    "mechanical_failure",
    "evidence_failure",
    "output_limit",
    "context_overflow",
    "model_timeout",
    "tokenization_timeout",
    "scope_refused",
    "route_refused",
    "not_applicable",
}
GENERATED_STATUSES = {
    "completed",
    "invalid_answer",
    "mechanical_failure",
    "evidence_failure",
    "output_limit",
}
HEX_SHA = re.compile(r"[0-9a-f]{64}")
PAIR_SPECS = (
    ("old_S2__new_S2", "baseline", "S2", "revision", "S2"),
    ("old_S3__new_S3", "baseline", "S3", "revision", "S3"),
    ("old_S5__new_S5", "baseline", "S5", "revision", "S5"),
    ("new_S2__new_S3", "revision", "S2", "revision", "S3"),
    ("new_S3__new_S5", "revision", "S3", "revision", "S5"),
    ("old_S0__new_S2", "baseline", "S0", "revision", "S2"),
    ("old_S0__new_S3", "baseline", "S0", "revision", "S3"),
    ("old_S0__new_S5", "baseline", "S0", "revision", "S5"),
)


class ComparisonError(ValueError):
    """Safe error code, with no answer, source text, or credential contents."""


class IncompleteRun(ComparisonError):
    """The runner has not produced a complete terminal journal."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ComparisonError(code)


def normalize(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key = normalize(key)
            require(key not in result, "duplicate_normalized_json_key")
            result[key] = normalize(item)
        return result
    return value


def canonical(value: Any) -> bytes:
    return json.dumps(
        normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise ComparisonError("nonfinite_json_number")


def parse_json(content: bytes) -> Any:
    return json.loads(content, object_pairs_hook=_object, parse_constant=_constant)


def regular(path: Path) -> None:
    require(not any(p.is_symlink() for p in (path, *path.parents)), "symlink_not_allowed")
    require(path.is_file(), "required_file_missing")


def load_json(path: Path) -> Any:
    regular(path)
    return parse_json(path.read_bytes())


def file_sha(path: Path) -> str:
    regular(path)
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def pinned(item: dict[str, Any], base: Path) -> Path:
    path = Path(item["path"])
    if not path.is_absolute():
        path = base / path
    require(HEX_SHA.fullmatch(item["sha256"]) is not None, "invalid_file_sha256")
    require(file_sha(path) == item["sha256"], "pinned_file_sha256_mismatch")
    return path


def json_lines(path: Path) -> list[dict[str, Any]]:
    return [parse_json(line) for line in path.read_bytes().splitlines() if line.strip()]


def _range(values: list[int]) -> dict[str, int] | None:
    return {"min": min(values), "max": max(values)} if values else None


def _evidence(record: dict[str, Any]) -> dict[str, Any]:
    result = record.get("result") or {}
    return (
        result.get("evidence") or record.get("evidence") or record.get("provisional_evidence") or {}
    )


def _generation_receipt(record: dict[str, Any], config: dict[str, Any]) -> None:
    exchange = record.get("exchange_receipt") or {}
    expected = {
        "schema_version": "rag-value-local-exchange-v1",
        "operation": "generate",
        "worker_sha256": config["files"]["generation_worker"]["sha256"],
        "model_policy_file_sha256": config["files"]["model_policy"]["sha256"],
        "network_denial_probes_passed": True,
        "fresh_conversation": True,
        "seed": config["generation_settings"]["seed"],
        "temperature": config["generation_settings"]["temperature"],
    }
    require(exchange.get("execution_attestation") == expected, "generation_attestation_mismatch")
    for key in ("request_sha256", "response_sha256"):
        require(
            isinstance(exchange.get(key), str) and HEX_SHA.fullmatch(exchange[key]) is not None,
            "generation_receipt_sha256_missing",
        )
    require(
        type(exchange.get("sequence")) is int and exchange["sequence"] > 0,
        "generation_receipt_sequence_invalid",
    )
    require(
        record.get("generation_identity") == config["generation_identity"],
        "generation_identity_mismatch",
    )
    result = record.get("result") or {}
    require(
        isinstance(result.get("raw_answer"), str) and result.get("status") == record["status"],
        "raw_generation_result_missing",
    )


@dataclass
class Run:
    key: str
    path: Path
    config: dict[str, Any]
    source: dict[str, Any]
    questions: dict[str, dict[str, Any]]
    cells: dict[str, dict[str, Any]]


def read_run(run_dir: Path, key: str, systems: tuple[str, ...]) -> Run:
    """Read one large record at a time; retain only operational metadata."""
    require(not any(p.is_symlink() for p in (run_dir, *run_dir.parents)), "symlink_not_allowed")
    run_dir = run_dir.absolute()
    freeze_path = run_dir / "freeze_receipt.json"
    freeze = load_json(freeze_path)
    config_path = pinned(freeze["runtime_config"], run_dir)
    approval_path = pinned(freeze["input_approval"], run_dir)
    config, approval = load_json(config_path), load_json(approval_path)
    config_sha, approval_sha = file_sha(config_path), file_sha(approval_path)
    require(config.get("status") == "frozen_for_formal_execution", "configuration_not_frozen")
    actual_systems = tuple(config.get("execution_systems", ALL_SYSTEMS))
    require(actual_systems == systems, "execution_systems_mismatch")
    if key == "revision":
        require("execution_systems" in config, "revision_subset_not_explicit")
    expected_count = 53 * len(systems)
    require(
        freeze.get("questions") == 53 and freeze.get("expected_cells") == expected_count,
        "freeze_scope_mismatch",
    )
    require(
        freeze.get("common_prompt_sha256") == config.get("prompt_policy_sha256")
        and isinstance(config.get("prompt_policy_sha256"), str)
        and HEX_SHA.fullmatch(config["prompt_policy_sha256"]) is not None,
        "prompt_identity_missing_or_changed",
    )
    require(
        approval.get("schema_version") == "rag-value-source-input-approval-v1"
        and approval.get("runtime_config_sha256") == config_sha
        and approval.get("gold_approved") is True
        and approval.get("oracle_approved") is True
        and approval.get("reference_packet_sha256") == config["files"]["input_review"]["sha256"],
        "input_approval_binding_invalid",
    )
    # These are explicit safe data/runtime artifacts. Never open database_url or private files.
    selected = COMMON_INPUT_FILES + COMMON_RUNTIME_FILES
    paths = {name: pinned(config["files"][name], config_path.parent) for name in selected}
    rows = json_lines(paths["questions"])
    questions = {row["question_id"]: row for row in rows}
    require(len(rows) == len(questions) == 53, "question_matrix_invalid")
    require(all(re.fullmatch(r"[A-Za-z0-9_-]+", qid) for qid in questions), "question_id_not_safe")
    candidates = json_lines(paths["gold_oracle_candidates"])
    require(
        len(candidates) == 53 and {row["question_id"] for row in candidates} == set(questions),
        "gold_oracle_matrix_invalid",
    )
    require(
        approval.get("oracle_entries") == [row["oracle_entry"] for row in candidates],
        "approved_oracle_differs_from_candidates",
    )
    summary_path = run_dir / "results" / "summary.json"
    if not summary_path.is_file():
        raise IncompleteRun("completed_summary_missing")
    summary = load_json(summary_path)
    if summary.get("execution_complete") is not True:
        raise IncompleteRun("execution_not_complete")
    expected = {f"{qid}.{system}" for qid in questions for system in systems}
    found = {
        p.stem: p
        for p in summary_path.parent.glob("*.json")
        if p.name != "summary.json" and not p.name.endswith(".started.json")
    }
    require(set(found) == expected, "result_matrix_missing_or_extra_records")
    require(
        summary.get("expected_cells") == summary.get("recorded_cells") == expected_count
        and summary.get("runtime_config_sha256") == config_sha
        and summary.get("input_approval_file_sha256") == approval_sha
        and summary.get("scientific_scoring_completed") is False,
        "summary_scope_or_identity_invalid",
    )
    if "execution_systems" in config:
        require(
            summary.get("execution_systems") == list(systems)
            and summary.get("full_matrix_expected_cells") == 371
            and summary.get("full_matrix_execution_complete") is (systems == ALL_SYSTEMS)
            and summary.get("execution_scope")
            == ("full_matrix" if systems == ALL_SYSTEMS else "system_subset"),
            "summary_execution_subset_mismatch",
        )
    cells, hashes = {}, {}
    for cell in sorted(expected):
        record = load_json(found[cell])
        qid, system = cell.rsplit(".", 1)
        identity = {
            "runtime_config_sha256": config_sha,
            "input_approval_file_sha256": approval_sha,
            "question_id": qid,
            "system_key": system,
            "run_mode": "formal_machine_execution",
        }
        require(all(record.get(k) == v for k, v in identity.items()), "record_identity_mismatch")
        record_sha = digest({k: v for k, v in record.items() if k != "record_sha256"})
        require(record.get("record_sha256") == record_sha, "record_sha256_mismatch")
        wording = questions[qid]["question_text_draft"]
        require(
            record.get("question_text") == wording
            and record.get("question_text_sha256") == hashlib.sha256(wording.encode()).hexdigest(),
            "record_question_mismatch",
        )
        status = record.get("status")
        require(status in TERMINAL_STATUSES, "unknown_or_blocking_status")
        require(record.get("scientific_score") is None, "unexpected_scientific_score")
        generated = record.get("generation_executed") is True
        require(system != "S4" or not generated, "deterministic_system_claims_generation")
        require(
            generated or system == "S4" or status not in GENERATED_STATUSES,
            "generated_status_without_receipt",
        )
        if generated:
            require(status in GENERATED_STATUSES, "generation_status_inconsistent")
            _generation_receipt(record, config)
        if status == "model_timeout":
            require(
                record.get("generation_executed") is None
                and record.get("generation_attempted") is True
                and record.get("automatic_retry") is False
                and record.get("worker_terminated") is True,
                "timeout_provenance_invalid",
            )
        evidence = _evidence(record)
        citations = evidence.get("citations", [])
        keys = record.get("retrieved_chunk_keys")
        require(
            isinstance(citations, list) and (keys is None or isinstance(keys, list)),
            "evidence_or_retrieval_shape_invalid",
        )
        result = record.get("result") or {}
        input_tokens = result.get("input_tokens", record.get("input_tokens"))
        cells[cell] = {
            "question_id": qid,
            "system_key": system,
            "family": questions[qid]["family"],
            "status": status,
            "generation_available": generated,
            "retrieval_recorded": keys is not None,
            "retrieval_nonempty": bool(keys),
            "retrieved_chunk_count": len(keys or []),
            "evidence_citation_segments": len(citations),
            "answer_cited_chunk_ids": len((result.get("answer") or {}).get("cited_chunk_ids", [])),
            "input_tokens": input_tokens,
            "cell_latency_ns": record.get("cell_latency_ns"),
            "source_run": key,
            "config_sha256": config_sha,
            "record_sha256": record_sha,
            "path": str(found[cell]),
        }
        hashes[cell] = record_sha
        del record, evidence, result
    counts = dict(Counter(cell["status"] for cell in cells.values()))
    generations = sum(cell["generation_available"] for cell in cells.values())
    require(
        summary.get("records") == hashes
        and summary.get("status_counts") == counts
        and summary.get("generation_calls_confirmed") == generations
        and summary.get("generation_requests_timed_out") == counts.get("model_timeout", 0),
        "summary_differs_from_verified_records",
    )
    implementations = config.get("implementation_files")
    require(
        isinstance(implementations, list) and bool(implementations),
        "implementation_manifest_missing",
    )
    for item in implementations:
        require(
            isinstance(item, dict)
            and isinstance(item.get("path"), str)
            and isinstance(item.get("sha256"), str)
            and HEX_SHA.fullmatch(item["sha256"]) is not None,
            "implementation_manifest_invalid",
        )
    source = {
        "run_directory": str(run_dir),
        "freeze_receipt_path": str(freeze_path),
        "freeze_receipt_sha256": file_sha(freeze_path),
        "config_path": str(config_path),
        "config_sha256": config_sha,
        "input_approval_path": str(approval_path),
        "input_approval_sha256": approval_sha,
        "summary_path": str(summary_path),
        "summary_sha256": file_sha(summary_path),
        "execution_systems": list(systems),
        "expected_cells": expected_count,
        "record_checksums_verified": len(cells),
        "generation_receipts_verified": generations,
        "implementation_files": implementations,
        "implementation_manifest_sha256": digest(implementations),
        "implementation_note": "Config-pinned hashes; current live source files are not rehashed.",
    }
    return Run(key, run_dir, config, source, questions, cells)


def statistics(cells: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "observations": len(cells),
        "status_counts": dict(sorted(Counter(c["status"] for c in cells).items())),
        "retrieval_recorded": sum(c["retrieval_recorded"] for c in cells),
        "retrieval_nonempty": sum(c["retrieval_nonempty"] for c in cells),
        "retrieved_chunk_count_range": _range(
            [c["retrieved_chunk_count"] for c in cells if c["retrieval_recorded"]]
        ),
        "evidence_citation_segments_total": sum(c["evidence_citation_segments"] for c in cells),
        "evidence_citation_segments_range": _range(
            [c["evidence_citation_segments"] for c in cells]
        ),
        "answer_cited_chunk_ids_total": sum(c["answer_cited_chunk_ids"] for c in cells),
        "generation_calls_confirmed": sum(c["generation_available"] for c in cells),
        "generation_requests_timed_out": sum(c["status"] == "model_timeout" for c in cells),
        "input_tokens_range": _range(
            [c["input_tokens"] for c in cells if type(c["input_tokens"]) is int]
        ),
        "cell_latency_ns_total": sum(
            c["cell_latency_ns"] for c in cells if type(c["cell_latency_ns"]) is int
        ),
    }


def breakdown(cells: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = list(cells.values())
    return {
        "total": statistics(rows),
        "by_system": {
            s: statistics([c for c in rows if c["system_key"] == s])
            for s in sorted({c["system_key"] for c in rows})
        },
        "by_family": {
            f: statistics([c for c in rows if c["family"] == f])
            for f in sorted({c["family"] for c in rows})
        },
    }


def make_comparison(baseline: Path, revision: Path) -> dict[str, Any]:
    require(baseline.resolve() != revision.resolve(), "same_run_not_comparable")
    old = read_run(baseline, "baseline", ALL_SYSTEMS)
    new = read_run(revision, "revision", REVISION_SYSTEMS)
    for key in COMMON_CONFIG_KEYS:
        require(
            key in old.config and key in new.config and old.config[key] == new.config[key],
            f"common_config_changed:{key}",
        )
    for key in COMMON_INPUT_FILES + COMMON_RUNTIME_FILES:
        require(
            old.config["files"][key]["sha256"] == new.config["files"][key]["sha256"],
            f"common_file_changed:{key}",
        )
    require(old.questions == new.questions, "question_set_changed")
    require(
        old.config.get("raw_sources") == new.config.get("raw_sources"),
        "raw_source_manifest_changed",
    )
    require(
        old.config.get("lexical_query_policy", "original-question-v1") == "original-question-v1"
        and new.config.get("lexical_query_policy") == "rag-value-lexical-query-v1",
        "lexical_policy_revision_mismatch",
    )
    runs = {"baseline": old, "revision": new}
    combined = {
        cell: c for cell, c in old.cells.items() if c["system_key"] not in REVISION_SYSTEMS
    } | new.cells
    observations = {
        cell: {key: c[key] for key in ("source_run", "config_sha256", "record_sha256", "path")}
        for cell, c in sorted(combined.items())
    }
    pairs = []
    for pair_id, left_run, left_system, right_run, right_system in PAIR_SPECS:
        available, exclusions, status_pairs = [], [], Counter()
        validation_statuses = {}
        for qid in old.questions:
            left = runs[left_run].cells[f"{qid}.{left_system}"]
            right = runs[right_run].cells[f"{qid}.{right_system}"]
            status_pairs[f"{left['status']} / {right['status']}"] += 1
            if left["generation_available"] and right["generation_available"]:
                available.append(qid)
                validation_statuses[qid] = {"left": left["status"], "right": right["status"]}
            else:
                reasons = [
                    {
                        "side": side,
                        "status": c["status"],
                        "reason": "no_confirmed_generation:" + c["status"],
                    }
                    for side, c in (("left", left), ("right", right))
                    if not c["generation_available"]
                ]
                exclusions.append(
                    {
                        "question_id": qid,
                        "left_status": left["status"],
                        "right_status": right["status"],
                        "reasons": reasons,
                    }
                )
        pairs.append(
            {
                "pair_id": pair_id,
                "left": {"source_run": left_run, "system_key": left_system},
                "right": {"source_run": right_run, "system_key": right_system},
                "cross_run": left_run != right_run,
                "candidate_questions": 53,
                "available_generation_count": len(available),
                "available_generation_question_ids": available,
                "available_generation_statuses": validation_statuses,
                "excluded_count": len(exclusions),
                "excluded_questions": exclusions,
                "status_pair_counts": dict(sorted(status_pairs.items())),
            }
        )
    common_ids, common_exclusions = [], []
    for qid in old.questions:
        absent = [
            {
                "system_key": s,
                "source_run": combined[f"{qid}.{s}"]["source_run"],
                "status": combined[f"{qid}.{s}"]["status"],
            }
            for s in GENERATION_SYSTEMS
            if not combined[f"{qid}.{s}"]["generation_available"]
        ]
        if absent:
            common_exclusions.append({"question_id": qid, "missing_generations": absent})
        else:
            common_ids.append(qid)
    return {
        "schema_version": "rag-value-source-run-operational-comparison-v1",
        "scientific_scoring_performed": False,
        "database_accessed": False,
        "model_calls_performed": 0,
        "public_publication": False,
        "sources": {key: run.source for key, run in runs.items()},
        "common_settings": {key: old.config[key] for key in COMMON_CONFIG_KEYS},
        "common_input_sha256": {
            key: old.config["files"][key]["sha256"]
            for key in COMMON_INPUT_FILES + COMMON_RUNTIME_FILES
        },
        "comparison_observation_count": len(observations),
        "historical_reference_count": 212,
        "new_execution_count": 159,
        "historical_reference_systems": ["S0", "S1", "S4", "S6"],
        "new_execution_systems": list(REVISION_SYSTEMS),
        "comparison_observations": observations,
        "baseline_statistics": breakdown(old.cells),
        "revision_statistics": breakdown(new.cells),
        "combined_reference_statistics": breakdown(combined),
        "predefined_pairs": pairs,
        "six_generation_systems_common_cohort": {
            "systems": list(GENERATION_SYSTEMS),
            "count": len(common_ids),
            "question_ids": common_ids,
            "excluded_count": len(common_exclusions),
            "excluded_questions": common_exclusions,
        },
        "metric_definitions": {
            "retrieval_nonempty": "Nonempty retrieved_chunk_keys; not relevance or accuracy.",
            "evidence_citation_segments": "Pack citation count, including timeout/overflow.",
            "generation_available": "Confirmed receipt and raw answer; failed validation retained.",
            "pair_exclusion": "No confirmed generation on either side; no expert scoring assumed.",
            "historical_reference": "Baseline S0/S1/S4/S6 paths; no results copied or relabelled.",
            "code_identity": "Frozen config implementation manifest; not a live code re-audit.",
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 关键词修复正式运行：操作性比较",
        "",
        "本地报告包含 371 个比较位置：159 个来自新版 S2/S3/S5 执行，"
        "212 个为旧版 S0/S1/S4/S6 的历史引用。各位置仅保存原始记录来源、配置与记录 SHA-256 "
        "及路径；没有复制旧答案或生成回执，也不表示本轮重新执行了 371 个单元。",
        "",
        "已核对两轮的 53 题、Gold/Oracle、输入包、共同生成设置、提示词及模型身份。"
        "统计仅描述终态、非空检索、提供的引用段数及确认生成；"
        "非空检索不等于找到正确证据，自动校验状态不等于科学正确率。第 7 步专家评分未做。",
        "",
        "## 运行来源与校验",
        "",
    ]
    for key, source in report["sources"].items():
        lines += [
            f"- {key}：`{source['run_directory']}`；"
            f"记录校验 {source['record_checksums_verified']}，"
            f"生成回执 {source['generation_receipts_verified']}。",
            f"  配置 SHA-256：`{source['config_sha256']}`；"
            f"summary SHA-256：`{source['summary_sha256']}`；"
            f"实现清单 SHA-256：`{source['implementation_manifest_sha256']}`。",
        ]
    lines += [
        "",
        "冻结回执、配置、输入采用记录及实现文件清单的路径和 SHA 均见 "
        "[comparison.json](comparison.json)。源码身份沿用配置绑定的实现清单，"
        "此脚本不以当前工作区源码替换历史版本，也不替代独立机器审计。",
        "",
        "## 条件统计",
        "",
        "| 来源 | 条件 | 单元 | 非空检索 / 记录检索 | 证据引用段数 | 确认生成 | 模型超时 | 终态 |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for key in ("baseline", "revision"):
        for system, stats in report[f"{key}_statistics"]["by_system"].items():
            states = "，".join(f"{s}={n}" for s, n in stats["status_counts"].items())
            lines.append(
                f"| {key} | {system} | {stats['observations']} | "
                f"{stats['retrieval_nonempty']} / {stats['retrieval_recorded']} | "
                f"{stats['evidence_citation_segments_total']} | "
                f"{stats['generation_calls_confirmed']} | "
                f"{stats['generation_requests_timed_out']} | {states} |"
            )
    lines += [
        "",
        "证据引用段数包含已保留的超限/超时证据包；不等于答案实际引用数。"
        "JSON 另存答案引用标识符数量、输入 token 范围、题型汇总及累计单元耗时。"
        "跨轮耗时受运行时间和环境影响，不能据此单独归因于检索算法。",
        "",
        "## 预定成对生成集合",
        "",
        "配对分母要求两侧均有确认生成回执及可追溯原始回答。"
        "已生成的拒答、invalid_answer、evidence_failure 等仍保留并列出原终态；"
        "这里的“可用生成”不表示已通过专家评分或具备科学正确性。",
        "",
        "| 对照 | 跨轮 | 可用生成题数 | 排除题数 |",
        "|---|---|---:|---:|",
    ]
    for pair in report["predefined_pairs"]:
        lines.append(
            f"| {pair['pair_id']} | {'是' if pair['cross_run'] else '否'} | "
            f"{pair['available_generation_count']} | {pair['excluded_count']} |"
        )
    for pair in report["predefined_pairs"]:
        lines += [
            "",
            f"### {pair['pair_id']}",
            "",
            "可用生成题号：" + ("、".join(pair["available_generation_question_ids"]) or "无"),
            "",
            "排除题号及原因：",
        ]
        if not pair["excluded_questions"]:
            lines += ["", "无。"]
        else:
            lines += [""]
            for row in pair["excluded_questions"]:
                reasons = "；".join(
                    f"{r['side']}={r['status']}（无确认生成）" for r in row["reasons"]
                )
                lines.append(f"- {row['question_id']}：{reasons}。")
    common = report["six_generation_systems_common_cohort"]
    lines += [
        "",
        "## 共同分母与范围边界",
        "",
        f"六个生成条件 S0/S1/S2/S3/S5/S6 的共同生成分母为 **{common['count']}**。"
        "全部排除题号及缺失系统见 JSON；历史 S1 的预算超限没有变为可作答结果。"
        "历史 S6 大集合超限也不能视为可作答的 Oracle 上限。",
        "",
        "成对集合各有分母，不能拼成完整七条件答案质量结论。"
        "本报告不判断哪个系统最好，不计算科学正确率，也不补造专家评分。",
        "",
    ]
    return "\n".join(lines)


def write_comparison(baseline: Path, revision: Path, output: Path) -> dict[str, Any]:
    target = output.absolute()
    require(not target.exists() and not target.is_symlink(), "output_already_exists")
    for run in (baseline, revision):
        require(
            not target.resolve().is_relative_to((run / "results").resolve()),
            "output_inside_immutable_results",
        )
    report = make_comparison(baseline, revision)
    rendered_json = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    rendered_markdown = render_markdown(report)
    target.mkdir(parents=True, exist_ok=False)
    with (target / "comparison.json").open("x", encoding="utf-8") as handle:
        handle.write(rendered_json)
    with (target / "COMPARISON.cn.md").open("x", encoding="utf-8") as handle:
        handle.write(rendered_markdown)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline", type=Path, required=True, help="Completed 53 × 7 run directory."
    )
    parser.add_argument(
        "--revision", type=Path, required=True, help="Completed S2/S3/S5 run directory."
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="New local comparison directory."
    )
    args = parser.parse_args()
    try:
        report = write_comparison(args.baseline, args.revision, args.output)
    except IncompleteRun as exc:
        print(f"尚未完成，未生成比较报告：{exc}", file=sys.stderr)
        return 2
    except ComparisonError as exc:
        print(f"比较被拒绝，未覆盖任何既有报告：{exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(
            f"比较未完成：输入文件缺失或结构不符合要求（{type(exc).__name__}）。", file=sys.stderr
        )
        return 1
    print(
        f"已生成本地操作性比较：{report['comparison_observation_count']} 个位置，"
        f"{report['new_execution_count']} 个新执行、"
        f"{report['historical_reference_count']} 个历史引用；未进行科学评分。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
