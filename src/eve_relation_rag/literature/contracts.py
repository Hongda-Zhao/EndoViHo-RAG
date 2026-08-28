"""Strict, immutable Milestone 3 literature schemas.

These models establish syntax and integrity boundaries only. A syntactically valid corpus
key is not thereby published, a manifest is not thereby approved, and a literature anchor
is never structured scientific truth. Database capability gates own execution authority.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Annotated, Final, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from eve_relation_rag.domain.keys import is_versioned_assembly_accession

CORPUS_MANIFEST_VERSION: Final = "corpus-manifest-v1"
LITERATURE_REQUEST_VERSION: Final = "literature-retrieval-request-v1"
RETRIEVED_CHUNKS_VERSION: Final = "retrieved-chunks-v2"
LITERATURE_ERROR_VERSION: Final = "literature-retrieval-error-v1"

PARSER_POLICY_KEY: Final = "parser:endoviho-documents-v2"
CHUNKING_POLICY_KEY: Final = "chunking:bge-small-en-v1.5:384-64-448-v2"
EMBEDDING_REPOSITORY_ID: Final = "BAAI/bge-small-en-v1.5"
EMBEDDING_REVISION: Final = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
EMBEDDING_QUERY_PREFIX: Final = "Represent this sentence for searching relevant passages: "
EMBEDDING_MODEL_KEY: Final = (
    "embedding:hf:BAAI-bge-small-en-v1.5@5c38ec7c405ec4b44b94cc5a9bb96e735b38267a:cls-l2norm-v1"
)
FTS_POLICY_KEY: Final = "fts:postgres16:english-weighted-v2"
RETRIEVAL_POLICY_KEY: Final = "retrieval:postgres16-english-bge-hnsw-summary-rrf60-v2"
ANCHOR_POLICY_KEY: Final = "anchor:endoviho-curated-retrieval-v2"

_CORPUS_KEY_RE: Final = re.compile(r"^corpus:endoviho-rag:v0:(?P<date>[0-9]{8}):[0-9]{3}$")
_DOCUMENT_KEY_RE: Final = re.compile(r"^document:sha256:[0-9a-f]{64}$")
_CHUNK_KEY_RE: Final = re.compile(r"^chunk:sha256:[0-9a-f]{64}$")
_ANCHOR_KEY_RE: Final = re.compile(r"^anchor:sha256:[0-9a-f]{64}$")
_LOCUS_KEY_RE: Final = re.compile(r"^locus:eve:v1:sha256:[0-9a-f]{64}$")
_DOI_RE: Final = re.compile(r"^10\.[0-9]{4,9}/\S+$")
_PMID_RE: Final = re.compile(r"^[1-9][0-9]*$")
_PMCID_RE: Final = re.compile(r"^PMC[1-9][0-9]*$")


class StrictFrozenSchema(BaseModel):
    """Immutable strict base that rejects every unknown field."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _validate_non_empty_text(value: str) -> str:
    if not value.strip():
        raise ValueError("text must contain non-whitespace characters")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValueError("text must not contain control or format characters")
    return value


def _validate_nfc_text(value: str) -> str:
    _validate_non_empty_text(value)
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError("canonical text must already use Unicode NFC")
    return value


def _validate_stable_token(value: str) -> str:
    if any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in value
    ):
        raise ValueError("stable tokens must not contain whitespace or control characters")
    return value


def _validate_question(value: str) -> str:
    if not value.strip():
        raise ValueError("question must contain non-whitespace text")
    if any(
        unicodedata.category(character).startswith("C") or character in {"\u2028", "\u2029"}
        for character in value
    ):
        raise ValueError("question must be one line without control characters")
    return value


