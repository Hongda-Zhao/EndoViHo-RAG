from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from eve_relation_rag.config import get_settings
from eve_relation_rag.db import Base
from eve_relation_rag.db.models import DetectionCall, EVELocus, ImportLedger, MethodDefinition

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REVISION_0002 = "0002_milestone_1_truth_layer"
REVISION_0004 = "0004_m1_shared_intervals"
REVISION_HEAD = "0005_m1_fail_closed_publication"
SHA_A = "a" * 64
SHA_B = "b" * 64


@pytest.fixture(scope="module")
def postgres_admin_engine() -> Iterator[Engine]:
    database_url = os.environ.get(
        "EVE_RAG_TEST_DATABASE_URL", get_settings().database_url
    )
    engine = create_engine(database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError as exc:
        engine.dispose()
        pytest.skip(f"PostgreSQL integration database is unavailable: {exc.orig}")

    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def isolated_schema(postgres_admin_engine: Engine) -> Iterator[tuple[Engine, str]]:
    schema = f"test_m1_migration_{uuid4().hex}"
    with postgres_admin_engine.begin() as connection:
        connection.execute(CreateSchema(schema))

    try:
        yield postgres_admin_engine, schema
    finally:
        with postgres_admin_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))


def test_orm_metadata_matches_m1_provenance_contract() -> None:
    assert MethodDefinition.__table__.c.definition_artifact_id.nullable is True

    detection_unique_constraints = _unique_constraints(DetectionCall.__table__)
    assert detection_unique_constraints["uq_detection_call_source_record_process_run"] == (
        "release_id",
        "source_record_id",
        "process_run_id",
    )
    assert detection_unique_constraints["uq_detection_call_release_id_source_record"] == (
        "release_id",
        "id",
        "source_record_id",
    )
    assert "uq_detection_call_source_record" not in detection_unique_constraints

    locus_unique_constraints = _unique_constraints(EVELocus.__table__)
    assert locus_unique_constraints["uq_eve_locus_release_id_source_record"] == (
        "release_id",
        "id",
        "source_record_id",
    )

    ledger_foreign_keys = _foreign_keys(ImportLedger.__table__)
    assert ledger_foreign_keys["fk_import_ledger_call_same_source_record"] == (
        ("release_id", "call_id", "source_record_id"),
        ("detection_call.release_id", "detection_call.id", "detection_call.source_record_id"),
    )
    assert ledger_foreign_keys["fk_import_ledger_locus_same_source_record"] == (
        ("release_id", "locus_id", "source_record_id"),
        ("eve_locus.release_id", "eve_locus.id", "eve_locus.source_record_id"),
    )


def test_fresh_head_has_fail_closed_release_and_provenance_constraints(
    isolated_schema: tuple[Engine, str],
) -> None:
    engine, schema = isolated_schema
    with engine.connect() as connection:
        _set_search_path(connection, schema)
        _upgrade(connection, through=REVISION_HEAD)

        database = inspect(connection)
        method_columns = {
            column["name"]: column for column in database.get_columns("method_definition")
        }
        assert method_columns["definition_artifact_id"]["nullable"] is True

        detection_uniques = {
            constraint["name"]
            for constraint in database.get_unique_constraints("detection_call")
        }
        assert "uq_detection_call_source_record_process_run" in detection_uniques
        assert "uq_detection_call_release_id_source_record" in detection_uniques
        assert "uq_detection_call_source_record" not in detection_uniques

        ledger_foreign_keys = {
            constraint["name"]: tuple(constraint["constrained_columns"])
            for constraint in database.get_foreign_keys("import_ledger")
        }
        assert ledger_foreign_keys["fk_import_ledger_call_same_source_record"] == (
            "release_id",
            "call_id",
            "source_record_id",
        )
        assert ledger_foreign_keys["fk_import_ledger_locus_same_source_record"] == (
            "release_id",
            "locus_id",
            "source_record_id",
        )

        _insert_candidate_release(connection, dataset_id=1, release_id=1)
        connection.commit()

        promotion_statements = (
            "UPDATE dataset_release SET status = 'validated' WHERE id = 1",
            """
            UPDATE dataset_release
               SET status = 'published',
                   manifest_sha256 = :manifest_sha256,
                   published_at = now()
             WHERE id = 1
            """,
        )
        for statement in promotion_statements:
            with pytest.raises(DBAPIError) as error:
                connection.execute(text(statement), {"manifest_sha256": SHA_A})
            assert "no trusted validation-receipt workflow exists" in str(error.value.orig)
            connection.rollback()
            assert connection.scalar(
                text("SELECT status FROM dataset_release WHERE id = 1")
            ) == "candidate"
            connection.commit()


