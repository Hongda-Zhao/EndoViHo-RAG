"""Add trusted structured-release receipts and publication authorization.

Revision ID: 0011_dataset_validation_receipt
Revises: 0010_m3_lock_hardening
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_dataset_validation_receipt"
down_revision: str | Sequence[str] | None = "0010_m3_lock_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_VALIDATED_CHILD_GUARD_SQL = """
CREATE OR REPLACE FUNCTION eve_block_published_release_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    old_status text;
    new_status text;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT status INTO new_status
          FROM dataset_release
         WHERE id = NEW.release_id
           FOR SHARE;
        IF new_status IN ('validated', 'published', 'deprecated') THEN
            RAISE EXCEPTION 'validated/published/deprecated release-scoped rows are immutable'
                USING ERRCODE = '55000';
        END IF;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        SELECT status INTO old_status
          FROM dataset_release
         WHERE id = OLD.release_id
           FOR SHARE;
        IF old_status IN ('validated', 'published', 'deprecated') THEN
            RAISE EXCEPTION 'validated/published/deprecated release-scoped rows are immutable'
                USING ERRCODE = '55000';
        END IF;
        RETURN OLD;
    END IF;

    PERFORM 1
      FROM dataset_release
     WHERE id IN (OLD.release_id, NEW.release_id)
     ORDER BY id
       FOR SHARE;
    SELECT status INTO old_status FROM dataset_release WHERE id = OLD.release_id;
    SELECT status INTO new_status FROM dataset_release WHERE id = NEW.release_id;
    IF old_status IN ('validated', 'published', 'deprecated')
       OR new_status IN ('validated', 'published', 'deprecated') THEN
        RAISE EXCEPTION 'validated/published/deprecated release-scoped rows are immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$
