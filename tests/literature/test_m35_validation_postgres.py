from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event, func, select, update
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from eve_relation_rag.application.literature import CandidateBenchmarkService
from eve_relation_rag.config import get_settings
from eve_relation_rag.db import Base
from eve_relation_rag.db.models import (
    CorpusImportRun,
    CorpusRelease,
    CorpusValidationReceipt,
    DocumentAnchor,
    DocumentChunk,
    DocumentEmbedding,
)
from eve_relation_rag.literature.anchors import CorpusAnchorManifest, import_candidate_anchors
from eve_relation_rag.literature.benchmarking import (
    BenchmarkDefinition,
    BenchmarkRuntimeFingerprint,
    build_benchmark_definition,
    run_benchmark,
)
from eve_relation_rag.literature.chunking import TokenSpan
from eve_relation_rag.literature.contracts import CorpusManifest
from eve_relation_rag.literature.gate import PublishedCorpusGate
from eve_relation_rag.literature.hashing import (
    canonical_json_sha256,
    canonical_manifest_sha256,
)
from eve_relation_rag.literature.ingestion import import_candidate_corpus
from eve_relation_rag.literature.local_bge import LocalBgeProvider
from eve_relation_rag.literature.providers import DeterministicFakeEmbeddingProvider
from eve_relation_rag.literature.publication import (
    CorpusPublicationError,
    publish_corpus,
    record_pilot_validation_receipt,
)
from eve_relation_rag.literature.validation import RebuildValidationReport, validate_corpus_rebuild

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "literature"
MANIFEST_PATH = FIXTURE_ROOT / "synthetic_corpus_manifest.json"
BENCHMARK_PATH = FIXTURE_ROOT / "synthetic_benchmark.json"
ANCHOR_MANIFEST_PATH = FIXTURE_ROOT / "synthetic_anchor_manifest.json"


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
def postgres_engine() -> Iterator[Engine]:
    database_url = os.environ.get("EVE_RAG_TEST_DATABASE_URL", get_settings().database_url)
    admin_engine = create_engine(database_url, poolclass=NullPool)
    schema = f"test_m35_{uuid4().hex}"
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
    try:
        yield engine
    finally:
        engine.dispose()
        event.remove(admin_engine, "connect", set_fixture_search_path)
        with admin_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin_engine.dispose()