def _validate_corpus_release_key(value: str) -> str:
    match = _CORPUS_KEY_RE.fullmatch(value)
    if match is None:
        raise ValueError("corpus_release_key must match corpus:endoviho-rag:v0:YYYYMMDD:NNN")
    token = match.group("date")
    try:
        date.fromisoformat(f"{token[:4]}-{token[4:6]}-{token[6:]}")
    except ValueError as error:
        raise ValueError("corpus_release_key contains an invalid calendar date") from error
    return value


def _validate_document_key(value: str) -> str:
    if _DOCUMENT_KEY_RE.fullmatch(value) is None:
        raise ValueError("document_key must match document:sha256:<64 lowercase hex>")
    return value


def _validate_chunk_key(value: str) -> str:
    if _CHUNK_KEY_RE.fullmatch(value) is None:
        raise ValueError("chunk_key must match chunk:sha256:<64 lowercase hex>")
    return value


def _validate_anchor_key(value: str) -> str:
    if _ANCHOR_KEY_RE.fullmatch(value) is None:
        raise ValueError("anchor_key must match anchor:sha256:<64 lowercase hex>")
    return value


def _validate_locus_key(value: str) -> str:
    if _LOCUS_KEY_RE.fullmatch(value) is None:
        raise ValueError("locus_key must match locus:eve:v1:sha256:<64 lowercase hex>")
    return value


def _validate_assembly_key(value: str) -> str:
    prefix = "assembly:ncbi:"
    if not value.startswith(prefix) or not is_versioned_assembly_accession(
        value.removeprefix(prefix)
    ):
        raise ValueError("assembly_key must contain an exact versioned GCA_/GCF_ accession")
    return value


def _validate_doi(value: str) -> str:
    if value != value.strip().lower() or _DOI_RE.fullmatch(value) is None:
        raise ValueError("doi must be a canonical lowercase bare DOI")
    return value


def _validate_pmid(value: str) -> str:
    if _PMID_RE.fullmatch(value) is None:
        raise ValueError("pmid must contain canonical decimal digits")
    return value


def _validate_pmcid(value: str) -> str:
    if _PMCID_RE.fullmatch(value) is None:
        raise ValueError("pmcid must match PMC followed by canonical decimal digits")
    return value


type Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
type CorpusReleaseKey = Annotated[
    str,
    Field(min_length=1, max_length=255),
    AfterValidator(_validate_corpus_release_key),
]
type DocumentKey = Annotated[
    str,
    Field(min_length=1, max_length=255),
    AfterValidator(_validate_document_key),
]
type ChunkKey = Annotated[
    str,
    Field(min_length=1, max_length=255),
    AfterValidator(_validate_chunk_key),
]
type AnchorKey = Annotated[
    str,
    Field(min_length=1, max_length=255),
    AfterValidator(_validate_anchor_key),
]
type LocusKey = Annotated[
    str,
    Field(min_length=1, max_length=255),
    AfterValidator(_validate_locus_key),
]
type AssemblyKey = Annotated[
    str,
    Field(min_length=1, max_length=255),
    AfterValidator(_validate_assembly_key),
]
type StableToken = Annotated[
    str,
    Field(min_length=1, max_length=255),
    AfterValidator(_validate_stable_token),
]
type NonEmptyText = Annotated[str, Field(min_length=1), AfterValidator(_validate_non_empty_text)]
type CanonicalText = Annotated[str, Field(min_length=1), AfterValidator(_validate_nfc_text)]
type QuestionText = Annotated[
    str,
    Field(min_length=1, max_length=2000),
    AfterValidator(_validate_question),
]
type Doi = Annotated[str, Field(min_length=7, max_length=255), AfterValidator(_validate_doi)]
type Pmid = Annotated[str, Field(max_length=32), AfterValidator(_validate_pmid)]
type Pmcid = Annotated[str, Field(max_length=35), AfterValidator(_validate_pmcid)]
type Rfc3339Utc = Annotated[
    str,
    Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"),
]