"""


_RECEIPT_GUARD_SQL = """
CREATE OR REPLACE FUNCTION eve_guard_dataset_validation_receipt()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    release_status text;
    release_key_value text;
    release_schema_version text;
    release_manifest_sha256 text;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'dataset validation receipts are immutable'
            USING ERRCODE = '55000';
    END IF;
    SELECT status, release_key, schema_version, manifest_sha256
      INTO release_status, release_key_value, release_schema_version,
           release_manifest_sha256
      FROM dataset_release
     WHERE id = NEW.release_id
       FOR SHARE;
    IF release_status IS DISTINCT FROM 'candidate' THEN
        RAISE EXCEPTION 'dataset validation receipt requires a candidate release'
            USING ERRCODE = '55000';
    END IF;
    IF NEW.trusted AND (
        NEW.status IS DISTINCT FROM 'passed'
        OR NEW.validation_evidence ->> 'evidence_schema_version'
           IS DISTINCT FROM 'dataset-validation-evidence-v1'
        OR NEW.validation_evidence #>> '{validation_input,input_schema_version}'
           IS DISTINCT FROM 'dataset-validation-input-v3'
        OR NEW.validation_evidence
             #>> '{validation_input,candidate_validation_input,input_schema_version}'
           IS DISTINCT FROM 'dataset-candidate-validation-input-v1'
        OR NEW.validation_evidence #>> '{validation_input,release_key}'
           IS DISTINCT FROM release_key_value
        OR NEW.validation_evidence #>> '{validation_input,release_schema_version}'
           IS DISTINCT FROM release_schema_version
        OR NEW.validation_evidence
             #>> '{validation_input,candidate_validation_input,release_key}'
           IS DISTINCT FROM release_key_value
        OR NEW.validation_evidence
             #>> '{validation_input,candidate_validation_input,release_schema_version}'
           IS DISTINCT FROM release_schema_version
        OR NEW.validation_evidence #>> '{validation_input,release_manifest_sha256}'
           IS DISTINCT FROM release_manifest_sha256
        OR NEW.validation_evidence
             #>> '{validation_input,candidate_validation_input,release_manifest_sha256}'
           IS DISTINCT FROM release_manifest_sha256
        OR NEW.manifest_sha256 IS DISTINCT FROM release_manifest_sha256
        OR NEW.validation_evidence #>> '{validation_input,expected_dependency_graph_sha256}'
           IS DISTINCT FROM NEW.dependency_graph_sha256
        OR NEW.validation_evidence
             #>> '{validation_input,candidate_validation_input,expected_dependency_graph_sha256}'
           IS DISTINCT FROM NEW.dependency_graph_sha256
        OR NEW.validation_evidence #>> '{dependency_graph_sha256}'
           IS DISTINCT FROM NEW.dependency_graph_sha256
        OR NEW.validation_evidence #>> '{validation_input,validation_request_sha256}'
           IS DISTINCT FROM NEW.validation_request_sha256
        OR NEW.validation_evidence
             #>> '{validation_input,candidate_validation_input,validation_request_sha256}'
           IS DISTINCT FROM NEW.validation_request_sha256
        OR NEW.validation_evidence
             #>> '{validation_input,candidate_validation_input_sha256}'
           IS DISTINCT FROM NEW.candidate_validation_input_sha256
        OR NEW.validation_evidence
             #>> '{validation_input,candidate_validation_input,input_sha256}'
           IS DISTINCT FROM NEW.candidate_validation_input_sha256
        OR NEW.validation_evidence #>> '{validation_input,activation_evidence_sha256}'
           IS DISTINCT FROM NEW.activation_evidence_sha256
        OR NEW.validation_evidence
             #>> '{validation_input,activation_evidence,evidence_sha256}'
           IS DISTINCT FROM NEW.activation_evidence_sha256
        OR NEW.validation_evidence
             #>> '{validation_input,activation_evidence,evidence_schema_version}'
           IS DISTINCT FROM 'dataset-activation-evidence-v2'
        OR NEW.validation_evidence
             #>> '{validation_input,activation_evidence,candidate_validation_input_sha256}'
           IS DISTINCT FROM NEW.validation_evidence
             #>> '{validation_input,candidate_validation_input,input_sha256}'
        OR NEW.validation_evidence
             #>> ARRAY[
                 'validation_input', 'candidate_validation_input',
                 'candidate_activation_evidence', 'evidence_schema_version'
             ]
           IS DISTINCT FROM 'dataset-candidate-activation-evidence-v1'
        OR NEW.validation_evidence
             #>> ARRAY[
                 'validation_input', 'candidate_validation_input',
                 'candidate_activation_evidence', 'evidence_sha256'
             ]
           IS DISTINCT FROM NEW.validation_evidence
             #>> ARRAY[
                 'validation_input', 'candidate_validation_input',
                 'candidate_activation_evidence_sha256'
             ]
        OR NEW.validation_evidence
             #>> ARRAY[
                 'validation_input', 'candidate_validation_input',
                 'candidate_activation_evidence',
                 'structured_activation_manifest_sha256'
             ]
           IS DISTINCT FROM release_manifest_sha256
        OR NEW.validation_evidence #>> '{validation_input,input_sha256}'
           IS DISTINCT FROM NEW.validation_input_sha256
        OR NEW.validation_evidence #>> '{validation_report_sha256}'
           IS DISTINCT FROM NEW.validation_report_sha256
        OR NEW.validation_evidence #>> '{validation_input,validator_code_sha256}'
           IS DISTINCT FROM NEW.validator_code_sha256
        OR NEW.validation_evidence #>> '{validation_report,valid}'
           IS DISTINCT FROM 'true'
        OR NEW.validation_evidence
             #>> '{validation_input,activation_evidence,clean_rebuild_passed}'
           IS DISTINCT FROM 'true'
        OR NEW.validation_evidence
             #>> '{validation_input,activation_evidence,structured_benchmark_passed}'
           IS DISTINCT FROM 'true'
        OR NEW.validation_evidence
             #>> '{validation_input,activation_evidence,hybrid_benchmark_passed}'
           IS DISTINCT FROM 'true'
        OR NEW.validation_evidence
             #>> '{validation_input,activation_evidence,human_review_passed}'
           IS DISTINCT FROM 'true'
        OR NEW.complete_lineage_closure_roles IS DISTINCT FROM
           NEW.validation_evidence #> '{validation_input,complete_lineage_closure_roles}'
        OR NEW.complete_lineage_closure_roles IS DISTINCT FROM
           NEW.validation_evidence
             #> '{validation_input,candidate_validation_input,complete_lineage_closure_roles}'
    ) THEN
        RAISE EXCEPTION 'trusted dataset validation receipt evidence is incomplete or incoherent'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$
