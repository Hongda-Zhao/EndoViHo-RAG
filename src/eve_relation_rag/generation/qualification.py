"""Pre-registered, replayable qualification evidence for the fixed V0 provider.

The definition is written before a provider process is started.  It freezes one exact
candidate, one synthetic grounded ContextPack, and objective pass rules.  A report can only be
built when every rule passed and binds both semantic manifests and their physical file bytes.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import os
import platform
import re
import stat
import sys
import tomllib
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import AfterValidator, Field, ValidationError, model_validator

from eve_relation_rag.generation.context import (
    APPROVED_ANSWER_INSTRUCTIONS,
    build_literature_context,
    canonical_context_json,
)
from eve_relation_rag.generation.policy import LocalModelPolicyManifest, PromptPolicyManifest
from eve_relation_rag.generation.providers import LLMProvider
from eve_relation_rag.hybrid.contracts import (
    ContextPack,
    GenerationComposition,
    ProviderIdentity,
    StrictFrozenSchema,
    canonical_model_json,
    canonical_model_sha256,
    canonical_self_sha256,
)
from eve_relation_rag.literature.contracts import (
    EMBEDDING_MODEL_KEY,
    RETRIEVAL_POLICY_KEY,
    RETRIEVED_CHUNKS_VERSION,
    LiteratureRetrievalRequest,
    PlainTextLocator,
    RetrievedChunk,
    RetrievedChunks,
    Sha256,
    StableToken,
)
from eve_relation_rag.literature.hashing import canonical_query_sha256

type ProviderQualificationClientDistributionName = Literal["pydantic", "pydantic-core"]

PROVIDER_QUALIFICATION_DEFINITION_VERSION: Final = "v0-provider-qualification-definition-v1"
PROVIDER_QUALIFICATION_REPORT_VERSION: Final = "v0-provider-qualification-report-v1"
PROVIDER_QUALIFICATION_OBSERVATION_VERSION: Final = "v0-provider-qualification-observation-v1"
PROVIDER_QUALIFICATION_CANDIDATE_KEY: Final = (
    "provider-qualification:qwen3-4b-instruct-2507-4bit:v0"
)
PROVIDER_QUALIFICATION_CASE_KEY: Final = "qualification:v0:fixed-grounded-literature"
PROVIDER_QUALIFICATION_RUNNER_PATH: Final = "scripts/run_v0_provider_qualification.py"
PROVIDER_QUALIFICATION_MODULE_PATH: Final = "src/eve_relation_rag/generation/qualification.py"
PROVIDER_QUALIFICATION_CLIENT_RUNTIME_VERSION: Final = "v0-provider-qualification-client-runtime-v1"
CURRENT_MODEL_POLICY_SHA256: Final = (
    "43a819d8532b3b267d8426c94134f287cd01152edd6657c28a522c13a2fead94"
)
CURRENT_PROMPT_POLICY_SHA256: Final = (
    "5d456d6083d6b4101f9877327c432a61a9d9a6dfee54986ed2e0a0ef02315a2b"
)
CURRENT_PROVIDER_ENVIRONMENT_MANIFEST_SHA256: Final = (
    "c34aacd009a38a0f4b7e60823a7e0566d2f78b12e4b7e4ed828207d66b400293"
)
CURRENT_PROVIDER_ENVIRONMENT_SHA256: Final = (
    "574a92fbb91f6d7e1bfe23b9a95f223cdd511a6a69fb841ecc2ecbfd767c59b8"
)
CURRENT_MODEL_KEY: Final = "model:hf:mlx-community:Qwen3-4B-Instruct-2507-4bit"
CURRENT_MODEL_REVISION: Final = "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b"

FIXED_QUALIFICATION_QUESTION: Final = "Explain the literature evidence for the synthetic benchmark"
FIXED_QUALIFICATION_EVIDENCE: Final = "The synthetic benchmark contains exact supporting evidence."

_MODEL_POLICY_PATH: Final = (
    ".artifacts/v0_activation/manifests/v0_local_model_policy_manifest.v2.json"
)
_PROMPT_POLICY_PATH: Final = (
    ".artifacts/v0_activation/manifests/v0_prompt_policy_manifest.v2.json"
)
_ENVIRONMENT_MANIFEST_PATH: Final = (
    ".artifacts/v0_activation/manifests/v0_provider_environment_manifest.json"
)
_CLIENT_PYTHON_LAUNCHER_PATH: Final = ".venv/bin/python"
_CLIENT_PYTHON_IDENTITY_PATH: Final = "runtime/client-python-resolved-executable"
_CLIENT_RUNTIME_SOURCE_PATHS: Final[tuple[str, ...]] = (
    "scripts/run_v0_provider_qualification.py",
    "scripts/v0_provider_environment.py",
    "src/eve_relation_rag/__init__.py",
    "src/eve_relation_rag/config/__init__.py",
    "src/eve_relation_rag/config/loopback.py",
    "src/eve_relation_rag/config/settings.py",
    "src/eve_relation_rag/domain/keys.py",
    "src/eve_relation_rag/generation/__init__.py",
    "src/eve_relation_rag/generation/composer.py",
    "src/eve_relation_rag/generation/context.py",
    "src/eve_relation_rag/generation/local_provider.py",
    "src/eve_relation_rag/generation/policy.py",
    "src/eve_relation_rag/generation/providers.py",
    "src/eve_relation_rag/generation/qualification.py",
    "src/eve_relation_rag/generation/rendering.py",
    "src/eve_relation_rag/generation/validators.py",
    "src/eve_relation_rag/hybrid/__init__.py",
    "src/eve_relation_rag/hybrid/contracts.py",
    "src/eve_relation_rag/literature/__init__.py",
    "src/eve_relation_rag/literature/contracts.py",
    "src/eve_relation_rag/literature/hashing.py",
    "src/eve_relation_rag/planning/__init__.py",
    "src/eve_relation_rag/planning/query_plans.py",
    "src/eve_relation_rag/planning/scope_policy.py",
    "src/eve_relation_rag/retrieval/__init__.py",
    "src/eve_relation_rag/retrieval/structured/__init__.py",
    "src/eve_relation_rag/retrieval/structured/rendering.py",
    "src/eve_relation_rag/retrieval/structured/results.py",
)
_CLIENT_RUNTIME_DISTRIBUTIONS: Final[tuple[ProviderQualificationClientDistributionName, ...]] = (
    "pydantic",
    "pydantic-core",
)
_CLIENT_RUNTIME_IMPORTED_MODULES: Final[tuple[tuple[str, str], ...]] = (
    ("eve_relation_rag", "src/eve_relation_rag/__init__.py"),
    ("eve_relation_rag.config", "src/eve_relation_rag/config/__init__.py"),
    (
        "eve_relation_rag.config.loopback",
        "src/eve_relation_rag/config/loopback.py",
    ),
    (
        "eve_relation_rag.config.settings",
        "src/eve_relation_rag/config/settings.py",
    ),
    ("eve_relation_rag.domain.keys", "src/eve_relation_rag/domain/keys.py"),
    ("eve_relation_rag.generation", "src/eve_relation_rag/generation/__init__.py"),
    (
        "eve_relation_rag.generation.composer",
        "src/eve_relation_rag/generation/composer.py",
    ),
    (
        "eve_relation_rag.generation.context",
        "src/eve_relation_rag/generation/context.py",
    ),
    (
        "eve_relation_rag.generation.local_provider",
        "src/eve_relation_rag/generation/local_provider.py",
    ),
    (
        "eve_relation_rag.generation.policy",
        "src/eve_relation_rag/generation/policy.py",
    ),
    (
        "eve_relation_rag.generation.providers",
        "src/eve_relation_rag/generation/providers.py",
    ),
    (
        "eve_relation_rag.generation.qualification",
        "src/eve_relation_rag/generation/qualification.py",
    ),
    (
        "eve_relation_rag.generation.rendering",
        "src/eve_relation_rag/generation/rendering.py",
    ),
    (
        "eve_relation_rag.generation.validators",
        "src/eve_relation_rag/generation/validators.py",
    ),
    ("eve_relation_rag.hybrid", "src/eve_relation_rag/hybrid/__init__.py"),
    ("eve_relation_rag.hybrid.contracts", "src/eve_relation_rag/hybrid/contracts.py"),
    ("eve_relation_rag.literature", "src/eve_relation_rag/literature/__init__.py"),
    (
        "eve_relation_rag.literature.contracts",
        "src/eve_relation_rag/literature/contracts.py",
    ),
    (
        "eve_relation_rag.literature.hashing",
        "src/eve_relation_rag/literature/hashing.py",
    ),
    ("eve_relation_rag.planning", "src/eve_relation_rag/planning/__init__.py"),
    (
        "eve_relation_rag.planning.query_plans",
        "src/eve_relation_rag/planning/query_plans.py",
    ),
    (
        "eve_relation_rag.planning.scope_policy",
        "src/eve_relation_rag/planning/scope_policy.py",
    ),
    ("eve_relation_rag.retrieval", "src/eve_relation_rag/retrieval/__init__.py"),
    (
        "eve_relation_rag.retrieval.structured",
        "src/eve_relation_rag/retrieval/structured/__init__.py",
    ),
    (
        "eve_relation_rag.retrieval.structured.rendering",
        "src/eve_relation_rag/retrieval/structured/rendering.py",
    ),
    (
        "eve_relation_rag.retrieval.structured.results",
        "src/eve_relation_rag/retrieval/structured/results.py",
    ),
    (
        "scripts.run_v0_provider_qualification",
        "scripts/run_v0_provider_qualification.py",
    ),
    ("scripts.v0_provider_environment", "scripts/v0_provider_environment.py"),
)
_MAX_DEFINITION_BYTES: Final = 512 * 1024
_MAX_REPORT_BYTES: Final = 512 * 1024
_HASH_CHUNK_BYTES: Final = 1024 * 1024
_STARTUP_DEADLINE_NS: Final = 300_000_000_000
_GENERATION_DEADLINE_NS: Final = 300_000_000_000
_RFC3339_UTC_RE: Final = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)


def _validate_rfc3339_utc(value: str) -> str:
    if _RFC3339_UTC_RE.fullmatch(value) is None:
        raise ValueError("qualification timestamp must be canonical RFC3339 UTC")
    datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    return value


Rfc3339Utc = Annotated[str, AfterValidator(_validate_rfc3339_utc)]


class ProviderQualificationError(RuntimeError):
    """A qualification definition, execution observation, or report failed closed."""


class QualificationFileIdentity(StrictFrozenSchema):
    """Physical identity of one no-symlink regular file."""

    relative_path: str = Field(min_length=1, max_length=512, pattern=r"^[A-Za-z0-9._/-]+$")
    byte_size: int = Field(gt=0)
    sha256: Sha256

    @model_validator(mode="after")
    def validate_relative_path(self) -> Self:
        path = Path(self.relative_path)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("qualification file path must be canonical and relative")
        if path.as_posix() != self.relative_path:
            raise ValueError("qualification file path must use canonical POSIX separators")
        return self


class ProviderQualificationClientPythonIdentity(StrictFrozenSchema):
    """Exact client interpreter used to execute the qualification decision code."""

    implementation: Literal["cpython"]
    version: str = Field(min_length=1, max_length=64)
    cache_tag: str = Field(min_length=1, max_length=64)
    compiler: str = Field(min_length=1, max_length=256)
    launcher_relative_path: Literal[".venv/bin/python"] = _CLIENT_PYTHON_LAUNCHER_PATH
    resolved_executable: QualificationFileIdentity

    @model_validator(mode="after")
    def validate_executable_label(self) -> Self:
        if self.resolved_executable.relative_path != _CLIENT_PYTHON_IDENTITY_PATH:
            raise ValueError("client Python executable identity has an invalid label")
        return self


class ProviderQualificationClientDistributionIdentity(StrictFrozenSchema):
    """Version plus RECORD and recorded-content identity for one client distribution."""

    distribution_name: ProviderQualificationClientDistributionName
    version: str = Field(min_length=1, max_length=128)
    record_relative_path: str = Field(
        min_length=1,
        max_length=512,
        pattern=r"^[A-Za-z0-9._/-]+$",
    )
    record_byte_size: int = Field(gt=0)
    record_sha256: Sha256
    recorded_file_count: int = Field(gt=0)
    recorded_total_byte_size: int = Field(gt=0)
    recorded_content_manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_record_path(self) -> Self:
        path = Path(self.record_relative_path)
        if (
            path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.as_posix() != self.record_relative_path
            or path.name != "RECORD"
            or not any(part.endswith(".dist-info") for part in path.parts)
        ):
            raise ValueError("client distribution RECORD path is invalid")
        return self


class ProviderQualificationLockedDependency(StrictFrozenSchema):
    """One critical resolved package projection from uv.lock."""

    distribution_name: ProviderQualificationClientDistributionName
    version: str = Field(min_length=1, max_length=128)
    locked_package_sha256: Sha256


class ProviderQualificationDependencyProjection(StrictFrozenSchema):
    """Runtime-semantic pyproject/lock projection that excludes root release metadata."""

    projection_schema_version: Literal["v0-qualification-dependency-projection-v1"] = (
        "v0-qualification-dependency-projection-v1"
    )
    requires_python: str = Field(min_length=1, max_length=128)
    declared_runtime_dependencies: tuple[str, ...] = Field(min_length=1)
    pyproject_runtime_projection_sha256: Sha256
    uv_lock_version: int = Field(gt=0)
    uv_lock_revision: int = Field(ge=0)
    uv_requires_python: str = Field(min_length=1, max_length=128)
    locked_critical_dependencies: tuple[ProviderQualificationLockedDependency, ...] = Field(
        min_length=2,
        max_length=2,
    )
    uv_runtime_projection_sha256: Sha256
    projection_sha256: Sha256

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        if self.declared_runtime_dependencies != tuple(
            sorted(set(self.declared_runtime_dependencies))
        ):
            raise ValueError("declared runtime dependencies are not canonical and unique")
        names = tuple(item.distribution_name for item in self.locked_critical_dependencies)
        if names != _CLIENT_RUNTIME_DISTRIBUTIONS:
            raise ValueError("locked critical dependency inventory is not exact")
        if self.projection_sha256 != canonical_self_sha256(self, "projection_sha256"):
            raise ValueError("dependency projection checksum does not match")
        return self


class ProviderQualificationClientRuntimeManifest(StrictFrozenSchema):
    """Canonical identity of all client code and runtime used to judge the candidate."""

    manifest_schema_version: Literal["v0-provider-qualification-client-runtime-v1"] = (
        PROVIDER_QUALIFICATION_CLIENT_RUNTIME_VERSION
    )
    source_files: tuple[QualificationFileIdentity, ...] = Field(min_length=1)
    source_manifest_sha256: Sha256
    dependency_projection: ProviderQualificationDependencyProjection
    python: ProviderQualificationClientPythonIdentity
    distributions: tuple[ProviderQualificationClientDistributionIdentity, ...] = Field(
        min_length=2,
        max_length=2,
    )
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_runtime_manifest(self) -> Self:
        source_paths = tuple(item.relative_path for item in self.source_files)
        distribution_names = tuple(item.distribution_name for item in self.distributions)
        if source_paths != _CLIENT_RUNTIME_SOURCE_PATHS:
            raise ValueError("client runtime source inventory is not exact and canonical")
        if len(source_paths) != len(set(source_paths)):
            raise ValueError("client runtime source inventory contains duplicates")
        if distribution_names != _CLIENT_RUNTIME_DISTRIBUTIONS:
            raise ValueError("client runtime distribution inventory is not exact and canonical")
        if len(distribution_names) != len(set(distribution_names)):
            raise ValueError("client runtime distribution inventory contains duplicates")
        if self.source_manifest_sha256 != canonical_model_sha256(self.source_files):
            raise ValueError("client runtime source checksum does not match")
        locked_versions = tuple(
            (item.distribution_name, item.version)
            for item in self.dependency_projection.locked_critical_dependencies
        )
        installed_versions = tuple(
            (item.distribution_name, item.version) for item in self.distributions
        )
        if installed_versions != locked_versions:
            raise ValueError("installed client distributions differ from the semantic lock")
        if self.manifest_sha256 != canonical_self_sha256(self, "manifest_sha256"):
            raise ValueError("client runtime manifest checksum does not match")
        return self


class ProviderEnvironmentManifestBinding(StrictFrozenSchema):
    """Compact environment identity independently reproduced by the launcher."""

    manifest_schema_version: Literal["v0-provider-environment-manifest-v1"]
    identity_schema_version: Literal["v0-provider-environment-identity-v1"]
    manifest_sha256: Sha256
    provider_environment_sha256: Sha256
    provider_environment_distribution_count: int = Field(gt=0)
    provider_environment_file_count: int = Field(gt=0)


class ProviderQualificationCandidate(StrictFrozenSchema):
    """One exact model/runtime candidate admitted to the fixed qualification."""

    candidate_key: Literal["provider-qualification:qwen3-4b-instruct-2507-4bit:v0"] = (
        PROVIDER_QUALIFICATION_CANDIDATE_KEY
    )
    provider_key: StableToken
    model_key: StableToken
    model_revision: StableToken
    generation_policy_key: StableToken
    prompt_policy_key: StableToken
    model_policy_manifest_sha256: Sha256
    prompt_policy_manifest_sha256: Sha256
    provider_environment_manifest_sha256: Sha256
    provider_environment_sha256: Sha256
    provider_environment_distribution_count: int = Field(gt=0)
    provider_environment_file_count: int = Field(gt=0)
    runtime_launcher_sha256: Sha256
    runtime_proxy_sha256: Sha256
    inference_engine_wrapper_sha256: Sha256
    egress_profile_sha256: Sha256
    sandbox_executable_sha256: Sha256
    environment_executable_sha256: Sha256
    network_policy_key: Literal["network:macos-sandbox-v0-ports-only-v2"]
    environment_policy_key: Literal["environment:scrubbed-allowlist-v1"]
    inner_authentication_key: Literal["authentication:inherited-fd-bearer-v1"]
    runtime_attestation_path: Literal["/v0/runtime-attestation"]
    network_attestation_key: Literal["egress-probe:external-and-unapproved-loopback-denied-v2"]
    outer_port: Literal[8123]
    inner_port: Literal[8124]
    timeout_seconds: Literal[300]
    retry_count: Literal[0]
    model_policy_file: QualificationFileIdentity
    prompt_policy_file: QualificationFileIdentity
    provider_environment_manifest_file: QualificationFileIdentity


class ProviderQualificationPassRule(StrictFrozenSchema):
    """Frozen objective rules evaluated after the one candidate has run."""

    startup_clock: Literal["time.perf_counter_ns"] = "time.perf_counter_ns"
    startup_deadline_ns: Literal[300_000_000_000] = _STARTUP_DEADLINE_NS
    generation_deadline_ns: Literal[300_000_000_000] = _GENERATION_DEADLINE_NS
    required_inner_unauthenticated_status: Literal[401] = 401
    require_hmac_runtime_attestation: Literal[True] = True
    require_provider_check_ready: Literal[True] = True
    generation_request_count: Literal[1] = 1
    retry_count: Literal[0] = 0
    minimum_claim_count: Literal[1] = 1
    minimum_citation_count: Literal[1] = 1
    validation_scope: Literal["mechanical"] = "mechanical"
    require_clean_shutdown: Literal[True] = True
    required_process_exit_code: Literal[0] = 0


class ProviderQualificationDefinition(StrictFrozenSchema):
    """Self-hashed pre-registration written before provider execution."""

    definition_schema_version: Literal["v0-provider-qualification-definition-v1"] = (
        PROVIDER_QUALIFICATION_DEFINITION_VERSION
    )
    qualification_key: Literal["qualification:v0:fixed-offline-provider-v1"] = (
        "qualification:v0:fixed-offline-provider-v1"
    )
    target_operating_system: Literal["Darwin"] = "Darwin"
    target_architecture: Literal["arm64"] = "arm64"
    execution_mode: Literal["fixed-offline-single-candidate"] = "fixed-offline-single-candidate"
    selection_rule: Literal["only_passing_candidate"] = "only_passing_candidate"
    candidate_set: tuple[ProviderQualificationCandidate, ...] = Field(min_length=1, max_length=1)
    runner_file: QualificationFileIdentity
    qualification_module_file: QualificationFileIdentity
    client_runtime_manifest: ProviderQualificationClientRuntimeManifest
    synthetic_case_key: Literal["qualification:v0:fixed-grounded-literature"] = (
        PROVIDER_QUALIFICATION_CASE_KEY
    )
    synthetic_context: ContextPack
    synthetic_context_self_sha256: Sha256
    synthetic_context_canonical_sha256: Sha256
    synthetic_context_byte_size: int = Field(gt=0)
    pass_rule: ProviderQualificationPassRule
    definition_sha256: Sha256

    @model_validator(mode="after")
    def validate_definition(self) -> Self:
        candidate = self.candidate_set[0]
        if (
            candidate.model_policy_manifest_sha256 != CURRENT_MODEL_POLICY_SHA256
            or candidate.prompt_policy_manifest_sha256 != CURRENT_PROMPT_POLICY_SHA256
            or candidate.provider_environment_manifest_sha256
            != CURRENT_PROVIDER_ENVIRONMENT_MANIFEST_SHA256
            or candidate.provider_environment_sha256 != CURRENT_PROVIDER_ENVIRONMENT_SHA256
            or candidate.model_key != CURRENT_MODEL_KEY
            or candidate.model_revision != CURRENT_MODEL_REVISION
            or self.runner_file.relative_path != PROVIDER_QUALIFICATION_RUNNER_PATH
            or self.qualification_module_file.relative_path != PROVIDER_QUALIFICATION_MODULE_PATH
        ):
            raise ValueError("qualification definition does not identify the current V0 candidate")
        fixed_context = build_fixed_qualification_context()
        canonical = canonical_context_json(fixed_context).encode("utf-8")
        runtime_files = {
            item.relative_path: item for item in self.client_runtime_manifest.source_files
        }
        if (
            runtime_files.get(PROVIDER_QUALIFICATION_RUNNER_PATH) != self.runner_file
            or runtime_files.get(PROVIDER_QUALIFICATION_MODULE_PATH)
            != self.qualification_module_file
        ):
            raise ValueError("qualification definition client runtime is inconsistent")
        if (
            self.synthetic_context != fixed_context
            or self.synthetic_context_self_sha256 != fixed_context.context_sha256
            or self.synthetic_context_canonical_sha256 != hashlib.sha256(canonical).hexdigest()
            or self.synthetic_context_byte_size != len(canonical)
        ):
            raise ValueError("qualification definition does not contain the fixed ContextPack")
        if self.definition_sha256 != canonical_self_sha256(self, "definition_sha256"):
            raise ValueError("qualification definition checksum does not match")
        return self


class ProviderQualificationObservation(StrictFrozenSchema):
    """Raw run result supplied to the fail-closed report builder."""

    observation_schema_version: Literal["v0-provider-qualification-observation-v1"] = (
        PROVIDER_QUALIFICATION_OBSERVATION_VERSION
    )
    started_at: Rfc3339Utc
    completed_at: Rfc3339Utc
    host_operating_system: str = Field(min_length=1, max_length=64)
    host_architecture: str = Field(min_length=1, max_length=64)
    startup_duration_ns: int = Field(ge=0)
    generation_duration_ns: int = Field(ge=0)
    outer_ready: bool
    inner_unauthenticated_status: int = Field(ge=100, le=599)
    hmac_runtime_attestation_verified: bool
    provider_check_ready: bool
    generation_request_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    context_self_sha256: Sha256
    context_canonical_sha256: Sha256
    provider_output_sha256: Sha256
    provider_output_byte_size: int = Field(gt=0)
    composition_sha256: Sha256
    claim_count: int = Field(ge=0)
    citation_count: int = Field(ge=0)
    validation_scope: str = Field(min_length=1, max_length=64)
    clean_shutdown: bool
    process_exit_code: int
    outer_port_closed_after_shutdown: bool
    inner_port_closed_after_shutdown: bool

    @model_validator(mode="after")
    def validate_timestamp_order(self) -> Self:
        started = datetime.fromisoformat(self.started_at.removesuffix("Z") + "+00:00")
        completed = datetime.fromisoformat(self.completed_at.removesuffix("Z") + "+00:00")
        if completed < started:
            raise ValueError("qualification completion precedes its start")
        return self


class ProviderQualificationReport(StrictFrozenSchema):
    """Self-hashed durable evidence for the sole passing fixed candidate."""

    report_schema_version: Literal["v0-provider-qualification-report-v1"] = (
        PROVIDER_QUALIFICATION_REPORT_VERSION
    )
    qualification_key: Literal["qualification:v0:fixed-offline-provider-v1"]
    definition_sha256: Sha256
    definition_file: QualificationFileIdentity
    candidate: ProviderQualificationCandidate
    candidate_status: Literal["passed"] = "passed"
    selected_candidate_key: Literal["provider-qualification:qwen3-4b-instruct-2507-4bit:v0"] = (
        PROVIDER_QUALIFICATION_CANDIDATE_KEY
    )
    selection: Literal["only_passing_candidate"] = "only_passing_candidate"
    target_operating_system: Literal["Darwin"] = "Darwin"
    target_architecture: Literal["arm64"] = "arm64"
    runner_file: QualificationFileIdentity
    qualification_module_file: QualificationFileIdentity
    client_runtime_manifest: ProviderQualificationClientRuntimeManifest
    network_attestation_key: Literal["egress-probe:external-and-unapproved-loopback-denied-v2"]
    pass_rule: ProviderQualificationPassRule
    observation: ProviderQualificationObservation
    report_sha256: Sha256

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        _require_passing_observation(self.observation, self.pass_rule)
        if (
            self.candidate.candidate_key != self.selected_candidate_key
            or self.runner_file.relative_path != PROVIDER_QUALIFICATION_RUNNER_PATH
            or self.qualification_module_file.relative_path != PROVIDER_QUALIFICATION_MODULE_PATH
            or self.runner_file
            != {item.relative_path: item for item in self.client_runtime_manifest.source_files}.get(
                PROVIDER_QUALIFICATION_RUNNER_PATH
            )
            or self.qualification_module_file
            != {item.relative_path: item for item in self.client_runtime_manifest.source_files}.get(
                PROVIDER_QUALIFICATION_MODULE_PATH
            )
            or self.network_attestation_key != self.candidate.network_attestation_key
        ):
            raise ValueError("qualification report selection or runtime identity is inconsistent")
        if self.report_sha256 != canonical_self_sha256(self, "report_sha256"):
            raise ValueError("qualification report checksum does not match")
        return self


class GenerationCallRecord(StrictFrozenSchema):
    """Exact request/output byte identities captured around one provider call."""

    request_count: Literal[1]
    context_canonical_sha256: Sha256
    context_byte_size: int = Field(gt=0)
    provider_output_sha256: Sha256
    provider_output_byte_size: int = Field(gt=0)


class RecordingGenerationProvider:
    """One-shot wrapper that records exact ContextPack/output UTF-8 bytes."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider
        self._request_count = 0
        self._record: GenerationCallRecord | None = None

    @property
    def identity(self) -> ProviderIdentity:
        return self._provider.identity

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def record(self) -> GenerationCallRecord:
        if self._record is None:
            raise ProviderQualificationError("The qualification generation did not complete.")
        return self._record

    def generate(self, context_json: str) -> str:
        if self._request_count != 0:
            raise ProviderQualificationError(
                "The qualification provider accepts exactly one generation request."
            )
        self._request_count += 1
        context_bytes = context_json.encode("utf-8")
        output = self._provider.generate(context_json)
        if not isinstance(output, str):
            raise ProviderQualificationError("The qualification provider output is invalid.")
        output_bytes = output.encode("utf-8")
        self._record = GenerationCallRecord(
            request_count=1,
            context_canonical_sha256=hashlib.sha256(context_bytes).hexdigest(),
            context_byte_size=len(context_bytes),
            provider_output_sha256=hashlib.sha256(output_bytes).hexdigest(),
            provider_output_byte_size=len(output_bytes),
        )
        return output


