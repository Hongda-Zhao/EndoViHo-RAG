"""Deterministic blinded review export, import validation, and agreement."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    EvaluationAnswer,
    EvaluationEvidencePack,
    QuestionFamily,
    SupportLabel,
    SystemKey,
)
from eve_relation_rag.experiments.rag_value_ablation.metrics import RatioMetric, ratio
from eve_relation_rag.literature.contracts import (
    NonEmptyText,
    QuestionText,
    Rfc3339Utc,
    Sha256,
    StableToken,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.hashing import canonical_json_sha256

_KAPPA_QUANTUM = Decimal("0.000000000001")
_KAPPA_PATTERN = r"^-?(?:0|1)\.[0-9]{12}$"
_REVIEW_ATTESTATION = (
    "I independently reviewed every assigned claim against only the supplied benchmark evidence."
)


class HumanReviewError(ValueError):
    """Raised when blinding, review completeness, or independence is invalid."""


class ReviewSourceAnswer(StrictFrozenSchema):
    """Internal unblinded input used only to construct a reviewer packet."""

    system_key: SystemKey
    question_id: StableToken
    family: QuestionFamily
    question_text: QuestionText
    answer: EvaluationAnswer
    answer_sha256: Sha256
    evidence: EvaluationEvidencePack

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        if self.question_id != self.evidence.question_id:
            raise ValueError("review source question ID does not match evidence")
        if self.question_text != self.evidence.question_text:
            raise ValueError("review source question text does not match evidence")
        if self.answer_sha256 != canonical_json_sha256(self.answer):
            raise ValueError("review source answer checksum does not match")
        return self


class BlindedCitation(StrictFrozenSchema):
    """One exact cited passage shown to a reviewer without retrieval metadata."""

    citation_id: StableToken
    source_kind: Literal["literature_chunk", "structured_export", "document"]
    source_key: StableToken
    document_key: StableToken | None = None
    chunk_key: StableToken | None = None
    locator_text: NonEmptyText
    passage_text: NonEmptyText
    passage_sha256: Sha256

    @model_validator(mode="after")
    def validate_source_identity(self) -> Self:
        has_chunk_identity = self.document_key is not None and self.chunk_key is not None
        if self.source_kind == "literature_chunk" and not has_chunk_identity:
            raise ValueError("literature review evidence requires document and chunk keys")
        if self.source_kind != "literature_chunk" and (
            self.document_key is not None or self.chunk_key is not None
        ):
            raise ValueError("raw review evidence must use only its source key")
        return self


class BlindedClaim(StrictFrozenSchema):
    """One claim target whose opaque identity is bound to exact claim bytes."""

    blind_claim_id: StableToken
    claim_sha256: Sha256
    text: NonEmptyText
    claim_type: StableToken
    citation_ids: tuple[StableToken, ...]


class BlindedAnswer(StrictFrozenSchema):
    """One system-hidden answer and its atomic claims/evidence."""

    blind_answer_id: StableToken
    answer_sha256: Sha256
    family: QuestionFamily
    question_text: QuestionText
    answer_text: NonEmptyText
    abstained: bool
    claims: tuple[BlindedClaim, ...]
    limitations: tuple[NonEmptyText, ...]
    citations: tuple[BlindedCitation, ...]

    @model_validator(mode="after")
    def validate_ids(self) -> Self:
        claim_ids = tuple(claim.blind_claim_id for claim in self.claims)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("blinded claim IDs must be unique")
        citation_ids = tuple(citation.citation_id for citation in self.citations)
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("blinded citation IDs must be unique")
        return self


class HumanReviewPacket(StrictFrozenSchema):
    """System-blinded packet supplied to each independent domain reviewer."""

    packet_schema_version: Literal["rag-value-human-review-packet-v1"] = (
        "rag-value-human-review-packet-v1"
    )
    rubric_version: Literal["rag-value-claim-support-rubric-v1"] = (
        "rag-value-claim-support-rubric-v1"
    )
    shuffle_seed_sha256: Sha256
    answers: tuple[BlindedAnswer, ...] = Field(min_length=1)
    packet_sha256: Sha256

    @model_validator(mode="after")
    def validate_packet(self) -> Self:
        answer_ids = tuple(answer.blind_answer_id for answer in self.answers)
        if len(answer_ids) != len(set(answer_ids)):
            raise ValueError("blinded answer IDs must be unique")
        claim_ids = tuple(
            claim.blind_claim_id for answer in self.answers for claim in answer.claims
        )
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("packet-wide blinded claim IDs must be unique")
        if self.packet_sha256 != _self_sha256(self, "packet_sha256"):
            raise ValueError("human review packet checksum does not match")
        return self


class UnblindingEntry(StrictFrozenSchema):
    """Withheld map from opaque answer/claim IDs to their originating condition."""

    blind_answer_id: StableToken
    system_key: SystemKey
    question_id: StableToken
    answer_sha256: Sha256
    claim_ids: dict[StableToken, StableToken]

    @field_validator("claim_ids")
    @classmethod
    def canonical_claim_map(cls, values: dict[str, str]) -> dict[str, str]:
        if tuple(values) != tuple(sorted(values)):
            raise ValueError("unblinding claim map must be sorted")
        if len(values.values()) != len(set(values.values())):
            raise ValueError("unblinding claim targets must be unique")
        return values


class UnblindingMap(StrictFrozenSchema):
    """Checksum-bound map withheld until reviews and adjudication are complete."""

    map_schema_version: Literal["rag-value-unblinding-map-v1"] = (
        "rag-value-unblinding-map-v1"
    )
    packet_sha256: Sha256
    entries: tuple[UnblindingEntry, ...] = Field(min_length=1)
    map_sha256: Sha256

    @model_validator(mode="after")
    def validate_map(self) -> Self:
        ids = tuple(entry.blind_answer_id for entry in self.entries)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise ValueError("unblinding entries must be sorted by unique blind_answer_id")
        if self.map_sha256 != _self_sha256(self, "map_sha256"):
            raise ValueError("unblinding map checksum does not match")
        return self


def build_blinded_review_packet(
    sources: Sequence[ReviewSourceAnswer],
    *,
    shuffle_seed: str,
) -> tuple[HumanReviewPacket, UnblindingMap]:
    """Build a deterministic system-hidden packet and separately withheld map."""

    if not sources:
        raise HumanReviewError("review packet requires at least one answer")
    if not shuffle_seed:
        raise HumanReviewError("review packet requires an explicit non-empty shuffle seed")
    source_keys = tuple((source.system_key, source.question_id) for source in sources)
    if len(source_keys) != len(set(source_keys)):
        raise HumanReviewError("review sources must be unique by system and question")
    seed_sha256 = hashlib.sha256(shuffle_seed.encode("utf-8")).hexdigest()
    ordered = tuple(
        sorted(
            sources,
            key=lambda source: hashlib.sha256(
                f"{shuffle_seed}\0{source.system_key}\0{source.question_id}".encode()
            ).hexdigest(),
        )
    )
    blinded_answers: list[BlindedAnswer] = []
    unblinding_entries: list[UnblindingEntry] = []
    seen_blind_ids: set[str] = set()
    for source in ordered:
        blind_answer_id = _opaque_id(
            "A", shuffle_seed, source.system_key, source.question_id, source.answer_sha256
        )
        if blind_answer_id in seen_blind_ids:
            raise HumanReviewError("opaque answer ID collision")
        seen_blind_ids.add(blind_answer_id)
        blinded_claims: list[BlindedClaim] = []
        claim_map: dict[str, str] = {}
        cited_ids: set[str] = set()
        for claim in source.answer.claims:
            claim_sha256 = canonical_json_sha256(claim)
            blind_claim_id = _opaque_id(
                "K", shuffle_seed, blind_answer_id, claim.claim_id, claim_sha256
            )
            if blind_claim_id in seen_blind_ids:
                raise HumanReviewError("opaque claim ID collision")
            seen_blind_ids.add(blind_claim_id)
            blinded_claims.append(
                BlindedClaim(
                    blind_claim_id=blind_claim_id,
                    claim_sha256=claim_sha256,
                    text=claim.text,
                    claim_type=claim.claim_type,
                    citation_ids=claim.citation_ids,
                )
            )
            claim_map[blind_claim_id] = claim.claim_id
            cited_ids.update(claim.citation_ids)
        citation_by_id = {
            citation.citation_id: citation for citation in source.evidence.citations
        }
        raw_by_id = {
            segment.segment_id: segment
            for segment in source.evidence.raw_context_segments
        }
        missing_citations = cited_ids - set(citation_by_id) - set(raw_by_id)
        if missing_citations:
            raise HumanReviewError("answer cites evidence absent from its evidence pack")
        blinded_citations = tuple(
            sorted(
                (
                    *(
                        BlindedCitation(
                            citation_id=citation.citation_id,
                            source_kind="literature_chunk",
                            source_key=citation.document_key,
                            document_key=citation.document_key,
                            chunk_key=citation.chunk_key,
                            locator_text=citation.locator_text,
                            passage_text=citation.text,
                            passage_sha256=citation.text_sha256,
                        )
                        for citation in source.evidence.citations
                        if citation.citation_id in cited_ids
                    ),
                    *(
                        BlindedCitation(
                            citation_id=segment.segment_id,
                            source_kind=segment.source_kind,
                            source_key=segment.source_key,
                            locator_text=(
                                f"bytes {segment.byte_start}:{segment.byte_end}"
                            ),
                            passage_text=segment.text,
                            passage_sha256=segment.text_sha256,
                        )
                        for segment in source.evidence.raw_context_segments
                        if segment.segment_id in cited_ids
                    ),
                ),
                key=lambda item: (item.citation_id[0], int(item.citation_id[1:])),
            )
        )
        blinded_answers.append(
            BlindedAnswer(
                blind_answer_id=blind_answer_id,
                answer_sha256=source.answer_sha256,
                family=source.family,
                question_text=source.question_text,
                answer_text=source.answer.answer_text,
                abstained=source.answer.abstained,
                claims=tuple(blinded_claims),
                limitations=source.answer.limitations,
                citations=blinded_citations,
            )
        )
        unblinding_entries.append(
            UnblindingEntry(
                blind_answer_id=blind_answer_id,
                system_key=source.system_key,
                question_id=source.question_id,
                answer_sha256=source.answer_sha256,
                claim_ids=dict(sorted(claim_map.items())),
            )
        )
    packet_payload: dict[str, object] = {
        "packet_schema_version": "rag-value-human-review-packet-v1",
        "rubric_version": "rag-value-claim-support-rubric-v1",
        "shuffle_seed_sha256": seed_sha256,
        "answers": tuple(blinded_answers),
    }
    packet = HumanReviewPacket.model_validate(
        {**packet_payload, "packet_sha256": canonical_json_sha256(packet_payload)}
    )
    map_payload: dict[str, object] = {
        "map_schema_version": "rag-value-unblinding-map-v1",
        "packet_sha256": packet.packet_sha256,
        "entries": tuple(
            sorted(unblinding_entries, key=lambda entry: entry.blind_answer_id)
        ),
    }
    unblinding = UnblindingMap.model_validate(
        {**map_payload, "map_sha256": canonical_json_sha256(map_payload)}
    )
    return packet, unblinding


class CitationReviewDecision(StrictFrozenSchema):
    """One passage-specific human judgment for one citation attached to a claim."""

    citation_id: StableToken
    cited_document_correct: bool
    cited_passage_correct: bool
    cited_passage_supports_claim: bool


class ClaimReviewDecision(StrictFrozenSchema):
    """One human claim label plus zero or more passage-specific judgments."""

    blind_answer_id: StableToken
    blind_claim_id: StableToken
    claim_sha256: Sha256
    label: SupportLabel
    citation_decisions: tuple[CitationReviewDecision, ...] = ()
    overinterpretation_present: bool
    review_note: str | None = Field(default=None, max_length=2000)

    @field_validator("citation_decisions")
    @classmethod
    def canonical_citation_decisions(
        cls, values: tuple[CitationReviewDecision, ...]
    ) -> tuple[CitationReviewDecision, ...]:
        ids = tuple(value.citation_id for value in values)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise ValueError("citation decisions must use sorted unique citation IDs")
        return values


class AnswerReviewDecision(StrictFrozenSchema):
    """Answer-level limitation, refusal, and atomicity review."""

    blind_answer_id: StableToken
    answer_sha256: Sha256
    required_limitation_present: bool
    refusal_appropriate: bool
    non_atomic_claim_ids: tuple[StableToken, ...] = ()

    @field_validator("non_atomic_claim_ids")
    @classmethod
    def canonical_non_atomic(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(values)) or len(values) != len(set(values)):
            raise ValueError("non-atomic claim IDs must be sorted and unique")
        return values


class HumanReviewSubmission(StrictFrozenSchema):
    """Complete independent review from one accountable EVE/virology reviewer."""

    submission_schema_version: Literal["rag-value-human-review-submission-v1"] = (
        "rag-value-human-review-submission-v1"
    )
    packet_sha256: Sha256
    reviewer_key: StableToken
    reviewer_role: Literal["eve_virology_reviewer"]
    reviewed_at: Rfc3339Utc
    attestation: Literal[
        "I independently reviewed every assigned claim against only the supplied benchmark "
        "evidence."
    ]
    claim_decisions: tuple[ClaimReviewDecision, ...] = Field(min_length=1)
    answer_decisions: tuple[AnswerReviewDecision, ...] = Field(min_length=1)
    submission_sha256: Sha256

    @model_validator(mode="after")
    def validate_submission(self) -> Self:
        claim_keys = tuple(
            (decision.blind_answer_id, decision.blind_claim_id)
            for decision in self.claim_decisions
        )
        if claim_keys != tuple(sorted(claim_keys)) or len(claim_keys) != len(set(claim_keys)):
            raise ValueError("claim decisions must use canonical unique order")
        answer_ids = tuple(decision.blind_answer_id for decision in self.answer_decisions)
        if answer_ids != tuple(sorted(answer_ids)) or len(answer_ids) != len(set(answer_ids)):
            raise ValueError("answer decisions must use canonical unique order")
        if self.submission_sha256 != _self_sha256(self, "submission_sha256"):
            raise ValueError("review submission checksum does not match")
        return self


def build_review_submission(
    *,
    packet_sha256: str,
    reviewer_key: str,
    reviewed_at: str,
    claim_decisions: Sequence[ClaimReviewDecision],
    answer_decisions: Sequence[AnswerReviewDecision],
) -> HumanReviewSubmission:
    """Bind human-supplied decisions without generating or changing any label."""

    payload: dict[str, object] = {
        "submission_schema_version": "rag-value-human-review-submission-v1",
        "packet_sha256": packet_sha256,
        "reviewer_key": reviewer_key,
        "reviewer_role": "eve_virology_reviewer",
        "reviewed_at": reviewed_at,
        "attestation": _REVIEW_ATTESTATION,
        "claim_decisions": tuple(
            sorted(
                claim_decisions,
                key=lambda decision: (
                    decision.blind_answer_id,
                    decision.blind_claim_id,
                ),
            )
        ),
        "answer_decisions": tuple(
            sorted(answer_decisions, key=lambda decision: decision.blind_answer_id)
        ),
    }
    return HumanReviewSubmission.model_validate(
        {**payload, "submission_sha256": canonical_json_sha256(payload)}
    )


def validate_review_submission(
    packet: HumanReviewPacket,
    submission: HumanReviewSubmission,
) -> None:
    """Require a complete, exact review of every answer and atomic claim."""

    if submission.packet_sha256 != packet.packet_sha256:
        raise HumanReviewError("review submission targets a different packet")
    expected_answers = {answer.blind_answer_id: answer for answer in packet.answers}
    observed_answers = {
        decision.blind_answer_id: decision for decision in submission.answer_decisions
    }
    if set(observed_answers) != set(expected_answers):
        raise HumanReviewError("review submission does not cover every answer exactly")
    expected_claims = {
        (answer.blind_answer_id, claim.blind_claim_id): (answer, claim)
        for answer in packet.answers
        for claim in answer.claims
    }
    observed_claims = {
        (decision.blind_answer_id, decision.blind_claim_id): decision
        for decision in submission.claim_decisions
    }
    if set(observed_claims) != set(expected_claims):
        raise HumanReviewError("review submission does not cover every claim exactly")
    for blind_answer_id, answer in expected_answers.items():
        answer_decision = observed_answers[blind_answer_id]
        if answer_decision.answer_sha256 != answer.answer_sha256:
            raise HumanReviewError("answer decision checksum does not match packet")
        claim_ids = {claim.blind_claim_id for claim in answer.claims}
        if not set(answer_decision.non_atomic_claim_ids) <= claim_ids:
            raise HumanReviewError("answer decision names an unknown non-atomic claim")
    for key, (_answer, claim) in expected_claims.items():
        claim_decision = observed_claims[key]
        if claim_decision.claim_sha256 != claim.claim_sha256:
            raise HumanReviewError("claim decision checksum does not match packet")
        observed_citations = tuple(
            item.citation_id for item in claim_decision.citation_decisions
        )
        if observed_citations != claim.citation_ids:
            raise HumanReviewError("claim review must cover every citation exactly")


def validate_independent_reviews(
    packet: HumanReviewPacket,
    submissions: Sequence[HumanReviewSubmission],
) -> None:
    """Require at least two complete reviews from distinct human reviewer keys."""

    if len(submissions) < 2:
        raise HumanReviewError("at least two independent reviewers are required")
    reviewer_keys = tuple(submission.reviewer_key for submission in submissions)
    if len(reviewer_keys) != len(set(reviewer_keys)):
        raise HumanReviewError("independent reviews require distinct reviewer keys")
    for submission in submissions:
        validate_review_submission(packet, submission)


def validate_review_target(packet: HumanReviewPacket) -> None:
    """Enforce the preregistered minimum real-answer and atomic-claim review target."""

    answer_count = len(packet.answers)
    claim_count = sum(len(answer.claims) for answer in packet.answers)
    if answer_count < 20:
        raise HumanReviewError("human review target requires at least 20 real answers")
    if claim_count < 100:
        raise HumanReviewError("human review target requires at least 100 atomic claims")


class ReviewerAgreement(StrictFrozenSchema):
    """Pairwise four-label agreement without automatic adjudication."""

    reviewer_keys: tuple[StableToken, StableToken]
    claim_count: int = Field(ge=1)
    raw_agreement: RatioMetric
    cohens_kappa: str | None = Field(default=None, pattern=_KAPPA_PATTERN)
    undefined_reason: StableToken | None = None

    @model_validator(mode="after")
    def validate_kappa_shape(self) -> Self:
        if self.reviewer_keys != tuple(sorted(self.reviewer_keys)):
            raise ValueError("reviewer keys must be sorted")
        if (self.cohens_kappa is None) == (self.undefined_reason is None):
            raise ValueError("kappa requires exactly one value or undefined reason")
        return self


def calculate_reviewer_agreement(
    packet: HumanReviewPacket,
    first: HumanReviewSubmission,
    second: HumanReviewSubmission,
) -> ReviewerAgreement:
    """Calculate raw agreement and four-category Cohen's kappa for two complete reviews."""

    validate_independent_reviews(packet, (first, second))
    labels = (
        "fully_supported",
        "partially_supported",
        "unsupported",
        "not_assessable",
    )
    first_by_key = {
        (decision.blind_answer_id, decision.blind_claim_id): decision.label
        for decision in first.claim_decisions
    }
    second_by_key = {
        (decision.blind_answer_id, decision.blind_claim_id): decision.label
        for decision in second.claim_decisions
    }
    keys = tuple(sorted(first_by_key))
    agreement_count = sum(first_by_key[key] == second_by_key[key] for key in keys)
    observed = Decimal(agreement_count) / Decimal(len(keys))
    expected = sum(
        (
            Decimal(sum(value == label for value in first_by_key.values()))
            / Decimal(len(keys))
        )
        * (
            Decimal(sum(value == label for value in second_by_key.values()))
            / Decimal(len(keys))
        )
        for label in labels
    )
    if expected == Decimal(1):
        kappa = None
        undefined_reason = "expected_agreement_is_one"
    else:
        kappa = _kappa((observed - expected) / (Decimal(1) - expected))
        undefined_reason = None
    ordered_reviewers = tuple(sorted((first.reviewer_key, second.reviewer_key)))
    return ReviewerAgreement(
        reviewer_keys=(ordered_reviewers[0], ordered_reviewers[1]),
        claim_count=len(keys),
        raw_agreement=ratio(
            agreement_count,
            len(keys),
            undefined_reason="no_reviewed_claims",
        ),
        cohens_kappa=kappa,
        undefined_reason=undefined_reason,
    )


