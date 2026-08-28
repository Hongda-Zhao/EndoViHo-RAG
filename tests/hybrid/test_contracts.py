from __future__ import annotations

import hashlib

import pytest
from pydantic import TypeAdapter, ValidationError

from eve_relation_rag.hybrid.contracts import (
    ANSWER_INSTRUCTIONS_VERSION,
    BINDING_MANIFEST_VERSION,
    CONTEXT_PACK_VERSION,
    GENERATED_DRAFT_VERSION,
    RAG_QUERY_REQUEST_VERSION,
    ROUTE_DECISION_VERSION,
    AnswerInstructions,
    EvidenceSpan,
    ExecutionFlags,
    GeneratedAnswerDraft,
    HybridReleaseBinding,
    HybridReleaseBindingManifest,
    LiteratureClaim,
    RagErrorResponse,
    RagQueryRequest,
    RagResponse,
    RouteDecision,
    canonical_model_json,
    canonical_model_sha256,
    canonical_self_sha256,
)

RELEASE_KEY = "release:endoviho-rag:v0:20991231:999"
CORPUS_KEY = "corpus:endoviho-rag:v0:20991231:999"
SHA_A = "a" * 64
SHA_B = "b" * 64


def _request(**overrides: object) -> RagQueryRequest:
    values: dict[str, object] = {
        "request_schema_version": RAG_QUERY_REQUEST_VERSION,
        "release_key": RELEASE_KEY,
        "corpus_release_key": CORPUS_KEY,
        "question": (
            "Count distinct included loci in this entire release and explain the literature "
            "evidence"
        ),
        "page": None,
        "literature_top_k": 8,
    }
    values.update(overrides)
    return RagQueryRequest.model_validate(values)


def _binding() -> HybridReleaseBinding:
    return HybridReleaseBinding(
        release_key=RELEASE_KEY,
        release_manifest_sha256=SHA_A,
        corpus_release_key=CORPUS_KEY,
        corpus_manifest_sha256=SHA_B,
    )


def _binding_manifest() -> HybridReleaseBindingManifest:
    payload: dict[str, object] = {
        "binding_schema_version": BINDING_MANIFEST_VERSION,
        "bindings": (_binding(),),
        "manifest_sha256": "0" * 64,
    }
    payload["manifest_sha256"] = canonical_self_sha256(payload, "manifest_sha256")
    return HybridReleaseBindingManifest.model_validate(payload)


def test_contract_versions_are_exact() -> None:
    assert RAG_QUERY_REQUEST_VERSION == "rag-query-request-v1"
    assert ROUTE_DECISION_VERSION == "rag-route-decision-v1"
    assert BINDING_MANIFEST_VERSION == "hybrid-release-binding-manifest-v1"
    assert CONTEXT_PACK_VERSION == "context-pack-v1"
    assert ANSWER_INSTRUCTIONS_VERSION == "answer-instructions-v1"
    assert GENERATED_DRAFT_VERSION == "generated-answer-draft-v1"


def test_rag_request_is_ascii_strict_frozen_and_selector_safe() -> None:
    request = _request()
    assert request.literature_top_k == 8
    with pytest.raises(ValidationError):
        RagQueryRequest.model_validate({**request.model_dump(), "route": "hybrid"})
    with pytest.raises(ValidationError):
        _request(question="Explain 方法")
    with pytest.raises(ValidationError):
        _request(question="two\nlines")
    with pytest.raises(ValidationError):
        _request(literature_top_k=9)
    with pytest.raises(ValidationError):
        _request(corpus_release_key=None, literature_top_k=8)
    with pytest.raises(ValidationError):
        request.question = "changed"  # type: ignore[misc]


