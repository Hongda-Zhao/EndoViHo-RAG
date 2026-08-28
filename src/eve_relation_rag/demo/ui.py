"""English-only Streamlit evidence workbench for the V0 HTTP contract."""

from __future__ import annotations

from typing import Any

import streamlit as st
from pydantic import ValidationError

from eve_relation_rag.demo.client import DemoClientError, submit_query
from eve_relation_rag.demo.examples import DemoExample, load_demo_examples
from eve_relation_rag.demo.presentation import execution_stages, response_details, response_label
from eve_relation_rag.hybrid.contracts import (
    HybridRouteAnswer,
    LiteratureRouteAnswer,
    RagErrorResponse,
    RagQueryRequest,
    RagResponse,
    StructuredRouteAnswer,
)

_STATIC_CSS = """
<style>
:root {
  --petri-blue: #163A70;
  --sequence-cyan: #00A6A6;
  --helix-violet: #6E56CF;
  --signal-coral: #E86655;
  --lab-ice: #F3F8FC;
}
.stApp { background: linear-gradient(135deg, #F8FBFD 0%, #EEF6FA 100%); }
h1, h2, h3 { font-family: "Avenir Next", "Segoe UI", sans-serif; letter-spacing: -0.02em; }
[data-testid="stMetric"] {
  background: rgba(255,255,255,.78);
  border: 1px solid rgba(22,58,112,.14);
  border-top: 4px solid var(--sequence-cyan);
  border-radius: 4px;
  padding: .65rem .8rem;
}
[data-testid="stSidebar"] { border-right: 1px solid rgba(22,58,112,.16); }
code, pre { font-family: "SFMono-Regular", Consolas, monospace !important; }
@media (prefers-reduced-motion: reduce) {
  * { scroll-behavior: auto !important; transition: none !important; }
}
</style>
"""


def render_app() -> None:
    """Render the local V0 demo without touching a database or model directly."""

    st.set_page_config(
        page_title="EndoViHo Evidence Workbench",
        page_icon="🧬",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.html(_STATIC_CSS)
    _render_sidebar()

    st.caption("ENDOVIHO / V0 CONTROLLED-EVIDENCE INTERFACE")
    st.title("Evidence Workbench")
    st.write(
        "Submit one controlled-English question to the routed API, then inspect exactly which "
        "evidence stages executed. Refusals are first-class results."
    )

    examples = load_demo_examples()
    by_title = {example.title: example for example in examples}
    selected_title = st.selectbox(
        "Example question",
        tuple(by_title),
        help="Examples carry fixed release selectors; this is not a client-owned route control.",
    )
    example = by_title[selected_title]
    _render_example_context(example)

    question = st.text_area(
        "Controlled-English question",
        value=example.request.question,
        height=110,
        key=f"question-{example.example_key}",
        help="Printable ASCII English only. The server owns route selection.",
    )
    st.code(_selector_summary(example), language="text")

    if st.button("Run evidence trace", type="primary", use_container_width=True):
        _run_request(example, question)


def _render_sidebar() -> None:
    with st.sidebar:
        st.subheader("Release boundary")
        st.write("Product version: **V0**")
        st.warning("Engineering preview. Real scientific activation is blocked.")
        st.markdown(
            "- PostgreSQL is the structured truth source.\n"
            "- Literature is explanatory evidence.\n"
            "- Generation is mechanical-validation only.\n"
            "- No live web search or test-only success mode."
        )
        st.caption("The API origin is fixed by the server environment and is never browser input.")


def _render_example_context(example: DemoExample) -> None:
    left, right = st.columns((1, 1))
    with left:
        st.caption("PURPOSE")
        st.write(example.purpose)
    with right:
        st.caption("CURRENT REAL STATE")
        st.info(example.current_outcome)
    with st.expander("Activation boundary", expanded=False):
        st.write(example.activation_blocker)


def _selector_summary(example: DemoExample) -> str:
    request = example.request
    return "\n".join(
        (
            f"structured release: {request.release_key or 'not requested'}",
            f"literature corpus: {request.corpus_release_key or 'not requested'}",
            f"top_k: {request.literature_top_k or 'server default / not applicable'}",
        )
    )


def _run_request(example: DemoExample, question: str) -> None:
    try:
        request = RagQueryRequest.model_validate(
            {**example.request.model_dump(), "question": question}
        )
    except ValidationError:
        st.error("The question does not satisfy the strict V0 request contract.")
        return

    with st.spinner("Tracing the server-owned evidence path..."):
        try:
            result = submit_query(request)
        except (DemoClientError, ValueError) as exc:
            st.error(str(exc))
            return
    _render_response(result.response, result.status_code)


def _render_response(response: RagResponse, status_code: int) -> None:
    st.divider()
    st.caption("SERVER-VALIDATED RESULT")
    st.subheader(response_label(response))
    st.caption(f"HTTP {status_code} · route: {response.route or 'unresolved'}")

    columns = st.columns(3)
    for column, stage in zip(columns, execution_stages(response), strict=True):
        column.metric(
            f"0{stage.sequence} / {stage.label}",
            stage.state.upper(),
            help="Derived only from canonical server execution flags.",
        )

    if isinstance(response, RagErrorResponse):
        st.error(response.message)
        if response.upstream_code is not None:
            st.caption(f"Upstream refusal: {response.upstream_code}")
    elif isinstance(response, StructuredRouteAnswer):
        st.subheader("Structured result")
        st.text(response.structured_text)
        _render_json("Validated query artifact", response.query_success.model_dump(mode="json"))
    elif isinstance(response, LiteratureRouteAnswer):
        st.subheader("Validated literature answer")
        st.text(response.answer_text)
    elif isinstance(response, HybridRouteAnswer):
        st.subheader("Validated hybrid answer")
        st.text(response.answer_text)

    details = response_details(response)
    if details.limitation_codes or details.anchor_diagnostics or details.validation_scope:
        st.subheader("Validation and limitations")
        if details.validation_scope is not None:
            st.text(f"validation scope: {details.validation_scope}")
        for code in details.anchor_diagnostics:
            st.text(f"anchor diagnostic: {code}")
        for code in details.limitation_codes:
            st.text(f"limitation: {code}")
    _render_citations(details.citations)

    st.warning(
        "Citation and identifier checks are mechanical. They do not establish semantic "
        "entailment or biological truth."
    )
    _render_json("Canonical response envelope", response.model_dump(mode="json"))


def _render_citations(citations: tuple[Any, ...]) -> None:
    st.subheader("Evidence provenance")
    if not citations:
        st.info("No citation survived the validated response contract.")
        return
    for citation in citations:
        with st.container(border=True):
            st.text(f"{citation.citation_id} · {citation.title}")
            st.code(
                "\n".join(
                    (
                        citation.locator_text,
                        f"document {citation.document_key}",
                        f"chunk {citation.chunk_key}",
                        f"text sha256 {citation.text_sha256}",
                    )
                ),
                language="text",
            )


def _render_json(label: str, value: Any) -> None:
    with st.expander(label, expanded=False):
        st.json(value, expanded=False)


__all__ = ["render_app"]
