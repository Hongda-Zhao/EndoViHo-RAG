"""Pinned-provider invocation and all-or-nothing generated answer composition."""

from __future__ import annotations

import hashlib
from typing import Literal

from eve_relation_rag.generation.context import (
    ContextBuildError,
    canonical_context_json,
    revalidate_context_pack,
)
from eve_relation_rag.generation.providers import LLMProvider
from eve_relation_rag.generation.rendering import render_literature_components
from eve_relation_rag.generation.validators import (
    AnswerValidationError,
    build_answer_citations,
    validate_generated_draft,
)
from eve_relation_rag.hybrid.contracts import (
    MAX_GENERATED_OUTPUT_BYTES,
    ContextPack,
    GeneratedAnswerDraft,
    GenerationComposition,
    ProviderIdentity,
)

type GenerationComposerCode = Literal[
    "insufficient_evidence",
    "context_integrity_error",
    "context_too_large",
    "llm_provider_unavailable",
    "generation_failed",
    "generated_draft_invalid",
    "answer_validation_failed",
]


class GenerationComposerError(RuntimeError):
    """Sanitized composer failure with exact invocation state for transport flags."""

    def __init__(
        self,
        code: GenerationComposerCode,
        message: str,
        *,
        generation_executed: bool,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.generation_executed = generation_executed


def _trusted_identity(identity: ProviderIdentity) -> ProviderIdentity:
    try:
        return ProviderIdentity.model_validate_json(identity.model_dump_json())
    except Exception:
        raise GenerationComposerError(
            "llm_provider_unavailable",
            "The configured LLM provider identity is unavailable.",
            generation_executed=False,
        ) from None


class GenerationComposer:
    """Invoke one exact provider once and return a mechanically validated composition."""

    def __init__(self, *, provider: LLMProvider, expected_identity: ProviderIdentity) -> None:
        self._provider = provider
        self._expected_identity = _trusted_identity(expected_identity)

    def compose(self, context: ContextPack) -> GenerationComposition:
        """Compose one generated artifact; never retry or return a partial draft."""

        try:
            trusted_context = revalidate_context_pack(context)
        except ContextBuildError as error:
            raise GenerationComposerError(
                error.code,
                error.public_message,
                generation_executed=False,
            ) from None
        if not trusted_context.retrieved_chunks.chunks:
            raise GenerationComposerError(
                "insufficient_evidence",
                "The approved corpus supplied no chunks for generation.",
                generation_executed=False,
            )

        try:
            observed_identity = _trusted_identity(self._provider.identity)
        except GenerationComposerError:
            raise
        except Exception:
            raise GenerationComposerError(
                "llm_provider_unavailable",
                "The configured LLM provider identity is unavailable.",
                generation_executed=False,
            ) from None
        if observed_identity != self._expected_identity:
            raise GenerationComposerError(
                "llm_provider_unavailable",
                "The configured LLM provider identity does not match the approved identity.",
                generation_executed=False,
            )
        if (
            observed_identity.prompt_policy_key
            != trusted_context.answer_instructions.instruction_policy_key
            or observed_identity.prompt_policy_sha256
            != trusted_context.answer_instructions.source_text_sha256
        ):
            raise GenerationComposerError(
                "llm_provider_unavailable",
                "The configured LLM provider prompt identity is not approved for ContextPack.",
                generation_executed=False,
            )

        context_json = canonical_context_json(trusted_context)
        try:
            raw_output = self._provider.generate(context_json)
        except Exception:
            raise GenerationComposerError(
                "generation_failed",
                "The configured LLM provider failed.",
                generation_executed=True,
            ) from None
        if not isinstance(raw_output, str):
            raise GenerationComposerError(
                "generated_draft_invalid",
                "The LLM provider returned an invalid generated draft.",
                generation_executed=True,
            )
        try:
            raw_output_bytes = raw_output.encode("utf-8")
            output_too_large = len(raw_output_bytes) > MAX_GENERATED_OUTPUT_BYTES
        except Exception:
            raise GenerationComposerError(
                "generated_draft_invalid",
                "The LLM provider returned an invalid generated draft.",
                generation_executed=True,
            ) from None
        if output_too_large:
            raise GenerationComposerError(
                "generated_draft_invalid",
                "The LLM provider output exceeds the approved byte limit.",
                generation_executed=True,
            )
        try:
            draft = GeneratedAnswerDraft.model_validate_json(raw_output)
        except Exception:
            raise GenerationComposerError(
                "generated_draft_invalid",
                "The LLM provider returned an invalid generated draft.",
                generation_executed=True,
            ) from None
        try:
            trusted_draft = validate_generated_draft(
                trusted_context,
                draft,
                observed_identity,
            )
            citations = build_answer_citations(trusted_context, trusted_draft)
        except AnswerValidationError as error:
            raise GenerationComposerError(
                error.code,
                error.public_message,
                generation_executed=True,
            ) from None

        literature_text = render_literature_components(
            claims=trusted_draft.claims,
            citations=citations,
            generated_limitation_codes=trusted_draft.selected_limitation_codes,
        )
        return GenerationComposition(
            context_sha256=trusted_context.context_sha256,
            provider_identity=observed_identity,
            claims=trusted_draft.claims,
            selected_limitation_codes=trusted_draft.selected_limitation_codes,
            citations=citations,
            literature_text=literature_text,
            literature_text_sha256=hashlib.sha256(literature_text.encode("utf-8")).hexdigest(),
            validation_scope="mechanical",
        )


AnswerComposer = GenerationComposer


__all__ = [
    "AnswerComposer",
    "GenerationComposer",
    "GenerationComposerCode",
    "GenerationComposerError",
]
