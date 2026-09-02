from __future__ import annotations

import json

import pytest

from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    build_evidence_pack,
    build_generation_identity,
)
from eve_relation_rag.experiments.rag_value_ablation.prompting import (
    PromptPolicyError,
    build_prompt_policy,
    render_user_payload,
    validate_generation_identity,
)


def test_frozen_prompt_contains_required_safety_rules_and_matches_generation() -> None:
    policy = build_prompt_policy()
    identity = _identity(
        system_instruction_sha256=policy.system_instruction_sha256,
        request_template_sha256=policy.request_template_sha256,
        output_schema_sha256=policy.output_schema_sha256,
    )

    validate_generation_identity(identity, policy)
    assert "Answer in English" in policy.system_instruction
    assert "do not use external knowledge" in policy.system_instruction
    assert "Do not invent accessions" in policy.system_instruction
    assert "Preserve structured values exactly" in policy.system_instruction
    assert "Cite every literature-derived factual claim" in policy.system_instruction
    assert "abstain" in policy.system_instruction
    assert "independent integration events" in policy.system_instruction


def test_prompt_mismatch_fails_before_generation_and_payload_hides_condition() -> None:
    policy = build_prompt_policy()
    mismatched = _identity(
        system_instruction_sha256="0" * 64,
        request_template_sha256=policy.request_template_sha256,
        output_schema_sha256=policy.output_schema_sha256,
    )
    with pytest.raises(PromptPolicyError, match="does not match"):
        validate_generation_identity(mismatched, policy)

    evidence = build_evidence_pack(
        question_id="closed-book-001",
        question_text="What can the supplied evidence establish?",
        policy_sha256=policy.policy_sha256,
        tokenizer_key="tokenizer:synthetic",
        model_context_limit_tokens=4096,
        reserved_output_tokens=512,
        input_token_count=50,
        context_token_count=10,
    )
    payload = json.loads(render_user_payload(evidence))
    assert payload["evidence"]["question"] == evidence.question_text
    assert "system_key" not in payload
    assert "gold" not in payload
    assert "review_status" not in payload


def _identity(
    *,
    system_instruction_sha256: str,
    request_template_sha256: str,
    output_schema_sha256: str,
):
    return build_generation_identity(
        provider_key="provider:synthetic",
        provider_kind="deterministic_fake",
        model_id="example/model",
        exact_revision="a" * 40,
        model_artifact_manifest_sha256="1" * 64,
        tokenizer_id="example/tokenizer",
        tokenizer_revision="b" * 40,
        tokenizer_artifact_manifest_sha256="2" * 64,
        system_instruction_sha256=system_instruction_sha256,
        request_template_sha256=request_template_sha256,
        output_schema_sha256=output_schema_sha256,
        temperature=0,
        max_output_tokens=512,
        max_output_bytes=16384,
        context_limit_tokens=4096,
        timeout_seconds=30,
        retry_count=0,
        request_concurrency=1,
        seed=7,
        tools_enabled=False,
        web_enabled=False,
        conversation_memory_enabled=False,
    )
