"""Database trust-boundary audit tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import Engine

from eve_relation_rag.operations.database_role import (
    _ROLE_AUDIT_SQL,
    _build_database_role_audit,
    audit_database_runtime_role,
)


def _values(**updates: bool) -> dict[str, bool]:
    values = {
        "role_is_superuser": False,
        "role_can_create_roles": False,
        "role_can_create_databases": False,
        "role_can_replicate": False,
        "role_can_bypass_rls": False,
        "role_has_memberships": False,
        "role_default_transaction_read_only": True,
        "owns_current_database": False,
        "owns_application_schema": False,
        "owns_application_tables": False,
        "owns_application_sequences": False,
        "can_create_in_database": False,
        "can_create_in_schema": False,
        "can_select_all_application_tables": True,
        "can_write_application_tables": False,
        "can_write_application_columns": False,
        "has_application_sequence_privileges": False,
        "can_insert_dataset_receipt": False,
        "can_update_release_status": False,
    }
    values.update(updates)
    return values


def test_exact_select_only_role_passes() -> None:
    audit = _build_database_role_audit(_values())

    assert audit.runtime_readonly is True


def test_runtime_audit_executes_one_live_readonly_query() -> None:
    engine = MagicMock(spec=Engine)
    connection = MagicMock()
    readonly_context = MagicMock()
    runtime_connection = MagicMock()
    engine.connect.return_value = connection
    connection.execution_options.return_value = readonly_context
    readonly_context.__enter__.return_value = runtime_connection
    runtime_connection.execute.return_value.mappings.return_value.one.return_value = _values()

    audit = audit_database_runtime_role(engine)

    assert audit.runtime_readonly is True
    engine.connect.assert_called_once_with()
    connection.execution_options.assert_called_once_with(postgresql_readonly=True)
    runtime_connection.execute.assert_called_once_with(_ROLE_AUDIT_SQL)


@pytest.mark.parametrize(
    "unsafe",
    (
        "role_is_superuser",
        "role_can_create_roles",
        "role_can_create_databases",
        "role_can_replicate",
        "role_can_bypass_rls",
        "role_has_memberships",
        "owns_current_database",
        "owns_application_schema",
        "owns_application_tables",
        "owns_application_sequences",
        "can_create_in_database",
        "can_create_in_schema",
        "can_write_application_tables",
        "can_write_application_columns",
        "has_application_sequence_privileges",
        "can_insert_dataset_receipt",
        "can_update_release_status",
    ),
)
def test_any_control_plane_privilege_fails(unsafe: str) -> None:
    audit = _build_database_role_audit(_values(**{unsafe: True}))

    assert audit.runtime_readonly is False


def test_missing_read_access_or_non_boolean_result_fails() -> None:
    assert (
        _build_database_role_audit(
            _values(can_select_all_application_tables=False)
        ).runtime_readonly
        is False
    )
    assert (
        _build_database_role_audit(
            _values(role_default_transaction_read_only=False)
        ).runtime_readonly
        is False
    )
    invalid: dict[str, object] = _values()
    invalid["can_insert_dataset_receipt"] = 0
    with pytest.raises(ValueError, match="non-boolean"):
        _build_database_role_audit(invalid)


def test_unexpected_result_shape_fails_closed() -> None:
    missing = _values()
    missing.pop("has_application_sequence_privileges")
    with pytest.raises(ValueError, match="unexpected result shape"):
        _build_database_role_audit(missing)

    extra: dict[str, object] = _values()
    extra["unreviewed_privilege"] = False
    with pytest.raises(ValueError, match="unexpected result shape"):
        _build_database_role_audit(extra)


def test_sql_covers_fixed_schema_column_sequence_ownership_and_role_default() -> None:
    statement = str(_ROLE_AUDIT_SQL)

    assert "namespace.nspname = 'public'" in statement
    assert "current_schema" not in statement
    assert "pg_db_role_setting" in statement
    assert "default_transaction_read_only" in statement
    assert "database.datdba = role.oid" in statement
    assert "namespace.nspowner = role.oid" in statement
    for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
        assert f"has_table_privilege(current_user, t.oid, '{privilege}')" in statement
    for privilege in ("INSERT", "UPDATE", "REFERENCES"):
        assert f"has_any_column_privilege(current_user, t.oid, '{privilege}')" in statement
    for privilege in ("USAGE", "SELECT", "UPDATE"):
        assert f"has_sequence_privilege(current_user, s.oid, '{privilege}')" in statement
