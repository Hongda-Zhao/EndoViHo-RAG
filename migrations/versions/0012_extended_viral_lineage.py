"""Add a distinct release role for evidence-backed extended viral lineages.

Revision ID: 0012_extended_viral_lineage
Revises: 0011_dataset_validation_receipt
Create Date: 2026-08-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012_extended_viral_lineage"
down_revision: str | Sequence[str] | None = "0011_dataset_validation_receipt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_EXTENDED_RELEASE_ROLE_CHECK = (
    "role IN ('assembly_source_taxonomy', 'formal_viral_taxonomy', "
    "'study_viral_lineage', 'extended_viral_lineage')"
)
_LEGACY_RELEASE_ROLE_CHECK = (
    "role IN ('assembly_source_taxonomy', 'formal_viral_taxonomy', 'study_viral_lineage')"
)

_EXTENDED_ROLE_NAMESPACE_CHECK = (
    "(role = 'assembly_source_taxonomy' AND domain = 'host' "
    "AND scheme_kind = 'formal_taxonomy') OR "
    "(role = 'formal_viral_taxonomy' AND domain = 'viral' "
    "AND scheme_kind = 'formal_taxonomy') OR "
    "(role = 'study_viral_lineage' AND domain = 'viral' "
    "AND scheme_kind = 'study_defined') OR "
    "(role = 'extended_viral_lineage' AND domain = 'viral' "
    "AND scheme_kind = 'study_defined')"
)
_LEGACY_ROLE_NAMESPACE_CHECK = (
    "(role = 'assembly_source_taxonomy' AND domain = 'host' "
    "AND scheme_kind = 'formal_taxonomy') OR "
    "(role = 'formal_viral_taxonomy' AND domain = 'viral' "
    "AND scheme_kind = 'formal_taxonomy') OR "
    "(role = 'study_viral_lineage' AND domain = 'viral' "
    "AND scheme_kind = 'study_defined')"
)

_EXTENDED_ASSERTION_DETAIL_CHECK = (
    "(assertion_type = 'hcvr' AND source_assessment_id IS NOT NULL "
    "AND source_label IS NOT NULL AND source_confidence IS NOT NULL "
    "AND lineage_snapshot_id IS NULL AND lineage_term_id IS NULL "
    "AND lineage_snapshot_role IS NULL) OR "
    "(assertion_type = 'viral_major_taxon' AND source_assessment_id IS NULL "
    "AND source_label IS NULL AND source_confidence IS NULL "
    "AND lineage_snapshot_id IS NOT NULL AND lineage_term_id IS NOT NULL "
    "AND lineage_snapshot_role IN "
    "('formal_viral_taxonomy', 'study_viral_lineage', 'extended_viral_lineage')) OR "
    "(assertion_type = 'vr_type' AND source_assessment_id IS NULL "
    "AND source_label IS NULL AND source_confidence IS NULL "
    "AND lineage_snapshot_id IS NULL AND lineage_term_id IS NULL "
    "AND lineage_snapshot_role IS NULL)"
)
_LEGACY_ASSERTION_DETAIL_CHECK = (
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
    "AND lineage_snapshot_role IS NULL)"
)

_DOWNGRADE_PREFLIGHT_SQL = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM release_lineage_snapshot
         WHERE role = 'extended_viral_lineage'
    ) OR EXISTS (
        SELECT 1
          FROM scientific_assertion
         WHERE lineage_snapshot_role = 'extended_viral_lineage'
    ) THEN
        RAISE EXCEPTION
            'cannot downgrade 0012 while extended_viral_lineage rows exist; migrate them first'
            USING ERRCODE = '55000';
    END IF;
END;
$$
"""


def _replace_checks(*, extended: bool) -> None:
    op.drop_constraint(
        op.f("ck_scientific_assertion_typed_detail_matches_assertion_type"),
        "scientific_assertion",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_release_lineage_snapshot_role_matches_namespace"),
        "release_lineage_snapshot",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_release_lineage_snapshot_valid_role"),
        "release_lineage_snapshot",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_release_lineage_snapshot_valid_role"),
        "release_lineage_snapshot",
        _EXTENDED_RELEASE_ROLE_CHECK if extended else _LEGACY_RELEASE_ROLE_CHECK,
    )
    op.create_check_constraint(
        op.f("ck_release_lineage_snapshot_role_matches_namespace"),
        "release_lineage_snapshot",
        _EXTENDED_ROLE_NAMESPACE_CHECK if extended else _LEGACY_ROLE_NAMESPACE_CHECK,
    )
    op.create_check_constraint(
        op.f("ck_scientific_assertion_typed_detail_matches_assertion_type"),
        "scientific_assertion",
        _EXTENDED_ASSERTION_DETAIL_CHECK if extended else _LEGACY_ASSERTION_DETAIL_CHECK,
    )


def upgrade() -> None:
    """Permit the extended viral role only in a viral study-defined namespace."""

    _replace_checks(extended=True)


def downgrade() -> None:
    """Restore the two viral-role contract; existing extended rows fail closed."""

    op.execute(_DOWNGRADE_PREFLIGHT_SQL)
    _replace_checks(extended=False)
