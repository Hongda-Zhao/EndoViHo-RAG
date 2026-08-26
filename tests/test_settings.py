import pytest

from eve_relation_rag.config import Settings


def test_settings_use_milestone_zero_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_version == "V0"
    assert settings.environment == "development"
    assert settings.database_url.endswith("/eve_relation_rag")


def test_settings_accept_prefixed_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVE_RAG_ENVIRONMENT", "test")
    monkeypatch.setenv(
        "EVE_RAG_DATABASE_URL",
        "postgresql+psycopg://test:test@database:5432/test_database",
    )

    settings = Settings(_env_file=None)
    assert settings.environment == "test"
    assert settings.database_url.endswith("/test_database")
