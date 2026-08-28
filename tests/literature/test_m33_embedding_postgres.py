from __future__ import annotations

import os
import re
from collections.abc import Iterator, Sequence
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

from eve_relation_rag.config import get_settings
from eve_relation_rag.db import Base
from eve_relation_rag.db.models import DocumentChunk, DocumentEmbedding
from eve_relation_rag.literature.chunking import TokenSpan
from eve_relation_rag.literature.contracts import EMBEDDING_MODEL_KEY, CorpusManifest
from eve_relation_rag.literature.embeddings import EmbeddingBuildError, embed_candidate_corpus
from eve_relation_rag.literature.ingestion import import_candidate_corpus
from eve_relation_rag.literature.providers import DeterministicFakeEmbeddingProvider

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "literature"
MANIFEST_PATH = FIXTURE_ROOT / "synthetic_corpus_manifest.json"


class WhitespaceOffsetTokenizer:
    @property
    def model_key(self) -> str:
        return "tokenizer:test:whitespace-offset-v1"

    def token_spans(self, text: str) -> tuple[TokenSpan, ...]:
        return tuple(
            TokenSpan(token_index=index, char_start=match.start(), char_end=match.end())
            for index, match in enumerate(re.finditer(r"\S+", text))
        )


class WrongCountProvider:
    @property
    def model_key(self) -> str:
        return EMBEDDING_MODEL_KEY

    @property
    def dimension(self) -> int:
        return 384

    @property
    def artifact_manifest_sha256(self) -> str:
        return "f" * 64

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        del texts
        return ()

    def embed_query(self, text: str) -> Sequence[float]:
        del text
        return (1.0, *([0.0] * 383))


@pytest.fixture(scope="module")
def postgres_engine() -> Iterator[Engine]:
    database_url = os.environ.get("EVE_RAG_TEST_DATABASE_URL", get_settings().database_url)
    admin_engine = create_engine(database_url, poolclass=NullPool)
    schema = f"test_m33_{uuid4().hex}"
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
        manifest = CorpusManifest.model_validate_json(MANIFEST_PATH.read_text())
        import_candidate_corpus(
            engine,
            manifest=manifest,
            import_root=FIXTURE_ROOT,
            tokenizer=WhitespaceOffsetTokenizer(),
            approved_manifest_sha256=manifest.manifest_sha256,
            importer_code_sha256="e" * 64,
            model_artifact_manifest_sha256="f" * 64,
        )
        yield engine
    finally:
        engine.dispose()
        event.remove(admin_engine, "connect", set_fixture_search_path)
        with admin_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin_engine.dispose()


def test_provider_failure_is_atomic_before_any_embedding_write(postgres_engine: Engine) -> None:
    manifest = CorpusManifest.model_validate_json(MANIFEST_PATH.read_text())

    with pytest.raises(EmbeddingBuildError, match="one vector per chunk"):
        embed_candidate_corpus(
            postgres_engine,
            corpus_release_key=manifest.corpus_release_key,
            provider=WrongCountProvider(),
        )

    with Session(postgres_engine) as session:
        assert session.scalar(select(func.count()).select_from(DocumentEmbedding)) == 0


def test_embedding_build_is_validated_atomic_and_idempotent(postgres_engine: Engine) -> None:
    manifest = CorpusManifest.model_validate_json(MANIFEST_PATH.read_text())
    provider = DeterministicFakeEmbeddingProvider()

    first = embed_candidate_corpus(
        postgres_engine,
        corpus_release_key=manifest.corpus_release_key,
        provider=provider,
        batch_size=5,
    )
    replay = embed_candidate_corpus(
        postgres_engine,
        corpus_release_key=manifest.corpus_release_key,
        provider=provider,
        batch_size=5,
    )

    assert first.inserted_count == first.chunk_count
    assert first.reused_count == 0
    assert replay.inserted_count == 0
    assert replay.reused_count == first.chunk_count
    assert replay.embeddings_sha256 == first.embeddings_sha256

    with Session(postgres_engine) as session:
        chunk_count = session.scalar(select(func.count()).select_from(DocumentChunk))
        embedding_count = session.scalar(select(func.count()).select_from(DocumentEmbedding))
        assert embedding_count == chunk_count == first.chunk_count
        assert all(
            len(embedding.embedding_sha256) == 64
            for embedding in session.scalars(select(DocumentEmbedding))
        )


def test_embedding_build_rejects_provider_for_a_different_model_artifact(
    postgres_engine: Engine,
) -> None:
    class WrongArtifactProvider(DeterministicFakeEmbeddingProvider):
        @property
        def artifact_manifest_sha256(self) -> str:
            return "e" * 64

    manifest = CorpusManifest.model_validate_json(MANIFEST_PATH.read_text())

    with pytest.raises(EmbeddingBuildError, match="artifact manifest"):
        embed_candidate_corpus(
            postgres_engine,
            corpus_release_key=manifest.corpus_release_key,
            provider=WrongArtifactProvider(),
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
