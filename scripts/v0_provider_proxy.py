#!/usr/bin/env python3
"""Authenticated, fail-closed runtime proxy for the local V0 MLX provider."""

from __future__ import annotations

import argparse
import errno
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Final

_LOOPBACK_HOST: Final = "127.0.0.1"
_ATTESTATION_PATH: Final = "/v0/runtime-attestation"
_FORWARDED_PATHS: Final = frozenset({"/v1/models", "/v1/chat/completions"})
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_CHALLENGE_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_LENGTH_RE: Final = re.compile(r"^[1-9][0-9]*$")
_MAX_REQUEST_BYTES: Final = 4 * 1024 * 1024
_MAX_RESPONSE_BYTES: Final = 131_072
_MAX_CONTEXT_BYTES: Final = 131_072
_MAX_POLICY_BYTES: Final = 20 * 1024 * 1024
_STARTUP_TIMEOUT_SECONDS: Final = 300.0
_HEADER_BODY_TIMEOUT_SECONDS: Final = 5.0
_INNER_KEY_BYTES: Final = 32
_EGRESS_PROBE_ADDRESS: Final = ("192.0.2.1", 443)
_EGRESS_IPV6_PROBE_ADDRESS: Final = ("2001:db8::1", 443, 0, 0)
_UNAPPROVED_LOOPBACK_PROBE_ADDRESS: Final = (_LOOPBACK_HOST, 5432)
_WARMUP_SYSTEM_TEXT: Final = (
    "Local V0 runtime warmup only. No factual task is present. Return one JSON token."
)
_WARMUP_USER_TEXT: Final = "Synthetic non-factual startup warmup."
_FIXED_ENVIRONMENT: Final[dict[str, str]] = {
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_OFFLINE": "1",
    "LC_ALL": "C",
    "NO_PROXY": _LOOPBACK_HOST,
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
    "PYTHONNOUSERSITE": "1",
    "PYTHONSAFEPATH": "1",
    "TOKENIZERS_PARALLELISM": "false",
    "TRANSFORMERS_OFFLINE": "1",
    "no_proxy": _LOOPBACK_HOST,
}
_PATH_ENVIRONMENT_KEYS: Final = frozenset({"HOME", "TMPDIR", "HF_HOME", "HF_HUB_CACHE"})
_APPLE_TEXT_ENCODING_KEY: Final = "__CF_USER_TEXT_ENCODING"


class RuntimeVerificationError(RuntimeError):
    """The local provider runtime does not match its approved policy."""


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _open_regular_file(path: Path) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(errno.EINVAL, "not a regular file")
        return descriptor
    except OSError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise RuntimeVerificationError("runtime dependency is unavailable") from None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    descriptor = _open_regular_file(path)
    try:
        before = os.fstat(descriptor)
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise RuntimeVerificationError("runtime dependency changed while it was hashed")
    return digest.hexdigest()


def _regular_file(path: Path) -> Path:
    if path.is_symlink() or not path.is_file():
        raise RuntimeVerificationError("runtime dependency is unavailable")
    return path.resolve(strict=True)


def _load_json_object(path: Path) -> dict[str, Any]:
    descriptor = _open_regular_file(path)
    try:
        raw = bytearray()
        while chunk := os.read(descriptor, 1024 * 1024):
            raw.extend(chunk)
            if len(raw) > _MAX_POLICY_BYTES:
                raise RuntimeVerificationError("runtime policy manifest is too large")
    finally:
        os.close(descriptor)
    try:
        value = json.loads(bytes(raw))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeVerificationError("runtime policy manifest is invalid") from exc
    if not isinstance(value, dict):
        raise RuntimeVerificationError("runtime policy manifest must be an object")
    return value


def _require_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise RuntimeVerificationError(f"invalid {label} checksum")
    return value


def _verify_manifest_self_hash(manifest: Mapping[str, Any], *, label: str) -> str:
    expected = _require_sha256(manifest.get("manifest_sha256"), label=label)
    payload = dict(manifest)
    del payload["manifest_sha256"]
    observed = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
    if not hmac.compare_digest(observed, expected):
        raise RuntimeVerificationError(f"{label} self-checksum does not match")
    return expected


def _verify_policy_self_hash(policy: Mapping[str, Any]) -> str:
    return _verify_manifest_self_hash(policy, label="model policy")


def _verify_prompt_policy(
    model_policy: Mapping[str, Any], prompt_policy_path: Path
) -> dict[str, Any]:
    prompt_policy = _load_json_object(prompt_policy_path)
    observed_sha256 = _verify_manifest_self_hash(prompt_policy, label="prompt policy")
    expected_sha256 = _require_sha256(
        model_policy.get("prompt_policy_manifest_sha256"),
        label="prompt policy",
    )
    if not hmac.compare_digest(observed_sha256, expected_sha256):
        raise RuntimeVerificationError("prompt policy checksum does not match")
    for field in ("source_text", "request_template_text"):
        if not isinstance(prompt_policy.get(field), str) or not prompt_policy[field]:
            raise RuntimeVerificationError("prompt policy content is unavailable")
    return prompt_policy


