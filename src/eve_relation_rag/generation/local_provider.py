"""Strict no-egress OpenAI-compatible provider for a loopback model server."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Literal, Self

import httpx
from pydantic import Field, SecretStr, ValidationError, model_validator

from eve_relation_rag.config.loopback import normalize_loopback_http_origin
from eve_relation_rag.generation.context import canonical_context_json, revalidate_context_pack
from eve_relation_rag.generation.policy import (
    GenerationPolicyError,
    LocalModelPolicyManifest,
    PromptPolicyManifest,
)
from eve_relation_rag.generation.providers import LLMProviderFailure, LLMProviderUnavailable
from eve_relation_rag.hybrid.contracts import (
    MAX_CONTEXT_BYTES,
    MAX_GENERATED_OUTPUT_BYTES,
    ContextPack,
    ProviderIdentity,
    StrictFrozenSchema,
    canonical_model_json,
)
from eve_relation_rag.literature.contracts import NonEmptyText, StableToken

_MAX_HTTP_RESPONSE_BYTES = 131_072
_HASH_CHUNK_BYTES = 1024 * 1024


class LocalProviderConfigurationError(LLMProviderUnavailable):
    """The approved local provider cannot be constructed safely."""


class LocalProviderRequestError(LLMProviderFailure):
    """One local model invocation failed without exposing payload or credentials."""


@dataclass(frozen=True, slots=True)
class LocalProviderConfig:
    """Server-owned loopback transport configuration."""

    base_url: str
    artifact_root: Path
    api_key: SecretStr | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", normalize_loopback_http_origin(self.base_url))


class _ChatMessage(StrictFrozenSchema):
    role: Literal["assistant"]
    content: NonEmptyText
    refusal: None = None


class _ChatChoice(StrictFrozenSchema):
    index: Literal[0]
    message: _ChatMessage
    finish_reason: Literal["stop"]
    logprobs: None = None


class _PromptTokenDetails(StrictFrozenSchema):
    cached_tokens: int = Field(ge=0)


class _TokenUsage(StrictFrozenSchema):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    prompt_tokens_details: _PromptTokenDetails | None = None

    @model_validator(mode="after")
    def validate_total(self) -> Self:
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("token usage total is inconsistent")
        return self


class _ChatCompletion(StrictFrozenSchema):
    id: NonEmptyText
    object: Literal["chat.completion"]
    created: int = Field(ge=0)
    model: StableToken
    choices: tuple[_ChatChoice, ...] = Field(min_length=1, max_length=1)
    usage: _TokenUsage | None = None
    system_fingerprint: StableToken | None = None


class _ModelRef(StrictFrozenSchema):
    id: StableToken
    object: Literal["model"]
    created: int = Field(ge=0)
    owned_by: StableToken | None = None


class _ModelsResponse(StrictFrozenSchema):
    object: Literal["list"]
    data: tuple[_ModelRef, ...] = Field(min_length=1, max_length=128)


class _RuntimeAttestation(StrictFrozenSchema):
    attestation_schema_version: Literal["v0-provider-runtime-attestation-v3"]
    challenge: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_policy_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_policy_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    inference_engine_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    inference_engine_wrapper_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    inference_engine_module_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    inference_python_executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    inference_python_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_environment_verifier_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_environment_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_environment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_environment_distribution_count: int = Field(gt=0)
    provider_environment_file_count: int = Field(gt=0)
    runtime_launcher_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_proxy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    egress_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sandbox_executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment_executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_distributions: dict[str, str] = Field(min_length=1)
    network_policy_key: Literal["network:macos-sandbox-v0-ports-only-v2"]
    environment_policy_key: Literal["environment:scrubbed-allowlist-v1"]
    inner_authentication_key: Literal["authentication:inherited-fd-bearer-v1"]
    egress_probe_key: Literal[
        "egress-probe:external-and-unapproved-loopback-denied-v2"
    ]
    startup_warmup_key: Literal["warmup:nonfactual-one-token-v1"]
    startup_warmup_max_tokens: Literal[1]
    outer_port: Literal[8123]
    inner_port: Literal[8124]
    attestation_hmac_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class LocalOpenAICompatibleProvider:
    """One-call local provider with an exact manifest and no external egress path."""

    def __init__(
        self,
        *,
        config: LocalProviderConfig,
        model_policy: LocalModelPolicyManifest,
        prompt_policy: PromptPolicyManifest,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        try:
            trusted_model = LocalModelPolicyManifest.model_validate_json(
                model_policy.model_dump_json()
            )
            trusted_prompt = PromptPolicyManifest.model_validate_json(
                prompt_policy.model_dump_json()
            )
            identity = trusted_model.provider_identity(trusted_prompt)
        except (GenerationPolicyError, ValidationError, ValueError):
            raise LocalProviderConfigurationError(
                "The approved local generation policy is unavailable."
            ) from None
        api_key_value = "" if config.api_key is None else config.api_key.get_secret_value()
        try:
            api_key_bytes = api_key_value.encode("ascii")
        except UnicodeEncodeError:
            api_key_bytes = b""
        if (
            not 32 <= len(api_key_bytes) <= 256
            or any(byte < 0x21 or byte > 0x7E for byte in api_key_bytes)
        ):
            raise LocalProviderConfigurationError(
                "The approved local provider authentication is unavailable."
            )
        if config.base_url != f"http://127.0.0.1:{trusted_model.outer_port}":
            raise LocalProviderConfigurationError(
                "The approved local provider endpoint is unavailable."
            )
        self._config = config
        self._model_policy = trusted_model
        self._prompt_policy = trusted_prompt
        self._identity = identity
        self._transport = transport
        self._call_lock = Lock()

    @property
    def identity(self) -> ProviderIdentity:
        """Return the checksum-bound identity before any model call."""

        return self._identity

    def generate(self, context_json: str) -> str:
        """Send one canonical ContextPack and return one bounded JSON draft string."""

        if not self._call_lock.acquire(blocking=False):
            raise LocalProviderRequestError(
                "The local model is already serving its one approved request."
            )
        try:
            deadline = time.monotonic() + self._model_policy.timeout_seconds
            return self._generate_once(context_json, deadline=deadline)
        finally:
            self._call_lock.release()

    def _generate_once(self, context_json: str, *, deadline: float) -> str:
        """Execute the one admitted request while the process-local call lock is held."""

        trusted_context_json = self._validate_context(context_json)
        self._require_runtime_attestation(deadline=deadline)
        self._require_verified_artifacts(deadline=deadline)
        # The frozen M4 AnswerInstructions identify the output schema but deliberately do not
        # duplicate its fields. Supply the independently checksum-bound serialization template as
        # non-factual system metadata. The ContextPack remains the sole factual user payload.
        system_prompt = (
            f"{self._prompt_policy.source_text}\n"
            f"{self._prompt_policy.request_template_text}"
        )
        request: dict[str, object] = {
            "model": self._model_policy.api_model_name,
            "messages": (
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": trusted_context_json},
            ),
            "temperature": 0,
            "top_p": self._model_policy.top_p,
            "top_k": self._model_policy.top_k,
            "min_p": self._model_policy.min_p,
            "n": 1,
            "stream": False,
            "max_tokens": self._model_policy.max_output_tokens,
            "response_format": {"type": self._model_policy.response_format},
        }
        if self._model_policy.seed is not None:
            request["seed"] = self._model_policy.seed
        raw = self._request_json(
            method="POST",
            path=self._model_policy.chat_completions_path,
            content=canonical_model_json(request).encode("utf-8"),
            deadline=deadline,
        )
        try:
            response = _ChatCompletion.model_validate_json(raw)
            if response.model != self._model_policy.api_model_name:
                raise ValueError
            output = response.choices[0].message.content
            if len(output.encode("utf-8")) > MAX_GENERATED_OUTPUT_BYTES:
                raise ValueError
            return output
        except (IndexError, TypeError, UnicodeError, ValidationError, ValueError):
            raise LocalProviderRequestError(
                "The local model returned an invalid generated response."
            ) from None

    def check_ready(self) -> bool:
        """Verify exact local artifacts and the configured model identity without generation."""

        if not self._call_lock.acquire(blocking=False):
            return False
        try:
            deadline = time.monotonic() + self._model_policy.timeout_seconds
            self._require_runtime_attestation(deadline=deadline)
            self._require_verified_artifacts(deadline=deadline)
            raw = self._request_json(
                method="GET",
                path=self._model_policy.readiness_path,
                content=None,
                deadline=deadline,
            )
            response = _ModelsResponse.model_validate_json(raw)
            expected_model_id = str(self._config.artifact_root.resolve(strict=True))
            return sum(model.id == expected_model_id for model in response.data) == 1
        except Exception:
            return False
        finally:
            self._call_lock.release()

    def _require_runtime_attestation(self, *, deadline: float) -> None:
        """Authenticate the proxy and bind its sandboxed runtime to the model policy."""

        api_key = self._config.api_key
        if api_key is None:  # pragma: no cover - constructor invariant.
            raise LocalProviderConfigurationError(
                "The approved local provider authentication is unavailable."
            )
        challenge = secrets.token_hex(32)
        raw = self._request_json(
            method="GET",
            path=self._model_policy.runtime_attestation_path,
            content=None,
            extra_headers={"x-v0-attestation-challenge": challenge},
            deadline=deadline,
        )
        try:
            attestation = _RuntimeAttestation.model_validate_json(raw)
            payload = attestation.model_dump(mode="json")
            observed_hmac = payload.pop("attestation_hmac_sha256")
            expected_hmac = hmac.new(
                api_key.get_secret_value().encode("utf-8"),
                canonical_model_json(payload).encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            expected_pairs = (
                (attestation.challenge, challenge),
                (
                    attestation.model_policy_manifest_sha256,
                    self._model_policy.manifest_sha256,
                ),
                (
                    attestation.prompt_policy_manifest_sha256,
                    self._model_policy.prompt_policy_manifest_sha256,
                ),
                (
                    attestation.inference_engine_lock_sha256,
                    self._model_policy.inference_engine_lock_sha256,
                ),
                (
                    attestation.inference_engine_wrapper_sha256,
                    self._model_policy.inference_engine_wrapper_sha256,
                ),
                (
                    attestation.inference_engine_module_sha256,
                    self._model_policy.inference_engine_module_sha256,
                ),
                (
                    attestation.inference_python_executable_sha256,
                    self._model_policy.inference_python_executable_sha256,
                ),
                (
                    attestation.inference_python_configuration_sha256,
                    self._model_policy.inference_python_configuration_sha256,
                ),
                (
                    attestation.provider_environment_verifier_sha256,
                    self._model_policy.provider_environment_verifier_sha256,
                ),
                (
                    attestation.provider_environment_manifest_sha256,
                    self._model_policy.provider_environment_manifest_sha256,
                ),
                (
                    attestation.provider_environment_sha256,
                    self._model_policy.provider_environment_sha256,
                ),
                (
                    attestation.provider_environment_distribution_count,
                    self._model_policy.provider_environment_distribution_count,
                ),
                (
                    attestation.provider_environment_file_count,
                    self._model_policy.provider_environment_file_count,
                ),
                (
                    attestation.runtime_launcher_sha256,
                    self._model_policy.runtime_launcher_sha256,
                ),
                (attestation.runtime_proxy_sha256, self._model_policy.runtime_proxy_sha256),
                (attestation.egress_profile_sha256, self._model_policy.egress_profile_sha256),
                (
                    attestation.sandbox_executable_sha256,
                    self._model_policy.sandbox_executable_sha256,
                ),
                (
                    attestation.environment_executable_sha256,
                    self._model_policy.environment_executable_sha256,
                ),
                (attestation.runtime_distributions, self._model_policy.runtime_distributions),
                (attestation.network_policy_key, self._model_policy.network_policy_key),
                (
                    attestation.environment_policy_key,
                    self._model_policy.environment_policy_key,
                ),
                (
                    attestation.inner_authentication_key,
                    self._model_policy.inner_authentication_key,
                ),
                (attestation.egress_probe_key, self._model_policy.egress_probe_key),
                (attestation.startup_warmup_key, self._model_policy.startup_warmup_key),
                (
                    attestation.startup_warmup_max_tokens,
                    self._model_policy.startup_warmup_max_tokens,
                ),
                (attestation.outer_port, self._model_policy.outer_port),
                (attestation.inner_port, self._model_policy.inner_port),
            )
            if not hmac.compare_digest(observed_hmac, expected_hmac) or any(
                observed != expected for observed, expected in expected_pairs
            ):
                raise ValueError
        except (TypeError, UnicodeError, ValidationError, ValueError):
            raise LocalProviderConfigurationError(
                "The approved local provider runtime attestation failed."
            ) from None

    def _validate_context(self, context_json: str) -> str:
        try:
            raw = context_json.encode("utf-8")
            if not raw or len(raw) > MAX_CONTEXT_BYTES:
                raise ValueError
            parsed = ContextPack.model_validate_json(raw)
            trusted = revalidate_context_pack(parsed)
            canonical = canonical_context_json(trusted)
            if canonical != context_json:
                raise ValueError
            return canonical
        except Exception:
            raise LocalProviderRequestError(
                "The local generation request does not match the approved ContextPack."
            ) from None

    def _artifacts_verified(self, *, deadline: float) -> bool:
        root = self._config.artifact_root
        try:
            if root.is_symlink() or not root.is_dir():
                return False
            root_resolved = root.resolve(strict=True)
            expected_paths = {
                str(artifact.relative_path) for artifact in self._model_policy.artifacts
            }
            if _discover_regular_files(root, deadline=deadline) != expected_paths:
                return False
            for artifact in self._model_policy.artifacts:
                path = root / artifact.relative_path
                if _has_symlink_component(root, artifact.relative_path):
                    return False
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(root_resolved) or not resolved.is_file():
                    return False
                observed_size, observed_sha256 = _file_identity(resolved, deadline=deadline)
                if observed_size != artifact.byte_size or observed_sha256 != artifact.sha256:
                    return False
            return True
        except (OSError, RuntimeError, ValueError):
            return False

    def _require_verified_artifacts(self, *, deadline: float) -> None:
        # Re-hash on every readiness and generation call.  A successful earlier
        # check must never become a reusable authorization after artifact drift.
        if not self._artifacts_verified(deadline=deadline):
            raise LocalProviderConfigurationError(
                "The approved local model artifacts are unavailable or invalid."
            )

    def _request_json(
        self,
        *,
        method: str,
        path: str,
        content: bytes | None,
        deadline: float,
        extra_headers: dict[str, str] | None = None,
    ) -> bytes:
        headers = {
            "accept": "application/json",
            "accept-encoding": "identity",
        }
        if content is not None:
            headers["content-type"] = "application/json"
        if self._config.api_key is not None:
            headers["authorization"] = f"Bearer {self._config.api_key.get_secret_value()}"
        if extra_headers is not None:
            headers.update(extra_headers)
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            with httpx.Client(
                base_url=self._config.base_url,
                timeout=httpx.Timeout(remaining),
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                with client.stream(
                    method,
                    path,
                    content=content,
                    headers=headers,
                ) as response:
                    if response.status_code != 200:
                        raise ValueError
                    content_type = response.headers.get("content-type", "")
                    if content_type.split(";", maxsplit=1)[0].strip().casefold() != (
                        "application/json"
                    ):
                        raise ValueError
                    payload = _read_bounded(response, deadline=deadline)
                    if time.monotonic() > deadline:
                        raise TimeoutError
                    return payload
        except Exception:
            raise LocalProviderRequestError("The approved local model is unavailable.") from None


def _read_bounded(response: httpx.Response, *, deadline: float) -> bytes:
    if response.headers.get_list("transfer-encoding"):
        raise ValueError
    encoding = response.headers.get("content-encoding")
    if encoding is not None and encoding.casefold() != "identity":
        raise ValueError
    declared_values = response.headers.get_list("content-length")
    if len(declared_values) > 1:
        raise ValueError
    declared = declared_values[0] if declared_values else None
    if declared is not None:
        try:
            length = int(declared)
        except ValueError:
            raise ValueError from None
        if length < 1 or length > _MAX_HTTP_RESPONSE_BYTES:
            raise ValueError
    payload = bytearray()
    for chunk in response.iter_bytes():
        if time.monotonic() > deadline:
            raise TimeoutError
        payload.extend(chunk)
        if len(payload) > _MAX_HTTP_RESPONSE_BYTES:
            raise ValueError
    if not payload:
        raise ValueError
    return bytes(payload)


def _has_symlink_component(root: Path, relative_path: str) -> bool:
    current = root
    for part in Path(relative_path).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _discover_regular_files(root: Path, *, deadline: float) -> set[str]:
    files: set[str] = set()
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        if time.monotonic() > deadline:
            raise TimeoutError
        directory_path = Path(directory)
        for name in tuple(directory_names):
            if (directory_path / name).is_symlink():
                raise OSError
        for name in file_names:
            path = directory_path / name
            if path.is_symlink() or not path.is_file():
                raise OSError
            files.add(path.relative_to(root).as_posix())
    return files


def _file_identity(path: Path, *, deadline: float) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        os.close(descriptor)
        raise OSError
    with os.fdopen(descriptor, "rb") as stream:
        while chunk := stream.read(_HASH_CHUNK_BYTES):
            if time.monotonic() > deadline:
                raise TimeoutError
            size += len(chunk)
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise OSError
    return size, digest.hexdigest()


__all__ = [
    "LocalOpenAICompatibleProvider",
    "LocalProviderConfig",
    "LocalProviderConfigurationError",
    "LocalProviderRequestError",
]
