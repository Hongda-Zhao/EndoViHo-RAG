"""Allow distinct source-occurrence loci to share an exact interval

Revision ID: 0004_m1_shared_intervals
Revises: 0003_m1_assertion_evidence
Create Date: 2026-08-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_m1_shared_intervals"
down_revision: str | Sequence[str] | None = "0003_m1_assertion_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Converge databases that already applied the earlier 0002/0003 files."""
    op.execute("DROP INDEX IF EXISTS uq_eve_locus_placement_exact_interval")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_eve_locus_placement_exact_interval
        ON eve_locus_placement (release_id, sequence_id, start0, end0)
        WHERE precision = 'exact'
        """
    )


def downgrade() -> None:
    """Keep the canonical non-unique index expected by revised revision 0003."""