def test_populated_0002_database_upgrades_to_head_without_losing_rows(
    isolated_schema: tuple[Engine, str],
) -> None:
    engine, schema = isolated_schema
    with engine.connect() as connection:
        _set_search_path(connection, schema)
        _upgrade(connection, through=REVISION_0002)
        _insert_candidate_release(connection, dataset_id=11, release_id=12)
        connection.execute(
            text(
                """
                INSERT INTO source_snapshot (
                    id, snapshot_key, source_name, source_version, source_uri,
                    retrieved_at, verified_manifest_sha256, verified_license_key
                ) VALUES (
                    13, 'source-snapshot:populated-0002', 'fixture', 'v1',
                    'https://example.invalid/source', now(), :sha256, 'fixture-license'
                )
                """
            ),
            {"sha256": SHA_A},
        )
        connection.commit()

        _upgrade(connection, after=REVISION_0002, through=REVISION_HEAD)

        assert connection.scalar(text("SELECT count(*) FROM dataset WHERE id = 11")) == 1
        assert connection.scalar(
            text("SELECT count(*) FROM dataset_release WHERE id = 12 AND status = 'candidate'")
        ) == 1
        assert connection.scalar(
            text("SELECT count(*) FROM source_snapshot WHERE id = 13")
        ) == 1

        connection.execute(
            text(
                """
                INSERT INTO method_definition (
                    id, method_definition_key, method_key, version, method_kind,
                    definition_artifact_id, definition_sha256,
                    parameter_schema, output_schema
                ) VALUES (
                    14, 'method-definition:without-artifact', 'fixture-method', 'v1',
                    'source_import', NULL, :sha256, '{}'::jsonb, '{}'::jsonb
                )
                """
            ),
            {"sha256": SHA_B},
        )
        connection.commit()
        assert connection.scalar(
            text(
                "SELECT count(*) FROM method_definition "
                "WHERE id = 14 AND definition_artifact_id IS NULL"
            )
        ) == 1


def test_import_ledger_composite_foreign_keys_reject_cross_source_links(
    isolated_schema: tuple[Engine, str],
) -> None:
    engine, schema = isolated_schema
    with engine.connect() as connection:
        _set_search_path(connection, schema)
        _upgrade(connection, through=REVISION_HEAD)
        _insert_provenance_graph(connection, method_artifact_id=None)
        _insert_ledger(connection, ledger_id=100, source_record_id=101, call_id=90, locus_id=80)
        connection.commit()

        with pytest.raises(DBAPIError) as call_error:
            _insert_ledger(
                connection,
                ledger_id=101,
                source_record_id=102,
                call_id=90,
                locus_id=81,
            )
        assert _constraint_name(call_error.value) == "fk_import_ledger_call_same_source_record"
        connection.rollback()

        with pytest.raises(DBAPIError) as locus_error:
            _insert_ledger(
                connection,
                ledger_id=102,
                source_record_id=102,
                call_id=91,
                locus_id=80,
            )
        assert _constraint_name(locus_error.value) == "fk_import_ledger_locus_same_source_record"
        connection.rollback()

        assert connection.scalar(text("SELECT count(*) FROM import_ledger")) == 1


def test_0005_bad_ledger_preflight_fails_before_any_ddl(
    isolated_schema: tuple[Engine, str],
) -> None:
    engine, schema = isolated_schema
    with engine.connect() as connection:
        _set_search_path(connection, schema)
        _upgrade(connection, through=REVISION_0004)
        _insert_provenance_graph(connection, method_artifact_id=20)
        _insert_ledger(connection, ledger_id=100, source_record_id=102, call_id=90, locus_id=81)
        connection.commit()

        with pytest.raises(DBAPIError) as error:
            _upgrade(connection, after=REVISION_0004, through=REVISION_HEAD)
        assert "cannot enforce call provenance" in str(error.value.orig)

        database = inspect(connection)
        method_columns = {
            column["name"]: column for column in database.get_columns("method_definition")
        }
        assert method_columns["definition_artifact_id"]["nullable"] is False

        detection_uniques = {
            constraint["name"]
            for constraint in database.get_unique_constraints("detection_call")
        }
        assert "uq_detection_call_source_record" in detection_uniques
        assert "uq_detection_call_source_record_process_run" not in detection_uniques

        ledger_foreign_keys = {
            constraint["name"] for constraint in database.get_foreign_keys("import_ledger")
        }
        assert "fk_import_ledger_call_same_release" in ledger_foreign_keys
        assert "fk_import_ledger_call_same_source_record" not in ledger_foreign_keys