def build_fixed_qualification_context() -> ContextPack:
    """Build the canonical synthetic fixture already used by V0 provider tests."""

    chunk = RetrievedChunk(
        citation_id="D1",
        chunk_key=f"chunk:sha256:{'d' * 64}",
        document_key=f"document:sha256:{'c' * 64}",
        title="Synthetic M4 benchmark document",
        doi="10.1234/synthetic.1",
        pmid="12345678",
        pmcid="PMC123456",
        section="Methods",
        locator=PlainTextLocator(
            locator_type="plain_text",
            paragraph_ordinal=1,
            line_start=1,
            line_end=1,
            token_start=None,
            token_end=None,
        ),
        locator_text="paragraph 1, line 1",
        text=FIXED_QUALIFICATION_EVIDENCE,
        text_sha256=hashlib.sha256(FIXED_QUALIFICATION_EVIDENCE.encode("utf-8")).hexdigest(),
        retrieval_tier="corpus_fill",
        fts_rank=1,
        vector_rank=1,
        summary_vector_rank=1,
        rrf_score="0.049180327869",
        matched_anchors=(),
    )
    corpus_release_key = "corpus:endoviho-rag:v0:20991231:999"
    request = LiteratureRetrievalRequest(
        request_schema_version="literature-retrieval-request-v1",
        corpus_release_key=corpus_release_key,
        question=FIXED_QUALIFICATION_QUESTION,
        top_k=8,
    )
    chunks = RetrievedChunks(
        result_schema_version=RETRIEVED_CHUNKS_VERSION,
        status="ok",
        corpus_release_key=corpus_release_key,
        corpus_manifest_sha256="e" * 64,
        retrieval_policy_key=RETRIEVAL_POLICY_KEY,
        embedding_model_key=EMBEDDING_MODEL_KEY,
        query_sha256=canonical_query_sha256(request, ()),
        requested_top_k=8,
        returned_count=1,
        retrieval_executed=True,
        anchor_mode="none",
        anchors_applied=(),
        warnings=(),
        chunks=(chunk,),
    )
    return build_literature_context(
        original_question=FIXED_QUALIFICATION_QUESTION,
        retrieved_chunks=chunks,
        answer_instructions=APPROVED_ANSWER_INSTRUCTIONS,
    )


