"""Checksum-bound local generation model and prompt policy manifests."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from eve_relation_rag.generation.context import (
    ANSWER_INSTRUCTION_POLICY_KEY,
    ANSWER_INSTRUCTION_TEXT,
    ANSWER_INSTRUCTION_TEXT_SHA256,
    ANSWER_INSTRUCTIONS_CANONICAL_SHA256,
)
from eve_relation_rag.hybrid.contracts import (
    CONTEXT_PACK_VERSION,
    GENERATED_DRAFT_VERSION,
    MAX_GENERATED_OUTPUT_BYTES,
    ContextPack,
    GeneratedAnswerDraft,
    ProviderIdentity,
    StrictFrozenSchema,
    canonical_model_sha256,
    canonical_self_sha256,
)
from eve_relation_rag.literature.contracts import NonEmptyText, RelativePath, Sha256, StableToken

PROMPT_POLICY_MANIFEST_VERSION: Literal["v0-prompt-policy-manifest-v1"] = (
    "v0-prompt-policy-manifest-v1"
)
MODEL_POLICY_MANIFEST_VERSION: Literal["v0-local-model-policy-manifest-v3"] = (
    "v0-local-model-policy-manifest-v3"
)
LOOPBACK_TRANSPORT_POLICY_KEY: Literal["transport:loopback-openai-compatible-http-v1"] = (
    "transport:loopback-openai-compatible-http-v1"
)
MAX_POLICY_MANIFEST_BYTES = 1_048_576
_HASH_CHUNK_BYTES = 1024 * 1024

PROVIDER_REQUEST_TEMPLATE_KEY = "prompt-template:endoviho-rag:v0:generated-draft-v1"
PROVIDER_REQUEST_TEMPLATE_SHA256 = (
    "d66794a9eaf88b1e2cc7b32ba37097e7687c1464fced687782144def9dadf2fb"
)
PROVIDER_REQUEST_TEMPLATE_TEXT = (
    "Output contract; this block contains no factual evidence.\n"
    "Return only one compact JSON object, with no prose or Markdown, using exactly these "
    "top-level keys:\n"
    "draft_schema_version, context_sha256, claims, selected_limitation_codes.\n"
    "Set draft_schema_version to generated-answer-draft-v1 and copy context_sha256 exactly "
    "once from the ContextPack.\n"
    "For supported evidence, claims is an array of objects with exactly claim_id, claim_text, "
    "citation_ids, evidence_spans. Use contiguous claim_id values C1..Cn. Each claim_text is "
    "one complete printable-ASCII English sentence without citation markers. citation_ids has "
    '1..4 quoted JSON strings matching D1, D2, and so on; for example ["D1"]. Order them by '
    "integer suffix and never emit numeric JSON values. evidence_spans has exactly one object "
    "per citation, in the same order, with exactly citation_id and quote. Each citation_id is "
    'the same quoted D string, for example "citation_id":"D1"; quote is an exact substring '
    "of that cited chunk.\n"
    "When claims is nonempty, selected_limitation_codes is exactly "
    '["literature_evidence_is_explanatory",'
    '"mechanical_validation_is_not_semantic_entailment"].\n'
    "When evidence is insufficient, claims is [] and selected_limitation_codes is exactly "
    '["insufficient_literature_evidence","literature_evidence_is_explanatory",'
    '"mechanical_validation_is_not_semantic_entailment"].\n'
    "Never emit insufficient_literature_evidence when claims is nonempty."
)

if hashlib.sha256(PROVIDER_REQUEST_TEMPLATE_TEXT.encode("utf-8")).hexdigest() != (
    PROVIDER_REQUEST_TEMPLATE_SHA256
):  # pragma: no cover - import-time invariant guarding source drift.
    raise RuntimeError("approved provider request template does not match its pinned SHA-256")


def _validate_immutable_https_uri(value: str) -> str:
    if value != value.strip():
        raise ValueError("source URI must not contain surrounding whitespace")
    try:
        parts = urlsplit(value)
        port = parts.port
    except (TypeError, ValueError):
        raise ValueError("source URI must be one credential-free HTTPS URL") from None
    path_parts = tuple(part.casefold() for part in parts.path.split("/") if part)
    if (
        parts.scheme != "https"
        or parts.hostname is None
        or parts.username is not None
        or parts.password is not None
        or not parts.path.strip("/")
        or parts.query not in {"", "download=true"}
        or parts.fragment
        or (port is not None and port != 443)
        or any(
            path_parts[index : index + 2]
            in {("tree", "main"), ("blob", "main"), ("resolve", "main")}
            for index in range(max(0, len(path_parts) - 1))
        )
    ):
        raise ValueError("source URI must be one immutable credential-free HTTPS URL")
    return value


type ImmutableHttpsUri = Annotated[
    str,
    Field(min_length=9, max_length=2048),
    AfterValidator(_validate_immutable_https_uri),
]
type GitCommitSha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
_RELATIVE_PATH_ADAPTER: TypeAdapter[RelativePath] = TypeAdapter(RelativePath)


class GenerationPolicyError(RuntimeError):
    """Sanitized failure loading an independently approved local policy."""


class ModelArtifactSpec(StrictFrozenSchema):
    """One exact file required by the approved local model package."""

    relative_path: RelativePath
    byte_size: int = Field(gt=0)
    sha256: Sha256


class PromptPolicyManifest(StrictFrozenSchema):
    """Exact prompt and generated-contract identity admitted to V0 generation."""

    manifest_schema_version: Literal["v0-prompt-policy-manifest-v1"] = (
        PROMPT_POLICY_MANIFEST_VERSION
    )
    prompt_policy_key: StableToken
    source_text: str = Field(min_length=1, max_length=8192)
    source_text_sha256: Sha256
    request_template_key: StableToken
    request_template_text: str = Field(min_length=1, max_length=8192)
    request_template_sha256: Sha256
    answer_instructions_sha256: Sha256
    context_schema_version: Literal["context-pack-v1"] = CONTEXT_PACK_VERSION
    context_schema_sha256: Sha256
    generated_draft_schema_version: Literal["generated-answer-draft-v1"] = GENERATED_DRAFT_VERSION
    generated_draft_schema_sha256: Sha256
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        if hashlib.sha256(self.source_text.encode("utf-8")).hexdigest() != (
            self.source_text_sha256
        ):
            raise ValueError("prompt source text checksum does not match")
        if hashlib.sha256(self.request_template_text.encode("utf-8")).hexdigest() != (
            self.request_template_sha256
        ):
            raise ValueError("provider request template checksum does not match")
        if self.manifest_sha256 != canonical_self_sha256(self, "manifest_sha256"):
            raise ValueError("prompt policy manifest checksum does not match")
        return self

    def require_approved_v0_policy(self) -> None:
        """Reject a self-consistent prompt that is not the approved V0 prompt."""

        expected = {
            "prompt_policy_key": ANSWER_INSTRUCTION_POLICY_KEY,
            "source_text": ANSWER_INSTRUCTION_TEXT,
            "source_text_sha256": ANSWER_INSTRUCTION_TEXT_SHA256,
            "request_template_key": PROVIDER_REQUEST_TEMPLATE_KEY,
            "request_template_text": PROVIDER_REQUEST_TEMPLATE_TEXT,
            "request_template_sha256": PROVIDER_REQUEST_TEMPLATE_SHA256,
            "answer_instructions_sha256": ANSWER_INSTRUCTIONS_CANONICAL_SHA256,
            "context_schema_sha256": canonical_model_sha256(ContextPack.model_json_schema()),
            "generated_draft_schema_sha256": canonical_model_sha256(
                GeneratedAnswerDraft.model_json_schema()
            ),
        }
        observed = self.model_dump(mode="python")
        if any(observed[field] != value for field, value in expected.items()):
            raise GenerationPolicyError("The configured prompt policy is not approved for V0.")


class LocalModelPolicyManifest(StrictFrozenSchema):
    """Complete model, runtime, tokenizer, and generation identity for local V0."""

    manifest_schema_version: Literal["v0-local-model-policy-manifest-v3"] = (
        MODEL_POLICY_MANIFEST_VERSION
    )
    provider_key: StableToken
    model_key: StableToken
    api_model_name: StableToken
    model_revision: StableToken
    repository_uri: ImmutableHttpsUri
    repository_revision: GitCommitSha
    base_model_repository_uri: ImmutableHttpsUri
    base_model_key: StableToken
    base_model_revision: GitCommitSha
    artifacts: tuple[ModelArtifactSpec, ...] = Field(min_length=1)
    license_key: NonEmptyText
    license_artifact_relative_path: RelativePath
    license_artifact_sha256: Sha256
    license_source_uri: ImmutableHttpsUri
    inference_engine_key: StableToken
    inference_engine_version: StableToken
    inference_engine_lock_sha256: Sha256
    inference_engine_wrapper_sha256: Sha256
    inference_engine_module_sha256: Sha256
    inference_python_executable_sha256: Sha256
    inference_python_configuration_sha256: Sha256
    provider_environment_verifier_sha256: Sha256
    provider_environment_manifest_sha256: Sha256
    provider_environment_sha256: Sha256
    provider_environment_distribution_count: int = Field(gt=0)
    provider_environment_file_count: int = Field(gt=0)
    runtime_launcher_sha256: Sha256
    runtime_proxy_sha256: Sha256
    egress_profile_sha256: Sha256
    sandbox_executable_sha256: Sha256
    environment_executable_sha256: Sha256
    runtime_distributions: dict[str, str] = Field(min_length=1)
    network_policy_key: Literal["network:macos-sandbox-v0-ports-only-v2"] = (
        "network:macos-sandbox-v0-ports-only-v2"
    )
    environment_policy_key: Literal["environment:scrubbed-allowlist-v1"] = (
        "environment:scrubbed-allowlist-v1"
    )
    inner_authentication_key: Literal["authentication:inherited-fd-bearer-v1"] = (
        "authentication:inherited-fd-bearer-v1"
    )
    authentication_required: Literal[True] = True
    runtime_attestation_path: Literal["/v0/runtime-attestation"] = (
        "/v0/runtime-attestation"
    )
    egress_probe_key: Literal["egress-probe:external-and-unapproved-loopback-denied-v2"] = (
        "egress-probe:external-and-unapproved-loopback-denied-v2"
    )
    startup_warmup_key: Literal["warmup:nonfactual-one-token-v1"] = (
        "warmup:nonfactual-one-token-v1"
    )
    startup_warmup_max_tokens: Literal[1] = 1
    outer_port: Literal[8123] = 8123
    inner_port: Literal[8124] = 8124
    quantization: StableToken
    tokenizer_key: StableToken
    tokenizer_revision: StableToken
    context_length_tokens: int = Field(ge=4096)
    seed_supported: bool
    seed: int | None = Field(default=None, ge=0, le=2_147_483_647)
    generation_policy_key: StableToken
    prompt_policy_manifest_sha256: Sha256
    endpoint_policy_key: Literal["transport:loopback-openai-compatible-http-v1"] = (
        LOOPBACK_TRANSPORT_POLICY_KEY
    )
    chat_completions_path: Literal["/v1/chat/completions"] = "/v1/chat/completions"
    readiness_path: Literal["/v1/models"] = "/v1/models"
    response_format: Literal["json_object"] = "json_object"
    temperature: Literal[0] = 0
    top_p: Literal[1] = 1
    top_k: Literal[0] = 0
    min_p: Literal[0] = 0
    max_output_tokens: int = Field(ge=1, le=8192)
    max_output_bytes: Literal[32768] = MAX_GENERATED_OUTPUT_BYTES
    timeout_seconds: int = Field(ge=1, le=300)
    retry_count: Literal[0] = 0
    max_concurrent_requests: Literal[1] = 1
    prompt_concurrency: Literal[1] = 1
    decode_concurrency: Literal[1] = 1
    manifest_sha256: Sha256

    @field_validator("artifacts")
    @classmethod
    def validate_artifact_order(
        cls, artifacts: tuple[ModelArtifactSpec, ...]
    ) -> tuple[ModelArtifactSpec, ...]:
        paths = tuple(artifact.relative_path for artifact in artifacts)
        if paths != tuple(sorted(paths)):
            raise ValueError("model artifacts must use canonical path order")
        if len(paths) != len(set(paths)):
            raise ValueError("model artifacts must not contain duplicate paths")
        return artifacts

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        if self.seed_supported != (self.seed is not None):
            raise ValueError("seed must be present exactly when seed support is enabled")
        if self.model_revision != self.repository_revision:
            raise ValueError("model_revision must equal the exact repository revision")
        artifact_by_path = {artifact.relative_path: artifact for artifact in self.artifacts}
        license_artifact = artifact_by_path.get(self.license_artifact_relative_path)
        if license_artifact is None or license_artifact.sha256 != self.license_artifact_sha256:
            raise ValueError("license artifact identity must match one model artifact")
        if self.base_model_revision not in self.license_source_uri:
            raise ValueError("license source URI must contain the exact base-model revision")
        expected_distributions = {
            "mlx": "0.32.2",
            "mlx-lm": "0.31.3",
            "mlx-metal": "0.32.2",
        }
        if self.runtime_distributions != expected_distributions:
            raise ValueError("runtime distribution versions do not match V0")
        if self.manifest_sha256 != canonical_self_sha256(self, "manifest_sha256"):
            raise ValueError("local model policy manifest checksum does not match")
        return self

    def provider_identity(self, prompt: PromptPolicyManifest) -> ProviderIdentity:
        """Return the identity checked before every generation call."""

        if prompt.manifest_sha256 != self.prompt_policy_manifest_sha256:
            raise GenerationPolicyError(
                "The configured model and prompt policy manifests do not match."
            )
        prompt.require_approved_v0_policy()
        return ProviderIdentity(
            provider_key=self.provider_key,
            model_key=self.model_key,
            model_revision=self.model_revision,
            provider_artifact_sha256=self.manifest_sha256,
            generation_policy_key=self.generation_policy_key,
            prompt_policy_key=prompt.prompt_policy_key,
            prompt_policy_sha256=prompt.source_text_sha256,
            temperature=0,
            max_output_bytes=MAX_GENERATED_OUTPUT_BYTES,
            timeout_seconds=self.timeout_seconds,
            retry_count=0,
        )


def build_approved_prompt_policy_manifest() -> PromptPolicyManifest:
    """Build the canonical manifest for the source-pinned V0 answer policy."""

    payload: dict[str, object] = {
        "manifest_schema_version": PROMPT_POLICY_MANIFEST_VERSION,
        "prompt_policy_key": ANSWER_INSTRUCTION_POLICY_KEY,
        "source_text": ANSWER_INSTRUCTION_TEXT,
        "source_text_sha256": ANSWER_INSTRUCTION_TEXT_SHA256,
        "request_template_key": PROVIDER_REQUEST_TEMPLATE_KEY,
        "request_template_text": PROVIDER_REQUEST_TEMPLATE_TEXT,
        "request_template_sha256": PROVIDER_REQUEST_TEMPLATE_SHA256,
        "answer_instructions_sha256": ANSWER_INSTRUCTIONS_CANONICAL_SHA256,
        "context_schema_version": CONTEXT_PACK_VERSION,
        "context_schema_sha256": canonical_model_sha256(ContextPack.model_json_schema()),
        "generated_draft_schema_version": GENERATED_DRAFT_VERSION,
        "generated_draft_schema_sha256": canonical_model_sha256(
            GeneratedAnswerDraft.model_json_schema()
        ),
        "manifest_sha256": "0" * 64,
    }
    payload["manifest_sha256"] = canonical_self_sha256(payload, "manifest_sha256")
    return PromptPolicyManifest.model_validate(payload)


def inventory_model_artifacts(
    artifact_root: Path,
    *,
    relative_paths: tuple[str, ...],
) -> tuple[ModelArtifactSpec, ...]:
    """Hash an exact caller-approved inventory and reject unlisted model-root bytes."""

    try:
        if artifact_root.is_symlink() or not artifact_root.is_dir():
            raise OSError
        root = artifact_root.resolve(strict=True)
        validated_paths = tuple(
            _RELATIVE_PATH_ADAPTER.validate_python(path, strict=True) for path in relative_paths
        )
        if not validated_paths or len(validated_paths) != len(set(validated_paths)):
            raise ValueError
        if _discover_regular_files(artifact_root) != set(validated_paths):
            raise ValueError
        artifacts: list[ModelArtifactSpec] = []
        for relative_path in sorted(validated_paths):
            if _has_symlink_component(artifact_root, relative_path):
                raise OSError
            path = (artifact_root / relative_path).resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file():
                raise OSError
            byte_size, sha256 = _file_identity(path)
            artifacts.append(
                ModelArtifactSpec(
                    relative_path=relative_path,
                    byte_size=byte_size,
                    sha256=sha256,
                )
            )
        return tuple(artifacts)
    except (OSError, RuntimeError, ValidationError, ValueError):
        raise GenerationPolicyError(
            "The exact local model artifact inventory is unavailable or invalid."
        ) from None


def build_local_model_policy_manifest(**values: object) -> LocalModelPolicyManifest:
    """Build and self-checksum one strict model policy from explicit approved values."""

    if {"manifest_schema_version", "manifest_sha256"}.intersection(values):
        raise GenerationPolicyError("Model policy builder fields are ambiguous.")
    payload: dict[str, object] = {
        "manifest_schema_version": MODEL_POLICY_MANIFEST_VERSION,
        "seed": None,
        "endpoint_policy_key": LOOPBACK_TRANSPORT_POLICY_KEY,
        "chat_completions_path": "/v1/chat/completions",
        "readiness_path": "/v1/models",
        "response_format": "json_object",
        "temperature": 0,
        "top_p": 1,
        "top_k": 0,
        "min_p": 0,
        "network_policy_key": "network:macos-sandbox-v0-ports-only-v2",
        "environment_policy_key": "environment:scrubbed-allowlist-v1",
        "inner_authentication_key": "authentication:inherited-fd-bearer-v1",
        "authentication_required": True,
        "runtime_attestation_path": "/v0/runtime-attestation",
        "egress_probe_key": "egress-probe:external-and-unapproved-loopback-denied-v2",
        "startup_warmup_key": "warmup:nonfactual-one-token-v1",
        "startup_warmup_max_tokens": 1,
        "outer_port": 8123,
        "inner_port": 8124,
        "max_output_bytes": MAX_GENERATED_OUTPUT_BYTES,
        "retry_count": 0,
        "max_concurrent_requests": 1,
        "prompt_concurrency": 1,
        "decode_concurrency": 1,
        **values,
        "manifest_sha256": "0" * 64,
    }
    payload["manifest_sha256"] = canonical_self_sha256(payload, "manifest_sha256")
    try:
        return LocalModelPolicyManifest.model_validate(payload)
    except ValidationError:
        raise GenerationPolicyError("The local model policy values are invalid.") from None


def load_prompt_policy_manifest(
    path: Path, *, approved_manifest_sha256: str
) -> PromptPolicyManifest:
    """Load one small strict prompt manifest and require its independent checksum."""

    manifest = _load_manifest(path, PromptPolicyManifest)
    if manifest.manifest_sha256 != approved_manifest_sha256:
        raise GenerationPolicyError(
            "The prompt policy manifest does not match the approved checksum."
        )
    manifest.require_approved_v0_policy()
    return manifest


def load_local_model_policy_manifest(
    path: Path, *, approved_manifest_sha256: str
) -> LocalModelPolicyManifest:
    """Load one small strict local-model manifest and require its independent checksum."""

    manifest = _load_manifest(path, LocalModelPolicyManifest)
    if manifest.manifest_sha256 != approved_manifest_sha256:
        raise GenerationPolicyError(
            "The local model policy manifest does not match the approved checksum."
        )
    return manifest


def _load_manifest[ManifestT: StrictFrozenSchema](path: Path, schema: type[ManifestT]) -> ManifestT:
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError
        raw = _read_regular_file(path, max_bytes=MAX_POLICY_MANIFEST_BYTES)
        if not raw or len(raw) > MAX_POLICY_MANIFEST_BYTES:
            raise OSError
        return schema.model_validate_json(raw)
    except (OSError, UnicodeError, ValidationError):
        raise GenerationPolicyError(
            "The configured generation policy manifest is unavailable or invalid."
        ) from None


def _has_symlink_component(root: Path, relative_path: str) -> bool:
    current = root
    for part in Path(relative_path).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _file_identity(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    descriptor = _open_regular_file(path)
    before = os.fstat(descriptor)
    with os.fdopen(descriptor, "rb") as stream:
        while chunk := stream.read(_HASH_CHUNK_BYTES):
            size += len(chunk)
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    if _stat_identity(before) != _stat_identity(after):
        raise OSError
    if size < 1:
        raise ValueError
    return size, digest.hexdigest()


def _discover_regular_files(root: Path) -> set[str]:
    files: set[str] = set()
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        if any((directory_path / name).is_symlink() for name in directory_names):
            raise OSError
        for name in file_names:
            path = directory_path / name
            if path.is_symlink() or not path.is_file():
                raise OSError
            files.add(path.relative_to(root).as_posix())
    return files


def _read_regular_file(path: Path, *, max_bytes: int) -> bytes:
    descriptor = _open_regular_file(path)
    before = os.fstat(descriptor)
    payload = bytearray()
    with os.fdopen(descriptor, "rb") as stream:
        while chunk := stream.read(min(_HASH_CHUNK_BYTES, max_bytes + 1 - len(payload))):
            payload.extend(chunk)
            if len(payload) > max_bytes:
                raise OSError
        after = os.fstat(stream.fileno())
    if _stat_identity(before) != _stat_identity(after):
        raise OSError
    return bytes(payload)


def _open_regular_file(path: Path) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        os.close(descriptor)
        raise OSError
    return descriptor


def _stat_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


__all__ = [
    "LOOPBACK_TRANSPORT_POLICY_KEY",
    "MODEL_POLICY_MANIFEST_VERSION",
    "PROMPT_POLICY_MANIFEST_VERSION",
    "GenerationPolicyError",
    "LocalModelPolicyManifest",
    "ModelArtifactSpec",
    "PromptPolicyManifest",
    "build_approved_prompt_policy_manifest",
    "build_local_model_policy_manifest",
    "inventory_model_artifacts",
    "load_local_model_policy_manifest",
    "load_prompt_policy_manifest",
]