def test_route_decision_enforces_supported_and_unsupported_shapes() -> None:
    hybrid = RouteDecision(
        route_schema_version=ROUTE_DECISION_VERSION,
        route="hybrid",
        original_question=_request().question,
        structured_question="Count distinct included loci in this entire release",
        literature_question=_request().question,
        effective_literature_top_k=8,
        refusal_code=None,
    )
    assert hybrid.route == "hybrid"

    unsupported = RouteDecision(
        route_schema_version=ROUTE_DECISION_VERSION,
        route="unsupported",
        original_question="Calculate prevalence.",
        structured_question=None,
        literature_question=None,
        effective_literature_top_k=None,
        refusal_code="unsupported_request",
    )
    assert unsupported.refusal_code == "unsupported_request"

    with pytest.raises(ValidationError):
        unsupported.model_copy(update={"structured_question": "Count loci"})
        RouteDecision.model_validate_json(
            unsupported.model_copy(update={"structured_question": "Count loci"}).model_dump_json()
        )


def test_binding_manifest_is_self_hashed_sorted_and_unique() -> None:
    manifest = _binding_manifest()
    assert manifest.manifest_sha256 == canonical_self_sha256(manifest, "manifest_sha256")
    assert canonical_model_json(manifest) == canonical_model_json(
        HybridReleaseBindingManifest.model_validate_json(manifest.model_dump_json())
    )
    assert (
        canonical_model_sha256(manifest)
        == hashlib.sha256(canonical_model_json(manifest).encode("utf-8")).hexdigest()
    )

    duplicate_payload = manifest.model_dump(mode="python")
    duplicate_payload["bindings"] = (manifest.bindings[0], manifest.bindings[0])
    duplicate_payload["manifest_sha256"] = canonical_self_sha256(
        duplicate_payload, "manifest_sha256"
    )
    with pytest.raises(ValidationError):
        HybridReleaseBindingManifest.model_validate(duplicate_payload)

    tampered = manifest.model_dump(mode="python")
    tampered["manifest_sha256"] = "f" * 64
    with pytest.raises(ValidationError):
        HybridReleaseBindingManifest.model_validate(tampered)


@pytest.mark.parametrize(
    "bindings",
    [
        (
            HybridReleaseBinding(
                release_key=RELEASE_KEY,
                release_manifest_sha256=SHA_A,
                corpus_release_key="corpus:endoviho-rag:v0:20991230:998",
                corpus_manifest_sha256=SHA_B,
            ),
            HybridReleaseBinding(
                release_key=RELEASE_KEY,
                release_manifest_sha256="c" * 64,
                corpus_release_key=CORPUS_KEY,
                corpus_manifest_sha256=SHA_B,
            ),
        ),
        (
            HybridReleaseBinding(
                release_key="release:endoviho-rag:v0:20991230:998",
                release_manifest_sha256=SHA_A,
                corpus_release_key=CORPUS_KEY,
                corpus_manifest_sha256=SHA_B,
            ),
            HybridReleaseBinding(
                release_key=RELEASE_KEY,
                release_manifest_sha256=SHA_A,
                corpus_release_key=CORPUS_KEY,
                corpus_manifest_sha256="c" * 64,
            ),
        ),
    ],
)
def test_binding_manifest_rejects_cross_manifest_identity_conflicts(
    bindings: tuple[HybridReleaseBinding, ...],
) -> None:
    ordered = tuple(sorted(bindings, key=lambda item: (item.release_key, item.corpus_release_key)))
    payload: dict[str, object] = {
        "binding_schema_version": BINDING_MANIFEST_VERSION,
        "bindings": ordered,
        "manifest_sha256": "0" * 64,
    }
    payload["manifest_sha256"] = canonical_self_sha256(payload, "manifest_sha256")

    with pytest.raises(ValidationError):
        HybridReleaseBindingManifest.model_validate(payload)