def parse_provider_environment_manifest(
    manifest: Mapping[str, object],
) -> ProviderEnvironmentManifestBinding:
    """Extract the compact fields qualification needs after the verifier checked the manifest."""

    try:
        return ProviderEnvironmentManifestBinding.model_validate(
            {field: manifest[field] for field in ProviderEnvironmentManifestBinding.model_fields}
        )
    except (KeyError, TypeError, ValidationError):
        raise ProviderQualificationError(
            "The provider environment manifest is unavailable or invalid."
        ) from None


def _current_project_root() -> Path:
    try:
        module_path = Path(__file__).resolve(strict=True)
        root = module_path.parents[3]
        if module_path != root / PROVIDER_QUALIFICATION_MODULE_PATH:
            raise OSError
        return root
    except (IndexError, OSError):
        raise ProviderQualificationError(
            "The qualification module import origin is not the project source tree."
        ) from None


def _validated_project_root(root: Path) -> Path:
    try:
        if root.is_symlink() or not root.is_dir():
            raise OSError
        resolved = root.resolve(strict=True)
        if not stat.S_ISDIR(resolved.lstat().st_mode):
            raise OSError
        return resolved
    except OSError:
        raise ProviderQualificationError(
            "The qualification client project root is unavailable or unsafe."
        ) from None


