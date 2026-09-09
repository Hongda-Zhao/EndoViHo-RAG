"""Synthetic authority lifecycle tests; no scientific or runtime approval is issued."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine

from eve_relation_rag.experiments.rag_value_ablation import execution_gate as gate
from eve_relation_rag.experiments.rag_value_ablation.preflight import (
    construct_phase3_dependencies,
    run_phase3_preflight,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes
from tests.experiments.test_rag_value_phase3_preflight import _ready_input


def request_for(evidence):
    now = datetime.now(UTC).replace(microsecond=0)
    return gate.Phase3ExecutionRequest.model_validate(
        {
            "schema_version": "rag-value-phase3-execution-request-v1",
            "experiment_namespace": "rag-value-ablation:tests-only",
            "preflight_input_sha256": evidence.input_sha256,
            "system_keys": ("S2",),
            "database_name": "endoviho_rag_value_tests",
            "database_role": "rag_value_tests_reader",
            "operator_key": "tests-only",
            "authorized_at": (now - timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            "expires_at": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
            "authorization_text": (
                "I authorize this exact isolated, read-only Phase 3 request; no production change."
            ),
            "network_policy": "loopback-postgresql-only",
            "generation_allowed": False,
            "output_trust_authorized": False,
        }
    )


def issue(monkeypatch):
    evidence = _ready_input()
    decision = run_phase3_preflight(evidence)
    request = request_for(evidence)
    engine = create_engine(
        "postgresql+psycopg://rag_value_tests_reader@127.0.0.1/endoviho_rag_value_tests"
    )
    monkeypatch.setattr(gate, "_live_fingerprint", lambda *args: "a" * 64)
    authority = gate.issue_phase3_execution_authority(
        engine=engine,
        evidence=evidence,
        decision=decision,
        request=request,
        approved_request_file_sha256=hashlib.sha256(
            canonical_json_bytes(request) + b"\n"
        ).hexdigest(),
    )
    return authority, decision


def test_factory_requires_exact_single_use_authority(monkeypatch):
    authority, decision = issue(monkeypatch)
    calls = []
    with pytest.raises(gate.ExecutionGateError, match="forged"):
        construct_phase3_dependencies(
            decision,
            lambda: calls.append(1),
            execution_authority=replace(authority),
        )
    assert not calls
    assert (
        construct_phase3_dependencies(
            decision,
            lambda: "retrieval-only",
            execution_authority=authority,
        )
        == "retrieval-only"
    )
    with pytest.raises(gate.ExecutionGateError, match="consumed"):
        construct_phase3_dependencies(
            decision, lambda: calls.append(1), execution_authority=authority
        )
    assert not calls


@pytest.mark.parametrize("change", ["expiry", "fingerprint", "decision", "audit_failure"])
def test_runtime_changes_never_call_factory(monkeypatch, change):
    authority, decision = issue(monkeypatch)
    if change == "expiry":
        monkeypatch.setattr(gate.time, "monotonic", lambda: authority._deadline + 1)
    elif change == "fingerprint":
        monkeypatch.setattr(gate, "_live_fingerprint", lambda *args: "b" * 64)
    elif change == "decision":
        decision = replace(decision)
    else:

        def fail(*args):
            raise RuntimeError("private connection detail")

        monkeypatch.setattr(gate, "_live_fingerprint", fail)
    calls = []
    with pytest.raises((gate.ExecutionGateError, TypeError)) as error:
        construct_phase3_dependencies(
            decision, lambda: calls.append(1), execution_authority=authority
        )
    assert "private connection" not in str(error.value)
    assert not calls


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://rag_value_tests_reader@127.0.0.1/production",
        "postgresql+psycopg://owner@127.0.0.1/endoviho_rag_value_tests",
        "postgresql+psycopg://rag_value_tests_reader@remote/endoviho_rag_value_tests",
        "postgresql+psycopg://rag_value_tests_reader@127.0.0.1/endoviho_rag_value_tests?host=remote",
        "sqlite://",
    ],
)
def test_target_rejection_precedes_connect(url):
    with pytest.raises(gate.ExecutionGateError):
        gate._validate_target(create_engine(url), request_for(_ready_input()))


@pytest.mark.parametrize("change", ["request_hash", "gold_unapproved"])
def test_unapproved_inputs_are_rejected_before_live_connection(monkeypatch, change):
    from tests.experiments.test_rag_value_phase3_preflight import _rebuild

    evidence = _ready_input()
    if change == "gold_unapproved":
        evidence = _rebuild(
            evidence,
            questions=evidence.questions.model_copy(
                update={
                    "gold_manifest": evidence.questions.gold_manifest.model_copy(
                        update={"approval_status": "pending"},
                    ),
                }
            ),
        )
    decision = run_phase3_preflight(evidence)
    request = request_for(evidence)
    digest = hashlib.sha256(canonical_json_bytes(request) + b"\n").hexdigest()
    calls = []
    monkeypatch.setattr(gate, "_live_fingerprint", lambda *args: calls.append(1))
    with pytest.raises(gate.ExecutionGateError):
        gate.issue_phase3_execution_authority(
            engine=create_engine("sqlite://"),
            evidence=evidence,
            decision=decision,
            request=request,
            approved_request_file_sha256="f" * 64 if change == "request_hash" else digest,
        )
    assert calls == []


@pytest.mark.parametrize("system", ["S1", "S3"])
def test_live_artifact_checks_require_paths_not_booleans(system):
    request = request_for(_ready_input()).model_copy(update={"system_keys": (system,)})
    engine = create_engine(
        "postgresql+psycopg://rag_value_tests_reader@127.0.0.1/endoviho_rag_value_tests"
    )
    with pytest.raises(gate.ExecutionGateError, match="artifact paths"):
        gate._validate_target(engine, request)