class AdjudicationDecision(StrictFrozenSchema):
    """One human final label for a claim on which independent reviewers disagreed."""

    blind_answer_id: StableToken
    blind_claim_id: StableToken
    claim_sha256: Sha256
    final_label: SupportLabel
    rationale: NonEmptyText


class AdjudicationSubmission(StrictFrozenSchema):
    """Human-only resolution of exactly the pairwise disagreement set."""

    submission_schema_version: Literal["rag-value-adjudication-v1"] = (
        "rag-value-adjudication-v1"
    )
    packet_sha256: Sha256
    reviewer_submission_sha256: tuple[Sha256, Sha256]
    adjudicator_key: StableToken
    adjudicated_at: Rfc3339Utc
    decisions: tuple[AdjudicationDecision, ...] = Field(min_length=1)
    submission_sha256: Sha256

    @model_validator(mode="after")
    def validate_submission(self) -> Self:
        keys = tuple(
            (decision.blind_answer_id, decision.blind_claim_id)
            for decision in self.decisions
        )
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("adjudication decisions must use canonical unique order")
        if self.submission_sha256 != _self_sha256(self, "submission_sha256"):
            raise ValueError("adjudication checksum does not match")
        return self


def build_adjudication_submission(
    *,
    packet_sha256: str,
    reviewer_submission_sha256: tuple[str, str],
    adjudicator_key: str,
    adjudicated_at: str,
    decisions: Sequence[AdjudicationDecision],
) -> AdjudicationSubmission:
    """Bind only human-supplied final labels; no majority rule is applied."""

    payload: dict[str, object] = {
        "submission_schema_version": "rag-value-adjudication-v1",
        "packet_sha256": packet_sha256,
        "reviewer_submission_sha256": reviewer_submission_sha256,
        "adjudicator_key": adjudicator_key,
        "adjudicated_at": adjudicated_at,
        "decisions": tuple(
            sorted(
                decisions,
                key=lambda decision: (
                    decision.blind_answer_id,
                    decision.blind_claim_id,
                ),
            )
        ),
    }
    return AdjudicationSubmission.model_validate(
        {**payload, "submission_sha256": canonical_json_sha256(payload)}
    )


