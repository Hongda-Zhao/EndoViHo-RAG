from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    EvidenceCitation,
    ExecutionTrace,
    build_evidence_pack,
    build_generation_identity,
)
from eve_relation_rag.experiments.rag_value_ablation.systems import (
    LLM_SYSTEM_KEYS,
    ComparisonInputRecord,
    SystemPolicyError,
    build_system_definitions,
    validate_evidence_for_system,
    validate_execution_trace,
    validate_llm_comparison_inputs,
    validate_system_definitions,
)

DOCUMENT = f"document:sha256:{'a' * 64}"
CHUNK = f"chunk:sha256:{'b' * 64}"


def test_canonical_systems_share_generation_identity_and_forbid_s4_llm() -> None:
    identity = _generation_identity()
    systems = build_system_definitions(identity)

    validate_system_definitions(systems, identity)
    assert tuple(system.system_key for system in systems) == (
        "S0",
        "S1",
        "S2",
        "S3",
        "S4",
        "S5",
        "S6",
    )
    assert {system.generation_identity_sha256 for system in systems if system.uses_llm} == {
        identity.identity_sha256
    }
    assert systems[4].uses_llm is False
    assert systems[4].generation_identity_sha256 is None
    with pytest.raises(SystemPolicyError, match="canonical S0-S6"):
        validate_system_definitions(systems[:-1], identity)


def test_phase3_systems_have_no_provider_binding_and_support_retrieval_only_trace() -> None:
    systems = build_system_definitions(None)
    validate_system_definitions(systems, None)
    assert all(
        system.generation_identity_sha256 is None for system in systems
    )
    system = systems[2]
    trace = ExecutionTrace(
        system_key="S2",
        question_id="literature-001",
        status="retrieval_only",
        constructed_dependencies=("database", "corpus", "fts"),
        called_stages=("fts_retrieval", "chunk_hydration", "context_construction"),
        generation_call_count=0,
    )

    validate_execution_trace(system, trace)
    with pytest.raises(SystemPolicyError, match="answer stage"):
        validate_execution_trace(
            system,
            trace.model_copy(
                update={
                    "called_stages": (
                        *trace.called_stages,
                        "generation",
                    )
                }
            ),
        )


def test_s2_trace_cannot_construct_embeddings_or_call_forbidden_stages() -> None:
    system = build_system_definitions(_generation_identity())[2]
    valid = ExecutionTrace(
        system_key="S2",
        question_id="literature-001",
        status="completed",
        constructed_dependencies=("database", "corpus", "fts", "llm_provider"),
        called_stages=system.required_success_stages,
        generation_call_count=1,
    )
    validate_execution_trace(system, valid)

    forbidden = valid.model_copy(
        update={
            "constructed_dependencies": (
                "database",
                "corpus",
                "fts",
                "embedding_provider",
                "llm_provider",
            )
        }
    )
    with pytest.raises(SystemPolicyError, match="forbidden dependencies"):
        validate_execution_trace(system, forbidden)


def test_refusal_trace_rejects_any_stage_after_refusal() -> None:
    with pytest.raises(ValidationError, match="after refusal"):
        ExecutionTrace(
            system_key="S5",
            question_id="unsupported-001",
            status="refused",
            constructed_dependencies=("database", "structured_retrieval"),
            called_stages=("structured_planning", "release_binding"),
            refusal_stage="structured_planning",
            generation_call_count=0,
        )


def test_system_evidence_shapes_prevent_hidden_context() -> None:
    systems = build_system_definitions(_generation_identity())
    empty = _evidence_pack(with_citation=False)
    literature = _evidence_pack(with_citation=True)

    validate_evidence_for_system(systems[0], empty)
    validate_evidence_for_system(systems[2], literature)
    oracle_empty = build_evidence_pack(
        question_id=empty.question_id,
        question_text=empty.question_text,
        policy_sha256="f" * 64,
        tokenizer_key="tokenizer:synthetic",
        model_context_limit_tokens=4096,
        reserved_output_tokens=512,
        input_token_count=100,
        context_token_count=50,
        oracle_entry_sha256="1" * 64,
    )
    validate_evidence_for_system(systems[6], oracle_empty)
    with pytest.raises(SystemPolicyError, match="evidence shape"):
        validate_evidence_for_system(systems[0], literature)
    with pytest.raises(SystemPolicyError, match="must not construct"):
        validate_evidence_for_system(systems[4], literature)
    with pytest.raises(SystemPolicyError, match="approved oracle entry"):
        validate_evidence_for_system(systems[6], empty)


def test_all_llm_inputs_keep_identical_wording_and_generation_policy() -> None:
    identity = _generation_identity()
    systems = build_system_definitions(identity)
    question = "What does the supplied evidence establish?"
    question_sha = hashlib.sha256(question.encode()).hexdigest()
    records = tuple(
        ComparisonInputRecord(
            system_key=system_key,
            question_id="hybrid-001",
            question_text=question,
            question_text_sha256=question_sha,
            generation_identity_sha256=identity.identity_sha256,
            evidence_pack_sha256=f"{index + 1:064x}",
        )
        for index, system_key in enumerate(LLM_SYSTEM_KEYS)
    )
    validate_llm_comparison_inputs(records, systems)

    changed = list(records)
    changed[2] = ComparisonInputRecord(
        **{
            **changed[2].model_dump(mode="python"),
            "question_text": "A different question.",
            "question_text_sha256": hashlib.sha256(b"A different question.").hexdigest(),
        }
    )
    with pytest.raises(SystemPolicyError, match="wording differs"):
        validate_llm_comparison_inputs(tuple(changed), systems)


def _generation_identity():
    return build_generation_identity(
        provider_key="provider:synthetic",
        provider_kind="deterministic_fake",
        model_id="example/model",
        exact_revision="a" * 40,
        model_artifact_manifest_sha256="1" * 64,
        tokenizer_id="example/tokenizer",
        tokenizer_revision="b" * 40,
        tokenizer_artifact_manifest_sha256="2" * 64,
        system_instruction_sha256="c" * 64,
        request_template_sha256="d" * 64,
        output_schema_sha256="e" * 64,
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


def _evidence_pack(*, with_citation: bool):
    citations = ()
    if with_citation:
        text = "A synthetic passage used only by unit tests."
        citations = (
            EvidenceCitation(
                citation_id="D1",
                document_key=DOCUMENT,
                chunk_key=CHUNK,
                locator_text="Synthetic paragraph 1",
                text=text,
                text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            ),
        )
    return build_evidence_pack(
        question_id="literature-001",
        question_text="What does the supplied evidence establish?",
        citations=citations,
        policy_sha256="f" * 64,
        tokenizer_key="tokenizer:synthetic",
        model_context_limit_tokens=4096,
        reserved_output_tokens=512,
        input_token_count=100,
        context_token_count=50,
    )
