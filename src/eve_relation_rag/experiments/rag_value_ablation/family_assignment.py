"""Bind the approved UNSUP-09 hybrid grouping without approving any scientific facts."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Literal, Self

from pydantic import model_validator

from eve_relation_rag.experiments.rag_value_ablation.authoring_review import (
    AuthoringReviewError,
    AuthoringReviewLedger,
    validate_authoring_ledger,
)
from eve_relation_rag.experiments.rag_value_ablation.contracts import QuestionFamily
from eve_relation_rag.experiments.rag_value_ablation.scope_amendment import (
    Core53ScopeAmendment,
    _read_input,
    core53_package_files,
)
from eve_relation_rag.literature.contracts import (
    QuestionText,
    Sha256,
    StableToken,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256

HYBRID_DECISION_TEXT = "可以，推进"
_SCOPE_SHA256 = "5a4adf894fc61d89591b5407b59adcc0d3f2182ca3c67b01bb4aa809b8d85a4e"
_FAMILY_NAMES = {
    "structured": "查数据库", "literature": "查文献",
    "hybrid": "两边对照（混合问题）", "unsupported": "拒答或执行边界测试",
}


class HybridFamilyAssignment(StrictFrozenSchema):
    """The user's family decision is neither Gold nor a HumanApproval signature."""

    schema_version: Literal["rag-value-hybrid-family-assignment-v1"]
    artifact_kind: Literal["authoring_family_only"]
    template_id: Literal["UNSUP-09"]
    source_record_sha256: Sha256
    source_scope_sha256: Literal[
        "5a4adf894fc61d89591b5407b59adcc0d3f2182ca3c67b01bb4aa809b8d85a4e"
    ]
    source_package_manifest_sha256: Sha256
    previous_family: None
    assigned_family: Literal["hybrid"]
    proposal_text: Literal[
        "UNSUP-09归为混合问题（两边对照）；53题分布为结构化16、文献16、混合9、拒答／边界12。"
    ]
    decision_text: Literal["可以，推进"]
    decision_source: Literal["user_message_in_current_thread"]
    decision_scope: Literal["UNSUP-09_scoring_family_only"]
    source_message_timestamp: None
    reviewer_identity: None
    scientific_approval: None
    runtime_authorization: None
    executable: Literal[False]
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        if self.manifest_sha256 != canonical_json_sha256(
            self.model_dump(mode="json", exclude={"manifest_sha256"})
        ):
            raise ValueError("family assignment checksum does not match")
        return self


class ClassifiedCandidate(StrictFrozenSchema):
    """A new projection with its own checksum; the source ledger is never rewritten."""

    schema_version: Literal["rag-value-classified-authoring-candidate-v1"]
    template_id: StableToken
    source_record_sha256: Sha256
    source_scope_sha256: Sha256
    family_assignment_sha256: Sha256
    family: QuestionFamily
    question_text_template: QuestionText
    question_text_sha256: Sha256
    entity_slots: tuple[StableToken, ...]
    benchmark_review_status: Literal["pending"]
    gold: None
    oracle: None
    approval: None
    executable: Literal[False]
    record_sha256: Sha256

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        if self.question_text_sha256 != hashlib.sha256(
            self.question_text_template.encode("utf-8")
        ).hexdigest():
            raise ValueError("classified question text checksum does not match")
        if self.record_sha256 != canonical_json_sha256(
            self.model_dump(mode="json", exclude={"record_sha256"})
        ):
            raise ValueError("classified candidate checksum does not match")
        return self


def build_hybrid_family_assignment(
    ledger: AuthoringReviewLedger, scope: Core53ScopeAmendment, *, decision_text: str,
) -> HybridFamilyAssignment:
    """Only the displayed candidate and explicit contextual decision can be recorded."""

    ledger = validate_authoring_ledger(ledger)
    files = core53_package_files(ledger, scope)
    if scope.manifest_sha256 != _SCOPE_SHA256 or decision_text != HYBRID_DECISION_TEXT:
        raise AuthoringReviewError("family decision must match the reviewed core-53 proposal")
    source = next(row for row in ledger.records if row.template_id == "UNSUP-09")
    payload = {
        "schema_version": "rag-value-hybrid-family-assignment-v1",
        "artifact_kind": "authoring_family_only",
        "template_id": source.template_id,
        "source_record_sha256": source.record_sha256,
        "source_scope_sha256": scope.manifest_sha256,
        "source_package_manifest_sha256": hashlib.sha256(
            files["package_manifest.json"]
        ).hexdigest(),
        "previous_family": None,
        "assigned_family": "hybrid",
        "proposal_text": (
            "UNSUP-09归为混合问题（两边对照）；53题分布为结构化16、文献16、混合9、拒答／边界12。"
        ),
        "decision_text": decision_text,
        "decision_source": "user_message_in_current_thread",
        "decision_scope": "UNSUP-09_scoring_family_only",
        "source_message_timestamp": None,
        "reviewer_identity": None,
        "scientific_approval": None,
        "runtime_authorization": None,
        "executable": False,
    }
    return HybridFamilyAssignment.model_validate_json(_json({
        **payload, "manifest_sha256": canonical_json_sha256(payload),
    }))


