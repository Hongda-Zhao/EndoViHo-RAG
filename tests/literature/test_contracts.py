from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from eve_relation_rag.literature.contracts import (
    ANCHOR_POLICY_KEY,
    CHUNKING_POLICY_KEY,
    CORPUS_MANIFEST_VERSION,
    EMBEDDING_MODEL_KEY,
    FTS_POLICY_KEY,
    LITERATURE_ERROR_VERSION,
    LITERATURE_REQUEST_VERSION,
    PARSER_POLICY_KEY,
    RETRIEVAL_POLICY_KEY,
    RETRIEVED_CHUNKS_VERSION,
    AssemblyAnchor,
    ChunkKeyPreimage,
    CorpusDocumentSpec,
    CorpusManifest,
    DocumentAnchor,
    DocumentKeyPreimage,
    JatsLocator,
    KeywordAnchor,
    LiteratureRetrievalError,
    LiteratureRetrievalInvocation,
    LiteratureRetrievalRequest,
    LocusAnchor,
    MarkdownLocator,
    PlainTextLocator,
    RetrievalAnchor,
    RetrievedChunk,
    RetrievedChunks,
)
from eve_relation_rag.literature.hashing import (
    canonical_manifest_sha256,
    document_key,
)

CORPUS_KEY = "corpus:endoviho-rag:v0:20990101:001"
SHA_A = "a" * 64
SHA_B = "b" * 64
DOCUMENT_KEY = f"document:sha256:{SHA_A}"
CHUNK_KEY = f"chunk:sha256:{SHA_B}"
ANCHOR_KEY_A = f"anchor:sha256:{'1' * 64}"
ANCHOR_KEY_B = f"anchor:sha256:{'2' * 64}"
FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "literature"


def _request(**overrides: object) -> LiteratureRetrievalRequest:
    values: dict[str, object] = {
        "request_schema_version": LITERATURE_REQUEST_VERSION,
        "corpus_release_key": CORPUS_KEY,
        "question": "What methods were used in the synthetic documents?",
        "top_k": 8,
    }
    values.update(overrides)
    return LiteratureRetrievalRequest.model_validate(values)


def _locus_anchor(*, anchor_key: str = ANCHOR_KEY_A) -> LocusAnchor:
    return LocusAnchor(
        anchor_type="locus",
        anchor_key=anchor_key,
        locus_key=f"locus:eve:v1:sha256:{'c' * 64}",
    )


def _keyword_anchor(*, anchor_key: str = ANCHOR_KEY_B) -> KeywordAnchor:
    return KeywordAnchor(
        anchor_type="keyword",
        anchor_key=anchor_key,
        phrase="deterministic fixture",
    )


def _plain_locator() -> PlainTextLocator:
    return PlainTextLocator(
        locator_type="plain_text",
        paragraph_ordinal=2,
        line_start=4,
        line_end=5,
        token_start=None,
        token_end=None,
    )


def _chunk(
    *,
    citation_id: str = "D1",
    chunk_key: str = CHUNK_KEY,
    retrieval_tier: str = "corpus_fill",
    matched_anchors: tuple[str, ...] = (),
) -> RetrievedChunk:
    text = "Synthetic retrieval text."
    return RetrievedChunk.model_validate(
        {
            "citation_id": citation_id,
            "chunk_key": chunk_key,
            "document_key": DOCUMENT_KEY,
            "title": "Synthetic document",
            "doi": None,
            "pmid": None,
            "pmcid": None,
            "section": "Methods",
            "locator": _plain_locator(),
            "locator_text": "paragraph 2, lines 4-5",
            "text": text,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "retrieval_tier": retrieval_tier,
            "fts_rank": 2,
            "vector_rank": 1,
            "summary_vector_rank": 1,
            "rrf_score": "0.048915917504",
            "matched_anchors": matched_anchors,
        }
    )


def _result(**overrides: object) -> RetrievedChunks:
    values: dict[str, object] = {
        "result_schema_version": RETRIEVED_CHUNKS_VERSION,
        "status": "ok",
        "corpus_release_key": CORPUS_KEY,
        "corpus_manifest_sha256": SHA_A,
        "retrieval_policy_key": RETRIEVAL_POLICY_KEY,
        "embedding_model_key": EMBEDDING_MODEL_KEY,
        "query_sha256": SHA_B,
        "requested_top_k": 8,
        "returned_count": 1,
        "retrieval_executed": True,
        "anchor_mode": "none",
        "anchors_applied": (),
        "warnings": (),
        "chunks": (_chunk(),),
    }
    values.update(overrides)
    return RetrievedChunks.model_validate(values)