def test_atomic_stage_with_embeddings_rebuilds_exactly(postgres_engine: Engine) -> None:
    manifest = CorpusManifest.model_validate_json(MANIFEST_PATH.read_text())
    provider = DeterministicFakeEmbeddingProvider()

    import_report = import_candidate_corpus(
        postgres_engine,
        manifest=manifest,
        import_root=FIXTURE_ROOT,
        tokenizer=WhitespaceOffsetTokenizer(),
        approved_manifest_sha256=manifest.manifest_sha256,
        importer_code_sha256="e" * 64,
        model_artifact_manifest_sha256="f" * 64,
        embedding_provider=provider,
    )
    anchor_manifest = CorpusAnchorManifest.model_validate_json(ANCHOR_MANIFEST_PATH.read_text())
    anchor_report = import_candidate_anchors(
        postgres_engine,
        manifest=anchor_manifest,
        approved_anchor_manifest_sha256=anchor_manifest.anchor_manifest_sha256,
    )
    anchor_replay = import_candidate_anchors(
        postgres_engine,
        manifest=anchor_manifest,
        approved_anchor_manifest_sha256=anchor_manifest.anchor_manifest_sha256,
    )
    second_manifest_payload = manifest.model_dump(mode="python")
    second_manifest_payload["corpus_release_key"] = (
        "corpus:endoviho-rag:v0:20990101:002"
    )
    second_manifest_payload["manifest_sha256"] = "0" * 64
    second_manifest_payload["manifest_sha256"] = canonical_manifest_sha256(
        second_manifest_payload
    )
    second_manifest = CorpusManifest.model_validate(second_manifest_payload)
    second_import = import_candidate_corpus(
        postgres_engine,
        manifest=second_manifest,
        import_root=FIXTURE_ROOT,
        tokenizer=WhitespaceOffsetTokenizer(),
        approved_manifest_sha256=second_manifest.manifest_sha256,
        importer_code_sha256="e" * 64,
        model_artifact_manifest_sha256="f" * 64,
        embedding_provider=provider,
    )
    second_anchor_payload = anchor_manifest.model_dump(mode="python")
    second_anchor_payload["corpus_release_key"] = second_manifest.corpus_release_key
    second_anchor_payload["corpus_manifest_sha256"] = second_manifest.manifest_sha256
    second_anchor_payload["anchor_manifest_sha256"] = "0" * 64
    second_anchor_hash_payload = dict(second_anchor_payload)
    del second_anchor_hash_payload["anchor_manifest_sha256"]
    second_anchor_payload["anchor_manifest_sha256"] = canonical_json_sha256(
        second_anchor_hash_payload
    )
    second_anchor_manifest = CorpusAnchorManifest.model_validate(second_anchor_payload)
    second_anchor_report = import_candidate_anchors(
        postgres_engine,
        manifest=second_anchor_manifest,
        approved_anchor_manifest_sha256=second_anchor_manifest.anchor_manifest_sha256,
    )
    validation = validate_corpus_rebuild(
        postgres_engine,
        manifest=manifest,
        import_root=FIXTURE_ROOT,
        tokenizer=WhitespaceOffsetTokenizer(),
        provider=provider,
        anchor_manifest=anchor_manifest,
    )
    benchmark = run_benchmark(
        CandidateBenchmarkService(postgres_engine, provider, validation),
        BenchmarkDefinition.model_validate_json(BENCHMARK_PATH.read_text()),
    )

    assert import_report.embedding_count == import_report.chunk_count
    assert anchor_report.inserted_count == 1
    assert anchor_replay.reused_count == 1
    assert second_import.embedding_count == second_import.chunk_count
    assert second_anchor_report.inserted_count == 1
    assert import_report.embeddings_sha256 is not None
    assert validation.passed is True
    assert validation.findings == ()
    assert validation.document_count == 3
    assert validation.chunk_count == validation.embedding_count
    assert len(validation.rebuild_sha256) == 64
    assert benchmark.passed is True

    with Session(postgres_engine) as session:
        assert session.scalar(select(func.count()).select_from(CorpusImportRun)) == 2
        assert session.scalar(select(func.count()).select_from(DocumentChunk)) == session.scalar(
            select(func.count()).select_from(DocumentEmbedding)
        )


def test_anchor_provenance_mutations_fail_rebuild_and_are_restored(
    postgres_engine: Engine,
) -> None:
    manifest = CorpusManifest.model_validate_json(MANIFEST_PATH.read_text())
    anchor_manifest = CorpusAnchorManifest.model_validate_json(ANCHOR_MANIFEST_PATH.read_text())
    provider = DeterministicFakeEmbeddingProvider()
    tokenizer = WhitespaceOffsetTokenizer()

    with Session(postgres_engine) as session:
        anchor = session.scalar(
            select(DocumentAnchor)
            .join(CorpusRelease, CorpusRelease.id == DocumentAnchor.release_id)
            .where(CorpusRelease.corpus_release_key == manifest.corpus_release_key)
        )
        assert anchor is not None
        anchor_id = anchor.id
        anchor_key = anchor.anchor_key
        original_source_locator = dict(anchor.source_locator)
        original_curation_method = anchor.curation_method

    def set_provenance(*, source_locator: Mapping[str, object], curation_method: str) -> None:
        with Session(postgres_engine) as session, session.begin():
            session.execute(
                update(DocumentAnchor)
                .where(DocumentAnchor.id == anchor_id)
                .values(
                    source_locator=dict(source_locator),
                    curation_method=curation_method,
                )
            )

    def rebuild() -> RebuildValidationReport:
        return validate_corpus_rebuild(
            postgres_engine,
            manifest=manifest,
            import_root=FIXTURE_ROOT,
            tokenizer=tokenizer,
            provider=provider,
            anchor_manifest=anchor_manifest,
        )

    try:
        for source_locator, curation_method in (
            ({"tampered": True}, original_curation_method),
            (original_source_locator, "curation:tampered-provenance"),
        ):
            set_provenance(
                source_locator=source_locator,
                curation_method=curation_method,
            )
            validation = rebuild()
            assert validation.passed is False
            assert f"anchor_manifest_entry_invalid:{anchor_key}" in validation.findings
            assert "anchor_rebuild_mismatch" in validation.findings
            set_provenance(
                source_locator=original_source_locator,
                curation_method=original_curation_method,
            )
            assert rebuild().passed is True
    finally:
        set_provenance(
            source_locator=original_source_locator,
            curation_method=original_curation_method,
        )