def classified_package_files(
    ledger: AuthoringReviewLedger, scope: Core53ScopeAmendment,
    assignment: HybridFamilyAssignment,
) -> dict[str, bytes]:
    """Build and fully validate the effective grouping, blank worksheets and report."""

    ledger = validate_authoring_ledger(ledger)
    files = core53_package_files(ledger, scope)
    if type(assignment) is not HybridFamilyAssignment:
        raise AuthoringReviewError("classification requires an exact family assignment type")
    try:
        assignment = HybridFamilyAssignment.model_validate_json(canonical_json_bytes(assignment))
    except (ValueError, TypeError) as exc:
        raise AuthoringReviewError("family assignment failed revalidation") from exc
    expected = build_hybrid_family_assignment(ledger, scope, decision_text=assignment.decision_text)
    if assignment != expected:
        raise AuthoringReviewError("family assignment differs from the reviewed source binding")

    projected: list[ClassifiedCandidate] = []
    for row in ledger.records:
        if row.disposition != "core":
            continue
        payload = {
            "schema_version": "rag-value-classified-authoring-candidate-v1",
            "template_id": row.template_id,
            "source_record_sha256": row.record_sha256,
            "source_scope_sha256": scope.manifest_sha256,
            "family_assignment_sha256": assignment.manifest_sha256,
            "family": assignment.assigned_family if row.template_id == "UNSUP-09" else row.family,
            "question_text_template": row.question_text_template,
            "question_text_sha256": hashlib.sha256(row.question_text_template.encode()).hexdigest(),
            "entity_slots": row.entity_slots,
            "benchmark_review_status": "pending",
            "gold": None, "oracle": None, "approval": None, "executable": False,
        }
        projected.append(ClassifiedCandidate.model_validate_json(_json({
            **payload, "record_sha256": canonical_json_sha256(payload),
        })))
    by_id = {row.template_id: row for row in projected}
    families: Counter[str] = Counter(row.family for row in projected)
    files["classified_candidates.jsonl"] = b"".join(_json(row) for row in projected)
    files["family_assignment.json"] = _json(assignment)
    for kind in ("gold", "oracle"):
        name = f"{kind}_annotation_template.jsonl"
        worksheets = [json.loads(line) for line in files[name].splitlines()]
        for worksheet in worksheets:
            candidate = by_id[worksheet["template_id"]]
            worksheet.update({
                "schema_version": f"rag-value-{kind}-authoring-worksheet-v2",
                "candidate_record_sha256": candidate.record_sha256,
                "source_candidate_record_sha256": candidate.source_record_sha256,
                "source_scope_sha256": scope.manifest_sha256,
                "family_assignment_sha256": assignment.manifest_sha256,
            })
            if kind == "gold":
                worksheet["family"] = candidate.family
        files[name] = b"".join(_json(row) for row in worksheets)
    readiness = json.loads(files["readiness.json"])
    readiness.update({
        "schema_version": "rag-value-classified-authoring-readiness-v1",
        "family_assignment_sha256": assignment.manifest_sha256,
        "core_family_counts": dict(sorted(families.items())),
        "unresolved_family_ids": [],
        "family_assignment_decision_recorded": True,
        "effective_candidate_file": "classified_candidates.jsonl",
        "blocker_codes": sorted(set(readiness["blocker_codes"]) - {"family_assignment_unresolved"}),
    })
    readiness.pop("report_sha256")
    files["readiness.json"] = _json({
        **readiness, "report_sha256": canonical_json_sha256(readiness),
    })
    files["README.md"] = _render_readme(assignment, families).encode("utf-8")
    files["QUESTION_REVIEW.cn.md"] = _render_questions(ledger, by_id).encode("utf-8")
    files.pop("package_manifest.json")
    manifest = {
        "schema_version": "rag-value-classified-authoring-package-v1",
        "artifact_kind": "authoring_only",
        "experiment_namespace": "rag-value-ablation:core53:classified-v1",
        "ledger_sha256": ledger.manifest_sha256,
        "scope_amendment_sha256": scope.manifest_sha256,
        "family_assignment_sha256": assignment.manifest_sha256,
        "effective_candidate_file": "classified_candidates.jsonl",
        "executable": False,
        "benchmark_results_created": False,
        "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(files.items())},
    }
    files["package_manifest.json"] = _json({
        **manifest, "manifest_sha256": canonical_json_sha256(manifest),
    })
    return files


def write_classified_package(
    ledger: AuthoringReviewLedger, scope: Core53ScopeAmendment,
    assignment: HybridFamilyAssignment, directory: Path,
) -> None:
    """Write a new packet only; preserve both earlier packets and all human edits."""

    files = classified_package_files(ledger, scope, assignment)
    if directory.exists() or directory.is_symlink():
        raise AuthoringReviewError("classified output already exists; choose a new directory")
    try:
        target = directory.parent.resolve(strict=True) / directory.name
        target.mkdir(exist_ok=False)
        for name, raw in files.items():  # Completion manifest is last.
            with (target / name).open("xb") as handle:
                handle.write(raw)
    except OSError as exc:
        raise AuthoringReviewError("classified package write failed; nothing overwritten") from exc
    verify_classified_package(target)


