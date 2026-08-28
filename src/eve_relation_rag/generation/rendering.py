"""Deterministic English rendering from already validated M4 values."""

from __future__ import annotations

from collections.abc import Sequence

from eve_relation_rag.hybrid.contracts import (
    AnswerCitation,
    GeneratedLimitationCode,
    GenerationComposition,
    LiteratureClaim,
    canonical_model_json,
)
from eve_relation_rag.retrieval.structured.rendering import render_structured_result_text
from eve_relation_rag.retrieval.structured.results import Limitation, QuerySuccess, StructuredResult

INSUFFICIENT_EVIDENCE_TEXT = (
    "The approved corpus provided insufficient evidence for a literature claim."
)

GENERATED_LIMITATION_MESSAGES: dict[GeneratedLimitationCode, str] = {
    "insufficient_literature_evidence": (
        "No supported literature claim was generated from the retrieved chunks."
    ),
    "literature_evidence_is_explanatory": (
        "Literature evidence is explanatory and does not replace structured facts."
    ),
    "mechanical_validation_is_not_semantic_entailment": (
        "Mechanical citation validation does not establish semantic or scientific entailment."
    ),
}


def _identifier_text(citation: AnswerCitation) -> str:
    identifiers = tuple(
        item
        for item in (
            f"doi:{citation.doi}" if citation.doi is not None else None,
            f"pmid:{citation.pmid}" if citation.pmid is not None else None,
            f"pmcid:{citation.pmcid}" if citation.pmcid is not None else None,
        )
        if item is not None
    )
    return ",".join(identifiers) if identifiers else "none"


def render_citation(citation: AnswerCitation) -> str:
    """Render one response-local citation without adding inferred metadata."""

    section = citation.section if citation.section is not None else "none"
    return (
        f"[{citation.citation_id}] document_key={citation.document_key}; "
        f"title={canonical_model_json(citation.title)}; identifiers={_identifier_text(citation)}; "
        f"section={canonical_model_json(section)}; "
        f"locator={canonical_model_json(citation.locator)}; "
        f"locator_text={canonical_model_json(citation.locator_text)}; "
        f"chunk_key={citation.chunk_key}; text_sha256={citation.text_sha256}"
    )


def render_literature_components(
    *,
    claims: Sequence[LiteratureClaim],
    citations: Sequence[AnswerCitation],
    generated_limitation_codes: Sequence[GeneratedLimitationCode],
    upstream_limitations: Sequence[Limitation] = (),
) -> str:
    """Render validated claims, limitations, and citations in canonical order."""

    claim_lines = (
        tuple(f"{claim.claim_text} [{', '.join(claim.citation_ids)}]" for claim in claims)
        if claims
        else (INSUFFICIENT_EVIDENCE_TEXT,)
    )
    limitation_lines = tuple(
        f"- {limitation.code}: {limitation.message}" for limitation in upstream_limitations
    ) + tuple(
        f"- {code}: {GENERATED_LIMITATION_MESSAGES[code]}" for code in generated_limitation_codes
    )
    citation_lines = tuple(render_citation(citation) for citation in citations) or ("None.",)
    return "\n".join(
        (
            "Literature",
            *claim_lines,
            "",
            "Limitations",
            *limitation_lines,
            "",
            "Citations",
            *citation_lines,
        )
    )


def render_literature_answer_text(composition: GenerationComposition) -> str:
    """Return the already checksum-bound deterministic literature rendering."""

    expected = render_literature_components(
        claims=composition.claims,
        citations=composition.citations,
        generated_limitation_codes=composition.selected_limitation_codes,
    )
    if expected != composition.literature_text:
        raise ValueError("generation composition does not contain canonical literature text")
    return expected


def render_hybrid_answer_text(
    structured_result: StructuredResult,
    composition: GenerationComposition,
) -> str:
    """Render unchanged structured data followed by validated literature content."""

    literature = render_literature_components(
        claims=composition.claims,
        citations=composition.citations,
        generated_limitation_codes=composition.selected_limitation_codes,
        upstream_limitations=structured_result.limitations,
    )
    structured = "Structured\n" + render_structured_result_text(structured_result)
    return "\n\n".join((structured, literature))


def render_structured_answer_text(query_success: QuerySuccess) -> str:
    """Render a structured route directly from the unchanged M2 result."""

    return render_structured_result_text(query_success.structured_result)


def render_hybrid_insufficient_answer_text(query_success: QuerySuccess) -> str:
    """Render unchanged structured facts plus the fixed zero-chunk limitation."""

    literature = render_literature_components(
        claims=(),
        citations=(),
        generated_limitation_codes=(
            "insufficient_literature_evidence",
            "literature_evidence_is_explanatory",
            "mechanical_validation_is_not_semantic_entailment",
        ),
        upstream_limitations=query_success.structured_result.limitations,
    )
    structured = "Structured\n" + render_structured_result_text(query_success.structured_result)
    return "\n\n".join((structured, literature))


render_literature_answer = render_literature_answer_text
render_hybrid_answer = render_hybrid_answer_text


__all__ = [
    "GENERATED_LIMITATION_MESSAGES",
    "INSUFFICIENT_EVIDENCE_TEXT",
    "render_citation",
    "render_hybrid_answer",
    "render_hybrid_answer_text",
    "render_hybrid_insufficient_answer_text",
    "render_literature_answer",
    "render_literature_answer_text",
    "render_literature_components",
    "render_structured_answer_text",
]