"""


_LIFECYCLE_SQL = """
CREATE OR REPLACE FUNCTION eve_guard_dataset_release_lifecycle()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    has_receipt boolean;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'candidate' THEN
            RAISE EXCEPTION 'new releases must start as candidate'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        IF OLD.status IN ('validated', 'published', 'deprecated') THEN
            RAISE EXCEPTION 'validated/published/deprecated release rows are immutable'
                USING ERRCODE = '55000';
        END IF;
        RETURN OLD;
    END IF;

    IF (OLD.status <> 'candidate' OR NEW.status <> 'candidate')
       AND ROW(
           NEW.id, NEW.dataset_id, NEW.release_key, NEW.schema_version,
           NEW.manifest_sha256, NEW.supersedes_release_id, NEW.created_at
       ) IS DISTINCT FROM ROW(
           OLD.id, OLD.dataset_id, OLD.release_key, OLD.schema_version,
           OLD.manifest_sha256, OLD.supersedes_release_id, OLD.created_at
       ) THEN
        RAISE EXCEPTION 'validated release identity and dependency manifest are immutable'
            USING ERRCODE = '55000';
    END IF;

    IF NEW.status = OLD.status
       AND NEW.published_at IS NOT DISTINCT FROM OLD.published_at THEN
        RETURN NEW;
    END IF;

    IF OLD.status IN ('published', 'deprecated', 'rejected') THEN
        IF OLD.status = 'published'
           AND NEW.status = 'deprecated'
           AND NEW.published_at IS NOT DISTINCT FROM OLD.published_at THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'terminal dataset release state is immutable'
            USING ERRCODE = '55000';
    END IF;

    IF NEW.status IN ('validated', 'published') THEN
        SELECT EXISTS (
            SELECT 1
              FROM dataset_validation_receipt AS receipt
             WHERE receipt.release_id = OLD.id
               AND receipt.status = 'passed'
               AND receipt.trusted
               AND receipt.manifest_sha256 = OLD.manifest_sha256
               AND receipt.validation_evidence #>> '{validation_report,valid}' = 'true'
               AND receipt.validation_evidence
                     #>> '{validation_input,activation_evidence,clean_rebuild_passed}' = 'true'
               AND receipt.validation_evidence
                     #>> '{validation_input,activation_evidence,structured_benchmark_passed}'
                   = 'true'
               AND receipt.validation_evidence
                     #>> '{validation_input,activation_evidence,hybrid_benchmark_passed}' = 'true'
               AND receipt.validation_evidence
                     #>> '{validation_input,activation_evidence,human_review_passed}' = 'true'
        ) INTO has_receipt;
        IF NOT has_receipt THEN
            RAISE EXCEPTION 'trusted passing dataset validation receipt is required'
                USING ERRCODE = '55000';
        END IF;
    END IF;

    IF OLD.status = 'candidate' AND NEW.status = 'validated'
       AND NEW.published_at IS NULL THEN
        RETURN NEW;
    ELSIF OLD.status = 'validated' AND NEW.status = 'published'
          AND NEW.published_at IS NOT NULL THEN
        RETURN NEW;
    ELSIF OLD.status = 'candidate' AND NEW.status = 'rejected'
          AND NEW.published_at IS NULL THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'invalid dataset release lifecycle transition'
        USING ERRCODE = '23514';