def test_contract_versions_and_policy_keys_are_exact() -> None:
    assert CORPUS_MANIFEST_VERSION == "corpus-manifest-v1"
    assert LITERATURE_REQUEST_VERSION == "literature-retrieval-request-v1"
    assert RETRIEVED_CHUNKS_VERSION == "retrieved-chunks-v2"
    assert LITERATURE_ERROR_VERSION == "literature-retrieval-error-v1"
    assert PARSER_POLICY_KEY == "parser:endoviho-documents-v2"
    assert CHUNKING_POLICY_KEY == "chunking:bge-small-en-v1.5:384-64-448-v2"
    assert FTS_POLICY_KEY == "fts:postgres16:english-weighted-v2"
    assert RETRIEVAL_POLICY_KEY == ("retrieval:postgres16-english-bge-hnsw-summary-rrf60-v2")
    assert ANCHOR_POLICY_KEY == "anchor:endoviho-curated-retrieval-v2"
    assert EMBEDDING_MODEL_KEY.endswith(":cls-l2norm-v1")


def test_request_is_strict_frozen_and_defaults_top_k() -> None:
    request = LiteratureRetrievalRequest(
        request_schema_version=LITERATURE_REQUEST_VERSION,
        corpus_release_key=CORPUS_KEY,
        question="Which synthetic method was described?",
    )

    assert request.top_k == 8
    with pytest.raises(ValidationError):
        LiteratureRetrievalRequest.model_validate({**request.model_dump(), "top_k": "8"})
    with pytest.raises(ValidationError):
        LiteratureRetrievalRequest.model_validate({**request.model_dump(), "anchors": []})
    with pytest.raises(ValidationError):
        request.top_k = 9  # type: ignore[misc]


@pytest.mark.parametrize(
    "corpus_release_key",
    [
        "latest",
        "corpus:endoviho-rag:v0:20990230:001",
        "corpus:endoviho-rag:v1:20990101:001",
        "release:endoviho-rag:v0:20990101:001",
        "corpus:endoviho-rag:v0:20990101:01",
    ],
)
def test_request_requires_exact_valid_v0_corpus_key(corpus_release_key: str) -> None:
    with pytest.raises(ValidationError):
        _request(corpus_release_key=corpus_release_key)


@pytest.mark.parametrize(
    "question",
    ["", "   ", "two\nlines", "control\x00character", "separator\u2028character"],
)
def test_request_rejects_empty_or_non_single_line_question(question: str) -> None:
    with pytest.raises(ValidationError):
        _request(question=question)


@pytest.mark.parametrize("top_k", [0, 21, -1])
def test_request_enforces_top_k_range(top_k: int) -> None:
    with pytest.raises(ValidationError):
        _request(top_k=top_k)


def test_all_six_anchor_types_validate_as_a_discriminated_union() -> None:
    adapter = TypeAdapter(RetrievalAnchor)
    payloads = (
        _locus_anchor().model_dump(),
        AssemblyAnchor(
            anchor_type="assembly",
            anchor_key=ANCHOR_KEY_A,
            assembly_key="assembly:ncbi:GCA_000000001.1",
        ).model_dump(),
        {
            "anchor_type": "lineage",
            "anchor_key": ANCHOR_KEY_A,
            "snapshot_key": "lineage-snapshot:test-v1",
            "term_key": "test-lineage:1",
        },
        {
            "anchor_type": "method",
            "anchor_key": ANCHOR_KEY_A,
            "method_definition_key": "method:deterministic-fixture-v1",
        },
        DocumentAnchor(
            anchor_type="document",
            anchor_key=ANCHOR_KEY_A,
            document_key=DOCUMENT_KEY,
            doi=None,
            pmid=None,
            pmcid=None,
        ).model_dump(),
        _keyword_anchor().model_dump(),
    )

    validated = tuple(adapter.validate_python(payload) for payload in payloads)

    assert tuple(anchor.anchor_type for anchor in validated) == (
        "locus",
        "assembly",
        "lineage",
        "method",
        "document",
        "keyword",
    )