def verify_classified_package(directory: Path) -> HybridFamilyAssignment:
    """Check exact sources and every derived byte, including effective family labels."""

    if directory.is_symlink():
        raise AuthoringReviewError("classified package cannot be a symlink")
    try:
        ledger = AuthoringReviewLedger.model_validate_json(_read_input(directory / "ledger.json"))
        scope = Core53ScopeAmendment.model_validate_json(
            _read_input(directory / "scope_amendment.json")
        )
        assignment = HybridFamilyAssignment.model_validate_json(
            _read_input(directory / "family_assignment.json")
        )
        expected = classified_package_files(ledger, scope, assignment)
        members = tuple(directory.iterdir())
        if {p.name for p in members} != set(expected):
            raise AuthoringReviewError("classified package has missing or extra files")
        for member in members:
            if member.is_symlink() or not member.is_file():
                raise AuthoringReviewError("classified package must contain regular files")
            if member.stat().st_size != len(expected[member.name]):
                raise AuthoringReviewError("classified file differs from its projection")
            if _read_input(member) != expected[member.name]:
                raise AuthoringReviewError("classified file differs from its projection")
    except (OSError, ValueError) as exc:
        raise AuthoringReviewError("classified package failed integrity validation") from exc
    return assignment


def _json(value: object) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def _render_readme(assignment: HybridFamilyAssignment, families: Counter[str]) -> str:
    return "\n".join([
        "# 53题工作包：分组已确认", "",
        "UNSUP-09现归为混合问题（两边对照）。53题范围与全部题意保持不变，无待分组题。", "",
        "| 分组 | 数量 |", "| --- | ---: |",
        *(f"| {label} | {families[family]} |" for family, label in _FAMILY_NAMES.items()), "",
        "[查看中文问题](QUESTION_REVIEW.cn.md)。题号UNSUP-09保留作追溯，前缀不决定评分分组。", "",
        "## 接下来需要的材料", "",
        "1. 具体分类群、Assembly、位点、病毒谱系，以及固定的数据与文献版本。",
        "2. [标准答案空表](gold_annotation_template.csv)和"
        "[人工证据空表](oracle_annotation_template.csv)。",
        "3. 本次模型与精确修订、提示词、费用和出站策略的明确授权。", "",
        "复制空表后填写，保留本包原样。旧CSV题意列中的提案备注保留作追溯，分组以本包为准。",
        "UNSUP-09需同时标注结构化关联与人工批准的来源证据；不借用旧拒答答案，",
        "不将缺少对应依据解释为整题必须拒答。正确映射、记录完整、不强行合并是评分方向，",
        "不是已填写的Gold，也不加入任何单个系统的专属提示词。", "",
        "## 版本与权限", "",
        "两份历史包、ledger、scope_amendment与core_candidates保持原字节；它们记录当时的分组状态。",
        "当前分组见classified_candidates.jsonl。每条新投影使用独立校验值，Gold／Oracle空表绑定该投影。",
        "原英文问题、53题成员、联网扩展和删除决定均不改变。", "",
        "题数、题意和评分分组已确认；科学批准仍为pending，Gold、Oracle和运行授权保持为空。",
        "真实执行接口和53题修订准入尚未接通；没有启动模型、联网或修改生产配置。",
        "不要通过修改状态字段绕过这些要求。", "",
        "[分组决定](family_assignment.json)与[就绪记录](readiness.json)供复核。",
        "校验值只证明内容一致，不证明专家身份；未编造身份、消息时间或签名。", "",
        f"分组决定 SHA-256：`{assignment.manifest_sha256}`。", "",
    ])


def _render_questions(
    ledger: AuthoringReviewLedger, candidates: dict[str, ClassifiedCandidate],
) -> str:
    lines = [
        "# 已确认的53题（分组定稿）", "",
        "题意和分组均已确认，不用重复审题。下一步补具体对象、标准答案和证据。",
        "UNSUP-09题号保留，但按混合问题评分；历史题号前缀不决定题型。", "",
    ]
    for family, label in _FAMILY_NAMES.items():
        rows = [row for row in ledger.records if row.template_id in candidates
                and candidates[row.template_id].family == family]
        lines.extend([f"## {label}（{len(rows)}题）", ""])
        for row in rows:
            wording = row.proposed_question_zh or row.original_question_zh
            if row.template_id == "UNSUP-09":
                wording = wording.split("\n题型建议：", 1)[0]
            safe = wording.replace("&", "&amp;").replace("<", "&lt;").replace(
                ">", "&gt;"
            ).replace("\n", "  \n  ")
            lines.extend([f"- `{row.template_id}`：{safe}", ""])
    lines.extend(["独立联网扩展与10个删除项不进入本轮；历史决定保留在ledger.json。", ""])
    return "\n".join(lines)
