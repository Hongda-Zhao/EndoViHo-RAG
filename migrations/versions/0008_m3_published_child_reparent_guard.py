"""Close published-corpus child reparenting bypass

Revision ID: 0008_m3_child_reparent_guard
Revises: 0007_m3_anchor_release_scope
Create Date: 2026-08-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_m3_child_reparent_guard"
down_revision: str | Sequence[str] | None = "0007_m3_anchor_release_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STRICT_GUARD_SQL = """
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


_PREVIOUS_GUARD_SQL = """
CREATE OR REPLACE FUNCTION eve_guard_published_corpus_child()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    target_release_id bigint;
    target_status text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        target_release_id := OLD.release_id;
    ELSE
        target_release_id := NEW.release_id;
    END IF;

    SELECT status INTO target_status
      FROM corpus_release
     WHERE id = target_release_id;

    IF target_status IN ('published', 'retired') THEN
        RAISE EXCEPTION 'published or retired corpus content is immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$
"""


def upgrade() -> None:
    """Reject UPDATE when either the old or new release is immutable."""

    op.execute(_STRICT_GUARD_SQL)


def downgrade() -> None:
    """Restore the previous one-sided release check."""

    op.execute(_PREVIOUS_GUARD_SQL)
