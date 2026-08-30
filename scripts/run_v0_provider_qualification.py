#!/usr/bin/env python3
"""Build and run the pre-registered fixed offline V0 provider qualification."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import platform
import secrets
import socket
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import SecretStr

from eve_relation_rag.generation.composer import GenerationComposer, GenerationComposerError
from eve_relation_rag.generation.local_provider import (
    LocalOpenAICompatibleProvider,
    LocalProviderConfig,
    LocalProviderConfigurationError,
    LocalProviderRequestError,
)
from eve_relation_rag.generation.policy import (
    GenerationPolicyError,
    LocalModelPolicyManifest,
    PromptPolicyManifest,
    load_local_model_policy_manifest,
    load_prompt_policy_manifest,
)
from eve_relation_rag.generation.qualification import (
    CURRENT_MODEL_POLICY_SHA256,
    CURRENT_PROMPT_POLICY_SHA256,
    ProviderEnvironmentManifestBinding,
    ProviderQualificationDefinition,
    ProviderQualificationError,
    ProviderQualificationObservation,
    QualificationFileIdentity,
    RecordingGenerationProvider,
    build_provider_qualification_candidate,
    build_provider_qualification_client_runtime_manifest,
    build_provider_qualification_definition,
    build_provider_qualification_report,
    composition_observation_fields,
    load_provider_qualification_definition,
    parse_provider_environment_manifest,
    qualification_file_identity,
    verify_provider_qualification_definition,
    write_provider_qualification_definition,
    write_provider_qualification_report,
)

if __package__:
    from scripts.v0_provider_environment import (
        ProviderEnvironmentError,
        load_provider_environment_manifest,
        verify_provider_environment_manifest,
    )
else:  # pragma: no cover - exercised by the documented direct-script invocation.
    from v0_provider_environment import (  # type: ignore[import-not-found, no-redef]
        ProviderEnvironmentError,
        load_provider_environment_manifest,
        verify_provider_environment_manifest,
    )

_MODEL_POLICY_RELATIVE = (
    ".artifacts/v0_activation/manifests/v0_local_model_policy_manifest.v2.json"
)
_PROMPT_POLICY_RELATIVE = (
    ".artifacts/v0_activation/manifests/v0_prompt_policy_manifest.v2.json"
)
_ENVIRONMENT_MANIFEST_RELATIVE = (
    ".artifacts/v0_activation/manifests/v0_provider_environment_manifest.json"
)
_RUNNER_RELATIVE = "scripts/run_v0_provider_qualification.py"
_QUALIFICATION_MODULE_RELATIVE = "src/eve_relation_rag/generation/qualification.py"
_LAUNCHER_RELATIVE = "scripts/run_v0_local_provider.sh"
_MODEL_ROOT_RELATIVE = ".artifacts/v0_activation/model/Qwen3-4B-Instruct-2507-4bit"
_PROVIDER_ROOT_RELATIVE = ".artifacts/v0_activation/provider-env"
_PORT_POLL_SECONDS = 0.2
_SHUTDOWN_TIMEOUT_SECONDS = 20.0
_PORT_CLOSE_TIMEOUT_SECONDS = 10.0


def _project_root() -> Path:
    return Path(__file__).resolve(strict=True).parents[1]


def _paths(root: Path) -> dict[str, Path]:
    return {
        "model_policy": root / _MODEL_POLICY_RELATIVE,
        "prompt_policy": root / _PROMPT_POLICY_RELATIVE,
        "environment_manifest": root / _ENVIRONMENT_MANIFEST_RELATIVE,
        "runner": root / _RUNNER_RELATIVE,
        "qualification_module": root / _QUALIFICATION_MODULE_RELATIVE,
        "launcher": root / _LAUNCHER_RELATIVE,
        "model_root": root / _MODEL_ROOT_RELATIVE,
        "provider_root": root / _PROVIDER_ROOT_RELATIVE,
    }


def _file_id(path: Path, relative_path: str) -> QualificationFileIdentity:
    return qualification_file_identity(path, relative_path=relative_path)


def _load_policy_inputs(
    root: Path,
    *,
    verify_environment: bool,
) -> tuple[
    LocalModelPolicyManifest,
    PromptPolicyManifest,
    ProviderEnvironmentManifestBinding,
    QualificationFileIdentity,
    QualificationFileIdentity,
    QualificationFileIdentity,
]:
    paths = _paths(root)
    model = load_local_model_policy_manifest(
        paths["model_policy"], approved_manifest_sha256=CURRENT_MODEL_POLICY_SHA256
    )
    prompt = load_prompt_policy_manifest(
        paths["prompt_policy"], approved_manifest_sha256=CURRENT_PROMPT_POLICY_SHA256
    )
    raw_environment = load_provider_environment_manifest(paths["environment_manifest"])
    environment = parse_provider_environment_manifest(raw_environment)
    if verify_environment:
        verified = verify_provider_environment_manifest(
            paths["provider_root"],
            raw_environment,
            expected_manifest_sha256=environment.manifest_sha256,
        )
        if (
            verified.semantic_sha256 != environment.provider_environment_sha256
            or verified.distribution_count != environment.provider_environment_distribution_count
            or verified.file_count != environment.provider_environment_file_count
        ):
            raise ProviderQualificationError(
                "The physical provider environment does not match the definition."
            )
    return (
        model,
        prompt,
        environment,
        _file_id(paths["model_policy"], _MODEL_POLICY_RELATIVE),
        _file_id(paths["prompt_policy"], _PROMPT_POLICY_RELATIVE),
        _file_id(paths["environment_manifest"], _ENVIRONMENT_MANIFEST_RELATIVE),
    )


def _build_definition(root: Path, output: Path) -> int:
    model, prompt, environment, model_file, prompt_file, environment_file = _load_policy_inputs(
        root, verify_environment=False
    )
    candidate = build_provider_qualification_candidate(
        model_policy=model,
        prompt_policy=prompt,
        environment_manifest=environment,
        model_policy_file=model_file,
        prompt_policy_file=prompt_file,
        environment_manifest_file=environment_file,
    )
    runner_file = _file_id(_paths(root)["runner"], _RUNNER_RELATIVE)
    qualification_module_file = _file_id(
        _paths(root)["qualification_module"], _QUALIFICATION_MODULE_RELATIVE
    )
    client_runtime_manifest = build_provider_qualification_client_runtime_manifest(root)
    definition = build_provider_qualification_definition(
        candidate=candidate,
        runner_file=runner_file,
        qualification_module_file=qualification_module_file,
        client_runtime_manifest=client_runtime_manifest,
    )
    written = write_provider_qualification_definition(output, definition)
    print(
        json.dumps(
            {
                "definition_file_sha256": written.sha256,
                "definition_sha256": definition.definition_sha256,
            },
            sort_keys=True,
        )
    )
    return 0


def _relative_artifact_path(path: Path, root: Path) -> str:
    try:
        return path.resolve(strict=True).relative_to(root.resolve(strict=True)).as_posix()
    except ValueError:
        return path.name


def _port_open(port: int, *, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_for_outer(process: subprocess.Popen[bytes], *, deadline_ns: int) -> int:
    while time.perf_counter_ns() <= deadline_ns:
        if process.poll() is not None:
            raise ProviderQualificationError(
                "The provider process exited before its outer endpoint became ready."
            )
        if _port_open(8123):
            return time.perf_counter_ns()
        time.sleep(_PORT_POLL_SECONDS)
    raise ProviderQualificationError("The provider outer readiness deadline expired.")


def _probe_inner_without_credentials() -> int:
    connection = http.client.HTTPConnection("127.0.0.1", 8124, timeout=5.0)
    try:
        connection.request("GET", "/v1/models", headers={"accept": "application/json"})
        response = connection.getresponse()
        status = response.status
        response.read(4097)
        return status
    except (OSError, TimeoutError, http.client.HTTPException):
        raise ProviderQualificationError(
            "The unauthenticated inner-provider probe failed."
        ) from None
    finally:
        connection.close()


def _write_private_key(path: Path, key: bytes) -> None:
    descriptor = -1
    created = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        created = True
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise OSError
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(key)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        if created:
            path.unlink(missing_ok=True)
        raise ProviderQualificationError(
            "The temporary provider credential could not be created safely."
        ) from None


def _stop_process(
    process: subprocess.Popen[bytes],
) -> tuple[bool, int, bool, bool]:
    forced = False
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            forced = True
            process.kill()
            process.wait(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
    exit_code = process.returncode
    deadline = time.monotonic() + _PORT_CLOSE_TIMEOUT_SECONDS
    outer_closed = False
    inner_closed = False
    while time.monotonic() <= deadline:
        outer_closed = not _port_open(8123)
        inner_closed = not _port_open(8124)
        if outer_closed and inner_closed:
            break
        time.sleep(_PORT_POLL_SECONDS)
    return (
        (not forced and exit_code == 0 and outer_closed and inner_closed),
        (exit_code if exit_code is not None else -1),
        outer_closed,
        inner_closed,
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _definition_file_identity(path: Path, root: Path) -> QualificationFileIdentity:
    return qualification_file_identity(
        path,
        relative_path=_relative_artifact_path(path, root),
        maximum_bytes=512 * 1024,
    )


def _verify_definition_inputs(
    root: Path,
    definition: ProviderQualificationDefinition,
) -> tuple[LocalModelPolicyManifest, PromptPolicyManifest]:
    model, prompt, environment, model_file, prompt_file, environment_file = _load_policy_inputs(
        root, verify_environment=True
    )
    verify_provider_qualification_definition(
        definition,
        model_policy=model,
        prompt_policy=prompt,
        environment_manifest=environment,
        model_policy_file=model_file,
        prompt_policy_file=prompt_file,
        environment_manifest_file=environment_file,
        runner_file=_file_id(_paths(root)["runner"], _RUNNER_RELATIVE),
        qualification_module_file=_file_id(
            _paths(root)["qualification_module"], _QUALIFICATION_MODULE_RELATIVE
        ),
        project_root=root,
    )
    return model, prompt


def _run_definition(
    root: Path,
    *,
    definition_path: Path,
    approved_definition_sha256: str,
    approved_definition_file_sha256: str,
    output: Path,
) -> int:
    definition = load_provider_qualification_definition(
        definition_path,
        approved_definition_sha256=approved_definition_sha256,
        approved_file_sha256=approved_definition_file_sha256,
    )
    definition_file = _definition_file_identity(definition_path, root)
    if definition_file.sha256 != approved_definition_file_sha256:
        raise ProviderQualificationError("The definition changed after approval.")
    model, prompt = _verify_definition_inputs(root, definition)
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ProviderQualificationError("The provider qualification host is not Darwin/arm64.")
    if _port_open(8123) or _port_open(8124):
        raise ProviderQualificationError("A qualification provider port is already occupied.")

    paths = _paths(root)
    key_text = secrets.token_urlsafe(48)
    key_bytes = key_text.encode("ascii")
    process: subprocess.Popen[bytes] | None = None
    stopped: tuple[bool, int, bool, bool] | None = None
    started_at = _utc_now()
    start_ns = time.perf_counter_ns()
    try:
        with tempfile.TemporaryDirectory(prefix="eve-v0-provider-qualification-") as directory:
            key_path = Path(directory) / "provider.key"
            _write_private_key(key_path, key_bytes)
            environment = os.environ.copy()
            environment["EVE_RAG_LLM_API_KEY_FILE"] = str(key_path)
            process = subprocess.Popen(
                [str(paths["launcher"])],
                cwd=root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            ready_ns = _wait_for_outer(
                process,
                deadline_ns=start_ns + definition.pass_rule.startup_deadline_ns,
            )
            startup_duration_ns = ready_ns - start_ns
            inner_status = _probe_inner_without_credentials()
            if inner_status != definition.pass_rule.required_inner_unauthenticated_status:
                raise ProviderQualificationError(
                    "The inner provider accepted an unauthenticated readiness request."
                )
            provider = LocalOpenAICompatibleProvider(
                config=LocalProviderConfig(
                    base_url="http://127.0.0.1:8123",
                    artifact_root=paths["model_root"],
                    api_key=SecretStr(key_text),
                ),
                model_policy=model,
                prompt_policy=prompt,
            )
            ready = provider.check_ready()
            if not ready:
                raise ProviderQualificationError(
                    "The provider failed exact HMAC-attested readiness."
                )
            recording = RecordingGenerationProvider(provider)
            composer = GenerationComposer(
                provider=recording,
                expected_identity=model.provider_identity(prompt),
            )
            generation_start_ns = time.perf_counter_ns()
            composition = composer.compose(definition.synthetic_context)
            generation_duration_ns = time.perf_counter_ns() - generation_start_ns
            record = recording.record
            composition_sha, claim_count, citation_count, validation_scope = (
                composition_observation_fields(composition)
            )
            stopped = _stop_process(process)
            process = None
            clean, exit_code, outer_closed, inner_closed = stopped
            observation = ProviderQualificationObservation(
                started_at=started_at,
                completed_at=_utc_now(),
                host_operating_system=platform.system(),
                host_architecture=platform.machine(),
                startup_duration_ns=startup_duration_ns,
                generation_duration_ns=generation_duration_ns,
                outer_ready=True,
                inner_unauthenticated_status=inner_status,
                hmac_runtime_attestation_verified=ready,
                provider_check_ready=ready,
                generation_request_count=record.request_count,
                retry_count=0,
                context_self_sha256=definition.synthetic_context.context_sha256,
                context_canonical_sha256=record.context_canonical_sha256,
                provider_output_sha256=record.provider_output_sha256,
                provider_output_byte_size=record.provider_output_byte_size,
                composition_sha256=composition_sha,
                claim_count=claim_count,
                citation_count=citation_count,
                validation_scope=validation_scope,
                clean_shutdown=clean,
                process_exit_code=exit_code,
                outer_port_closed_after_shutdown=outer_closed,
                inner_port_closed_after_shutdown=inner_closed,
            )
    finally:
        if process is not None:
            _stop_process(process)

    report = build_provider_qualification_report(
        definition,
        definition_file=definition_file,
        observation=observation,
    )
    written = write_provider_qualification_report(output, report)
    print(
        json.dumps(
            {
                "report_file_sha256": written.sha256,
                "report_sha256": report.report_sha256,
            },
            sort_keys=True,
        )
    )
    return 0


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    definition = subparsers.add_parser(
        "build-definition", help="freeze the exact candidate and pass rules"
    )
    definition.add_argument("--output", type=Path, required=True)
    run = subparsers.add_parser("run", help="run one independently approved definition")
    run.add_argument("--definition", type=Path, required=True)
    run.add_argument("--approved-definition-sha256", required=True)
    run.add_argument("--approved-definition-file-sha256", required=True)
    run.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    try:
        root = _project_root()
        if arguments.command == "build-definition":
            return _build_definition(root, arguments.output)
        return _run_definition(
            root,
            definition_path=arguments.definition,
            approved_definition_sha256=arguments.approved_definition_sha256,
            approved_definition_file_sha256=arguments.approved_definition_file_sha256,
            output=arguments.output,
        )
    except Exception as error:
        safe_types = (
            ProviderQualificationError,
            GenerationPolicyError,
            GenerationComposerError,
            LocalProviderConfigurationError,
            LocalProviderRequestError,
            ProviderEnvironmentError,
        )
        safe_message = (
            str(error)
            if isinstance(error, safe_types)
            else "An unexpected qualification failure was suppressed."
        )
        print(
            f"The fixed V0 provider qualification failed safely "
            f"[{type(error).__name__}]: {safe_message}",
            file=sys.stderr,
        )
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
