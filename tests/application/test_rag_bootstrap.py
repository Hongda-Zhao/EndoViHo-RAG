from __future__ import annotations

import pytest

import eve_relation_rag.bootstrap as bootstrap
from eve_relation_rag.hybrid.bindings import UnavailableHybridBindingRegistry
from eve_relation_rag.hybrid.contracts import ExecutionFlags, RagErrorResponse, RagQueryRequest


def test_production_rag_bootstrap_is_dependency_lazy_for_unsupported_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def unavailable_engine() -> None:
        calls.append("engine")
        raise AssertionError("unsupported route constructed a database dependency")

    bootstrap.get_rag_query_application.cache_clear()
    monkeypatch.setattr(bootstrap, "get_engine", unavailable_engine)
    try:
        application = bootstrap.get_rag_query_application()
        response = application.query(
            RagQueryRequest(
                corpus_release_key="corpus:endoviho-rag:v0:20991231:999",
                question="Explain the literature evidence for prevalence in birds",
            )
        )
    finally:
        bootstrap.get_rag_query_application.cache_clear()

    assert isinstance(response, RagErrorResponse)
    assert response.code == "unsupported_request"
    assert calls == []


def test_real_zhao_and_published_corpus_pair_fails_closed_before_downstream_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def forbidden_dependency(name: str) -> None:
        calls.append(name)
        raise AssertionError(f"unapproved real hybrid pair constructed {name}")

    bootstrap.get_rag_query_application.cache_clear()
    monkeypatch.setattr(
        bootstrap,
        "get_hybrid_binding_registry",
        lambda: UnavailableHybridBindingRegistry(),
    )
    monkeypatch.setattr(
        bootstrap,
        "get_structured_query_application",
        lambda: forbidden_dependency("structured application"),
    )
    monkeypatch.setattr(
        bootstrap,
        "get_literature_retrieval_service",
        lambda: forbidden_dependency("literature service"),
    )
    monkeypatch.setattr(
        bootstrap,
        "get_engine",
        lambda: forbidden_dependency("database engine"),
    )
    try:
        application = bootstrap.get_rag_query_application()
        response = application.query(
            RagQueryRequest(
                release_key="release:endoviho-rag:v0:20260826:001",
                corpus_release_key="corpus:endoviho-rag:v0:20260828:001",
                question=(
                    "Count distinct included loci in this release. "
                    "and explain the literature evidence"
                ),
            )
        )
    finally:
        bootstrap.get_rag_query_application.cache_clear()

    assert isinstance(response, RagErrorResponse)
    assert response.code == "hybrid_binding_unavailable"
    assert response.requested_release_key == "release:endoviho-rag:v0:20260826:001"
    assert response.requested_corpus_release_key == "corpus:endoviho-rag:v0:20260828:001"
    assert response.execution == ExecutionFlags(
        structured_retrieval_executed=False,
        literature_retrieval_executed=False,
        generation_executed=False,
    )
    assert calls == []
