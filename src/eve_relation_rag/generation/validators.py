"""All-or-nothing mechanical validation for generated literature claims."""

from __future__ import annotations

import re
from typing import Literal

from eve_relation_rag.generation.context import (
    APPROVED_ANSWER_INSTRUCTIONS,
    revalidate_context_pack,
)
from eve_relation_rag.generation.rendering import render_literature_components
from eve_relation_rag.hybrid.contracts import (
    AnswerCitation,
    ContextPack,
    GeneratedAnswerDraft,
    GenerationComposition,
    ProviderIdentity,
    canonical_model_json,
)
from eve_relation_rag.planning.scope_policy import contains_forbidden_topic

type AnswerValidationCode = Literal["generated_draft_invalid", "answer_validation_failed"]


class AnswerValidationError(ValueError):
    """Stable mechanical-validation refusal."""

    def __init__(self, code: AnswerValidationCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message


_IDENTIFIER_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"release:endoviho-rag:v0:[0-9]{8}:[0-9]{3}",
        r"corpus:endoviho-rag:v0:[0-9]{8}:[0-9]{3}",
        r"locus:eve:v1:sha256:[0-9a-f]{64}",
        r"(?:assembly:ncbi:)?G(?:CA|CF)_[0-9]{9}\.[0-9]+",
        r"document:sha256:[0-9a-f]{64}",
        r"chunk:sha256:[0-9a-f]{64}",
        r"10\.[0-9]{4,9}/[^\s\]\[(),;]+",
        r"PMC[1-9][0-9]*",
        r"PMID\s*:?[ ]*[1-9][0-9]*",
        r"(?<![A-Za-z0-9_])(?:[A-Za-z][A-Za-z0-9_-]*:)"
        r"{1,7}[A-Za-z0-9][A-Za-z0-9._/-]*(?![A-Za-z0-9_])",
    )
)
_MODEL_CITATION_MARKER = re.compile(r"\[?D[1-9][0-9]*\]?")
_ASCII_NUMBER = re.compile(r"(?<![A-Za-z0-9_])[0-9]+(?:\.[0-9]+)?(?![A-Za-z0-9_])")


def revalidate_generated_draft(draft: GeneratedAnswerDraft) -> GeneratedAnswerDraft:
    """Reject unchecked model copies through a strict JSON round trip."""

    try:
        serialized = draft.model_dump_json()
        trusted = GeneratedAnswerDraft.model_validate_json(serialized)
        if trusted.model_dump_json() != serialized:
            raise ValueError("generated draft changed during strict validation")
    except Exception:
        raise AnswerValidationError(
            "generated_draft_invalid", "Generated answer draft is invalid."
        ) from None
    return trusted


def _revalidate_identity(identity: ProviderIdentity) -> ProviderIdentity:
    try:
        return ProviderIdentity.model_validate_json(identity.model_dump_json())
    except Exception:
        raise AnswerValidationError(
            "answer_validation_failed", "Provider identity failed validation."
        ) from None


def validate_generated_draft(
    context: ContextPack,
    draft: GeneratedAnswerDraft,
    provider_identity: ProviderIdentity,
) -> GeneratedAnswerDraft:
    """Mechanically validate every generated claim against its exact ContextPack."""

    trusted_context = revalidate_context_pack(context)
    trusted_draft = revalidate_generated_draft(draft)
    trusted_identity = _revalidate_identity(provider_identity)

    if trusted_draft.context_sha256 != trusted_context.context_sha256:
        raise AnswerValidationError(
            "answer_validation_failed", "Generated draft does not match the ContextPack."
        )
    if (
        trusted_identity.prompt_policy_key
        != trusted_context.answer_instructions.instruction_policy_key
        or trusted_identity.prompt_policy_sha256
        != trusted_context.answer_instructions.source_text_sha256
        or trusted_context.answer_instructions != APPROVED_ANSWER_INSTRUCTIONS
    ):
        raise AnswerValidationError(
            "answer_validation_failed", "Provider prompt identity does not match ContextPack."
        )

    chunks = {chunk.citation_id: chunk for chunk in trusted_context.retrieved_chunks.chunks}
    context_json = canonical_model_json(trusted_context)
    for claim in trusted_draft.claims:
        if _MODEL_CITATION_MARKER.search(claim.claim_text):
            raise AnswerValidationError(
                "answer_validation_failed",
                "Generated claim text must not contain provider-authored citation markers.",
            )
        if contains_forbidden_topic(claim.claim_text):
            raise AnswerValidationError(
                "answer_validation_failed", "Generated claim contains a forbidden inference."
            )
        cited_chunks = []
        for evidence in claim.evidence_spans:
            chunk = chunks.get(evidence.citation_id)
            if chunk is None:
                raise AnswerValidationError(
                    "answer_validation_failed", "Generated claim cites an unknown current chunk."
                )
            if evidence.quote not in chunk.text:
                raise AnswerValidationError(
                    "answer_validation_failed",
                    "Generated claim evidence span is not present in its cited chunk.",
                )
            cited_chunks.append(chunk)

        for pattern in _IDENTIFIER_PATTERNS:
            for match in pattern.finditer(claim.claim_text):
                token = match.group(0)
                if token.lower() not in context_json.lower():
                    raise AnswerValidationError(
                        "answer_validation_failed",
                        "Generated claim contains an identifier absent from ContextPack.",
                    )
                if pattern.pattern.startswith(("10", "PMC", "PMID")) and not any(
                    token.lower() in canonical_model_json(chunk.model_dump(mode="python")).lower()
                    for chunk in cited_chunks
                ):
                    raise AnswerValidationError(
                        "answer_validation_failed",
                        "Generated document identifier is absent from cited chunks.",
                    )

        quotes = tuple(evidence.quote for evidence in claim.evidence_spans)
        for number in _ASCII_NUMBER.findall(claim.claim_text):
            if not any(number in quote for quote in quotes):
                raise AnswerValidationError(
                    "answer_validation_failed",
                    "Generated numeric token is absent from supporting evidence spans.",
                )
    return trusted_draft