def _verify_static_model_policy(policy: Mapping[str, Any]) -> None:
    expected: dict[str, object] = {
        "manifest_schema_version": "v0-local-model-policy-manifest-v3",
        "api_model_name": "default_model",
        "inference_engine_key": "engine:mlx-lm",
        "inference_engine_version": "0.31.3+mlx-0.32.2+mlx-metal-0.32.2",
        "endpoint_policy_key": "transport:loopback-openai-compatible-http-v1",
        "chat_completions_path": "/v1/chat/completions",
        "readiness_path": "/v1/models",
        "response_format": "json_object",
        "temperature": 0,
        "top_p": 1,
        "top_k": 0,
        "min_p": 0,
        "max_output_tokens": 256,
        "max_output_bytes": 32768,
        "timeout_seconds": 300,
        "retry_count": 0,
        "max_concurrent_requests": 1,
        "prompt_concurrency": 1,
        "decode_concurrency": 1,
        "seed_supported": True,
        "seed": 0,
        "authentication_required": True,
        "runtime_attestation_path": _ATTESTATION_PATH,
        "network_policy_key": "network:macos-sandbox-v0-ports-only-v2",
        "environment_policy_key": "environment:scrubbed-allowlist-v1",
        "inner_authentication_key": "authentication:inherited-fd-bearer-v1",
        "egress_probe_key": ("egress-probe:external-and-unapproved-loopback-denied-v2"),
        "startup_warmup_key": "warmup:nonfactual-one-token-v1",
        "startup_warmup_max_tokens": 1,
        "outer_port": 8123,
        "inner_port": 8124,
    }
    if any(
        policy.get(field) != value or type(policy.get(field)) is not type(value)
        for field, value in expected.items()
    ):
        raise RuntimeVerificationError("model runtime policy does not match V0")


def _verify_file(path: Path, expected: object, *, label: str) -> str:
    approved = _require_sha256(expected, label=label)
    observed = _sha256_file(_regular_file(path))
    if not hmac.compare_digest(observed, approved):
        raise RuntimeVerificationError(f"{label} checksum does not match")
    return observed


def _verify_python_executable(path: Path, expected: object) -> Path:
    if not path.is_symlink() or not os.access(path, os.X_OK):
        raise RuntimeVerificationError("provider Python environment launcher is invalid")
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or resolved.is_symlink():
        raise RuntimeVerificationError("provider Python executable is unavailable")
    approved = _require_sha256(expected, label="Python executable")
    if not hmac.compare_digest(_sha256_file(resolved), approved):
        raise RuntimeVerificationError("Python executable checksum does not match")
    return path.absolute()


def _require_provider_python_paths(
    *, provider_root: Path, executable: Path, configuration: Path
) -> None:
    supplied_root = provider_root.absolute()
    if provider_root.is_symlink() or not provider_root.is_dir():
        raise RuntimeVerificationError("provider environment root is unavailable")
    if (
        executable.absolute() != supplied_root / "bin" / "python"
        or configuration.absolute() != supplied_root / "pyvenv.cfg"
    ):
        raise RuntimeVerificationError("provider Python environment paths are ambiguous")


def _read_api_key(path: Path) -> bytes:
    descriptor = _open_regular_file(path)
    try:
        metadata = os.fstat(descriptor)
        try:
            parent = path.parent.stat(follow_symlinks=False)
        except OSError as exc:
            raise RuntimeVerificationError("provider API key parent is unavailable") from exc
        if (
            metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o077
            or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.getuid()
            or parent.st_mode & 0o022
        ):
            raise RuntimeVerificationError("provider API key ownership or permissions are unsafe")
        raw = bytearray()
        while len(raw) <= 256:
            chunk = os.read(descriptor, 257 - len(raw))
            if not chunk:
                break
            raw.extend(chunk)
    finally:
        os.close(descriptor)
    key = bytes(raw)
    if key.endswith(b"\r\n"):
        key = key[:-2]
    elif key.endswith(b"\n"):
        key = key[:-1]
    if not 32 <= len(key) <= 256:
        raise RuntimeVerificationError("provider API key must contain 32..256 exact bytes")
    if any(byte < 0x21 or byte > 0x7E for byte in key):
        raise RuntimeVerificationError("provider API key must be printable ASCII without spaces")
    return key


def _verify_denied_socket(family: int, kind: int, address: object) -> None:
    try:
        with socket.socket(family, kind) as denied:
            denied.settimeout(0.5)
            result = denied.connect_ex(address)  # type: ignore[arg-type]
    except OSError as exc:
        result = exc.errno if exc.errno is not None else -1
    if result not in {errno.EACCES, errno.EPERM}:
        raise RuntimeVerificationError("the operating-system network sandbox is not enforced")


