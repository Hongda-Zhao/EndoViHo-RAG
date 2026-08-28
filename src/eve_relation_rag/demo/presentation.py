"""Pure presentation helpers shared by the demo and its tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from eve_relation_rag.hybrid.contracts import (
    AnswerCitation,
    ExecutionFlags,
    HybridRouteAnswer,
    LiteratureRouteAnswer,
    RagErrorResponse,
    RagResponse,
    StructuredRouteAnswer,
)


@dataclass(frozen=True, slots=True)
class ExecutionStage:
    sequence: Literal[1, 2, 3]
    label: str
    state: Literal["executed", "held"]


@dataclass(frozen=True, slots=True)
class CitationProvenance:
    citation_id: str
    document_key: str
    chunk_key: str
    title: str
    locator_text: str
    text_sha256: str


@dataclass(frozen=True, slots=True)
class ResponseDetails:
    limitation_codes: tuple[str, ...]
    anchor_diagnostics: tuple[str, ...]
    validation_scope: str | None
    citations: tuple[CitationProvenance, ...]


def execution_stages(response: RagResponse) -> tuple[ExecutionStage, ...]:
    """Map server-owned execution flags onto the three-stage provenance rail."""

    flags: ExecutionFlags = response.execution
    return (
        ExecutionStage(
            sequence=1,
            label="Structured truth",
            state="executed" if flags.structured_retrieval_executed else "held",
        ),
        ExecutionStage(
            sequence=2,
            label="Literature evidence",
            state="executed" if flags.literature_retrieval_executed else "held",
        ),
        ExecutionStage(
            sequence=3,
            label="Constrained generation",
            state="executed" if flags.generation_executed else "held",
        ),
    )


def response_label(response: RagResponse) -> str:
    """Return a concise stable label without interpreting scientific content."""

    if isinstance(response, RagErrorResponse):
        return f"NOT COMPLETED / {response.code}"
    return f"VALIDATED / {response.response_kind}"


def response_details(response: RagResponse) -> ResponseDetails:
    """Extract provenance and limitations without interpreting scientific claims."""

    limitation_codes: tuple[str, ...] = ()
    anchor_diagnostics: tuple[str, ...] = ()
    validation_scope: str | None = None
    raw_citations: tuple[AnswerCitation, ...] = ()
    if isinstance(response, StructuredRouteAnswer):
        limitation_codes = tuple(
            item.code for item in response.query_success.structured_result.limitations
        )
    elif isinstance(response, LiteratureRouteAnswer):
        limitation_codes = response.generation.selected_limitation_codes
        validation_scope = response.generation.validation_scope
        raw_citations = response.generation.citations
    elif isinstance(response, HybridRouteAnswer):
        anchor_diagnostics = response.anchor_diagnostics
        if response.insufficient_evidence_limitation is not None:
            limitation_codes = (response.insufficient_evidence_limitation,)
        elif response.generation is not None:
            limitation_codes = response.generation.selected_limitation_codes
            validation_scope = response.generation.validation_scope
            raw_citations = response.generation.citations
    return ResponseDetails(
        limitation_codes=limitation_codes,
        anchor_diagnostics=anchor_diagnostics,
        validation_scope=validation_scope,
        citations=tuple(
            CitationProvenance(
                citation_id=item.citation_id,
                document_key=item.document_key,
                chunk_key=item.chunk_key,
                title=item.title,
                locator_text=item.locator_text,
                text_sha256=item.text_sha256,
            )
            for item in raw_citations
        ),
    )


__all__ = [
    "CitationProvenance",
    "ExecutionStage",
    "ResponseDetails",
    "execution_stages",
    "response_details",
    "response_label",
]
