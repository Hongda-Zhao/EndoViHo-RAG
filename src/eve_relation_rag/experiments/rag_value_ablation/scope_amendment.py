"""Record the user's exact core-53 scope decision, without issuing runtime authority."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from eve_relation_rag.experiments.rag_value_ablation.authoring_package import (
    authoring_package_files,
)
from eve_relation_rag.experiments.rag_value_ablation.authoring_review import (
    AuthoringReviewError,
    AuthoringReviewLedger,
    validate_authoring_ledger,
)
from eve_relation_rag.literature.contracts import Sha256, StableToken, StrictFrozenSchema
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256

# This decision applies only to the saved review that the user saw, not any 53 rows.
CORE53_LEDGER_SHA256 = "2f24551b6a9e766d3af1ffb2c6633fa6be00f20dad53520c2828087d62ffde18"
CORE53_DECISION_TEXT = "按这 53 题继续"


class ScopedCandidate(StrictFrozenSchema):
    template_id: StableToken
    record_sha256: Sha256


class Core53ScopeAmendment(StrictFrozenSchema):
    """A narrow conversation decision, not HumanApproval or an experiment manifest."""

    schema_version: Literal["rag-value-core53-scope-amendment-v1"]
    artifact_kind: Literal["authoring_scope_only"]
    experiment_namespace: Literal["rag-value-ablation:core53:v1"]
    source_ledger_sha256: Literal[
        "2f24551b6a9e766d3af1ffb2c6633fa6be00f20dad53520c2828087d62ffde18"
    ]
    source_core_file_sha256: Sha256
    source_package_manifest_sha256: Sha256
    decision_text: Literal["按这 53 题继续"]
    decision_source: Literal["user_message_in_current_thread"]
    decision_scope: Literal["exact_core_membership_and_authoring_quota_only"]
    fixed_candidate_count: Literal[53]
    per_family_quota: Literal["observed_distribution_no_balancing_or_filler"]
    candidates: tuple[ScopedCandidate, ...] = Field(min_length=53, max_length=53)
    unresolved_family_ids: tuple[Literal["UNSUP-09"]]  # No inferred scoring family.
    source_message_timestamp: None
    reviewer_identity: None
    scientific_approval: None
    runtime_authorization: None
    executable: Literal[False]
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        ids = tuple(row.template_id for row in self.candidates)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("scope candidates must be sorted and unique")
        if self.unresolved_family_ids != ("UNSUP-09",):
            raise ValueError("scope decision cannot assign a scoring family")
        payload = self.model_dump(mode="json", exclude={"manifest_sha256"})
        if self.manifest_sha256 != canonical_json_sha256(payload):
            raise ValueError("scope amendment checksum does not match")
        return self


def build_core53_scope_amendment(
    ledger: AuthoringReviewLedger, *, decision_text: str,
) -> Core53ScopeAmendment:
    """Record an explicit decision for the previously shown, immutable candidate set."""

    ledger = validate_authoring_ledger(ledger)
    if ledger.manifest_sha256 != CORE53_LEDGER_SHA256 or decision_text != CORE53_DECISION_TEXT:
        raise AuthoringReviewError("scope decision must match the exact reviewed core-53 set")
    files = authoring_package_files(ledger)
    payload = {
        "schema_version": "rag-value-core53-scope-amendment-v1",
        "artifact_kind": "authoring_scope_only",
        "experiment_namespace": "rag-value-ablation:core53:v1",
        "source_ledger_sha256": ledger.manifest_sha256,
        "source_core_file_sha256": hashlib.sha256(files["core_candidates.jsonl"]).hexdigest(),
        "source_package_manifest_sha256": hashlib.sha256(
            files["package_manifest.json"]
        ).hexdigest(),
        "decision_text": decision_text,
        "decision_source": "user_message_in_current_thread",
        "decision_scope": "exact_core_membership_and_authoring_quota_only",
        "fixed_candidate_count": 53,
        "per_family_quota": "observed_distribution_no_balancing_or_filler",
        "candidates": [
            {"template_id": row.template_id, "record_sha256": row.record_sha256}
            for row in sorted(ledger.records, key=lambda row: row.template_id)
            if row.disposition == "core"
        ],
        "unresolved_family_ids": ["UNSUP-09"],
        "source_message_timestamp": None,
        "reviewer_identity": None,
        "scientific_approval": None,
        "runtime_authorization": None,
        "executable": False,
    }
    return Core53ScopeAmendment.model_validate_json(
        canonical_json_bytes({**payload, "manifest_sha256": canonical_json_sha256(payload)})
    )


def core53_package_files(
    ledger: AuthoringReviewLedger, amendment: Core53ScopeAmendment,
) -> dict[str, bytes]:
    """Revalidate bindings and reuse all question/blank evidence bytes unchanged."""

    ledger = validate_authoring_ledger(ledger)
    if type(amendment) is not Core53ScopeAmendment:
        raise AuthoringReviewError("scope package requires an exact amendment type")
    try:
        amendment = Core53ScopeAmendment.model_validate_json(canonical_json_bytes(amendment))
    except (ValueError, TypeError) as exc:
        raise AuthoringReviewError("scope amendment failed revalidation") from exc
    expected = build_core53_scope_amendment(ledger, decision_text=amendment.decision_text)
    if amendment != expected:
        raise AuthoringReviewError("scope amendment differs from the reviewed membership")
    files = authoring_package_files(ledger)
    readiness = json.loads(files["readiness.json"])
    readiness.update({
        "schema_version": "rag-value-core53-authoring-readiness-v1",
        "scope_amendment_sha256": amendment.manifest_sha256,
        "quota_changed": True,
        "effective_authoring_quota": {
            "exact_total": amendment.fixed_candidate_count,
            "per_family": amendment.per_family_quota,
            "membership": "exact_amendment_candidate_ids_and_record_checksums",
        },
        "scope_decision_recorded": True,
        "scope_runtime_admission_implemented": False,
        "blocker_codes": sorted(
            (set(readiness["blocker_codes"]) - {"trusted_question_quota_not_met"})
            | {"scope_amendment_runtime_admission_not_implemented"}
        ),
    })
    readiness.pop("report_sha256")
    files["readiness.json"] = _json({
        **readiness, "report_sha256": canonical_json_sha256(readiness),
    })
    files["scope_amendment.json"] = _json(amendment)
    files["README.md"] = _render_readme(amendment, readiness).encode("utf-8")
    files.pop("package_manifest.json")
    manifest = {
        "schema_version": "rag-value-core53-authoring-package-v1",
        "artifact_kind": "authoring_only",
        "ledger_sha256": ledger.manifest_sha256,
        "scope_amendment_sha256": amendment.manifest_sha256,
        "executable": False,
        "benchmark_results_created": False,
        "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(files.items())},
    }
    files["package_manifest.json"] = _json({
        **manifest, "manifest_sha256": canonical_json_sha256(manifest),
    })
    return files


def write_core53_package(
    ledger: AuthoringReviewLedger, amendment: Core53ScopeAmendment, directory: Path,
) -> None:
    """Write only a new directory, preserving historical and human-edited packets."""

    files = core53_package_files(ledger, amendment)
    if directory.exists() or directory.is_symlink():
        raise AuthoringReviewError("scope output already exists; choose a new directory")
    try:
        target = directory.parent.resolve(strict=True) / directory.name
        target.mkdir(exist_ok=False)
        # A partially written package lacks a valid final completion manifest.
        for name, raw in files.items():
            with (target / name).open("xb") as handle:
                handle.write(raw)
    except OSError as exc:
        raise AuthoringReviewError(
            "could not write scope package; nothing was overwritten"
        ) from exc
    verify_core53_package(target)


def verify_core53_package(directory: Path) -> Core53ScopeAmendment:
    """Verify the complete projection, not just self-declared hashes or approval fields."""

    if directory.is_symlink():
        raise AuthoringReviewError("scope package cannot be a symlink")
    try:
        ledger = AuthoringReviewLedger.model_validate_json(_read_input(directory / "ledger.json"))
        amendment = Core53ScopeAmendment.model_validate_json(
            _read_input(directory / "scope_amendment.json")
        )
        expected = core53_package_files(ledger, amendment)
        members = tuple(directory.iterdir())
        if {p.name for p in members} != set(expected):
            raise AuthoringReviewError("scope package has missing or extra files")
        for member in members:
            if member.is_symlink() or not member.is_file():
                raise AuthoringReviewError("scope package must contain regular files")
            if member.stat().st_size != len(expected[member.name]):
                raise AuthoringReviewError("scope file differs from its validated projection")
            if member.read_bytes() != expected[member.name]:
                raise AuthoringReviewError("scope file differs from its validated projection")
    except (OSError, ValueError) as exc:
        raise AuthoringReviewError("scope package failed integrity validation") from exc
    return amendment


def _read_input(path: Path) -> bytes:
    limit = 4 * 1024 * 1024
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise AuthoringReviewError("scope input must be a bounded regular file")
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise AuthoringReviewError("scope input exceeds the size limit")
    return raw


def _json(value: object) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def _render_readme(amendment: Core53ScopeAmendment, readiness: dict[str, object]) -> str:
    families = readiness["core_family_counts"]
    assert isinstance(families, dict)
    labels = {
        "structured": "查数据库", "literature": "查文献", "hybrid": "两边对照",
        "unsupported": "拒答或执行边界测试", "unresolved": "评分分组待确定",
    }
    return "\n".join([
        "# 本轮按53题继续", "",
        f"用户已明确决定：{amendment.decision_text}。本轮固定这53个核心候选，",
        "不补题凑数，不再要求每类15–20题。原方案和上一版工作包保留为历史记录。", "",
        "| 当前分组 | 数量 |", "| --- | ---: |",
        *(f"| {label} | {families[key]} |" for key, label in labels.items()), "",
        "题目内容、旧审批和回答规则均未改写。1个联网扩展与10个删除项仍不进入本轮。",
        "UNSUP-09仍在53题内，只是评分分组尚待确定，不自动改回拒答题。", "",
        "## 接下来只补答案和证据", "",
        "1. 绑定题目中的具体分类群、Assembly、位点与病毒谱系，固定数据和文献版本。",
        "2. 填写[标准答案空表](gold_annotation_template.csv)与"
        "[人工证据空表](oracle_annotation_template.csv)。已同意的题意不用重新审批。",
        "3. 批准本次模型、精确修订、提示词、费用和出站策略；真实执行接口仍需实现与验证。",
        "", "[中文问题清单](QUESTION_REVIEW.cn.md)供查阅。复制空表后填写，保留此包原样。", "",
        "## 批准范围", "",
        "这次只批准题数、成员范围和取消均衡配额，不是标准答案、Oracle、分类定义或运行授权。",
        "全部候选仍为pending；Gold和Oracle均为空；没有启动模型、联网或生产数据写入。",
        "可信运行准入尚未接通本修订，旧通用gate仍保留历史配额。不能靠改数字绕过它。",
        "未来正式运行必须绑定本修订、具体实体以及53题各自获批的答案与证据。",
        "不同分组数量不均衡；结果须按组报告并披露样本数，不能声称是原均衡方案的结果。", "",
        "[机器可读修订](scope_amendment.json)与[就绪记录](readiness.json)可供复核。",
        "校验和证明内容一致性，不证明审阅人身份；没有编造消息时间、专家签名或Gold。", "",
        f"范围修订 SHA-256：`{amendment.manifest_sha256}`。", "",
    ])