def _verify_network_sandbox() -> str:
    _verify_denied_socket(socket.AF_INET, socket.SOCK_STREAM, _EGRESS_PROBE_ADDRESS)
    _verify_denied_socket(socket.AF_INET, socket.SOCK_DGRAM, _EGRESS_PROBE_ADDRESS)
    if socket.has_ipv6:
        _verify_denied_socket(
            socket.AF_INET6,
            socket.SOCK_STREAM,
            _EGRESS_IPV6_PROBE_ADDRESS,
        )
    _verify_denied_socket(
        socket.AF_INET,
        socket.SOCK_STREAM,
        _UNAPPROVED_LOOPBACK_PROBE_ADDRESS,
    )
    for port in (8123, 8124):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as admitted:
                admitted.bind((_LOOPBACK_HOST, port))
        except OSError as exc:
            raise RuntimeVerificationError(
                "an approved operating-system loopback port is unavailable"
            ) from exc
    return "egress-probe:external-and-unapproved-loopback-denied-v2"


def _model_inventory(root: Path) -> list[str]:
    observed: list[str] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise RuntimeVerificationError("model artifact inventory is unavailable") from exc
        for entry in entries:
            path = Path(entry.path)
            if entry.is_symlink():
                raise RuntimeVerificationError("model artifact inventory contains a symlink")
            if entry.is_dir(follow_symlinks=False):
                pending.append(path)
            elif entry.is_file(follow_symlinks=False):
                observed.append(path.relative_to(root).as_posix())
            else:
                raise RuntimeVerificationError("model artifact inventory contains a special file")
    return sorted(observed)


