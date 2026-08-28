from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event, inspect, select, text, update
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from eve_relation_rag.config import get_settings
from eve_relation_rag.db import Base
from eve_relation_rag.db.models import (
    CorpusDocumentMembership,
    CorpusImportLedger,
    CorpusImportRun,
    CorpusRelease,
    CorpusValidationReceipt,
    Document,
    DocumentAnchor,
    DocumentChunk,
    DocumentEmbedding,
    EmbeddingModel,
    LiteraturePolicy,
)
from eve_relation_rag.literature.candidate_gate import ValidatedCandidateGate
from eve_relation_rag.literature.contracts import (
    ANCHOR_POLICY_KEY,
    CHUNKING_POLICY_KEY,
    EMBEDDING_MODEL_KEY,
    FTS_POLICY_KEY,
    PARSER_POLICY_KEY,
    RETRIEVAL_POLICY_KEY,
)
from eve_relation_rag.literature.errors import LiteratureRetrievalRefusal
from eve_relation_rag.literature.gate import PublishedCorpusGate
from eve_relation_rag.literature.hashing import canonical_json_sha256
from eve_relation_rag.literature.validation import RebuildValidationReport
from tests.support.m3 import build_trusted_receipt_fixture

ROOT = Path(__file__).resolve().parents[2]
CORPUS_KEY = "corpus:endoviho-rag:v0:20990102:001"
NOW = datetime(2026, 8, 27, 12, tzinfo=UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _fixture_policy_sha256(policy_key: str) -> str:
    return canonical_json_sha256({"policy_key": policy_key})


def _fixture_policy_graph_sha256(
    *,
    anchor_policy_key: str = ANCHOR_POLICY_KEY,
    anchor_policy_sha256: str | None = None,
) -> str:
    policy_keys = {
        "anchor": anchor_policy_key,
        "chunking": CHUNKING_POLICY_KEY,
        "fts": FTS_POLICY_KEY,
        "parser": PARSER_POLICY_KEY,
        "retrieval": RETRIEVAL_POLICY_KEY,
    }
    identities = {
        name: {
            "policy_key": key,
            "policy_sha256": (
                anchor_policy_sha256
                if name == "anchor" and anchor_policy_sha256 is not None
                else _fixture_policy_sha256(key)
            ),
        }
        for name, key in policy_keys.items()
    }
    return canonical_json_sha256(
        {
            "anchor_policy": identities["anchor"],
            "chunking_policy": identities["chunking"],
            "embedding_model": {
                "artifact_manifest_sha256": SHA_C,
                "model_key": EMBEDDING_MODEL_KEY,
            },
            "fts_policy": identities["fts"],
            "parser_policy": identities["parser"],
            "retrieval_policy": identities["retrieval"],
        }
    )


@pytest.fixture(scope="module")
def postgres_engine() -> Iterator[Engine]:
    database_url = os.environ.get("EVE_RAG_TEST_DATABASE_URL", get_settings().database_url)
    admin_engine = create_engine(database_url, poolclass=NullPool)
    schema = f"test_m31_{uuid4().hex}"
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


def test_m31_metadata_contains_all_approved_literature_objects() -> None:
    assert {
        LiteraturePolicy.__tablename__,
        EmbeddingModel.__tablename__,
        CorpusRelease.__tablename__,
        Document.__tablename__,
        CorpusDocumentMembership.__tablename__,
        DocumentChunk.__tablename__,
        DocumentEmbedding.__tablename__,
        DocumentAnchor.__tablename__,
        CorpusImportRun.__tablename__,
        CorpusImportLedger.__tablename__,
        CorpusValidationReceipt.__tablename__,
    } == {
        "literature_policy",
        "embedding_model",
        "corpus_release",
        "document",
        "corpus_document_membership",
        "document_chunk",
        "document_embedding",
        "document_anchor",
        "corpus_import_run",
        "corpus_import_ledger",
        "corpus_validation_receipt",
    }
    assert str(DocumentEmbedding.__table__.c.embedding.type) == "VECTOR(384)"
    assert str(DocumentChunk.__table__.c.fts_document.type) == "TSVECTOR"


def test_fresh_head_installs_pgvector_tables_and_exact_indexes(postgres_engine: Engine) -> None:
    with postgres_engine.connect() as connection:
        database = inspect(connection)
        tables = set(database.get_table_names())
        assert {
            "literature_policy",
            "embedding_model",
            "corpus_release",
            "document",
            "corpus_document_membership",
            "document_chunk",
            "document_embedding",
            "document_anchor",
            "corpus_import_run",
            "corpus_import_ledger",
            "corpus_validation_receipt",
        }.issubset(tables)
        assert (
            connection.scalar(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
            is not None
        )
        chunk_indexes = {item["name"] for item in database.get_indexes("document_chunk")}
        embedding_indexes = {item["name"] for item in database.get_indexes("document_embedding")}
        assert "ix_document_chunk_fts_document_gin" in chunk_indexes
        assert "ix_document_embedding_hnsw_cosine" in embedding_indexes


def test_candidate_cannot_be_validated_without_a_trusted_receipt(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as session:
        _insert_candidate_graph(session, base_id=100)
        session.commit()

    with Session(postgres_engine) as session:
        with pytest.raises(DBAPIError, match="trusted passing corpus validation receipt"):
            session.execute(
                update(CorpusRelease).where(CorpusRelease.id == 120).values(status="validated")
            )
            session.commit()
        session.rollback()


def test_candidate_child_write_blocks_concurrent_validation_promotion(
    postgres_engine: Engine,
) -> None:
    base_id = 600
    release_id = base_id + 20
    chunk_id = base_id + 40
    corpus_release_key = "corpus:endoviho-rag:v0:20990102:601"
    graph_sha256 = _fixture_policy_graph_sha256()
    chunk_hex = format(base_id // 100 + 6, "x")
    with Session(postgres_engine) as session:
        _insert_candidate_graph(
            session,
            base_id=base_id,
            policy_graph_sha256=graph_sha256,
        )
        session.flush()
        session.add(
            CorpusValidationReceipt(
                id=base_id + 60,
                release_id=release_id,
                **build_trusted_receipt_fixture(
                    corpus_release_key=corpus_release_key,
                    manifest_sha256=SHA_A,
                    policy_graph_sha256=graph_sha256,
                    model_artifact_manifest_sha256=SHA_C,
                    document_count=1,
                    chunk_count=1,
                    embedding_count=1,
                    anchor_count=0,
                    relevant_chunk_key=f"chunk:sha256:{chunk_hex * 64}",
                    seed=str(base_id),
                ),
            )
        )
        session.commit()

    with (
        postgres_engine.connect() as child_writer,
        postgres_engine.connect() as release_promoter,
    ):
        child_transaction = child_writer.begin()
        promoter_transaction = release_promoter.begin()
        assert child_writer.scalar(text("SELECT pg_backend_pid()")) != release_promoter.scalar(
            text("SELECT pg_backend_pid()")
        )
        try:
            child_writer.execute(
                update(DocumentChunk)
                .where(DocumentChunk.id == chunk_id)
                .values(locator_text="uncommitted candidate edit")
            )
            release_promoter.exec_driver_sql("SET LOCAL lock_timeout = '250ms'")
            with pytest.raises(DBAPIError, match="lock timeout"):
                release_promoter.execute(
                    update(CorpusRelease)
                    .where(CorpusRelease.id == release_id)
                    .values(status="validated")
                )
        finally:
            if promoter_transaction.is_active:
                promoter_transaction.rollback()
            if child_transaction.is_active:
                child_transaction.rollback()

    with Session(postgres_engine) as session:
        release = session.get(CorpusRelease, release_id)
        chunk = session.get(DocumentChunk, chunk_id)
        assert release is not None and release.status == "candidate"
        assert chunk is not None and chunk.locator_text == "paragraph 1"


def test_published_corpus_gate_issues_exact_capability_after_full_validation(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as session:
        _insert_publishable_graph(session, base_id=200)
        session.commit()

    capability = PublishedCorpusGate(postgres_engine).authorize(
        "corpus:endoviho-rag:v0:20990102:201"
    )

    assert capability.release_id == 220
    assert capability.status == "published"
    assert capability.manifest_sha256 == SHA_A
    assert capability.validation_receipt_key.startswith("corpus-receipt:sha256:")
    assert capability.embedding_model_key == EMBEDDING_MODEL_KEY
    assert capability.embedding_dimension == 384
    assert capability.model_artifact_manifest_sha256 == SHA_C
    assert capability.retrieval_policy_key == RETRIEVAL_POLICY_KEY


def test_gate_fails_closed_before_retrieval_for_invalid_or_candidate_corpus(
    postgres_engine: Engine,
) -> None:
    gate = PublishedCorpusGate(postgres_engine)
    with pytest.raises(LiteratureRetrievalRefusal) as invalid:
        gate.authorize("latest")
    assert invalid.value.code == "unsupported_request"
    assert invalid.value.retrieval_executed is False

    with pytest.raises(LiteratureRetrievalRefusal) as candidate:
        gate.authorize("corpus:endoviho-rag:v0:20990102:101")
    assert candidate.value.code == "corpus_not_published"
    assert candidate.value.retrieval_executed is False


def test_candidate_gate_recomputes_policy_graph_instead_of_trusting_report(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as session:
        _insert_candidate_graph(
            session,
            base_id=300,
            policy_graph_sha256=SHA_D,
        )
        session.commit()

    report_payload = {
        "validation_schema_version": "corpus-rebuild-validation-v2",
        "corpus_release_key": "corpus:endoviho-rag:v0:20990102:301",
        "manifest_sha256": SHA_A,
        "policy_graph_sha256": SHA_D,
        "embedding_model_key": EMBEDDING_MODEL_KEY,
        "model_artifact_manifest_sha256": SHA_C,
        "anchor_manifest_sha256": SHA_D,
        "provider_kind": "local_bge",
        "passed": True,
        "findings": (),
        "document_count": 1,
        "chunk_count": 1,
        "embedding_count": 1,
        "anchor_count": 0,
        "document_keys_sha256": SHA_A,
        "document_rebuild_sha256": SHA_A,
        "chunk_rebuild_sha256": SHA_B,
        "embedding_rebuild_sha256": SHA_C,
        "anchor_rebuild_sha256": SHA_D,
    }
    report = RebuildValidationReport(
        **report_payload,
        rebuild_sha256=canonical_json_sha256(report_payload),
    )

    with pytest.raises(LiteratureRetrievalRefusal) as refused:
        ValidatedCandidateGate(postgres_engine).authorize(report)
    assert refused.value.code == "corpus_manifest_invalid"


def test_published_gate_rejects_nonapproved_anchor_policy_key(
    postgres_engine: Engine,
) -> None:
    corrupt_anchor_key = "anchor:endoviho-unapproved-v3"
    corrupt_anchor_sha256 = _fixture_policy_sha256(corrupt_anchor_key)
    with Session(postgres_engine) as session:
        policy_ids = _ensure_shared_dependencies(session)
        session.add(
            LiteraturePolicy(
                id=9994,
                policy_key=corrupt_anchor_key,
                policy_kind="anchor",
                schema_version="literature-policy-v1",
                policy_json={"policy_key": corrupt_anchor_key},
                policy_sha256=corrupt_anchor_sha256,
                code_sha256=SHA_B,
            )
        )
        session.flush()
        corrupt_policy_ids = {**policy_ids, "anchor": 9994}
        _insert_publishable_graph(
            session,
            base_id=400,
            policy_ids=corrupt_policy_ids,
            policy_graph_sha256=_fixture_policy_graph_sha256(
                anchor_policy_key=corrupt_anchor_key,
                anchor_policy_sha256=corrupt_anchor_sha256,
            ),
        )
        session.commit()

    with pytest.raises(LiteratureRetrievalRefusal) as refused:
        PublishedCorpusGate(postgres_engine).authorize("corpus:endoviho-rag:v0:20990102:401")
    assert refused.value.code == "corpus_manifest_invalid"


def test_published_gate_rejects_policy_json_checksum_tampering(
    postgres_engine: Engine,
) -> None:
    with postgres_engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE literature_policy DISABLE TRIGGER trg_literature_policy_immutable")
        )
        connection.execute(
            update(LiteraturePolicy)
            .where(LiteraturePolicy.id == 5)
            .values(policy_json={"tampered": True})
        )
        connection.execute(
            text("ALTER TABLE literature_policy ENABLE TRIGGER trg_literature_policy_immutable")
        )

    try:
        with pytest.raises(LiteratureRetrievalRefusal) as refused:
            PublishedCorpusGate(postgres_engine).authorize("corpus:endoviho-rag:v0:20990102:201")
        assert refused.value.code == "corpus_manifest_invalid"
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE literature_policy DISABLE TRIGGER trg_literature_policy_immutable"
                )
            )
            connection.execute(
                update(LiteraturePolicy)
                .where(LiteraturePolicy.id == 5)
                .values(policy_json={"policy_key": ANCHOR_POLICY_KEY})
            )
            connection.execute(
                text("ALTER TABLE literature_policy ENABLE TRIGGER trg_literature_policy_immutable")
            )


def test_published_gate_rejects_tampered_receipt_evidence_with_stable_code(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as session:
        _insert_publishable_graph(session, base_id=500)
        session.commit()
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE corpus_validation_receipt "
                "DISABLE TRIGGER trg_corpus_validation_receipt_immutable"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE corpus_validation_receipt "
                "DISABLE TRIGGER trg_corpus_validation_receipt_release_guard"
            )
        )
        connection.execute(
            update(CorpusValidationReceipt)
            .where(CorpusValidationReceipt.id == 560)
            .values(validation_report={"tampered": True})
        )
        connection.execute(
            text(
                "ALTER TABLE corpus_validation_receipt "
                "ENABLE TRIGGER trg_corpus_validation_receipt_immutable"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE corpus_validation_receipt "
                "ENABLE TRIGGER trg_corpus_validation_receipt_release_guard"
            )
        )

    with pytest.raises(LiteratureRetrievalRefusal) as refused:
        PublishedCorpusGate(postgres_engine).authorize(
            "corpus:endoviho-rag:v0:20990102:501"
        )
    assert refused.value.code == "corpus_receipt_invalid"


def test_published_corpus_and_immutable_children_reject_mutation(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as session:
        with pytest.raises(
            DBAPIError,
            match="validated, published, or retired corpus content is immutable",
        ):
            session.execute(
                update(DocumentChunk).where(DocumentChunk.id == 240).values(locator_text="changed")
            )
            session.commit()
        session.rollback()

    with Session(postgres_engine) as session:
        with pytest.raises(DBAPIError, match="immutable literature identity row"):
            session.execute(update(Document).where(Document.id == 230).values(title="changed"))
            session.commit()
        session.rollback()

    with Session(postgres_engine) as session:
        with pytest.raises(
            DBAPIError,
            match="validated, published, or retired corpus content is immutable",
        ):
            session.execute(
                update(DocumentChunk).where(DocumentChunk.id == 240).values(release_id=120)
            )
            session.commit()
        session.rollback()


def test_vector_dimension_and_anchor_target_constraints_fail_closed(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as session:
        invalid_embedding = DocumentEmbedding(
            id=9991,
            release_id=220,
            chunk_id=240,
            embedding_model_id=210,
            embedding=[1.0, 0.0],
            embedding_mode="passage",
            embedding_sha256=SHA_A,
        )
        session.add(invalid_embedding)
        with pytest.raises((DBAPIError, ValueError)):
            session.commit()
        session.rollback()

    with Session(postgres_engine) as session:
        invalid_anchor = DocumentAnchor(
            id=9992,
            anchor_key=f"anchor:sha256:{'9' * 64}",
            release_id=120,
            document_id=130,
            anchor_type="locus",
            locus_key=None,
            assembly_key=None,
            lineage_snapshot_key=None,
            lineage_term_key=None,
            method_definition_key=None,
            target_document_key=None,
            doi=None,
            pmid=None,
            pmcid=None,
            keyword_phrase=None,
            manifest_row=1,
            curation_method="synthetic-test",
            source_locator={"row": 1},
            anchor_sha256=SHA_A,
        )
        session.add(invalid_anchor)
        with pytest.raises(DBAPIError):
            session.commit()
        session.rollback()


def _insert_candidate_graph(
    session: Session,
    *,
    base_id: int,
    policy_ids: dict[str, int] | None = None,
    policy_graph_sha256: str | None = None,
) -> None:
    if policy_ids is None:
        policy_ids = _ensure_shared_dependencies(session)
    document_hex = format(base_id // 100 + 2, "x")
    chunk_hex = format(base_id // 100 + 6, "x")
    release_suffix = base_id + 1
    corpus_release_key = f"corpus:endoviho-rag:v0:20990102:{release_suffix:03d}"
    text_value = f"Synthetic chunk for {corpus_release_key}."
    session.add(
        CorpusRelease(
            id=base_id + 20,
            corpus_release_key=corpus_release_key,
            title="Synthetic corpus",
            purpose="M3 PostgreSQL integration fixture",
            status="candidate",
            manifest_sha256=SHA_A,
            policy_graph_sha256=policy_graph_sha256 or _fixture_policy_graph_sha256(),
            manifest_document_count=1,
            expected_chunk_count_min=1,
            expected_chunk_count_max=1,
            parser_policy_id=policy_ids["parser"],
            chunking_policy_id=policy_ids["chunking"],
            fts_policy_id=policy_ids["fts"],
            retrieval_policy_id=policy_ids["retrieval"],
            anchor_policy_id=policy_ids["anchor"],
            embedding_model_id=10,
            published_at=None,
            supersedes_release_id=None,
        )
    )
    session.add(
        Document(
            id=base_id + 30,
            document_key=f"document:sha256:{document_hex * 64}",
            source_artifact_sha256=SHA_A,
            normalized_document_sha256=SHA_B,
            byte_size=100,
            media_type="text/plain",
            document_version="synthetic-v1",
            title="Synthetic document",
            authors=["Fixture Author"],
            doi=None,
            pmid=None,
            pmcid=None,
            source_uri=f"urn:endoviho:synthetic:m31:{base_id}",
            retrieved_at=NOW,
            declared_license="CC0-1.0",
            license_evidence_uri="urn:endoviho:synthetic:license",
            license_review_status="approved",
            retrieval_text_allowed=True,
            bibliographic_metadata={"fixture": True},
        )
    )
    session.flush()
    session.add(
        CorpusDocumentMembership(
            release_id=base_id + 20,
            document_id=base_id + 30,
            manifest_row=1,
        )
    )
    session.flush()
    session.add(
        DocumentChunk(
            id=base_id + 40,
            chunk_key=f"chunk:sha256:{chunk_hex * 64}",
            release_id=base_id + 20,
            document_id=base_id + 30,
            parser_policy_id=policy_ids["parser"],
            chunking_policy_id=policy_ids["chunking"],
            chunk_index=0,
            section_path=["Methods"],
            block_type="paragraph",
            locator={"locator_type": "plain_text", "paragraph_ordinal": 1},
            locator_text="paragraph 1",
            text=text_value,
            text_sha256=hashlib.sha256(text_value.encode()).hexdigest(),
            token_count=6,
            fts_document="'synthet':1 'chunk':2",
        )
    )
    session.flush()
    vector = [0.0] * 384
    vector[0] = 1.0
    session.add(
        DocumentEmbedding(
            id=base_id + 50,
            release_id=base_id + 20,
            chunk_id=base_id + 40,
            embedding_model_id=10,
            embedding=vector,
            embedding_mode="passage",
            embedding_sha256=SHA_C,
        )
    )


def _ensure_shared_dependencies(session: Session) -> dict[str, int]:
    policies = (
        (1, PARSER_POLICY_KEY, "parser"),
        (2, CHUNKING_POLICY_KEY, "chunking"),
        (3, FTS_POLICY_KEY, "fts"),
        (4, RETRIEVAL_POLICY_KEY, "retrieval"),
        (5, ANCHOR_POLICY_KEY, "anchor"),
    )
    if session.scalar(select(LiteraturePolicy.id).limit(1)) is None:
        for policy_id, policy_key, policy_kind in policies:
            session.add(
                LiteraturePolicy(
                    id=policy_id,
                    policy_key=policy_key,
                    policy_kind=policy_kind,
                    schema_version="literature-policy-v1",
                    policy_json={"policy_key": policy_key},
                    policy_sha256=_fixture_policy_sha256(policy_key),
                    code_sha256=SHA_B,
                )
            )
        session.add(
            EmbeddingModel(
                id=10,
                model_key=EMBEDDING_MODEL_KEY,
                provider_kind="local_hf",
                repository_id="BAAI/bge-small-en-v1.5",
                revision="5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
                dimension=384,
                max_sequence_tokens=512,
                pooling="cls",
                l2_normalized=True,
                passage_prefix="",
                query_prefix="Represent this sentence for searching relevant passages: ",
                similarity="cosine",
                license_key="MIT",
                artifact_manifest_sha256=SHA_C,
                model_metadata={"dtype": "float32", "offline_required": True},
            )
        )
        session.flush()
    return {policy_kind: policy_id for policy_id, _policy_key, policy_kind in policies}


def _insert_publishable_graph(
    session: Session,
    *,
    base_id: int,
    policy_ids: dict[str, int] | None = None,
    policy_graph_sha256: str | None = None,
) -> None:
    graph_sha256 = policy_graph_sha256 or _fixture_policy_graph_sha256()
    _insert_candidate_graph(
        session,
        base_id=base_id,
        policy_ids=policy_ids,
        policy_graph_sha256=graph_sha256,
    )
    session.flush()
    release_suffix = base_id + 1
    corpus_release_key = f"corpus:endoviho-rag:v0:20990102:{release_suffix:03d}"
    chunk_hex = format(base_id // 100 + 6, "x")
    receipt_values = build_trusted_receipt_fixture(
        corpus_release_key=corpus_release_key,
        manifest_sha256=SHA_A,
        policy_graph_sha256=graph_sha256,
        model_artifact_manifest_sha256=SHA_C,
        document_count=1,
        chunk_count=1,
        embedding_count=1,
        anchor_count=0,
        relevant_chunk_key=f"chunk:sha256:{chunk_hex * 64}",
        seed=str(base_id),
    )
    session.add(
        CorpusValidationReceipt(
            id=base_id + 60,
            release_id=base_id + 20,
            **receipt_values,
        )
    )
    session.flush()
    session.execute(
        update(CorpusRelease).where(CorpusRelease.id == base_id + 20).values(status="validated")
    )
    session.flush()
    session.execute(
        update(CorpusRelease)
        .where(CorpusRelease.id == base_id + 20)
        .values(status="published", published_at=NOW)
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