def test_document_anchor_requires_exactly_one_canonical_identifier() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        DocumentAnchor(
            anchor_type="document",
            anchor_key=ANCHOR_KEY_A,
            document_key=None,
            doi=None,
            pmid=None,
            pmcid=None,
        )
    with pytest.raises(ValidationError, match="exactly one"):
        DocumentAnchor(
            anchor_type="document",
            anchor_key=ANCHOR_KEY_A,
            document_key=DOCUMENT_KEY,
            doi="10.1234/synthetic",
            pmid=None,
            pmcid=None,
        )
    with pytest.raises(ValidationError):
        DocumentAnchor(
            anchor_type="document",
            anchor_key=ANCHOR_KEY_A,
            document_key=None,
            doi="HTTPS://DOI.ORG/10.1234/SYNTHETIC",
            pmid=None,
            pmcid=None,
        )


def test_invocation_canonicalizes_and_deduplicates_system_anchors() -> None:
    invocation = LiteratureRetrievalInvocation(
        request=_request(),
        system_anchors=(_keyword_anchor(), _locus_anchor()),
    )

    assert tuple(anchor.anchor_key for anchor in invocation.system_anchors) == (
        ANCHOR_KEY_A,
        ANCHOR_KEY_B,
    )
    with pytest.raises(ValidationError, match="duplicate anchor_key"):
        LiteratureRetrievalInvocation(
            request=_request(),
            system_anchors=(_locus_anchor(), _locus_anchor()),
        )


def test_all_three_locator_types_validate_and_preserve_typed_fields() -> None:
    markdown = MarkdownLocator(
        locator_type="markdown",
        heading_path=("Methods",),
        block_type="paragraph",
        block_ordinal=2,
        line_start=5,
        line_end=7,
        token_start=None,
        token_end=None,
    )
    plain = _plain_locator()
    jats = JatsLocator(
        locator_type="jats_xml",
        section_path=("Methods",),
        element_type="paragraph",
        element_ordinal=1,
        xml_element_path="/article/body/sec[1]/p[1]",
        line_start=None,
        line_end=None,
        token_start=0,
        token_end=42,
    )

    assert markdown.block_ordinal == 2
    assert plain.paragraph_ordinal == 2
    assert jats.xml_element_path.startswith("/article/")


def test_locators_reject_reversed_lines_and_partial_or_empty_token_spans() -> None:
    with pytest.raises(ValidationError):
        PlainTextLocator(
            locator_type="plain_text",
            paragraph_ordinal=1,
            line_start=5,
            line_end=4,
            token_start=None,
            token_end=None,
        )
    with pytest.raises(ValidationError):
        PlainTextLocator(
            locator_type="plain_text",
            paragraph_ordinal=1,
            line_start=1,
            line_end=1,
            token_start=0,
            token_end=None,
        )
    with pytest.raises(ValidationError):
        PlainTextLocator(
            locator_type="plain_text",
            paragraph_ordinal=1,
            line_start=1,
            line_end=1,
            token_start=4,
            token_end=4,
        )


def test_retrieved_chunk_checks_text_hash_rank_and_score_contracts() -> None:
    assert _chunk().citation_id == "D1"
    invalid = _chunk().model_dump()
    invalid["text_sha256"] = SHA_A
    with pytest.raises(ValidationError, match="text_sha256"):
        RetrievedChunk.model_validate(invalid)
    invalid = _chunk().model_dump()
    invalid["fts_rank"] = None
    invalid["vector_rank"] = None
    invalid["summary_vector_rank"] = None
    with pytest.raises(ValidationError, match="component rank"):
        RetrievedChunk.model_validate(invalid)
    invalid = _chunk().model_dump()
    invalid["rrf_score"] = "0.1"
    with pytest.raises(ValidationError):
        RetrievedChunk.model_validate(invalid)


def test_retrieved_chunks_requires_count_contiguous_citations_and_unique_chunks() -> None:
    assert _result().returned_count == 1
    with pytest.raises(ValidationError, match="returned_count"):
        _result(returned_count=2)
    with pytest.raises(ValidationError, match="contiguous"):
        _result(chunks=(_chunk(citation_id="D2"),))
    second = _chunk(citation_id="D2")
    with pytest.raises(ValidationError, match="duplicate chunk_key"):
        _result(returned_count=2, chunks=(_chunk(), second))