class LiteratureRetrievalRequest(StrictFrozenSchema):
    """Client-safe internal request with no client-authored anchors."""

    request_schema_version: Literal["literature-retrieval-request-v1"]
    corpus_release_key: CorpusReleaseKey
    question: QuestionText
    top_k: int = Field(default=8, ge=1, le=20)


class AnchorBase(StrictFrozenSchema):
    """Fields common to exact, curated, corpus-scoped retrieval anchors."""

    anchor_key: AnchorKey


class LocusAnchor(AnchorBase):
    anchor_type: Literal["locus"]
    locus_key: LocusKey


class AssemblyAnchor(AnchorBase):
    anchor_type: Literal["assembly"]
    assembly_key: AssemblyKey


class LineageAnchor(AnchorBase):
    anchor_type: Literal["lineage"]
    snapshot_key: StableToken
    term_key: StableToken


class MethodAnchor(AnchorBase):
    anchor_type: Literal["method"]
    method_definition_key: StableToken


class DocumentAnchor(AnchorBase):
    anchor_type: Literal["document"]
    document_key: DocumentKey | None
    doi: Doi | None
    pmid: Pmid | None
    pmcid: Pmcid | None

    @model_validator(mode="after")
    def validate_one_identifier(self) -> Self:
        populated = sum(
            value is not None for value in (self.document_key, self.doi, self.pmid, self.pmcid)
        )
        if populated != 1:
            raise ValueError("document anchor requires exactly one canonical identifier")
        return self


class KeywordAnchor(AnchorBase):
    anchor_type: Literal["keyword"]
    phrase: Annotated[CanonicalText, Field(max_length=255)]


type RetrievalAnchor = Annotated[
    LocusAnchor | AssemblyAnchor | LineageAnchor | MethodAnchor | DocumentAnchor | KeywordAnchor,
    Field(discriminator="anchor_type"),
]


class LiteratureRetrievalInvocation(StrictFrozenSchema):
    """Request plus trusted system anchors, kept outside the public request model."""

    request: LiteratureRetrievalRequest
    system_anchors: tuple[RetrievalAnchor, ...] = Field(default=(), max_length=64)

    @field_validator("system_anchors")
    @classmethod
    def canonicalize_anchors(
        cls, anchors: tuple[RetrievalAnchor, ...]
    ) -> tuple[RetrievalAnchor, ...]:
        keys = tuple(anchor.anchor_key for anchor in anchors)
        if len(keys) != len(set(keys)):
            raise ValueError("system_anchors contains a duplicate anchor_key")
        return tuple(sorted(anchors, key=lambda anchor: anchor.anchor_key))


type BlockType = Literal[
    "title",
    "abstract",
    "paragraph",
    "list_item",
    "table",
    "table_caption",
    "figure_caption",
    "reference",
    "supplementary",
]


class TokenSpanMixin(StrictFrozenSchema):
    """Optional zero-based half-open token span for a split logical block."""

    token_start: int | None = Field(default=None, ge=0)
    token_end: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_token_span(self) -> Self:
        if (self.token_start is None) != (self.token_end is None):
            raise ValueError("token_start and token_end must be supplied together")
        if (
            self.token_start is not None
            and self.token_end is not None
            and self.token_start >= self.token_end
        ):
            raise ValueError("token span must be non-empty and half-open")
        return self


class MarkdownLocator(TokenSpanMixin):
    locator_type: Literal["markdown"]
    heading_path: tuple[NonEmptyText, ...] = Field(max_length=32)
    block_type: BlockType
    block_ordinal: int = Field(ge=1)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_line_range(self) -> Self:
        if self.line_start > self.line_end:
            raise ValueError("line_start must not exceed line_end")
        return self


class PlainTextLocator(TokenSpanMixin):
    locator_type: Literal["plain_text"]
    paragraph_ordinal: int = Field(ge=1)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_line_range(self) -> Self:
        if self.line_start > self.line_end:
            raise ValueError("line_start must not exceed line_end")
        return self