def _has_symlink_component(root: Path, relative_path: str) -> bool:
    current = root
    try:
        for part in Path(relative_path).parts:
            current = current / part
            if stat.S_ISLNK(current.lstat().st_mode):
                return True
    except OSError:
        return True
    return False


def _project_file_identity(root: Path, relative_path: str) -> QualificationFileIdentity:
    if _has_symlink_component(root, relative_path):
        raise ProviderQualificationError(
            "A qualification client source file has a symlink component."
        )
    return qualification_file_identity(root / relative_path, relative_path=relative_path)


def _assert_import_origins(root: Path) -> None:
    try:
        module_names = tuple(item[0] for item in _CLIENT_RUNTIME_IMPORTED_MODULES)
        origin_paths = tuple(item[1] for item in _CLIENT_RUNTIME_IMPORTED_MODULES)
        if (
            len(module_names) != len(set(module_names))
            or len(origin_paths) != len(set(origin_paths))
            or tuple(sorted(origin_paths)) != _CLIENT_RUNTIME_SOURCE_PATHS
        ):
            raise ValueError
        for module_name, relative_path in _CLIENT_RUNTIME_IMPORTED_MODULES:
            expected = (root / relative_path).resolve(strict=True)
            if module_name == "scripts.run_v0_provider_qualification":
                module = sys.modules.get(module_name)
                if module is None:
                    direct_module = sys.modules.get("__main__")
                    direct_origin = getattr(direct_module, "__file__", None)
                    try:
                        direct_matches = isinstance(direct_origin, str) and (
                            Path(direct_origin).resolve(strict=True) == expected
                        )
                    except OSError:
                        direct_matches = False
                    if direct_matches:
                        module = direct_module
                if module is None:
                    # Packet verification imports the qualification API but does not execute the
                    # runner. Its bytes remain source-bound; either live runner form is checked.
                    continue
            elif module_name == "scripts.v0_provider_environment":
                module = sys.modules.get("v0_provider_environment") or sys.modules.get(module_name)
                if module is None:
                    # Packet verification does not execute this script. Its exact bytes are still
                    # source-bound; when either runner import form is live, its origin is checked.
                    continue
            else:
                module = importlib.import_module(module_name)
            origin = getattr(module, "__file__", None)
            if not isinstance(origin, str):
                raise OSError
            if Path(origin).resolve(strict=True) != expected:
                raise OSError

        direct_script_module = sys.modules.get("v0_provider_environment")
        if direct_script_module is not None:
            direct_origin = getattr(direct_script_module, "__file__", None)
            expected_script = (root / "scripts/v0_provider_environment.py").resolve(strict=True)
            if (
                not isinstance(direct_origin, str)
                or Path(direct_origin).resolve(strict=True) != expected_script
            ):
                raise OSError
    except (ImportError, OSError, RuntimeError, ValueError):
        raise ProviderQualificationError(
            "A qualification client module import origin does not match the project source tree."
        ) from None


