"""Read checksum-pinned human wording decisions without approving benchmark evidence.

This version imports the original 64-row workbook and its ten-row incremental review.
Checksums establish byte integrity, not reviewer identity or a human signature. No
model, database, production setting, Gold, or trusted admission gate is involved.
"""

from __future__ import annotations

import hashlib
import io
import posixpath
import re
from collections import Counter
from dataclasses import dataclass
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Literal, Self
from xml.etree.ElementTree import Element
from zipfile import BadZipFile, ZipFile

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException
from pydantic import Field, model_validator

from eve_relation_rag.experiments.rag_value_ablation.scientific_questions import (
    ScientificQuestionFamily,
    ScientificQuestionTemplate,
    build_scientific_question_templates,
    scientific_questions_template_bytes,
)
from eve_relation_rag.literature.contracts import (
    QuestionText,
    Sha256,
    StableToken,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256

_INCREMENTAL_IDS = (
    "HOST-H-02",
    "VIRUS-H-02",
    "REL-H-02",
    "UNSUP-01",
    "UNSUP-02",
    "UNSUP-06",
    "UNSUP-09",
    "UNSUP-14",
    "UNSUP-15",
    "UNSUP-16",
)
_MODIFIED_IDS = frozenset({"HOST-H-02", "VIRUS-H-02", "REL-H-02", "UNSUP-14"})
_EXCLUDED_IDS = frozenset(
    {
        "HOST-H-03",
        "HOST-H-04",
        "VIRUS-H-03",
        "VIRUS-H-04",
        "REL-H-03",
        "REL-H-04",
        "RECORD-H-03",
        "RECORD-H-04",
        "UNSUP-07",
        "UNSUP-08",
    }
)
_QUESTION_CHANGED_IDS = _MODIFIED_IDS | {"UNSUP-09"}
_FAMILY_LABELS = {
    "structured": "查数据库",
    "literature": "查文献",
    "hybrid": "两边对照",
    "unsupported": "拒答测试",
}
_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_SHA_RE = re.compile(r"[0-9a-f]{64}")
_CELL_RE = re.compile(r"[A-Z]{1,3}[1-9][0-9]{0,6}")
_SLOT_RE = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")
_MAX_RAW_BYTES = 4 * 1024 * 1024
_MAX_MEMBER_BYTES = 4 * 1024 * 1024
_MAX_TOTAL_BYTES = 20 * 1024 * 1024
_MAX_TEXT = 20_000


class AuthoringReviewError(ValueError):
    """A review source is incomplete, stale, unsafe, or not explicitly accepted."""


class AuthoringReviewRecord(StrictFrozenSchema):
    """Accepted wording or a deletion; never an EvaluationQuestion or approval."""

    template_id: StableToken
    source_record_sha256: Sha256
    source_family: ScientificQuestionFamily
    family: ScientificQuestionFamily | None
    disposition: Literal["core", "external_extension", "excluded"]
    original_question_text: QuestionText
    original_question_zh: str = Field(min_length=1, max_length=_MAX_TEXT)
    original_decision: Literal["保留", "修改", "删除"]
    original_notes: str = Field(max_length=_MAX_TEXT)
    incremental_decision: Literal["同意"] | None
    incremental_notes: str | None = Field(max_length=_MAX_TEXT)
    proposed_question_zh: str | None = Field(max_length=_MAX_TEXT)
    change_scope: str = Field(min_length=1, max_length=_MAX_TEXT)
    question_text_template: QuestionText
    response_policy: str | None = Field(max_length=_MAX_TEXT)
    proposal_sha256: Sha256 | None
    entity_slots: tuple[StableToken, ...]
    benchmark_review_status: Literal["pending"] = "pending"
    wording_status: Literal["human_accepted", "excluded"]
    executable: Literal[False] = False
    gold: Literal[None] = None
    oracle: Literal[None] = None
    approval: Literal[None] = None
    record_sha256: Sha256

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        sources = _source_templates()
        source = sources.get(self.template_id)
        if source is None or (
            self.source_record_sha256 != source.record_sha256
            or self.source_family != source.family
            or self.original_question_text != source.question_text_template
        ):
            raise ValueError("authoring record does not match its preregistered source")
        expected_family = None if self.template_id == "UNSUP-09" else source.family
        if self.family != expected_family:
            raise ValueError("authoring family cannot silently change or resolve UNSUP-09")
        expected_decision = (
            "删除"
            if self.template_id in _EXCLUDED_IDS
            else "修改"
            if self.template_id in _MODIFIED_IDS
            else "保留"
        )
        expected_disposition = (
            "excluded"
            if self.template_id in _EXCLUDED_IDS
            else "external_extension"
            if self.template_id == "UNSUP-14"
            else "core"
        )
        if self.original_decision != expected_decision or self.disposition != expected_disposition:
            raise ValueError("authoring decision cannot revive a deletion or move execution scope")
        if self.wording_status != (
            "excluded" if self.disposition == "excluded" else "human_accepted"
        ):
            raise ValueError("wording status does not match the decision")
        if self.template_id in _INCREMENTAL_IDS:
            if (
                self.incremental_decision != "同意"
                or self.incremental_notes != ""
                or not self.proposed_question_zh
                or not self.response_policy
            ):
                raise ValueError("incremental proposal needs an unconditional saved agreement")
            if self.proposal_sha256 != canonical_json_sha256(_proposal_payload(self)):
                raise ValueError("proposal checksum does not match the reviewed wording and policy")
            required_scope = (
                "独立扩展提案，不启用联网"
                if self.disposition == "external_extension"
                else "核心离线 benchmark"
            )
            if required_scope not in self.change_scope:
                raise ValueError("reviewed proposal does not preserve its explicit execution scope")
            if self.template_id not in _QUESTION_CHANGED_IDS and (
                self.question_text_template != source.question_text_template
            ):
                raise ValueError("a response-policy review cannot silently replace the question")
        elif (
            self.incremental_decision is not None
            or self.incremental_notes is not None
            or self.proposed_question_zh is not None
            or self.response_policy is not None
            or self.proposal_sha256 is not None
            or self.question_text_template != source.question_text_template
        ):
            raise ValueError("unchanged or deleted questions cannot inherit an unreviewed revision")
        slots = tuple(sorted(set(_SLOT_RE.findall(self.question_text_template))))
        residual = _SLOT_RE.sub("", self.question_text_template)
        if (
            self.entity_slots != slots
            or "{" in residual
            or "}" in residual
            or not set(slots).issubset(source.entity_slots)
        ):
            raise ValueError(
                "authoring entity slots are malformed or not inherited from the source"
            )
        if self.record_sha256 != _self_sha256(self, "record_sha256"):
            raise ValueError("authoring record checksum does not match")
        return self


class AuthoringReviewLedger(StrictFrozenSchema):
    """Integrity-bound source decisions, with no evidence or execution authority."""

    schema_version: Literal["rag-value-authoring-review-ledger-v1"] = (
        "rag-value-authoring-review-ledger-v1"
    )
    original_workbook_sha256: Sha256
    incremental_workbook_sha256: Sha256
    source_templates_sha256: Sha256
    records: tuple[AuthoringReviewRecord, ...] = Field(min_length=64, max_length=64)
    record_count: Literal[64] = 64
    core_count: Literal[53] = 53
    extension_count: Literal[1] = 1
    excluded_count: Literal[10] = 10
    checksum_notice: Literal[
        "Checksums verify integrity, not human identity, signatures, Gold, or execution authority."
    ] = "Checksums verify integrity, not human identity, signatures, Gold, or execution authority."
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        for record in self.records:
            if type(record) is not AuthoringReviewRecord:
                raise ValueError("ledger requires exact authoring records")
            AuthoringReviewRecord.model_validate_json(canonical_json_bytes(record))
        expected_ids = tuple(item.template_id for item in build_scientific_question_templates())
        if tuple(record.template_id for record in self.records) != expected_ids:
            raise ValueError(
                "ledger must cover each original template exactly once in source order"
            )
        if self.source_templates_sha256 != _source_templates_sha256():
            raise ValueError("ledger source templates checksum is stale")
        counts = Counter(record.disposition for record in self.records)
        if counts != {
            "core": self.core_count,
            "external_extension": self.extension_count,
            "excluded": self.excluded_count,
        }:
            raise ValueError("ledger disposition counts do not match")
        questions = [
            " ".join(record.question_text_template.split()).casefold()
            for record in self.records
            if record.disposition != "excluded"
        ]
        if len(questions) != len(set(questions)):
            raise ValueError("active authoring questions contain duplicate wording")
        if self.manifest_sha256 != _self_sha256(self, "manifest_sha256"):
            raise ValueError("authoring ledger checksum does not match")
        return self


def validate_authoring_ledger(ledger: AuthoringReviewLedger) -> AuthoringReviewLedger:
    """Revalidate serialized fields; reject subclassed/copied/constructed bypasses."""

    if type(ledger) is not AuthoringReviewLedger:
        raise AuthoringReviewError("authoring export requires an exact AuthoringReviewLedger")
    try:
        return AuthoringReviewLedger.model_validate_json(canonical_json_bytes(ledger))
    except (ValueError, TypeError) as exc:
        raise AuthoringReviewError(
            "authoring ledger failed complete checksum revalidation"
        ) from exc


def load_reviewed_workbooks(
    original_workbook: Path,
    incremental_workbook: Path,
    *,
    expected_original_sha256: str,
    expected_incremental_sha256: str,
) -> AuthoringReviewLedger:
    """Import only saved decisions from two explicit externally checksum-pinned files.

    Unreviewed decisions and agreement with additional comments fail closed. New
    comments require a new explicit proposal review; they are never interpreted as
    permission. The original 64-template source remains unchanged.
    """

    original = _read_workbook(original_workbook, expected_original_sha256)
    incremental = _read_workbook(incremental_workbook, expected_incremental_sha256)
    _header(
        original,
        "简版审批",
        8,
        (
            "编号",
            "题目类型",
            "一句话看懂问题",
            "你的决定",
            "修改或删除意见（可用中文）",
        ),
    )
    _header(
        original,
        "原文与说明",
        21,
        (
            "编号",
            "完整英文原题",
            "原始状态",
            "源记录 SHA-256",
        ),
    )
    _header(
        incremental,
        "旧审阅已对照",
        5,
        (
            "编号",
            "原题目类型",
            "原中文题意",
            "原决定",
            "原意见",
        ),
    )
    _header(
        incremental,
        "英文与版本对照",
        5,
        (
            "编号",
            "原英文问题（保留）",
            "新版英文问题或规则草案",
            "本次变化与范围",
            "原记录校验值",
            "原题库状态",
        ),
    )
    _header(
        incremental,
        "本轮只审新改动",
        8,
        (
            "编号",
            "你上次的决定和意见",
            "这次只需审阅的内容",
            "你的新决定",
            "补充意见（可用中文）",
        ),
    )
    if (
        _value(incremental, "英文与版本对照", "B73") != expected_original_sha256
        or _value(incremental, "英文与版本对照", "B75") != _source_templates_sha256()
    ):
        raise AuthoringReviewError("incremental workbook references stale original/source bytes")
    decisions = {row[0]: row for row in _table(incremental, "本轮只审新改动", 9, 18, 5)}
    if tuple(decisions) != _INCREMENTAL_IDS:
        raise AuthoringReviewError("incremental review must cover the exact ten proposal IDs once")
    original_rows = _table(original, "简版审批", 9, 72, 5)
    original_english = _table(original, "原文与说明", 22, 85, 4)
    copied_rows = _table(incremental, "旧审阅已对照", 6, 69, 5)
    english_rows = _table(incremental, "英文与版本对照", 6, 69, 6)
    records: list[AuthoringReviewRecord] = []
    for source, old, old_english, copied, english in zip(
        build_scientific_question_templates(),
        original_rows,
        original_english,
        copied_rows,
        english_rows,
        strict=True,
    ):
        template_id = source.template_id
        if (
            old[0] != template_id
            or old[1] != _FAMILY_LABELS[source.family]
            or copied != old
            or old_english
            != (
                template_id,
                source.question_text_template,
                "pending",
                source.record_sha256,
            )
            or (english[0], english[1], english[4], english[5])
            != (
                template_id,
                source.question_text_template,
                source.record_sha256,
                "pending",
            )
        ):
            raise AuthoringReviewError(
                "original identity, wording, decision, or notes do not match"
            )
        decision = decisions.get(template_id)
        question, policy = source.question_text_template, None
        if decision is not None:
            if decision[1] != f"原决定：{old[3]}\n{old[4]}":
                raise AuthoringReviewError(
                    "incremental proposal does not preserve original opinion"
                )
            if decision[3] != "同意" or decision[4] != "":
                raise AuthoringReviewError(
                    "incremental decision is pending or needs comment review"
                )
            question, policy = _split_proposal(english[2])
        elif english[2] != "":
            raise AuthoringReviewError("unreviewed English revision cannot inherit an old decision")
        payload: dict[str, object] = {
            "template_id": template_id,
            "source_record_sha256": source.record_sha256,
            "source_family": source.family,
            "family": None if template_id == "UNSUP-09" else source.family,
            "disposition": "excluded"
            if template_id in _EXCLUDED_IDS
            else ("external_extension" if template_id == "UNSUP-14" else "core"),
            "original_question_text": source.question_text_template,
            "original_question_zh": old[2],
            "original_decision": old[3],
            "original_notes": old[4],
            "incremental_decision": decision[3] if decision else None,
            "incremental_notes": decision[4] if decision else None,
            "proposed_question_zh": decision[2] if decision else None,
            "change_scope": english[3],
            "question_text_template": question,
            "response_policy": policy,
            "proposal_sha256": None,
            "entity_slots": tuple(sorted(set(_SLOT_RE.findall(question)))),
            "benchmark_review_status": "pending",
            "executable": False,
            "wording_status": "excluded" if template_id in _EXCLUDED_IDS else "human_accepted",
            "gold": None,
            "oracle": None,
            "approval": None,
        }
        if decision:
            payload["proposal_sha256"] = canonical_json_sha256(
                {key: payload[key] for key in _PROPOSAL_FIELDS}
            )
        try:
            records.append(
                AuthoringReviewRecord.model_validate(
                    {
                        **payload,
                        "record_sha256": canonical_json_sha256(payload),
                    }
                )
            )
        except ValueError as exc:
            raise AuthoringReviewError("reviewed authoring record is invalid") from exc
    payload = {
        "schema_version": "rag-value-authoring-review-ledger-v1",
        "original_workbook_sha256": expected_original_sha256,
        "incremental_workbook_sha256": expected_incremental_sha256,
        "source_templates_sha256": _source_templates_sha256(),
        "records": tuple(records),
        "record_count": 64,
        "core_count": 53,
        "extension_count": 1,
        "excluded_count": 10,
        "checksum_notice": (
            "Checksums verify integrity, not human identity, signatures, "
            "Gold, or execution authority."
        ),
    }
    try:
        ledger = AuthoringReviewLedger.model_validate(
            {
                **payload,
                "manifest_sha256": canonical_json_sha256(payload),
            }
        )
    except ValueError as exc:
        raise AuthoringReviewError("reviewed authoring ledger is invalid") from exc
    return validate_authoring_ledger(ledger)


_PROPOSAL_FIELDS = (
    "template_id",
    "source_record_sha256",
    "proposed_question_zh",
    "change_scope",
    "question_text_template",
    "response_policy",
    "family",
    "disposition",
)


def _proposal_payload(record: AuthoringReviewRecord) -> dict[str, object]:
    return {key: getattr(record, key) for key in _PROPOSAL_FIELDS}


def _self_sha256(value: StrictFrozenSchema, field: str) -> str:
    return canonical_json_sha256(value.model_dump(mode="python", exclude={field}))


@cache
def _source_templates_sha256() -> str:
    return hashlib.sha256(scientific_questions_template_bytes()).hexdigest()


@cache
def _source_templates() -> dict[str, ScientificQuestionTemplate]:
    return {item.template_id: item for item in build_scientific_question_templates()}


def _split_proposal(text: str) -> tuple[str, str]:
    marker = "\nProposed response policy: "
    if not text.startswith("Question: ") or text.count(marker) != 1:
        raise AuthoringReviewError("incremental English proposal has an unexpected format")
    question, policy = text.removeprefix("Question: ").split(marker)
    if not question.strip() or not policy.strip():
        raise AuthoringReviewError("incremental question and response policy must be nonempty")
    return question, policy


@dataclass(frozen=True, slots=True)
class _Cell:
    text: str
    formula: bool
    valid_text_type: bool = True


type _Workbook = dict[str, dict[str, _Cell]]


def _value(workbook: _Workbook, sheet: str, address: str) -> str:
    if sheet not in workbook:
        raise AuthoringReviewError("required authoring worksheet is missing")
    cell = workbook[sheet].get(address, _Cell("", False))
    if cell.formula:
        raise AuthoringReviewError("formulas are forbidden in source or review input cells")
    if not cell.valid_text_type:
        raise AuthoringReviewError("source and review input cells must contain literal text")
    return cell.text


def _header(workbook: _Workbook, sheet: str, row: int, expected: tuple[str, ...]) -> None:
    if (
        tuple(_value(workbook, sheet, f"{chr(65 + index)}{row}") for index in range(len(expected)))
        != expected
    ):
        raise AuthoringReviewError("authoring worksheet headers do not match")


def _table(
    workbook: _Workbook,
    sheet: str,
    start: int,
    end: int,
    width: int,
) -> tuple[tuple[str, ...], ...]:
    rows = tuple(
        tuple(_value(workbook, sheet, f"{chr(65 + index)}{row}") for index in range(width))
        for row in range(start, end + 1)
    )
    ids = tuple(row[0] for row in rows)
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        raise AuthoringReviewError("authoring worksheet has missing or duplicate IDs")
    return rows


def _read_workbook(path: Path, expected_sha256: str) -> _Workbook:
    if _SHA_RE.fullmatch(expected_sha256) is None or path.is_symlink():
        raise AuthoringReviewError("workbook needs an exact checksum and a nonsymlink input")
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_RAW_BYTES + 1)
        if len(raw) > _MAX_RAW_BYTES:
            raise AuthoringReviewError("authoring workbook exceeds the compressed-size limit")
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise AuthoringReviewError("authoring workbook checksum does not match pinned bytes")
        with ZipFile(io.BytesIO(raw)) as archive:
            _validate_archive(archive)
            shared = _shared_strings(archive)
            paths = _worksheet_paths(archive)
            return {
                name: _worksheet_cells(_xml(archive, member), shared)
                for name, member in paths.items()
            }
    except AuthoringReviewError:
        raise
    except (
        OSError,
        BadZipFile,
        KeyError,
        ValueError,
        SafeET.ParseError,
        DefusedXmlException,
        RuntimeError,
        NotImplementedError,
    ) as exc:
        raise AuthoringReviewError("authoring workbook is unreadable or unsafe") from exc


