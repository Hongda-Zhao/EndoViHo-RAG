from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_v0_database_role.py"
_SPEC = importlib.util.spec_from_file_location("check_v0_database_role", _SCRIPT)
assert _SPEC is not None
assert _SPEC.loader is not None
role_check = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(role_check)


class _Engine:
    disposed = False

    def dispose(self) -> None:
        self.disposed = True


def test_configuration_failure_is_sanitized(monkeypatch, capsys) -> None:
    def fail_settings() -> None:
        raise RuntimeError("postgresql://user:secret@example.invalid/database")

    monkeypatch.setattr(role_check, "get_settings", fail_settings)

    assert role_check.main() == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "secret" not in captured.out
    assert json.loads(captured.out) == {
        "audit_schema_version": "v0-database-runtime-role-audit-v1",
        "status": "error",
    }


def test_engine_is_disposed_after_audit(monkeypatch, capsys) -> None:
    engine = _Engine()
    monkeypatch.setattr(
        role_check,
        "get_settings",
        lambda: SimpleNamespace(database_url="postgresql+psycopg://ignored"),
    )
    monkeypatch.setattr(role_check, "create_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(
        role_check,
        "audit_database_runtime_role",
        lambda observed: SimpleNamespace(
            runtime_readonly=True,
            model_dump_json=lambda: '{"runtime_readonly":true}',
        ),
    )

    assert role_check.main() == 0
    assert engine.disposed is True
    assert capsys.readouterr().out == '{"runtime_readonly":true}\n'