def _client_python_identity(root: Path) -> ProviderQualificationClientPythonIdentity:
    try:
        launcher = root / _CLIENT_PYTHON_LAUNCHER_PATH
        resolved_launcher = launcher.resolve(strict=True)
        running_executable = Path(sys.executable).resolve(strict=True)
        if resolved_launcher != running_executable or not resolved_launcher.is_file():
            raise OSError
        _raw, byte_size, sha256 = _read_regular_file(
            resolved_launcher,
            maximum_bytes=None,
        )
        implementation = sys.implementation.name
        cache_tag = sys.implementation.cache_tag
        version = platform.python_version()
        compiler = platform.python_compiler()
        if (
            implementation != "cpython"
            or not isinstance(cache_tag, str)
            or not cache_tag
            or not version
            or not compiler
        ):
            raise ValueError
        return ProviderQualificationClientPythonIdentity(
            implementation="cpython",
            version=version,
            cache_tag=cache_tag,
            compiler=compiler,
            resolved_executable=QualificationFileIdentity(
                relative_path=_CLIENT_PYTHON_IDENTITY_PATH,
                byte_size=byte_size,
                sha256=sha256,
            ),
        )
    except (OSError, ValidationError, ValueError):
        raise ProviderQualificationError(
            "The qualification client Python runtime is unavailable or changed."
        ) from None


def _distribution_identity(
    root: Path,
    distribution_name: ProviderQualificationClientDistributionName,
) -> ProviderQualificationClientDistributionIdentity:
    try:
        environment_root = (root / ".venv").resolve(strict=True)
        if _has_symlink_component(root, ".venv") or not environment_root.is_dir():
            raise OSError
        installed = importlib.metadata.distribution(distribution_name)
        files = installed.files
        if not files:
            raise ValueError
        identities: list[tuple[str, int, str]] = []
        seen: set[str] = set()
        for package_path in files:
            located = Path(str(installed.locate_file(package_path)))
            resolved = located.resolve(strict=True)
            relative = resolved.relative_to(environment_root).as_posix()
            if relative in seen or _has_symlink_component(environment_root, relative):
                raise OSError
            seen.add(relative)
            _raw, byte_size, sha256 = _read_regular_file(
                resolved,
                maximum_bytes=None,
                allow_empty=True,
            )
            identities.append((relative, byte_size, sha256))
        identities.sort(key=lambda item: item[0])
        records = tuple(
            item
            for item in identities
            if Path(item[0]).name == "RECORD"
            and any(part.endswith(".dist-info") for part in Path(item[0]).parts)
        )
        if len(records) != 1:
            raise ValueError
        record = records[0]
        return ProviderQualificationClientDistributionIdentity(
            distribution_name=distribution_name,
            version=installed.version,
            record_relative_path=record[0],
            record_byte_size=record[1],
            record_sha256=record[2],
            recorded_file_count=len(identities),
            recorded_total_byte_size=sum(item[1] for item in identities),
            recorded_content_manifest_sha256=canonical_model_sha256(
                tuple(
                    {
                        "relative_path": relative_path,
                        "byte_size": byte_size,
                        "sha256": sha256,
                    }
                    for relative_path, byte_size, sha256 in identities
                )
            ),
        )
    except (
        OSError,
        RuntimeError,
        ValidationError,
        ValueError,
        importlib.metadata.PackageNotFoundError,
    ):
        raise ProviderQualificationError(
            "A qualification client distribution is unavailable or changed."
        ) from None


