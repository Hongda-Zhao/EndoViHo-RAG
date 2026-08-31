"""Production cross-validation for the local-only V0 activation boundary."""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from eve_relation_rag.config import Settings


def _production_values(tmp_path: Path) -> dict[str, object]:
    embedding_root = tmp_path / "embedding-model"
    model_root = tmp_path / "generation-model"
    embedding_root.mkdir()
    model_root.mkdir()
    files = {
        "embedding_artifact_manifest_path": tmp_path / "embedding-manifest.json",
        "hybrid_binding_manifest_path": tmp_path / "binding-manifest.json",
        "llm_model_policy_manifest_path": tmp_path / "model-policy.json",
        "llm_prompt_policy_manifest_path": tmp_path / "prompt-policy.json",
        "migration_config_path": tmp_path / "alembic.ini",
    }
    for path in files.values():
        path.write_text("{}", encoding="utf-8")
    api_key_file = tmp_path / "provider-api-key"
    api_key_file.write_text(
        "loopback-secret-must-not-leak-and-has-enough-bytes\n",
        encoding="ascii",
    )
    api_key_file.chmod(0o600)
    return {
        "environment": "production",
        "database_url": (
            "postgresql+psycopg://eve_prod:tests-only-production-password@db:5432/eve_prod"
        ),
        "cursor_hmac_secret": "cursor-secret-is-at-least-thirty-two-bytes",
        "embedding_model_path": embedding_root,
        "embedding_artifact_manifest_sha256": "a" * 64,
        "llm_provider": "local_openai_compatible",
        "llm_base_url": "http://127.0.0.1:8123/",
        "llm_api_key_file": api_key_file,
        "llm_model_artifact_root": model_root,
        "llm_model_policy_manifest_sha256": "b" * 64,
        "llm_prompt_policy_manifest_sha256": "c" * 64,
        "hybrid_binding_manifest_sha256": "d" * 64,
        "activation_release_key": "release:endoviho-rag:v0:20260829:001",
        "activation_corpus_release_key": "corpus:endoviho-rag:v0:20260829:001",
        **files,
    }


def test_production_defaults_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.casefold().startswith("eve_rag_"):
            monkeypatch.delenv(name)

    with pytest.raises(ValidationError, match="production rejects default"):
        Settings(_env_file=None, environment="production")


def test_local_provider_requires_all_checksum_bound_configuration() -> None:
    with pytest.raises(ValidationError, match="local provider requires"):
        Settings(
            _env_file=None,
            environment="test",
            llm_provider="local_openai_compatible",
            llm_base_url="http://127.0.0.1:8123",
        )


@pytest.mark.parametrize(
    "endpoint",
    (
        "http://localhost:8123",
        "https://127.0.0.1:8123",
        "http://192.0.2.1:8123",
        "http://127.0.0.1:8123/v1",
    ),
)
def test_settings_reject_non_numeric_or_non_loopback_provider_endpoint(endpoint: str) -> None:
    with pytest.raises(ValidationError, match="numeric loopback"):
        Settings(_env_file=None, environment="test", llm_base_url=endpoint)


def test_complete_production_configuration_is_normalized_and_secret_safe(
    tmp_path: Path,
) -> None:
    values = _production_values(tmp_path)

    settings = Settings(_env_file=None, **values)  # type: ignore[arg-type]

    assert settings.llm_base_url == "http://127.0.0.1:8123"
    assert settings.llm_provider == "local_openai_compatible"
    assert settings.llm_api_key is None
    assert settings.llm_api_key_file == values["llm_api_key_file"]
    assert settings.activation_release_key == "release:endoviho-rag:v0:20260829:001"
    assert "loopback-secret-must-not-leak" not in repr(settings)
    assert "cursor-secret-is-at-least-thirty-two-bytes" not in repr(settings)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    (
        ("database_url", "postgresql+psycopg://eve:eve_dev_password@db:5432/eve", "database"),
        ("cursor_hmac_secret", "too-short", "cursor HMAC"),
        ("activation_release_key", "release:endoviho-rag:v0:bad", "activation release"),
        (
            "activation_corpus_release_key",
            "corpus:endoviho-rag:v0:bad",
            "activation corpus",
        ),
    ),
)
def test_production_rejects_unsafe_identity_or_secret_values(
    tmp_path: Path,
    field: str,
    replacement: object,
    message: str,
) -> None:
    values = _production_values(tmp_path)
    values[field] = replacement
    with pytest.raises(ValidationError, match=message):
        Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_production_rejects_symlinked_dependency(tmp_path: Path) -> None:
    values = _production_values(tmp_path)
    real = tmp_path / "real-prompt-policy.json"
    real.write_text("{}", encoding="utf-8")
    linked = tmp_path / "linked-prompt-policy.json"
    linked.symlink_to(real)
    values["llm_prompt_policy_manifest_path"] = linked

    with pytest.raises(ValidationError, match="dependencies are incomplete"):
        Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_production_rejects_inline_provider_credential(tmp_path: Path) -> None:
    values = _production_values(tmp_path)
    values.pop("llm_api_key_file")
    values["llm_api_key"] = "inline-secret-must-not-be-used-in-production"

    with pytest.raises(ValidationError, match="private provider API key file"):
        Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_production_rejects_broad_provider_credential_permissions(tmp_path: Path) -> None:
    values = _production_values(tmp_path)
    key_file = values["llm_api_key_file"]
    assert isinstance(key_file, Path)
    key_file.chmod(0o644)

    with pytest.raises(ValidationError, match="private provider API key file"):
        Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_settings_rejects_ambiguous_provider_credentials(tmp_path: Path) -> None:
    values = _production_values(tmp_path)
    values["environment"] = "test"
    values["llm_api_key"] = "inline-secret-must-not-be-combined-with-file"

    with pytest.raises(ValidationError, match="ambiguous"):
        Settings(_env_file=None, **values)  # type: ignore[arg-type]