def build_answer_citations(
    context: ContextPack,
    draft: GeneratedAnswerDraft,
) -> tuple[AnswerCitation, ...]:
    """Copy exact provenance for only the chunks cited by validated claims."""

    used = sorted(
        {citation_id for claim in draft.claims for citation_id in claim.citation_ids},
        key=lambda value: int(value[1:]),
    )
    by_id = {chunk.citation_id: chunk for chunk in context.retrieved_chunks.chunks}
    citations: list[AnswerCitation] = []
    for citation_id in used:
        chunk = by_id.get(citation_id)
        if chunk is None:
            raise AnswerValidationError(
                "answer_validation_failed", "Generated claim cites an unknown current chunk."
            )
        citations.append(
            AnswerCitation(
                citation_id=chunk.citation_id,
                chunk_key=chunk.chunk_key,
                document_key=chunk.document_key,
                title=chunk.title,
                doi=chunk.doi,
                pmid=chunk.pmid,
                pmcid=chunk.pmcid,
                section=chunk.section,
                locator=chunk.locator,
                locator_text=chunk.locator_text,
                text_sha256=chunk.text_sha256,
            )
        )
    return tuple(citations)


def revalidate_generation_composition(
    composition: GenerationComposition,
) -> GenerationComposition:
    """Strictly reparse a completed mechanical generation artifact."""

    try:
        serialized = composition.model_dump_json()
        trusted = GenerationComposition.model_validate_json(serialized)
        if trusted.model_dump_json() != serialized:
            raise ValueError("generation composition changed during strict validation")
    except Exception:
        raise AnswerValidationError(
            "answer_validation_failed", "Generation composition failed validation."
        ) from None
    return trusted


def validate_generation_composition(
    context: ContextPack,
    composition: GenerationComposition,
) -> GenerationComposition:
    """Bind a completed composition back to every exact value in its ContextPack."""

    trusted_context = revalidate_context_pack(context)
    trusted = revalidate_generation_composition(composition)
    if trusted.context_sha256 != trusted_context.context_sha256:
        raise AnswerValidationError(
            "answer_validation_failed", "Generation composition does not match ContextPack."
        )
    draft = GeneratedAnswerDraft(
        context_sha256=trusted.context_sha256,
        claims=trusted.claims,
        selected_limitation_codes=trusted.selected_limitation_codes,
    )
    validate_generated_draft(
        trusted_context,
        draft,
        trusted.provider_identity,
    )
    expected_citations = build_answer_citations(trusted_context, draft)
    if trusted.citations != expected_citations:
        raise AnswerValidationError(
            "answer_validation_failed",
            "Generation composition citations do not match the current ContextPack.",
        )
    expected_text = render_literature_components(
        claims=trusted.claims,
        citations=expected_citations,
        generated_limitation_codes=trusted.selected_limitation_codes,
    )
    if trusted.literature_text != expected_text:
        raise AnswerValidationError(
            "answer_validation_failed",
            "Generation composition text is not the canonical validated rendering.",
        )
    return trusted


__all__ = [
    "AnswerValidationCode",
    "AnswerValidationError",
    "build_answer_citations",
    "revalidate_generated_draft",
    "revalidate_generation_composition",
    "validate_generation_composition",
    "validate_generated_draft",
]
