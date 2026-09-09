#!/usr/bin/env python3
"""Create an authoring-only minimal annotation inventory; never run a provider."""

from __future__ import annotations

import argparse
from pathlib import Path

from eve_relation_rag.experiments.rag_value_ablation.annotation_workload import (
    build_annotation_workload,
)
from eve_relation_rag.experiments.rag_value_ablation.authoring_review import AuthoringReviewError
from eve_relation_rag.literature.hashing import canonical_json_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("classified_package", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        plan = build_annotation_workload(args.classified_package)
        parent = args.output.parent.resolve(strict=True)
        if args.output.is_symlink() or args.output.exists():
            raise ValueError("output already exists")
        target = parent / args.output.name
        target.mkdir(exist_ok=False)
        report = (
            "# 本轮最少人工标注\n\n"
            f"保留已确认的 {plan['question_count']} 题；不重审题意、题数或分组。\n\n"
            f"1. 保留已确认运行设置和对象选择，核对 {plan['shared_entity_count']} 个对象的"
            "新增精确绑定，不重复选模型或重审题意。\n"
            "2. 正确事实和文献段落只核实一次，给它们编号，多题引用。\n"
            f"3. 核对 {plan['question_count']} 题各自引用的答案范围和证据；"
            "可以逐题看完后批准一个精确批次，不必写多篇长答案。\n"
            "4. 同一证据可用于 Gold 与 Oracle，但须明确同意 Oracle 用途。\n\n"
            f"其中 {plan['literature_or_hybrid_question_count']} 题涉及文献，"
            f"{plan['boundary_question_count']} 题核对回答边界；"
            "边界题不是一律拒答。不同证据究竟有几组，需要人工选证后才能确定。\n\n"
            "完整结论还需运行后盲审：至少20份真实答案、100条真实原子主张，"
            "由2位独立专家各自评审。\n\n"
            "本文件只是填写清单：没有 Gold、Oracle、科学评分或运行授权。"
            "ASSEMBLY_B（旧表的第二个 Assembly）未被核心题引用，无需填写。\n\n"
            f"清单校验值：`{plan['workload_sha256']}`\n"
        )
        for name, content in (
            ("annotation_workload.json", canonical_json_bytes(plan) + b"\n"),
            ("README.cn.md", report.encode("utf-8")),
        ):
            with (target / name).open("xb") as handle:
                handle.write(content)
    except (AuthoringReviewError, OSError, ValueError):
        print("Preparation failed: verify the classified packet and use a new output directory.")
        return 2
    print("authoring_only: 53 questions, 9 shared objects; no Gold or execution authority.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
