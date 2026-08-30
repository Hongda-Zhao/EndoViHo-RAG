#!/usr/bin/env python3
"""Build the exact V0 local-model policy candidate without replacing prior evidence."""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from eve_relation_rag.generation.policy import (
    build_approved_prompt_policy_manifest,
    build_local_model_policy_manifest,
    inventory_model_artifacts,
)
from eve_relation_rag.hybrid.contracts import canonical_model_json

if TYPE_CHECKING:
    from scripts.v0_provider_environment import (
        load_provider_environment_manifest,
        verify_provider_environment_manifest,
    )
elif __package__:
    from scripts.v0_provider_environment import (
        load_provider_environment_manifest,
        verify_provider_environment_manifest,
    )
else:  # pragma: no cover - exercised by the documented direct-script invocation.
    from v0_provider_environment import (
        load_provider_environment_manifest,
        verify_provider_environment_manifest,
    )

_QUANTIZED_REVISION = "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b"
_BASE_MODEL_REVISION = "cdbee75f17c01a7cc42f958dc650907174af0554"
_MODEL_RELATIVE_PATHS = (
    ".gitattributes",
    "LICENSE.base-apache-2.0",
    "README.md",
    "added_tokens.json",
    "chat_template.jinja",
    "config.json",
    "generation_config.json",
    "merges.txt",
    "model.safetensors",
    "model.safetensors.index.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
)


@dataclass(frozen=True, slots=True)
class _PreparedOutputTarget:
    """One absent basename anchored to an already opened real directory."""

    path: Path
    basename: str
    parent_descriptor: int
    parent_device: int
    parent_inode: int


@dataclass(frozen=True, slots=True)
class _ReservedOutput:
    """One O_EXCL-created regular file owned by the current write transaction."""

    target: _PreparedOutputTarget
    descriptor: int
    device: int
    inode: int


def _sha256_file(path: Path, *, executable: bool = False) -> str:
    if path.is_symlink() or not path.is_file() or (executable and not os.access(path, os.X_OK)):
        raise RuntimeError("an exact V0 runtime artifact is unavailable")
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        os.close(descriptor)
        raise RuntimeError("an exact V0 runtime artifact is unavailable")
    with os.fdopen(descriptor, "rb") as stream:
        while chunk := stream.read(1024 * 1024):
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
        raise RuntimeError("an exact V0 runtime artifact changed while hashing")
    return digest.hexdigest()


def _close_prepared_targets(targets: Sequence[_PreparedOutputTarget]) -> None:
    for target in targets:
        try:
            os.close(target.parent_descriptor)
        except OSError:
            pass


