from __future__ import annotations

import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from eve_relation_rag.application.literature import LiteratureRetrievalService
from eve_relation_rag.config import get_settings
from eve_relation_rag.db import Base
from eve_relation_rag.db.models import (
    CorpusDocumentMembership,
    CorpusRelease,
    CorpusValidationReceipt,
    Document,
    DocumentAnchor,
    DocumentChunk,
    DocumentEmbedding,
)
from eve_relation_rag.literature.benchmarking import BenchmarkDefinition, run_benchmark
from eve_relation_rag.literature.chunking import TokenSpan
from eve_relation_rag.literature.contracts import (
    CorpusManifest,
    KeywordAnchor,
    LiteratureRetrievalInvocation,
    LiteratureRetrievalRequest,
    RetrievedChunks,
)
from eve_relation_rag.literature.embeddings import embed_candidate_corpus
from eve_relation_rag.literature.hashing import canonical_json_sha256
from eve_relation_rag.literature.ingestion import import_candidate_corpus
from eve_relation_rag.literature.providers import DeterministicFakeEmbeddingProvider
from tests.support.m3 import build_trusted_receipt_fixture

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "literature"
MANIFEST_PATH = FIXTURE_ROOT / "synthetic_corpus_manifest.json"
BENCHMARK_PATH = FIXTURE_ROOT / "synthetic_benchmark.json"


class WhitespaceOffsetTokenizer:
    @property
    def model_key(self) -> str:
        return "tokenizer:test:whitespace-offset-v1"

    def token_spans(self, text: str) -> tuple[TokenSpan, ...]:
        return tuple(
            TokenSpan(token_index=index, char_start=match.start(), char_end=match.end())
            for index, match in enumerate(re.finditer(r"\S+", text))
        )


