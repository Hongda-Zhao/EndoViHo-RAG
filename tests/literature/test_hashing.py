from __future__ import annotations

import hashlib
import math

import pytest

from eve_relation_rag.literature.contracts import (
    LITERATURE_REQUEST_VERSION,
    ChunkKeyPreimage,
    KeywordAnchor,
    LiteratureRetrievalRequest,
    LocusAnchor,
    PlainTextLocator,
)
from eve_relation_rag.literature.hashing import (
    CanonicalHashError,
    anchor_key,
    canonical_json_bytes,
    canonical_json_sha256,
    canonical_query_sha256,
    chunk_key,
    corpus_import_run_key,
    corpus_receipt_key,
)

CORPUS_KEY = "corpus:endoviho-rag:v0:20990101:001"


def _request(
    *, question: str = "What synthetic method was used?", top_k: int = 8
) -> LiteratureRetrievalRequest:
    return LiteratureRetrievalRequest(
        request_schema_version=LITERATURE_REQUEST_VERSION,
        corpus_release_key=CORPUS_KEY,
        question=question,
        top_k=top_k,
    )


def test_canonical_json_is_sorted_compact_utf8_and_unicode_nfc() -> None:
    decomposed = {"b": [1, True, None], "a": "e\u0301"}
    composed = {"a": "é", "b": [1, True, None]}
    expected = '{"a":"é","b":[1,true,null]}'.encode()

    assert canonical_json_bytes(decomposed) == expected
    assert canonical_json_bytes(composed) == expected
    assert canonical_json_sha256(decomposed) == hashlib.sha256(expected).hexdigest()


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_canonical_json_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(CanonicalHashError, match="non-finite"):
        canonical_json_bytes({"value": value})


@pytest.mark.parametrize(
    "value",
    [{1: "not a string key"}, {"bytes": b"no"}, {"set": {"no"}}],
)
def test_canonical_json_rejects_ambiguous_or_unsupported_values(value: object) -> None:
    with pytest.raises(CanonicalHashError):
        canonical_json_bytes(value)


def test_canonical_json_rejects_keys_that_collide_after_nfc() -> None:
    with pytest.raises(CanonicalHashError, match="collide"):
        canonical_json_bytes({"é": 1, "e\u0301": 2})


def test_all_m3_hash_namespaces_are_exact_and_payload_sensitive() -> None:
    assert anchor_key({"kind": "keyword", "phrase": "fixture"}).startswith("anchor:sha256:")
    assert corpus_import_run_key({"manifest": "a" * 64}).startswith("corpus-import:sha256:")
    assert corpus_receipt_key({"manifest": "a" * 64}).startswith("corpus-receipt:sha256:")

    text = "synthetic chunk"
    preimage = ChunkKeyPreimage(
        key_schema_version="chunk-key-v1",
        corpus_release_key=CORPUS_KEY,
        document_key=f"document:sha256:{'a' * 64}",
        parser_policy_key="parser:endoviho-documents-v2",
        chunking_policy_key="chunking:bge-small-en-v1.5:384-64-448-v2",
        section_path=("Methods",),
        locator=PlainTextLocator(
            locator_type="plain_text",
            paragraph_ordinal=1,
            line_start=1,
            line_end=1,
            token_start=None,
            token_end=None,
        ),
        chunk_index=0,
        normalized_chunk_text=text,
        normalized_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )
    first = chunk_key(preimage)
    second = chunk_key(preimage.model_copy(update={"chunk_index": 1}))

    assert first.startswith("chunk:sha256:")
    assert first != second


def test_query_hash_canonicalizes_anchor_order_and_binds_every_approved_input() -> None:
    locus = LocusAnchor(
        anchor_type="locus",
        anchor_key=f"anchor:sha256:{'1' * 64}",
        locus_key=f"locus:eve:v1:sha256:{'a' * 64}",
    )
    keyword = KeywordAnchor(
        anchor_type="keyword",
        anchor_key=f"anchor:sha256:{'2' * 64}",
        phrase="synthetic fixture",
    )

    forward = canonical_query_sha256(_request(), (locus, keyword))
    reverse = canonical_query_sha256(_request(), (keyword, locus))

    assert forward == reverse
    assert canonical_query_sha256(_request(top_k=9), (locus, keyword)) != forward
    assert (
        canonical_query_sha256(_request(question="A different question?"), (locus, keyword))
        != forward
    )
    assert canonical_query_sha256(_request(), (locus,)) != forward


def test_canonical_hashing_accepts_strict_pydantic_models_without_mutating_them() -> None:
    request = _request()
    before = request.model_dump()

    digest = canonical_json_sha256(request)

    assert len(digest) == 64
    assert request.model_dump() == before