def test_no_chunk_success_is_explicit_and_honest() -> None:
    empty = _result(
        returned_count=0,
        chunks=(),
        warnings=("no_chunks_retrieved",),
    )

    assert empty.retrieval_executed is True
    with pytest.raises(ValidationError, match="no_chunks_retrieved"):
        _result(returned_count=0, chunks=(), warnings=())
    with pytest.raises(ValidationError, match="only valid"):
        _result(warnings=("no_chunks_retrieved",))


def test_anchored_result_requires_applied_anchor_and_tier_order() -> None:
    anchor = _locus_anchor()
    anchored = _chunk(retrieval_tier="anchored", matched_anchors=(ANCHOR_KEY_A,))
    fill = _chunk(
        citation_id="D2",
        chunk_key=f"chunk:sha256:{'d' * 64}",
        retrieval_tier="corpus_fill",
    )
    result = _result(
        anchor_mode="anchored_then_corpus_fill",
        anchors_applied=(anchor,),
        returned_count=2,
        chunks=(anchored, fill),
    )

    assert result.chunks[0].retrieval_tier == "anchored"
    with pytest.raises(ValidationError, match="tier order"):
        _result(
            anchor_mode="anchored_then_corpus_fill",
            anchors_applied=(anchor,),
            returned_count=2,
            chunks=(
                fill.model_copy(update={"citation_id": "D1"}),
                anchored.model_copy(update={"citation_id": "D2"}),
            ),
        )
    with pytest.raises(ValidationError, match="unknown matched anchor"):
        _result(
            anchor_mode="anchored_then_corpus_fill",
            anchors_applied=(anchor,),
            chunks=(_chunk(retrieval_tier="anchored", matched_anchors=(ANCHOR_KEY_B,)),),
        )


def test_result_enforces_full_deterministic_order_within_one_tier() -> None:
    first = _chunk().model_copy(update={"rrf_score": "0.020000000000"})
    second = _chunk(
        citation_id="D2",
        chunk_key=f"chunk:sha256:{'d' * 64}",
    ).model_copy(update={"rrf_score": "0.030000000000"})

    with pytest.raises(ValidationError, match="final retrieval order"):
        _result(returned_count=2, chunks=(first, second))


def test_anchor_miss_warning_exactly_records_an_empty_anchored_tier() -> None:
    anchor = _locus_anchor()
    fill = _chunk()

    result = _result(
        anchor_mode="anchored_then_corpus_fill",
        anchors_applied=(anchor,),
        warnings=("anchor_miss",),
        chunks=(fill,),
    )
    assert result.warnings == ("anchor_miss",)

    with pytest.raises(ValidationError, match="anchor_miss"):
        _result(
            anchor_mode="anchored_then_corpus_fill",
            anchors_applied=(anchor,),
            chunks=(fill,),
        )
    with pytest.raises(ValidationError, match="anchor_miss"):
        _result(warnings=("anchor_miss",))


def test_unanchored_result_rejects_anchor_state_and_anchored_tier() -> None:
    with pytest.raises(ValidationError, match="anchor_mode none"):
        _result(anchors_applied=(_locus_anchor(),))
    with pytest.raises(ValidationError, match="anchor_mode none"):
        _result(chunks=(_chunk(retrieval_tier="anchored"),))


def test_error_envelope_is_strict_typed_and_has_no_partial_chunks() -> None:
    error = LiteratureRetrievalError(
        error_schema_version=LITERATURE_ERROR_VERSION,
        status="error",
        code="corpus_not_published",
        message="The exact corpus is not published.",
        requested_corpus_release_key=CORPUS_KEY,
        retrieval_executed=False,
    )

    assert error.code == "corpus_not_published"
    with pytest.raises(ValidationError):
        LiteratureRetrievalError.model_validate({**error.model_dump(), "chunks": []})
    with pytest.raises(ValidationError):
        LiteratureRetrievalError.model_validate({**error.model_dump(), "code": "unknown"})


