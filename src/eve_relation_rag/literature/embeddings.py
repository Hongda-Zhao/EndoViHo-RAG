"""Embedding validation, canonical checksums, and atomic candidate persistence."""

from __future__ import annotations

import hashlib
import math
import re
import struct
from collections.abc import Sequence
from typing import Literal, cast

from pydantic import Field
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from eve_relation_rag.db.models import (
    CorpusRelease,
    DocumentChunk,
    DocumentEmbedding,
    EmbeddingModel,
)
from eve_relation_rag.literature.contracts import EMBEDDING_MODEL_KEY, StrictFrozenSchema
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256
from eve_relation_rag.literature.providers import EmbeddingProvider

type EmbeddingMode = Literal["passage", "query"]

_UNIT_NORM_TOLERANCE = 0.00001
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class EmbeddingValidationError(ValueError):
    """Raised when a provider vector violates the pinned representation contract."""


class EmbeddingBuildError(RuntimeError):
    """Raised when corpus embedding construction cannot complete atomically."""


class ValidatedEmbedding(StrictFrozenSchema):
    """Canonical float32 embedding plus its model- and subject-bound digest."""

    vector: tuple[float, ...]
    embedding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EmbeddingBuildReport(StrictFrozenSchema):
    """Deterministic summary of an embedding build or exact replay."""

    corpus_release_key: str
    embedding_model_key: str
    chunk_count: int = Field(ge=1)
    inserted_count: int = Field(ge=0)
    reused_count: int = Field(ge=0)
    embeddings_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def canonical_embedding_sha256(
    vector: Sequence[float],
    *,
    model_key: str,
    subject_key: str,
    mode: EmbeddingMode,
) -> str:
    """Hash exact canonical little-endian float32 bytes and representation metadata."""

    float32_values = _canonical_float32(vector)
    vector_bytes = struct.pack(f"<{len(float32_values)}f", *float32_values)
    metadata = canonical_json_bytes(
        {
            "dimension": len(float32_values),
            "embedding_mode": mode,
            "l2_normalized": True,
            "model_key": model_key,
            "pooling": "cls",
            "subject_key": subject_key,
            "vector_encoding": "float32-little-endian-v1",
        }
    )
    return hashlib.sha256(metadata + b"\x00" + vector_bytes).hexdigest()


def validate_embedding(
    vector: Sequence[float],
    *,
    expected_dimension: int,
    model_key: str,
    subject_key: str,
    mode: EmbeddingMode,
) -> ValidatedEmbedding:
    """Validate dimension, finiteness, float32 representation, and unit norm."""

    if len(vector) != expected_dimension:
        raise EmbeddingValidationError(
            f"embedding dimension {len(vector)} does not match {expected_dimension}"
        )
    values = _canonical_float32(vector)
    norm = math.sqrt(math.fsum(value * value for value in values))
    if abs(norm - 1.0) > _UNIT_NORM_TOLERANCE:
        raise EmbeddingValidationError("embedding is not unit-normalized")
    return ValidatedEmbedding(
        vector=values,
        embedding_sha256=canonical_embedding_sha256(
            values,
            model_key=model_key,
            subject_key=subject_key,
            mode=mode,
        ),
    )


