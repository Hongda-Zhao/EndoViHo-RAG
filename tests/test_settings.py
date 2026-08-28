import pytest
from pydantic import ValidationError

from eve_relation_rag.config import Settings


def test_settings_use_milestone_zero_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable_name in (
        "EVE_RAG_APP_NAME",
        "EVE_RAG_APP_VERSION",
        "EVE_RAG_ENVIRONMENT",
        "EVE_RAG_DATABASE_URL",
        "EVE_RAG_CURSOR_HMAC_SECRET",
        "EVE_RAG_LLM_PROVIDER",
        "EVE_RAG_HYBRID_BINDING_MANIFEST_PATH",
        "EVE_RAG_HYBRID_BINDING_MANIFEST_SHA256",
    ):
        monkeypatch.delenv(variable_name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.app_version == "V0"
    assert settings.environment == "development"
    assert settings.database_url.endswith("/eve_relation_rag")
    assert settings.cursor_hmac_secret is None
    assert settings.llm_provider == "disabled"
    assert settings.hybrid_binding_manifest_path is None
    assert settings.hybrid_binding_manifest_sha256 is None


def test_settings_accept_prefixed_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVE_RAG_ENVIRONMENT", "test")
    monkeypatch.setenv(
        "EVE_RAG_DATABASE_URL",
        "postgresql+psycopg://test:test@database:5432/test_database",
    )
    monkeypatch.setenv("EVE_RAG_CURSOR_HMAC_SECRET", "x" * 32)
    monkeypatch.setenv("EVE_RAG_LLM_PROVIDER", "disabled")
    monkeypatch.setenv("EVE_RAG_HYBRID_BINDING_MANIFEST_PATH", "/tmp/m4-bindings.json")
    monkeypatch.setenv("EVE_RAG_HYBRID_BINDING_MANIFEST_SHA256", "a" * 64)

    settings = Settings(_env_file=None)
    assert settings.environment == "test"
    assert settings.database_url.endswith("/test_database")
    assert settings.cursor_hmac_secret is not None
    assert settings.cursor_hmac_secret.get_secret_value() == "x" * 32
    assert settings.llm_provider == "disabled"
    assert str(settings.hybrid_binding_manifest_path) == "/tmp/m4-bindings.json"
    assert settings.hybrid_binding_manifest_sha256 == "a" * 64


def test_production_settings_cannot_select_a_tests_only_llm_provider() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_provider="fake")  # type: ignore[arg-type]
