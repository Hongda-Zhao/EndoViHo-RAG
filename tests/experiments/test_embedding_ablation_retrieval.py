from __future__ import annotations

import hashlib

from eve_relation_rag.experiments.embedding_ablation.contracts import (
    AblationSystem,
    ModelRepresentationContract,
)
from eve_relation_rag.experiments.embedding_ablation.corpus_snapshot import (
    CorpusSnapshot,
    SnapshotAnchor,
    SnapshotChunk,
    SnapshotDocument,
    build_corpus_snapshot,
)
from eve_relation_rag.experiments.embedding_ablation.retrieval import AblationRetriever
from eve_relation_rag.experiments.embedding_ablation.sidecar import ExactVectorIndex
from eve_relation_rag.literature.contracts import (
    ANCHOR_POLICY_KEY,
    FTS_POLICY_KEY,
    RETRIEVAL_POLICY_KEY,
    BlockType,
)

DOC_A = f"document:sha256:{'a' * 64}"
DOC_B = f"document:sha256:{'b' * 64}"
KEY_A = f"chunk:sha256:{'a' * 64}"
KEY_B = f"chunk:sha256:{'b' * 64}"
KEY_C = f"chunk:sha256:{'c' * 64}"
ANCHOR = f"anchor:sha256:{'a' * 64}"


class StaticFts:
    def rank(
        self,
        question: str,
        *,
        allowed_document_keys: frozenset[str] | None,
        limit: int,
    ) -> tuple[str, ...]:
        assert question == "query"
        assert limit == 100
        if allowed_document_keys == frozenset({DOC_B}):
            return (KEY_B, KEY_C)
        return (KEY_B, KEY_A, KEY_C)


def test_hybrid_retriever_keeps_fts_rrf_summary_and_anchor_tiers_fixed() -> None:
    retriever = AblationRetriever(
        system=_system(),
        snapshot=_snapshot(),
        dense_index=_index(),
        fts_provider=StaticFts(),
    )

    corpus = retriever.retrieve(question="query", query_vector=(1.0, 0.0))
    anchored = retriever.retrieve(
        question="query",
        query_vector=(1.0, 0.0),
        anchor_keys=(ANCHOR,),
    )

    assert tuple(item.candidate.chunk_key for item in corpus.candidates) == (
        KEY_A,
        KEY_C,
        KEY_B,
    )
    assert corpus.candidates[0].candidate.fts_rank == 2
    assert corpus.candidates[0].candidate.vector_rank == 1
    assert corpus.candidates[0].candidate.summary_vector_rank == 1
    assert tuple(item.candidate.retrieval_tier for item in anchored.candidates) == (
        "anchored",
        "anchored",
        "corpus_fill",
    )
    assert {item.candidate.chunk_key for item in anchored.candidates[:2]} == {KEY_B, KEY_C}
    assert anchored.candidates[2].candidate.chunk_key == KEY_A


def _system() -> AblationSystem:
    return AblationSystem(
        system_key="test_exact_hybrid",
        embedding_model_key="embedding:test:two-dimensional",
        embedding_artifact_manifest_sha256="a" * 64,
        embedding_dimension=2,
    )


def _index() -> ExactVectorIndex:
    return ExactVectorIndex.build(
        model_key="embedding:test:two-dimensional",
        artifact_manifest_sha256="a" * 64,
        representation=_representation(),
        chunk_keys=(KEY_A, KEY_B, KEY_C),
        vectors=((1.0, 0.0), (0.8, 0.6), (0.0, 1.0)),
    )


def _snapshot() -> CorpusSnapshot:
    documents = (
        _document(DOC_A, 1, "Document A"),
        _document(DOC_B, 2, "Document B"),
    )
    chunks = (
        _chunk(KEY_A, DOC_A, "title", "alpha"),
        _chunk(KEY_B, DOC_B, "paragraph", "beta"),
        _chunk(KEY_C, DOC_B, "abstract", "gamma"),
    )
    return build_corpus_snapshot(
        corpus_release_key="corpus:endoviho-rag:v0:20990101:001",
        corpus_manifest_sha256="a" * 64,
        policy_graph_sha256="b" * 64,
        fts_policy_key=FTS_POLICY_KEY,
        retrieval_policy_key=RETRIEVAL_POLICY_KEY,
        anchor_policy_key=ANCHOR_POLICY_KEY,
        validation_receipt_key="receipt:test",
        validation_receipt_sha256="c" * 64,
        documents=documents,
        chunks=chunks,
        anchors=(
            SnapshotAnchor(
                anchor_key=ANCHOR,
                document_key=DOC_B,
                anchor_sha256="d" * 64,
            ),
        ),
    )


def _document(key: str, row: int, title: str) -> SnapshotDocument:
    return SnapshotDocument(
        document_key=key,
        manifest_row=row,
        source_artifact_sha256="e" * 64,
        normalized_document_sha256="f" * 64,
        byte_size=100,
        title=title,
        title_sha256=hashlib.sha256(title.encode()).hexdigest(),
    )


def _chunk(
    key: str,
    document_key: str,
    block_type: BlockType,
    value: str,
) -> SnapshotChunk:
    return SnapshotChunk(
        chunk_key=key,
        document_key=document_key,
        block_type=block_type,
        section_path=("Section",),
        locator_sha256="1" * 64,
        locator_text="fixture locator",
        locator_text_sha256=hashlib.sha256(b"fixture locator").hexdigest(),
        text=value,
        text_sha256=hashlib.sha256(value.encode()).hexdigest(),
    )


def _representation() -> ModelRepresentationContract:
    return ModelRepresentationContract(
        task_kind="embedding",
        dimension=2,
        pooling="cls",
        normalization="l2",
        similarity="cosine",
        query_format="{query}",
        passage_format="{passage}",
        max_sequence_length=8,
        truncation_policy="reject",
        truncation_side="none",
        output_dtype="float32",
    )