def test_claim_and_draft_enforce_canonical_citations_spans_and_limits() -> None:
    claim = LiteratureClaim(
        claim_id="C1",
        claim_text="The synthetic method used a deterministic comparison.",
        citation_ids=("D1",),
        evidence_spans=(
            EvidenceSpan(
                citation_id="D1",
                quote="used a deterministic comparison",
            ),
        ),
    )
    draft = GeneratedAnswerDraft(
        draft_schema_version=GENERATED_DRAFT_VERSION,
        context_sha256=SHA_A,
        claims=(claim,),
        selected_limitation_codes=(
            "literature_evidence_is_explanatory",
            "mechanical_validation_is_not_semantic_entailment",
        ),
    )
    assert draft.claims[0].claim_id == "C1"

    with pytest.raises(ValidationError):
        LiteratureClaim.model_validate(
            {
                **claim.model_dump(),
                "citation_ids": ("D2", "D1"),
                "evidence_spans": (
                    {"citation_id": "D2", "quote": "first"},
                    {"citation_id": "D1", "quote": "second"},
                ),
            }
        )
    with pytest.raises(ValidationError):
        GeneratedAnswerDraft.model_validate(
            {
                **draft.model_dump(),
                "claims": (claim.model_copy(update={"claim_id": "C2"}),),
            }
        )


def test_execution_flags_and_error_are_strict_response_variant() -> None:
    error = RagErrorResponse(
        response_schema_version="rag-error-v1",
        response_kind="error",
        route="hybrid",
        requested_release_key=RELEASE_KEY,
        requested_corpus_release_key=CORPUS_KEY,
        code="hybrid_binding_unavailable",
        message="The exact release pair is not approved.",
        upstream_code=None,
        execution=ExecutionFlags(
            structured_retrieval_executed=False,
            literature_retrieval_executed=False,
            generation_executed=False,
        ),
    )
    parsed = TypeAdapter(RagResponse).validate_json(error.model_dump_json())
    assert isinstance(parsed, RagErrorResponse)


@pytest.mark.parametrize(
    "updates",
    (
        {"code": "internal_error", "upstream_code": "secret-internal"},
        {"code": "structured_refused", "upstream_code": "secret-internal"},
        {"code": "literature_refused", "upstream_code": "release_not_found"},
        {
            "route": "unsupported",
            "code": "unsupported_request",
            "execution": ExecutionFlags(
                structured_retrieval_executed=True,
                literature_retrieval_executed=True,
                generation_executed=True,
            ),
        },
        {
            "execution": ExecutionFlags(
                structured_retrieval_executed=False,
                literature_retrieval_executed=True,
                generation_executed=False,
            ),
        },
        {
            "execution": ExecutionFlags(
                structured_retrieval_executed=True,
                literature_retrieval_executed=False,
                generation_executed=True,
            ),
        },
        {"route": "unsupported", "code": "internal_error"},
    ),
)
def test_error_response_rejects_cross_field_tampering(updates: dict[str, object]) -> None:
    baseline = {
        "response_schema_version": "rag-error-v1",
        "response_kind": "error",
        "route": "hybrid",
        "requested_release_key": RELEASE_KEY,
        "requested_corpus_release_key": CORPUS_KEY,
        "code": "hybrid_binding_unavailable",
        "message": "The exact release pair is not approved.",
        "upstream_code": None,
        "execution": ExecutionFlags(
            structured_retrieval_executed=False,
            literature_retrieval_executed=False,
            generation_executed=False,
        ),
    }

    with pytest.raises(ValidationError):
        RagErrorResponse.model_validate(baseline | updates)


def test_answer_instructions_validate_the_source_text_hash() -> None:
    source_text = "Use only the supplied context."
    instructions = AnswerInstructions(
        instruction_schema_version=ANSWER_INSTRUCTIONS_VERSION,
        instruction_policy_key="answer:endoviho-rag:v0:test-v1",
        source_text=source_text,
        source_text_sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
    )
    assert instructions.source_text == source_text
    with pytest.raises(ValidationError):
        AnswerInstructions.model_validate(
            {**instructions.model_dump(), "source_text_sha256": "f" * 64}
        )