def test_synthetic_manifest_and_source_fixtures_are_checksum_bound() -> None:
    manifest = CorpusManifest.model_validate_json(
        (FIXTURE_ROOT / "synthetic_corpus_manifest.json").read_text()
    )

    assert manifest.document_count == 3
    assert manifest.manifest_sha256 == canonical_manifest_sha256(manifest)
    assert {document.document_format for document in manifest.documents} == {
        "markdown",
        "plain_text",
        "jats_xml",
    }
    for document in manifest.documents:
        source = FIXTURE_ROOT / document.relative_path
        payload = source.read_bytes()
        assert len(payload) == document.byte_size
        assert hashlib.sha256(payload).hexdigest() == document.source_sha256
        preimage = DocumentKeyPreimage(
            key_schema_version="document-key-v1",
            source_artifact_sha256=document.source_sha256,
            media_type=document.media_type,
            document_version=document.document_version,
            doi=document.doi,
            pmid=document.pmid,
            pmcid=document.pmcid,
            canonical_title=document.title,
        )
        assert document_key(preimage) == document.expected_document_key


def test_manifest_rejects_traversal_media_mismatch_and_inconsistent_counts() -> None:
    raw = json.loads((FIXTURE_ROOT / "synthetic_corpus_manifest.json").read_text())
    raw["documents"][0]["relative_path"] = "../synthetic_article.md"
    with pytest.raises(ValidationError):
        CorpusManifest.model_validate_json(json.dumps(raw))

    raw = json.loads((FIXTURE_ROOT / "synthetic_corpus_manifest.json").read_text())
    raw["documents"][0]["media_type"] = "text/plain"
    with pytest.raises(ValidationError, match="media_type"):
        CorpusManifest.model_validate_json(json.dumps(raw))

    raw = json.loads((FIXTURE_ROOT / "synthetic_corpus_manifest.json").read_text())
    raw["document_count"] = 4
    with pytest.raises(ValidationError, match="document_count"):
        CorpusManifest.model_validate_json(json.dumps(raw))

    raw = json.loads((FIXTURE_ROOT / "synthetic_corpus_manifest.json").read_text())
    raw["documents"][0]["relative_path"] = "synthetic_article.txt"
    with pytest.raises(ValidationError, match="relative_path"):
        CorpusManifest.model_validate_json(json.dumps(raw))


def test_manifest_rejects_a_mismatched_canonical_payload_hash() -> None:
    raw = json.loads((FIXTURE_ROOT / "synthetic_corpus_manifest.json").read_text())
    raw["manifest_sha256"] = SHA_A

    with pytest.raises(ValidationError, match="manifest_sha256"):
        CorpusManifest.model_validate_json(json.dumps(raw))


def test_manifest_document_rejects_unapproved_text_return_and_duplicate_identity() -> None:
    raw = json.loads((FIXTURE_ROOT / "synthetic_corpus_manifest.json").read_text())
    document = raw["documents"][0]
    document["license_review_status"] = "pending"
    with pytest.raises(ValidationError, match="retrieval_text_allowed"):
        CorpusDocumentSpec.model_validate_json(json.dumps(document))

    raw = json.loads((FIXTURE_ROOT / "synthetic_corpus_manifest.json").read_text())
    document = raw["documents"][0]
    document["expected_document_key"] = f"document:sha256:{'e' * 64}"
    with pytest.raises(ValidationError, match="expected_document_key"):
        CorpusDocumentSpec.model_validate_json(json.dumps(document))

    raw = json.loads((FIXTURE_ROOT / "synthetic_corpus_manifest.json").read_text())
    raw["documents"][1]["expected_document_key"] = raw["documents"][0]["expected_document_key"]
    with pytest.raises(ValidationError, match="expected_document_key"):
        CorpusManifest.model_validate_json(json.dumps(raw))


def test_chunk_key_preimage_rejects_a_mismatched_normalized_text_hash() -> None:
    with pytest.raises(ValidationError, match="normalized_text_sha256"):
        ChunkKeyPreimage(
            key_schema_version="chunk-key-v1",
            corpus_release_key=CORPUS_KEY,
            document_key=DOCUMENT_KEY,
            parser_policy_key=PARSER_POLICY_KEY,
            chunking_policy_key=CHUNKING_POLICY_KEY,
            section_path=("Methods",),
            locator=_plain_locator(),
            chunk_index=0,
            normalized_chunk_text="synthetic",
            normalized_text_sha256=SHA_A,
        )