def embed_candidate_corpus(
    engine: Engine,
    *,
    corpus_release_key: str,
    provider: EmbeddingProvider,
    batch_size: int = 500,
) -> EmbeddingBuildReport:
    """Compute every vector before atomically inserting or exactly reusing embeddings."""

    if not 1 <= batch_size <= 500:
        raise EmbeddingBuildError("batch_size must be in 1..500")
    if (
        provider.model_key != EMBEDDING_MODEL_KEY
        or provider.dimension != 384
        or not _SHA256_RE.fullmatch(provider.artifact_manifest_sha256)
    ):
        raise EmbeddingBuildError("embedding provider does not match the pinned model contract")

    release_id, model_id, artifact_manifest_sha256, chunk_rows = _load_build_inputs(
        engine, corpus_release_key
    )
    if provider.artifact_manifest_sha256 != artifact_manifest_sha256:
        raise EmbeddingBuildError(
            "embedding provider artifact manifest does not match the corpus model"
        )
    validated: list[tuple[int, str, ValidatedEmbedding]] = []
    try:
        for offset in range(0, len(chunk_rows), batch_size):
            batch = chunk_rows[offset : offset + batch_size]
            vectors = provider.embed_documents(tuple(row[2] for row in batch))
            if len(vectors) != len(batch):
                raise EmbeddingBuildError("provider must return exactly one vector per chunk")
            for (chunk_id, chunk_key, _text, _text_sha256), vector in zip(
                batch, vectors, strict=True
            ):
                item = validate_embedding(
                    vector,
                    expected_dimension=384,
                    model_key=provider.model_key,
                    subject_key=chunk_key,
                    mode="passage",
                )
                validated.append((chunk_id, chunk_key, item))
    except EmbeddingBuildError:
        raise
    except Exception as exc:
        raise EmbeddingBuildError(
            "embedding provider failed or returned an invalid vector"
        ) from exc

    embeddings_sha256 = canonical_json_sha256(
        tuple(sorted((chunk_key, item.embedding_sha256) for _, chunk_key, item in validated))
    )
    inserted_count = 0
    reused_count = 0
    try:
        with Session(engine) as session, session.begin():
            release = session.scalar(
                select(CorpusRelease).where(CorpusRelease.id == release_id).with_for_update()
            )
            if (
                release is None
                or release.corpus_release_key != corpus_release_key
                or release.status not in {"candidate", "validated"}
                or release.embedding_model_id != model_id
            ):
                raise EmbeddingBuildError("corpus release changed during embedding construction")

            live_chunks = tuple(
                session.execute(
                    select(
                        DocumentChunk.id,
                        DocumentChunk.chunk_key,
                        DocumentChunk.text,
                        DocumentChunk.text_sha256,
                    )
                    .where(DocumentChunk.release_id == release_id)
                    .order_by(DocumentChunk.chunk_key)
                ).all()
            )
            if live_chunks != chunk_rows:
                raise EmbeddingBuildError("corpus chunks changed during embedding construction")

            for chunk_id, chunk_key, item in validated:
                existing = session.scalar(
                    select(DocumentEmbedding).where(
                        DocumentEmbedding.release_id == release_id,
                        DocumentEmbedding.chunk_id == chunk_id,
                        DocumentEmbedding.embedding_model_id == model_id,
                    )
                )
                if existing is not None:
                    replay = validate_embedding(
                        tuple(float(value) for value in existing.embedding),
                        expected_dimension=384,
                        model_key=provider.model_key,
                        subject_key=chunk_key,
                        mode="passage",
                    )
                    if (
                        replay.embedding_sha256 != item.embedding_sha256
                        or existing.embedding_sha256 != item.embedding_sha256
                    ):
                        raise EmbeddingBuildError(
                            f"existing embedding differs for chunk {chunk_key}"
                        )
                    reused_count += 1
                    continue
                session.add(
                    DocumentEmbedding(
                        release_id=release_id,
                        chunk_id=chunk_id,
                        embedding_model_id=model_id,
                        embedding=list(item.vector),
                        embedding_mode="passage",
                        embedding_sha256=item.embedding_sha256,
                    )
                )
                inserted_count += 1
            session.flush()
    except EmbeddingBuildError:
        raise
    except Exception as exc:
        raise EmbeddingBuildError("embedding persistence transaction failed") from exc

    return EmbeddingBuildReport(
        corpus_release_key=corpus_release_key,
        embedding_model_key=provider.model_key,
        chunk_count=len(validated),
        inserted_count=inserted_count,
        reused_count=reused_count,
        embeddings_sha256=embeddings_sha256,
    )


def _load_build_inputs(
    engine: Engine, corpus_release_key: str
) -> tuple[int, int, str, tuple[tuple[int, str, str, str], ...]]:
    with Session(engine) as session:
        release_row = session.execute(
            select(
                CorpusRelease.id,
                CorpusRelease.status,
                CorpusRelease.embedding_model_id,
                EmbeddingModel.model_key,
                EmbeddingModel.dimension,
                EmbeddingModel.artifact_manifest_sha256,
            )
            .join(EmbeddingModel, EmbeddingModel.id == CorpusRelease.embedding_model_id)
            .where(CorpusRelease.corpus_release_key == corpus_release_key)
        ).one_or_none()
        if release_row is None:
            raise EmbeddingBuildError("corpus release was not found")
        if release_row.status not in {"candidate", "validated"}:
            raise EmbeddingBuildError("embeddings may only be built for candidate/validated corpus")
        if release_row.model_key != EMBEDDING_MODEL_KEY or release_row.dimension != 384:
            raise EmbeddingBuildError("corpus release does not pin the approved embedding model")
        chunks = cast(
            tuple[tuple[int, str, str, str], ...],
            tuple(
                session.execute(
                    select(
                        DocumentChunk.id,
                        DocumentChunk.chunk_key,
                        DocumentChunk.text,
                        DocumentChunk.text_sha256,
                    )
                    .where(DocumentChunk.release_id == release_row.id)
                    .order_by(DocumentChunk.chunk_key)
                ).all()
            ),
        )
        if not chunks:
            raise EmbeddingBuildError("corpus release contains no chunks")
        return (
            int(release_row.id),
            int(release_row.embedding_model_id),
            str(release_row.artifact_manifest_sha256),
            chunks,
        )


def _canonical_float32(vector: Sequence[float]) -> tuple[float, ...]:
    values: list[float] = []
    for raw_value in vector:
        if isinstance(raw_value, bool):
            raise EmbeddingValidationError("embedding values must be real finite numbers")
        try:
            value = float(raw_value)
            canonical = struct.unpack("<f", struct.pack("<f", value))[0]
        except (OverflowError, TypeError, ValueError, struct.error) as exc:
            raise EmbeddingValidationError("embedding values must be finite float32") from exc
        if not math.isfinite(canonical):
            raise EmbeddingValidationError("embedding values must be finite")
        values.append(canonical)
    return tuple(values)
