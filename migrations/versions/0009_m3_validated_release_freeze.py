"""Freeze release-scoped corpus rows as soon as validation succeeds

Revision ID: 0009_m3_validated_freeze
Revises: 0008_m3_child_reparent_guard
Create Date: 2026-08-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_m3_validated_freeze"
down_revision: str | Sequence[str] | None = "0008_m3_child_reparent_guard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_VALIDATED_GUARD_SQL = """
CREATE OR REPLACE FUNCTION eve_guard_published_corpus_child()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    old_status text;
    new_status text;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT status INTO new_status
          FROM corpus_release
         WHERE id = NEW.release_id
           FOR KEY SHARE;
        IF new_status IN ('validated', 'published', 'retired') THEN
            RAISE EXCEPTION 'validated, published, or retired corpus content is immutable'
                USING ERRCODE = '55000';
        END IF;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        SELECT status INTO old_status
          FROM corpus_release
         WHERE id = OLD.release_id
           FOR KEY SHARE;
        IF old_status IN ('validated', 'published', 'retired') THEN
            RAISE EXCEPTION 'validated, published, or retired corpus content is immutable'
                USING ERRCODE = '55000';
        END IF;
        RETURN OLD;
    END IF;

    PERFORM 1
      FROM corpus_release
     WHERE id IN (OLD.release_id, NEW.release_id)
     ORDER BY id
       FOR KEY SHARE;
    SELECT status INTO old_status
      FROM corpus_release
     WHERE id = OLD.release_id;
    SELECT status INTO new_status
      FROM corpus_release
     WHERE id = NEW.release_id;

    IF old_status IN ('validated', 'published', 'retired')
       OR new_status IN ('validated', 'published', 'retired') THEN
        RAISE EXCEPTION 'validated, published, or retired corpus content is immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$
"""


_PREVIOUS_GUARD_SQL = """
CREATE OR REPLACE FUNCTION eve_guard_published_corpus_child()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    old_status text;
    new_status text;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT status INTO new_status
          FROM corpus_release
         WHERE id = NEW.release_id;
        IF new_status IN ('published', 'retired') THEN
            RAISE EXCEPTION 'published or retired corpus content is immutable'
                USING ERRCODE = '55000';
        END IF;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        SELECT status INTO old_status
          FROM corpus_release
         WHERE id = OLD.release_id;
        IF old_status IN ('published', 'retired') THEN
            RAISE EXCEPTION 'published or retired corpus content is immutable'
                USING ERRCODE = '55000';
        END IF;
        RETURN OLD;
    END IF;

    SELECT status INTO old_status
      FROM corpus_release
     WHERE id = OLD.release_id;
    SELECT status INTO new_status
      FROM corpus_release
     WHERE id = NEW.release_id;

    IF old_status IN ('published', 'retired')
       OR new_status IN ('published', 'retired') THEN
        RAISE EXCEPTION 'published or retired corpus content is immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    """Serialize child writes with validation and freeze all validated children."""

    op.execute(_VALIDATED_GUARD_SQL)
    op.execute(
        """
        CREATE TRIGGER trg_corpus_validation_receipt_release_guard
        BEFORE INSERT OR UPDATE OR DELETE ON corpus_validation_receipt
        FOR EACH ROW EXECUTE FUNCTION eve_guard_published_corpus_child()
        """
    )


def downgrade() -> None:
    """Restore the published-only guard from revision 0008."""

    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_corpus_validation_receipt_release_guard
        ON corpus_validation_receipt
        """
    )
    op.execute(_PREVIOUS_GUARD_SQL)