def test_explicit_publication_requires_and_replays_exact_trusted_receipt(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = CorpusManifest.model_validate_json(MANIFEST_PATH.read_text())
    anchor_manifest = CorpusAnchorManifest.model_validate_json(ANCHOR_MANIFEST_PATH.read_text())
    fake_provider = DeterministicFakeEmbeddingProvider()
    tokenizer = WhitespaceOffsetTokenizer()
    monkeypatch.setattr(
        LocalBgeProvider,
        "embed_documents",
        lambda _self, texts: fake_provider.embed_documents(texts),
    )
    monkeypatch.setattr(
        LocalBgeProvider,
        "embed_query",
        lambda _self, text: fake_provider.embed_query(text),
    )
    monkeypatch.setattr(
        LocalBgeProvider,
        "token_spans",
        lambda _self, text: tokenizer.token_spans(text),
    )
    local_bge = object.__new__(LocalBgeProvider)
    local_bge._artifact_manifest_sha256 = "f" * 64  # noqa: SLF001 - test-only exact adapter.
    deterministic_definition = BenchmarkDefinition.model_validate_json(
        BENCHMARK_PATH.read_text()
    )
    pilot_definition = build_benchmark_definition(
        tier="pilot_release",
        corpus_release_key=manifest.corpus_release_key,
        corpus_manifest_sha256=manifest.manifest_sha256,
        questions=deterministic_definition.questions,
    )
    runtime = BenchmarkRuntimeFingerprint(
        python_version="3.12.test",
        platform_system="test",
        platform_release="test",
        platform_machine="test",
        uv_lock_sha256="a" * 64,
        postgresql_version="PostgreSQL 16 test",
        pgvector_version="0.8.test",
    )

    receipt = record_pilot_validation_receipt(
        postgres_engine,
        manifest=manifest,
        import_root=FIXTURE_ROOT,
        anchor_manifest=anchor_manifest,
        benchmark_definition=pilot_definition,
        runtime_fingerprint=runtime,
        validator_code_sha256="e" * 64,
        provider=local_bge,
    )
    receipt_replay = record_pilot_validation_receipt(
        postgres_engine,
        manifest=manifest,
        import_root=FIXTURE_ROOT,
        anchor_manifest=anchor_manifest,
        benchmark_definition=pilot_definition,
        runtime_fingerprint=runtime,
        validator_code_sha256="e" * 64,
        provider=local_bge,
    )

    with Session(postgres_engine) as session, session.begin():
        release = session.scalar(
            select(CorpusRelease).where(
                CorpusRelease.corpus_release_key == manifest.corpus_release_key
            )
        )
        assert release is not None
        release_id = release.id
        assert release.status == "validated"

    with Session(postgres_engine) as session:
        with pytest.raises(
            DBAPIError,
            match="validated, published, or retired corpus content is immutable",
        ):
            session.execute(
                update(DocumentChunk)
                .where(DocumentChunk.release_id == release_id)
                .values(locator_text="changed after validation")
            )
            session.commit()
        session.rollback()

    with Session(postgres_engine) as session:
        session.add(
            CorpusValidationReceipt(
                receipt_key=f"corpus-receipt:sha256:{'1' * 64}",
                release_id=release_id,
                status="failed",
                trusted=False,
                manifest_sha256=manifest.manifest_sha256,
                policy_graph_sha256=pilot_definition.benchmark_manifest_sha256,
                rebuild_sha256="2" * 64,
                benchmark_sha256="3" * 64,
                receipt_sha256="4" * 64,
                validation_report={"synthetic_fixture": True},
            )
        )
        with pytest.raises(
            DBAPIError,
            match="validated, published, or retired corpus content is immutable",
        ):
            session.commit()
        session.rollback()

    first = publish_corpus(
        postgres_engine,
        corpus_release_key=manifest.corpus_release_key,
        expected_manifest_sha256=manifest.manifest_sha256,
        expected_receipt_sha256=receipt.receipt_sha256,
    )
    replay = publish_corpus(
        postgres_engine,
        corpus_release_key=manifest.corpus_release_key,
        expected_manifest_sha256=manifest.manifest_sha256,
        expected_receipt_sha256=receipt.receipt_sha256,
    )
    capability = PublishedCorpusGate(postgres_engine).authorize(manifest.corpus_release_key)

    assert first.status == "published"
    assert first.replayed is False
    assert receipt.replayed is False
    assert receipt_replay.replayed is True
    assert receipt_replay.receipt_sha256 == receipt.receipt_sha256
    assert replay.replayed is True
    assert replay.published_at == first.published_at
    assert capability.corpus_release_key == manifest.corpus_release_key


def test_candidate_cannot_be_published_directly_even_with_exact_receipt(
    postgres_engine: Engine,
) -> None:
    candidate_key = "corpus:endoviho-rag:v0:20990101:002"
    with Session(postgres_engine) as session, session.begin():
        release = session.scalar(
            select(CorpusRelease).where(CorpusRelease.corpus_release_key == candidate_key)
        )
        assert release is not None
        assert release.status == "candidate"
        candidate_manifest_sha256 = release.manifest_sha256
        session.add(
            CorpusValidationReceipt(
                receipt_key=f"corpus-receipt:sha256:{'5' * 64}",
                release_id=release.id,
                status="passed",
                trusted=True,
                manifest_sha256=release.manifest_sha256,
                policy_graph_sha256=release.policy_graph_sha256,
                rebuild_sha256="6" * 64,
                benchmark_sha256="7" * 64,
                receipt_sha256="8" * 64,
                validation_report={"synthetic_fixture": True},
            )
        )

    with pytest.raises(CorpusPublicationError, match="publishable lifecycle state"):
        publish_corpus(
            postgres_engine,
            corpus_release_key=candidate_key,
            expected_manifest_sha256=candidate_manifest_sha256,
            expected_receipt_sha256="8" * 64,
        )

    with Session(postgres_engine) as session:
        with pytest.raises(DBAPIError, match="invalid corpus release lifecycle transition"):
            session.execute(
                update(CorpusRelease)
                .where(CorpusRelease.corpus_release_key == candidate_key)
                .values(status="published", published_at=func.now())
            )
            session.commit()
        session.rollback()

    with Session(postgres_engine) as session, session.begin():
        session.execute(
            update(CorpusRelease)
            .where(CorpusRelease.corpus_release_key == candidate_key)
            .values(status="validated")
        )

    with pytest.raises(CorpusPublicationError, match="receipt evidence is invalid"):
        publish_corpus(
            postgres_engine,
            corpus_release_key=candidate_key,
            expected_manifest_sha256=candidate_manifest_sha256,
            expected_receipt_sha256="8" * 64,
        )


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
