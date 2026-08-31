"""Audit the configured PostgreSQL role before exposing the V0 public runtime."""

from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from eve_relation_rag.config import get_settings
from eve_relation_rag.operations.database_role import audit_database_runtime_role


def main() -> int:
    """Print a sanitized audit and fail until the role is strictly read-only."""

    engine = None
    try:
        engine = create_engine(get_settings().database_url, poolclass=NullPool)
        audit = audit_database_runtime_role(engine)
    except Exception:
        print(
            json.dumps(
                {
                    "audit_schema_version": "v0-database-runtime-role-audit-v1",
                    "status": "error",
                },
                sort_keys=True,
            )
        )
        return 2
    finally:
        if engine is not None:
            engine.dispose()
    print(audit.model_dump_json())
    return 0 if audit.runtime_readonly else 1


if __name__ == "__main__":
    raise SystemExit(main())
