from __future__ import annotations

from eve_relation_rag.demo.examples import load_demo_examples
from eve_relation_rag.demo.presentation import execution_stages, response_details, response_label
from eve_relation_rag.generation.rendering import render_structured_answer_text
from eve_relation_rag.hybrid.contracts import (
    ExecutionFlags,
    RagErrorResponse,
    RagQueryRequest,
    StructuredRouteAnswer,
)
from eve_relation_rag.planning.router import DeterministicRouter
from tests.support.m4 import make_structured_success


def test_examples_cover_each_family_without_client_owned_route() -> None:
    examples = load_demo_examples()

    assert tuple(example.family for example in examples) == (
        "structured",
        "literature",
        "hybrid",
        "unsupported",
    )
    assert all("route" not in example.request.model_fields_set for example in examples)
    assert examples[0].request.release_key == "release:endoviho-rag:v0:20260826:001"
    assert examples[1].request.corpus_release_key == "corpus:endoviho-rag:v0:20260828:001"
    assert examples[2].request.release_key is not None
    assert examples[2].request.corpus_release_key is not None
    assert examples[3].request.release_key is None
    assert examples[3].request.corpus_release_key is None
    assert all("success" not in example.current_outcome.casefold() for example in examples)
    router = DeterministicRouter()
    assert tuple(router.route(example.request).route for example in examples) == tuple(
        example.family for example in examples
    )


def test_execution_rail_is_derived_only_from_server_flags() -> None:
    response = RagErrorResponse(
        route="hybrid",
        requested_release_key="release:endoviho-rag:v0:20260826:001",
        requested_corpus_release_key="corpus:endoviho-rag:v0:20260828:001",
        code="llm_provider_unavailable",
        message="No production provider is approved.",
        upstream_code=None,
        execution=ExecutionFlags(
            structured_retrieval_executed=True,
            literature_retrieval_executed=True,
            generation_executed=False,
        ),
    )

    stages = execution_stages(response)

    assert tuple(stage.state for stage in stages) == ("executed", "executed", "held")
    assert response_label(response) == "NOT COMPLETED / llm_provider_unavailable"


def test_structured_presentation_exposes_required_limitations() -> None:
    question = "Count distinct included loci in this release."
    success = make_structured_success("aggregate", structured_question=question)
    response = StructuredRouteAnswer(
        original_request=RagQueryRequest(
            release_key=success.query_plan.release_key,
            corpus_release_key=None,
            question=question,
            page=None,
            literature_top_k=None,
        ),
        query_success=success,
        structured_text=render_structured_answer_text(success),
        execution=ExecutionFlags(
            structured_retrieval_executed=True,
            literature_retrieval_executed=False,
            generation_executed=False,
        ),
    )

    details = response_details(response)

    assert "assembly_local_locus_is_not_independent_integration_event" in (
        details.limitation_codes
    )
    assert details.anchor_diagnostics == ()
    assert details.validation_scope is None
    assert details.citations == ()