def validate_adjudication(
    packet: HumanReviewPacket,
    first: HumanReviewSubmission,
    second: HumanReviewSubmission,
    adjudication: AdjudicationSubmission,
) -> None:
    """Reject automatic, incomplete, or out-of-scope disagreement resolution."""

    validate_independent_reviews(packet, (first, second))
    if adjudication.packet_sha256 != packet.packet_sha256:
        raise HumanReviewError("adjudication targets a different packet")
    if set(adjudication.reviewer_submission_sha256) != {
        first.submission_sha256,
        second.submission_sha256,
    }:
        raise HumanReviewError("adjudication does not bind the two reviewed submissions")
    first_by_key = {
        (decision.blind_answer_id, decision.blind_claim_id): decision
        for decision in first.claim_decisions
    }
    second_by_key = {
        (decision.blind_answer_id, decision.blind_claim_id): decision
        for decision in second.claim_decisions
    }
    disagreement_keys = {
        key for key in first_by_key if first_by_key[key].label != second_by_key[key].label
    }
    observed = {
        (decision.blind_answer_id, decision.blind_claim_id): decision
        for decision in adjudication.decisions
    }
    if set(observed) != disagreement_keys:
        raise HumanReviewError("adjudication must cover exactly every disagreement")
    for key, decision in observed.items():
        if decision.claim_sha256 != first_by_key[key].claim_sha256:
            raise HumanReviewError("adjudication claim checksum does not match reviews")


def _opaque_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:24]}"


def _self_sha256(value: StrictFrozenSchema, field_name: str) -> str:
    payload = value.model_dump(mode="python")
    del payload[field_name]
    return canonical_json_sha256(payload)


def _kappa(value: Decimal) -> str:
    return f"{value.quantize(_KAPPA_QUANTUM, rounding=ROUND_HALF_EVEN):.12f}"
