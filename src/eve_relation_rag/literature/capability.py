"""Unforgeable-by-request capability for one exact published corpus."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol


class CorpusCapability(Protocol):
    """Structural boundary accepted by later literature repositories."""

    @property
    def release_id(self) -> int: ...

    @property
    def corpus_release_key(self) -> str: ...

    @property
    def status(self) -> Literal["published", "validation_candidate"]: ...

    @property
    def published_at(self) -> datetime: ...

    @property
    def manifest_sha256(self) -> str: ...

    @property
    def policy_graph_sha256(self) -> str: ...

    @property
    def validation_receipt_key(self) -> str: ...

    @property
    def validation_receipt_sha256(self) -> str: ...

    @property
    def parser_policy_id(self) -> int: ...

    @property
    def parser_policy_key(self) -> str: ...

    @property
    def chunking_policy_id(self) -> int: ...

    @property
    def chunking_policy_key(self) -> str: ...

    @property
    def fts_policy_id(self) -> int: ...

    @property
    def fts_policy_key(self) -> str: ...

    @property
    def retrieval_policy_id(self) -> int: ...

    @property
    def retrieval_policy_key(self) -> str: ...

    @property
    def anchor_policy_id(self) -> int: ...

    @property
    def anchor_policy_key(self) -> str: ...

    @property
    def embedding_model_id(self) -> int: ...

    @property
    def embedding_model_key(self) -> str: ...

    @property
    def embedding_dimension(self) -> int: ...

    @property
    def model_artifact_manifest_sha256(self) -> str: ...


_GATE_ISSUER = object()


@dataclass(frozen=True, slots=True)
class _QueryableCorpus:
    """Production capability issued only after complete gate verification."""

    release_id: int
    corpus_release_key: str
    status: Literal["published", "validation_candidate"]
    published_at: datetime
    manifest_sha256: str
    policy_graph_sha256: str
    validation_receipt_key: str
    validation_receipt_sha256: str
    parser_policy_id: int
    parser_policy_key: str
    chunking_policy_id: int
    chunking_policy_key: str
    fts_policy_id: int
    fts_policy_key: str
    retrieval_policy_id: int
    retrieval_policy_key: str
    anchor_policy_id: int
    anchor_policy_key: str
    embedding_model_id: int
    embedding_model_key: str
    embedding_dimension: int
    model_artifact_manifest_sha256: str
    _issuer: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _GATE_ISSUER:
            raise TypeError("QueryableCorpus may only be issued by PublishedCorpusGate")


def _issue_queryable_corpus(
    *,
    release_id: int,
    corpus_release_key: str,
    status: Literal["published", "validation_candidate"],
    published_at: datetime,
    manifest_sha256: str,
    policy_graph_sha256: str,
    validation_receipt_key: str,
    validation_receipt_sha256: str,
    parser_policy_id: int,
    parser_policy_key: str,
    chunking_policy_id: int,
    chunking_policy_key: str,
    fts_policy_id: int,
    fts_policy_key: str,
    retrieval_policy_id: int,
    retrieval_policy_key: str,
    anchor_policy_id: int,
    anchor_policy_key: str,
    embedding_model_id: int,
    embedding_model_key: str,
    embedding_dimension: int,
    model_artifact_manifest_sha256: str,
) -> _QueryableCorpus:
    return _QueryableCorpus(
        release_id=release_id,
        corpus_release_key=corpus_release_key,
        status=status,
        published_at=published_at,
        manifest_sha256=manifest_sha256,
        policy_graph_sha256=policy_graph_sha256,
        validation_receipt_key=validation_receipt_key,
        validation_receipt_sha256=validation_receipt_sha256,
        parser_policy_id=parser_policy_id,
        parser_policy_key=parser_policy_key,
        chunking_policy_id=chunking_policy_id,
        chunking_policy_key=chunking_policy_key,
        fts_policy_id=fts_policy_id,
        fts_policy_key=fts_policy_key,
        retrieval_policy_id=retrieval_policy_id,
        retrieval_policy_key=retrieval_policy_key,
        anchor_policy_id=anchor_policy_id,
        anchor_policy_key=anchor_policy_key,
        embedding_model_id=embedding_model_id,
        embedding_model_key=embedding_model_key,
        embedding_dimension=embedding_dimension,
        model_artifact_manifest_sha256=model_artifact_manifest_sha256,
        _issuer=_GATE_ISSUER,
    )
