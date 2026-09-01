"""Concrete provenance gates separating trusted runs from fake/test output."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from eve_relation_rag.experiments.embedding_ablation.artifacts import (
    VerifiedModelArtifact,
    is_verified_artifact,
)
from eve_relation_rag.experiments.embedding_ablation.baseline import (
    baseline_bge_representation_contract,
)
from eve_relation_rag.experiments.embedding_ablation.contracts import (
    AnnotationManifest,
    RecordedModelIdentity,
    TrustStatus,
)
from eve_relation_rag.experiments.embedding_ablation.providers import (
    DeterministicFakeRerankerProvider,
)
from eve_relation_rag.literature.contracts import (
    EMBEDDING_MODEL_KEY,
    EMBEDDING_REPOSITORY_ID,
    EMBEDDING_REVISION,
)
from eve_relation_rag.literature.local_bge import LocalBgeProvider
from eve_relation_rag.literature.providers import DeterministicFakeEmbeddingProvider

_TRUST_ISSUER = object()


@dataclass(frozen=True, slots=True)
class ProviderEvidence:
    """Provider provenance derived from concrete runtime objects, never caller-authored JSON."""

    component: Literal["embedding", "reranker"]
    provider_kind: Literal["verified_local", "deterministic_fake", "unverified"]
    model_key: str
    artifact_manifest_sha256: str
    model_identity: RecordedModelIdentity | None
    reason: str
    _issuer: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _TRUST_ISSUER:
            raise TypeError("ProviderEvidence may only be issued by the trust gate")


@dataclass(frozen=True, slots=True)
class RunTrustDecision:
    """Non-forgeable trust outcome consumed by deterministic reporting."""

    status: TrustStatus
    reasons: tuple[str, ...]
    provider_records: tuple[ProviderEvidence, ...]
    corpus_release_key: str
    corpus_manifest_sha256: str
    annotation_manifest_sha256: str
    gold_sha256: str
    approved_question_count: int
    _issuer: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _TRUST_ISSUER:
            raise TypeError("RunTrustDecision may only be issued by the trust gate")


def collect_provider_evidence(
    provider: object,
    *,
    component: Literal["embedding", "reranker"],
    verified_artifact: VerifiedModelArtifact | None = None,
) -> ProviderEvidence:
    """Classify concrete providers, making fake types test-only even with plausible keys."""

    model_key = getattr(provider, "model_key", "")
    artifact_sha256 = getattr(provider, "artifact_manifest_sha256", "")
    if isinstance(provider, DeterministicFakeEmbeddingProvider) or isinstance(
        provider, DeterministicFakeRerankerProvider
    ):
        return _provider_evidence(
            component,
            "deterministic_fake",
            str(model_key),
            str(artifact_sha256),
            None,
            "deterministic fake providers are reserved for tests",
        )
    if component == "embedding" and type(provider) is LocalBgeProvider:
        identity = _baseline_model_identity(str(artifact_sha256))
        if identity is None:
            return _provider_evidence(
                component,
                "unverified",
                str(model_key),
                str(artifact_sha256),
                None,
                "LocalBgeProvider exposed an invalid artifact identity",
            )
        return _provider_evidence(
            component,
            "verified_local",
            str(model_key),
            str(artifact_sha256),
            identity,
            "LocalBgeProvider verified its complete local artifact at construction",
        )
    if verified_artifact is not None and is_verified_artifact(verified_artifact):
        manifest = verified_artifact.manifest
        if (
            manifest.representation.task_kind == component
            and model_key == manifest.model_key
            and artifact_sha256 == verified_artifact.artifact_manifest_sha256
        ):
            return _provider_evidence(
                component,
                "unverified",
                str(model_key),
                str(artifact_sha256),
                _recorded_identity(verified_artifact),
                "artifact is verified, but the provider concrete adapter is not allowlisted",
            )
    return _provider_evidence(
        component,
        "unverified",
        str(model_key),
        str(artifact_sha256),
        None,
        "provider has no accepted concrete local-artifact provenance",
    )


def evaluate_run_trust(
    *,
    annotation_manifest: AnnotationManifest,
    providers: tuple[ProviderEvidence, ...],
    corpus_unchanged: bool,
    production_sources_unchanged: bool,
    failure_count: int,
) -> RunTrustDecision:
    """Issue trusted/test-only/failed status from immutable run evidence."""

    if any(provider._issuer is not _TRUST_ISSUER for provider in providers):
        raise TypeError("provider evidence was not issued by the trust gate")
    provider_keys = tuple(
        (provider.component, provider.model_key, provider.artifact_manifest_sha256)
        for provider in providers
    )
    if len(provider_keys) != len(set(provider_keys)):
        raise ValueError("provider evidence contains duplicate component identities")
    ordered_providers = tuple(
        sorted(
            providers,
            key=lambda provider: (
                provider.component,
                provider.model_key,
                provider.artifact_manifest_sha256,
            ),
        )
    )
    reasons: list[str] = []
    failed = False
    if annotation_manifest.approved_question_count == 0:
        reasons.append("no approved expert questions")
        failed = True
    if not providers:
        reasons.append("no provider evidence")
        failed = True
    if not corpus_unchanged:
        reasons.append("published corpus changed during the run")
        failed = True
    if not production_sources_unchanged:
        reasons.append("production defaults changed during the run")
        failed = True
    if failure_count:
        reasons.append(f"run recorded {failure_count} failure(s)")
        failed = True
    if any(provider.provider_kind == "unverified" for provider in providers):
        reasons.append("one or more providers are unverified")
        failed = True
    has_fake = any(provider.provider_kind == "deterministic_fake" for provider in providers)
    if has_fake:
        reasons.append("one or more deterministic fake providers were used")

    status: TrustStatus
    if failed:
        status = "failed"
    elif has_fake:
        status = "test_only"
    else:
        status = "trusted"
        reasons.append("all trust gates passed")
    return RunTrustDecision(
        status=status,
        reasons=tuple(reasons),
        provider_records=ordered_providers,
        corpus_release_key=annotation_manifest.corpus_release_key,
        corpus_manifest_sha256=annotation_manifest.corpus_manifest_sha256,
        annotation_manifest_sha256=annotation_manifest.annotation_manifest_sha256,
        gold_sha256=annotation_manifest.gold_sha256,
        approved_question_count=annotation_manifest.approved_question_count,
        _issuer=_TRUST_ISSUER,
    )


def is_issued_trust_decision(value: object) -> bool:
    """Prevent callers from substituting a shape-compatible trusted flag."""

    return isinstance(value, RunTrustDecision) and value._issuer is _TRUST_ISSUER


def _provider_evidence(
    component: Literal["embedding", "reranker"],
    provider_kind: Literal["verified_local", "deterministic_fake", "unverified"],
    model_key: str,
    artifact_manifest_sha256: str,
    model_identity: RecordedModelIdentity | None,
    reason: str,
) -> ProviderEvidence:
    return ProviderEvidence(
        component=component,
        provider_kind=provider_kind,
        model_key=model_key,
        artifact_manifest_sha256=artifact_manifest_sha256,
        model_identity=model_identity,
        reason=reason,
        _issuer=_TRUST_ISSUER,
    )


def _recorded_identity(artifact: VerifiedModelArtifact) -> RecordedModelIdentity:
    manifest = artifact.manifest
    return RecordedModelIdentity(
        artifact_manifest_schema_version=manifest.manifest_schema_version,
        artifact_manifest_sha256=artifact.artifact_manifest_sha256,
        model_key=manifest.model_key,
        model_id=manifest.model_id,
        exact_revision=manifest.exact_revision,
        license=manifest.license,
        representation=manifest.representation,
        runtime_key=manifest.runtime_key,
    )


def _baseline_model_identity(
    artifact_manifest_sha256: str,
) -> RecordedModelIdentity | None:
    try:
        return RecordedModelIdentity(
            artifact_manifest_schema_version="embedding-artifact-manifest-v1",
            artifact_manifest_sha256=artifact_manifest_sha256,
            model_key=EMBEDDING_MODEL_KEY,
            model_id=EMBEDDING_REPOSITORY_ID,
            exact_revision=EMBEDDING_REVISION,
            license="MIT",
            representation=baseline_bge_representation_contract(),
            runtime_key="runtime:sentence-transformers:local-bge-production-v1",
        )
    except Exception:
        return None
