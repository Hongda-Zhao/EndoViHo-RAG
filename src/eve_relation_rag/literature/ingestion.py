"""Atomic, manifest-first candidate corpus ingestion."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import Field
from sqlalchemy import Engine, func, literal_column, select
from sqlalchemy.orm import Session

from eve_relation_rag.db.models import (
    CorpusDocumentMembership,
    CorpusImportLedger,
    CorpusImportRun,
    CorpusRelease,
    Document,
    DocumentChunk,
    DocumentEmbedding,
    EmbeddingModel,
    LiteraturePolicy,
)
from eve_relation_rag.literature.chunking import (
    DocumentChunkDraft,
    OffsetTokenizer,
    chunk_document,
)
from eve_relation_rag.literature.contracts import (
    ANCHOR_POLICY_KEY,
    CHUNKING_POLICY_KEY,
    EMBEDDING_MODEL_KEY,
    EMBEDDING_QUERY_PREFIX,
    EMBEDDING_REPOSITORY_ID,
    EMBEDDING_REVISION,
    FTS_POLICY_KEY,
    PARSER_POLICY_KEY,
    RETRIEVAL_POLICY_KEY,
    CorpusDocumentSpec,
    CorpusManifest,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.embeddings import ValidatedEmbedding, validate_embedding
from eve_relation_rag.literature.hashing import (
    canonical_json_sha256,
    canonical_manifest_sha256,
    corpus_import_run_key,
)
from eve_relation_rag.literature.parsing import ParsedDocument, parse_document
from eve_relation_rag.literature.providers import EmbeddingProvider

IMPORTER_VERSION = "eve-literature-importer-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CorpusImportError(RuntimeError):
    """Raised when candidate import cannot prove exact, immutable replay."""


class CorpusImportReport(StrictFrozenSchema):
    """Canonical summary of a completed or exactly replayed candidate import."""

    run_key: str = Field(pattern=r"^corpus-import:sha256:[0-9a-f]{64}$")
    corpus_release_key: str
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    replayed: bool
    imported_documents: int = Field(ge=0)
    reused_documents: int = Field(ge=0)
    document_count: int = Field(ge=1)
    chunk_count: int = Field(ge=1)
    document_keys_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    chunk_keys_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding_count: int = Field(ge=0)
    embeddings_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class _PreparedDocument:
    spec: CorpusDocumentSpec
    parsed: ParsedDocument
    chunks: tuple[DocumentChunkDraft, ...]


@dataclass(frozen=True)
class _PolicySpec:
    key: str
    kind: str
    body: dict[str, Any]


def verify_source_artifact(spec: CorpusDocumentSpec, import_root: Path) -> bytes:
    """Read one exact regular file below the approved root, rejecting symlinks."""

    try:
        root = import_root.resolve(strict=True)
    except OSError as exc:
        raise CorpusImportError("approved import root does not exist") from exc
    candidate = import_root / spec.relative_path
    if candidate.is_symlink():
        raise CorpusImportError(f"source artifact is a symbolic link: {spec.relative_path}")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise CorpusImportError(f"source artifact is missing: {spec.relative_path}") from exc
    if not resolved.is_relative_to(root):
        raise CorpusImportError(f"source artifact escapes the approved root: {spec.relative_path}")
    if not resolved.is_file():
        raise CorpusImportError(f"source artifact is not a regular file: {spec.relative_path}")

    payload = resolved.read_bytes()
    if len(payload) != spec.byte_size:
        raise CorpusImportError(f"source artifact byte_size mismatch: {spec.relative_path}")
    observed_sha256 = hashlib.sha256(payload).hexdigest()
    if observed_sha256 != spec.source_sha256:
        raise CorpusImportError(f"source artifact SHA-256 mismatch: {spec.relative_path}")
    return payload


def verify_import_root(manifest: CorpusManifest, import_root: Path) -> Path:
    """Require the approved root to contain exactly the manifested regular files."""

    if import_root.is_symlink():
        raise CorpusImportError("approved import root must not be a symbolic link")
    try:
        root = import_root.resolve(strict=True)
    except OSError as exc:
        raise CorpusImportError("approved import root does not exist") from exc
    if not root.is_dir():
        raise CorpusImportError("approved import root must be a directory")

    actual_files: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise CorpusImportError("approved import root contains a symbolic link")
        if path.is_file() and path.suffix in {".md", ".txt", ".xml"}:
            actual_files.add(path.relative_to(root).as_posix())
    expected_files = {document.relative_path for document in manifest.documents}
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        unmanifested = sorted(actual_files - expected_files)
        raise CorpusImportError(
            f"import root file set differs from manifest: missing={missing}, "
            f"unmanifested={unmanifested}"
        )
    return root


def import_candidate_corpus(
    engine: Engine,
    *,
    manifest: CorpusManifest,
    import_root: Path,
    tokenizer: OffsetTokenizer,
    approved_manifest_sha256: str,
    importer_code_sha256: str,
    model_artifact_manifest_sha256: str,
    embedding_provider: EmbeddingProvider | None = None,
    embedding_batch_size: int = 500,
) -> CorpusImportReport:
    """Prepare all artifacts before atomically inserting one candidate release."""

    _validate_import_identity(
        manifest,
        approved_manifest_sha256=approved_manifest_sha256,
        importer_code_sha256=importer_code_sha256,
        model_artifact_manifest_sha256=model_artifact_manifest_sha256,
    )
    if embedding_provider is not None:
        _validate_embedding_provider_identity(
            embedding_provider,
            expected_artifact_manifest_sha256=model_artifact_manifest_sha256,
        )
    verified_import_root = verify_import_root(manifest, import_root)
    prepared = _prepare_documents(manifest, verified_import_root, tokenizer)
    all_chunks = tuple(chunk for document in prepared for chunk in document.chunks)
    if not (
        manifest.expected_chunk_count_min <= len(all_chunks) <= manifest.expected_chunk_count_max
    ):
        raise CorpusImportError("derived chunk count is outside the manifest-approved range")

    document_keys = tuple(sorted(document.spec.expected_document_key for document in prepared))
    chunk_keys = tuple(sorted(chunk.chunk_key for chunk in all_chunks))
    document_keys_sha256 = canonical_json_sha256(document_keys)
    chunk_keys_sha256 = canonical_json_sha256(chunk_keys)
    prepared_embeddings = _prepare_embeddings(
        all_chunks,
        embedding_provider,
        batch_size=embedding_batch_size,
    )
    embeddings_sha256 = (
        canonical_json_sha256(
            tuple(
                sorted(
                    (chunk_key, embedding.embedding_sha256)
                    for chunk_key, embedding in prepared_embeddings.items()
                )
            )
        )
        if prepared_embeddings
        else None
    )
    parameters = {
        "chunking_policy_key": manifest.chunking_policy_key,
        "embedding_model_key": manifest.embedding_model_key,
        "fts_policy_key": manifest.fts_policy_key,
        "model_artifact_manifest_sha256": model_artifact_manifest_sha256,
        "parser_policy_key": manifest.parser_policy_key,
        "retrieval_policy_key": manifest.retrieval_policy_key,
        "tokenizer_model_key": tokenizer.model_key,
        "embedding_build": embedding_provider is not None,
    }
    parameters_sha256 = canonical_json_sha256(parameters)
    run_key = corpus_import_run_key(
        {
            "corpus_release_key": manifest.corpus_release_key,
            "importer_code_sha256": importer_code_sha256,
            "importer_version": IMPORTER_VERSION,
            "manifest_sha256": manifest.manifest_sha256,
            "parameters_sha256": parameters_sha256,
        }
    )

    try:
        with Session(engine) as session, session.begin():
            existing_run = session.scalar(
                select(CorpusImportRun).where(CorpusImportRun.run_key == run_key)
            )
            if existing_run is not None:
                return _replay_report(
                    existing_run,
                    manifest=manifest,
                    parameters_sha256=parameters_sha256,
                    document_keys_sha256=document_keys_sha256,
                    chunk_keys_sha256=chunk_keys_sha256,
                    expected_chunk_count=len(all_chunks),
                    expected_embedding_count=len(prepared_embeddings),
                    embeddings_sha256=embeddings_sha256,
                )

            policies = {
                spec.kind: _ensure_policy(session, spec, importer_code_sha256)
                for spec in _policy_specs(manifest)
            }
            model = _ensure_embedding_model(session, model_artifact_manifest_sha256)
            policy_graph_sha256 = canonical_json_sha256(
                {
                    "anchor_policy": _policy_identity(policies["anchor"]),
                    "chunking_policy": _policy_identity(policies["chunking"]),
                    "embedding_model": {
                        "artifact_manifest_sha256": model.artifact_manifest_sha256,
                        "model_key": model.model_key,
                    },
                    "fts_policy": _policy_identity(policies["fts"]),
                    "parser_policy": _policy_identity(policies["parser"]),
                    "retrieval_policy": _policy_identity(policies["retrieval"]),
                }
            )
            release = _ensure_release(
                session,
                manifest=manifest,
                policies=policies,
                model=model,
                policy_graph_sha256=policy_graph_sha256,
            )

            imported_documents = 0
            reused_documents = 0
            persisted: list[tuple[_PreparedDocument, Document, str]] = []
            for item in prepared:
                document, outcome = _ensure_document(session, item)
                imported_documents += int(outcome == "imported")
                reused_documents += int(outcome == "reused")
                _ensure_membership(session, release, document, item.spec.manifest_row)
                _ensure_chunks(
                    session,
                    release=release,
                    document=document,
                    item=item,
                    parser_policy=policies["parser"],
                    chunking_policy=policies["chunking"],
                )
                persisted.append((item, document, outcome))

            if prepared_embeddings:
                chunk_rows = tuple(
                    session.execute(
                        select(DocumentChunk.id, DocumentChunk.chunk_key).where(
                            DocumentChunk.release_id == release.id,
                            DocumentChunk.chunk_key.in_(tuple(prepared_embeddings)),
                        )
                    ).all()
                )
                if len(chunk_rows) != len(prepared_embeddings):
                    raise CorpusImportError("not every prepared embedding has a persisted chunk")
                for chunk_id, persisted_chunk_key in chunk_rows:
                    embedding = prepared_embeddings[persisted_chunk_key]
                    existing_embedding = session.scalar(
                        select(DocumentEmbedding).where(
                            DocumentEmbedding.release_id == release.id,
                            DocumentEmbedding.chunk_id == chunk_id,
                            DocumentEmbedding.embedding_model_id == model.id,
                        )
                    )
                    if existing_embedding is not None:
                        if existing_embedding.embedding_sha256 != embedding.embedding_sha256:
                            raise CorpusImportError(
                                f"existing embedding differs for chunk {persisted_chunk_key}"
                            )
                        continue
                    session.add(
                        DocumentEmbedding(
                            release_id=release.id,
                            chunk_id=chunk_id,
                            embedding_model_id=model.id,
                            embedding=list(embedding.vector),
                            embedding_mode="passage",
                            embedding_sha256=embedding.embedding_sha256,
                        )
                    )
                session.flush()

            now = datetime.now(UTC)
            terminal_counts: dict[str, Any] = {
                "chunk_count": len(all_chunks),
                "chunk_keys_sha256": chunk_keys_sha256,
                "document_count": len(prepared),
                "document_keys_sha256": document_keys_sha256,
                "imported_documents": imported_documents,
                "reused_documents": reused_documents,
                "embedding_count": len(prepared_embeddings),
                "embeddings_sha256": embeddings_sha256,
            }
            run = CorpusImportRun(
                run_key=run_key,
                release_id=release.id,
                manifest_sha256=manifest.manifest_sha256,
                importer_version=IMPORTER_VERSION,
                code_sha256=importer_code_sha256,
                parameters=parameters,
                parameters_sha256=parameters_sha256,
                terminal_counts=terminal_counts,
                status="succeeded",
                started_at=now,
                finished_at=now,
            )
            session.add(run)
            session.flush()
            for item, document, outcome in persisted:
                session.add(
                    CorpusImportLedger(
                        run_id=run.id,
                        release_id=release.id,
                        manifest_row=item.spec.manifest_row,
                        document_id=document.id,
                        outcome=outcome,
                        reason_code=None,
                        source_sha256=item.spec.source_sha256,
                        chunk_count=len(item.chunks),
                        details={
                            "document_key": document.document_key,
                            "relative_path": item.spec.relative_path,
                        },
                    )
                )
            session.flush()
    except CorpusImportError:
        raise
    except Exception as exc:
        raise CorpusImportError("candidate corpus transaction failed") from exc

    return CorpusImportReport(
        run_key=run_key,
        corpus_release_key=manifest.corpus_release_key,
        manifest_sha256=manifest.manifest_sha256,
        replayed=False,
        imported_documents=imported_documents,
        reused_documents=reused_documents,
        document_count=len(prepared),
        chunk_count=len(all_chunks),
        document_keys_sha256=document_keys_sha256,
        chunk_keys_sha256=chunk_keys_sha256,
        embedding_count=len(prepared_embeddings),
        embeddings_sha256=embeddings_sha256,
    )


def _validate_import_identity(
    manifest: CorpusManifest,
    *,
    approved_manifest_sha256: str,
    importer_code_sha256: str,
    model_artifact_manifest_sha256: str,
) -> None:
    if approved_manifest_sha256 != manifest.manifest_sha256:
        raise CorpusImportError("approved_manifest_sha256 does not match the manifest")
    if canonical_manifest_sha256(manifest) != manifest.manifest_sha256:
        raise CorpusImportError("manifest canonical SHA-256 is invalid")
    if not _SHA256_RE.fullmatch(importer_code_sha256):
        raise CorpusImportError("importer_code_sha256 must be lowercase SHA-256")
    if not _SHA256_RE.fullmatch(model_artifact_manifest_sha256):
        raise CorpusImportError("model artifact manifest SHA-256 is invalid")
    expected = (
        manifest.parser_policy_key == PARSER_POLICY_KEY,
        manifest.chunking_policy_key == CHUNKING_POLICY_KEY,
        manifest.embedding_model_key == EMBEDDING_MODEL_KEY,
        manifest.fts_policy_key == FTS_POLICY_KEY,
        manifest.retrieval_policy_key == RETRIEVAL_POLICY_KEY,
        manifest.anchor_policy_key == ANCHOR_POLICY_KEY,
    )
    if not all(expected):
        raise CorpusImportError("manifest policy graph does not match the approved M3 contract")


def _prepare_documents(
    manifest: CorpusManifest,
    import_root: Path,
    tokenizer: OffsetTokenizer,
) -> tuple[_PreparedDocument, ...]:
    prepared: list[_PreparedDocument] = []
    for spec in manifest.documents:
        if spec.license_review_status != "approved" or not spec.retrieval_text_allowed:
            raise CorpusImportError(
                "every imported document requires approved retrieval-text rights"
            )
        payload = verify_source_artifact(spec, import_root)
        try:
            parsed = parse_document(spec.document_format, payload)
            chunks = chunk_document(
                parsed,
                corpus_release_key=manifest.corpus_release_key,
                document_key=spec.expected_document_key,
                tokenizer=tokenizer,
            )
        except ValueError as exc:
            raise CorpusImportError(
                f"document parsing/chunking failed: {spec.relative_path}"
            ) from exc
        if parsed.title != spec.title:
            raise CorpusImportError(f"parsed title does not match manifest: {spec.relative_path}")
        prepared.append(_PreparedDocument(spec=spec, parsed=parsed, chunks=chunks))
    return tuple(prepared)


def _prepare_embeddings(
    chunks: tuple[DocumentChunkDraft, ...],
    provider: EmbeddingProvider | None,
    *,
    batch_size: int,
) -> dict[str, ValidatedEmbedding]:
    if provider is None:
        return {}
    if not 1 <= batch_size <= 500:
        raise CorpusImportError("embedding_batch_size must be in 1..500")

    prepared: dict[str, ValidatedEmbedding] = {}
    try:
        for offset in range(0, len(chunks), batch_size):
            batch = chunks[offset : offset + batch_size]
            vectors = provider.embed_documents(tuple(chunk.text for chunk in batch))
            if len(vectors) != len(batch):
                raise CorpusImportError("embedding provider must return one vector per chunk")
            for chunk, vector in zip(batch, vectors, strict=True):
                prepared[chunk.chunk_key] = validate_embedding(
                    vector,
                    expected_dimension=384,
                    model_key=provider.model_key,
                    subject_key=chunk.chunk_key,
                    mode="passage",
                )
    except CorpusImportError:
        raise
    except Exception as exc:
        raise CorpusImportError("embedding preparation failed before database mutation") from exc
    return prepared


def _validate_embedding_provider_identity(
    provider: EmbeddingProvider,
    *,
    expected_artifact_manifest_sha256: str,
) -> None:
    """Bind an embedding build to the same exact model artifact approved by the caller."""

    if provider.model_key != EMBEDDING_MODEL_KEY or provider.dimension != 384:
        raise CorpusImportError("embedding provider does not match the pinned model contract")
    if (
        not _SHA256_RE.fullmatch(provider.artifact_manifest_sha256)
        or provider.artifact_manifest_sha256 != expected_artifact_manifest_sha256
    ):
        raise CorpusImportError(
            "embedding provider artifact manifest does not match the approved checksum"
        )


def _policy_specs(manifest: CorpusManifest) -> tuple[_PolicySpec, ...]:
    return (
        _PolicySpec(
            manifest.parser_policy_key,
            "parser",
            {
                "formats": ["jats_xml", "markdown", "plain_text"],
                "max_normalized_codepoints": 5_000_000,
                "normalization": "utf8-lf-nfc-trailing-space-blank-runs-v1",
            },
        ),
        _PolicySpec(
            manifest.chunking_policy_key,
            "chunking",
            {"hard_max": 448, "overlap": 64, "target": 384},
        ),
        _PolicySpec(
            manifest.fts_policy_key,
            "fts",
            {
                "configuration": "english",
                "depth": 100,
                "weights": {"block_and_section": "B", "text": "D", "title": "A"},
            },
        ),
        _PolicySpec(
            manifest.retrieval_policy_key,
            "retrieval",
            {
                "fts_depth": 100,
                "rrf_k": 60,
                "summary_block_types": ["abstract", "title"],
                "summary_vector_depth": 100,
                "vector_depth": 100,
            },
        ),
        _PolicySpec(
            manifest.anchor_policy_key,
            "anchor",
            {
                "mode": "curated-exact-corpus-scoped",
                "types": ["assembly", "document", "keyword", "lineage", "locus", "method"],
            },
        ),
    )


def _ensure_policy(session: Session, spec: _PolicySpec, code_sha256: str) -> LiteraturePolicy:
    expected = {
        "policy_kind": spec.kind,
        "schema_version": "literature-policy-v1",
        "policy_json": spec.body,
        "policy_sha256": canonical_json_sha256(spec.body),
        "code_sha256": code_sha256,
    }
    existing = session.scalar(
        select(LiteraturePolicy).where(LiteraturePolicy.policy_key == spec.key)
    )
    if existing is not None:
        _require_fields(existing, expected, f"literature policy {spec.key}")
        return existing
    policy = LiteraturePolicy(policy_key=spec.key, **expected)
    session.add(policy)
    session.flush()
    return policy


def _ensure_embedding_model(session: Session, artifact_sha256: str) -> EmbeddingModel:
    expected: dict[str, Any] = {
        "provider_kind": "local_hf",
        "repository_id": EMBEDDING_REPOSITORY_ID,
        "revision": EMBEDDING_REVISION,
        "dimension": 384,
        "max_sequence_tokens": 512,
        "pooling": "cls",
        "l2_normalized": True,
        "passage_prefix": "",
        "query_prefix": EMBEDDING_QUERY_PREFIX,
        "similarity": "cosine",
        "license_key": "MIT",
        "artifact_manifest_sha256": artifact_sha256,
        "model_metadata": {"dtype": "float32", "offline_required": True},
    }
    existing = session.scalar(
        select(EmbeddingModel).where(EmbeddingModel.model_key == EMBEDDING_MODEL_KEY)
    )
    if existing is not None:
        _require_fields(existing, expected, f"embedding model {EMBEDDING_MODEL_KEY}")
        return existing
    model = EmbeddingModel(model_key=EMBEDDING_MODEL_KEY, **expected)
    session.add(model)
    session.flush()
    return model


def _policy_identity(policy: LiteraturePolicy) -> dict[str, str]:
    return {"policy_key": policy.policy_key, "policy_sha256": policy.policy_sha256}


def _ensure_release(
    session: Session,
    *,
    manifest: CorpusManifest,
    policies: dict[str, LiteraturePolicy],
    model: EmbeddingModel,
    policy_graph_sha256: str,
) -> CorpusRelease:
    expected: dict[str, Any] = {
        "title": manifest.release_title,
        "purpose": manifest.purpose,
        "status": "candidate",
        "manifest_sha256": manifest.manifest_sha256,
        "policy_graph_sha256": policy_graph_sha256,
        "manifest_document_count": manifest.document_count,
        "expected_chunk_count_min": manifest.expected_chunk_count_min,
        "expected_chunk_count_max": manifest.expected_chunk_count_max,
        "parser_policy_id": policies["parser"].id,
        "chunking_policy_id": policies["chunking"].id,
        "fts_policy_id": policies["fts"].id,
        "retrieval_policy_id": policies["retrieval"].id,
        "anchor_policy_id": policies["anchor"].id,
        "embedding_model_id": model.id,
        "published_at": None,
        "supersedes_release_id": None,
    }
    existing = session.scalar(
        select(CorpusRelease).where(CorpusRelease.corpus_release_key == manifest.corpus_release_key)
    )
    if existing is not None:
        _require_fields(existing, expected, f"corpus release {manifest.corpus_release_key}")
        return existing
    release = CorpusRelease(corpus_release_key=manifest.corpus_release_key, **expected)
    session.add(release)
    session.flush()
    return release


def _ensure_document(session: Session, item: _PreparedDocument) -> tuple[Document, str]:
    spec = item.spec
    expected: dict[str, Any] = {
        "source_artifact_sha256": spec.source_sha256,
        "normalized_document_sha256": item.parsed.normalized_document_sha256,
        "byte_size": spec.byte_size,
        "media_type": spec.media_type,
        "document_version": spec.document_version,
        "title": spec.title,
        "authors": list(spec.authors),
        "doi": spec.doi,
        "pmid": spec.pmid,
        "pmcid": spec.pmcid,
        "source_uri": spec.source_uri,
        "retrieved_at": _parse_rfc3339(spec.retrieved_at),
        "declared_license": spec.declared_license,
        "license_evidence_uri": spec.license_evidence_uri,
        "license_review_status": spec.license_review_status,
        "retrieval_text_allowed": spec.retrieval_text_allowed,
        "bibliographic_metadata": {"document_format": spec.document_format},
    }
    existing = session.scalar(
        select(Document).where(Document.document_key == spec.expected_document_key)
    )
    if existing is not None:
        _require_fields(existing, expected, f"document {spec.expected_document_key}")
        return existing, "reused"
    document = Document(document_key=spec.expected_document_key, **expected)
    session.add(document)
    session.flush()
    return document, "imported"


def _ensure_membership(
    session: Session,
    release: CorpusRelease,
    document: Document,
    manifest_row: int,
) -> None:
    existing = session.get(CorpusDocumentMembership, (release.id, document.id))
    if existing is not None:
        if existing.manifest_row != manifest_row:
            raise CorpusImportError("existing corpus membership differs from the manifest")
        return
    occupied = session.scalar(
        select(CorpusDocumentMembership).where(
            CorpusDocumentMembership.release_id == release.id,
            CorpusDocumentMembership.manifest_row == manifest_row,
        )
    )
    if occupied is not None:
        raise CorpusImportError("manifest row is already occupied by another document")
    session.add(
        CorpusDocumentMembership(
            release_id=release.id,
            document_id=document.id,
            manifest_row=manifest_row,
        )
    )
    session.flush()


def _ensure_chunks(
    session: Session,
    *,
    release: CorpusRelease,
    document: Document,
    item: _PreparedDocument,
    parser_policy: LiteraturePolicy,
    chunking_policy: LiteraturePolicy,
) -> None:
    for chunk in item.chunks:
        expected: dict[str, Any] = {
            "release_id": release.id,
            "document_id": document.id,
            "parser_policy_id": parser_policy.id,
            "chunking_policy_id": chunking_policy.id,
            "chunk_index": chunk.chunk_index,
            "section_path": list(chunk.section_path),
            "block_type": chunk.block_type,
            "locator": chunk.locator.model_dump(mode="json"),
            "locator_text": chunk.locator_text,
            "text": chunk.text,
            "text_sha256": chunk.text_sha256,
            "token_count": chunk.token_count,
        }
        existing = session.scalar(
            select(DocumentChunk).where(DocumentChunk.chunk_key == chunk.chunk_key)
        )
        if existing is not None:
            _require_fields(existing, expected, f"chunk {chunk.chunk_key}")
            continue

        english: Any = literal_column("'english'::regconfig")
        title_vector = func.setweight(
            func.to_tsvector(english, item.spec.title), literal_column("'A'")
        )
        section_label = " ".join((*chunk.section_path, chunk.block_type))
        section_vector = func.setweight(
            func.to_tsvector(english, section_label), literal_column("'B'")
        )
        text_vector = func.setweight(func.to_tsvector(english, chunk.text), literal_column("'D'"))
        fts_document = title_vector.op("||")(section_vector).op("||")(text_vector)
        session.add(
            DocumentChunk(
                chunk_key=chunk.chunk_key,
                fts_document=cast(Any, fts_document),
                **expected,
            )
        )
    session.flush()


def _replay_report(
    run: CorpusImportRun,
    *,
    manifest: CorpusManifest,
    parameters_sha256: str,
    document_keys_sha256: str,
    chunk_keys_sha256: str,
    expected_chunk_count: int,
    expected_embedding_count: int,
    embeddings_sha256: str | None,
) -> CorpusImportReport:
    if (
        run.status != "succeeded"
        or run.manifest_sha256 != manifest.manifest_sha256
        or run.parameters_sha256 != parameters_sha256
    ):
        raise CorpusImportError("existing import run is not an exact successful replay")
    counts = run.terminal_counts
    expected = {
        "chunk_count": expected_chunk_count,
        "chunk_keys_sha256": chunk_keys_sha256,
        "document_count": manifest.document_count,
        "document_keys_sha256": document_keys_sha256,
        "embedding_count": expected_embedding_count,
        "embeddings_sha256": embeddings_sha256,
    }
    if any(counts.get(key) != value for key, value in expected.items()):
        raise CorpusImportError("existing import run terminal digests do not match replay")
    return CorpusImportReport(
        run_key=run.run_key,
        corpus_release_key=manifest.corpus_release_key,
        manifest_sha256=manifest.manifest_sha256,
        replayed=True,
        imported_documents=int(counts.get("imported_documents", 0)),
        reused_documents=int(counts.get("reused_documents", 0)),
        document_count=manifest.document_count,
        chunk_count=expected_chunk_count,
        document_keys_sha256=document_keys_sha256,
        chunk_keys_sha256=chunk_keys_sha256,
        embedding_count=expected_embedding_count,
        embeddings_sha256=embeddings_sha256,
    )


def _require_fields(row: object, expected: dict[str, Any], label: str) -> None:
    mismatched = tuple(
        field_name
        for field_name, expected_value in expected.items()
        if getattr(row, field_name) != expected_value
    )
    if mismatched:
        raise CorpusImportError(f"existing {label} differs in: {', '.join(mismatched)}")


def _parse_rfc3339(value: str) -> datetime:
    return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