class JatsLocator(TokenSpanMixin):
    locator_type: Literal["jats_xml"]
    section_path: tuple[NonEmptyText, ...] = Field(max_length=32)
    element_type: BlockType
    element_ordinal: int = Field(ge=1)
    xml_element_path: Annotated[NonEmptyText, Field(max_length=2000)]
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_source_location(self) -> Self:
        if not self.xml_element_path.startswith("/article/"):
            raise ValueError("xml_element_path must be an absolute path below /article")
        if (self.line_start is None) != (self.line_end is None):
            raise ValueError("JATS line_start and line_end must be supplied together")
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_start > self.line_end
        ):
            raise ValueError("line_start must not exceed line_end")
        return self


type CanonicalLocator = Annotated[
    MarkdownLocator | PlainTextLocator | JatsLocator,
    Field(discriminator="locator_type"),
]


class DocumentKeyPreimage(StrictFrozenSchema):
    """Approved immutable document identity preimage."""

    key_schema_version: Literal["document-key-v1"]
    source_artifact_sha256: Sha256
    media_type: Literal["text/markdown", "text/plain", "application/xml"]
    document_version: NonEmptyText
    doi: Doi | None
    pmid: Pmid | None
    pmcid: Pmcid | None
    canonical_title: CanonicalText


class ChunkKeyPreimage(StrictFrozenSchema):
    """Approved corpus-scoped deterministic chunk identity preimage."""

    key_schema_version: Literal["chunk-key-v1"]
    corpus_release_key: CorpusReleaseKey
    document_key: DocumentKey
    parser_policy_key: Literal["parser:endoviho-documents-v2"]
    chunking_policy_key: Literal["chunking:bge-small-en-v1.5:384-64-448-v2"]
    section_path: tuple[NonEmptyText, ...] = Field(max_length=32)
    locator: CanonicalLocator
    chunk_index: int = Field(ge=0)
    normalized_chunk_text: CanonicalText
    normalized_text_sha256: Sha256

    @model_validator(mode="after")
    def validate_normalized_text_hash(self) -> Self:
        observed = hashlib.sha256(self.normalized_chunk_text.encode("utf-8")).hexdigest()
        if observed != self.normalized_text_sha256:
            raise ValueError("normalized_text_sha256 does not match normalized_chunk_text")
        return self


type RetrievalTier = Literal["anchored", "corpus_fill"]
type RetrievalWarning = Literal[
    "anchor_miss",
    "fts_no_indexable_terms",
    "no_chunks_retrieved",
]
type PositiveRank = Annotated[int, Field(ge=1)]
type CitationId = Annotated[str, Field(pattern=r"^D[1-9][0-9]*$")]
type RrfScore = Annotated[str, Field(pattern=r"^(?:0|[1-9][0-9]*)\.[0-9]{12}$")]


class RetrievedChunk(StrictFrozenSchema):
    """One quoted, checksum-bound literature chunk in final response order."""

    citation_id: CitationId
    chunk_key: ChunkKey
    document_key: DocumentKey
    title: NonEmptyText
    doi: Doi | None
    pmid: Pmid | None
    pmcid: Pmcid | None
    section: NonEmptyText | None
    locator: CanonicalLocator
    locator_text: NonEmptyText
    text: CanonicalText
    text_sha256: Sha256
    retrieval_tier: RetrievalTier
    fts_rank: PositiveRank | None
    vector_rank: PositiveRank | None
    summary_vector_rank: PositiveRank | None
    rrf_score: RrfScore
    matched_anchors: tuple[AnchorKey, ...] = ()

    @field_validator("matched_anchors")
    @classmethod
    def validate_matched_anchor_order(cls, keys: tuple[str, ...]) -> tuple[str, ...]:
        if len(keys) != len(set(keys)):
            raise ValueError("matched_anchors must not contain duplicates")
        return tuple(sorted(keys))

    @model_validator(mode="after")
    def validate_chunk_integrity(self) -> Self:
        observed = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if observed != self.text_sha256:
            raise ValueError("text_sha256 does not match text")
        if self.fts_rank is None and self.vector_rank is None and self.summary_vector_rank is None:
            raise ValueError("at least one component rank is required")
        return self


