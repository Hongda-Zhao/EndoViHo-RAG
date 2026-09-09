"""S2/S3 evidence hydration using the existing FTS and production hybrid repository."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from sqlalchemy import Engine

from eve_relation_rag.experiments.embedding_ablation.corpus_snapshot import (
    PublishedCorpusSnapshot,
)
from eve_relation_rag.experiments.embedding_ablation.retrieval import PostgresFtsCandidateProvider
from eve_relation_rag.experiments.rag_value_ablation.contracts import EvidenceCitation
from eve_relation_rag.experiments.rag_value_ablation.structured_evidence import (
    StructuredEvidenceGroup,
)
from eve_relation_rag.experiments.rag_value_ablation.systems import GENERATION_CONTEXT_CHUNK_LIMIT
from eve_relation_rag.literature.contracts import (
    EMBEDDING_MODEL_KEY,
    RetrievalAnchor,
    Sha256,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.embeddings import validate_embedding
from eve_relation_rag.literature.hashing import canonical_json_bytes
from eve_relation_rag.literature.local_bge import verify_model_artifact_manifest
from eve_relation_rag.planning.scope_policy import contains_forbidden_topic
from eve_relation_rag.retrieval.hybrid.anchors import (
    StructuredAnchorResolution,
    StructuredAnchorResolver,
)
from eve_relation_rag.retrieval.literature.repository import LiteratureRepository


class LiteratureAdapterError(ValueError):
    """Retrieval stopped without constructing a replacement evidence path."""


class BgeAssetFiles(StrictFrozenSchema):
    model_root: str
    artifact_manifest_path: str
    python_executable: str
    python_executable_sha256: Sha256
    worker_script: str
    worker_script_sha256: Sha256


def verify_bge_assets(files: BgeAssetFiles, approved_sha256: str) -> str:
    for path, expected in (
        (files.python_executable, files.python_executable_sha256),
        (files.worker_script, files.worker_script_sha256),
    ):
        with Path(path).open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != expected:
                raise LiteratureAdapterError("BGE worker or interpreter differs from request")
    return verify_model_artifact_manifest(
        Path(files.model_root),
        Path(files.artifact_manifest_path),
        approved_sha256,
    )


class OfflineBgeQueryProvider:
    """Query-only adapter around the original LocalBgeProvider in an existing Python environment."""

    def __init__(self, files: BgeAssetFiles, approved_sha256: str) -> None:
        self.files = files
        self.artifact_manifest_sha256 = verify_bge_assets(files, approved_sha256)

    def embed_query(self, question: str) -> tuple[float, ...]:
        if contains_forbidden_topic(question):
            raise LiteratureAdapterError("shared scope policy refused the query before model use")
        verify_bge_assets(self.files, self.artifact_manifest_sha256)
        raw = canonical_json_bytes({"question": question})
        try:
            completed = subprocess.run(
                [
                    "/usr/bin/sandbox-exec",
                    "-p",
                    "(version 1)(allow default)(deny network*)",
                    str(Path(self.files.python_executable).absolute()),
                    "-I",
                    str(Path(self.files.worker_script).absolute()),
                    "--model-root",
                    self.files.model_root,
                    "--manifest",
                    self.files.artifact_manifest_path,
                    "--manifest-sha256",
                    self.artifact_manifest_sha256,
                ],
                input=raw,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=120,
                check=True,
                env={
                    "PATH": "/usr/bin:/bin",
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "HF_HUB_DISABLE_TELEMETRY": "1",
                    "TOKENIZERS_PARALLELISM": "false",
                },
            )
            value = json.loads(completed.stdout)
            if value["request_sha256"] != hashlib.sha256(raw).hexdigest():
                raise ValueError("query mismatch")
            return validate_embedding(
                value["vector"],
                expected_dimension=384,
                model_key=EMBEDDING_MODEL_KEY,
                subject_key=hashlib.sha256(question.encode()).hexdigest(),
                mode="query",
            ).vector
        except Exception:
            raise LiteratureAdapterError("offline BGE query failed; no retry or fallback") from None


class LiteratureEvidenceAdapter:
    """Retain all 100 ranked keys for retrieval metrics and at most eight model citations."""

    def __init__(
        self,
        engine: Engine,
        published: PublishedCorpusSnapshot,
        *,
        bge: OfflineBgeQueryProvider | None = None,
    ) -> None:
        if bge is not None and (
            type(bge) is not OfflineBgeQueryProvider
            or bge.artifact_manifest_sha256 != published.capability.model_artifact_manifest_sha256
        ):
            raise LiteratureAdapterError("query BGE identity differs from the published corpus")
        self.published = published
        self._bge = bge
        self._fts = PostgresFtsCandidateProvider(engine, published) if bge is None else None
        self._hybrid = LiteratureRepository(engine) if bge is not None else None
        self._anchor_resolver = StructuredAnchorResolver(engine)

    def retrieve(self, question: str) -> tuple[tuple[str, ...], tuple[EvidenceCitation, ...]]:
        return self._retrieve(question, anchors=())

    def retrieve_structured(
        self, question: str, groups: tuple[StructuredEvidenceGroup, ...],
    ) -> tuple[
        tuple[str, ...], tuple[EvidenceCitation, ...], tuple[StructuredAnchorResolution, ...],
    ]:
        """S5 derives anchors through the original persisted corpus-anchor verifier.

        Unmatched anchors remain diagnostics; the production repository's frozen
        corpus-fill behavior is retained. This method issues no run authority.
        """
        if contains_forbidden_topic(question):
            raise LiteratureAdapterError("shared scope refusal precedes all retrieval")
        if self._bge is None or not groups:
            raise LiteratureAdapterError(
                "S5 requires complete structured groups and hybrid retrieval"
            )
        checked = tuple(StructuredEvidenceGroup.model_validate_json(g.model_dump_json())
                        for g in groups)
        releases = {(g.pages or g.details)[0].structured_result.release.model_dump_json()
                    for g in checked}
        if len(releases) != 1:
            raise LiteratureAdapterError("S5 groups differ in their fixed release")
        resolutions = []
        anchors: dict[str, RetrievalAnchor] = {}
        for group in checked:
            for success in (*group.pages, *group.details):
                resolved = self._anchor_resolver.resolve(success, self.published.capability)
                resolutions.append(resolved)
                for anchor in resolved.anchors:
                    previous = anchors.get(anchor.anchor_key)
                    if previous is not None and previous != anchor:
                        raise LiteratureAdapterError("same anchor key has conflicting identities")
                    anchors[anchor.anchor_key] = anchor
                if len(anchors) > 64:
                    raise LiteratureAdapterError("S5 exceeds the frozen 64-anchor limit")
        keys, citations = self._retrieve(
            question, anchors=tuple(anchors[k] for k in sorted(anchors)),
        )
        return keys, citations, tuple(resolutions)

    def _retrieve(
        self, question: str, *, anchors: tuple[RetrievalAnchor, ...],
    ) -> tuple[tuple[str, ...], tuple[EvidenceCitation, ...]]:
        if contains_forbidden_topic(question):
            raise LiteratureAdapterError("shared scope refusal precedes all retrieval")
        if self._bge is not None and self._hybrid is not None:
            result = self._hybrid.retrieve(
                self.published.capability,
                question=question,
                query_vector=self._bge.embed_query(question),
                anchors=anchors,
                top_k=100,
            )
            keys = tuple(hit.chunk_key for hit in result.hits)
            snapshot = {chunk.chunk_key: chunk for chunk in self.published.snapshot.chunks}
            for hit in result.hits:
                chunk = snapshot.get(hit.chunk_key)
                if chunk is None or (
                    hit.document_key,
                    hit.text,
                    hit.text_sha256,
                    hit.locator_text,
                ) != (chunk.document_key, chunk.text, chunk.text_sha256, chunk.locator_text):
                    raise LiteratureAdapterError("retrieved passage differs from frozen snapshot")
        elif self._fts is not None:
            keys = self._fts.rank(question, allowed_document_keys=None, limit=100)
        else:
            raise LiteratureAdapterError("retrieval branch is unavailable")
        return keys, hydrate_citations(self.published, keys[:GENERATION_CONTEXT_CHUNK_LIMIT])


def hydrate_citations(
    published: PublishedCorpusSnapshot,
    keys: tuple[str, ...],
) -> tuple[EvidenceCitation, ...]:
    if len(keys) != len(set(keys)):
        raise LiteratureAdapterError("ranked passages contain duplicates")
    chunks = {chunk.chunk_key: chunk for chunk in published.snapshot.chunks}
    if not set(keys) <= chunks.keys():
        raise LiteratureAdapterError("ranked passage is outside the fixed corpus")
    return tuple(
        EvidenceCitation(
            citation_id=f"D{index}",
            document_key=chunks[key].document_key,
            chunk_key=key,
            locator_text=chunks[key].locator_text,
            text=chunks[key].text,
            text_sha256=chunks[key].text_sha256,
        )
        for index, key in enumerate(keys, 1)
    )
