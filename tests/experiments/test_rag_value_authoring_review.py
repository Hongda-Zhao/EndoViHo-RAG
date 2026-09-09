"""Synthetic workbook integrity tests; no human identity or scientific Gold is invented."""

from __future__ import annotations

import hashlib
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from pydantic import ValidationError

from eve_relation_rag.experiments.rag_value_ablation import authoring_review as review
from eve_relation_rag.experiments.rag_value_ablation.annotations import (
    AnnotationError,
    require_trusted_question_set,
)
from eve_relation_rag.experiments.rag_value_ablation.scientific_questions import (
    build_scientific_question_templates,
    scientific_questions_template_bytes,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256

_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships"
type Sheets = dict[str, dict[str, str]]


def _row(sheet: dict[str, str], number: int, values: tuple[str, ...]) -> None:
    sheet.update({f"{chr(65 + index)}{number}": text for index, text in enumerate(values)})


def _synthetic_sheets() -> tuple[Sheets, Sheets]:
    original: Sheets = {"简版审批": {}, "原文与说明": {}}
    incremental: Sheets = {"旧审阅已对照": {}, "英文与版本对照": {}, "本轮只审新改动": {}}
    _row(
        original["简版审批"],
        8,
        (
            "编号",
            "题目类型",
            "一句话看懂问题",
            "你的决定",
            "修改或删除意见（可用中文）",
        ),
    )
    _row(original["原文与说明"], 21, ("编号", "完整英文原题", "原始状态", "源记录 SHA-256"))
    _row(incremental["旧审阅已对照"], 5, ("编号", "原题目类型", "原中文题意", "原决定", "原意见"))
    _row(
        incremental["英文与版本对照"],
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
    _row(
        incremental["本轮只审新改动"],
        8,
        (
            "编号",
            "你上次的决定和意见",
            "这次只需审阅的内容",
            "你的新决定",
            "补充意见（可用中文）",
        ),
    )
    for index, source in enumerate(build_scientific_question_templates()):
        template_id = source.template_id
        decision = (
            "删除"
            if template_id in review._EXCLUDED_IDS
            else ("修改" if template_id in review._MODIFIED_IDS else "保留")
        )
        notes = f"  软件测试意见：{template_id}\n保留空白  "
        old = (
            template_id,
            review._FAMILY_LABELS[source.family],
            f"合成题意 {template_id}",
            decision,
            notes,
        )
        _row(original["简版审批"], index + 9, old)
        _row(
            original["原文与说明"],
            index + 22,
            (
                template_id,
                source.question_text_template,
                "pending",
                source.record_sha256,
            ),
        )
        _row(incremental["旧审阅已对照"], index + 6, old)
        question = source.question_text_template
        proposal = ""
        scope = "原题不变，沿用保留" if decision == "保留" else "沿用删除"
        if template_id in review._INCREMENTAL_IDS:
            if template_id in review._QUESTION_CHANGED_IDS:
                question += " List the permitted evidence separately."
            proposal = f"Question: {question}\nProposed response policy: Synthetic policy only."
            scope = (
                "独立扩展提案，不启用联网" if template_id == "UNSUP-14" else "核心离线 benchmark"
            )
            _row(
                incremental["本轮只审新改动"],
                review._INCREMENTAL_IDS.index(template_id) + 9,
                (
                    template_id,
                    f"原决定：{decision}\n{notes}",
                    f"合成新提案 {template_id}",
                    "同意",
                    "",
                ),
            )
        _row(
            incremental["英文与版本对照"],
            index + 6,
            (
                template_id,
                source.question_text_template,
                proposal,
                scope,
                source.record_sha256,
                "pending",
            ),
        )
    incremental["英文与版本对照"]["B75"] = hashlib.sha256(
        scientific_questions_template_bytes(),
    ).hexdigest()
    return original, incremental


def _write_workbook(
    path: Path,
    sheets: Sheets,
    *,
    shared: bool = False,
    formula: tuple[str, str] | None = None,
    duplicate_cell: bool = False,
) -> str:
    workbook = Element(f"{{{_NS}}}workbook")
    sheet_nodes = SubElement(workbook, f"{{{_NS}}}sheets")
    relationships = Element(f"{{{_PACKAGE}}}Relationships")
    strings: list[str] = []
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for number, (name, cells) in enumerate(sheets.items(), 1):
            SubElement(
                sheet_nodes,
                f"{{{_NS}}}sheet",
                {
                    "name": name,
                    f"{{{_REL}}}id": f"rId{number}",
                },
            )
            SubElement(
                relationships,
                f"{{{_PACKAGE}}}Relationship",
                {
                    "Id": f"rId{number}",
                    "Target": f"worksheets/sheet{number}.xml",
                    "Type": f"{_REL}/worksheet",
                },
            )
            root = Element(f"{{{_NS}}}worksheet")
            data = SubElement(root, f"{{{_NS}}}sheetData")
            for address, text in cells.items():
                row = SubElement(data, f"{{{_NS}}}row")
                cell = SubElement(
                    row,
                    f"{{{_NS}}}c",
                    {
                        "r": address,
                        "t": "s" if shared else "inlineStr",
                    },
                )
                if formula == (name, address):
                    SubElement(cell, f"{{{_NS}}}f").text = '"同意"'
                if shared:
                    strings.append(text)
                    SubElement(cell, f"{{{_NS}}}v").text = str(len(strings) - 1)
                else:
                    inline = SubElement(cell, f"{{{_NS}}}is")
                    SubElement(inline, f"{{{_NS}}}t").text = text
            if duplicate_cell and number == 1:
                SubElement(data, f"{{{_NS}}}c", {"r": next(iter(cells))})
            archive.writestr(f"xl/worksheets/sheet{number}.xml", tostring(root))
        archive.writestr("xl/workbook.xml", tostring(workbook))
        archive.writestr("xl/_rels/workbook.xml.rels", tostring(relationships))
        if shared:
            root = Element(f"{{{_NS}}}sst")
            for text in strings:
                item = SubElement(root, f"{{{_NS}}}si")
                SubElement(item, f"{{{_NS}}}t").text = text
            archive.writestr("xl/sharedStrings.xml", tostring(root))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(
    tmp_path: Path,
    original: Sheets,
    incremental: Sheets,
    *,
    shared: bool = False,
    formula: tuple[str, str] | None = None,
    duplicate_cell: bool = False,
) -> review.AuthoringReviewLedger:
    first, second = tmp_path / "original.xlsx", tmp_path / "incremental.xlsx"
    original_sha = _write_workbook(first, original, shared=shared)
    incremental["英文与版本对照"]["B73"] = original_sha
    incremental_sha = _write_workbook(
        second,
        incremental,
        shared=shared,
        formula=formula,
        duplicate_cell=duplicate_cell,
    )
    return review.load_reviewed_workbooks(
        first,
        second,
        expected_original_sha256=original_sha,
        expected_incremental_sha256=incremental_sha,
    )


@pytest.mark.parametrize("shared", [False, True])
def test_complete_saved_review_is_authoring_only(tmp_path: Path, shared: bool) -> None:
    original, incremental = _synthetic_sheets()
    ledger = _load(tmp_path, original, incremental, shared=shared)
    assert (
        ledger.record_count,
        ledger.core_count,
        ledger.extension_count,
        ledger.excluded_count,
    ) == (64, 53, 1, 10)
    records = {item.template_id: item for item in ledger.records}
    assert records["UNSUP-09"].family is None
    assert records["UNSUP-14"].disposition == "external_extension"
    assert records["UNSUP-07"].disposition == "excluded"
    assert records["HOST-S-01"].original_notes == "  软件测试意见：HOST-S-01\n保留空白  "
    assert all(item.gold is item.oracle is item.approval is None for item in ledger.records)
    assert all(
        not item.executable and item.benchmark_review_status == "pending" for item in ledger.records
    )
    assert review.validate_authoring_ledger(ledger) == ledger
    assert review.AuthoringReviewLedger.model_validate_json(canonical_json_bytes(ledger)) == ledger
    with pytest.raises(AnnotationError, match="exact QuestionManifest"):
        require_trusted_question_set(ledger)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("sheet", "cell", "text"),
    [
        ("旧审阅已对照", "A6", "UNKNOWN"),
        ("旧审阅已对照", "A7", "HOST-S-01"),
        ("旧审阅已对照", "A6", ""),
        ("旧审阅已对照", "E6", "altered notes"),
        ("英文与版本对照", "B6", "Altered original English."),
        ("英文与版本对照", "E6", "0" * 64),
        ("英文与版本对照", "B75", "0" * 64),
        ("英文与版本对照", "C6", "Unreviewed new English."),
        ("英文与版本对照", "C5", "changed header"),
        ("本轮只审新改动", "A9", "UNKNOWN"),
        ("本轮只审新改动", "A10", "HOST-H-02"),
        ("本轮只审新改动", "B9", "altered old decision"),
        ("本轮只审新改动", "D9", "待定"),
        ("本轮只审新改动", "E9", "同意但是需要改变提案"),
    ],
)
def test_modified_or_incomplete_sources_fail_closed(
    tmp_path: Path,
    sheet: str,
    cell: str,
    text: str,
) -> None:
    original, incremental = _synthetic_sheets()
    incremental[sheet][cell] = text
    with pytest.raises(review.AuthoringReviewError):
        _load(tmp_path, original, incremental)


def test_changed_english_cannot_reuse_old_pinned_agreement(tmp_path: Path) -> None:
    original, incremental = _synthetic_sheets()
    ledger = _load(tmp_path, original, incremental)
    incremental["英文与版本对照"]["C15"] += " Unreviewed new policy."
    _write_workbook(tmp_path / "incremental.xlsx", incremental)
    with pytest.raises(review.AuthoringReviewError, match="pinned bytes"):
        review.load_reviewed_workbooks(
            tmp_path / "original.xlsx",
            tmp_path / "incremental.xlsx",
            expected_original_sha256=ledger.original_workbook_sha256,
            expected_incremental_sha256=ledger.incremental_workbook_sha256,
        )


def test_formulas_in_inputs_are_rejected_but_summary_formulas_are_not_used(tmp_path: Path) -> None:
    original, incremental = _synthetic_sheets()
    with pytest.raises(review.AuthoringReviewError, match="formulas"):
        _load(tmp_path, original, incremental, formula=("本轮只审新改动", "D9"))
    incremental["本轮只审新改动"]["A1"] = "ignored summary"
    assert _load(tmp_path, original, incremental, formula=("本轮只审新改动", "A1")).core_count == 53


def test_duplicate_xml_cell_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(review.AuthoringReviewError, match="duplicate"):
        _load(tmp_path, *_synthetic_sheets(), duplicate_cell=True)


def test_gold_or_approval_cannot_be_smuggled_through_construct_or_copy(tmp_path: Path) -> None:
    ledger = _load(tmp_path, *_synthetic_sheets())
    changed = ledger.records[0].model_dump(mode="python")
    changed["gold"] = {"invented": "not allowed"}
    changed["record_sha256"] = canonical_json_sha256(
        {key: value for key, value in changed.items() if key != "record_sha256"}
    )
    with pytest.raises(ValidationError):
        review.AuthoringReviewRecord.model_validate(changed)
    fake_record = review.AuthoringReviewRecord.model_construct(**changed)
    payload = ledger.model_dump(mode="python", exclude={"manifest_sha256"})
    payload["records"] = (fake_record, *ledger.records[1:])
    payload["manifest_sha256"] = canonical_json_sha256(payload)
    with pytest.raises(ValidationError):
        review.AuthoringReviewLedger.model_validate(payload)
    forged = review.AuthoringReviewLedger.model_construct(**payload)
    with pytest.raises(review.AuthoringReviewError, match="revalidation"):
        review.validate_authoring_ledger(forged)
    with pytest.raises(review.AuthoringReviewError, match="revalidation"):
        review.validate_authoring_ledger(ledger.model_copy(update={"core_count": 54}))


def test_rehashed_record_cannot_reuse_stale_proposal_checksum(tmp_path: Path) -> None:
    ledger = _load(tmp_path, *_synthetic_sheets())
    record = next(item for item in ledger.records if item.template_id == "HOST-H-02")
    changed = record.model_dump(mode="python", exclude={"record_sha256"})
    changed["response_policy"] = "An unreviewed replacement policy."
    changed["record_sha256"] = canonical_json_sha256(changed)
    with pytest.raises(ValidationError, match="proposal checksum"):
        review.AuthoringReviewRecord.model_validate(changed)


def test_authoring_ledger_subclasses_are_not_exportable(tmp_path: Path) -> None:
    class DerivedLedger(review.AuthoringReviewLedger):
        pass

    ledger = _load(tmp_path, *_synthetic_sheets())
    derived = DerivedLedger.model_validate_json(canonical_json_bytes(ledger))
    with pytest.raises(review.AuthoringReviewError, match="exact AuthoringReviewLedger"):
        review.validate_authoring_ledger(derived)


@pytest.mark.parametrize("unsafe_member", ["../escape.xml", "/absolute.xml", "xl\\bad.xml"])
def test_zip_member_paths_are_bounded(tmp_path: Path, unsafe_member: str) -> None:
    path = tmp_path / "unsafe.xlsx"
    with ZipFile(path, "w") as archive:
        archive.writestr(unsafe_member, b"bad")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(review.AuthoringReviewError, match="unsafe"):
        review._read_workbook(path, digest)


def test_original_source_templates_remain_unchanged(tmp_path: Path) -> None:
    before = scientific_questions_template_bytes()
    _load(tmp_path, *_synthetic_sheets())
    assert scientific_questions_template_bytes() == before
    assert all(
        item.review_status == "pending" and item.gold is None
        for item in build_scientific_question_templates()
    )


def test_missing_worksheet_and_revived_deletion_are_rejected(tmp_path: Path) -> None:
    original, incremental = _synthetic_sheets()
    del incremental["本轮只审新改动"]
    with pytest.raises(review.AuthoringReviewError, match="missing"):
        _load(tmp_path, original, incremental)
    original, incremental = _synthetic_sheets()
    deleted_index = next(
        index
        for index, source in enumerate(build_scientific_question_templates())
        if source.template_id == "HOST-H-03"
    )
    original["简版审批"][f"D{deleted_index + 9}"] = "保留"
    incremental["旧审阅已对照"][f"D{deleted_index + 6}"] = "保留"
    with pytest.raises(review.AuthoringReviewError, match="record is invalid"):
        _load(tmp_path, original, incremental)


def test_stale_original_reference_and_symlinks_are_rejected(tmp_path: Path) -> None:
    original, incremental = _synthetic_sheets()
    ledger = _load(tmp_path, original, incremental)
    incremental["英文与版本对照"]["B73"] = "0" * 64
    new_sha = _write_workbook(tmp_path / "incremental.xlsx", incremental)
    with pytest.raises(review.AuthoringReviewError, match="stale"):
        review.load_reviewed_workbooks(
            tmp_path / "original.xlsx",
            tmp_path / "incremental.xlsx",
            expected_original_sha256=ledger.original_workbook_sha256,
            expected_incremental_sha256=new_sha,
        )
    link = tmp_path / "linked.xlsx"
    link.symlink_to(tmp_path / "original.xlsx")
    with pytest.raises(review.AuthoringReviewError, match="nonsymlink"):
        review._read_workbook(link, ledger.original_workbook_sha256)


def test_dtd_and_compression_bombs_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.xlsx"
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", b"x" * (1024 * 1024))
    with pytest.raises(review.AuthoringReviewError, match="unsafe"):
        review._read_workbook(path, hashlib.sha256(path.read_bytes()).hexdigest())
    with ZipFile(path, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", b'<!DOCTYPE sst [<!ENTITY a "danger">]><sst/>')
    with pytest.raises(review.AuthoringReviewError, match="unsafe"):
        review._read_workbook(path, hashlib.sha256(path.read_bytes()).hexdigest())
