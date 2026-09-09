"""Export accepted wording and empty annotation forms, never benchmark results."""

from __future__ import annotations

import csv
import hashlib
import io
from collections import Counter
from pathlib import Path

from eve_relation_rag.experiments.rag_value_ablation.authoring_review import (
    AuthoringReviewError,
    AuthoringReviewLedger,
    AuthoringReviewRecord,
    validate_authoring_ledger,
)
from eve_relation_rag.experiments.rag_value_ablation.scientific_questions import (
    scientific_entity_bindings_template_bytes,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256

_FAMILY_NAMES = {
    "structured": "查数据库",
    "literature": "查文献",
    "hybrid": "两边对照",
    "unsupported": "拒答或执行边界测试",
    "unresolved": "关联查询，分组待确定",
}
_BLOCKERS = (
    "approved_entity_bindings_missing",
    "approved_gold_missing",
    "approved_oracle_missing",
    "exact_dataset_and_corpus_run_bindings_missing",
    "exact_generation_authorization_missing",
    "family_assignment_unresolved",
    "phase3_runtime_execution_gate_not_implemented",
    "scientific_association_projection_runtime_missing",
    "trusted_question_quota_not_met",
)


def authoring_package_files(ledger: AuthoringReviewLedger) -> dict[str, bytes]:
    """Build deterministic files without reading settings, evidence, or a provider."""

    ledger = validate_authoring_ledger(ledger)
    core = tuple(record for record in ledger.records if record.disposition == "core")
    extension = tuple(
        record for record in ledger.records if record.disposition == "external_extension"
    )
    excluded = tuple(record for record in ledger.records if record.disposition == "excluded")
    families: Counter[str] = Counter(record.family or "unresolved" for record in core)
    readiness: dict[str, object] = {
        "schema_version": "rag-value-authoring-readiness-v1",
        "artifact_kind": "authoring_only",
        "ledger_sha256": ledger.manifest_sha256,
        "original_record_count": ledger.record_count,
        "core_candidate_count": len(core),
        "external_extension_count": len(extension),
        "excluded_count": len(excluded),
        "core_family_counts": dict(sorted(families.items())),
        "unresolved_family_ids": [r.template_id for r in core if r.family is None],
        "wording_confirmed_count": len(core) + len(extension),
        "trusted_question_count": 0,
        "gold_annotation_count": 0,
        "oracle_annotation_count": 0,
        "trusted_execution_ready": False,
        "production_dependencies_constructed": False,
        "preregistered_quota": {
            "total_min": 60,
            "total_max": 80,
            "per_family_min": 15,
            "per_family_max": 20,
        },
        "quota_changed": False,
        "blocker_codes": list(_BLOCKERS),
        "relation_class_definitions_required_for_current_association_questions": False,
        "relation_class_definition_approval": None,
        "observation_scope": (
            "This authoring package has no human facts or runtime grants; it is not an "
            "inventory of all local models, databases, or corpus artifacts."
        ),
    }
    readiness["report_sha256"] = canonical_json_sha256(readiness)
    files = {
        "ledger.json": _json(ledger),
        "core_candidates.jsonl": _jsonl(core),
        "external_extension_proposals.jsonl": _jsonl(extension),
        "excluded_decisions.jsonl": _jsonl(excluded),
        "gold_annotation_template.jsonl": _jsonl(tuple(_blank_gold(r) for r in core)),
        "oracle_annotation_template.jsonl": _jsonl(tuple(_blank_oracle(r) for r in core)),
        "gold_annotation_template.csv": _annotation_csv(core, oracle=False),
        "oracle_annotation_template.csv": _annotation_csv(core, oracle=True),
        "entity_bindings_template.json": scientific_entity_bindings_template_bytes(),
        "run_authorization_template.json": _json(_blank_authorization(ledger)),
        "readiness.json": _json(readiness),
        "QUESTION_REVIEW.cn.md": render_reviewed_questions(ledger).encode("utf-8"),
        "README.md": _render_readme(ledger, families).encode("utf-8"),
    }
    manifest = {
        "schema_version": "rag-value-authoring-package-v1",
        "artifact_kind": "authoring_only",
        "ledger_sha256": ledger.manifest_sha256,
        "executable": False,
        "benchmark_results_created": False,
        "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(files.items())},
    }
    files["package_manifest.json"] = _json(
        {**manifest, "manifest_sha256": canonical_json_sha256(manifest)}
    )
    return files


def write_authoring_package(ledger: AuthoringReviewLedger, output_directory: Path) -> None:
    """Create a new directory once; never overwrite an earlier annotation package."""

    files = authoring_package_files(ledger)
    if output_directory.exists() or output_directory.is_symlink():
        raise AuthoringReviewError("authoring output already exists; choose a new directory")
    try:
        parent = output_directory.parent.resolve(strict=True)
    except OSError as exc:
        raise AuthoringReviewError("authoring output parent does not exist") from exc
    target = parent / output_directory.name
    try:
        target.mkdir(exist_ok=False)
        # The package manifest is deliberately written last. A partial directory has
        # no valid completion manifest and verify_authoring_package rejects it.
        for name, raw in files.items():
            with (target / name).open("xb") as handle:
                handle.write(raw)
    except OSError as exc:
        raise AuthoringReviewError(
            "could not write authoring package; existing data was not overwritten"
        ) from exc
    verify_authoring_package(target)


def verify_authoring_package(directory: Path) -> AuthoringReviewLedger:
    """Recompute every derived file; changed user annotations require a new packet."""

    if directory.is_symlink():
        raise AuthoringReviewError("authoring package cannot be a symlink")
    try:
        ledger_path = directory / "ledger.json"
        if ledger_path.is_symlink():
            raise AuthoringReviewError("authoring ledger cannot be a symlink")
        limit = 4 * 1024 * 1024
        if not ledger_path.is_file() or ledger_path.stat().st_size > limit:
            raise AuthoringReviewError("authoring ledger exceeds the size limit")
        with ledger_path.open("rb") as handle:
            raw = handle.read(limit + 1)
        if len(raw) > limit:
            raise AuthoringReviewError("authoring ledger exceeds the size limit")
        ledger = AuthoringReviewLedger.model_validate_json(raw)
        expected = authoring_package_files(ledger)
        members = tuple(directory.iterdir())
        if {p.name for p in members} != set(expected):
            raise AuthoringReviewError("authoring package has missing or extra files")
        for member in members:
            if member.is_symlink() or not member.is_file():
                raise AuthoringReviewError("authoring package must contain regular files")
            if member.stat().st_size != len(expected[member.name]):
                raise AuthoringReviewError("authoring file differs from its ledger projection")
            if member.read_bytes() != expected[member.name]:
                raise AuthoringReviewError("authoring file differs from its ledger projection")
    except (OSError, ValueError) as exc:
        raise AuthoringReviewError("authoring package failed integrity validation") from exc
    return ledger


def render_reviewed_questions(ledger: AuthoringReviewLedger) -> str:
    """Show accepted Chinese wording without asking for the same approval again."""

    ledger = validate_authoring_ledger(ledger)
    lines = [
        "# 已确认的候选问题",
        "",
        "题意已确认，不用再审一遍。下一步只补实体、标准答案和证据。",
        "回答规则用于人工标注和评分，不自动加入某个系统的专属提示词。",
        "核心候选与联网扩展分开；原题、旧意见和删除记录完整保留在 ledger.json。",
        "",
    ]
    for family, name in _FAMILY_NAMES.items():
        records = tuple(
            r for r in ledger.records
            if r.disposition == "core" and (r.family or "unresolved") == family
        )
        lines.extend([f"## {name}（{len(records)}题）", ""])
        for record in records:
            wording = record.proposed_question_zh or record.original_question_zh
            lines.extend([f"- `{record.template_id}`：{_md(wording)}", ""])
    lines.extend(["## 独立扩展（不进入核心 S0–S6）", ""])
    for record in ledger.records:
        if record.disposition == "external_extension":
            wording = record.proposed_question_zh or record.original_question_zh
            lines.extend([f"- `{record.template_id}`：{_md(wording)}", ""])
    excluded = ", ".join(r.template_id for r in ledger.records if r.disposition == "excluded")
    lines.extend(["## 沿用删除决定", "", excluded, ""])
    return "\n".join(lines)


def _blank_gold(record: AuthoringReviewRecord) -> dict[str, object]:
    return {
        "schema_version": "rag-value-gold-authoring-worksheet-v1",
        "artifact_kind": "authoring_only",
        "template_id": record.template_id,
        "candidate_record_sha256": record.record_sha256,
        "question_text_template": record.question_text_template,
        "template_text_sha256": hashlib.sha256(record.question_text_template.encode()).hexdigest(),
        "family": record.family,
        "entity_slots": record.entity_slots,
        "bound_question_text": None,
        "bound_question_text_sha256": None,
        "dataset_release_key": None,
        "dataset_manifest_sha256": None,
        "corpus_release_key": None,
        "corpus_manifest_sha256": None,
        "review_status": "pending",
        "gold": None,
        "approval": None,
        "executable": False,
    }


def _blank_oracle(record: AuthoringReviewRecord) -> dict[str, object]:
    # This is not OracleEvidenceEntry: the final question is not bound yet.
    return {
        "schema_version": "rag-value-oracle-authoring-worksheet-v1",
        "artifact_kind": "authoring_only",
        "template_id": record.template_id,
        "candidate_record_sha256": record.record_sha256,
        "bound_question_text_sha256": None,
        "dataset_release_key": None,
        "dataset_manifest_sha256": None,
        "corpus_release_key": None,
        "corpus_manifest_sha256": None,
        "review_status": "pending",
        "evidence_disposition": None,
        "structured_facts": None,
        "literature_chunk_keys": None,
        "source_attestation": None,
        "approval": None,
        "executable": False,
    }


def _blank_authorization(ledger: AuthoringReviewLedger) -> dict[str, object]:
    return {
        "schema_version": "rag-value-run-authorization-worksheet-v1",
        "artifact_kind": "authoring_only",
        "ledger_sha256": ledger.manifest_sha256,
        "review_status": "pending",
        "provider": None,
        "model_id": None,
        "exact_revision": None,
        "model_artifact_manifest_sha256": None,
        "tokenizer_identity": None,
        "prompt_policy_sha256": None,
        "output_schema_sha256": None,
        "temperature_required": 0,
        "max_output_tokens": None,
        "context_limit_tokens": None,
        "credential_reference": None,
        "credentials_must_not_be_written_here": True,
        "egress_policy": None,
        "maximum_cost": None,
        "currency": None,
        "dataset_release_binding": None,
        "corpus_release_binding": None,
        "approved_question_manifest_sha256": None,
        "approved_oracle_manifest_sha256": None,
        "preregistered_sampling_policy": None,
        "reviewer_key": None,
        "reviewed_at_utc": None,
        "executable": False,
    }


def _annotation_csv(records: tuple[AuthoringReviewRecord, ...], *, oracle: bool) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    if oracle:
        labels = ("人工选定的结构化事实", "人工选定的文献及段落", "证据处置说明")
    else:
        labels = ("标准答案与精确记录", "支持文献及具体段落", "必要限制和禁止结论")
    writer.writerow(("编号", "已确认题意（不需重审）", *labels, "审核人键", "审核时间 UTC"))
    for record in records:
        wording = record.proposed_question_zh or record.original_question_zh
        # JSON retains exact source text; CSV is safe to open in a spreadsheet.
        safe_wording = (
            "'" + wording if wording.lstrip().startswith(("=", "+", "-", "@")) else wording
        )
        writer.writerow((record.template_id, safe_wording, "", "", "", "", ""))
    return buffer.getvalue().encode("utf-8-sig")


def _render_readme(ledger: AuthoringReviewLedger, families: Counter[str]) -> str:
    lines = [
        "# RAG-value 人工标注工作包",
        "",
        "这是题意确认与空白标注材料，不是 benchmark 成绩，也不授予执行权限。",
        "",
        f"原始记录 {ledger.record_count} 条；核心候选 {ledger.core_count} 条；",
        f"独立扩展 {ledger.extension_count} 条；排除 {ledger.excluded_count} 条。",
        "可信问题、真实 Gold、Oracle 标注均为 0。",
        "",
        "| 当前分组 | 数量 |",
        "| --- | ---: |",
        *(f"| {name} | {families[family]} |" for family, name in _FAMILY_NAMES.items()),
        "",
        "## 已完成",
        "",
        "- 原64题与旧决定逐条对应，10项修改使用已保存的同意记录。",
        "- 新英文、中文题意、回答规则和范围一起校验，旧题不重新审批。",
        "- 删除项保留审计记录；联网提案不进入核心候选或 Gold / Oracle 空表。",
        "- 原始预注册64题、生产配置、发布数据和 embeddings 保持不变。",
        "",
        "## 接下来填写什么",
        "",
        "1. 绑定题目中的分类群、Assembly、位点、病毒谱系，以及精确数据/文献版本。",
        "2. 在 Gold 空表填写标准事实、记录集和证据，另在 Oracle 空表人工选定证据。",
        "3. 确定 UNSUP-09 的分组及题目配额；原60–80题、每类15–20题门槛尚未满足。",
        "4. 明确批准本次 provider、模型修订、提示词、费用和出站策略后，才能接通真实执行。",
        "",
        "Transferred gene / Integrated virus 的定义不阻塞现有关联题。",
        "如果加入分类筛选，先由人定义，再逐对象标注证据；不能自动映射来源标签。",
        "",
        "## 文件",
        "",
        "- [已确认问题（中文）](QUESTION_REVIEW.cn.md)：不用重复审题。",
        "- [Gold 空表](gold_annotation_template.csv)和"
        "[Oracle 空表](oracle_annotation_template.csv)：",
        "  中文填写辅助表；JSONL保留候选校验值，实体绑定后还须生成正式契约并独立批准。",
        "- [实体空表](entity_bindings_template.json)：复用原有10个 pending 槽位。",
        "- [运行授权空表](run_authorization_template.json)：不填密码或密钥。",
        "- [就绪状态](readiness.json)：仅描述本工作包没有的证据和权限，不代表本机无模型文件。",
        "",
        "复制空表后填写，保留本目录原样以便复核。校验和只保证完整性，不是人类签名。",
        "本目录没有导入真实执行器的入口，不能用改动 pending 字段的方式绕过批准。",
        "",
        f"审阅记录 SHA-256：`{ledger.manifest_sha256}`。",
        "",
    ]
    return "\n".join(lines)


def _json(value: object) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def _jsonl(values: tuple[object, ...]) -> bytes:
    return b"".join(_json(value) for value in values)


def _md(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(
        ">", "&gt;"
    ).replace("\n", "  \n  ")