def _validate_archive(archive: ZipFile) -> None:
    members = archive.infolist()
    names = [member.filename for member in members]
    if len(names) > 128 or len(names) != len(set(names)):
        raise AuthoringReviewError("authoring ZIP contains too many or duplicate members")
    if sum(member.file_size for member in members) > _MAX_TOTAL_BYTES:
        raise AuthoringReviewError("authoring ZIP exceeds the uncompressed-size limit")
    for member in members:
        path = PurePosixPath(member.filename)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in member.filename
            or ":" in member.filename
            or member.flag_bits & 1
            or (member.external_attr >> 16) & 0o170000 == 0o120000
            or member.file_size > _MAX_MEMBER_BYTES
            or member.file_size > max(member.compress_size, 1) * 200
        ):
            raise AuthoringReviewError("authoring ZIP contains an unsafe or oversized member")


def _xml(archive: ZipFile, member: str) -> Element:
    return SafeET.fromstring(
        archive.read(member), forbid_dtd=True, forbid_entities=True, forbid_external=True
    )


def _shared_strings(archive: ZipFile) -> tuple[str, ...]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return ()
    strings = tuple(
        "".join(node.text or "" for node in item.iter(f"{{{_NS}}}t"))
        for item in _xml(archive, "xl/sharedStrings.xml").findall(f"{{{_NS}}}si")
    )
    if len(strings) > 20_000 or any(len(text) > _MAX_TEXT for text in strings):
        raise AuthoringReviewError("authoring shared strings exceed bounded input limits")
    return strings


