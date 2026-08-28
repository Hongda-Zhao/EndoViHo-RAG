from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
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
from eve_relation_rag.db.models import (
    CorpusImportLedger,
    CorpusImportRun,
    CorpusRelease,
    Document,
    DocumentChunk,
    DocumentEmbedding,
)
from eve_relation_rag.literature.chunking import TokenSpan
from eve_relation_rag.literature.contracts import CorpusManifest
from eve_relation_rag.literature.ingestion import (
    CorpusImportError,
    import_candidate_corpus,
    verify_import_root,
    verify_source_artifact,
)
from eve_relation_rag.literature.providers import DeterministicFakeEmbeddingProvider

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "literature"
MANIFEST_PATH = FIXTURE_ROOT / "synthetic_corpus_manifest.json"
CODE_SHA256 = "e" * 64
MODEL_ARTIFACT_MANIFEST_SHA256 = "f" * 64


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
    schema = f"test_m32_{uuid4().hex}"
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


def test_manifest_first_candidate_import_is_atomic_and_idempotent(
    postgres_engine: Engine,
) -> None:
    manifest = CorpusManifest.model_validate_json(MANIFEST_PATH.read_text())
    tokenizer = WhitespaceOffsetTokenizer()

    first = import_candidate_corpus(
        postgres_engine,
        manifest=manifest,
        import_root=FIXTURE_ROOT,
        tokenizer=tokenizer,
        approved_manifest_sha256=manifest.manifest_sha256,
        importer_code_sha256=CODE_SHA256,
        model_artifact_manifest_sha256=MODEL_ARTIFACT_MANIFEST_SHA256,
    )
    replay = import_candidate_corpus(
        postgres_engine,
        manifest=manifest,
        import_root=FIXTURE_ROOT,
        tokenizer=tokenizer,
        approved_manifest_sha256=manifest.manifest_sha256,
        importer_code_sha256=CODE_SHA256,
        model_artifact_manifest_sha256=MODEL_ARTIFACT_MANIFEST_SHA256,
    )

    assert first.replayed is False
    assert first.imported_documents == 3
    assert first.reused_documents == 0
    assert first.chunk_count >= 3
    assert replay.replayed is True
    assert replay.run_key == first.run_key
    assert replay.document_keys_sha256 == first.document_keys_sha256
    assert replay.chunk_keys_sha256 == first.chunk_keys_sha256

    with Session(postgres_engine) as session:
        release = session.scalar(
            select(CorpusRelease).where(
                CorpusRelease.corpus_release_key == manifest.corpus_release_key
            )
        )
        assert release is not None
        assert release.status == "candidate"
        assert session.scalar(select(func.count()).select_from(Document)) == 3
        assert session.scalar(select(func.count()).select_from(DocumentChunk)) == first.chunk_count
        assert session.scalar(select(func.count()).select_from(DocumentEmbedding)) == 0
        assert session.scalar(select(func.count()).select_from(CorpusImportRun)) == 1
        assert session.scalar(select(func.count()).select_from(CorpusImportLedger)) == 3
        assert (
            session.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(DocumentChunk.fts_document.is_(None))
            )
            == 0
        )


def test_import_refuses_unapproved_manifest_before_database_mutation(
    postgres_engine: Engine,
) -> None:
    manifest = CorpusManifest.model_validate_json(MANIFEST_PATH.read_text())

    with pytest.raises(CorpusImportError, match="approved_manifest_sha256"):
        import_candidate_corpus(
            postgres_engine,
            manifest=manifest,
            import_root=FIXTURE_ROOT,
            tokenizer=WhitespaceOffsetTokenizer(),
            approved_manifest_sha256="0" * 64,
            importer_code_sha256=CODE_SHA256,
            model_artifact_manifest_sha256=MODEL_ARTIFACT_MANIFEST_SHA256,
        )


def test_atomic_stage_rejects_cli_artifact_sha_that_differs_from_provider(
    postgres_engine: Engine,
) -> None:
    manifest = CorpusManifest.model_validate_json(MANIFEST_PATH.read_text())
    with Session(postgres_engine) as session:
        run_count_before = session.scalar(select(func.count()).select_from(CorpusImportRun))

    with pytest.raises(CorpusImportError, match="provider artifact manifest"):
        import_candidate_corpus(
            postgres_engine,
            manifest=manifest,
            import_root=FIXTURE_ROOT,
            tokenizer=WhitespaceOffsetTokenizer(),
            approved_manifest_sha256=manifest.manifest_sha256,
            importer_code_sha256=CODE_SHA256,
            model_artifact_manifest_sha256="0" * 64,
            embedding_provider=DeterministicFakeEmbeddingProvider(),
        )

    with Session(postgres_engine) as session:
        assert session.scalar(select(func.count()).select_from(CorpusImportRun)) == run_count_before


def test_source_verification_rejects_symlinks_even_when_bytes_match(tmp_path: Path) -> None:
    raw = json.loads(MANIFEST_PATH.read_text())
    manifest = CorpusManifest.model_validate_json(MANIFEST_PATH.read_text())
    source = FIXTURE_ROOT / raw["documents"][0]["relative_path"]
    link = tmp_path / raw["documents"][0]["relative_path"]
    link.symlink_to(source)

    with pytest.raises(CorpusImportError, match="symbolic link"):
        verify_source_artifact(manifest.documents[0], tmp_path)


def test_import_root_rejects_unmanifested_document_files(tmp_path: Path) -> None:
    manifest = CorpusManifest.model_validate_json(MANIFEST_PATH.read_text())
    for document in manifest.documents:
        source = FIXTURE_ROOT / document.relative_path
        destination = tmp_path / document.relative_path
        destination.write_bytes(source.read_bytes())
    (tmp_path / "unmanifested.txt").write_text("not approved", encoding="utf-8")

    with pytest.raises(CorpusImportError, match="unmanifested.txt"):
        verify_import_root(manifest, tmp_path)


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