class RetrievedChunks(StrictFrozenSchema):
    """Successful, typed literature retrieval result."""

    result_schema_version: Literal["retrieved-chunks-v2"]
    status: Literal["ok"]
    corpus_release_key: CorpusReleaseKey
    corpus_manifest_sha256: Sha256
    retrieval_policy_key: Literal["retrieval:postgres16-english-bge-hnsw-summary-rrf60-v2"]
    embedding_model_key: Literal[
        "embedding:hf:BAAI-bge-small-en-v1.5@5c38ec7c405ec4b44b94cc5a9bb96e735b38267a:cls-l2norm-v1"
    ]
    query_sha256: Sha256
    requested_top_k: int = Field(ge=1, le=20)
    returned_count: int = Field(ge=0, le=20)
    retrieval_executed: Literal[True]
    anchor_mode: Literal["none", "anchored_then_corpus_fill"]
    anchors_applied: tuple[RetrievalAnchor, ...] = Field(max_length=64)
    warnings: tuple[RetrievalWarning, ...]
    chunks: tuple[RetrievedChunk, ...] = Field(max_length=20)

    @field_validator("anchors_applied")
    @classmethod
    def canonicalize_applied_anchors(
        cls, anchors: tuple[RetrievalAnchor, ...]
    ) -> tuple[RetrievalAnchor, ...]:
        keys = tuple(anchor.anchor_key for anchor in anchors)
        if len(keys) != len(set(keys)):
            raise ValueError("anchors_applied contains a duplicate anchor_key")
        return tuple(sorted(anchors, key=lambda anchor: anchor.anchor_key))

    @field_validator("warnings")
    @classmethod
    def validate_warning_uniqueness(
        cls, warnings: tuple[RetrievalWarning, ...]
    ) -> tuple[RetrievalWarning, ...]:
        if len(warnings) != len(set(warnings)):
            raise ValueError("warnings must not contain duplicates")
        return warnings

    @model_validator(mode="after")
    def validate_result_integrity(self) -> Self:
        if self.returned_count != len(self.chunks):
            raise ValueError("returned_count must equal the number of chunks")
        if self.returned_count > self.requested_top_k:
            raise ValueError("returned_count must not exceed requested_top_k")

        expected_citations = tuple(f"D{index}" for index in range(1, len(self.chunks) + 1))
        observed_citations = tuple(chunk.citation_id for chunk in self.chunks)
        if observed_citations != expected_citations:
            raise ValueError("citation IDs must be contiguous D1..Dn in response order")

        chunk_keys = tuple(chunk.chunk_key for chunk in self.chunks)
        if len(chunk_keys) != len(set(chunk_keys)):
            raise ValueError("chunks contains a duplicate chunk_key")

        no_chunks_warning = "no_chunks_retrieved" in self.warnings
        if not self.chunks and not no_chunks_warning:
            raise ValueError("an empty success requires the no_chunks_retrieved warning")
        if self.chunks and no_chunks_warning:
            raise ValueError("no_chunks_retrieved is only valid for an empty result")

        applied_keys = {anchor.anchor_key for anchor in self.anchors_applied}
        if self.anchor_mode == "none":
            if self.anchors_applied or any(
                chunk.retrieval_tier == "anchored" or chunk.matched_anchors for chunk in self.chunks
            ):
                raise ValueError("anchor_mode none forbids anchors and anchored chunk state")
        elif not self.anchors_applied:
            raise ValueError("anchored_then_corpus_fill requires at least one applied anchor")

        seen_fill = False
        for chunk in self.chunks:
            if not set(chunk.matched_anchors).issubset(applied_keys):
                raise ValueError("chunk contains an unknown matched anchor")
            if chunk.retrieval_tier == "corpus_fill":
                seen_fill = True
            elif seen_fill:
                raise ValueError("retrieval tier order must place anchored before corpus_fill")
            elif not chunk.matched_anchors:
                raise ValueError("an anchored chunk must record at least one matched anchor")

        has_anchored_chunk = any(chunk.retrieval_tier == "anchored" for chunk in self.chunks)
        has_anchor_miss = "anchor_miss" in self.warnings
        if self.anchor_mode == "none" and has_anchor_miss:
            raise ValueError("anchor_miss requires anchored_then_corpus_fill mode")
        if self.anchor_mode == "anchored_then_corpus_fill":
            if has_anchored_chunk == has_anchor_miss:
                raise ValueError(
                    "anchor_miss must be present exactly when the anchored tier is empty"
                )

        def final_order_key(chunk: RetrievedChunk) -> tuple[int, Decimal, int, int, str]:
            ranks = tuple(
                rank
                for rank in (
                    chunk.fts_rank,
                    chunk.vector_rank,
                    chunk.summary_vector_rank,
                )
                if rank is not None
            )
            return (
                0 if chunk.retrieval_tier == "anchored" else 1,
                -Decimal(chunk.rrf_score),
                -len(ranks),
                min(ranks),
                chunk.chunk_key,
            )

        if self.chunks != tuple(sorted(self.chunks, key=final_order_key)):
            raise ValueError("chunks do not follow the approved final retrieval order")
        return self


