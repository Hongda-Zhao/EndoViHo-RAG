"""No-egress local generation provider and policy-manifest tests."""

from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from eve_relation_rag.config.loopback import normalize_loopback_http_origin
from eve_relation_rag.generation.context import (
    build_literature_context,
    canonical_context_json,
)
from eve_relation_rag.generation.local_provider import (
    LocalOpenAICompatibleProvider,
    LocalProviderConfig,
    LocalProviderConfigurationError,
    LocalProviderRequestError,
)
from eve_relation_rag.generation.policy import (
    GenerationPolicyError,
    LocalModelPolicyManifest,
    build_approved_prompt_policy_manifest,
    build_local_model_policy_manifest,
    inventory_model_artifacts,
    load_local_model_policy_manifest,
    load_prompt_policy_manifest,
)
from eve_relation_rag.hybrid.contracts import canonical_model_json, canonical_self_sha256
from tests.support.m4 import make_generated_draft, make_retrieved_chunks

QUESTION = "Explain the literature evidence for the synthetic benchmark"
ARTIFACT_BYTES = b"tests-only-local-model-artifact"
SECRET = "tests-only-secret-must-not-leak-v0"
SECRET_VALUE = SecretStr(SECRET)


def _model_policy(tmp_path: Path) -> tuple[LocalModelPolicyManifest, Path]:
    artifact_root = tmp_path / "model"
    artifact_root.mkdir()
    artifact_path = artifact_root / "weights.bin"
    artifact_path.write_bytes(ARTIFACT_BYTES)
    license_path = artifact_root / "LICENSE"
    license_path.write_text("Tests-only Apache-2.0 license artifact.\n", encoding="utf-8")
    prompt = build_approved_prompt_policy_manifest()
    artifacts = inventory_model_artifacts(
        artifact_root,
        relative_paths=("weights.bin", "LICENSE"),
    )
    repository_revision = "1" * 40
    base_model_revision = "2" * 40
    license_artifact = next(
        artifact for artifact in artifacts if artifact.relative_path == "LICENSE"
    )
    return (
        build_local_model_policy_manifest(
            provider_key="provider:local-openai-compatible:v1",
            model_key="model:tests:v0",
            api_model_name="default_model",
            model_revision=repository_revision,
            repository_uri="https://huggingface.co/tests/model-v0",
            repository_revision=repository_revision,
            base_model_repository_uri="https://huggingface.co/tests/base-model-v0",
            base_model_key="model:tests:base-v0",
            base_model_revision=base_model_revision,
            artifacts=artifacts,
            license_key="Apache-2.0",
            license_artifact_relative_path="LICENSE",
            license_artifact_sha256=license_artifact.sha256,
            license_source_uri=(
                f"https://huggingface.co/tests/base-model-v0/blob/{base_model_revision}/LICENSE"
            ),
            inference_engine_key="engine:tests:v1",
            inference_engine_version="version:1.0.0",
            inference_engine_lock_sha256="3" * 64,
            inference_engine_wrapper_sha256="4" * 64,
            inference_engine_module_sha256="5" * 64,
            inference_python_executable_sha256="6" * 64,
            inference_python_configuration_sha256="e" * 64,
            provider_environment_verifier_sha256="f" * 64,
            provider_environment_manifest_sha256="b" * 64,
            provider_environment_sha256="c" * 64,
            provider_environment_distribution_count=3,
            provider_environment_file_count=100,
            runtime_launcher_sha256="7" * 64,
            runtime_proxy_sha256="8" * 64,
            egress_profile_sha256="9" * 64,
            sandbox_executable_sha256="a" * 64,
            environment_executable_sha256="d" * 64,
            runtime_distributions={
                "mlx": "0.32.2",
                "mlx-lm": "0.31.3",
                "mlx-metal": "0.32.2",
            },
            quantization="quantization:none",
            tokenizer_key="tokenizer:tests:v1",
            tokenizer_revision="revision:tests:v1",
            context_length_tokens=8192,
            seed_supported=True,
            seed=7,
            generation_policy_key="generation:json-zero-temp:v1",
            prompt_policy_manifest_sha256=prompt.manifest_sha256,
            max_output_tokens=1024,
            timeout_seconds=5,
        ),
        artifact_root,
    )