def _prepare_new_output_targets(paths: Sequence[Path]) -> tuple[_PreparedOutputTarget, ...]:
    if not paths:
        raise RuntimeError("at least one candidate output is required")
    targets: list[_PreparedOutputTarget] = []
    try:
        for path in paths:
            if not path.name or any(part == ".." for part in path.parts):
                raise RuntimeError("the candidate output path is unsafe")
            parent = path.parent
            resolved_parent = parent.resolve(strict=True)
            if parent.absolute() != resolved_parent:
                raise RuntimeError("the candidate output directory is unsafe")
            directory_flags = (
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            parent_descriptor = os.open(resolved_parent, directory_flags)
            try:
                parent_stat = os.fstat(parent_descriptor)
                if not stat.S_ISDIR(parent_stat.st_mode):
                    raise RuntimeError("the candidate output directory is unavailable")
                try:
                    os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise RuntimeError("the candidate output already exists")
            except Exception:
                try:
                    os.close(parent_descriptor)
                except OSError:
                    pass
                raise
            targets.append(
                _PreparedOutputTarget(
                    path=path,
                    basename=path.name,
                    parent_descriptor=parent_descriptor,
                    parent_device=parent_stat.st_dev,
                    parent_inode=parent_stat.st_ino,
                )
            )
        identities = tuple(
            (target.parent_device, target.parent_inode, target.basename) for target in targets
        )
        if len(identities) != len(set(identities)):
            raise RuntimeError("candidate output paths must be distinct")
        return tuple(targets)
    except Exception:
        _close_prepared_targets(targets)
        raise


def _validate_new_output_targets(paths: Sequence[Path]) -> None:
    try:
        targets = _prepare_new_output_targets(paths)
    except OSError:
        raise RuntimeError("the candidate output directory is unavailable") from None
    _close_prepared_targets(targets)


def _write_reserved_payload(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("candidate output write made no progress")
        remaining = remaining[written:]
    os.fsync(descriptor)


def _remove_owned_output(output: _ReservedOutput) -> None:
    try:
        observed = os.stat(
            output.target.basename,
            dir_fd=output.target.parent_descriptor,
            follow_symlinks=False,
        )
        if observed.st_dev == output.device and observed.st_ino == output.inode:
            os.unlink(output.target.basename, dir_fd=output.target.parent_descriptor)
    except OSError:
        pass


def _write_new_outputs(outputs: Sequence[tuple[Path, bytes]]) -> None:
    paths = tuple(path for path, _payload in outputs)
    try:
        targets = _prepare_new_output_targets(paths)
    except OSError:
        raise RuntimeError("the candidate output directory is unavailable") from None
    reservations: list[_ReservedOutput] = []
    try:
        creation_flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        for target in targets:
            descriptor = os.open(
                target.basename,
                creation_flags,
                0o644,
                dir_fd=target.parent_descriptor,
            )
            observed = os.fstat(descriptor)
            reservation = _ReservedOutput(
                target=target,
                descriptor=descriptor,
                device=observed.st_dev,
                inode=observed.st_ino,
            )
            reservations.append(reservation)
            if (
                not stat.S_ISREG(observed.st_mode)
                or observed.st_nlink != 1
                or observed.st_size != 0
            ):
                raise OSError("candidate output reservation is not a new regular file")
        for reservation, (_path, payload) in zip(reservations, outputs, strict=True):
            _write_reserved_payload(reservation.descriptor, payload)
    except Exception:
        for reservation in reversed(reservations):
            _remove_owned_output(reservation)
        raise RuntimeError("the candidate outputs could not be created atomically") from None
    finally:
        for reservation in reservations:
            try:
                os.close(reservation.descriptor)
            except OSError:
                pass
        _close_prepared_targets(targets)


def _write_new(path: Path, payload: bytes) -> None:
    """Compatibility wrapper for one create-only candidate output."""

    _write_new_outputs(((path, payload),))


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--prompt-output",
        type=Path,
        help="optional new canonical prompt-policy artifact bound by the model policy",
    )
    parser.add_argument("--provider-environment-manifest", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    output_paths = (arguments.output,) + (
        (arguments.prompt_output,) if arguments.prompt_output is not None else ()
    )
    _validate_new_output_targets(output_paths)
    project_root = Path(__file__).resolve(strict=True).parents[1]
    activation_root = project_root / ".artifacts" / "v0_activation"
    model_root = activation_root / "model" / "Qwen3-4B-Instruct-2507-4bit"
    provider_root = activation_root / "provider-env"
    python_link = provider_root / "bin" / "python"
    python_executable = python_link.resolve(strict=True)
    provider_environment_manifest_path = (
        arguments.provider_environment_manifest
        if arguments.provider_environment_manifest is not None
        else activation_root / "manifests" / "v0_provider_environment_manifest.json"
    )
    provider_environment_manifest = load_provider_environment_manifest(
        provider_environment_manifest_path
    )
    provider_environment_identity = verify_provider_environment_manifest(
        provider_root,
        provider_environment_manifest,
    )
    runtime_paths = {
        "engine_lock": project_root / "config" / "v0-provider-requirements.lock",
        "engine_wrapper": project_root / "scripts" / "v0_mlx_authenticated_server.py",
        "engine_module": (
            provider_root / "lib" / "python3.12" / "site-packages" / "mlx_lm" / "server.py"
        ),
        "python_configuration": provider_root / "pyvenv.cfg",
        "provider_environment_verifier": (project_root / "scripts" / "v0_provider_environment.py"),
        "provider_environment_manifest": provider_environment_manifest_path,
        "runtime_launcher": project_root / "scripts" / "run_v0_local_provider.sh",
        "runtime_proxy": project_root / "scripts" / "v0_provider_proxy.py",
        "egress_profile": project_root / "scripts" / "v0_provider_loopback.sb",
        "sandbox_executable": Path("/usr/bin/sandbox-exec"),
        "environment_executable": Path("/usr/bin/env"),
    }
    artifacts = inventory_model_artifacts(
        model_root,
        relative_paths=_MODEL_RELATIVE_PATHS,
    )
    license_artifact = next(
        item for item in artifacts if item.relative_path == "LICENSE.base-apache-2.0"
    )
    prompt_policy = build_approved_prompt_policy_manifest()
    manifest = build_local_model_policy_manifest(
        provider_key="provider:local-openai-compatible:v1",
        model_key="model:hf:mlx-community:Qwen3-4B-Instruct-2507-4bit",
        api_model_name="default_model",
        model_revision=_QUANTIZED_REVISION,
        repository_uri=("https://huggingface.co/mlx-community/Qwen3-4B-Instruct-2507-4bit"),
        repository_revision=_QUANTIZED_REVISION,
        base_model_repository_uri="https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507",
        base_model_key="model:hf:Qwen:Qwen3-4B-Instruct-2507",
        base_model_revision=_BASE_MODEL_REVISION,
        artifacts=artifacts,
        license_key="Apache-2.0",
        license_artifact_relative_path="LICENSE.base-apache-2.0",
        license_artifact_sha256=license_artifact.sha256,
        license_source_uri=(
            "https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507/resolve/"
            f"{_BASE_MODEL_REVISION}/LICENSE"
        ),
        inference_engine_key="engine:mlx-lm",
        inference_engine_version="0.31.3+mlx-0.32.2+mlx-metal-0.32.2",
        inference_engine_lock_sha256=_sha256_file(runtime_paths["engine_lock"]),
        inference_engine_wrapper_sha256=_sha256_file(
            runtime_paths["engine_wrapper"], executable=True
        ),
        inference_engine_module_sha256=_sha256_file(runtime_paths["engine_module"]),
        inference_python_executable_sha256=_sha256_file(python_executable, executable=True),
        inference_python_configuration_sha256=_sha256_file(runtime_paths["python_configuration"]),
        provider_environment_verifier_sha256=_sha256_file(
            runtime_paths["provider_environment_verifier"]
        ),
        provider_environment_manifest_sha256=str(provider_environment_manifest["manifest_sha256"]),
        provider_environment_sha256=str(provider_environment_identity.semantic_sha256),
        provider_environment_distribution_count=(provider_environment_identity.distribution_count),
        provider_environment_file_count=provider_environment_identity.file_count,
        runtime_launcher_sha256=_sha256_file(runtime_paths["runtime_launcher"], executable=True),
        runtime_proxy_sha256=_sha256_file(runtime_paths["runtime_proxy"], executable=True),
        egress_profile_sha256=_sha256_file(runtime_paths["egress_profile"]),
        sandbox_executable_sha256=_sha256_file(
            runtime_paths["sandbox_executable"], executable=True
        ),
        environment_executable_sha256=_sha256_file(
            runtime_paths["environment_executable"], executable=True
        ),
        runtime_distributions={
            "mlx": "0.32.2",
            "mlx-lm": "0.31.3",
            "mlx-metal": "0.32.2",
        },
        quantization="mlx:4bit:g64:converted-by-mlx-lm-0.26.2",
        tokenizer_key="tokenizer:hf:Qwen3-4B-Instruct-2507",
        tokenizer_revision=_QUANTIZED_REVISION,
        context_length_tokens=262_144,
        seed_supported=True,
        seed=0,
        generation_policy_key="generation:v0:json-temp0-seed0-single-request",
        prompt_policy_manifest_sha256=prompt_policy.manifest_sha256,
        max_output_tokens=256,
        timeout_seconds=300,
    )
    payload = canonical_model_json(manifest).encode("utf-8")
    prompt_payload = (
        (canonical_model_json(prompt_policy) + "\n").encode("utf-8")
        if arguments.prompt_output is not None
        else None
    )
    outputs = ((arguments.output, payload),) + (
        ((arguments.prompt_output, prompt_payload),)
        if arguments.prompt_output is not None and prompt_payload is not None
        else ()
    )
    _write_new_outputs(outputs)
    print(f"manifest_sha256={manifest.manifest_sha256}")
    print(f"file_sha256={hashlib.sha256(payload).hexdigest()}")
    if arguments.prompt_output is not None and prompt_payload is not None:
        print(f"prompt_manifest_sha256={prompt_policy.manifest_sha256}")
        print(f"prompt_file_sha256={hashlib.sha256(prompt_payload).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