END;
$$
"""


_DEPENDENCY_FREEZE_SQL = """
CREATE OR REPLACE FUNCTION eve_block_bound_lineage_child_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM 1
      FROM release_lineage_snapshot AS binding
      JOIN dataset_release AS release ON release.id = binding.release_id
      JOIN (SELECT DISTINCT snapshot_id FROM inserted_rows) AS inserted
        ON inserted.snapshot_id = binding.snapshot_id
     ORDER BY release.id
       FOR SHARE OF release;
    IF EXISTS (
        SELECT 1
          FROM release_lineage_snapshot AS binding
          JOIN dataset_release AS release ON release.id = binding.release_id
          JOIN (SELECT DISTINCT snapshot_id FROM inserted_rows) AS inserted
            ON inserted.snapshot_id = binding.snapshot_id
         WHERE release.status IN ('validated', 'published', 'deprecated')
    ) THEN
        RAISE EXCEPTION 'validated release lineage snapshot is immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION eve_block_bound_source_child_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM 1
      FROM release_source_snapshot AS binding
      JOIN dataset_release AS release ON release.id = binding.release_id
      JOIN (SELECT DISTINCT snapshot_id FROM inserted_rows) AS inserted
        ON inserted.snapshot_id = binding.source_snapshot_id
     ORDER BY release.id
       FOR SHARE OF release;
    IF EXISTS (
        SELECT 1
          FROM release_source_snapshot AS binding
          JOIN dataset_release AS release ON release.id = binding.release_id
          JOIN (SELECT DISTINCT snapshot_id FROM inserted_rows) AS inserted
            ON inserted.snapshot_id = binding.source_snapshot_id
         WHERE release.status IN ('validated', 'published', 'deprecated')
    ) THEN
        RAISE EXCEPTION 'validated release source snapshot is immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION eve_block_bound_assembly_child_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM 1
      FROM release_assembly_membership AS binding
      JOIN dataset_release AS release ON release.id = binding.release_id
      JOIN (SELECT DISTINCT assembly_id FROM inserted_rows) AS inserted
        ON inserted.assembly_id = binding.assembly_id
     ORDER BY release.id
       FOR SHARE OF release;
    IF EXISTS (
        SELECT 1
          FROM release_assembly_membership AS binding
          JOIN dataset_release AS release ON release.id = binding.release_id
          JOIN (SELECT DISTINCT assembly_id FROM inserted_rows) AS inserted
            ON inserted.assembly_id = binding.assembly_id
         WHERE release.status IN ('validated', 'published', 'deprecated')
    ) THEN
        RAISE EXCEPTION 'validated release assembly is immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN NULL;
END;
$$
"""


_VALIDATED_QUARANTINE_GUARD_SQL = """
CREATE OR REPLACE FUNCTION eve_block_published_quarantine_issue_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    old_release_id bigint;
    new_release_id bigint;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        SELECT release_id INTO old_release_id FROM import_ledger WHERE id = OLD.ledger_id;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        SELECT release_id INTO new_release_id FROM import_ledger WHERE id = NEW.ledger_id;
    END IF;
    PERFORM 1
      FROM dataset_release
     WHERE id IN (old_release_id, new_release_id)
     ORDER BY id
       FOR SHARE;
    IF EXISTS (
        SELECT 1
          FROM dataset_release
         WHERE id IN (old_release_id, new_release_id)
           AND status IN ('validated', 'published', 'deprecated')
    ) THEN
        RAISE EXCEPTION 'validated/published/deprecated quarantine issues are immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$
