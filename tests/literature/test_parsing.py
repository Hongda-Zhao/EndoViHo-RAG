from __future__ import annotations

from pathlib import Path

import pytest

from eve_relation_rag.literature.parsing import (
    DocumentParseError,
    parse_document,
    resolve_locator,
)

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "literature"


@pytest.mark.parametrize(
    ("filename", "document_format", "expected_title"),
    [
        ("synthetic_article.md", "markdown", "Synthetic EVE Retrieval Article"),
        (
            "synthetic_notes.txt",
            "plain_text",
            "Synthetic plain-text literature fixture",
        ),
        ("synthetic_article.xml", "jats_xml", "Synthetic JATS Retrieval Article"),
    ],
)
def test_all_three_safe_formats_parse_into_typed_resolvable_blocks(
    filename: str,
    document_format: str,
    expected_title: str,
) -> None:
    payload = (FIXTURE_ROOT / filename).read_bytes()

    parsed = parse_document(document_format, payload)

    assert parsed.title == expected_title
    assert parsed.blocks
    assert parsed.normalized_document_sha256
    assert parsed.parser_policy_key == "parser:endoviho-documents-v2"
    for block in parsed.blocks:
        assert block.text.strip()
        assert resolve_locator(document_format, payload, block.locator) == block.text


def test_markdown_preserves_sections_lists_table_captions_and_untrusted_text() -> None:
    parsed = parse_document("markdown", (FIXTURE_ROOT / "synthetic_article.md").read_bytes())

    block_types = {block.block_type for block in parsed.blocks}
    assert {"title", "abstract", "paragraph", "list_item", "table", "table_caption"}.issubset(
        block_types
    )
    assert any(block.section_path == ("Methods",) for block in parsed.blocks)
    assert any("ignore prior instructions" in block.text for block in parsed.blocks)
    assert all(block.locator.locator_type == "markdown" for block in parsed.blocks)


def test_plain_text_paragraphs_have_exact_one_based_line_ranges() -> None:
    payload = (FIXTURE_ROOT / "synthetic_notes.txt").read_bytes()
    parsed = parse_document("plain_text", payload)

    assert parsed.blocks[0].block_type == "title"
    assert parsed.blocks[0].locator.line_start == 1
    assert parsed.blocks[0].locator.line_end == 1
    assert [block.locator.paragraph_ordinal for block in parsed.blocks] == [1, 2, 3, 4]


def test_jats_preserves_section_paths_table_and_figure_captions() -> None:
    parsed = parse_document("jats_xml", (FIXTURE_ROOT / "synthetic_article.xml").read_bytes())

    assert any(block.block_type == "figure_caption" for block in parsed.blocks)
    assert any(block.block_type == "table_caption" for block in parsed.blocks)
    assert any(block.block_type == "table" for block in parsed.blocks)
    assert any(block.section_path == ("Methods",) for block in parsed.blocks)
    assert all(block.locator.xml_element_path.startswith("/article/") for block in parsed.blocks)


@pytest.mark.parametrize(
    ("document_format", "payload", "message"),
    [
        ("plain_text", b"invalid-utf8:\xff", "UTF-8"),
        ("jats_xml", b"<!DOCTYPE article><article/>", "DOCTYPE"),
        (
            "jats_xml",
            b"<!ENTITY x SYSTEM 'file:///etc/passwd'><article>&x;</article>",
            "ENTITY",
        ),
        (
            "jats_xml",
            b"<article xmlns:xi='http://www.w3.org/2001/XInclude'><xi:include href='x'/></article>",
            "XInclude",
        ),
        ("jats_xml", b"<not-article/>", "article root"),
        ("markdown", b"text\x00control", "control"),
    ],
)
def test_parser_rejects_unsafe_or_noncanonical_inputs(
    document_format: str, payload: bytes, message: str
) -> None:
    with pytest.raises(DocumentParseError, match=message):
        parse_document(document_format, payload)


def test_parser_rejects_unsupported_format_without_inference() -> None:
    with pytest.raises(DocumentParseError, match="unsupported"):
        parse_document("pdf", b"%PDF synthetic")