def _context_json() -> str:
    chunks = make_retrieved_chunks(
        question=QUESTION,
        text="The synthetic benchmark contains exact supporting evidence.",
    )
    return canonical_context_json(
        build_literature_context(original_question=QUESTION, retrieved_chunks=chunks)
    )


def _completion(context_json: str, *, model: str = "default_model") -> dict[str, object]:
    context_sha256 = json.loads(context_json)["context_sha256"]
    draft = make_generated_draft(
        context_sha256=context_sha256,
        claim_text="The synthetic benchmark contains exact supporting evidence.",
        citation_id="D1",
        evidence_quote="exact supporting evidence",
    )
    return {
        "id": "completion-tests-v0",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": canonical_model_json(draft)},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "prompt_tokens_details": {"cached_tokens": 0},
        },
    }


def _provider(
    tmp_path: Path,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    api_key: SecretStr | None = SECRET_VALUE,
) -> tuple[LocalOpenAICompatibleProvider, Path]:
    model, artifact_root = _model_policy(tmp_path)

    def authenticated_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == model.runtime_attestation_path:
            challenge = request.headers["x-v0-attestation-challenge"]
            body: dict[str, object] = {
                "attestation_schema_version": "v0-provider-runtime-attestation-v3",
                "challenge": challenge,
                "model_policy_manifest_sha256": model.manifest_sha256,
                "prompt_policy_manifest_sha256": model.prompt_policy_manifest_sha256,
                "inference_engine_lock_sha256": model.inference_engine_lock_sha256,
                "inference_engine_wrapper_sha256": model.inference_engine_wrapper_sha256,
                "inference_engine_module_sha256": model.inference_engine_module_sha256,
                "inference_python_executable_sha256": (
                    model.inference_python_executable_sha256
                ),
                "inference_python_configuration_sha256": (
                    model.inference_python_configuration_sha256
                ),
                "provider_environment_verifier_sha256": (
                    model.provider_environment_verifier_sha256
                ),
                "provider_environment_manifest_sha256": (
                    model.provider_environment_manifest_sha256
                ),
                "provider_environment_sha256": model.provider_environment_sha256,
                "provider_environment_distribution_count": (
                    model.provider_environment_distribution_count
                ),
                "provider_environment_file_count": model.provider_environment_file_count,
                "runtime_launcher_sha256": model.runtime_launcher_sha256,
                "runtime_proxy_sha256": model.runtime_proxy_sha256,
                "egress_profile_sha256": model.egress_profile_sha256,
                "sandbox_executable_sha256": model.sandbox_executable_sha256,
                "environment_executable_sha256": model.environment_executable_sha256,
                "runtime_distributions": model.runtime_distributions,
                "network_policy_key": model.network_policy_key,
                "environment_policy_key": model.environment_policy_key,
                "inner_authentication_key": model.inner_authentication_key,
                "egress_probe_key": model.egress_probe_key,
                "startup_warmup_key": model.startup_warmup_key,
                "startup_warmup_max_tokens": model.startup_warmup_max_tokens,
                "outer_port": model.outer_port,
                "inner_port": model.inner_port,
            }
            body["attestation_hmac_sha256"] = hmac.new(
                SECRET.encode(),
                canonical_model_json(body).encode(),
                hashlib.sha256,
            ).hexdigest()
            return httpx.Response(200, json=body)
        return handler(request)

    provider = LocalOpenAICompatibleProvider(
        config=LocalProviderConfig(
            base_url="http://127.0.0.1:8123",
            artifact_root=artifact_root,
            api_key=api_key,
        ),
        model_policy=model,
        prompt_policy=build_approved_prompt_policy_manifest(),
        transport=httpx.MockTransport(authenticated_handler),
    )
    return provider, artifact_root


def test_docs_style_model_builder_materializes_all_hashed_defaults(tmp_path: Path) -> None:
    model, _artifact_root = _model_policy(tmp_path)

    assert model.seed == 7
    assert model.endpoint_policy_key == "transport:loopback-openai-compatible-http-v1"
    assert model.chat_completions_path == "/v1/chat/completions"
    assert model.readiness_path == "/v1/models"
    assert model.response_format == "json_object"
    assert model.temperature == 0
    assert model.top_p == 1
    assert model.top_k == 0
    assert model.min_p == 0
    assert model.authentication_required is True
    assert model.runtime_attestation_path == "/v0/runtime-attestation"
    assert model.network_policy_key == "network:macos-sandbox-v0-ports-only-v2"
    assert model.environment_policy_key == "environment:scrubbed-allowlist-v1"
    assert model.inner_authentication_key == "authentication:inherited-fd-bearer-v1"
    assert model.egress_probe_key == (
        "egress-probe:external-and-unapproved-loopback-denied-v2"
    )
    assert model.startup_warmup_key == "warmup:nonfactual-one-token-v1"
    assert model.startup_warmup_max_tokens == 1
    assert model.outer_port == 8123
    assert model.inner_port == 8124
    assert model.max_output_bytes == 32768
    assert model.retry_count == 0
    assert model.max_concurrent_requests == 1
    assert model.prompt_concurrency == 1
    assert model.decode_concurrency == 1
    assert model.manifest_sha256 == canonical_self_sha256(model, "manifest_sha256")


