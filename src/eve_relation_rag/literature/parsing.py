"""Safe deterministic parsing into typed blocks and canonical locators."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from typing import Any, cast
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as DefusedET
from markdown_it import MarkdownIt
from markdown_it.token import Token
from pydantic import Field

from eve_relation_rag.literature.contracts import (
    PARSER_POLICY_KEY,
    BlockType,
    CanonicalLocator,
    DocumentFormat,
    JatsLocator,
    MarkdownLocator,
    PlainTextLocator,
    StrictFrozenSchema,
)

_MAX_NORMALIZED_CODEPOINTS = 5_000_000
_PROHIBITED_XML_MARKERS = (
    (re.compile(rb"<!DOCTYPE", re.IGNORECASE), "DOCTYPE"),
    (re.compile(rb"<!ENTITY", re.IGNORECASE), "ENTITY"),
    (re.compile(rb"<(?:[A-Za-z_][\w.-]*:)?include\b", re.IGNORECASE), "XInclude"),
)


class DocumentParseError(ValueError):
    """Raised when untrusted document bytes violate the parser contract."""


class NormalizedBlock(StrictFrozenSchema):
    """One typed, non-empty source block with a resolvable locator."""

    block_index: int = Field(ge=0)
    block_type: BlockType
    section_path: tuple[str, ...] = Field(max_length=32)
    text: str = Field(min_length=1)
    locator: CanonicalLocator
    locator_text: str = Field(min_length=1)


class ParsedDocument(StrictFrozenSchema):
    """Normalized parser output before token-aware chunking."""

    parser_policy_key: str
    document_format: DocumentFormat
    title: str = Field(min_length=1)
    normalized_source_text: str = Field(min_length=1)
    normalized_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    blocks: tuple[NormalizedBlock, ...] = Field(min_length=1)


def parse_document(document_format: str, payload: bytes) -> ParsedDocument:
    """Parse one explicitly typed local artifact without format inference."""

    if document_format not in {"markdown", "plain_text", "jats_xml"}:
        raise DocumentParseError(f"unsupported document format: {document_format}")
    normalized = _normalize_source(payload)
    if document_format == "markdown":
        title, blocks = _parse_markdown(normalized)
    elif document_format == "plain_text":
        title, blocks = _parse_plain_text(normalized)
    else:
        title, blocks = _parse_jats(payload, normalized)
    if not blocks:
        raise DocumentParseError("document produced no stable non-empty blocks")
    return ParsedDocument(
        parser_policy_key=PARSER_POLICY_KEY,
        document_format=cast(DocumentFormat, document_format),
        title=title,
        normalized_source_text=normalized,
        normalized_document_sha256=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        blocks=tuple(blocks),
    )


def resolve_locator(document_format: str, payload: bytes, locator: CanonicalLocator) -> str:
    """Reparse source bytes and resolve exactly one canonical locator."""

    parsed = parse_document(document_format, payload)
    matches = tuple(block.text for block in parsed.blocks if block.locator == locator)
    if len(matches) != 1:
        raise DocumentParseError("locator did not resolve to exactly one normalized block")
    return matches[0]


def _normalize_source(payload: bytes) -> str:
    try:
        decoded = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise DocumentParseError("document must be strict UTF-8") from exc
    normalized = unicodedata.normalize("NFC", decoded.replace("\r\n", "\n").replace("\r", "\n"))
    if len(normalized) > _MAX_NORMALIZED_CODEPOINTS:
        raise DocumentParseError("normalized document exceeds the code-point limit")
    for character in normalized:
        if character in {"\n", "\t"}:
            continue
        if unicodedata.category(character).startswith("C"):
            raise DocumentParseError("document contains a prohibited control or format character")

    lines = [line.rstrip(" \t") for line in normalized.split("\n")]
    canonical_lines: list[str] = []
    previous_blank = False
    for line in lines:
        is_blank = not line
        if is_blank and previous_blank:
            continue
        canonical_lines.append(line)
        previous_blank = is_blank
    while canonical_lines and not canonical_lines[0]:
        canonical_lines.pop(0)
    while canonical_lines and not canonical_lines[-1]:
        canonical_lines.pop()
    result = "\n".join(canonical_lines)
    if not result.strip():
        raise DocumentParseError("document contains no non-whitespace text")
    return result + "\n"


def _parse_markdown(source: str) -> tuple[str, list[NormalizedBlock]]:
    parser = MarkdownIt("commonmark", {"html": False}).enable("table")
    tokens = parser.parse(source)
    blocks: list[NormalizedBlock] = []
    ordinals: defaultdict[str, int] = defaultdict(int)
    heading_levels: dict[int, str] = {}
    title: str | None = None
    list_depth = 0
    index = 0

    while index < len(tokens):
        token = tokens[index]
        if token.type == "heading_open":
            inline = _next_inline(tokens, index)
            level = int(token.tag.removeprefix("h"))
            heading_text = _clean_inline_text(inline.content)
            heading_levels = {
                observed: value for observed, value in heading_levels.items() if observed < level
            }
            if level == 1 and title is None:
                title = heading_text
                _append_markdown_block(
                    blocks,
                    ordinals,
                    block_type="title",
                    section_path=(),
                    text=heading_text,
                    source_map=token.map,
                )
            else:
                heading_levels[level] = heading_text
            index += 1
        elif token.type == "list_item_open":
            list_depth += 1
            inline = _inline_before_close(tokens, index, "list_item_close")
            _append_markdown_block(
                blocks,
                ordinals,
                block_type="list_item",
                section_path=_markdown_section_path(heading_levels),
                text=_clean_inline_text(inline.content),
                source_map=token.map,
            )
        elif token.type == "list_item_close":
            list_depth -= 1
        elif token.type == "table_open":
            close_index = _find_close(tokens, index, "table_close")
            cells = [
                _clean_inline_text(item.content)
                for item in tokens[index + 1 : close_index]
                if item.type == "inline" and item.content.strip()
            ]
            _append_markdown_block(
                blocks,
                ordinals,
                block_type="table",
                section_path=_markdown_section_path(heading_levels),
                text=" | ".join(cells),
                source_map=token.map,
            )
            index = close_index
        elif token.type == "paragraph_open" and list_depth == 0:
            inline = _next_inline(tokens, index)
            text = _clean_inline_text(inline.content)
            section_path = _markdown_section_path(heading_levels)
            block_type = _paragraph_block_type(section_path, text)
            _append_markdown_block(
                blocks,
                ordinals,
                block_type=block_type,
                section_path=section_path,
                text=text,
                source_map=token.map,
            )
        index += 1

    if title is None:
        title = blocks[0].text if blocks else "Untitled document"
    return title, blocks


def _append_markdown_block(
    blocks: list[NormalizedBlock],
    ordinals: defaultdict[str, int],
    *,
    block_type: BlockType,
    section_path: tuple[str, ...],
    text: str,
    source_map: list[int] | None,
) -> None:
    if not text.strip() or source_map is None:
        return
    ordinals[block_type] += 1
    line_start = source_map[0] + 1
    line_end = source_map[1]
    locator = MarkdownLocator(
        locator_type="markdown",
        heading_path=section_path,
        block_type=block_type,
        block_ordinal=ordinals[block_type],
        line_start=line_start,
        line_end=line_end,
        token_start=None,
        token_end=None,
    )
    blocks.append(
        NormalizedBlock(
            block_index=len(blocks),
            block_type=block_type,
            section_path=section_path,
            text=text,
            locator=locator,
            locator_text=(
                f"{'>'.join(section_path) + ', ' if section_path else ''}"
                f"{block_type} {ordinals[block_type]}, lines {line_start}-{line_end}"
            ),
        )
    )


def _next_inline(tokens: list[Token], index: int) -> Token:
    for token in tokens[index + 1 :]:
        if token.type == "inline":
            return token
        if token.type.endswith("_close"):
            break
    raise DocumentParseError("Markdown block is missing inline content")


def _inline_before_close(tokens: list[Token], index: int, close_type: str) -> Token:
    for token in tokens[index + 1 :]:
        if token.type == "inline":
            return token
        if token.type == close_type:
            break
    raise DocumentParseError("Markdown list item is missing inline content")


def _find_close(tokens: list[Token], index: int, close_type: str) -> int:
    for close_index in range(index + 1, len(tokens)):
        if tokens[close_index].type == close_type:
            return close_index
    raise DocumentParseError(f"Markdown token stream is missing {close_type}")


def _markdown_section_path(levels: dict[int, str]) -> tuple[str, ...]:
    return tuple(levels[level] for level in sorted(levels) if level > 1)


def _clean_inline_text(value: str) -> str:
    return " ".join(value.split())


def _paragraph_block_type(section_path: tuple[str, ...], text: str) -> BlockType:
    section = section_path[-1].casefold() if section_path else ""
    if section == "abstract":
        return "abstract"
    if section == "references":
        return "reference"
    if re.match(r"^table\s+\d", text, flags=re.IGNORECASE):
        return "table_caption"
    if re.match(r"^(?:figure|fig\.)\s+\d", text, flags=re.IGNORECASE):
        return "figure_caption"
    return "paragraph"


def _parse_plain_text(source: str) -> tuple[str, list[NormalizedBlock]]:
    lines = source.rstrip("\n").split("\n")
    paragraphs: list[tuple[int, int, str]] = []
    start: int | None = None
    buffer: list[str] = []
    for line_number, line in enumerate(lines, start=1):
        if line:
            if start is None:
                start = line_number
            buffer.append(line)
        elif buffer and start is not None:
            paragraphs.append((start, line_number - 1, " ".join(buffer)))
            start = None
            buffer = []
    if buffer and start is not None:
        paragraphs.append((start, len(lines), " ".join(buffer)))
    if not paragraphs:
        raise DocumentParseError("plain-text document contains no paragraphs")

    blocks: list[NormalizedBlock] = []
    for paragraph_ordinal, (line_start, line_end, text) in enumerate(paragraphs, start=1):
        block_type: BlockType = "title" if paragraph_ordinal == 1 else "paragraph"
        locator = PlainTextLocator(
            locator_type="plain_text",
            paragraph_ordinal=paragraph_ordinal,
            line_start=line_start,
            line_end=line_end,
            token_start=None,
            token_end=None,
        )
        blocks.append(
            NormalizedBlock(
                block_index=len(blocks),
                block_type=block_type,
                section_path=(),
                text=text,
                locator=locator,
                locator_text=f"paragraph {paragraph_ordinal}, lines {line_start}-{line_end}",
            )
        )
    return paragraphs[0][2], blocks


def _parse_jats(payload: bytes, normalized_source: str) -> tuple[str, list[NormalizedBlock]]:
    del normalized_source
    for pattern, label in _PROHIBITED_XML_MARKERS:
        if pattern.search(payload):
            raise DocumentParseError(f"JATS {label} constructs are prohibited")
    try:
        root = DefusedET.fromstring(payload)
    except Exception as exc:
        raise DocumentParseError("JATS XML is malformed or unsafe") from exc
    if _local_name(root.tag) != "article":
        raise DocumentParseError("JATS requires one article root")

    parents = {child: parent for parent in root.iter() for child in parent}
    blocks: list[NormalizedBlock] = []
    ordinals: defaultdict[str, int] = defaultdict(int)
    article_title = next(
        (element for element in root.iter() if _local_name(element.tag) == "article-title"),
        None,
    )
    if article_title is None or not _element_text(article_title):
        raise DocumentParseError("JATS article-title is required")
    title = _element_text(article_title)
    _append_jats_block(
        blocks,
        ordinals,
        root=root,
        parents=parents,
        element=article_title,
        block_type="title",
        section_path=(),
        text=title,
    )

    for abstract in (element for element in root.iter() if _local_name(element.tag) == "abstract"):
        for paragraph in (item for item in abstract.iter() if _local_name(item.tag) == "p"):
            _append_jats_block(
                blocks,
                ordinals,
                root=root,
                parents=parents,
                element=paragraph,
                block_type="abstract",
                section_path=("Abstract",),
                text=_element_text(paragraph),
            )

    body = next((element for element in root if _local_name(element.tag) == "body"), None)
    if body is not None:
        _walk_jats_children(
            body,
            section_path=(),
            blocks=blocks,
            ordinals=ordinals,
            root=root,
            parents=parents,
        )
    return title, blocks


def _walk_jats_children(
    container: Element,
    *,
    section_path: tuple[str, ...],
    blocks: list[NormalizedBlock],
    ordinals: defaultdict[str, int],
    root: Element,
    parents: dict[Element, Element],
) -> None:
    for element in container:
        tag = _local_name(element.tag)
        if tag == "title":
            continue
        if tag == "sec":
            section_title_element = next(
                (child for child in element if _local_name(child.tag) == "title"), None
            )
            section_title = (
                _element_text(section_title_element)
                if section_title_element is not None
                else "Untitled section"
            )
            _walk_jats_children(
                element,
                section_path=(*section_path, section_title),
                blocks=blocks,
                ordinals=ordinals,
                root=root,
                parents=parents,
            )
        elif tag == "p":
            _append_jats_block(
                blocks,
                ordinals,
                root=root,
                parents=parents,
                element=element,
                block_type=_paragraph_block_type(section_path, _element_text(element)),
                section_path=section_path,
                text=_element_text(element),
            )
        elif tag == "list":
            for item in (
                child for child in element.iter() if _local_name(child.tag) == "list-item"
            ):
                _append_jats_block(
                    blocks,
                    ordinals,
                    root=root,
                    parents=parents,
                    element=item,
                    block_type="list_item",
                    section_path=section_path,
                    text=_element_text(item),
                )
        elif tag == "fig":
            caption = next(
                (child for child in element if _local_name(child.tag) == "caption"), None
            )
            if caption is not None:
                _append_jats_block(
                    blocks,
                    ordinals,
                    root=root,
                    parents=parents,
                    element=caption,
                    block_type="figure_caption",
                    section_path=section_path,
                    text=_element_text(caption),
                )
        elif tag == "table-wrap":
            caption = next(
                (child for child in element if _local_name(child.tag) == "caption"), None
            )
            table = next((child for child in element if _local_name(child.tag) == "table"), None)
            if caption is not None:
                _append_jats_block(
                    blocks,
                    ordinals,
                    root=root,
                    parents=parents,
                    element=caption,
                    block_type="table_caption",
                    section_path=section_path,
                    text=_element_text(caption),
                )
            if table is not None:
                cells = [
                    _element_text(cell)
                    for cell in table.iter()
                    if _local_name(cell.tag) in {"td", "th"} and _element_text(cell)
                ]
                _append_jats_block(
                    blocks,
                    ordinals,
                    root=root,
                    parents=parents,
                    element=table,
                    block_type="table",
                    section_path=section_path,
                    text=" | ".join(cells),
                )
        elif tag == "ref-list":
            for reference in (child for child in element.iter() if _local_name(child.tag) == "ref"):
                _append_jats_block(
                    blocks,
                    ordinals,
                    root=root,
                    parents=parents,
                    element=reference,
                    block_type="reference",
                    section_path=(*section_path, "References"),
                    text=_element_text(reference),
                )
        elif tag == "supplementary-material":
            _append_jats_block(
                blocks,
                ordinals,
                root=root,
                parents=parents,
                element=element,
                block_type="supplementary",
                section_path=section_path,
                text=_element_text(element),
            )
        else:
            _walk_jats_children(
                element,
                section_path=section_path,
                blocks=blocks,
                ordinals=ordinals,
                root=root,
                parents=parents,
            )


def _append_jats_block(
    blocks: list[NormalizedBlock],
    ordinals: defaultdict[str, int],
    *,
    root: Element,
    parents: dict[Element, Element],
    element: Element,
    block_type: BlockType,
    section_path: tuple[str, ...],
    text: str,
) -> None:
    if not text.strip():
        return
    ordinals[block_type] += 1
    path = _xml_path(root, element, parents)
    locator = JatsLocator(
        locator_type="jats_xml",
        section_path=section_path,
        element_type=block_type,
        element_ordinal=ordinals[block_type],
        xml_element_path=path,
        line_start=None,
        line_end=None,
        token_start=None,
        token_end=None,
    )
    blocks.append(
        NormalizedBlock(
            block_index=len(blocks),
            block_type=block_type,
            section_path=section_path,
            text=text,
            locator=locator,
            locator_text=(
                f"{'>'.join(section_path) + ', ' if section_path else ''}"
                f"{block_type} {ordinals[block_type]}, {path}"
            ),
        )
    )


def _xml_path(root: Element, element: Element, parents: dict[Element, Element]) -> str:
    if element is root:
        return "/article"
    parts: list[str] = []
    current = element
    while current is not root:
        parent = parents[current]
        tag = _local_name(current.tag)
        siblings = [child for child in parent if _local_name(child.tag) == tag]
        parts.append(f"{tag}[{siblings.index(current) + 1}]")
        current = parent
    return "/article/" + "/".join(reversed(parts))


def _local_name(tag: Any) -> str:
    value = str(tag)
    return value.rsplit("}", 1)[-1]


def _element_text(element: Element | None) -> str:
    if element is None:
        return ""
    return " ".join("".join(cast(Iterable[str], element.itertext())).split())