type LiteratureErrorCode = Literal[
    "corpus_not_found",
    "corpus_not_published",
    "corpus_manifest_invalid",
    "corpus_receipt_invalid",
    "corpus_incomplete",
    "document_license_not_approved",
    "chunk_locator_invalid",
    "embedding_incomplete",
    "embedding_model_mismatch",
    "embedding_provider_failed",
    "query_too_long",
    "anchor_invalid",
    "retrieval_failed",
    "unsupported_request",
]


class LiteratureRetrievalError(StrictFrozenSchema):
    """Fail-closed literature error envelope with no partial chunk field."""

    error_schema_version: Literal["literature-retrieval-error-v1"]
    status: Literal["error"]
    code: LiteratureErrorCode
    message: NonEmptyText
    requested_corpus_release_key: NonEmptyText | None
    retrieval_executed: bool


type DocumentFormat = Literal["markdown", "plain_text", "jats_xml"]
type MediaType = Literal["text/markdown", "text/plain", "application/xml"]
type LicenseReviewStatus = Literal["approved", "pending", "rejected", "unknown", "incompatible"]


def _validate_relative_path(value: str) -> str:
    if "\\" in value:
        raise ValueError("relative_path must use POSIX separators")
    path = PurePosixPath(value)
    if path.is_absolute() or value in {"", "."} or ".." in path.parts:
        raise ValueError("relative_path must remain below the approved import root")
    if any(part in {"", "."} for part in path.parts):
        raise ValueError("relative_path must be canonical")
    return value


type RelativePath = Annotated[
    str,
    Field(min_length=1, max_length=1000),
    AfterValidator(_validate_relative_path),
]