def _unique_constraints(table: sa.Table) -> dict[str, tuple[str, ...]]:
    return {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint) and constraint.name is not None
    }


def _foreign_keys(
    table: sa.Table,
) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    return {
        constraint.name: (
            tuple(column.name for column in constraint.columns),
            tuple(element.target_fullname for element in constraint.elements),
        )
        for constraint in table.constraints
        if isinstance(constraint, sa.ForeignKeyConstraint) and constraint.name is not None
    }


def _set_search_path(connection: sa.Connection, schema: str) -> None:
    connection.execute(text(f'SET search_path TO "{schema}", public'))
    connection.commit()


def _upgrade(
    connection: sa.Connection,
    *,
    through: str,
    after: str | None = None,
) -> None:
    script = ScriptDirectory.from_config(Config(str(REPOSITORY_ROOT / "alembic.ini")))
    revisions = list(reversed(list(script.walk_revisions(base="base", head="heads"))))
    apply_revision = after is None
    reached_target = False

    for revision in revisions:
        if not apply_revision:
            if revision.revision == after:
                apply_revision = True
            continue

        with connection.begin():
            context = MigrationContext.configure(
                connection,
                opts={"target_metadata": Base.metadata},
            )
            with Operations.context(context):
                revision.module.upgrade()

        if revision.revision == through:
            reached_target = True
            break

    if not reached_target:
        raise AssertionError(f"Alembic revision {through!r} was not reached")