@pytest.mark.parametrize(
    "value, expected",
    (
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
        ("http://[::1]:8000", "http://[::1]:8000"),
    ),
)
def test_loopback_origin_accepts_only_canonical_numeric_loopback(value: str, expected: str) -> None:
    assert normalize_loopback_http_origin(value) == expected


@pytest.mark.parametrize(
    "value",
    (
        "https://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.2:8000/v1",
        "http://127.0.0.1:8000?model=x",
        "http://user:password@127.0.0.1:8000",
        "http://192.0.2.1:8000",
    ),
)
def test_loopback_origin_rejects_nonlocal_or_ambiguous_origins(value: str) -> None:
    with pytest.raises(ValueError, match="numeric loopback"):
        normalize_loopback_http_origin(value)


def test_generation_uses_one_fixed_bounded_request_and_exact_identity(tmp_path: Path) -> None:
    context_json = _context_json()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url == "http://127.0.0.1:8123/v1/chat/completions"
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        assert request.headers["accept-encoding"] == "identity"
        payload = json.loads(request.content)
        prompt_policy = build_approved_prompt_policy_manifest()
        expected_system_prompt = (
            f"{prompt_policy.source_text}\n{prompt_policy.request_template_text}"
        )
        assert payload == {
            "max_tokens": 1024,
            "messages": [
                {"content": expected_system_prompt, "role": "system"},
                {"content": context_json, "role": "user"},
            ],
            "model": "default_model",
            "n": 1,
            "response_format": {"type": "json_object"},
            "seed": 7,
            "stream": False,
            "temperature": 0,
            "top_p": 1,
            "top_k": 0,
            "min_p": 0,
        }
        assert "tools" not in payload
        return httpx.Response(200, json=_completion(context_json))

    provider, _root = _provider(tmp_path, handler)

    output = provider.generate(context_json)

    assert len(requests) == 1
    assert json.loads(output)["context_sha256"] == json.loads(context_json)["context_sha256"]
    assert provider.identity.provider_artifact_sha256 is not None
    assert provider.identity.retry_count == 0


def test_readiness_requires_exact_model_and_rehashes_artifacts(tmp_path: Path) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {
                        "id": str(artifact_root.resolve()),
                        "object": "model",
                        "created": 1,
                    }
                ],
            },
        )

    provider, artifact_root = _provider(tmp_path, handler)
    assert provider.check_ready() is True
    (artifact_root / "weights.bin").write_bytes(b"tampered-after-readiness")
    assert provider.check_ready() is False
    assert calls == 1


def test_model_inventory_and_readiness_reject_unlisted_bytes(tmp_path: Path) -> None:
    inventory_root = tmp_path / "inventory"
    inventory_root.mkdir()
    (inventory_root / "weights.bin").write_bytes(ARTIFACT_BYTES)
    (inventory_root / "unexpected.txt").write_text("not approved\n", encoding="utf-8")
    with pytest.raises(GenerationPolicyError, match="inventory"):
        inventory_model_artifacts(inventory_root, relative_paths=("weights.bin",))

    provider_root = tmp_path / "provider"
    provider_root.mkdir()
    provider, artifact_root = _provider(
        provider_root,
        lambda _request: pytest.fail("unlisted model bytes must fail before transport"),
    )
    (artifact_root / "unexpected.txt").write_text("not approved\n", encoding="utf-8")
    assert provider.check_ready() is False


