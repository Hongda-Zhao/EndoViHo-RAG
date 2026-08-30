"""Focused security tests for the V0 provider's two-process boundary."""

from __future__ import annotations

import argparse
import errno
import hashlib
import importlib.util
import json
import os
import sys
from email.message import Message
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from threading import Lock
from types import MethodType, ModuleType, SimpleNamespace

import pytest

from scripts import v0_provider_proxy as proxy


def _artifact(path: Path, root: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "byte_size": len(payload),
        "relative_path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def test_model_inventory_rejects_extra_files_and_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "model"
    root.mkdir()
    approved = root / "approved.bin"
    approved.write_bytes(b"approved")
    policy = {"artifacts": [_artifact(approved, root)]}

    assert proxy._verify_model_artifacts(policy, root) == root.resolve()
    extra = root / "extra.bin"
    extra.write_bytes(b"extra")
    with pytest.raises(proxy.RuntimeVerificationError, match="not exact"):
        proxy._verify_model_artifacts(policy, root)
    extra.unlink()
    (root / "link.bin").symlink_to(approved)
    with pytest.raises(proxy.RuntimeVerificationError, match="symlink"):
        proxy._verify_model_artifacts(policy, root)


def test_engine_command_uses_verified_interpreter_isolation_and_inherited_fd(
    tmp_path: Path,
) -> None:
    arguments = argparse.Namespace(
        python_executable=tmp_path / "provider-env" / "bin" / "python",
        engine_wrapper=tmp_path / "v0_mlx_authenticated_server.py",
        model_dir=tmp_path / "model",
    )

    command = proxy._engine_command(arguments, inner_key_descriptor=17)

    assert command[:4] == [
        str(arguments.python_executable),
        "-B",
        "-I",
        str(arguments.engine_wrapper),
    ]
    assert command[-2:] == ["--inner-key-fd", "17"]
    assert not any("Bearer" in argument for argument in command)


def test_inner_wrapper_requires_fd_key_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mlx = ModuleType("mlx")
    fake_core = ModuleType("mlx.core")
    fake_mlx.core = fake_core  # type: ignore[attr-defined]
    fake_mlx_lm = ModuleType("mlx_lm")
    fake_server = ModuleType("mlx_lm.server")
    fake_server.APIHandler = object  # type: ignore[attr-defined]
    fake_mlx_lm.server = fake_server  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlx", fake_mlx)
    monkeypatch.setitem(sys.modules, "mlx.core", fake_core)
    monkeypatch.setitem(sys.modules, "mlx_lm", fake_mlx_lm)
    monkeypatch.setitem(sys.modules, "mlx_lm.server", fake_server)
    wrapper_path = Path(proxy.__file__).with_name("v0_mlx_authenticated_server.py")
    specification = importlib.util.spec_from_file_location(
        "_v0_authenticated_wrapper_test", wrapper_path
    )
    assert specification is not None and specification.loader is not None
    wrapper = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(wrapper)

    read_descriptor, write_descriptor = os.pipe()
    os.write(write_descriptor, b"i" * 32)
    os.close(write_descriptor)
    inner_key = wrapper._read_inner_key(read_descriptor)
    wrapper.AuthenticatedAPIHandler.inner_api_key = inner_key
    handler = object.__new__(wrapper.AuthenticatedAPIHandler)
    handler.headers = Message()
    assert handler._authorized() is False
    handler.headers["authorization"] = f"Bearer {(b'i' * 32).hex()}"
    assert handler._authorized() is True


def test_environment_allowlist_is_exact(tmp_path: Path) -> None:
    environment = dict(proxy._FIXED_ENVIRONMENT)
    for key in proxy._PATH_ENVIRONMENT_KEYS:
        path = tmp_path / key.lower()
        path.mkdir()
        environment[key] = str(path)

    assert proxy._verify_scrubbed_environment(environment) == environment
    environment[proxy._APPLE_TEXT_ENCODING_KEY] = f"0x{proxy.os.getuid():X}:0x0:0x0"
    assert proxy._verify_scrubbed_environment(environment) == {
        key: value
        for key, value in environment.items()
        if key != proxy._APPLE_TEXT_ENCODING_KEY
    }
    environment[proxy._APPLE_TEXT_ENCODING_KEY] = "0x0:0x0:0x0"
    with pytest.raises(proxy.RuntimeVerificationError, match="allowlist"):
        proxy._verify_scrubbed_environment(environment)
    environment[proxy._APPLE_TEXT_ENCODING_KEY] = f"0x{proxy.os.getuid():X}:0x0:0x0"
    environment["PATH"] = "/unapproved"
    with pytest.raises(proxy.RuntimeVerificationError, match="allowlist"):
        proxy._verify_scrubbed_environment(environment)


class _ProbeSocket:
    def __init__(self, result: int) -> None:
        self.result = result

    def __enter__(self) -> _ProbeSocket:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def settimeout(self, _timeout: float) -> None:
        return None

    def connect_ex(self, _address: object) -> int:
        return self.result

    def bind(self, _address: tuple[str, int]) -> None:
        return


def test_network_probe_requires_external_udp_tcp_and_unapproved_loopback_denial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sockets = iter(
        [
            _ProbeSocket(errno.EPERM),
            _ProbeSocket(errno.EACCES),
            _ProbeSocket(errno.EPERM),
            _ProbeSocket(0),
            _ProbeSocket(0),
        ]
    )
    monkeypatch.setattr(proxy.socket, "has_ipv6", False)
    monkeypatch.setattr(proxy.socket, "socket", lambda *_args: next(sockets))

    assert (
        proxy._verify_network_sandbox() == "egress-probe:external-and-unapproved-loopback-denied-v2"
    )


def test_network_probe_rejects_connection_refused_as_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(proxy.socket, "has_ipv6", False)
    monkeypatch.setattr(
        proxy.socket,
        "socket",
        lambda *_args: _ProbeSocket(errno.ECONNREFUSED),
    )

    with pytest.raises(proxy.RuntimeVerificationError, match="not enforced"):
        proxy._verify_network_sandbox()


class _FakeSocket:
    def settimeout(self, _timeout: float) -> None:
        return


class _FakeResponse:
    def __init__(self) -> None:
        self.status = HTTPStatus.OK
        self.version = 11
        self._payload = b'{"choices":[{"finish_reason":"length","index":0}]}'
        self.headers = Message()
        self.headers["content-type"] = "application/json"
        self.headers["content-length"] = str(len(self._payload))

    def read(self, _limit: int) -> bytes:
        return self._payload

    def getheader(self, name: str, default: str = "") -> str:
        value = self.headers.get(name)
        return default if value is None else value


class _FakeConnection:
    observed_headers: dict[str, str] = {}
    observed_body: dict[str, object] = {}

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.sock = _FakeSocket()

    def request(
        self,
        _method: str,
        _path: str,
        *,
        body: bytes,
        headers: dict[str, str],
    ) -> None:
        type(self).observed_headers = headers
        type(self).observed_body = json.loads(body)

    def getresponse(self) -> _FakeResponse:
        return _FakeResponse()

    def close(self) -> None:
        return


def test_startup_warmup_is_fixed_nonfactual_authenticated_and_one_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(proxy.http.client, "HTTPConnection", _FakeConnection)
    proxy._warm_engine(
        inner_port=8124,
        inner_api_key=b"k" * 32,
        model_name="default_model",
        deadline=__import__("time").monotonic() + 5,
    )

    assert _FakeConnection.observed_headers["authorization"] == (f"Bearer {(b'k' * 32).hex()}")
    assert _FakeConnection.observed_body["max_tokens"] == 1
    assert _FakeConnection.observed_body["temperature"] == 0
    assert _FakeConnection.observed_body["messages"] == [
        {"content": proxy._WARMUP_SYSTEM_TEXT, "role": "system"},
        {"content": proxy._WARMUP_USER_TEXT, "role": "user"},
    ]


def test_outer_generation_lock_returns_429_before_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(proxy, "_validate_chat_request", lambda *_args, **_kwargs: None)
    lock = Lock()
    lock.acquire()
    server = SimpleNamespace(
        api_key=b"a" * 32,
        generation_lock=lock,
        policy={},
        prompt_policy={},
    )
    headers = Message()
    headers["authorization"] = f"Bearer {'a' * 32}"
    headers["content-type"] = "application/json"
    headers["content-length"] = "2"
    handler = object.__new__(proxy._ProxyHandler)
    handler.server = server
    handler.headers = headers
    handler.path = "/v1/chat/completions"
    handler.rfile = BytesIO(b"{}")
    observed: list[HTTPStatus] = []

    def capture_error(_self: object, status: HTTPStatus) -> None:
        observed.append(status)

    handler._error = MethodType(capture_error, handler)
    handler.do_POST()
    lock.release()

    assert observed == [HTTPStatus.TOO_MANY_REQUESTS]


def test_monotonic_deadline_cannot_be_reused_after_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = iter((10.0, 10.5, 11.1))
    monkeypatch.setattr(proxy.time, "monotonic", lambda: next(ticks))
    deadline = proxy.time.monotonic() + 1.0

    assert proxy._remaining_seconds(deadline) == pytest.approx(0.5)
    with pytest.raises(TimeoutError):
        proxy._remaining_seconds(deadline)
