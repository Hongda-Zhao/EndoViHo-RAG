"""Add versioned methods, evidence-backed assertions, and immutable truth

Revision ID: 0003_m1_assertion_evidence
Revises: 0002_milestone_1_truth_layer
Create Date: 2026-08-26 14:43:41.015189
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_m1_assertion_evidence"
down_revision: str | Sequence[str] | None = "0002_milestone_1_truth_layer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


GLOBAL_APPEND_ONLY_TABLES = (
    "dataset",
    "source_snapshot",
    "source_artifact",
    "method_definition",
    "lineage_snapshot",
    "lineage_term",
    "lineage_alias",
    "lineage_closure",
    "genome_assembly",
    "assembly_sequence",
    "source_record",
)

RELEASE_SCOPED_TABLES = (
    "release_source_snapshot",
    "release_lineage_snapshot",
    "release_assembly_membership",
    "assembly_taxon_assignment",
    "import_run",
    "eve_locus",
    "detection_call",
    "source_assessment",
    "import_ledger",
    "eve_locus_placement",
    "flank_assessment",
    "inclusion_decision",
    "release_locus_membership",
    "release_method_definition",
    "process_run",
    "evidence_item",
    "scientific_assertion",
    "assertion_evidence",
    "release_assertion_membership",
)


def upgrade() -> None:
    # A legacy DetectionCall can only be upgraded when its original ImportRun is
    # unambiguous.  This preflight deliberately runs before any DDL so failure leaves
    # the 0002 database byte-for-byte usable and directs the operator to re-import.
    op.execute(
        """
        DO $$
        DECLARE
            invalid_call_id bigint;
        BEGIN
            SELECT legacy_call.id
              INTO invalid_call_id
              FROM detection_call AS legacy_call
              JOIN dataset_release AS release
                ON release.id = legacy_call.release_id
             WHERE release.status IN ('published', 'deprecated')
             LIMIT 1;

            IF FOUND THEN
                RAISE EXCEPTION
                    'cannot backfill process provenance into published detection_call %',
                    invalid_call_id
                    USING ERRCODE = '55000',
                          HINT = 'Preserve the published 0002 database and re-import the '
                                 'frozen source into a new candidate/superseding release. '
                                 'No migration DDL has been applied.';
            END IF;

            SELECT call.id
              INTO invalid_call_id
              FROM detection_call AS call
              LEFT JOIN import_ledger AS ledger
                ON ledger.release_id = call.release_id
               AND ledger.call_id = call.id
             GROUP BY call.id
            HAVING count(DISTINCT ledger.run_id) <> 1
             LIMIT 1;

            IF FOUND THEN
                RAISE EXCEPTION
                    'cannot upgrade legacy detection_call %: expected exactly one '
                    'ImportLedger ImportRun mapping', invalid_call_id
                    USING ERRCODE = '23514',
                          HINT = 'Keep the database at 0002 and either restore one '
                                 'unambiguous ledger mapping to a succeeded ImportRun, '
                                 'or re-import the frozen source artifacts into a fresh '
                                 'head database. No migration DDL has been applied.';
            END IF;

            SELECT call.id
              INTO invalid_call_id
              FROM detection_call AS call
              JOIN import_ledger AS ledger
                ON ledger.release_id = call.release_id
               AND ledger.call_id = call.id
              JOIN import_run AS run
                ON run.id = ledger.run_id
               AND run.release_id = ledger.release_id
             WHERE run.status <> 'succeeded'
             LIMIT 1;

            IF FOUND THEN
                RAISE EXCEPTION
                    'cannot upgrade legacy detection_call %: linked ImportRun did not succeed',
                    invalid_call_id
                    USING ERRCODE = '23514',
                          HINT = 'Do not infer successful process provenance. Re-run the '
                                 'frozen import successfully or re-import into a fresh '
                                 'head database. No migration DDL has been applied.';
            END IF;

            IF EXISTS (SELECT 1 FROM detection_call)
               AND (
                   EXISTS (
                       SELECT 1
                         FROM source_snapshot
                        WHERE snapshot_key =
                              'internal:migration:0003:legacy-process-backfill:v1'
                   )
                   OR EXISTS (
                       SELECT 1
                         FROM source_artifact
                        WHERE artifact_key =
                              'internal:migration:0003:legacy-process-definition:v1'
                   )
               ) THEN
                RAISE EXCEPTION 'reserved legacy migration provenance key already exists'
                    USING ERRCODE = '23505',
                          HINT = 'Rename the conflicting pre-existing internal key before '
                                 'retrying; no migration DDL has been applied.';
            END IF;
        END;
        $$
        """
    )

    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "method_definition",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("method_definition_key", sa.String(length=255), nullable=False),
        sa.Column("method_key", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("method_kind", sa.String(length=64), nullable=False),
        sa.Column("definition_artifact_id", sa.BigInteger(), nullable=False),
        sa.Column("definition_sha256", sa.String(length=64), nullable=False),
        sa.Column("parameter_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "definition_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_method_definition_valid_sha256")
        ),
        sa.CheckConstraint(
            "method_kind IN ('source_import', 'source_assessment', 'manual_curation')",
            name=op.f("ck_method_definition_valid_method_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["definition_artifact_id"],
            ["source_artifact.id"],
            name=op.f("fk_method_definition_definition_artifact_id_source_artifact"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_method_definition")),
        sa.UniqueConstraint(
            "method_definition_key", name=op.f("uq_method_definition_method_definition_key")
        ),
        sa.UniqueConstraint("method_key", "version", name="uq_method_definition_key_version"),
    )
    op.create_table(
        "evidence_item",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("evidence_key", sa.String(length=255), nullable=False),
        sa.Column("release_id", sa.BigInteger(), nullable=False),
        sa.Column("source_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("source_artifact_id", sa.BigInteger(), nullable=False),
        sa.Column("evidence_type", sa.String(length=128), nullable=False),
        sa.Column("source_locator", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "evidence_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_evidence_item_valid_sha256")
        ),
        sa.ForeignKeyConstraint(
            ["release_id", "source_snapshot_id"],
            ["release_source_snapshot.release_id", "release_source_snapshot.source_snapshot_id"],
            name="fk_evidence_item_pinned_snapshot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id", "source_artifact_id"],
            ["source_artifact.snapshot_id", "source_artifact.id"],
            name="fk_evidence_item_artifact_same_snapshot",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_item")),
        sa.UniqueConstraint("release_id", "evidence_key", name="uq_evidence_item_release_key"),
        sa.UniqueConstraint("release_id", "id", name="uq_evidence_item_release_id_id"),
    )
    op.create_table(
        "release_method_definition",
        sa.Column("release_id", sa.BigInteger(), nullable=False),
        sa.Column("method_definition_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["method_definition_id"],
            ["method_definition.id"],
            name=op.f("fk_release_method_definition_method_definition_id_method_definition"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["release_id"],
            ["dataset_release.id"],
            name=op.f("fk_release_method_definition_release_id_dataset_release"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "release_id", "method_definition_id", name=op.f("pk_release_method_definition")
        ),
        sa.UniqueConstraint(
            "release_id", "method_definition_id", "role", name="uq_release_method_definition_role"
        ),
    )
    op.create_table(
        "process_run",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("process_run_key", sa.String(length=255), nullable=False),
        sa.Column("release_id", sa.BigInteger(), nullable=False),
        sa.Column("method_definition_id", sa.BigInteger(), nullable=False),
        sa.Column("method_role", sa.String(length=64), nullable=False),
        sa.Column("import_run_id", sa.BigInteger(), nullable=True),
        sa.Column("execution_status", sa.String(length=32), nullable=False),
        sa.Column("software_agent_key", sa.String(length=255), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("parameters_sha256", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(execution_status = 'running' AND finished_at IS NULL) OR "
            "(execution_status <> 'running' AND finished_at IS NOT NULL)",
            name=op.f("ck_process_run_finish_matches_status"),
        ),
        sa.CheckConstraint(
            "execution_status IN ('running', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_process_run_valid_execution_status"),
        ),
        sa.CheckConstraint(
            "parameters_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_process_run_valid_parameters")
        ),
        sa.ForeignKeyConstraint(
            ["import_run_id", "release_id"],
            ["import_run.id", "import_run.release_id"],
            name="fk_process_run_import_run_same_release",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["release_id", "method_definition_id", "method_role"],
            [
                "release_method_definition.release_id",
                "release_method_definition.method_definition_id",
                "release_method_definition.role",
            ],
            name="fk_process_run_pinned_method",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_process_run")),
        sa.UniqueConstraint(
            "id", "release_id", "execution_status", name="uq_process_run_membership_ref"
        ),
        sa.UniqueConstraint("id", "release_id", name="uq_process_run_id_release"),
        sa.UniqueConstraint("process_run_key", name=op.f("uq_process_run_process_run_key")),
    )

    # Preserve populated 0002 databases without pretending that the migration can
    # rediscover a scientific method.  The embedded immutable artifact documents the
    # mechanical linkage policy; the original ImportRun remains the provenance source.
    op.execute(
        """
        INSERT INTO source_snapshot (
            snapshot_key,
            source_name,
            source_version,
            source_uri,
            retrieved_at,
            declared_manifest_sha256,
            verified_manifest_sha256,
            declared_license_key,
            verified_license_key
        )
        SELECT
            'internal:migration:0003:legacy-process-backfill:v1',
            'EVE Relation RAG migration provenance',
            '0003-m1-v1',
            'urn:eve-relation-rag:migration:0003:legacy-process-backfill:v1',
            TIMESTAMPTZ '2026-08-26 00:00:00+00',
            '531f9794af537fe35a79a5ea2abec1ace8b771e9330f2fed0162828a5b2233c7',
            '531f9794af537fe35a79a5ea2abec1ace8b771e9330f2fed0162828a5b2233c7',
            'LicenseRef-EVE-Relation-RAG',
            'LicenseRef-EVE-Relation-RAG'
        WHERE EXISTS (SELECT 1 FROM detection_call)
        """
    )
    op.execute(
        """
        INSERT INTO source_artifact (
            snapshot_id,
            artifact_key,
            filename,
            media_type,
            byte_size,
            declared_sha256,
            verified_sha256,
            source_uri,
            retrieved_at,
            declared_license_key,
            verified_license_key,
            remote_checksum_verified,
            remote_verification_at,
            remote_verification_uri
        )
        SELECT
            snapshot.id,
            'internal:migration:0003:legacy-process-definition:v1',
            '0003_legacy_process_backfill_v1.json',
            'application/json',
            180,
            '531f9794af537fe35a79a5ea2abec1ace8b771e9330f2fed0162828a5b2233c7',
            '531f9794af537fe35a79a5ea2abec1ace8b771e9330f2fed0162828a5b2233c7',
            'data:application/json,%7B%22kind%22%3A%22legacy_process_backfill%22%2C' ||
            '%22migration%22%3A%220003_m1_assertion_evidence%22%2C%22policy%22%3A' ||
            '%22link%20each%20detection_call%20to%20its%20single%20succeeded%20' ||
            'import_run%20through%20import_ledger%22%2C%22version%22%3A1%7D',
            TIMESTAMPTZ '2026-08-26 00:00:00+00',
            'LicenseRef-EVE-Relation-RAG',
            'LicenseRef-EVE-Relation-RAG',
            true,
            TIMESTAMPTZ '2026-08-26 00:00:00+00',
            'data:application/json,%7B%22kind%22%3A%22legacy_process_backfill%22%2C' ||
            '%22migration%22%3A%220003_m1_assertion_evidence%22%2C%22policy%22%3A' ||
            '%22link%20each%20detection_call%20to%20its%20single%20succeeded%20' ||
            'import_run%20through%20import_ledger%22%2C%22version%22%3A1%7D'
        FROM source_snapshot AS snapshot
        WHERE snapshot.snapshot_key =
              'internal:migration:0003:legacy-process-backfill:v1'
        """
    )
    op.execute(
        """
        INSERT INTO method_definition (
            method_definition_key,
            method_key,
            version,
            method_kind,
            definition_artifact_id,
            definition_sha256,
            parameter_schema,
            output_schema
        )
        SELECT
            'internal:migration:0003:legacy-process-linkage:v1',
            'internal:legacy-process-linkage',
            '1',
            'source_import',
            artifact.id,
            '531f9794af537fe35a79a5ea2abec1ace8b771e9330f2fed0162828a5b2233c7',
            '{"type":"object","description":"Original ImportRun parameters"}'::jsonb,
            '{"type":"object","description":"Mechanical legacy call linkage"}'::jsonb
        FROM source_artifact AS artifact
        WHERE artifact.artifact_key =
              'internal:migration:0003:legacy-process-definition:v1'
        """
    )
    op.execute(
        """
        INSERT INTO release_method_definition (
            release_id,
            method_definition_id,
            role
        )
        SELECT DISTINCT
            legacy_call.release_id,
            method.id,
            'legacy_source_import'
        FROM detection_call AS legacy_call
        CROSS JOIN method_definition AS method
        WHERE method.method_definition_key =
              'internal:migration:0003:legacy-process-linkage:v1'
        """
    )
    op.execute(
        """
        WITH used_run AS (
            SELECT DISTINCT ledger.run_id
              FROM detection_call AS legacy_call
              JOIN import_ledger AS ledger
                ON ledger.release_id = legacy_call.release_id
               AND ledger.call_id = legacy_call.id
        )
        INSERT INTO process_run (
            process_run_key,
            release_id,
            method_definition_id,
            method_role,
            import_run_id,
            execution_status,
            software_agent_key,
            parameters,
            parameters_sha256,
            started_at,
            finished_at
        )
        SELECT
            'internal:migration:0003:import-run:' || original_run.id::text,
            original_run.release_id,
            method.id,
            'legacy_source_import',
            original_run.id,
            'succeeded',
            'migration:0003_m1_assertion_evidence',
            original_run.parameters,
            original_run.parameters_sha256,
            original_run.started_at,
            original_run.finished_at
        FROM used_run
        JOIN import_run AS original_run
          ON original_run.id = used_run.run_id
        CROSS JOIN method_definition AS method
        WHERE method.method_definition_key =
              'internal:migration:0003:legacy-process-linkage:v1'
        """
    )

    op.add_column("detection_call", sa.Column("process_run_id", sa.BigInteger(), nullable=True))
    op.execute(
        """
        WITH call_run AS (
            SELECT ledger.call_id, min(ledger.run_id) AS run_id
              FROM import_ledger AS ledger
             WHERE ledger.call_id IS NOT NULL
             GROUP BY ledger.call_id
        )
        UPDATE detection_call AS legacy_call
           SET process_run_id = process.id
          FROM call_run
          JOIN process_run AS process
            ON process.import_run_id = call_run.run_id
          JOIN method_definition AS method
            ON method.id = process.method_definition_id
           AND method.method_definition_key =
               'internal:migration:0003:legacy-process-linkage:v1'
         WHERE legacy_call.id = call_run.call_id
           AND legacy_call.release_id = process.release_id
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM detection_call WHERE process_run_id IS NULL
            ) THEN
                RAISE EXCEPTION 'legacy DetectionCall process backfill was incomplete'
                    USING ERRCODE = '23514';
            END IF;
        END;
        $$
        """
    )
    op.alter_column(
        "detection_call",
        "process_run_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.add_column(
        "detection_call",
        sa.Column(
            "process_run_status",
            sa.String(length=32),
            server_default=sa.text("'succeeded'"),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_detection_call_release_id_process_run",
        "detection_call",
        ["release_id", "id", "process_run_id"],
    )
    op.create_foreign_key(
        "fk_detection_call_process_run_same_release",
        "detection_call",
        "process_run",
        ["process_run_id", "release_id", "process_run_status"],
        ["id", "release_id", "execution_status"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_detection_call_requires_succeeded_process_run"),
        "detection_call",
        "process_run_status = 'succeeded'",
    )
    op.add_column("source_assessment", sa.Column("process_run_id", sa.BigInteger(), nullable=True))
    op.execute(
        """
        UPDATE source_assessment AS assessment
           SET process_run_id = legacy_call.process_run_id
          FROM detection_call AS legacy_call
         WHERE legacy_call.release_id = assessment.release_id
           AND legacy_call.id = assessment.call_id
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM source_assessment WHERE process_run_id IS NULL
            ) THEN
                RAISE EXCEPTION 'legacy SourceAssessment process backfill was incomplete'
                    USING ERRCODE = '23514';
            END IF;
        END;
        $$
        """
    )
    op.alter_column(
        "source_assessment",
        "process_run_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_source_assessment_assertion_ref",
        "source_assessment",
        ["id", "release_id", "call_id", "process_run_id", "source_label", "confidence"],
    )
    op.drop_constraint(
        op.f("fk_source_assessment_call_same_release"), "source_assessment", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_source_assessment_call_same_release",
        "source_assessment",
        "detection_call",
        ["release_id", "call_id", "process_run_id"],
        ["release_id", "id", "process_run_id"],
        ondelete="RESTRICT",
    )
    op.create_table(
        "scientific_assertion",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("assertion_key", sa.String(length=255), nullable=False),
        sa.Column("release_id", sa.BigInteger(), nullable=False),
        sa.Column("call_id", sa.BigInteger(), nullable=False),
        sa.Column("locus_id", sa.BigInteger(), nullable=True),
        sa.Column("process_run_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "process_run_status",
            sa.String(length=32),
            server_default=sa.text("'succeeded'"),
            nullable=False,
        ),
        sa.Column("assertion_type", sa.String(length=64), nullable=False),
        sa.Column("predicate_key", sa.String(length=255), nullable=False),
        sa.Column("asserted_value", sa.Text(), nullable=False),
        sa.Column("source_assessment_id", sa.BigInteger(), nullable=True),
        sa.Column("source_label", sa.Text(), nullable=True),
        sa.Column("source_confidence", sa.String(length=32), nullable=True),
        sa.Column("lineage_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("lineage_snapshot_role", sa.String(length=64), nullable=True),
        sa.Column("lineage_term_id", sa.BigInteger(), nullable=True),
        sa.Column("result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "(assertion_type = 'hcvr' AND source_assessment_id IS NOT NULL "
            "AND source_label IS NOT NULL AND source_confidence IS NOT NULL "
            "AND lineage_snapshot_id IS NULL AND lineage_term_id IS NULL "
            "AND lineage_snapshot_role IS NULL) OR "
            "(assertion_type = 'viral_major_taxon' AND source_assessment_id IS NULL "
            "AND source_label IS NULL AND source_confidence IS NULL "
            "AND lineage_snapshot_id IS NOT NULL AND lineage_term_id IS NOT NULL "
            "AND lineage_snapshot_role IN "
            "('formal_viral_taxonomy', 'study_viral_lineage')) OR "
            "(assertion_type = 'vr_type' AND source_assessment_id IS NULL "
            "AND source_label IS NULL AND source_confidence IS NULL "
            "AND lineage_snapshot_id IS NULL AND lineage_term_id IS NULL "
            "AND lineage_snapshot_role IS NULL)",
            name=op.f("ck_scientific_assertion_typed_detail_matches_assertion_type"),
        ),
        sa.CheckConstraint(
            "assertion_type IN ('hcvr', 'viral_major_taxon', 'vr_type')",
            name=op.f("ck_scientific_assertion_valid_assertion_type"),
        ),
        sa.CheckConstraint(
            "process_run_status = 'succeeded'",
            name=op.f("ck_scientific_assertion_requires_succeeded_process_run"),
        ),
        sa.ForeignKeyConstraint(
            ["lineage_snapshot_id", "lineage_term_id"],
            ["lineage_term.snapshot_id", "lineage_term.id"],
            name="fk_scientific_assertion_lineage_term_same_snapshot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["process_run_id", "release_id", "process_run_status"],
            ["process_run.id", "process_run.release_id", "process_run.execution_status"],
            name="fk_scientific_assertion_succeeded_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["release_id", "call_id", "process_run_id"],
            ["detection_call.release_id", "detection_call.id", "detection_call.process_run_id"],
            name="fk_scientific_assertion_call_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["release_id", "lineage_snapshot_id", "lineage_snapshot_role"],
            [
                "release_lineage_snapshot.release_id",
                "release_lineage_snapshot.snapshot_id",
                "release_lineage_snapshot.role",
            ],
            name="fk_scientific_assertion_pinned_lineage_snapshot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["release_id", "locus_id"],
            ["eve_locus.release_id", "eve_locus.id"],
            name="fk_scientific_assertion_locus_same_release",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "source_assessment_id",
                "release_id",
                "call_id",
                "process_run_id",
                "source_label",
                "source_confidence",
            ],
            [
                "source_assessment.id",
                "source_assessment.release_id",
                "source_assessment.call_id",
                "source_assessment.process_run_id",
                "source_assessment.source_label",
                "source_assessment.confidence",
            ],
            name="fk_scientific_assertion_hcvr_source_assessment",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scientific_assertion")),
        sa.UniqueConstraint(
            "id",
            "release_id",
            "locus_id",
            "process_run_id",
            name="uq_scientific_assertion_membership_ref",
        ),
        sa.UniqueConstraint(
            "release_id", "assertion_key", name="uq_scientific_assertion_release_key"
        ),
        sa.UniqueConstraint("release_id", "id", name="uq_scientific_assertion_release_id_id"),
    )
    op.create_table(
        "assertion_evidence",
        sa.Column("release_id", sa.BigInteger(), nullable=False),
        sa.Column("assertion_id", sa.BigInteger(), nullable=False),
        sa.Column("evidence_id", sa.BigInteger(), nullable=False),
        sa.Column("relation", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "relation IN ('supports', 'contradicts', 'context')",
            name=op.f("ck_assertion_evidence_valid_relation"),
        ),
        sa.ForeignKeyConstraint(
            ["release_id", "assertion_id"],
            ["scientific_assertion.release_id", "scientific_assertion.id"],
            name="fk_assertion_evidence_assertion_same_release",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["release_id", "evidence_id"],
            ["evidence_item.release_id", "evidence_item.id"],
            name="fk_assertion_evidence_item_same_release",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "release_id",
            "assertion_id",
            "evidence_id",
            "relation",
            name=op.f("pk_assertion_evidence"),
        ),
    )
    op.create_table(
        "release_assertion_membership",
        sa.Column("release_id", sa.BigInteger(), nullable=False),
        sa.Column("assertion_id", sa.BigInteger(), nullable=False),
        sa.Column("locus_id", sa.BigInteger(), nullable=False),
        sa.Column("process_run_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "process_run_status",
            sa.String(length=32),
            server_default=sa.text("'succeeded'"),
            nullable=False,
        ),
        sa.Column("supporting_evidence_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "evidence_relation",
            sa.String(length=32),
            server_default=sa.text("'supports'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "evidence_relation = 'supports'",
            name=op.f("ck_release_assertion_membership_requires_supporting_evidence"),
        ),
        sa.CheckConstraint(
            "process_run_status = 'succeeded'",
            name=op.f("ck_release_assertion_membership_requires_succeeded_process_run"),
        ),
        sa.ForeignKeyConstraint(
            ["assertion_id", "release_id", "locus_id", "process_run_id"],
            [
                "scientific_assertion.id",
                "scientific_assertion.release_id",
                "scientific_assertion.locus_id",
                "scientific_assertion.process_run_id",
            ],
            name="fk_release_assertion_membership_assertion",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["process_run_id", "release_id", "process_run_status"],
            ["process_run.id", "process_run.release_id", "process_run.execution_status"],
            name="fk_release_assertion_membership_succeeded_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["release_id", "assertion_id", "supporting_evidence_id", "evidence_relation"],
            [
                "assertion_evidence.release_id",
                "assertion_evidence.assertion_id",
                "assertion_evidence.evidence_id",
                "assertion_evidence.relation",
            ],
            name="fk_release_assertion_membership_supporting_evidence",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["release_id", "locus_id"],
            ["release_locus_membership.release_id", "release_locus_membership.locus_id"],
            name="fk_release_assertion_membership_public_locus",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "release_id", "assertion_id", name=op.f("pk_release_assertion_membership")
        ),
    )

    # Coordinates are placement evidence, not EVELocus identity.  Development
    # builds of 0002 accidentally made an exact interval unique, which would reject
    # distinct source occurrences sharing the same coordinates.
    op.execute("DROP INDEX IF EXISTS uq_eve_locus_placement_exact_interval")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_eve_locus_placement_exact_interval
        ON eve_locus_placement (release_id, sequence_id, start0, end0)
        WHERE precision = 'exact'
        """
    )

    # Some development databases applied revision 0002 before its trigger guards
    # were finalized.  Reinstall every 0002 guard here so incremental upgrades are
    # self-contained and converge to the same schema as a fresh upgrade.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION eve_check_placement_bounds()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            expected_assembly_id bigint;
            expected_sequence_length bigint;
        BEGIN
            SELECT assembly_id, sequence_length
              INTO expected_assembly_id, expected_sequence_length
              FROM assembly_sequence
             WHERE id = NEW.sequence_id;

            IF NOT FOUND
               OR expected_assembly_id <> NEW.assembly_id
               OR NEW.end0 > expected_sequence_length THEN
                RAISE EXCEPTION
                    'placement % is outside sequence % or uses the wrong assembly',
                    NEW.placement_key,
                    NEW.sequence_id
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_eve_locus_placement_within_sequence';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_eve_locus_placement_bounds ON eve_locus_placement")
    op.execute(
        """
        CREATE TRIGGER trg_eve_locus_placement_bounds
        BEFORE INSERT OR UPDATE OF assembly_id, sequence_id, start0, end0
        ON eve_locus_placement
        FOR EACH ROW EXECUTE FUNCTION eve_check_placement_bounds()
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM eve_locus_placement AS placement
                  LEFT JOIN assembly_sequence AS sequence
                    ON sequence.id = placement.sequence_id
                 WHERE sequence.id IS NULL
                    OR sequence.assembly_id <> placement.assembly_id
                    OR placement.end0 > sequence.sequence_length
            ) THEN
                RAISE EXCEPTION 'existing locus placement is outside its sequence'
                    USING ERRCODE = '23514';
            END IF;
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION eve_require_quarantine_issue()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.outcome = 'quarantine'
               AND NOT EXISTS (
                   SELECT 1 FROM quarantine_issue WHERE ledger_id = NEW.id
               ) THEN
                RAISE EXCEPTION
                    'quarantine ledger entry % has no quarantine issue', NEW.id
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_import_ledger_quarantine_has_issue';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_import_ledger_quarantine_issue ON import_ledger")
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_import_ledger_quarantine_issue
        AFTER INSERT OR UPDATE ON import_ledger
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION eve_require_quarantine_issue()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION eve_preserve_quarantine_issue()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF EXISTS (
                   SELECT 1
                     FROM import_ledger
                    WHERE id = OLD.ledger_id AND outcome = 'quarantine'
               )
               AND NOT EXISTS (
                   SELECT 1 FROM quarantine_issue WHERE ledger_id = OLD.ledger_id
               ) THEN
                RAISE EXCEPTION
                    'cannot remove the last issue from quarantine ledger entry %', OLD.ledger_id
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_import_ledger_quarantine_has_issue';
            END IF;
            RETURN OLD;
        END;
        $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_quarantine_issue_preserved ON quarantine_issue")
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_quarantine_issue_preserved
        AFTER DELETE OR UPDATE ON quarantine_issue
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION eve_preserve_quarantine_issue()
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM import_ledger AS ledger
                 WHERE ledger.outcome = 'quarantine'
                   AND NOT EXISTS (
                       SELECT 1
                         FROM quarantine_issue AS issue
                        WHERE issue.ledger_id = ledger.id
                   )
            ) THEN
                RAISE EXCEPTION 'existing quarantine ledger entry has no issue'
                    USING ERRCODE = '23514';
            END IF;
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION eve_block_published_release_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            old_release_id bigint;
            new_release_id bigint;
        BEGIN
            old_release_id := CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.release_id END;
            new_release_id := CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE NEW.release_id END;
            IF EXISTS (
                SELECT 1
                  FROM dataset_release
                 WHERE id IN (old_release_id, new_release_id)
                   AND status IN ('published', 'deprecated')
            ) THEN
                RAISE EXCEPTION
                    'published/deprecated release-scoped rows are immutable'
                    USING ERRCODE = '55000';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$
        """
    )
    for table_name in RELEASE_SCOPED_TABLES:
        op.execute(
            sa.text(f"DROP TRIGGER IF EXISTS trg_{table_name}_published_immutable ON {table_name}")
        )
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table_name}_published_immutable "
                f"BEFORE INSERT OR UPDATE OR DELETE ON {table_name} "
                "FOR EACH ROW EXECUTE FUNCTION eve_block_published_release_mutation()"
            )
        )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION eve_guard_dataset_release_lifecycle()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'INSERT' AND NEW.status <> 'candidate' THEN
                RAISE EXCEPTION 'new releases must start as candidate'
                    USING ERRCODE = '23514';
            ELSIF TG_OP = 'DELETE' AND OLD.status IN ('published', 'deprecated') THEN
                RAISE EXCEPTION 'published/deprecated release rows cannot be deleted'
                    USING ERRCODE = '55000';
            ELSIF TG_OP = 'UPDATE' THEN
                IF OLD.status = 'deprecated' THEN
                    RAISE EXCEPTION 'deprecated release rows are immutable'
                        USING ERRCODE = '55000';
                ELSIF OLD.status = 'published' THEN
                    IF NEW.status <> 'deprecated'
                       OR ROW(
                           NEW.id,
                           NEW.dataset_id,
                           NEW.release_key,
                           NEW.schema_version,
                           NEW.manifest_sha256,
                           NEW.published_at,
                           NEW.supersedes_release_id,
                           NEW.created_at
                       ) IS DISTINCT FROM ROW(
                           OLD.id,
                           OLD.dataset_id,
                           OLD.release_key,
                           OLD.schema_version,
                           OLD.manifest_sha256,
                           OLD.published_at,
                           OLD.supersedes_release_id,
                           OLD.created_at
                       ) THEN
                        RAISE EXCEPTION
                            'published release content is immutable; only deprecation is allowed'
                            USING ERRCODE = '55000';
                    END IF;
                ELSIF NEW.status = 'published' AND OLD.status <> 'validated' THEN
                    RAISE EXCEPTION 'only a validated release can be published'
                        USING ERRCODE = '23514';
                ELSIF NEW.status = 'deprecated' THEN
                    RAISE EXCEPTION 'only a published release can be deprecated'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_dataset_release_lifecycle ON dataset_release")
    op.execute(
        """
        CREATE TRIGGER trg_dataset_release_lifecycle
        BEFORE INSERT OR UPDATE OR DELETE ON dataset_release
        FOR EACH ROW EXECUTE FUNCTION eve_guard_dataset_release_lifecycle()
        """
    )

    # Global identity/provenance facts are append-only.  Corrections and stronger
    # verification are represented by new versioned rows instead of rewriting history.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION eve_block_truth_update_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'global identity/provenance truth is append-only; create a new version'
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    for table_name in GLOBAL_APPEND_ONLY_TABLES:
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only ON {table_name}"))
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table_name}_append_only "
                f"BEFORE UPDATE OR DELETE ON {table_name} "
                "FOR EACH ROW EXECUTE FUNCTION eve_block_truth_update_delete()"
            )
        )

    # QuarantineIssue is indirectly release-scoped through ImportLedger, so it needs
    # a join-aware guard rather than the generic release_id trigger.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION eve_block_published_quarantine_issue_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            old_ledger_id bigint;
            new_ledger_id bigint;
        BEGIN
            old_ledger_id := CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.ledger_id END;
            new_ledger_id := CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE NEW.ledger_id END;

            IF EXISTS (
                SELECT 1
                  FROM import_ledger AS ledger
                  JOIN dataset_release AS release
                    ON release.id = ledger.release_id
                 WHERE ledger.id IN (old_ledger_id, new_ledger_id)
                   AND release.status IN ('published', 'deprecated')
            ) THEN
                RAISE EXCEPTION
                    'published/deprecated quarantine issues are immutable'
                    USING ERRCODE = '55000';
            END IF;

            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_quarantine_issue_published_immutable ON quarantine_issue"
    )
    op.execute(
        """
        CREATE TRIGGER trg_quarantine_issue_published_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON quarantine_issue
        FOR EACH ROW
        EXECUTE FUNCTION eve_block_published_quarantine_issue_mutation()
        """
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_quarantine_issue_published_immutable ON quarantine_issue"
    )
    op.execute("DROP FUNCTION IF EXISTS eve_block_published_quarantine_issue_mutation()")

    for table_name in reversed(GLOBAL_APPEND_ONLY_TABLES):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only ON {table_name}"))
    op.execute("DROP FUNCTION IF EXISTS eve_block_truth_update_delete()")

    op.drop_table("release_assertion_membership")
    op.drop_table("assertion_evidence")
    op.drop_table("scientific_assertion")

    op.drop_constraint(
        "fk_source_assessment_call_same_release",
        "source_assessment",
        type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk_source_assessment_call_same_release"),
        "source_assessment",
        "detection_call",
        ["release_id", "call_id"],
        ["release_id", "id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "uq_source_assessment_assertion_ref",
        "source_assessment",
        type_="unique",
    )
    op.drop_column("source_assessment", "process_run_id")

    op.drop_constraint(
        op.f("ck_detection_call_requires_succeeded_process_run"),
        "detection_call",
        type_="check",
    )
    op.drop_constraint(
        "fk_detection_call_process_run_same_release",
        "detection_call",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_detection_call_release_id_process_run",
        "detection_call",
        type_="unique",
    )
    op.drop_column("detection_call", "process_run_status")
    op.drop_column("detection_call", "process_run_id")

    op.drop_table("process_run")
    op.drop_table("release_method_definition")
    op.drop_table("evidence_item")
    op.drop_table("method_definition")
    op.execute(
        "DELETE FROM source_artifact "
        "WHERE artifact_key = "
        "'internal:migration:0003:legacy-process-definition:v1'"
    )
    op.execute(
        "DELETE FROM source_snapshot "
        "WHERE snapshot_key = "
        "'internal:migration:0003:legacy-process-backfill:v1'"
    )
