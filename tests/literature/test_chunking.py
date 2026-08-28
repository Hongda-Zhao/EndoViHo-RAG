from __future__ import annotations

import re

from eve_relation_rag.literature.chunking import TokenSpan, chunk_document
from eve_relation_rag.literature.parsing import parse_document

CORPUS_KEY = "corpus:endoviho-rag:v0:20990101:001"
DOCUMENT_KEY = f"document:sha256:{'a' * 64}"


class WhitespaceOffsetTokenizer:
    @property
    def model_key(self) -> str:
        return "tokenizer:test:whitespace-offset-v1"

    def token_spans(self, text: str) -> tuple[TokenSpan, ...]:
        return tuple(
            TokenSpan(token_index=index, char_start=match.start(), char_end=match.end())
            for index, match in enumerate(re.finditer(r"\S+", text))
        )


def test_short_typed_blocks_never_merge_across_boundaries() -> None:
    parsed = parse_document("plain_text", b"Title\n\nFirst block.\n\nSecond block.\n")

    chunks = chunk_document(
        parsed,
        corpus_release_key=CORPUS_KEY,
        document_key=DOCUMENT_KEY,
        tokenizer=WhitespaceOffsetTokenizer(),
    )

    assert len(chunks) == len(parsed.blocks)
    assert [chunk.block_type for chunk in chunks] == [block.block_type for block in parsed.blocks]
    assert all(chunk.token_count <= 448 for chunk in chunks)


def test_long_block_uses_384_target_64_overlap_and_stable_token_locators() -> None:
    words = [f"token{index:04d}" for index in range(700)]
    parsed = parse_document("plain_text", (" ".join(words) + "\n").encode())

    chunks = chunk_document(
        parsed,
        corpus_release_key=CORPUS_KEY,
        document_key=DOCUMENT_KEY,
        tokenizer=WhitespaceOffsetTokenizer(),
    )

    assert len(chunks) == 2
    assert chunks[0].token_count == 384
    assert chunks[1].token_count == 380
    assert chunks[0].text.split()[-64:] == chunks[1].text.split()[:64]
    assert chunks[0].locator.token_start == 0
    assert chunks[0].locator.token_end == 384
    assert chunks[1].locator.token_start == 320
    assert chunks[1].locator.token_end == 700
    assert all(chunk.token_count <= 448 for chunk in chunks)


def test_chunk_keys_are_reproducible_and_bind_exact_release() -> None:
    parsed = parse_document("plain_text", b"Title\n\nSynthetic paragraph.\n")
    tokenizer = WhitespaceOffsetTokenizer()

    first = chunk_document(
        parsed,
        corpus_release_key=CORPUS_KEY,
        document_key=DOCUMENT_KEY,
        tokenizer=tokenizer,
    )
    replay = chunk_document(
        parsed,
        corpus_release_key=CORPUS_KEY,
        document_key=DOCUMENT_KEY,
        tokenizer=tokenizer,
    )
    other_release = chunk_document(
        parsed,
        corpus_release_key="corpus:endoviho-rag:v0:20990101:002",
        document_key=DOCUMENT_KEY,
        tokenizer=tokenizer,
    )

    assert tuple(chunk.chunk_key for chunk in first) == tuple(chunk.chunk_key for chunk in replay)
    assert tuple(chunk.chunk_key for chunk in first) != tuple(
        chunk.chunk_key for chunk in other_release
    )
    assert [chunk.chunk_index for chunk in first] == list(range(len(first)))
