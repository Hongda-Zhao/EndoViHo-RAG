from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import SecretStr, TypeAdapter, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from eve_relation_rag.config.loopback import normalize_loopback_http_origin
from eve_relation_rag.domain.keys import is_release_key
from eve_relation_rag.literature.contracts import CorpusReleaseKey

_DEFAULT_DATABASE_URL = "postgresql+psycopg://eve:eve_dev_password@localhost:5432/eve_relation_rag"
_SHA256_ADAPTER_PATTERN = frozenset("0123456789abcdef")
_CORPUS_RELEASE_KEY_ADAPTER: TypeAdapter[CorpusReleaseKey] = TypeAdapter(CorpusReleaseKey)


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="EVE_RAG_",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "EVE Relation RAG"
    app_version: str = "V0"
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = _DEFAULT_DATABASE_URL
    cursor_hmac_secret: SecretStr | None = None
    embedding_provider: Literal["local_bge"] = "local_bge"
    embedding_model_path: Path | None = None
    embedding_artifact_manifest_path: Path | None = None
    embedding_artifact_manifest_sha256: str | None = None
    corpus_import_root: Path | None = None
    llm_provider: Literal["disabled", "local_openai_compatible"] = "disabled"
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_api_key_file: Path | None = None
    llm_model_artifact_root: Path | None = None
    llm_model_policy_manifest_path: Path | None = None
    llm_model_policy_manifest_sha256: str | None = None
    llm_prompt_policy_manifest_path: Path | None = None
    llm_prompt_policy_manifest_sha256: str | None = None
    hybrid_binding_manifest_path: Path | None = None
    hybrid_binding_manifest_sha256: str | None = None
    activation_release_key: str | None = None
    activation_corpus_release_key: str | None = None
    migration_config_path: Path = Path("alembic.ini")

    @field_validator(
        "embedding_artifact_manifest_sha256",
        "hybrid_binding_manifest_sha256",
        "llm_model_policy_manifest_sha256",
        "llm_prompt_policy_manifest_sha256",
    )
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        if value is not None and (
            len(value) != 64 or any(character not in _SHA256_ADAPTER_PATTERN for character in value)
        ):
            raise ValueError("approved manifest checksums must be lowercase SHA-256 values")
        return value

    @model_validator(mode="after")
    def validate_runtime_boundary(self) -> Self:
        if self.llm_base_url is not None:
            self.llm_base_url = normalize_loopback_http_origin(self.llm_base_url)

        if self.llm_provider == "local_openai_compatible":
            required = (
                self.llm_base_url,
                self.llm_model_artifact_root,
                self.llm_model_policy_manifest_path,
                self.llm_model_policy_manifest_sha256,
                self.llm_prompt_policy_manifest_path,
                self.llm_prompt_policy_manifest_sha256,
            )
            if any(value is None for value in required):
                raise ValueError(
                    "local provider requires endpoint, artifact root, and approved policy manifests"
                )
            if self.llm_api_key is not None and self.llm_api_key_file is not None:
                raise ValueError("local provider authentication configuration is ambiguous")
            if self.llm_api_key is None and self.llm_api_key_file is None:
                raise ValueError("local provider requires authenticated loopback transport")

        if self.environment != "production":
            return self

        try:
            database = make_url(self.database_url)
        except Exception:
            raise ValueError("production database configuration is invalid") from None
        if (
            self.database_url == _DEFAULT_DATABASE_URL
            or database.drivername != "postgresql+psycopg"
            or database.password in {None, "eve_dev_password"}
        ):
            raise ValueError("production rejects default or incomplete database credentials")
        if (
            self.cursor_hmac_secret is None
            or len(self.cursor_hmac_secret.get_secret_value().encode("utf-8")) < 32
        ):
            raise ValueError("production requires a cursor HMAC secret of at least 32 bytes")
        if self.llm_provider != "local_openai_compatible":
            raise ValueError("production requires the approved local generation provider")
        if (
            self.llm_api_key is not None
            or self.llm_api_key_file is None
            or self.llm_api_key_file.is_symlink()
            or not self.llm_api_key_file.is_file()
            or self.llm_api_key_file.stat().st_mode & 0o077
        ):
            raise ValueError("production requires a private provider API key file")
        if (
            self.activation_release_key is None
            or not self.activation_release_key.startswith("release:endoviho-rag:v0:")
            or not is_release_key(self.activation_release_key)
        ):
            raise ValueError("production requires one exact activation release key")
        try:
            if self.activation_corpus_release_key is None:
                raise ValueError
            _CORPUS_RELEASE_KEY_ADAPTER.validate_python(
                self.activation_corpus_release_key, strict=True
            )
        except Exception:
            raise ValueError(
                "production requires one exact activation corpus release key"
            ) from None

        required_files = (
            self.embedding_artifact_manifest_path,
            self.hybrid_binding_manifest_path,
            self.llm_model_policy_manifest_path,
            self.llm_prompt_policy_manifest_path,
            self.migration_config_path,
        )
        required_hashes = (
            self.embedding_artifact_manifest_sha256,
            self.hybrid_binding_manifest_sha256,
            self.llm_model_policy_manifest_sha256,
            self.llm_prompt_policy_manifest_sha256,
        )
        if (
            self.embedding_model_path is None
            or self.llm_model_artifact_root is None
            or any(
                path is None or path.is_symlink() or not path.is_file() for path in required_files
            )
            or any(value is None for value in required_hashes)
            or self.embedding_model_path.is_symlink()
            or not self.embedding_model_path.is_dir()
            or self.llm_model_artifact_root.is_symlink()
            or not self.llm_model_artifact_root.is_dir()
        ):
            raise ValueError("production readiness dependencies are incomplete")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return one immutable-by-convention settings object per process."""
    return Settings()