@pytest.fixture(scope="module")
def published_corpus() -> Iterator[tuple[Engine, CorpusManifest, KeywordAnchor]]:
    database_url = os.environ.get("EVE_RAG_TEST_DATABASE_URL", get_settings().database_url)
    admin_engine = create_engine(database_url, poolclass=NullPool)
    schema = f"test_m34_{uuid4().hex}"
    try:
        with admin_engine.begin() as connection:
            connection.execute(CreateSchema(schema))
    except OperationalError as exc:
        admin_engine.dispose()
        pytest.skip(f"PostgreSQL integration database is unavailable: {exc.orig}")

    with admin_engine.connect() as connection:
        connection.exec_driver_sql(f'SET search_path TO "{schema}", public')
        connection.commit()
        _upgrade_to_head(connection)

    def set_fixture_search_path(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        try:
            cursor.execute(f'SET search_path TO "{schema}", public')
        finally:
            cursor.close()
        dbapi_connection.commit()  # type: ignore[union-attr]

    event.listen(admin_engine, "connect", set_fixture_search_path)
    admin_engine.dispose()
    engine = admin_engine.execution_options(schema_translate_map={None: schema})
    manifest = CorpusManifest.model_validate_json(MANIFEST_PATH.read_text())
    provider = DeterministicFakeEmbeddingProvider()
    try:
        import_candidate_corpus(
            engine,
            manifest=manifest,
            import_root=FIXTURE_ROOT,
            tokenizer=WhitespaceOffsetTokenizer(),
            approved_manifest_sha256=manifest.manifest_sha256,
            importer_code_sha256="e" * 64,
            model_artifact_manifest_sha256="f" * 64,
        )
        embed_candidate_corpus(
            engine,
            corpus_release_key=manifest.corpus_release_key,
            provider=provider,
        )
        keyword_anchor = _publish_with_anchor(engine, manifest)
        yield engine, manifest, keyword_anchor
    finally:
        engine.dispose()
        event.remove(admin_engine, "connect", set_fixture_search_path)
        with admin_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin_engine.dispose()


def test_hybrid_retrieval_returns_typed_checksum_bound_chunks(
    published_corpus: tuple[Engine, CorpusManifest, KeywordAnchor],
) -> None:
    engine, manifest, _anchor = published_corpus
    service = LiteratureRetrievalService(engine, DeterministicFakeEmbeddingProvider())

    result = service.retrieve(
        LiteratureRetrievalInvocation(
            request=LiteratureRetrievalRequest(
                request_schema_version="literature-retrieval-request-v1",
                corpus_release_key=manifest.corpus_release_key,
                question="synthetic retrieval methods",
                top_k=5,
            )
        )
    )

    assert isinstance(result, RetrievedChunks)
    assert result.returned_count == 5
    assert result.retrieval_executed is True
    assert result.anchor_mode == "none"
    assert [chunk.citation_id for chunk in result.chunks] == ["D1", "D2", "D3", "D4", "D5"]
    assert all(
        chunk.fts_rank is not None
        or chunk.vector_rank is not None
        or chunk.summary_vector_rank is not None
        for chunk in result.chunks
    )
    assert any(chunk.summary_vector_rank is not None for chunk in result.chunks)


def test_retrieval_rejects_provider_for_a_different_model_artifact(
    published_corpus: tuple[Engine, CorpusManifest, KeywordAnchor],
) -> None:
    class WrongArtifactProvider(DeterministicFakeEmbeddingProvider):
        @property
        def artifact_manifest_sha256(self) -> str:
            return "e" * 64

    engine, manifest, _anchor = published_corpus
    service = LiteratureRetrievalService(engine, WrongArtifactProvider())

    result = service.retrieve(
        LiteratureRetrievalInvocation(
            request=LiteratureRetrievalRequest(
                request_schema_version="literature-retrieval-request-v1",
                corpus_release_key=manifest.corpus_release_key,
                question="synthetic retrieval methods",
                top_k=5,
            )
        )
    )

    assert result.status == "error"
    assert result.code == "embedding_model_mismatch"
    assert result.retrieval_executed is False


def test_stopword_only_fts_branch_is_recorded_while_vector_retrieval_continues(
    published_corpus: tuple[Engine, CorpusManifest, KeywordAnchor],
) -> None:
    engine, manifest, _anchor = published_corpus
    service = LiteratureRetrievalService(engine, DeterministicFakeEmbeddingProvider())

    result = service.retrieve(
        LiteratureRetrievalInvocation(
            request=LiteratureRetrievalRequest(
                request_schema_version="literature-retrieval-request-v1",
                corpus_release_key=manifest.corpus_release_key,
                question="the and or",
                top_k=3,
            )
        )
    )

    assert isinstance(result, RetrievedChunks)
    assert "fts_no_indexable_terms" in result.warnings
    assert all(
        chunk.fts_rank is None
        and (chunk.vector_rank is not None or chunk.summary_vector_rank is not None)
        for chunk in result.chunks
    )


def test_anchor_first_retrieval_precedes_corpus_fill_and_records_matches(
    published_corpus: tuple[Engine, CorpusManifest, KeywordAnchor],
) -> None:
    engine, manifest, keyword_anchor = published_corpus
    service = LiteratureRetrievalService(engine, DeterministicFakeEmbeddingProvider())

    result = service.retrieve(
        LiteratureRetrievalInvocation(
            request=LiteratureRetrievalRequest(
                request_schema_version="literature-retrieval-request-v1",
                corpus_release_key=manifest.corpus_release_key,
                question="synthetic retrieval",
                top_k=20,
            ),
            system_anchors=(keyword_anchor,),
        )
    )

    assert isinstance(result, RetrievedChunks)
    tiers = [chunk.retrieval_tier for chunk in result.chunks]
    assert result.anchor_mode == "anchored_then_corpus_fill"
    assert "anchored" in tiers
    assert "corpus_fill" in tiers
    assert tiers == sorted(tiers, key=lambda tier: 0 if tier == "anchored" else 1)
    assert all(
        chunk.matched_anchors == (keyword_anchor.anchor_key,)
        for chunk in result.chunks
        if chunk.retrieval_tier == "anchored"
    )


def test_unknown_corpus_and_unresolved_anchor_fail_closed(
    published_corpus: tuple[Engine, CorpusManifest, KeywordAnchor],
) -> None:
    engine, manifest, _anchor = published_corpus
    service = LiteratureRetrievalService(engine, DeterministicFakeEmbeddingProvider())
    unknown = service.retrieve(
        LiteratureRetrievalInvocation(
            request=LiteratureRetrievalRequest(
                request_schema_version="literature-retrieval-request-v1",
                corpus_release_key="corpus:endoviho-rag:v0:20990101:999",
                question="synthetic",
            )
        )
    )
    unresolved_anchor = KeywordAnchor(
        anchor_key=f"anchor:sha256:{'0' * 64}",
        anchor_type="keyword",
        phrase="missing curated anchor",
    )
    unresolved = service.retrieve(
        LiteratureRetrievalInvocation(
            request=LiteratureRetrievalRequest(
                request_schema_version="literature-retrieval-request-v1",
                corpus_release_key=manifest.corpus_release_key,
                question="synthetic",
            ),
            system_anchors=(unresolved_anchor,),
        )
    )

    assert unknown.status == "error"
    assert unknown.code == "corpus_not_found"
    assert unknown.retrieval_executed is False
    assert unresolved.status == "error"
    assert unresolved.code == "anchor_invalid"
    assert unresolved.retrieval_executed is False


def test_committed_deterministic_benchmark_meets_m3_thresholds(
    published_corpus: tuple[Engine, CorpusManifest, KeywordAnchor],
) -> None:
    engine, _manifest, _anchor = published_corpus
    definition = BenchmarkDefinition.model_validate_json(BENCHMARK_PATH.read_text())
    service = LiteratureRetrievalService(engine, DeterministicFakeEmbeddingProvider())

    report = run_benchmark(service, definition)

    assert report.passed is True, report.model_dump_json(indent=2)
    assert report.recall_at_5 >= "0.800000000000"
    assert report.recall_at_10 >= "0.900000000000"
    assert report.citation_id_validity == "1.000000000000"
    assert report.locator_validity == "1.000000000000"


def _publish_with_anchor(engine: Engine, manifest: CorpusManifest) -> KeywordAnchor:
    key = "anchor:sha256:8900b9908e72b5e1370da601a8e1565bbacd9325e09c46e10c28ba0b5eff0e62"
    anchor = KeywordAnchor(anchor_key=key, anchor_type="keyword", phrase="synthetic fixture")
    with Session(engine) as session, session.begin():
        release = session.scalar(
            select(CorpusRelease).where(
                CorpusRelease.corpus_release_key == manifest.corpus_release_key
            )
        )
        document = session.scalar(
            select(Document).where(
                Document.document_key == manifest.documents[0].expected_document_key
            )
        )
        assert release is not None and document is not None
        membership = session.get(CorpusDocumentMembership, (release.id, document.id))
        assert membership is not None
        session.add(
            DocumentAnchor(
                anchor_key=anchor.anchor_key,
                release_id=release.id,
                document_id=document.id,
                anchor_type="keyword",
                locus_key=None,
                assembly_key=None,
                lineage_snapshot_key=None,
                lineage_term_key=None,
                method_definition_key=None,
                target_document_key=None,
                doi=None,
                pmid=None,
                pmcid=None,
                keyword_phrase=anchor.phrase,
                manifest_row=membership.manifest_row,
                curation_method="synthetic-m34-fixture-v1",
                source_locator={"fixture": "test_m34_retrieval_postgres"},
                anchor_sha256=canonical_json_sha256(anchor),
            )
        )
        session.flush()
        chunk_count = session.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.release_id == release.id)
        )
        embedding_count = session.scalar(
            select(func.count())
            .select_from(DocumentEmbedding)
            .where(DocumentEmbedding.release_id == release.id)
        )
        relevant_chunk_key = session.scalar(
            select(DocumentChunk.chunk_key)
            .where(DocumentChunk.release_id == release.id)
            .order_by(DocumentChunk.chunk_key)
            .limit(1)
        )
        assert chunk_count is not None and embedding_count is not None
        assert relevant_chunk_key is not None
        receipt_values = build_trusted_receipt_fixture(
            corpus_release_key=release.corpus_release_key,
            manifest_sha256=release.manifest_sha256,
            policy_graph_sha256=release.policy_graph_sha256,
            model_artifact_manifest_sha256="f" * 64,
            document_count=manifest.document_count,
            chunk_count=chunk_count,
            embedding_count=embedding_count,
            anchor_count=1,
            relevant_chunk_key=relevant_chunk_key,
            seed="m34",
        )
        session.add(CorpusValidationReceipt(release_id=release.id, **receipt_values))
        session.flush()
        release.status = "validated"
        session.flush()
        release.status = "published"
        release.published_at = datetime.now(UTC)
        session.flush()
    return anchor


def _upgrade_to_head(connection: object) -> None:
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    revisions = list(reversed(list(script.walk_revisions(base="base", head="heads"))))
    for revision in revisions:
        with connection.begin():  # type: ignore[union-attr]
            context = MigrationContext.configure(
                connection,  # type: ignore[arg-type]
                opts={"target_metadata": Base.metadata},
            )
            with Operations.context(context):
                revision.module.upgrade()