def build_provider_qualification_dependency_projection(
    pyproject_bytes: bytes,
    uv_lock_bytes: bytes,
) -> ProviderQualificationDependencyProjection:
    """Project only execution semantics, deliberately excluding the root package version."""

    try:
        pyproject = tomllib.loads(pyproject_bytes.decode("utf-8"))
        uv_lock = tomllib.loads(uv_lock_bytes.decode("utf-8"))
        project = pyproject.get("project")
        if not isinstance(project, dict):
            raise ValueError
        requires_python = project.get("requires-python")
        dependencies = project.get("dependencies")
        if (
            not isinstance(requires_python, str)
            or not isinstance(dependencies, list)
            or not dependencies
            or any(not isinstance(item, str) or not item for item in dependencies)
        ):
            raise ValueError
        canonical_dependencies = tuple(sorted(set(dependencies)))
        pyproject_projection = {
            "requires_python": requires_python,
            "declared_runtime_dependencies": canonical_dependencies,
        }

        lock_version = uv_lock.get("version")
        lock_revision = uv_lock.get("revision")
        uv_requires_python = uv_lock.get("requires-python")
        packages = uv_lock.get("package")
        if (
            not isinstance(lock_version, int)
            or isinstance(lock_version, bool)
            or not isinstance(lock_revision, int)
            or isinstance(lock_revision, bool)
            or not isinstance(uv_requires_python, str)
            or not isinstance(packages, list)
        ):
            raise ValueError
        critical: list[ProviderQualificationLockedDependency] = []
        critical_payloads: list[dict[str, object]] = []
        for distribution_name in _CLIENT_RUNTIME_DISTRIBUTIONS:
            matches = tuple(
                package
                for package in packages
                if isinstance(package, dict) and package.get("name") == distribution_name
            )
            if len(matches) != 1:
                raise ValueError
            package = matches[0]
            version = package.get("version")
            if not isinstance(version, str) or not version:
                raise ValueError
            package_sha256 = canonical_model_sha256(package)
            critical.append(
                ProviderQualificationLockedDependency(
                    distribution_name=distribution_name,
                    version=version,
                    locked_package_sha256=package_sha256,
                )
            )
            critical_payloads.append(package)
        uv_projection = {
            "uv_lock_version": lock_version,
            "uv_lock_revision": lock_revision,
            "uv_requires_python": uv_requires_python,
            "critical_packages": tuple(critical_payloads),
        }
        payload: dict[str, object] = {
            "projection_schema_version": "v0-qualification-dependency-projection-v1",
            **pyproject_projection,
            "pyproject_runtime_projection_sha256": canonical_model_sha256(pyproject_projection),
            "uv_lock_version": lock_version,
            "uv_lock_revision": lock_revision,
            "uv_requires_python": uv_requires_python,
            "locked_critical_dependencies": tuple(critical),
            "uv_runtime_projection_sha256": canonical_model_sha256(uv_projection),
            "projection_sha256": "0" * 64,
        }
        payload["projection_sha256"] = canonical_self_sha256(payload, "projection_sha256")
        return ProviderQualificationDependencyProjection.model_validate(payload)
    except (UnicodeError, ValidationError, ValueError, tomllib.TOMLDecodeError):
        raise ProviderQualificationError(
            "The qualification dependency projection is unavailable or invalid."
        ) from None


def _dependency_projection(root: Path) -> ProviderQualificationDependencyProjection:
    try:
        for relative_path in ("pyproject.toml", "uv.lock"):
            if _has_symlink_component(root, relative_path):
                raise OSError
        pyproject_bytes, _pyproject_size, _pyproject_sha256 = _read_regular_file(
            root / "pyproject.toml",
            maximum_bytes=1024 * 1024,
        )
        uv_lock_bytes, _uv_size, _uv_sha256 = _read_regular_file(
            root / "uv.lock",
            maximum_bytes=4 * 1024 * 1024,
        )
        return build_provider_qualification_dependency_projection(
            pyproject_bytes,
            uv_lock_bytes,
        )
    except (OSError, ProviderQualificationError):
        raise ProviderQualificationError(
            "The qualification dependency files are unavailable or unsafe."
        ) from None


def build_provider_qualification_client_runtime_manifest(
    project_root: Path | None = None,
) -> ProviderQualificationClientRuntimeManifest:
    """Inventory exact imported client code, lock files, Python, and critical packages."""

    try:
        root = _validated_project_root(
            _current_project_root() if project_root is None else project_root
        )
        _assert_import_origins(root)
        source_files = tuple(
            _project_file_identity(root, relative_path)
            for relative_path in _CLIENT_RUNTIME_SOURCE_PATHS
        )
        dependency_projection = _dependency_projection(root)
        python_identity = _client_python_identity(root)
        distributions = tuple(
            _distribution_identity(root, distribution_name)
            for distribution_name in _CLIENT_RUNTIME_DISTRIBUTIONS
        )
        payload: dict[str, object] = {
            "manifest_schema_version": PROVIDER_QUALIFICATION_CLIENT_RUNTIME_VERSION,
            "source_files": source_files,
            "source_manifest_sha256": canonical_model_sha256(source_files),
            "dependency_projection": dependency_projection,
            "python": python_identity,
            "distributions": distributions,
            "manifest_sha256": "0" * 64,
        }
        payload["manifest_sha256"] = canonical_self_sha256(payload, "manifest_sha256")
        return ProviderQualificationClientRuntimeManifest.model_validate(payload)
    except ProviderQualificationError:
        raise
    except (OSError, RuntimeError, ValidationError, ValueError):
        raise ProviderQualificationError(
            "The qualification client runtime manifest could not be built safely."
        ) from None


def verify_provider_qualification_client_runtime_manifest(
    manifest: ProviderQualificationClientRuntimeManifest,
    *,
    project_root: Path | None = None,
) -> ProviderQualificationClientRuntimeManifest:
    """Rebuild the client runtime before provider start and reject every drift."""

    try:
        trusted = ProviderQualificationClientRuntimeManifest.model_validate_json(
            manifest.model_dump_json()
        )
        expected = build_provider_qualification_client_runtime_manifest(project_root)
        if trusted != expected:
            raise ValueError
        return trusted
    except ProviderQualificationError:
        raise
    except (ValidationError, ValueError):
        raise ProviderQualificationError(
            "The qualification client runtime does not replay against current bytes."
        ) from None


def build_provider_qualification_candidate(
    *,
    model_policy: LocalModelPolicyManifest,
    prompt_policy: PromptPolicyManifest,
    environment_manifest: ProviderEnvironmentManifestBinding,
    model_policy_file: QualificationFileIdentity,
    prompt_policy_file: QualificationFileIdentity,
    environment_manifest_file: QualificationFileIdentity,
) -> ProviderQualificationCandidate:
    """Bind the exact current policy objects and their physical manifest bytes."""

    try:
        model = LocalModelPolicyManifest.model_validate_json(model_policy.model_dump_json())
        prompt = PromptPolicyManifest.model_validate_json(prompt_policy.model_dump_json())
        environment = ProviderEnvironmentManifestBinding.model_validate_json(
            environment_manifest.model_dump_json()
        )
        identity = model.provider_identity(prompt)
        if (
            model.manifest_sha256 != CURRENT_MODEL_POLICY_SHA256
            or prompt.manifest_sha256 != CURRENT_PROMPT_POLICY_SHA256
            or environment.manifest_sha256 != CURRENT_PROVIDER_ENVIRONMENT_MANIFEST_SHA256
            or environment.provider_environment_sha256 != CURRENT_PROVIDER_ENVIRONMENT_SHA256
            or model.provider_environment_manifest_sha256 != environment.manifest_sha256
            or model.provider_environment_sha256 != environment.provider_environment_sha256
            or model.provider_environment_distribution_count
            != environment.provider_environment_distribution_count
            or model.provider_environment_file_count != environment.provider_environment_file_count
            or model.timeout_seconds != 300
            or model_policy_file.relative_path != _MODEL_POLICY_PATH
            or prompt_policy_file.relative_path != _PROMPT_POLICY_PATH
            or environment_manifest_file.relative_path != _ENVIRONMENT_MANIFEST_PATH
        ):
            raise ValueError
        return ProviderQualificationCandidate(
            provider_key=identity.provider_key,
            model_key=identity.model_key,
            model_revision=identity.model_revision,
            generation_policy_key=identity.generation_policy_key,
            prompt_policy_key=identity.prompt_policy_key,
            model_policy_manifest_sha256=model.manifest_sha256,
            prompt_policy_manifest_sha256=prompt.manifest_sha256,
            provider_environment_manifest_sha256=environment.manifest_sha256,
            provider_environment_sha256=environment.provider_environment_sha256,
            provider_environment_distribution_count=(
                environment.provider_environment_distribution_count
            ),
            provider_environment_file_count=environment.provider_environment_file_count,
            runtime_launcher_sha256=model.runtime_launcher_sha256,
            runtime_proxy_sha256=model.runtime_proxy_sha256,
            inference_engine_wrapper_sha256=model.inference_engine_wrapper_sha256,
            egress_profile_sha256=model.egress_profile_sha256,
            sandbox_executable_sha256=model.sandbox_executable_sha256,
            environment_executable_sha256=model.environment_executable_sha256,
            network_policy_key=model.network_policy_key,
            environment_policy_key=model.environment_policy_key,
            inner_authentication_key=model.inner_authentication_key,
            runtime_attestation_path=model.runtime_attestation_path,
            network_attestation_key=model.egress_probe_key,
            outer_port=model.outer_port,
            inner_port=model.inner_port,
            timeout_seconds=300,
            retry_count=model.retry_count,
            model_policy_file=model_policy_file,
            prompt_policy_file=prompt_policy_file,
            provider_environment_manifest_file=environment_manifest_file,
        )
    except Exception:
        raise ProviderQualificationError(
            "The qualification candidate does not match the exact current V0 policy."
        ) from None


