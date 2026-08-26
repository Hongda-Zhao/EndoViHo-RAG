"""Fail closed on publication and bind ledger outcomes to source records

Revision ID: 0005_m1_fail_closed_publication
Revises: 0004_m1_shared_intervals
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_m1_fail_closed_publication"
down_revision: str | Sequence[str] | None = "0004_m1_shared_intervals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FAIL_CLOSED_LIFECYCLE_SQL = """
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
        ELSIF NEW.status IS DISTINCT FROM OLD.status
              AND NEW.status IN ('validated', 'published') THEN
            RAISE EXCEPTION
                'release promotion is disabled: no trusted validation-receipt workflow exists'
                USING ERRCODE = '55000',
                      HINT = 'Keep the release as candidate. Implement and migrate a '
                             'verified validation-receipt workflow before promotion.';
        ELSIF NEW.status = 'deprecated' THEN
            RAISE EXCEPTION 'only a published release can be deprecated'
                USING ERRCODE = '23514';
        ELSIF OLD.status = 'rejected' AND NEW.status <> 'rejected' THEN
            RAISE EXCEPTION 'rejected releases cannot be reopened in place'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$
"""


_LEGACY_LIFECYCLE_SQL = """
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


def upgrade() -> None:
    """Add provenance constraints only after a non-mutating compatibility check."""

    op.execute(
        """
        DO $$
        DECLARE
            invalid_ledger_id bigint;
            unreceipted_release_key text;
        BEGIN
            SELECT ledger.id
              INTO invalid_ledger_id
              FROM import_ledger AS ledger
              JOIN detection_call AS call
                ON call.release_id = ledger.release_id
               AND call.id = ledger.call_id
             WHERE call.source_record_id IS DISTINCT FROM ledger.source_record_id
             LIMIT 1;

            IF FOUND THEN
                RAISE EXCEPTION
                    'cannot enforce call provenance: ImportLedger % links a call from a '
                    'different SourceRecord', invalid_ledger_id
                    USING ERRCODE = '23514',
                          HINT = 'Keep the database at 0004 and re-import the frozen '
                                 'candidate release. Do not rewrite source provenance; '
                                 'no 0005 DDL has been applied.';
            END IF;

            SELECT ledger.id
              INTO invalid_ledger_id
              FROM import_ledger AS ledger
              JOIN eve_locus AS locus
                ON locus.release_id = ledger.release_id
               AND locus.id = ledger.locus_id
             WHERE locus.source_record_id IS DISTINCT FROM ledger.source_record_id
             LIMIT 1;

            IF FOUND THEN
                RAISE EXCEPTION
                    'cannot enforce locus provenance: ImportLedger % links a locus from a '
                    'different SourceRecord', invalid_ledger_id
                    USING ERRCODE = '23514',
                          HINT = 'Keep the database at 0004 and re-import the frozen '
                                 'candidate release. Do not rewrite source provenance; '
                                 'no 0005 DDL has been applied.';
            END IF;

            SELECT release_key
              INTO unreceipted_release_key
              FROM dataset_release
             WHERE status IN ('validated', 'published', 'deprecated')
             LIMIT 1;

            IF FOUND THEN
                RAISE EXCEPTION
                    'cannot trust legacy validated/published/deprecated release % without a '
                    'validation receipt',
                    unreceipted_release_key
                    USING ERRCODE = '55000',
                          HINT = 'Keep the database at 0004 and preserve it for audit. '
                                 'Rebuild the frozen source as a new candidate at head; '
                                 'no 0005 DDL has been applied.';
            END IF;
        END;
        $$
        """
    )

    op.alter_column(
        "method_definition",
        "definition_artifact_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )

    op.drop_constraint(
        "fk_import_ledger_call_same_release",
        "import_ledger",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_import_ledger_locus_same_release",
        "import_ledger",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_detection_call_source_record",
        "detection_call",
        type_="unique",
    )

    op.create_unique_constraint(
        "uq_detection_call_source_record_process_run",
        "detection_call",
        ["release_id", "source_record_id", "process_run_id"],
    )
    op.create_unique_constraint(
        "uq_detection_call_release_id_source_record",
        "detection_call",
        ["release_id", "id", "source_record_id"],
    )
    op.create_unique_constraint(
        "uq_eve_locus_release_id_source_record",
        "eve_locus",
        ["release_id", "id", "source_record_id"],
    )
    op.create_foreign_key(
        "fk_import_ledger_call_same_source_record",
        "import_ledger",
        "detection_call",
        ["release_id", "call_id", "source_record_id"],
        ["release_id", "id", "source_record_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_import_ledger_locus_same_source_record",
        "import_ledger",
        "eve_locus",
        ["release_id", "locus_id", "source_record_id"],
        ["release_id", "id", "source_record_id"],
        ondelete="RESTRICT",
    )

    op.execute(_FAIL_CLOSED_LIFECYCLE_SQL)


def downgrade() -> None:
    """Restore 0004 only when its stricter cardinality can be represented."""

    op.execute(
        """
        DO $$
        DECLARE
            duplicate_source_record_id bigint;
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM method_definition
                 WHERE definition_artifact_id IS NULL
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade: MethodDefinition rows without definition artifacts exist'
                    USING ERRCODE = '23502',
                          HINT = 'Do not fabricate an artifact link. Remain at 0005 or '
                                 'remove the unpublished replacement method rows.';
            END IF;

            SELECT source_record_id
              INTO duplicate_source_record_id
              FROM detection_call
             GROUP BY release_id, source_record_id
            HAVING count(*) > 1
             LIMIT 1;

            IF FOUND THEN
                RAISE EXCEPTION
                    'cannot downgrade: SourceRecord % has multiple method-specific calls',
                    duplicate_source_record_id
                    USING ERRCODE = '23505',
                          HINT = 'Revision 0004 cannot represent multiple ProcessRun calls '
                                 'for one source row; remain at 0005.';
            END IF;
        END;
        $$
        """
    )

    op.drop_constraint(
        "fk_import_ledger_locus_same_source_record",
        "import_ledger",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_import_ledger_call_same_source_record",
        "import_ledger",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_eve_locus_release_id_source_record",
        "eve_locus",
        type_="unique",
    )
    op.drop_constraint(
        "uq_detection_call_release_id_source_record",
        "detection_call",
        type_="unique",
    )
    op.drop_constraint(
        "uq_detection_call_source_record_process_run",
        "detection_call",
        type_="unique",
    )

    op.create_unique_constraint(
        "uq_detection_call_source_record",
        "detection_call",
        ["release_id", "source_record_id"],
    )
    op.create_foreign_key(
        "fk_import_ledger_call_same_release",
        "import_ledger",
        "detection_call",
        ["release_id", "call_id"],
        ["release_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_import_ledger_locus_same_release",
        "import_ledger",
        "eve_locus",
        ["release_id", "locus_id"],
        ["release_id", "id"],
        ondelete="RESTRICT",
    )
    op.alter_column(
        "method_definition",
        "definition_artifact_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.execute(_LEGACY_LIFECYCLE_SQL)
