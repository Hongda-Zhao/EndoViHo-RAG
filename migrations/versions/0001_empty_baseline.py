"""Establish the empty Milestone 0 migration baseline.

Revision ID: 0001_empty_baseline
Revises: None
"""

from collections.abc import Sequence

revision: str = "0001_empty_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply an intentionally empty baseline with no domain tables."""
    pass


def downgrade() -> None:
    """Revert an intentionally empty baseline with no domain tables."""
    pass