def build_provider_qualification_definition(
    *,
    candidate: ProviderQualificationCandidate,
    runner_file: QualificationFileIdentity,
    qualification_module_file: QualificationFileIdentity,
    client_runtime_manifest: ProviderQualificationClientRuntimeManifest | None = None,
) -> ProviderQualificationDefinition:
    """Create the self-hashed pre-registration without starting a provider."""

    try:
        trusted_candidate = ProviderQualificationCandidate.model_validate_json(
            candidate.model_dump_json()
        )
        trusted_runner = QualificationFileIdentity.model_validate_json(
            runner_file.model_dump_json()
        )
        trusted_module = QualificationFileIdentity.model_validate_json(
            qualification_module_file.model_dump_json()
        )
        trusted_runtime = (
            build_provider_qualification_client_runtime_manifest()
            if client_runtime_manifest is None
            else ProviderQualificationClientRuntimeManifest.model_validate_json(
                client_runtime_manifest.model_dump_json()
            )
        )
        context = build_fixed_qualification_context()
        context_bytes = canonical_context_json(context).encode("utf-8")
        payload: dict[str, object] = {
            "definition_schema_version": PROVIDER_QUALIFICATION_DEFINITION_VERSION,
            "qualification_key": "qualification:v0:fixed-offline-provider-v1",
            "target_operating_system": "Darwin",
            "target_architecture": "arm64",
            "execution_mode": "fixed-offline-single-candidate",
            "selection_rule": "only_passing_candidate",
            "candidate_set": (trusted_candidate,),
            "runner_file": trusted_runner,
            "qualification_module_file": trusted_module,
            "client_runtime_manifest": trusted_runtime,
            "synthetic_case_key": PROVIDER_QUALIFICATION_CASE_KEY,
            "synthetic_context": context,
            "synthetic_context_self_sha256": context.context_sha256,
            "synthetic_context_canonical_sha256": hashlib.sha256(context_bytes).hexdigest(),
            "synthetic_context_byte_size": len(context_bytes),
            "pass_rule": ProviderQualificationPassRule(),
            "definition_sha256": "0" * 64,
        }
        payload["definition_sha256"] = canonical_self_sha256(payload, "definition_sha256")
        return ProviderQualificationDefinition.model_validate(payload)
    except ProviderQualificationError:
        raise
    except Exception:
        raise ProviderQualificationError(
            "The provider qualification definition could not be built safely."
        ) from None


def verify_provider_qualification_definition(
    definition: ProviderQualificationDefinition,
    *,
    model_policy: LocalModelPolicyManifest,
    prompt_policy: PromptPolicyManifest,
    environment_manifest: ProviderEnvironmentManifestBinding,
    model_policy_file: QualificationFileIdentity,
    prompt_policy_file: QualificationFileIdentity,
    environment_manifest_file: QualificationFileIdentity,
    runner_file: QualificationFileIdentity,
    qualification_module_file: QualificationFileIdentity,
    project_root: Path | None = None,
) -> ProviderQualificationDefinition:
    """Replay all definition bindings against the exact physical policy inputs."""

    try:
        trusted = ProviderQualificationDefinition.model_validate_json(definition.model_dump_json())
        expected_candidate = build_provider_qualification_candidate(
            model_policy=model_policy,
            prompt_policy=prompt_policy,
            environment_manifest=environment_manifest,
            model_policy_file=model_policy_file,
            prompt_policy_file=prompt_policy_file,
            environment_manifest_file=environment_manifest_file,
        )
        trusted_runner = QualificationFileIdentity.model_validate_json(
            runner_file.model_dump_json()
        )
        trusted_module = QualificationFileIdentity.model_validate_json(
            qualification_module_file.model_dump_json()
        )
        trusted_runtime = verify_provider_qualification_client_runtime_manifest(
            trusted.client_runtime_manifest,
            project_root=project_root,
        )
        if (
            trusted.candidate_set != (expected_candidate,)
            or trusted.runner_file != trusted_runner
            or trusted.qualification_module_file != trusted_module
            or trusted.client_runtime_manifest != trusted_runtime
        ):
            raise ValueError
        return trusted
    except ProviderQualificationError:
        raise
    except Exception:
        raise ProviderQualificationError(
            "The provider qualification definition does not replay against current inputs."
        ) from None


def build_provider_qualification_report(
    definition: ProviderQualificationDefinition,
    *,
    definition_file: QualificationFileIdentity,
    observation: ProviderQualificationObservation,
) -> ProviderQualificationReport:
    """Build a passing report or reject the complete observation without partial evidence."""

    try:
        trusted_definition = ProviderQualificationDefinition.model_validate_json(
            definition.model_dump_json()
        )
        trusted_file = QualificationFileIdentity.model_validate_json(
            definition_file.model_dump_json()
        )
        trusted_observation = ProviderQualificationObservation.model_validate_json(
            observation.model_dump_json()
        )
        definition_bytes = canonical_model_json(trusted_definition).encode("utf-8") + b"\n"
        if (
            trusted_file.byte_size != len(definition_bytes)
            or trusted_file.sha256 != hashlib.sha256(definition_bytes).hexdigest()
        ):
            raise ValueError
        _require_passing_observation(trusted_observation, trusted_definition.pass_rule)
        payload: dict[str, object] = {
            "report_schema_version": PROVIDER_QUALIFICATION_REPORT_VERSION,
            "qualification_key": trusted_definition.qualification_key,
            "definition_sha256": trusted_definition.definition_sha256,
            "definition_file": trusted_file,
            "candidate": trusted_definition.candidate_set[0],
            "candidate_status": "passed",
            "selected_candidate_key": trusted_definition.candidate_set[0].candidate_key,
            "selection": "only_passing_candidate",
            "target_operating_system": trusted_definition.target_operating_system,
            "target_architecture": trusted_definition.target_architecture,
            "runner_file": trusted_definition.runner_file,
            "qualification_module_file": trusted_definition.qualification_module_file,
            "client_runtime_manifest": trusted_definition.client_runtime_manifest,
            "network_attestation_key": (
                trusted_definition.candidate_set[0].network_attestation_key
            ),
            "pass_rule": trusted_definition.pass_rule,
            "observation": trusted_observation,
            "report_sha256": "0" * 64,
        }
        payload["report_sha256"] = canonical_self_sha256(payload, "report_sha256")
        return ProviderQualificationReport.model_validate(payload)
    except ProviderQualificationError:
        raise
    except Exception:
        raise ProviderQualificationError(
            "The provider qualification observation did not pass every frozen rule."
        ) from None


def verify_provider_qualification_report(
    report: ProviderQualificationReport,
    *,
    definition: ProviderQualificationDefinition,
    definition_file: QualificationFileIdentity,
) -> ProviderQualificationReport:
    """Verify a report is the unique passing projection of one exact definition."""

    try:
        trusted_report = ProviderQualificationReport.model_validate_json(report.model_dump_json())
        expected = build_provider_qualification_report(
            definition,
            definition_file=definition_file,
            observation=trusted_report.observation,
        )
        if trusted_report != expected:
            raise ValueError
        return trusted_report
    except ProviderQualificationError:
        raise
    except Exception:
        raise ProviderQualificationError(
            "The provider qualification report does not replay against its definition."
        ) from None


