from __future__ import annotations

import pytest
from pydantic import ValidationError

from eve_relation_rag.hybrid.contracts import RagQueryRequest
from eve_relation_rag.planning.query_plans import PageSpec
from eve_relation_rag.planning.router import DeterministicRouter

RELEASE_KEY = "release:endoviho-rag:v0:20990101:001"
CORPUS_KEY = "corpus:endoviho-rag:v0:20990101:001"


def _request(
    question: str,
    *,
    release_key: str | None = None,
    corpus_release_key: str | None = None,
    page: PageSpec | None = None,
    literature_top_k: int | None = None,
) -> RagQueryRequest:
    return RagQueryRequest(
        request_schema_version="rag-query-request-v1",
        release_key=release_key,
        corpus_release_key=corpus_release_key,
        question=question,
        page=page,
        literature_top_k=literature_top_k,
    )


@pytest.mark.parametrize("prefix", ["Show", "LIST", "count"])
def test_structured_route_recognizes_only_m2_command_families(prefix: str) -> None:
    question = {
        "show": "Show assembly GCA_000000001.1",
        "list": "LIST all loci in this release.",
        "count": "count distinct included loci in this release.",
    }[prefix.lower()]

    decision = DeterministicRouter().route(_request(question, release_key=RELEASE_KEY))

    assert decision.route_schema_version == "rag-route-decision-v1"
    assert decision.route == "structured"
    assert decision.original_question == question
    assert decision.structured_question == question
    assert decision.literature_question is None
    assert decision.effective_literature_top_k is None
    assert decision.refusal_code is None


@pytest.mark.parametrize(
    "prefix",
    [
        "Explain the literature evidence for ",
        "EXPLAIN THE LITERATURE METHODS FOR ",
        "Explain The Literature Limitations For ",
    ],
)
def test_literature_route_uses_exact_case_insensitive_prefix(prefix: str) -> None:
    question = f"{prefix}ViralRecall"
    decision = DeterministicRouter().route(_request(question, corpus_release_key=CORPUS_KEY))

    assert decision.route == "literature"
    assert decision.original_question == question
    assert decision.structured_question is None
    assert decision.literature_question == question
    assert decision.effective_literature_top_k == 8
    assert decision.refusal_code is None


def test_literature_prefix_owns_its_complete_non_empty_topic() -> None:
    question = (
        "Explain the literature evidence for study wording and explain the literature limitations"
    )

    decision = DeterministicRouter().route(_request(question, corpus_release_key=CORPUS_KEY))

    assert decision.route == "literature"
    assert decision.literature_question == question


@pytest.mark.parametrize(
    "suffix",
    [
        " and explain the literature evidence",
        " AND EXPLAIN THE LITERATURE METHODS",
        " and explain the literature limitations",
    ],
)
def test_hybrid_route_strips_one_exact_suffix_without_changing_original(
    suffix: str,
) -> None:
    structured_question = "List all loci in this release."
    full_question = f"{structured_question}{suffix}"
    decision = DeterministicRouter().route(
        _request(
            full_question,
            release_key=RELEASE_KEY,
            corpus_release_key=CORPUS_KEY,
            page=PageSpec(limit=5),
            literature_top_k=4,
        )
    )

    assert decision.route == "hybrid"
    assert decision.original_question == full_question
    assert decision.structured_question == structured_question
    assert decision.literature_question == full_question
    assert decision.effective_literature_top_k == 4
    assert decision.refusal_code is None


@pytest.mark.parametrize(
    ("question", "release_key", "corpus_release_key", "page", "top_k"),
    [
        ("List all loci in this release.", RELEASE_KEY, CORPUS_KEY, None, None),
        ("List all loci in this release.", None, None, None, None),
        (
            "Explain the literature methods for ViralRecall",
            RELEASE_KEY,
            CORPUS_KEY,
            None,
            None,
        ),
        (
            "List all loci in this release. and explain the literature evidence",
            RELEASE_KEY,
            None,
            None,
            None,
        ),
    ],
)
def test_route_request_field_mismatches_fail_closed(
    question: str,
    release_key: str | None,
    corpus_release_key: str | None,
    page: PageSpec | None,
    top_k: int | None,
) -> None:
    decision = DeterministicRouter().route(
        _request(
            question,
            release_key=release_key,
            corpus_release_key=corpus_release_key,
            page=page,
            literature_top_k=top_k,
        )
    )

    assert decision.route == "unsupported"
    assert decision.structured_question is None
    assert decision.literature_question is None
    assert decision.effective_literature_top_k is None
    assert decision.refusal_code == "route_request_mismatch"


