"""Deterministic token-offset chunking for parsed literature documents."""

from __future__ import annotations

import hashlib
from typing import Protocol

from pydantic import Field, TypeAdapter

from eve_relation_rag.literature.contracts import (
    CHUNKING_POLICY_KEY,
    PARSER_POLICY_KEY,
    BlockType,
    CanonicalLocator,
    ChunkKeyPreimage,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.hashing import chunk_key
from eve_relation_rag.literature.parsing import ParsedDocument

TARGET_TOKENS = 384
OVERLAP_TOKENS = 64
MAX_TOKENS = 448

_LOCATOR_ADAPTER: TypeAdapter[CanonicalLocator] = TypeAdapter(CanonicalLocator)


class ChunkingError(ValueError):
    """Raised when tokenizer output cannot satisfy the chunking contract."""


class TokenSpan(StrictFrozenSchema):
    """One tokenizer token with a zero-based half-open source character range."""

    token_index: int = Field(ge=0)
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)


class OffsetTokenizer(Protocol):
    """Minimal tokenizer interface required for deterministic source slicing."""

    @property
    def model_key(self) -> str: ...

    def token_spans(self, text: str) -> tuple[TokenSpan, ...]: ...


class DocumentChunkDraft(StrictFrozenSchema):
    """Validated persistence-neutral representation of one corpus chunk."""

    chunk_key: str = Field(pattern=r"^chunk:sha256:[0-9a-f]{64}$")
    corpus_release_key: str
    document_key: str = Field(pattern=r"^document:sha256:[0-9a-f]{64}$")
    parser_policy_key: str
    chunking_policy_key: str
    chunk_index: int = Field(ge=0)
    section_path: tuple[str, ...] = Field(max_length=32)
    block_type: BlockType
    locator: CanonicalLocator
    locator_text: str = Field(min_length=1)
    text: str = Field(min_length=1)
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    token_count: int = Field(gt=0, le=MAX_TOKENS)


def chunk_document(
    parsed: ParsedDocument,
    *,
    corpus_release_key: str,
    document_key: str,
    tokenizer: OffsetTokenizer,
) -> tuple[DocumentChunkDraft, ...]:
    """Chunk typed blocks independently using exact tokenizer character offsets."""

    if parsed.parser_policy_key != PARSER_POLICY_KEY:
        raise ChunkingError("parsed document does not use the approved parser policy")
    if not tokenizer.model_key.strip():
        raise ChunkingError("tokenizer model_key must be non-empty")

    drafts: list[DocumentChunkDraft] = []
    for block in parsed.blocks:
        spans = tokenizer.token_spans(block.text)
        _validate_token_spans(block.text, spans)
        for token_start, token_end in _chunk_windows(len(spans)):
            first = spans[token_start]
            last = spans[token_end - 1]
            text = block.text[first.char_start : last.char_end]
            text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
            locator = block.locator
            locator_text = block.locator_text
            if len(spans) > TARGET_TOKENS:
                locator = _LOCATOR_ADAPTER.validate_python(
                    {
                        **block.locator.model_dump(mode="python"),
                        "token_start": token_start,
                        "token_end": token_end,
                    }
                )
                locator_text = f"{locator_text}, tokens {token_start}-{token_end}"

            chunk_index = len(drafts)
            preimage = ChunkKeyPreimage(
                key_schema_version="chunk-key-v1",
                corpus_release_key=corpus_release_key,
                document_key=document_key,
                parser_policy_key=PARSER_POLICY_KEY,
                chunking_policy_key=CHUNKING_POLICY_KEY,
                section_path=block.section_path,
                locator=locator,
                chunk_index=chunk_index,
                normalized_chunk_text=text,
                normalized_text_sha256=text_sha256,
            )
            drafts.append(
                DocumentChunkDraft(
                    chunk_key=chunk_key(preimage),
                    corpus_release_key=corpus_release_key,
                    document_key=document_key,
                    parser_policy_key=PARSER_POLICY_KEY,
                    chunking_policy_key=CHUNKING_POLICY_KEY,
                    chunk_index=chunk_index,
                    section_path=block.section_path,
                    block_type=block.block_type,
                    locator=locator,
                    locator_text=locator_text,
                    text=text,
                    text_sha256=text_sha256,
                    token_count=token_end - token_start,
                )
            )
    if not drafts:
        raise ChunkingError("document produced no chunks")
    return tuple(drafts)


def _validate_token_spans(text: str, spans: tuple[TokenSpan, ...]) -> None:
    if not spans:
        raise ChunkingError("non-empty block produced no tokenizer tokens")
    previous_end = 0
    for expected_index, span in enumerate(spans):
        if span.token_index != expected_index:
            raise ChunkingError("token indices must be contiguous and zero-based")
        if span.char_start >= span.char_end or span.char_end > len(text):
            raise ChunkingError("token character range is outside the source block")
        if expected_index and span.char_start < previous_end:
            raise ChunkingError("token character ranges overlap or are out of order")
        previous_end = span.char_end


def _chunk_windows(token_count: int) -> tuple[tuple[int, int], ...]:
    if token_count <= TARGET_TOKENS:
        return ((0, token_count),)
    windows: list[tuple[int, int]] = []
    start = 0
    while start < token_count:
        end = min(start + TARGET_TOKENS, token_count)
        windows.append((start, end))
        if end == token_count:
            break
        start = end - OVERLAP_TOKENS
    if any(end - start > MAX_TOKENS for start, end in windows):
        raise ChunkingError("chunking policy exceeded its hard token limit")
    return tuple(windows)
