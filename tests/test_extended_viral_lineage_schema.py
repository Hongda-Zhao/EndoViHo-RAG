from __future__ import annotations

from sqlalchemy import CheckConstraint, Table

from eve_relation_rag.db.models import ReleaseLineageSnapshot, ScientificAssertion


def _check_sql(table: Table, constraint_name: str) -> str:
    constraints = table.constraints
    matches = [
        constraint
        for constraint in constraints
        if isinstance(constraint, CheckConstraint) and constraint.name == constraint_name
    ]
    assert len(matches) == 1
    return str(matches[0].sqltext)


def test_orm_constraints_admit_only_study_defined_extended_lineages() -> None:
    role_sql = _check_sql(
        ReleaseLineageSnapshot.__table__,
        "ck_release_lineage_snapshot_valid_role",
    )
    namespace_sql = _check_sql(
        ReleaseLineageSnapshot.__table__,
        "ck_release_lineage_snapshot_role_matches_namespace",
    )
    assertion_sql = _check_sql(
        ScientificAssertion.__table__,
        "ck_scientific_assertion_typed_detail_matches_assertion_type",
    )

    assert "'extended_viral_lineage'" in role_sql
    assert (
        "role = 'extended_viral_lineage' AND domain = 'viral' "
        "AND scheme_kind = 'study_defined'"
    ) in namespace_sql
    assert "'extended_viral_lineage'" in assertion_sql
