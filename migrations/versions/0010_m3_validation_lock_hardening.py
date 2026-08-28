"""Serialize candidate child writes with every validation-state transition

Revision ID: 0010_m3_lock_hardening
Revises: 0009_m3_validated_freeze
Create Date: 2026-08-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_m3_lock_hardening"
down_revision: str | Sequence[str] | None = "0009_m3_validated_freeze"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SHARE_GUARD_SQL = """
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
           FOR SHARE;
        IF new_status IN ('validated', 'published', 'retired') THEN
            RAISE EXCEPTION 'validated, published, or retired corpus content is immutable'
                USING ERRCODE = '55000';
        END IF;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        SELECT status INTO old_status
          FROM corpus_release
         WHERE id = OLD.release_id
           FOR SHARE;
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
       FOR SHARE;
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


_KEY_SHARE_GUARD_SQL = _SHARE_GUARD_SQL.replace("FOR SHARE", "FOR KEY SHARE")


def upgrade() -> None:
    """Make candidate child writes conflict with parent status updates."""

    op.execute(_SHARE_GUARD_SQL)


def downgrade() -> None:
    """Restore revision 0009's weaker key-share lock mode."""

    op.execute(_KEY_SHARE_GUARD_SQL)
