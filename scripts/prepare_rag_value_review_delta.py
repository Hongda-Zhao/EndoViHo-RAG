#!/usr/bin/env python3
"""Carry saved decisions into one authoring delta without changing the reviewed workbook."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from eve_relation_rag.experiments.rag_value_ablation.annotation_workload import (
    build_annotation_workload,
)
from eve_relation_rag.experiments.rag_value_ablation.question_revision import HISTORICAL_PACKAGE
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256

ROOT = Path(__file__).resolve().parents[1]
SAVED = ROOT / "outputs/01a060a2-5481-7ce2-a919-8d003188164a"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt_path = SAVED / "rag_value_review_intake_20260907/object_review_receipt.json"
    amendment_path = SAVED / "rag_value_region_terms_20260907/authoring_amendment.json"
    receipt = json.loads(receipt_path.read_bytes())
    amendment = json.loads(amendment_path.read_bytes())
    workbook = Path(amendment["output_workbook"])
    if sha(workbook) != "f211a529379e88427e7e3e5f46e8faa5143ab4db8fbfc8c3323625cd5d59600d":
        raise ValueError("saved workbook changed; preserve and inspect newer decisions")
    if sha(receipt_path) != amendment["prior_receipt_sha256"]:
        raise ValueError("saved receipt differs from the amendment source")
    workload = build_annotation_workload(ROOT / HISTORICAL_PACKAGE)
    entities = []
    for original in receipt["entities"]:
        row = dict(original)
        if row["entity_slot"] == "REPORTED_REGION_A":
            row["historical_label"] = row["selected_label"]
            row["selected_label"] = amendment["selection"]["selected_reported_region"]
            row["authoring_decision"] = "accepted_by_saved_amendment"
        row["candidate_stable_key"] = (
            "assembly:ncbi:" + row["selected_label"]
            if row["entity_slot"] == "ASSEMBLY_A" else None
        )
        row["candidate_is_verified_release_member"] = False
        row["technical_binding_status"] = "not_bound_to_experiment_release"
        entities.append(row)
    payload = {
        "schema_version": "rag-value-review-delta-v1", "artifact_kind": "authoring_only",
        "source_workbook_sha256": sha(workbook), "source_receipt_sha256": sha(receipt_path),
        "source_amendment_sha256": sha(amendment_path),
        "question_revision": workload["question_revision"],
        "entities": entities, "shared_evidence_candidates": receipt["evidence_as_saved"],
        "questions": workload["questions"],
        "gold_approvals_created": 0, "oracle_approvals_created": 0,
        "technical_work_is_not_a_human_question": True,
    }
    args.output.mkdir(exist_ok=False)
    (args.output / "review_delta.json").write_bytes(canonical_json_bytes({
        **payload, "delta_sha256": canonical_json_sha256(payload),
    }) + b"\n")
    (args.output / "REVIEW.cn.md").write_text(
        "# 仅核对尚未批准的科学内容\n\n"
        "已保留53题题意、分组、三个对象的接受意见，以及文献区域替换和术语规则。"
        "原Excel及其意见没有修改。技术版本和稳定键缺口由工程处理，不要求重填。\n\n"
        "1. 共用事实证据 GEVE-01、ERV-01、VPH-01：逐篇核实保存的候选摘要、"
        "定位、作者原话及确定程度；未报告概率保持空缺。每项核对一次，多题引用。\n"
        "2. 对照这些事实和后续完整数据集合，核对53题的答案范围；12道边界题"
        "分别标明可回答条件或拒答边界。大致区域不评分为精确碱基坐标。\n"
        "3. 对已核实证据分别决定 Gold 和 Oracle 用途；对象接受与题意同意"
        "不会自动转换为这两项批准。可以批准一次完整、精确的批次。\n\n"
        "以上仍是候选核对单，尚未具备全部事实和完整集合，不能签为最终Gold。"
        "运行后盲审另行进行：20–30份真实答案、至少100条原子主张、2位独立审阅者；"
        "当前没有生成标签或一致性分数。\n", encoding="utf-8",
    )
    print("53 questions / 9 saved objects / 3 shared evidence candidates; zero new approvals")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
