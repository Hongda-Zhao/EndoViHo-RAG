"""Rehearsal isolation and accounting tests; no scientific provider authority or model loading."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from eve_relation_rag.experiments.rag_value_ablation import local_generation as local
from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    EvaluationAnswer,
    build_evidence_pack,
)
from eve_relation_rag.experiments.rag_value_ablation.prompting import build_prompt_policy
from eve_relation_rag.experiments.rag_value_ablation.runner import generate_prepared_rehearsal
from eve_relation_rag.generation.policy import build_local_model_policy_manifest
from eve_relation_rag.literature.hashing import canonical_json_bytes
from tests.generation.test_local_provider_v0 import _model_policy


@pytest.fixture
def config(tmp_path):
    policy, root = _model_policy(tmp_path)
    policy = build_local_model_policy_manifest(
        **{
            **policy.model_dump(
                mode="python", exclude={"manifest_schema_version", "manifest_sha256"}
            ),
            "model_revision": local.MODEL_REVISION,
            "repository_revision": local.MODEL_REVISION,
            "repository_uri": f"https://huggingface.co/{local.MODEL_ID}",
        }
    )
    path = tmp_path / "model-policy.json"
    raw = canonical_json_bytes(policy)
    path.write_bytes(raw)
    worker = tmp_path / "synthetic_worker.py"
    worker.write_text("# tests-only worker identity, never executed\n")
    return local.LocalGenerationConfig(
        model_root=root,
        model_policy_path=path,
        model_policy_file_sha256=hashlib.sha256(raw).hexdigest(),
        python_executable=Path(__file__),
        worker_script=worker,
    )


def pack(**changes):
    values = dict(
        question_id="tests-only",
        question_text="What is supported by the evidence?",
        policy_sha256=build_prompt_policy().policy_sha256,
        tokenizer_key=local.TOKENIZER_KEY,
        model_context_limit_tokens=32768,
        reserved_output_tokens=8192,
        input_token_count=100,
        context_token_count=20,
    )
    return build_evidence_pack(**{**values, **changes})


def test_generation_identity_uses_complete_shared_settings_and_no_trust(config):
    with local.OfflineMlxGenerationProvider(config) as provider:
        assert provider.identity.provider_kind == "unverified"
        assert provider.identity.seed == 0
        assert provider.identity.max_output_tokens == 8192
        assert provider.identity.context_limit_tokens == 32768
        assert provider._process is None  # Constructing the adapter does not load a model.


@pytest.mark.parametrize("mutation", ["file", "unlisted", "manifest", "symlink"])
def test_verified_generation_inventory_rejects_drift(config, mutation):
    local.verify_generation_assets(config)
    weights = config.model_root / "weights.bin"
    if mutation == "file":
        weights.write_bytes(b"drift")
    elif mutation == "unlisted":
        (config.model_root / "hidden.txt").write_text("unlisted")
    elif mutation == "manifest":
        config.model_policy_path.write_bytes(b"{}")
    else:
        data = weights.read_bytes()
        weights.unlink()
        external = config.model_root.parent / "external.bin"
        external.write_bytes(data)
        weights.symlink_to(external)
    with pytest.raises((ValueError, RuntimeError)):
        local.verify_generation_assets(config)


def test_actual_chat_count_is_required_before_generation(config, monkeypatch):
    with local.OfflineMlxGenerationProvider(config) as provider:
        calls = []

        def exchange(operation, evidence):
            calls.append(operation)
            return {"input_tokens": 101, "context_tokens": 20}

        monkeypatch.setattr(provider, "_exchange", exchange)
        with pytest.raises(local.LocalGenerationError, match="actual tokenizer"):
            provider.generate(pack())
        assert calls == ["count"]


def test_overflow_preserves_failure_count_and_never_calls_generate():
    calls = []
    provider = SimpleNamespace(measure=lambda value: calls.append(value) or (24577, 24000))
    with pytest.raises(local.ContextOverflow) as exc:
        local.build_measured_evidence(
            provider,
            question_id="tests-only",
            question_text="What is supported?",
            policy_sha256=build_prompt_policy().policy_sha256,
        )
    assert exc.value.input_token_count == 24577
    assert len(calls) == 1


def test_scope_refusal_has_zero_provider_calls():
    class NoProvider:
        def __getattr__(self, name):
            raise AssertionError("scope refusal touched provider")

    result = generate_prepared_rehearsal(
        NoProvider(), "S0", pack(question_text="Infer current infection from this record.")
    )
    assert result["status"] == "scope_refused"
    assert result["generation_executed"] is False


@pytest.mark.parametrize(
    "response_kind", ["valid", "invalid_json", "unknown_citation", "invented_identifier", "length"]
)
def test_real_response_uses_common_mechanical_validation(config, monkeypatch, response_kind):
    answer = EvaluationAnswer(
        answer_text="Insufficient evidence.",
        abstained=True,
        claims=(),
        structured_facts=None,
        cited_chunk_ids=(),
        limitations=("No evidence was supplied.",),
    )
    raw = answer.model_dump_json()
    if response_kind == "invalid_json":
        raw = "not JSON"
    elif response_kind in {"unknown_citation", "invented_identifier"}:
        payload = answer.model_dump(mode="json")
        payload.update(
            abstained=False,
            claims=[
                {
                    "claim_id": "C1",
                    "text": "The source says something.",
                    "claim_type": "literature_fact",
                    "citation_ids": ["D1"],
                }
            ],
        )
        if response_kind == "invented_identifier":
            payload["claims"] = [
                {
                    "claim_id": "C1",
                    "claim_type": "interpretation",
                    "text": "The sequence MN888888.1 is present.",
                }
            ]
        import json

        raw = json.dumps(payload)
    with local.OfflineMlxGenerationProvider(config) as provider:
        monkeypatch.setattr(
            provider,
            "_exchange",
            lambda operation, evidence: {
                "input_tokens": 100,
                "context_tokens": 20,
                "output_tokens": 20,
                "latency_ns": 1,
                "peak_memory_bytes": 1,
                "answer": raw,
                "finish_reason": "length" if response_kind == "length" else "stop",
            },
        )
        result = provider.generate(pack())
    assert (
        result.status
        == {
            "valid": "completed",
            "invalid_json": "invalid_answer",
            "unknown_citation": "mechanical_failure",
            "invented_identifier": "evidence_failure",
            "length": "output_limit",
        }[response_kind]
    )
    assert result.raw_answer == raw
    if response_kind == "invented_identifier":
        assert result.validation_report.structured_evidence_status == "failed"
    assert result.validation_report.semantic_support_status == "not_assessed"


def test_worker_start_failure_is_sanitized(config, monkeypatch):
    provider = local.OfflineMlxGenerationProvider(config)

    def fail():
        raise RuntimeError("private connection detail")

    monkeypatch.setattr(provider, "_start", fail)
    with pytest.raises(local.LocalGenerationError, match="no retry") as exc:
        provider.measure(pack())
    assert "private" not in str(exc.value)
    assert provider._process is None


def test_modified_worker_is_rejected_before_process_spawn(config):
    provider = local.OfflineMlxGenerationProvider(config)
    config.worker_script.write_text("changed")
    with pytest.raises(local.LocalGenerationError, match="changed before startup"):
        provider._start()
    assert provider._process is None


def test_wrong_model_revision_is_rejected(config):
    with pytest.raises(local.LocalGenerationError, match="pinned identity"):
        local.verify_generation_assets(replace(config, model_policy_file_sha256="f" * 64))


def test_timed_out_worker_does_not_poison_the_next_question(config, monkeypatch):
    import json

    request = {}
    observations = []

    def write(raw):
        request["raw"] = raw[:-1]
        request["value"] = json.loads(raw)
        observations.append(request["value"]["sequence"])

    def receive(*args, **kwargs):
        if len(observations) == 1:
            raise local.LocalGenerationTimeout("receive")
        return canonical_json_bytes(
            {
                "status": "ok",
                "sequence": 1,
                "request_sha256": hashlib.sha256(request["raw"]).hexdigest(),
                "input_tokens": 100,
                "context_tokens": 20,
            }
        )

    with local.OfflineMlxGenerationProvider(config) as provider:
        process = SimpleNamespace(
            stdin=SimpleNamespace(write=write, flush=lambda: None),
            stdout=SimpleNamespace(fileno=lambda: 0),
        )
        monkeypatch.setattr(provider, "_start", lambda: process)
        monkeypatch.setattr(local, "_read_response", receive)
        with pytest.raises(local.LocalGenerationTimeout) as failure:
            provider.measure(pack(question_id="failed-question"))
        assert failure.value.operation == "count"
        assert provider.last_exchange_receipt is None
        assert provider.measure(pack(question_id="next-question")) == (100, 20)
        assert observations == [1, 1]  # A new worker starts a new protocol sequence.