def _worksheet_paths(archive: ZipFile) -> dict[str, str]:
    relationships: dict[str, str] = {}
    for node in _xml(archive, "xl/_rels/workbook.xml.rels").findall(
        f"{{{_PACKAGE_NS}}}Relationship",
    ):
        key, target = node.attrib["Id"], node.attrib["Target"]
        if key in relationships or node.attrib.get("TargetMode") == "External":
            raise AuthoringReviewError("duplicate or external workbook relationship")
        if "\\" in target or ":" in target or ".." in PurePosixPath(target).parts:
            raise AuthoringReviewError("unsafe workbook relationship target")
        relationships[key] = posixpath.normpath(
            target.lstrip("/") if target.startswith("/") else posixpath.join("xl", target)
        )
    paths: dict[str, str] = {}
    for sheet in _xml(archive, "xl/workbook.xml").iter(f"{{{_NS}}}sheet"):
        name, key = sheet.attrib["name"], sheet.attrib[f"{{{_REL_NS}}}id"]
        target = relationships[key]
        if (
            name in paths
            or target in paths.values()
            or not target.startswith("xl/worksheets/")
            or target not in archive.namelist()
        ):
            raise AuthoringReviewError("duplicate or unsafe worksheet identity")
        paths[name] = target
    if len(paths) > 16:
        raise AuthoringReviewError("authoring workbook contains too many worksheets")
    return paths


def _worksheet_cells(root: Element, shared: tuple[str, ...]) -> dict[str, _Cell]:
    cells: dict[str, _Cell] = {}
    for cell in root.iter(f"{{{_NS}}}c"):
        address = cell.attrib.get("r", "")
        if _CELL_RE.fullmatch(address) is None or address in cells or len(cells) >= 20_000:
            raise AuthoringReviewError("duplicate, invalid, or excessive worksheet cells")
        value = cell.find(f"{{{_NS}}}v")
        text = "" if value is None else value.text or ""
        kind = cell.attrib.get("t")
        if kind == "s":
            if not text.isdigit() or int(text) >= len(shared):
                raise AuthoringReviewError("invalid shared string index")
            text = shared[int(text)]
        elif kind == "inlineStr":
            text = "".join(node.text or "" for node in cell.iter(f"{{{_NS}}}t"))
        if len(text) > _MAX_TEXT:
            raise AuthoringReviewError("worksheet cell exceeds the text-size limit")
        cells[address] = _Cell(
            text,
            cell.find(f"{{{_NS}}}f") is not None,
            kind in {None, "s", "inlineStr", "str", "n"},
        )
    return cells