def _insert_candidate_release(
    connection: sa.Connection,
    *,
    dataset_id: int,
    release_id: int,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO dataset (id, dataset_key, title)
            VALUES (:dataset_id, :dataset_key, 'Migration fixture')
            """
        ),
        {"dataset_id": dataset_id, "dataset_key": f"dataset:{dataset_id}"},
    )
    connection.execute(
        text(
            """
            INSERT INTO dataset_release (
                id, dataset_id, release_key, schema_version, status
            ) VALUES (
                :release_id, :dataset_id, :release_key, 'm1-test', 'candidate'
            )
            """
        ),
        {
            "dataset_id": dataset_id,
            "release_id": release_id,
            "release_key": f"release:{release_id}",
        },
    )


def _insert_provenance_graph(
    connection: sa.Connection,
    *,
    method_artifact_id: int | None,
) -> None:
    _insert_candidate_release(connection, dataset_id=1, release_id=1)
    statements: tuple[tuple[str, dict[str, object]], ...] = (
        (
            """
            INSERT INTO source_snapshot (
                id, snapshot_key, source_name, source_version, source_uri,
                retrieved_at, verified_manifest_sha256, verified_license_key
            ) VALUES (
                10, 'source-snapshot:fixture', 'fixture', 'v1',
                'https://example.invalid/source', now(), :sha_a, 'fixture-license'
            )
            """,
            {"sha_a": SHA_A},
        ),
        (
            """
            INSERT INTO source_artifact (
                id, snapshot_id, artifact_key, filename, media_type, byte_size,
                verified_sha256, source_uri, retrieved_at, verified_license_key
            ) VALUES (
                20, 10, 'source-artifact:fixture', 'fixture.xlsx',
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                1, :sha_b, 'https://example.invalid/artifact', now(), 'fixture-license'
            )
            """,
            {"sha_b": SHA_B},
        ),
        (
            """
            INSERT INTO release_source_snapshot (release_id, source_snapshot_id, role)
            VALUES (1, 10, 'primary_data')
            """,
            {},
        ),
        (
            """
            INSERT INTO genome_assembly (
                id, assembly_key, namespace, accession_version,
                source_organism_name, source_artifact_id
            ) VALUES (
                30, 'assembly:fixture', 'ncbi', 'GCA_000000001.1',
                'Fixture organism', 20
            )
            """,
            {},
        ),
        (
            """
            INSERT INTO assembly_sequence (
                id, assembly_id, sequence_key, namespace, accession_version,
                sequence_length, source_artifact_id
            ) VALUES (
                40, 30, 'sequence:fixture', 'insdc', 'CONTIG_1.1', 1000, 20
            )
            """,
            {},
        ),
        (
            """
            INSERT INTO release_assembly_membership (release_id, assembly_id)
            VALUES (1, 30)
            """,
            {},
        ),
        (
            """
            INSERT INTO source_record (
                id, source_record_key, snapshot_id, artifact_id, worksheet,
                row_number, native_vr_token, assembly_accession_version,
                sequence_accession_version, source_locator, raw_payload,
                raw_payload_sha256
            ) VALUES
                (101, 'source-record:101', 10, 20, 'Sheet1', 2, 'vr-1',
                 'GCA_000000001.1', 'CONTIG_1.1', '{}'::jsonb, '{}'::jsonb, :sha_a),
                (102, 'source-record:102', 10, 20, 'Sheet1', 3, 'vr-2',
                 'GCA_000000001.1', 'CONTIG_1.1', '{}'::jsonb, '{}'::jsonb, :sha_b)
            """,
            {"sha_a": SHA_A, "sha_b": SHA_B},
        ),
        (
            """
            INSERT INTO import_run (
                id, run_key, release_id, source_snapshot_id, source_artifact_id,
                importer_name, importer_version, code_sha256, parameters,
                parameters_sha256, status, started_at, finished_at
            ) VALUES (
                60, 'import-run:fixture', 1, 10, 20, 'fixture', 'v1', :sha_a,
                '{}'::jsonb, :sha_b, 'succeeded', now(), now()
            )
            """,
            {"sha_a": SHA_A, "sha_b": SHA_B},
        ),
        (
            """
            INSERT INTO method_definition (
                id, method_definition_key, method_key, version, method_kind,
                definition_artifact_id, definition_sha256,
                parameter_schema, output_schema
            ) VALUES (
                50, 'method-definition:fixture', 'fixture-method', 'v1',
                'source_import', :artifact_id, :sha_a, '{}'::jsonb, '{}'::jsonb
            )
            """,
            {"artifact_id": method_artifact_id, "sha_a": SHA_A},
        ),
        (
            """
            INSERT INTO release_method_definition (
                release_id, method_definition_id, role
            ) VALUES (1, 50, 'source_import')
            """,
            {},
        ),
        (
            """
            INSERT INTO process_run (
                id, process_run_key, release_id, method_definition_id,
                method_role, import_run_id, execution_status,
                software_agent_key, parameters, parameters_sha256,
                started_at, finished_at
            ) VALUES (
                70, 'process-run:fixture', 1, 50, 'source_import', 60,
                'succeeded', 'software:fixture', '{}'::jsonb, :sha_b, now(), now()
            )
            """,
            {"sha_b": SHA_B},
        ),
        (
            """
            INSERT INTO eve_locus (
                id, locus_key, release_id, assembly_id, sequence_id,
                source_snapshot_id, source_record_id, native_vr_token,
                identity_policy_key
            ) VALUES
                (80, :locus_80, 1, 30, 40, 10, 101, 'vr-1', 'identity-policy:v1'),
                (81, :locus_81, 1, 30, 40, 10, 102, 'vr-2', 'identity-policy:v1')
            """,
            {
                "locus_80": f"locus:eve:v1:sha256:{'8' * 64}",
                "locus_81": f"locus:eve:v1:sha256:{'9' * 64}",
            },
        ),
        (
            """
            INSERT INTO detection_call (
                id, call_key, release_id, source_snapshot_id, source_record_id,
                locus_id, process_run_id, process_run_status,
                source_method_key, source_locator, raw_result
            ) VALUES
                (90, 'detection-call:90', 1, 10, 101, 80, 70, 'succeeded',
                 'fixture-method', '{}'::jsonb, '{}'::jsonb),
                (91, 'detection-call:91', 1, 10, 102, 81, 70, 'succeeded',
                 'fixture-method', '{}'::jsonb, '{}'::jsonb)
            """,
            {},
        ),
    )
    for statement, parameters in statements:
        connection.execute(text(statement), parameters)


def _insert_ledger(
    connection: sa.Connection,
    *,
    ledger_id: int,
    source_record_id: int,
    call_id: int,
    locus_id: int,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO import_ledger (
                id, run_id, release_id, source_record_id, call_id,
                locus_id, outcome, result_payload, result_sha256
            ) VALUES (
                :ledger_id, 60, 1, :source_record_id, :call_id,
                :locus_id, 'normalized_candidate', '{}'::jsonb, :sha256
            )
            """
        ),
        {
            "ledger_id": ledger_id,
            "source_record_id": source_record_id,
            "call_id": call_id,
            "locus_id": locus_id,
            "sha256": SHA_A,
        },
    )


def _constraint_name(error: DBAPIError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)
