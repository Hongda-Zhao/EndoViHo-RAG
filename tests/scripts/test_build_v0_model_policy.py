"""Atomic dual-output tests for the V0 model/prompt policy builder."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import build_v0_model_policy as builder


def test_dual_outputs_are_reserved_before_write_and_keep_canonical_newlines(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model = tmp_path / "model.json"
    prompt = tmp_path / "prompt.json"
    model_payload = b'{"model":true}'
    prompt_payload = b'{"prompt":true}\n'
    observed: list[tuple[int, int]] = []
    write_payload = builder._write_reserved_payload

    def inspect_reservations(descriptor: int, payload: bytes) -> None:
        assert model.is_file()
        assert prompt.is_file()
        assert stat.S_ISREG(model.stat().st_mode)
        assert stat.S_ISREG(prompt.stat().st_mode)
        if not observed:
            assert model.stat().st_size == 0
            assert prompt.stat().st_size == 0
        observed.append((descriptor, len(payload)))
        write_payload(descriptor, payload)

    monkeypatch.setattr(builder, "_write_reserved_payload", inspect_reservations)

    builder._write_new_outputs(((model, model_payload), (prompt, prompt_payload)))

    assert model.read_bytes() == model_payload
    assert not model.read_bytes().endswith(b"\n")
    assert prompt.read_bytes() == prompt_payload
    assert prompt.read_bytes().endswith(b"\n")
    assert not prompt.read_bytes().endswith(b"\n\n")
    assert len(observed) == 2


@pytest.mark.parametrize("existing_index", (0, 1))
def test_either_existing_target_prevents_every_new_output(
    tmp_path: Path,
    existing_index: int,
) -> None:
    paths = (tmp_path / "model.json", tmp_path / "prompt.json")
    paths[existing_index].write_bytes(b"preexisting")

    with pytest.raises(RuntimeError, match="already exists"):
        builder._write_new_outputs(
            ((paths[0], b'{"model":true}'), (paths[1], b'{"prompt":true}\n'))
        )

    assert paths[existing_index].read_bytes() == b"preexisting"
    assert not paths[1 - existing_index].exists()


def test_second_target_o_excl_race_rolls_back_first_without_touching_racer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model = tmp_path / "model.json"
    prompt = tmp_path / "prompt.json"
    real_open = os.open

    def race_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if path == prompt.name and flags & os.O_EXCL:
            prompt.write_bytes(b"racer-owned")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", race_open)

    with pytest.raises(RuntimeError, match="atomically"):
        builder._write_new_outputs(((model, b"model"), (prompt, b"prompt\n")))

    assert not model.exists()
    assert prompt.read_bytes() == b"racer-owned"


def test_second_payload_failure_removes_both_owned_outputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model = tmp_path / "model.json"
    prompt = tmp_path / "prompt.json"
    real_write = builder._write_reserved_payload
    calls = 0

    def fail_second(descriptor: int, payload: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            assert os.write(descriptor, payload[:3]) == 3
            raise OSError("synthetic second-write failure")
        real_write(descriptor, payload)

    monkeypatch.setattr(builder, "_write_reserved_payload", fail_second)

    with pytest.raises(RuntimeError, match="atomically"):
        builder._write_new_outputs(((model, b"model"), (prompt, b"prompt\n")))

    assert calls == 2
    assert not model.exists()
    assert not prompt.exists()


def test_prompt_output_main_writes_dual_physical_and_semantic_identities(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = tmp_path / "scripts" / "build_v0_model_policy.py"
    script.parent.mkdir()
    script.write_text("# test script identity\n", encoding="utf-8")
    python = tmp_path / ".artifacts" / "v0_activation" / "provider-env" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    model_output = tmp_path / "model-policy.json"
    prompt_output = tmp_path / "prompt-policy.json"
    prompt_policy = SimpleNamespace(manifest_sha256="a" * 64)
    model_policy = SimpleNamespace(manifest_sha256="b" * 64)
    environment = SimpleNamespace(
        semantic_sha256="c" * 64,
        distribution_count=3,
        file_count=9,
    )
    artifact = SimpleNamespace(
        relative_path="LICENSE.base-apache-2.0",
        sha256="d" * 64,
    )

    monkeypatch.setattr(builder, "__file__", str(script))
    monkeypatch.setattr(
        builder,
        "load_provider_environment_manifest",
        lambda _path: {"manifest_sha256": "e" * 64},
    )
    monkeypatch.setattr(
        builder,
        "verify_provider_environment_manifest",
        lambda _root, _manifest: environment,
    )
    monkeypatch.setattr(
        builder,
        "inventory_model_artifacts",
        lambda *_args, **_kwargs: (artifact,),
    )
    monkeypatch.setattr(builder, "build_approved_prompt_policy_manifest", lambda: prompt_policy)
    monkeypatch.setattr(
        builder, "build_local_model_policy_manifest", lambda **_kwargs: model_policy
    )
    monkeypatch.setattr(builder, "_sha256_file", lambda *_args, **_kwargs: "f" * 64)

    def canonical(value: object) -> str:
        if value is model_policy:
            return '{"model":true}'
        if value is prompt_policy:
            return '{"prompt":true}'
        raise AssertionError("unexpected policy object")

    monkeypatch.setattr(builder, "canonical_model_json", canonical)

    assert (
        builder.main(
            [
                "--output",
                str(model_output),
                "--prompt-output",
                str(prompt_output),
            ]
        )
        == 0
    )

    model_payload = b'{"model":true}'
    prompt_payload = b'{"prompt":true}\n'
    assert model_output.read_bytes() == model_payload
    assert prompt_output.read_bytes() == prompt_payload
    lines = set(capsys.readouterr().out.splitlines())
    assert f"manifest_sha256={'b' * 64}" in lines
    assert f"file_sha256={hashlib.sha256(model_payload).hexdigest()}" in lines
    assert f"prompt_manifest_sha256={'a' * 64}" in lines
    assert f"prompt_file_sha256={hashlib.sha256(prompt_payload).hexdigest()}" in lines