def _verify_model_artifacts(policy: Mapping[str, Any], model_dir: Path) -> Path:
    if model_dir.is_symlink() or not model_dir.is_dir():
        raise RuntimeVerificationError("model artifact root is unavailable")
    root = model_dir.resolve(strict=True)
    raw_artifacts = policy.get("artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise RuntimeVerificationError("model artifact inventory is unavailable")
    approved_paths: list[str] = []
    for raw_artifact in raw_artifacts:
        if not isinstance(raw_artifact, Mapping):
            raise RuntimeVerificationError("model artifact inventory is invalid")
        relative_path = raw_artifact.get("relative_path")
        byte_size = raw_artifact.get("byte_size")
        if (
            not isinstance(relative_path, str)
            or not relative_path
            or Path(relative_path).is_absolute()
            or ".." in Path(relative_path).parts
            or Path(relative_path).as_posix() != relative_path
            or not isinstance(byte_size, int)
            or isinstance(byte_size, bool)
            or byte_size <= 0
        ):
            raise RuntimeVerificationError("model artifact inventory is invalid")
        candidate = root / relative_path
        resolved = _regular_file(candidate)
        if not resolved.is_relative_to(root):
            raise RuntimeVerificationError("model artifact escapes its approved root")
        try:
            observed_size = resolved.stat().st_size
        except OSError as exc:
            raise RuntimeVerificationError("model artifact is unavailable") from exc
        if observed_size != byte_size:
            raise RuntimeVerificationError("model artifact size does not match")
        _verify_file(resolved, raw_artifact.get("sha256"), label="model artifact")
        approved_paths.append(relative_path)
    if approved_paths != sorted(approved_paths) or len(approved_paths) != len(set(approved_paths)):
        raise RuntimeVerificationError("model artifact inventory order is invalid")
    observed_paths = _model_inventory(root)
    if observed_paths != approved_paths:
        raise RuntimeVerificationError("model artifact inventory is not exact")
    return root


def _verify_scrubbed_environment(environment: Mapping[str, str]) -> dict[str, str]:
    expected_keys = set(_FIXED_ENVIRONMENT) | set(_PATH_ENVIRONMENT_KEYS)
    observed_keys = set(environment)
    allowed_keys = expected_keys | {_APPLE_TEXT_ENCODING_KEY}
    if observed_keys != expected_keys and observed_keys != allowed_keys:
        raise RuntimeVerificationError("provider environment is not the approved allowlist")
    injected = environment.get(_APPLE_TEXT_ENCODING_KEY)
    if injected is not None and injected != f"0x{os.getuid():X}:0x0:0x0":
        raise RuntimeVerificationError("provider environment is not the approved allowlist")
    if any(environment.get(key) != value for key, value in _FIXED_ENVIRONMENT.items()):
        raise RuntimeVerificationError("provider environment policy does not match V0")
    for key in _PATH_ENVIRONMENT_KEYS:
        raw_path = environment.get(key)
        if not raw_path or not Path(raw_path).is_absolute():
            raise RuntimeVerificationError("provider environment path is invalid")
        path = Path(raw_path)
        try:
            metadata = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise RuntimeVerificationError("provider environment path is unavailable") from exc
        if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeVerificationError("provider environment path is unsafe")
    scrubbed = dict(environment)
    scrubbed.pop(_APPLE_TEXT_ENCODING_KEY, None)
    return scrubbed


def _verify_provider_environment(
    *,
    policy: Mapping[str, Any],
    manifest_path: Path,
    provider_root: Path,
    verifier_path: Path,
    python_executable: Path,
    environment: Mapping[str, str],
    deadline: float,
) -> tuple[dict[str, str], str, int, int]:
    manifest = _load_json_object(manifest_path)
    manifest_sha256 = _verify_manifest_self_hash(manifest, label="provider environment manifest")
    approved_manifest_sha256 = _require_sha256(
        policy.get("provider_environment_manifest_sha256"),
        label="provider environment manifest",
    )
    if not hmac.compare_digest(manifest_sha256, approved_manifest_sha256):
        raise RuntimeVerificationError("provider environment manifest checksum does not match")
    expected_sha256 = _require_sha256(
        policy.get("provider_environment_sha256"), label="provider environment"
    )
    distribution_count = policy.get("provider_environment_distribution_count")
    file_count = policy.get("provider_environment_file_count")
    distributions = policy.get("runtime_distributions")
    if (
        manifest.get("manifest_schema_version") != "v0-provider-environment-manifest-v1"
        or manifest.get("identity_schema_version") != "v0-provider-environment-identity-v1"
        or manifest.get("provider_environment_sha256") != expected_sha256
        or manifest.get("provider_environment_distribution_count") != distribution_count
        or manifest.get("provider_environment_file_count") != file_count
        or not isinstance(distribution_count, int)
        or isinstance(distribution_count, bool)
        or distribution_count <= 0
        or not isinstance(file_count, int)
        or isinstance(file_count, bool)
        or file_count <= 0
        or not isinstance(distributions, Mapping)
    ):
        raise RuntimeVerificationError("provider environment manifest does not match V0")
    try:
        completed = subprocess.run(
            [
                str(python_executable),
                "-B",
                "-I",
                str(verifier_path),
                "identity",
                "--provider-root",
                str(provider_root),
            ],
            capture_output=True,
            check=False,
            close_fds=True,
            env=dict(environment),
            timeout=_remaining_seconds(deadline),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeVerificationError("provider environment identity does not match") from exc
    expected_output = _canonical_json_bytes(manifest) + b"\n"
    if (
        completed.returncode != 0
        or completed.stderr
        or len(completed.stdout) > _MAX_POLICY_BYTES
        or not hmac.compare_digest(completed.stdout, expected_output)
    ):
        raise RuntimeVerificationError("provider environment identity does not match")
    manifest_distributions = manifest.get("distributions")
    if not isinstance(manifest_distributions, list):
        raise RuntimeVerificationError("provider environment manifest does not match V0")
    observed_versions: dict[str, str] = {}
    for item in manifest_distributions:
        if not isinstance(item, Mapping):
            raise RuntimeVerificationError("provider environment manifest does not match V0")
        name = item.get("canonical_name")
        version = item.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            raise RuntimeVerificationError("provider environment manifest does not match V0")
        observed_versions[name] = version
    approved_distributions = dict(distributions)
    if (
        any(
            observed_versions.get(name) != version
            for name, version in approved_distributions.items()
        )
        or len(observed_versions) != distribution_count
    ):
        raise RuntimeVerificationError("provider environment identity does not match")
    return approved_distributions, expected_sha256, distribution_count, file_count


def _validate_chat_request(
    body: bytes,
    *,
    model_policy: Mapping[str, Any],
    prompt_policy: Mapping[str, Any],
) -> None:
    try:
        request = json.loads(body)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeVerificationError("generation request is not valid JSON") from exc
    if not isinstance(request, dict) or _canonical_json_bytes(request) != body:
        raise RuntimeVerificationError("generation request is not canonical JSON")
    expected_keys = {
        "max_tokens",
        "messages",
        "model",
        "n",
        "response_format",
        "stream",
        "temperature",
        "top_p",
        "top_k",
        "min_p",
    }
    if model_policy.get("seed") is not None:
        expected_keys.add("seed")
    if set(request) != expected_keys:
        raise RuntimeVerificationError("generation request fields do not match V0")
    scalar_pairs = (
        (request.get("model"), model_policy.get("api_model_name")),
        (request.get("temperature"), model_policy.get("temperature")),
        (request.get("top_p"), model_policy.get("top_p")),
        (request.get("top_k"), model_policy.get("top_k")),
        (request.get("min_p"), model_policy.get("min_p")),
        (request.get("max_tokens"), model_policy.get("max_output_tokens")),
        (request.get("seed"), model_policy.get("seed")),
    )
    if (
        any(
            observed != expected or type(observed) is not type(expected)
            for observed, expected in scalar_pairs
        )
        or request.get("n") != 1
        or isinstance(request.get("n"), bool)
        or request.get("stream") is not False
        or request.get("response_format") != {"type": "json_object"}
    ):
        raise RuntimeVerificationError("generation request policy does not match V0")
    messages = request.get("messages")
    if not isinstance(messages, list) or len(messages) != 2:
        raise RuntimeVerificationError("generation request messages do not match V0")
    expected_system = f"{prompt_policy['source_text']}\n{prompt_policy['request_template_text']}"
    if messages[0] != {"role": "system", "content": expected_system}:
        raise RuntimeVerificationError("generation system policy does not match V0")
    user_message = messages[1]
    if (
        not isinstance(user_message, dict)
        or set(user_message) != {"role", "content"}
        or user_message.get("role") != "user"
        or not isinstance(user_message.get("content"), str)
    ):
        raise RuntimeVerificationError("generation factual payload does not match V0")
    user_bytes = user_message["content"].encode("utf-8")
    if not user_bytes or len(user_bytes) > _MAX_CONTEXT_BYTES:
        raise RuntimeVerificationError("generation ContextPack exceeds its approved bound")
    try:
        context = json.loads(user_bytes)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeVerificationError("generation ContextPack is not valid JSON") from exc
    if not isinstance(context, dict) or _canonical_json_bytes(context) != user_bytes:
        raise RuntimeVerificationError("generation ContextPack is not canonical JSON")
    observed_context_sha256 = _require_sha256(context.get("context_sha256"), label="ContextPack")
    context_without_hash = dict(context)
    del context_without_hash["context_sha256"]
    expected_context_sha256 = hashlib.sha256(
        _canonical_json_bytes(context_without_hash)
    ).hexdigest()
    instructions = context.get("answer_instructions")
    if (
        not hmac.compare_digest(observed_context_sha256, expected_context_sha256)
        or context.get("context_schema_version") != "context-pack-v1"
        or not isinstance(instructions, dict)
        or instructions.get("instruction_policy_key") != prompt_policy.get("prompt_policy_key")
        or instructions.get("source_text") != prompt_policy.get("source_text")
        or instructions.get("source_text_sha256") != prompt_policy.get("source_text_sha256")
    ):
        raise RuntimeVerificationError("generation ContextPack policy does not match V0")


def _runtime_attestation(
    *,
    challenge: str,
    api_key: bytes,
    policy: Mapping[str, Any],
    policy_sha256: str,
    distributions: Mapping[str, str],
) -> dict[str, object]:
    body: dict[str, object] = {
        "attestation_schema_version": "v0-provider-runtime-attestation-v3",
        "challenge": challenge,
        "model_policy_manifest_sha256": policy_sha256,
        "prompt_policy_manifest_sha256": policy["prompt_policy_manifest_sha256"],
        "inference_engine_lock_sha256": policy["inference_engine_lock_sha256"],
        "inference_engine_wrapper_sha256": policy["inference_engine_wrapper_sha256"],
        "inference_engine_module_sha256": policy["inference_engine_module_sha256"],
        "inference_python_executable_sha256": policy["inference_python_executable_sha256"],
        "inference_python_configuration_sha256": policy["inference_python_configuration_sha256"],
        "provider_environment_verifier_sha256": policy["provider_environment_verifier_sha256"],
        "provider_environment_manifest_sha256": policy["provider_environment_manifest_sha256"],
        "provider_environment_sha256": policy["provider_environment_sha256"],
        "provider_environment_distribution_count": policy[
            "provider_environment_distribution_count"
        ],
        "provider_environment_file_count": policy["provider_environment_file_count"],
        "runtime_launcher_sha256": policy["runtime_launcher_sha256"],
        "runtime_proxy_sha256": policy["runtime_proxy_sha256"],
        "egress_profile_sha256": policy["egress_profile_sha256"],
        "sandbox_executable_sha256": policy["sandbox_executable_sha256"],
        "environment_executable_sha256": policy["environment_executable_sha256"],
        "runtime_distributions": dict(distributions),
        "network_policy_key": policy["network_policy_key"],
        "environment_policy_key": policy["environment_policy_key"],
        "inner_authentication_key": policy["inner_authentication_key"],
        "egress_probe_key": policy["egress_probe_key"],
        "startup_warmup_key": policy["startup_warmup_key"],
        "startup_warmup_max_tokens": policy["startup_warmup_max_tokens"],
        "outer_port": policy["outer_port"],
        "inner_port": policy["inner_port"],
    }
    body["attestation_hmac_sha256"] = hmac.new(
        api_key, _canonical_json_bytes(body), hashlib.sha256
    ).hexdigest()
    return body


class _ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        address: tuple[str, int],
        *,
        inner_port: int,
        inner_api_key: bytes,
        api_key: bytes,
        policy: Mapping[str, Any],
        prompt_policy: Mapping[str, Any],
        policy_sha256: str,
        distributions: Mapping[str, str],
    ) -> None:
        super().__init__(address, _ProxyHandler)
        self.inner_port = inner_port
        self.inner_api_key = inner_api_key
        self.api_key = api_key
        self.policy = policy
        self.prompt_policy = prompt_policy
        self.policy_sha256 = policy_sha256
        self.distributions = distributions
        self.generation_lock = Lock()


class _ProxyHandler(BaseHTTPRequestHandler):
    server: _ProxyServer
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(_HEADER_BODY_TIMEOUT_SECONDS)

    def log_message(self, _format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.close_connection = True
        if not self._safe_framing(expect_body=False):
            self._error(HTTPStatus.BAD_REQUEST)
            return
        if not self._authorized():
            self._error(HTTPStatus.UNAUTHORIZED)
            return
        if self.path == _ATTESTATION_PATH:
            challenges = self.headers.get_all("x-v0-attestation-challenge", [])
            challenge = challenges[0] if len(challenges) == 1 else ""
            if _CHALLENGE_RE.fullmatch(challenge) is None:
                self._error(HTTPStatus.BAD_REQUEST)
                return
            self._json(
                HTTPStatus.OK,
                _runtime_attestation(
                    challenge=challenge,
                    api_key=self.server.api_key,
                    policy=self.server.policy,
                    policy_sha256=self.server.policy_sha256,
                    distributions=self.server.distributions,
                ),
            )
            return
        if self.path != "/v1/models":
            self._error(HTTPStatus.NOT_FOUND)
            return
        self._forward(body=None)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.close_connection = True
        if not self._safe_framing(expect_body=True):
            self._error(HTTPStatus.BAD_REQUEST)
            return
        if not self._authorized():
            self._error(HTTPStatus.UNAUTHORIZED)
            return
        if self.path != "/v1/chat/completions":
            self._error(HTTPStatus.NOT_FOUND)
            return
        content_types = self.headers.get_all("content-type", [])
        if (
            len(content_types) != 1
            or content_types[0].split(";", maxsplit=1)[0].strip().casefold() != "application/json"
        ):
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        length_text = self.headers.get_all("content-length", [""])[0]
        if _CONTENT_LENGTH_RE.fullmatch(length_text) is None:
            self._error(HTTPStatus.LENGTH_REQUIRED)
            return
        length = int(length_text)
        if length > _MAX_REQUEST_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        try:
            body = self.rfile.read(length)
        except (OSError, TimeoutError):
            self._error(HTTPStatus.REQUEST_TIMEOUT)
            return
        if len(body) != length:
            self._error(HTTPStatus.BAD_REQUEST)
            return
        try:
            _validate_chat_request(
                body,
                model_policy=self.server.policy,
                prompt_policy=self.server.prompt_policy,
            )
        except RuntimeVerificationError:
            self._error(HTTPStatus.BAD_REQUEST)
            return
        if not self.server.generation_lock.acquire(blocking=False):
            self._error(HTTPStatus.TOO_MANY_REQUESTS)
            return
        try:
            self._forward(body=body)
        finally:
            self.server.generation_lock.release()

    def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.close_connection = True
        self._error(HTTPStatus.METHOD_NOT_ALLOWED)

    def _safe_framing(self, *, expect_body: bool) -> bool:
        if (
            self.headers.get_all("transfer-encoding", [])
            or self.headers.get_all("expect", [])
            or self.headers.get_all("content-encoding", [])
            or len(self.headers.get_all("authorization", [])) != 1
        ):
            return False
        lengths = self.headers.get_all("content-length", [])
        return len(lengths) == 1 if expect_body else not lengths

    def _authorized(self) -> bool:
        expected = b"Bearer " + self.server.api_key
        observed_values = self.headers.get_all("authorization", [])
        if len(observed_values) != 1:
            return False
        try:
            observed = observed_values[0].encode("ascii")
        except UnicodeEncodeError:
            return False
        return hmac.compare_digest(observed, expected)

    def _forward(self, *, body: bytes | None) -> None:
        if self.path not in _FORWARDED_PATHS:
            self._error(HTTPStatus.NOT_FOUND)
            return
        timeout = self.server.policy.get("timeout_seconds")
        if not isinstance(timeout, int) or isinstance(timeout, bool):
            self._error(HTTPStatus.BAD_GATEWAY)
            return
        deadline = time.monotonic() + timeout
        headers = {
            "accept": "application/json",
            "accept-encoding": "identity",
            "authorization": f"Bearer {self.server.inner_api_key.hex()}",
            "connection": "close",
        }
        if body is not None:
            headers["content-type"] = "application/json"
        connection = http.client.HTTPConnection(
            _LOOPBACK_HOST,
            self.server.inner_port,
            timeout=_remaining_seconds(deadline),
        )
        try:
            connection.request(self.command, self.path, body=body, headers=headers)
            _set_connection_deadline(connection, deadline)
            response = connection.getresponse()
            _set_connection_deadline(connection, deadline)
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
            if time.monotonic() > deadline:
                raise TimeoutError
            if len(payload) > _MAX_RESPONSE_BYTES:
                raise RuntimeVerificationError("provider response exceeds the approved bound")
            response_types = response.headers.get_all("content-type") or []
            response_lengths = response.headers.get_all("content-length") or []
            length_is_valid = (
                len(response_lengths) == 1
                and response_lengths[0].isdigit()
                and int(response_lengths[0]) == len(payload)
            ) or (
                not response_lengths
                and (
                    response.version == 10
                    or response.getheader("connection", "").casefold() == "close"
                )
            )
            if (
                response.headers.get_all("transfer-encoding")
                or len(response_types) != 1
                or response_types[0].split(";", 1)[0].strip().casefold() != "application/json"
                or not length_is_valid
            ):
                raise RuntimeVerificationError("provider response framing is invalid")
            self.send_response(response.status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.send_header("cache-control", "no-store")
            self.send_header("connection", "close")
            self.end_headers()
            self.wfile.write(payload)
        except Exception:
            self._error(HTTPStatus.BAD_GATEWAY)
        finally:
            connection.close()

    def _json(self, status: HTTPStatus, value: object) -> None:
        payload = _canonical_json_bytes(value)
        self.close_connection = True
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.send_header("cache-control", "no-store")
        self.send_header("connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, status: HTTPStatus) -> None:
        self._json(status, {"error": "local_provider_request_rejected"})


def _remaining_seconds(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    return remaining


def _set_connection_deadline(connection: http.client.HTTPConnection, deadline: float) -> None:
    if connection.sock is not None:
        connection.sock.settimeout(_remaining_seconds(deadline))


def _engine_command(arguments: argparse.Namespace, *, inner_key_descriptor: int) -> list[str]:
    return [
        str(arguments.python_executable),
        "-B",
        "-I",
        str(arguments.engine_wrapper),
        "--model-dir",
        str(arguments.model_dir),
        "--inner-key-fd",
        str(inner_key_descriptor),
    ]


def _inner_headers(inner_api_key: bytes) -> dict[str, str]:
    return {
        "accept": "application/json",
        "accept-encoding": "identity",
        "authorization": f"Bearer {inner_api_key.hex()}",
        "connection": "close",
    }


def _read_bounded_json_response(
    connection: http.client.HTTPConnection, *, deadline: float
) -> tuple[int, dict[str, Any]]:
    _set_connection_deadline(connection, deadline)
    response = connection.getresponse()
    _set_connection_deadline(connection, deadline)
    payload = response.read(_MAX_RESPONSE_BYTES + 1)
    if time.monotonic() > deadline or len(payload) > _MAX_RESPONSE_BYTES:
        raise RuntimeVerificationError("inference engine response exceeded its bound")
    if response.headers.get_all("transfer-encoding"):
        raise RuntimeVerificationError("inference engine response framing is invalid")
    content_types = response.headers.get_all("content-type") or []
    content_lengths = response.headers.get_all("content-length") or []
    length_is_valid = (
        len(content_lengths) == 1
        and content_lengths[0].isdigit()
        and int(content_lengths[0]) == len(payload)
    ) or (
        not content_lengths
        and (response.version == 10 or response.getheader("connection", "").casefold() == "close")
    )
    if (
        len(content_types) != 1
        or content_types[0].split(";", 1)[0].strip().casefold() != "application/json"
        or not length_is_valid
    ):
        raise RuntimeVerificationError("inference engine response framing is invalid")
    try:
        parsed = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeVerificationError("inference engine response is invalid") from exc
    if not isinstance(parsed, dict):
        raise RuntimeVerificationError("inference engine response is invalid")
    return response.status, parsed


def _wait_for_engine(
    process: subprocess.Popen[bytes],
    inner_port: int,
    *,
    inner_api_key: bytes,
    expected_model_id: str,
    deadline: float,
) -> None:
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeVerificationError("inference engine exited during startup")
        connection = http.client.HTTPConnection(
            _LOOPBACK_HOST,
            inner_port,
            timeout=min(1.0, _remaining_seconds(deadline)),
        )
        try:
            connection.request("GET", "/v1/models", headers=_inner_headers(inner_api_key))
            status, parsed = _read_bounded_json_response(connection, deadline=deadline)
            data = parsed.get("data")
            if (
                status == HTTPStatus.OK
                and parsed.get("object") == "list"
                and isinstance(data, list)
                and len(data) == 1
                and isinstance(data[0], dict)
                and data[0].get("id") == expected_model_id
            ):
                return
        except (OSError, TimeoutError, RuntimeVerificationError):
            pass
        finally:
            connection.close()
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
    raise RuntimeVerificationError("inference engine readiness timed out")


def _warm_engine(
    *,
    inner_port: int,
    inner_api_key: bytes,
    model_name: str,
    deadline: float,
) -> None:
    payload = _canonical_json_bytes(
        {
            "max_tokens": 1,
            "messages": [
                {"content": _WARMUP_SYSTEM_TEXT, "role": "system"},
                {"content": _WARMUP_USER_TEXT, "role": "user"},
            ],
            "min_p": 0,
            "model": model_name,
            "n": 1,
            "response_format": {"type": "json_object"},
            "seed": 0,
            "stream": False,
            "temperature": 0,
            "top_k": 0,
            "top_p": 1,
        }
    )
    headers = _inner_headers(inner_api_key)
    headers["content-type"] = "application/json"
    connection = http.client.HTTPConnection(
        _LOOPBACK_HOST,
        inner_port,
        timeout=_remaining_seconds(deadline),
    )
    try:
        connection.request("POST", "/v1/chat/completions", body=payload, headers=headers)
        status, response = _read_bounded_json_response(connection, deadline=deadline)
        choices = response.get("choices")
        if (
            status != HTTPStatus.OK
            or not isinstance(choices, list)
            or len(choices) != 1
            or not isinstance(choices[0], dict)
        ):
            raise RuntimeVerificationError("inference engine startup warmup failed")
    except (OSError, TimeoutError) as exc:
        raise RuntimeVerificationError("inference engine startup warmup failed") from exc
    finally:
        connection.close()


def _parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outer-port", type=int, required=True)
    parser.add_argument("--inner-port", type=int, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--model-policy", type=Path, required=True)
    parser.add_argument("--prompt-policy", type=Path, required=True)
    parser.add_argument("--provider-root", type=Path, required=True)
    parser.add_argument("--provider-environment-manifest", type=Path, required=True)
    parser.add_argument("--environment-verifier", type=Path, required=True)
    parser.add_argument("--engine-wrapper", type=Path, required=True)
    parser.add_argument("--engine-module", type=Path, required=True)
    parser.add_argument("--python-executable", type=Path, required=True)
    parser.add_argument("--python-configuration", type=Path, required=True)
    parser.add_argument("--engine-lock", type=Path, required=True)
    parser.add_argument("--runtime-launcher", type=Path, required=True)
    parser.add_argument("--proxy-script", type=Path, required=True)
    parser.add_argument("--egress-profile", type=Path, required=True)
    parser.add_argument("--sandbox-executable", type=Path, required=True)
    parser.add_argument("--environment-executable", type=Path, required=True)
    parser.add_argument("--api-key-file", type=Path, required=True)
    arguments = parser.parse_args(argv)
    for port in (arguments.outer_port, arguments.inner_port):
        if not 1024 <= port <= 65535:
            parser.error("provider ports must be unprivileged")
    if arguments.outer_port == arguments.inner_port:
        parser.error("provider ports must differ")
    return arguments


def _write_exact(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise RuntimeVerificationError("inner provider credential pipe failed")
        offset += written


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    policy = _load_json_object(arguments.model_policy)
    policy_sha256 = _verify_policy_self_hash(policy)
    _verify_static_model_policy(policy)
    prompt_policy = _verify_prompt_policy(policy, arguments.prompt_policy)
    if arguments.outer_port != policy.get("outer_port") or arguments.inner_port != policy.get(
        "inner_port"
    ):
        raise RuntimeVerificationError("provider ports do not match the approved policy")
    _require_provider_python_paths(
        provider_root=arguments.provider_root,
        executable=arguments.python_executable,
        configuration=arguments.python_configuration,
    )
    runtime_files = (
        (arguments.engine_lock, policy.get("inference_engine_lock_sha256"), "engine lock"),
        (
            arguments.engine_wrapper,
            policy.get("inference_engine_wrapper_sha256"),
            "engine wrapper",
        ),
        (
            arguments.engine_module,
            policy.get("inference_engine_module_sha256"),
            "engine module",
        ),
        (
            arguments.python_configuration,
            policy.get("inference_python_configuration_sha256"),
            "Python configuration",
        ),
        (
            arguments.environment_verifier,
            policy.get("provider_environment_verifier_sha256"),
            "provider environment verifier",
        ),
        (
            arguments.runtime_launcher,
            policy.get("runtime_launcher_sha256"),
            "runtime launcher",
        ),
        (arguments.proxy_script, policy.get("runtime_proxy_sha256"), "runtime proxy"),
        (arguments.egress_profile, policy.get("egress_profile_sha256"), "egress profile"),
        (
            arguments.sandbox_executable,
            policy.get("sandbox_executable_sha256"),
            "sandbox executable",
        ),
        (
            arguments.environment_executable,
            policy.get("environment_executable_sha256"),
            "environment executable",
        ),
    )
    for path, expected, label in runtime_files:
        _verify_file(path, expected, label=label)
    python_executable = _verify_python_executable(
        arguments.python_executable,
        policy.get("inference_python_executable_sha256"),
    )
    child_environment = _verify_scrubbed_environment(os.environ)
    environment_deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
    distributions, environment_sha256, distribution_count, file_count = (
        _verify_provider_environment(
            policy=policy,
            manifest_path=arguments.provider_environment_manifest,
            provider_root=arguments.provider_root,
            verifier_path=arguments.environment_verifier,
            python_executable=python_executable,
            environment=child_environment,
            deadline=environment_deadline,
        )
    )
    if (
        environment_sha256 != policy.get("provider_environment_sha256")
        or distribution_count != policy.get("provider_environment_distribution_count")
        or file_count != policy.get("provider_environment_file_count")
    ):
        raise RuntimeVerificationError("provider environment identity does not match")
    api_key = _read_api_key(arguments.api_key_file)
    model_root = _verify_model_artifacts(policy, arguments.model_dir)
    _verify_network_sandbox()

    inner_api_key = secrets.token_bytes(_INNER_KEY_BYTES)
    read_descriptor, write_descriptor = os.pipe()
    try:
        _write_exact(write_descriptor, inner_api_key)
    finally:
        os.close(write_descriptor)
    arguments.python_executable = python_executable
    try:
        process = subprocess.Popen(
            _engine_command(arguments, inner_key_descriptor=read_descriptor),
            close_fds=True,
            env=child_environment,
            pass_fds=(read_descriptor,),
        )
    finally:
        os.close(read_descriptor)
    startup_deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
    try:
        _wait_for_engine(
            process,
            arguments.inner_port,
            inner_api_key=inner_api_key,
            expected_model_id=str(model_root),
            deadline=startup_deadline,
        )
        _warm_engine(
            inner_port=arguments.inner_port,
            inner_api_key=inner_api_key,
            model_name=str(policy["api_model_name"]),
            deadline=startup_deadline,
        )
        server = _ProxyServer(
            (_LOOPBACK_HOST, arguments.outer_port),
            inner_port=arguments.inner_port,
            inner_api_key=inner_api_key,
            api_key=api_key,
            policy=policy,
            prompt_policy=prompt_policy,
            policy_sha256=policy_sha256,
            distributions=distributions,
        )

        def stop(_signum: int, _frame: object) -> None:
            Thread(target=server.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        server.serve_forever(poll_interval=0.25)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeVerificationError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(4) from None