"""


_PUBLISHED_QUARANTINE_GUARD_SQL = _VALIDATED_QUARANTINE_GUARD_SQL.replace(
    "validated/published/deprecated", "published/deprecated"
).replace("('validated', 'published', 'deprecated')", "('published', 'deprecated')")


_FAIL_CLOSED_LIFECYCLE_SQL = """
CREATE OR REPLACE FUNCTION eve_guard_dataset_release_lifecycle()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' AND NEW.status <> 'candidate' THEN
        RAISE EXCEPTION 'new releases must start as candidate' USING ERRCODE = '23514';
    ELSIF TG_OP = 'DELETE' AND OLD.status IN ('published', 'deprecated') THEN
        RAISE EXCEPTION 'published/deprecated release rows cannot be deleted'
            USING ERRCODE = '55000';
    ELSIF TG_OP = 'UPDATE' THEN
        IF OLD.status = 'deprecated' THEN
            RAISE EXCEPTION 'deprecated release rows are immutable' USING ERRCODE = '55000';
        ELSIF OLD.status = 'published' THEN
            IF NEW.status <> 'deprecated'
               OR ROW(
                   NEW.id, NEW.dataset_id, NEW.release_key, NEW.schema_version,
                   NEW.manifest_sha256, NEW.published_at, NEW.supersedes_release_id,
                   NEW.created_at
               ) IS DISTINCT FROM ROW(
                   OLD.id, OLD.dataset_id, OLD.release_key, OLD.schema_version,
                   OLD.manifest_sha256, OLD.published_at, OLD.supersedes_release_id,
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
                USING ERRCODE = '55000';
        ELSIF NEW.status = 'deprecated' THEN
            RAISE EXCEPTION 'only a published release can be deprecated'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$
"""


_PUBLISHED_CHILD_GUARD_SQL = _VALIDATED_CHILD_GUARD_SQL.replace(
    "validated/published/deprecated", "published/deprecated"
).replace("('validated', 'published', 'deprecated')", "('published', 'deprecated')")


def upgrade() -> None:
    """Install immutable receipts and unlock only receipt-backed transitions."""

    op.create_table(
        "dataset_validation_receipt",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("receipt_key", sa.String(length=255), nullable=False),
        sa.Column("release_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("trusted", sa.Boolean(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("dependency_graph_sha256", sa.String(length=64), nullable=False),
        sa.Column("validation_request_sha256", sa.String(length=64), nullable=False),
        sa.Column("activation_evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "candidate_validation_input_sha256", sa.String(length=64), nullable=False
        ),
        sa.Column("validation_input_sha256", sa.String(length=64), nullable=False),
        sa.Column("validation_report_sha256", sa.String(length=64), nullable=False),
        sa.Column("validator_code_sha256", sa.String(length=64), nullable=False),
        sa.Column("receipt_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "complete_lineage_closure_roles",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("validation_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "receipt_key ~ '^dataset-receipt:sha256:[0-9a-f]{64}$'",
            name=op.f("ck_dataset_validation_receipt_valid_receipt_key"),
        ),
        sa.CheckConstraint(
            "status IN ('passed', 'failed')",
            name=op.f("ck_dataset_validation_receipt_valid_status"),
        ),
        sa.CheckConstraint(
            "NOT trusted OR status = 'passed'",
            name=op.f("ck_dataset_validation_receipt_trusted_receipt_must_pass"),
        ),
        sa.CheckConstraint(
            "manifest_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_dataset_validation_receipt_valid_manifest"),
        ),
        sa.CheckConstraint(
            "dependency_graph_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_dataset_validation_receipt_valid_dependency_graph"),
        ),
        sa.CheckConstraint(
            "validation_request_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_dataset_validation_receipt_valid_validation_request"),
        ),
        sa.CheckConstraint(
            "activation_evidence_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_dataset_validation_receipt_valid_activation_evidence"),
        ),
        sa.CheckConstraint(
            "candidate_validation_input_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f(
                "ck_dataset_validation_receipt_valid_candidate_validation_input"
            ),
        ),
        sa.CheckConstraint(
            "validation_input_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_dataset_validation_receipt_valid_validation_input"),
        ),
        sa.CheckConstraint(
            "validation_report_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_dataset_validation_receipt_valid_validation_report"),
        ),
        sa.CheckConstraint(
            "validator_code_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_dataset_validation_receipt_valid_validator_code"),
        ),
        sa.CheckConstraint(
            "receipt_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_dataset_validation_receipt_valid_receipt"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(complete_lineage_closure_roles) = 'array'",
            name=op.f("ck_dataset_validation_receipt_closure_roles_are_array"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(validation_evidence) = 'object'",
            name=op.f("ck_dataset_validation_receipt_evidence_is_object"),
        ),
        sa.ForeignKeyConstraint(
            ["release_id"],
            ["dataset_release.id"],
            name=op.f("fk_dataset_validation_receipt_release_id_dataset_release"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_validation_receipt")),
        sa.UniqueConstraint("receipt_key", name=op.f("uq_dataset_validation_receipt_receipt_key")),
    )
    op.create_index(
        "uq_dataset_validation_receipt_passing_release",
        "dataset_validation_receipt",
        ["release_id"],
        unique=True,
        postgresql_where=sa.text("status = 'passed' AND trusted"),
    )
    op.execute(_RECEIPT_GUARD_SQL)
    op.execute(
        """
        CREATE TRIGGER trg_dataset_validation_receipt_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON dataset_validation_receipt
        FOR EACH ROW EXECUTE FUNCTION eve_guard_dataset_validation_receipt()
        """
    )
    op.execute(_VALIDATED_CHILD_GUARD_SQL)
    op.execute(_DEPENDENCY_FREEZE_SQL)
    op.execute(_VALIDATED_QUARANTINE_GUARD_SQL)
    for table_name in ("lineage_term", "lineage_alias", "lineage_closure"):
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table_name}_validated_snapshot_freeze "
                f"AFTER INSERT ON {table_name} "
                "REFERENCING NEW TABLE AS inserted_rows FOR EACH STATEMENT "
                "EXECUTE FUNCTION eve_block_bound_lineage_child_insert()"
            )
        )
    for table_name in ("source_artifact", "source_record"):
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table_name}_validated_snapshot_freeze "
                f"AFTER INSERT ON {table_name} "
                "REFERENCING NEW TABLE AS inserted_rows FOR EACH STATEMENT "
                "EXECUTE FUNCTION eve_block_bound_source_child_insert()"
            )
        )
    op.execute(
        """
        CREATE TRIGGER trg_assembly_sequence_validated_assembly_freeze
        AFTER INSERT ON assembly_sequence
        REFERENCING NEW TABLE AS inserted_rows FOR EACH STATEMENT
        EXECUTE FUNCTION eve_block_bound_assembly_child_insert()
        """
    )
    op.execute(_LIFECYCLE_SQL)


def downgrade() -> None:
    """Restore the intentionally fail-closed pre-receipt structured lifecycle."""

    op.execute(_FAIL_CLOSED_LIFECYCLE_SQL)
    op.execute(_PUBLISHED_CHILD_GUARD_SQL)
    op.execute(_PUBLISHED_QUARANTINE_GUARD_SQL)
    op.execute(
        "DROP TRIGGER IF EXISTS trg_assembly_sequence_validated_assembly_freeze "
        "ON assembly_sequence"
    )
    for table_name in ("source_artifact", "source_record"):
        op.execute(
            sa.text(
                f"DROP TRIGGER IF EXISTS trg_{table_name}_validated_snapshot_freeze ON {table_name}"
            )
        )
    for table_name in ("lineage_term", "lineage_alias", "lineage_closure"):
        op.execute(
            sa.text(
                f"DROP TRIGGER IF EXISTS trg_{table_name}_validated_snapshot_freeze ON {table_name}"
            )
        )
    op.execute("DROP FUNCTION IF EXISTS eve_block_bound_assembly_child_insert()")
    op.execute("DROP FUNCTION IF EXISTS eve_block_bound_source_child_insert()")
    op.execute("DROP FUNCTION IF EXISTS eve_block_bound_lineage_child_insert()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_dataset_validation_receipt_immutable "
        "ON dataset_validation_receipt"
    )
    op.execute("DROP FUNCTION IF EXISTS eve_guard_dataset_validation_receipt()")
    op.drop_index(
        "uq_dataset_validation_receipt_passing_release",
        table_name="dataset_validation_receipt",
    )
    op.drop_table("dataset_validation_receipt")
