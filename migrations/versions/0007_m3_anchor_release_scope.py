"""Scope curated anchor identity to one corpus release

Revision ID: 0007_m3_anchor_release_scope
Revises: 0006_m3_literature_retrieval
Create Date: 2026-08-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_m3_anchor_release_scope"
down_revision: str | Sequence[str] | None = "0006_m3_literature_retrieval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow the same curated anchor key in distinct immutable corpus releases."""

    op.drop_constraint(
        op.f("uq_document_anchor_anchor_key"),
        "document_anchor",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_document_anchor_release_anchor_key",
        "document_anchor",
        ["release_id", "anchor_key"],
    )


def downgrade() -> None:
    """Restore global uniqueness, failing safely if cross-release duplicates exist."""

    op.drop_constraint(
        "uq_document_anchor_release_anchor_key",
        "document_anchor",
        type_="unique",
    )
    op.create_unique_constraint(
        op.f("uq_document_anchor_anchor_key"),
        "document_anchor",
        ["anchor_key"],
    )
