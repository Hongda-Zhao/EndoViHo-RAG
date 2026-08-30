"""Fail-closed audit of the PostgreSQL role used by the public runtime."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, text


class DatabaseRoleAudit(BaseModel):
    """Sanitized privilege result; database-role names are intentionally omitted."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    audit_schema_version: Literal["v0-database-runtime-role-audit-v1"]
    runtime_readonly: bool
    role_is_superuser: bool
    role_can_create_roles: bool
    role_can_create_databases: bool
    role_can_replicate: bool
    role_can_bypass_rls: bool
    role_has_memberships: bool
    role_default_transaction_read_only: bool
    owns_current_database: bool
    owns_application_schema: bool
    owns_application_tables: bool
    owns_application_sequences: bool
    can_create_in_database: bool
    can_create_in_schema: bool
    can_select_all_application_tables: bool
    can_write_application_tables: bool
    can_write_application_columns: bool
    has_application_sequence_privileges: bool
    can_insert_dataset_receipt: bool
    can_update_release_status: bool


_ROLE_AUDIT_SQL = text(
    """
    WITH current_role_state AS (
        SELECT oid, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls
          FROM pg_roles
         WHERE rolname = current_user
    ), current_database_state AS (
        SELECT oid, datdba
          FROM pg_database
         WHERE datname = current_database()
    ), application_schema AS (
        SELECT namespace.oid, namespace.nspowner
          FROM pg_namespace AS namespace
         WHERE namespace.nspname = 'public'
    ), application_tables AS (
        SELECT relation.oid, relation.relowner
          FROM pg_class AS relation
          JOIN application_schema AS namespace ON namespace.oid = relation.relnamespace
         WHERE relation.relkind IN ('r', 'p', 'v', 'm', 'f')
    ), application_sequences AS (
        SELECT relation.oid, relation.relowner
          FROM pg_class AS relation
          JOIN application_schema AS namespace ON namespace.oid = relation.relnamespace
         WHERE relation.relkind = 'S'
    )
    SELECT
        role.rolsuper AS role_is_superuser,
        role.rolcreaterole AS role_can_create_roles,
        role.rolcreatedb AS role_can_create_databases,
        role.rolreplication AS role_can_replicate,
        role.rolbypassrls AS role_can_bypass_rls,
        EXISTS (
            SELECT 1 FROM pg_auth_members AS membership
             WHERE membership.member = role.oid
        ) AS role_has_memberships,
        COALESCE(
            (
                SELECT split_part(setting_entry.setting_value, '=', 2) = 'on'
                  FROM pg_db_role_setting AS setting
                  CROSS JOIN LATERAL unnest(setting.setconfig)
                    AS setting_entry(setting_value)
                 WHERE setting.setrole = role.oid
                   AND setting.setdatabase IN (0, database.oid)
                   AND split_part(setting_entry.setting_value, '=', 1)
                       = 'default_transaction_read_only'
                 ORDER BY (setting.setdatabase = database.oid) DESC
                 LIMIT 1
            ),
            false
        ) AS role_default_transaction_read_only,
        database.datdba = role.oid AS owns_current_database,
        namespace.nspowner = role.oid AS owns_application_schema,
        COALESCE(
            (SELECT bool_or(t.relowner = role.oid) FROM application_tables AS t),
            false
        ) AS owns_application_tables,
        COALESCE(
            (SELECT bool_or(s.relowner = role.oid) FROM application_sequences AS s),
            false
        ) AS owns_application_sequences,
        has_database_privilege(current_user, current_database(), 'CREATE')
            AS can_create_in_database,
        has_schema_privilege(current_user, namespace.oid, 'CREATE')
            AS can_create_in_schema,
        COALESCE(
            (
                SELECT bool_and(has_table_privilege(current_user, t.oid, 'SELECT'))
                  FROM application_tables AS t
            ),
            false
        ) AS can_select_all_application_tables,
        COALESCE(
            (
                SELECT bool_or(
                    has_table_privilege(current_user, t.oid, 'INSERT')
                    OR has_table_privilege(current_user, t.oid, 'UPDATE')
                    OR has_table_privilege(current_user, t.oid, 'DELETE')
                    OR has_table_privilege(current_user, t.oid, 'TRUNCATE')
                    OR has_table_privilege(current_user, t.oid, 'REFERENCES')
                    OR has_table_privilege(current_user, t.oid, 'TRIGGER')
                )
                  FROM application_tables AS t
            ),
            false
        ) AS can_write_application_tables,
        COALESCE(
            (
                SELECT bool_or(
                    has_any_column_privilege(current_user, t.oid, 'INSERT')
                    OR has_any_column_privilege(current_user, t.oid, 'UPDATE')
                    OR has_any_column_privilege(current_user, t.oid, 'REFERENCES')
                )
                  FROM application_tables AS t
            ),
            false
        ) AS can_write_application_columns,
        COALESCE(
            (
                SELECT bool_or(
                    has_sequence_privilege(current_user, s.oid, 'USAGE')
                    OR has_sequence_privilege(current_user, s.oid, 'SELECT')
                    OR has_sequence_privilege(current_user, s.oid, 'UPDATE')
                )
                  FROM application_sequences AS s
            ),
            false
        ) AS has_application_sequence_privileges,
        has_table_privilege(
            current_user,
            'public.dataset_validation_receipt',
            'INSERT'
        ) AS can_insert_dataset_receipt,
        has_column_privilege(
            current_user,
            'public.dataset_release',
            'status',
            'UPDATE'
        ) AS can_update_release_status
      FROM current_role_state AS role
      CROSS JOIN current_database_state AS database
      CROSS JOIN application_schema AS namespace
    """
)


def audit_database_runtime_role(engine: Engine) -> DatabaseRoleAudit:
    """Require a non-owner role with SELECT-only access to every application table."""

    with engine.connect().execution_options(postgresql_readonly=True) as connection:
        row = connection.execute(_ROLE_AUDIT_SQL).mappings().one()
    return _build_database_role_audit(cast(Mapping[str, object], row))


def _build_database_role_audit(values: Mapping[str, object]) -> DatabaseRoleAudit:
    expected_fields = set(DatabaseRoleAudit.model_fields) - {
        "audit_schema_version",
        "runtime_readonly",
    }
    if set(values) != expected_fields:
        raise ValueError("database role audit returned an unexpected result shape")
    flags = {
        field: values[field]
        for field in DatabaseRoleAudit.model_fields
        if field not in {"audit_schema_version", "runtime_readonly"}
    }
    if any(type(value) is not bool for value in flags.values()):
        raise ValueError("database role audit returned a non-boolean privilege")
    runtime_readonly = bool(
        flags["can_select_all_application_tables"]
        and flags["role_default_transaction_read_only"]
        and not any(
            flags[field]
            for field in (
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
            )
        )
    )
    return DatabaseRoleAudit.model_validate(
        {
            "audit_schema_version": "v0-database-runtime-role-audit-v1",
            "runtime_readonly": runtime_readonly,
            **flags,
        }
    )


__all__ = ["DatabaseRoleAudit", "audit_database_runtime_role"]
