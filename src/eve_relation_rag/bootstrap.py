"""Production dependency composition shared by HTTP and CLI adapters."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from sqlalchemy import Engine, create_engine

from eve_relation_rag.application.literature import LiteratureRetrievalService
from eve_relation_rag.application.rag import RagQueryApplication
from eve_relation_rag.application.structured import StructuredQueryApplication
from eve_relation_rag.config import get_settings
from eve_relation_rag.generation.composer import GenerationComposer
from eve_relation_rag.generation.local_provider import (
    LocalOpenAICompatibleProvider,
    LocalProviderConfig,
    LocalProviderConfigurationError,
)
from eve_relation_rag.generation.policy import (
    load_local_model_policy_manifest,
    load_prompt_policy_manifest,
)
from eve_relation_rag.hybrid.bindings import (
    ConfiguredHybridBindingRegistry,
    HybridBindingRegistry,
    UnavailableHybridBindingRegistry,
)
from eve_relation_rag.literature.candidate_gate import ValidatedCandidateGate
from eve_relation_rag.literature.gate import PublishedCorpusGate
from eve_relation_rag.literature.local_bge import LocalBgeConfigurationError, LocalBgeProvider
from eve_relation_rag.literature.validation import RebuildValidationReport
from eve_relation_rag.operations.readiness import ReadinessService
from eve_relation_rag.planning.router import DeterministicRouter
from eve_relation_rag.planning.sqlalchemy_resolver import SqlAlchemyReleaseResolverFactory
from eve_relation_rag.releases.receipt_integrity import DatasetCandidateValidationInput
from eve_relation_rag.retrieval.hybrid.anchors import StructuredAnchorResolver
from eve_relation_rag.retrieval.structured.candidate_gate import (
    ValidatedCandidateReleaseGate,
)
from eve_relation_rag.retrieval.structured.gate import PublishedReleaseGate
from eve_relation_rag.retrieval.structured.repository import StructuredRepository
from eve_relation_rag.retrieval.structured.service import StructuredRetrievalService


@lru_cache
def get_engine() -> Engine:
    """Return the process-local SQLAlchemy engine without opening a connection."""

    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_structured_query_application() -> StructuredQueryApplication:
    """Compose the one production question-first structured query application."""

    settings = get_settings()
    engine = get_engine()
    gate = PublishedReleaseGate(engine)
    secret_setting = settings.cursor_hmac_secret
    retrieval: StructuredRetrievalService | None = None
    if secret_setting is not None:
        secret = secret_setting.get_secret_value().encode("utf-8")
        if len(secret) >= 32:
            retrieval = StructuredRetrievalService(
                gate=gate,
                repository=StructuredRepository(engine),
                cursor_secret=secret,
            )
    return StructuredQueryApplication(
        gate=gate,
        resolver_factory=SqlAlchemyReleaseResolverFactory(engine),
        retrieval=retrieval,
    )


@lru_cache
def get_local_bge_provider() -> LocalBgeProvider:
    """Load the one verified offline BGE provider configured for this process."""

    settings = get_settings()
    if (
        settings.embedding_model_path is None
        or settings.embedding_artifact_manifest_path is None
        or settings.embedding_artifact_manifest_sha256 is None
    ):
        raise LocalBgeConfigurationError(
            "local BGE path, artifact manifest, and approved checksum are required"
        )
    return LocalBgeProvider(
        settings.embedding_model_path,
        artifact_manifest_path=settings.embedding_artifact_manifest_path,
        approved_artifact_manifest_sha256=settings.embedding_artifact_manifest_sha256,
    )


@lru_cache
def get_literature_retrieval_service() -> LiteratureRetrievalService:
    """Compose local-only literature retrieval, refusing incomplete model provenance."""

    return LiteratureRetrievalService(get_engine(), get_local_bge_provider())


@lru_cache
def get_hybrid_binding_registry() -> HybridBindingRegistry:
    """Load the optional checksum-approved binding manifest or remain unavailable."""

    settings = get_settings()
    path = settings.hybrid_binding_manifest_path
    approved_sha256 = settings.hybrid_binding_manifest_sha256
    if path is None or approved_sha256 is None:
        return UnavailableHybridBindingRegistry()
    return ConfiguredHybridBindingRegistry(
        path,
        approved_manifest_sha256=approved_sha256,
    )


@lru_cache
def get_local_llm_provider() -> LocalOpenAICompatibleProvider:
    """Load the one checksum-approved no-egress loopback generation provider."""

    settings = get_settings()
    if settings.llm_provider != "local_openai_compatible":
        raise LocalProviderConfigurationError(
            "The approved local generation provider is not configured."
        )
    required = (
        settings.llm_base_url,
        settings.llm_model_artifact_root,
        settings.llm_model_policy_manifest_path,
        settings.llm_model_policy_manifest_sha256,
        settings.llm_prompt_policy_manifest_path,
        settings.llm_prompt_policy_manifest_sha256,
    )
    if any(value is None for value in required):
        raise LocalProviderConfigurationError(
            "The approved local generation provider is not configured."
        )
    model_path = settings.llm_model_policy_manifest_path
    model_sha256 = settings.llm_model_policy_manifest_sha256
    prompt_path = settings.llm_prompt_policy_manifest_path
    prompt_sha256 = settings.llm_prompt_policy_manifest_sha256
    base_url = settings.llm_base_url
    artifact_root = settings.llm_model_artifact_root
    api_key = _load_local_provider_api_key(
        inline=settings.llm_api_key,
        path=settings.llm_api_key_file,
    )
    assert model_path is not None
    assert model_sha256 is not None
    assert prompt_path is not None
    assert prompt_sha256 is not None
    assert base_url is not None
    assert artifact_root is not None
    model_policy = load_local_model_policy_manifest(
        model_path,
        approved_manifest_sha256=model_sha256,
    )
    prompt_policy = load_prompt_policy_manifest(
        prompt_path,
        approved_manifest_sha256=prompt_sha256,
    )
    return LocalOpenAICompatibleProvider(
        config=LocalProviderConfig(
            base_url=base_url,
            artifact_root=artifact_root,
            api_key=api_key,
        ),
        model_policy=model_policy,
        prompt_policy=prompt_policy,
    )


def _load_local_provider_api_key(
    *,
    inline: SecretStr | None,
    path: Path | None,
) -> SecretStr:
    if inline is not None and path is not None:
        raise LocalProviderConfigurationError(
            "The local provider authentication configuration is ambiguous."
        )
    if inline is not None:
        value = inline.get_secret_value()
    elif path is not None:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
                raise OSError
            raw = path.read_bytes()
            if raw.endswith(b"\r\n"):
                raw = raw[:-2]
            elif raw.endswith(b"\n"):
                raw = raw[:-1]
            value = raw.decode("ascii")
        except (OSError, UnicodeError):
            raise LocalProviderConfigurationError(
                "The local provider authentication is unavailable."
            ) from None
    else:
        raise LocalProviderConfigurationError("The local provider authentication is unavailable.")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        encoded = b""
    if not 32 <= len(encoded) <= 256 or any(byte < 0x21 or byte > 0x7E for byte in encoded):
        raise LocalProviderConfigurationError("The local provider authentication is unavailable.")
    return SecretStr(value)


@lru_cache
def get_generation_composer() -> GenerationComposer:
    """Compose generation only from the exact approved local provider identity."""

    provider = get_local_llm_provider()
    return GenerationComposer(provider=provider, expected_identity=provider.identity)


def _get_configured_composer() -> GenerationComposer | None:
    if get_settings().llm_provider == "disabled":
        return None
    return get_generation_composer()


@lru_cache
def get_readiness_service() -> ReadinessService:
    """Compose sanitized readiness checks over exact activation dependencies."""

    settings = get_settings()
    return ReadinessService(
        service=settings.app_name,
        version=settings.app_version,
        engine_factory=get_engine,
        migration_config_path=settings.migration_config_path,
        release_key=settings.activation_release_key,
        corpus_release_key=settings.activation_corpus_release_key,
        release_gate_factory=lambda: PublishedReleaseGate(get_engine()),
        corpus_gate_factory=lambda: PublishedCorpusGate(get_engine()),
        binding_registry_factory=get_hybrid_binding_registry,
        provider_factory=get_local_llm_provider,
        environment=settings.environment,
    )


@lru_cache
def get_rag_query_application() -> RagQueryApplication:
    """Compose routed RAG with disabled-default, local-only generation."""

    return RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=get_structured_query_application,
        corpus_gate_factory=lambda: PublishedCorpusGate(get_engine()),
        literature_service_factory=get_literature_retrieval_service,
        binding_registry_factory=get_hybrid_binding_registry,
        anchor_resolver_factory=lambda: StructuredAnchorResolver(get_engine()),
        composer_factory=_get_configured_composer,
    )


def build_v0_candidate_rag_query_application(
    *,
    candidate_validation_input: DatasetCandidateValidationInput,
    corpus_rebuild_report: RebuildValidationReport,
) -> RagQueryApplication:
    """Compose the routed app with two exact validation-only capabilities.

    This is deliberately not cached and is never used by HTTP or the ordinary
    ``rag query`` command.  The bound gates reject any request key other than
    the separately approved candidate inputs.
    """

    settings = get_settings()
    secret_setting = settings.cursor_hmac_secret
    if secret_setting is None:
        raise RuntimeError("candidate benchmark requires the cursor HMAC secret")
    secret = secret_setting.get_secret_value().encode("utf-8")
    if len(secret) < 32:
        raise RuntimeError("candidate benchmark cursor HMAC secret is invalid")

    engine = get_engine()
    structured_gate = ValidatedCandidateReleaseGate(engine).bind(candidate_validation_input)
    structured_application = StructuredQueryApplication(
        gate=structured_gate,
        resolver_factory=SqlAlchemyReleaseResolverFactory(engine),
        retrieval=StructuredRetrievalService(
            gate=structured_gate,
            repository=StructuredRepository(engine),
            cursor_secret=secret,
        ),
    )
    corpus_gate = ValidatedCandidateGate(engine).bind(corpus_rebuild_report)
    return RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=lambda: structured_application,
        corpus_gate_factory=lambda: corpus_gate,
        literature_service_factory=get_literature_retrieval_service,
        binding_registry_factory=get_hybrid_binding_registry,
        anchor_resolver_factory=lambda: StructuredAnchorResolver(engine),
        composer_factory=_get_configured_composer,
    )