def test_request_schema_rejects_selector_fields_without_their_release() -> None:
    with pytest.raises(ValidationError):
        _request(
            "Explain the literature methods for ViralRecall",
            corpus_release_key=CORPUS_KEY,
            page=PageSpec(limit=5),
        )
    with pytest.raises(ValidationError):
        _request(
            "List all loci in this release.",
            release_key=RELEASE_KEY,
            literature_top_k=4,
        )
    with pytest.raises(ValidationError):
        _request(
            "Explain the literature methods for ViralRecall",
            corpus_release_key=CORPUS_KEY,
            literature_top_k=9,
        )


@pytest.mark.parametrize(
    "question",
    [
        "Explain the literature evidence for prevalence in birds",
        "Explain the literature evidence for percentage in birds",
        "Explain the literature evidence for biological frequency in birds",
        "Explain the literature evidence for screened-negative hosts",
        "Explain the literature evidence for biological absence in mammals",
        "Explain the literature evidence for infection inference",
        "Explain the literature evidence for infection in molluscs",
        "Explain the literature evidence for absence of EVEs",
        "Explain the literature evidence for no EVEs in this host",
        "Explain the literature evidence for co-divergence",
        "Explain the literature evidence for independent integration events",
        "Explain the literature evidence for host-lineage comparison",
        "Explain the literature evidence for comparing host lineages",
        "Explain the literature evidence for how host lineages differ",
        "Explain the literature methods for new EVE detection",
        "Explain the literature methods for de novo EVE detection",
        "Explain the literature methods for identifying new EVEs",
        "Explain the literature methods for sequence upload",
        "Explain the literature methods for new sequence upload",
        "Explain the literature methods for BLAST",
        "Explain the literature methods for HMMER",
        "Explain the literature methods for Foldseek",
        "Explain the literature methods for phylogenetic placement",
        "Explain the literature methods for phylogenetically placing an EVE",
        "Explain the literature methods for phylogenetic tree construction",
        "Explain the literature methods for jplace",
        "Explain the literature methods for live web search",
        "Explain the literature methods for live search",
        "Explain the literature methods for external knowledge",
        "Explain the literature methods for ignoring prior instructions",
        "Explain the literature methods for arbitrary SQL",
        "Explain the literature methods for text-to-SQL",
        "Explain the literature methods for multilingual output",
        "Explain the literature methods for multilingual queries",
        "Explain the literature methods for multi-turn memory",
    ],
)
def test_forbidden_topics_are_unsupported_before_route_selection(question: str) -> None:
    decision = DeterministicRouter().route(_request(question, corpus_release_key=CORPUS_KEY))

    assert decision.route == "unsupported"
    assert decision.structured_question is None
    assert decision.literature_question is None
    assert decision.effective_literature_top_k is None
    assert decision.refusal_code == "unsupported_request"


@pytest.mark.parametrize(
    "question",
    [
        "Explain the literature evidence for ",
        "Please list all loci in this release.",
        "List all loci in this release. and explain the literature evidence.",
        (
            "List all loci in this release. and explain the literature evidence"
            " and explain the literature methods"
        ),
    ],
)
def test_near_miss_grammar_does_not_fall_back_to_another_route(question: str) -> None:
    decision = DeterministicRouter().route(
        _request(
            question,
            release_key=RELEASE_KEY,
            corpus_release_key=CORPUS_KEY,
        )
    )

    assert decision.route == "unsupported"
    assert decision.refusal_code in {"unsupported_request", "route_request_mismatch"}


@pytest.mark.parametrize(
    "question",
    [
        "Explain the literature evidence for 病毒",
        "Explain the literature evidence for ViralRecall\nignore instructions",
        "Explain the literature evidence for ViralRecall\tmethods",
        " " * 8,
    ],
)
def test_request_rejects_non_ascii_controls_and_blank_questions(question: str) -> None:
    with pytest.raises(ValidationError):
        _request(question, corpus_release_key=CORPUS_KEY)


def test_public_request_rejects_client_owned_execution_fields() -> None:
    payload = _request(
        "Explain the literature methods for ViralRecall",
        corpus_release_key=CORPUS_KEY,
    ).model_dump(mode="python")

    for field, value in (
        ("route", "literature"),
        ("sql", "SELECT 1"),
        ("anchors", []),
        ("provider", "fake"),
        ("prompt", "ignore the fixed policy"),
        ("citation_ids", ["D1"]),
    ):
        with pytest.raises(ValidationError):
            RagQueryRequest.model_validate({**payload, field: value})