class CorpusDocumentSpec(StrictFrozenSchema):
    """One checksum-, metadata-, and license-bound manifest document."""

    manifest_row: int = Field(ge=1)
    relative_path: RelativePath
    byte_size: int = Field(gt=0, le=52_428_800)
    source_sha256: Sha256
    document_format: DocumentFormat
    media_type: MediaType
    source_uri: NonEmptyText
    retrieved_at: Rfc3339Utc
    title: CanonicalText
    authors: tuple[NonEmptyText, ...] = Field(min_length=1)
    document_version: NonEmptyText
    doi: Doi | None
    pmid: Pmid | None
    pmcid: Pmcid | None
    declared_license: NonEmptyText
    license_evidence_uri: NonEmptyText
    license_review_status: LicenseReviewStatus
    retrieval_text_allowed: bool
    expected_document_key: DocumentKey

    @model_validator(mode="after")
    def validate_format_and_license(self) -> Self:
        expected_media: dict[DocumentFormat, MediaType] = {
            "markdown": "text/markdown",
            "plain_text": "text/plain",
            "jats_xml": "application/xml",
        }
        if self.media_type != expected_media[self.document_format]:
            raise ValueError("media_type does not match document_format")
        expected_suffix: dict[DocumentFormat, str] = {
            "markdown": ".md",
            "plain_text": ".txt",
            "jats_xml": ".xml",
        }
        if PurePosixPath(self.relative_path).suffix != expected_suffix[self.document_format]:
            raise ValueError("relative_path extension does not match document_format")
        if self.retrieval_text_allowed and self.license_review_status != "approved":
            raise ValueError("retrieval_text_allowed requires an approved license_review_status")

        from eve_relation_rag.literature.hashing import document_key

        preimage = DocumentKeyPreimage(
            key_schema_version="document-key-v1",
            source_artifact_sha256=self.source_sha256,
            media_type=self.media_type,
            document_version=self.document_version,
            doi=self.doi,
            pmid=self.pmid,
            pmcid=self.pmcid,
            canonical_title=self.title,
        )
        if document_key(preimage) != self.expected_document_key:
            raise ValueError("expected_document_key does not match canonical document identity")
        return self


class CorpusManifest(StrictFrozenSchema):
    """Strict mechanism schema; validation does not constitute corpus approval."""

    manifest_schema_version: Literal["corpus-manifest-v1"]
    corpus_release_key: CorpusReleaseKey
    release_title: CanonicalText
    purpose: NonEmptyText
    document_count: int = Field(ge=1)
    expected_chunk_count_min: int = Field(ge=1)
    expected_chunk_count_max: int = Field(ge=1)
    parser_policy_key: Literal["parser:endoviho-documents-v2"]
    chunking_policy_key: Literal["chunking:bge-small-en-v1.5:384-64-448-v2"]
    embedding_model_key: Literal[
        "embedding:hf:BAAI-bge-small-en-v1.5@5c38ec7c405ec4b44b94cc5a9bb96e735b38267a:cls-l2norm-v1"
    ]
    fts_policy_key: Literal["fts:postgres16:english-weighted-v2"]
    retrieval_policy_key: Literal["retrieval:postgres16-english-bge-hnsw-summary-rrf60-v2"]
    anchor_policy_key: Literal["anchor:endoviho-curated-retrieval-v2"]
    manifest_sha256: Sha256
    documents: tuple[CorpusDocumentSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_manifest_counts_and_uniqueness(self) -> Self:
        if self.document_count != len(self.documents):
            raise ValueError("document_count must equal the number of documents")
        if self.expected_chunk_count_min > self.expected_chunk_count_max:
            raise ValueError("expected chunk-count range is reversed")

        rows = tuple(document.manifest_row for document in self.documents)
        if rows != tuple(range(1, len(self.documents) + 1)):
            raise ValueError("manifest_row values must be contiguous and in canonical order")
        uniqueness_fields = {
            "relative_path": tuple(document.relative_path for document in self.documents),
            "source_sha256": tuple(document.source_sha256 for document in self.documents),
            "expected_document_key": tuple(
                document.expected_document_key for document in self.documents
            ),
        }
        for field_name, values in uniqueness_fields.items():
            if len(values) != len(set(values)):
                raise ValueError(f"documents contains a duplicate {field_name}")

        from eve_relation_rag.literature.hashing import canonical_manifest_sha256

        if canonical_manifest_sha256(self) != self.manifest_sha256:
            raise ValueError("manifest_sha256 does not match the canonical manifest payload")
        return self