@pytest.mark.parametrize(
    "response",
    (
        httpx.Response(307, headers={"location": "http://192.0.2.1/steal"}),
        httpx.Response(200, headers={"content-type": "text/plain"}, content=b"{}"),
        httpx.Response(
            200,
            headers={"content-type": "application/json", "content-encoding": "gzip"},
            content=gzip.compress(b"{}"),
        ),
        httpx.Response(
            200,
            headers={"content-type": "application/json", "transfer-encoding": "chunked"},
            content=b"{}",
        ),
        httpx.Response(
            200,
            headers=[
                ("content-type", "application/json"),
                ("content-length", "2"),
                ("content-length", "2"),
            ],
            content=b"{}",
        ),
        httpx.Response(200, headers={"content-type": "application/json"}, content=b"x" * 131073),
    ),
)
def test_redirect_encoding_type_and_size_fail_closed(
    tmp_path: Path, response: httpx.Response
) -> None:
    provider, _root = _provider(
        tmp_path,
        lambda _request: response,
    )
    with pytest.raises(LocalProviderRequestError, match="unavailable"):
        provider.generate(_context_json())


def test_transport_errors_suppress_secret_and_context_payload(tmp_path: Path) -> None:
    context_json = _context_json()

    def handler(_request: httpx.Request) -> httpx.Response:
        raise RuntimeError(f"{SECRET}:{context_json}")

    provider, _root = _provider(tmp_path, handler)
    with pytest.raises(LocalProviderRequestError) as raised:
        provider.generate(context_json)

    rendered = "".join(traceback.format_exception(raised.value))
    assert SECRET not in str(raised.value)
    assert SECRET not in rendered
    assert context_json not in str(raised.value)
    assert context_json not in rendered
    assert raised.value.__cause__ is None


def test_process_local_provider_refuses_a_second_concurrent_request(tmp_path: Path) -> None:
    context_json = _context_json()
    entered = Event()
    release = Event()

    def handler(_request: httpx.Request) -> httpx.Response:
        entered.set()
        assert release.wait(timeout=5)
        return httpx.Response(200, json=_completion(context_json))

    provider, _root = _provider(tmp_path, handler)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(provider.generate, context_json)
        assert entered.wait(timeout=5)
        with pytest.raises(LocalProviderRequestError, match="already serving"):
            provider.generate(context_json)
        assert provider.check_ready() is False
        release.set()
        assert (
            json.loads(first.result())["context_sha256"]
            == json.loads(context_json)["context_sha256"]
        )


def test_policy_loaders_require_self_hash_and_independent_approval(tmp_path: Path) -> None:
    model, _root = _model_policy(tmp_path)
    prompt = build_approved_prompt_policy_manifest()
    model_path = tmp_path / "model-policy.json"
    prompt_path = tmp_path / "prompt-policy.json"
    model_path.write_text(canonical_model_json(model), encoding="utf-8")
    prompt_path.write_text(canonical_model_json(prompt), encoding="utf-8")

    assert (
        load_local_model_policy_manifest(
            model_path,
            approved_manifest_sha256=model.manifest_sha256,
        )
        == model
    )
    assert (
        load_prompt_policy_manifest(
            prompt_path,
            approved_manifest_sha256=prompt.manifest_sha256,
        )
        == prompt
    )
    with pytest.raises(GenerationPolicyError, match="approved checksum"):
        load_local_model_policy_manifest(
            model_path,
            approved_manifest_sha256="f" * 64,
        )

    tampered = json.loads(prompt_path.read_text(encoding="utf-8"))
    tampered["source_text"] = "tampered prompt"
    prompt_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(GenerationPolicyError, match="unavailable or invalid"):
        load_prompt_policy_manifest(
            prompt_path,
            approved_manifest_sha256=prompt.manifest_sha256,
        )


def test_model_policy_rejects_incoherent_seed_and_prompt_binding(tmp_path: Path) -> None:
    model, artifact_root = _model_policy(tmp_path)
    with pytest.raises(ValidationError, match="seed must be present"):
        LocalModelPolicyManifest.model_validate(
            model.model_dump(mode="python")
            | {"seed_supported": False, "manifest_sha256": model.manifest_sha256}
        )

    wrong_prompt = build_approved_prompt_policy_manifest().model_copy(
        update={"manifest_sha256": "f" * 64}
    )
    with pytest.raises(LocalProviderConfigurationError):
        LocalOpenAICompatibleProvider(
            config=LocalProviderConfig(
                base_url="http://127.0.0.1:8123",
                artifact_root=artifact_root,
            ),
            model_policy=model,
            prompt_policy=wrong_prompt,
        )