def composition_observation_fields(
    composition: GenerationComposition,
) -> tuple[str, int, int, Literal["mechanical"]]:
    """Return the exact mechanical composition identity and minimum-count fields."""

    trusted = GenerationComposition.model_validate_json(composition.model_dump_json())
    return (
        canonical_model_sha256(trusted),
        len(trusted.claims),
        len(trusted.citations),
        trusted.validation_scope,
    )


def qualification_file_identity(
    path: Path,
    *,
    relative_path: str,
    maximum_bytes: int | None = None,
) -> QualificationFileIdentity:
    """Hash one stable no-symlink regular file through a single descriptor."""

    try:
        raw, byte_size, sha256 = _read_regular_file(path, maximum_bytes=maximum_bytes)
        del raw
        return QualificationFileIdentity(
            relative_path=relative_path,
            byte_size=byte_size,
            sha256=sha256,
        )
    except Exception:
        raise ProviderQualificationError(
            "A qualification input file is unavailable or changed while read."
        ) from None


def write_provider_qualification_definition(
    path: Path, definition: ProviderQualificationDefinition
) -> QualificationFileIdentity:
    trusted = ProviderQualificationDefinition.model_validate_json(definition.model_dump_json())
    return _write_artifact(path, trusted, relative_path=path.name)


def load_provider_qualification_definition(
    path: Path,
    *,
    approved_definition_sha256: str,
    approved_file_sha256: str,
) -> ProviderQualificationDefinition:
    definition = _load_artifact(path, ProviderQualificationDefinition, _MAX_DEFINITION_BYTES)
    if (
        definition.definition_sha256 != approved_definition_sha256
        or qualification_file_identity(
            path, relative_path=path.name, maximum_bytes=_MAX_DEFINITION_BYTES
        ).sha256
        != approved_file_sha256
    ):
        raise ProviderQualificationError(
            "The provider qualification definition does not match both approved checksums."
        )
    return definition


def write_provider_qualification_report(
    path: Path, report: ProviderQualificationReport
) -> QualificationFileIdentity:
    trusted = ProviderQualificationReport.model_validate_json(report.model_dump_json())
    return _write_artifact(path, trusted, relative_path=path.name)


def load_provider_qualification_report(
    path: Path,
    *,
    approved_report_sha256: str,
    approved_file_sha256: str,
) -> ProviderQualificationReport:
    report = _load_artifact(path, ProviderQualificationReport, _MAX_REPORT_BYTES)
    if (
        report.report_sha256 != approved_report_sha256
        or qualification_file_identity(
            path, relative_path=path.name, maximum_bytes=_MAX_REPORT_BYTES
        ).sha256
        != approved_file_sha256
    ):
        raise ProviderQualificationError(
            "The provider qualification report does not match both approved checksums."
        )
    return report


def _require_passing_observation(
    observation: ProviderQualificationObservation,
    rule: ProviderQualificationPassRule,
) -> None:
    context = build_fixed_qualification_context()
    context_bytes = canonical_context_json(context).encode("utf-8")
    if (
        observation.host_operating_system != "Darwin"
        or observation.host_architecture != "arm64"
        or observation.startup_duration_ns > rule.startup_deadline_ns
        or observation.generation_duration_ns > rule.generation_deadline_ns
        or not observation.outer_ready
        or observation.inner_unauthenticated_status != rule.required_inner_unauthenticated_status
        or observation.hmac_runtime_attestation_verified != rule.require_hmac_runtime_attestation
        or observation.provider_check_ready != rule.require_provider_check_ready
        or observation.generation_request_count != rule.generation_request_count
        or observation.retry_count != rule.retry_count
        or observation.context_self_sha256 != context.context_sha256
        or observation.context_canonical_sha256 != hashlib.sha256(context_bytes).hexdigest()
        or observation.claim_count < rule.minimum_claim_count
        or observation.citation_count < rule.minimum_citation_count
        or observation.validation_scope != rule.validation_scope
        or observation.clean_shutdown != rule.require_clean_shutdown
        or observation.process_exit_code != rule.required_process_exit_code
        or not observation.outer_port_closed_after_shutdown
        or not observation.inner_port_closed_after_shutdown
    ):
        raise ValueError("qualification observation failed a frozen pass rule")


def _load_artifact[SchemaT: StrictFrozenSchema](
    path: Path,
    schema: type[SchemaT],
    maximum_bytes: int,
) -> SchemaT:
    try:
        raw, _byte_size, _sha256 = _read_regular_file(path, maximum_bytes=maximum_bytes)
        value = schema.model_validate_json(raw)
        if raw != canonical_model_json(value).encode("utf-8") + b"\n":
            raise ValueError
        return value
    except (OSError, UnicodeError, ValidationError, ValueError):
        raise ProviderQualificationError(
            "A provider qualification artifact is unavailable or invalid."
        ) from None


def _write_artifact(
    path: Path,
    value: StrictFrozenSchema,
    *,
    relative_path: str,
) -> QualificationFileIdentity:
    payload = canonical_model_json(value).encode("utf-8") + b"\n"
    created = False
    try:
        parent = path.parent.lstat()
        if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
            raise OSError
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o644)
        created = True
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise OSError
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
                final = os.fstat(stream.fileno())
            if _stat_fingerprint(opened)[:3] != _stat_fingerprint(final)[:3]:
                raise OSError
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        return QualificationFileIdentity(
            relative_path=relative_path,
            byte_size=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
    except Exception:
        try:
            if created:
                path.unlink(missing_ok=True)
        finally:
            raise ProviderQualificationError(
                "A qualification artifact must be written to one new regular file."
            ) from None


def _read_regular_file(
    path: Path,
    *,
    maximum_bytes: int | None,
    allow_empty: bool = False,
) -> tuple[bytes, int, str]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        os.close(descriptor)
        raise OSError
    payload = bytearray()
    digest = hashlib.sha256()
    with os.fdopen(descriptor, "rb") as stream:
        while chunk := stream.read(_HASH_CHUNK_BYTES):
            payload.extend(chunk)
            digest.update(chunk)
            if maximum_bytes is not None and len(payload) > maximum_bytes:
                raise OSError
        after = os.fstat(stream.fileno())
    if _stat_fingerprint(before) != _stat_fingerprint(after) or (not payload and not allow_empty):
        raise OSError
    return bytes(payload), len(payload), digest.hexdigest()


def _stat_fingerprint(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


__all__ = [
    "CURRENT_MODEL_POLICY_SHA256",
    "CURRENT_PROMPT_POLICY_SHA256",
    "CURRENT_PROVIDER_ENVIRONMENT_MANIFEST_SHA256",
    "FIXED_QUALIFICATION_EVIDENCE",
    "FIXED_QUALIFICATION_QUESTION",
    "GenerationCallRecord",
    "ProviderEnvironmentManifestBinding",
    "ProviderQualificationCandidate",
    "ProviderQualificationClientDistributionIdentity",
    "ProviderQualificationClientPythonIdentity",
    "ProviderQualificationClientRuntimeManifest",
    "ProviderQualificationDependencyProjection",
    "ProviderQualificationDefinition",
    "ProviderQualificationError",
    "ProviderQualificationObservation",
    "ProviderQualificationPassRule",
    "ProviderQualificationReport",
    "QualificationFileIdentity",
    "RecordingGenerationProvider",
    "build_fixed_qualification_context",
    "build_provider_qualification_dependency_projection",
    "build_provider_qualification_client_runtime_manifest",
    "build_provider_qualification_candidate",
    "build_provider_qualification_definition",
    "build_provider_qualification_report",
    "composition_observation_fields",
    "load_provider_qualification_definition",
    "load_provider_qualification_report",
    "parse_provider_environment_manifest",
    "qualification_file_identity",
    "verify_provider_qualification_definition",
    "verify_provider_qualification_client_runtime_manifest",
    "verify_provider_qualification_report",
    "write_provider_qualification_definition",
    "write_provider_qualification_report",
]
