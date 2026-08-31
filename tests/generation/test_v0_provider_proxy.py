"""Security-boundary tests for the sandboxed V0 provider proxy."""

from __future__ import annotations

import errno
import hashlib
import json
from pathlib import Path

import pytest

from eve_relation_rag.generation.context import (
    build_literature_context,
    canonical_context_json,
)
from eve_relation_rag.generation.policy import build_approved_prompt_policy_manifest
from eve_relation_rag.hybrid.contracts import canonical_model_json
from scripts import v0_provider_proxy as proxy
from tests.support.m4 import make_retrieved_chunks


def _request_fixture() -> tuple[bytes, dict[str, object], dict[str, object]]:
    question = "Explain the literature evidence for the synthetic benchmark"
    context = build_literature_context(
        original_question=question,
        retrieved_chunks=make_retrieved_chunks(
            question=question,
            text="The synthetic benchmark contains exact supporting evidence.",
        ),
    )
    prompt = build_approved_prompt_policy_manifest().model_dump(mode="json")
    model: dict[str, object] = {
        "api_model_name": "default_model",
        "temperature": 0,
        "top_p": 1,
        "top_k": 0,
        "min_p": 0,
        "max_output_tokens": 256,
        "seed": 0,
    }
    payload = {
        "model": "default_model",
        "messages": [
            {
                "role": "system",
                "content": f"{prompt['source_text']}\n{prompt['request_template_text']}",
            },
            {"role": "user", "content": canonical_context_json(context)},
        ],
        "temperature": 0,
        "top_p": 1,
        "top_k": 0,
        "min_p": 0,
        "n": 1,
        "stream": False,
        "max_tokens": 256,
        "seed": 0,
        "response_format": {"type": "json_object"},
    }
    return canonical_model_json(payload).encode("utf-8"), model, prompt


def test_proxy_accepts_only_the_exact_canonical_v0_request() -> None:
    body, model, prompt = _request_fixture()

    proxy._validate_chat_request(
        body,
        model_policy=model,
        prompt_policy=prompt,
    )


@pytest.mark.parametrize("tamper", ("sampling", "system", "context", "extra", "encoding"))
def test_proxy_rejects_request_policy_drift(tamper: str) -> None:
    body, model, prompt = _request_fixture()
    request = json.loads(body)
    if tamper == "sampling":
        request["top_p"] = 0.9
    elif tamper == "system":
        request["messages"][0]["content"] = "replacement system prompt"
    elif tamper == "context":
        context = json.loads(request["messages"][1]["content"])
        context["original_question"] = "A different question"
        request["messages"][1]["content"] = canonical_model_json(context)
    elif tamper == "extra":
        request["user"] = "unexpected"
    encoded = canonical_model_json(request).encode("utf-8")
    if tamper == "encoding":
        encoded = json.dumps(request, ensure_ascii=False).encode("utf-8")

    with pytest.raises(proxy.RuntimeVerificationError):
        proxy._validate_chat_request(
            encoded,
            model_policy=model,
            prompt_policy=prompt,
        )


def test_proxy_key_reader_accepts_one_generator_newline_and_requires_private_mode(
    tmp_path: Path,
) -> None:
    path = tmp_path / "provider-key"
    path.write_bytes(b"a" * 64 + b"\n")
    path.chmod(0o600)

    assert proxy._read_api_key(path) == b"a" * 64
    path.chmod(0o644)
    with pytest.raises(proxy.RuntimeVerificationError, match="permissions"):
        proxy._read_api_key(path)


def test_proxy_rehashes_the_complete_model_inventory(tmp_path: Path) -> None:
    root = tmp_path / "model"
    root.mkdir()
    first = root / "a.bin"
    second = root / "b.bin"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    policy = {
        "artifacts": [
            {
                "relative_path": path.name,
                "byte_size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in (first, second)
        ]
    }

    assert proxy._verify_model_artifacts(policy, root) == root.resolve()
    second.write_bytes(b"tampered")
    with pytest.raises(proxy.RuntimeVerificationError, match="size"):
        proxy._verify_model_artifacts(policy, root)


class _ProbeSocket:
    def __init__(self, *, connect_result: int) -> None:
        self.connect_result = connect_result
        self.bound = False

    def __enter__(self) -> _ProbeSocket:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def settimeout(self, _timeout: float) -> None:
        return None

    def connect_ex(self, _address: tuple[str, int]) -> int:
        return self.connect_result

    def bind(self, _address: tuple[str, int]) -> None:
        self.bound = True


def test_proxy_attests_only_an_os_permission_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    sockets = iter(
        (
            _ProbeSocket(connect_result=errno.EPERM),
            _ProbeSocket(connect_result=errno.EACCES),
            _ProbeSocket(connect_result=errno.EPERM),
            _ProbeSocket(connect_result=errno.EACCES),
            _ProbeSocket(connect_result=0),
            _ProbeSocket(connect_result=0),
        )
    )
    monkeypatch.setattr(proxy.socket, "has_ipv6", True)
    monkeypatch.setattr(proxy.socket, "socket", lambda *_args: next(sockets))

    assert proxy._verify_network_sandbox() == (
        "egress-probe:external-and-unapproved-loopback-denied-v2"
    )


def test_proxy_rejects_an_ordinary_network_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proxy.socket, "has_ipv6", False)
    monkeypatch.setattr(
        proxy.socket,
        "socket",
        lambda *_args: _ProbeSocket(connect_result=errno.ECONNREFUSED),
    )

    with pytest.raises(proxy.RuntimeVerificationError, match="not enforced"):
        proxy._verify_network_sandbox()
