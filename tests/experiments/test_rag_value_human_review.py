from __future__ import annotations

import hashlib

import pytest

from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    EvaluationAnswer,
    EvaluationClaim,
    EvidenceCitation,
    build_evidence_pack,
)
from eve_relation_rag.experiments.rag_value_ablation.human_review import (
    AdjudicationDecision,
    AnswerReviewDecision,
    CitationReviewDecision,
    ClaimReviewDecision,
    HumanReviewError,
    ReviewSourceAnswer,
    build_adjudication_submission,
    build_blinded_review_packet,
    build_review_submission,
    calculate_reviewer_agreement,
    validate_adjudication,
    validate_independent_reviews,
    validate_review_submission,
    validate_review_target,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256

DOCUMENT = f"document:sha256:{'a' * 64}"
CHUNK = f"chunk:sha256:{'b' * 64}"


def test_review_export_is_deterministic_and_contains_no_system_identity() -> None:
    sources = (_source("S2", "q-1"), _source("S3", "q-1"))

    first_packet, first_map = build_blinded_review_packet(
        sources, shuffle_seed="review-seed-1"
    )
    second_packet, second_map = build_blinded_review_packet(
        sources, shuffle_seed="review-seed-1"
    )

    assert first_packet == second_packet
    assert first_map == second_map
    packet_bytes = canonical_json_bytes(first_packet)
    assert b"system_key" not in packet_bytes
    assert b"retrieval" not in packet_bytes
    assert {entry.system_key for entry in first_map.entries} == {"S2", "S3"}


def test_two_complete_independent_reviews_are_required_and_agreement_is_exact() -> None:
    packet, _mapping = build_blinded_review_packet(
        (_source("S2", "q-1"), _source("S3", "q-1")),
        shuffle_seed="review-seed-2",
    )
    first = _submission(packet, "reviewer-a", disagree_first=False)
    second = _submission(packet, "reviewer-b", disagree_first=True)

    validate_independent_reviews(packet, (first, second))
    agreement = calculate_reviewer_agreement(packet, first, second)

    assert agreement.claim_count == 4
    assert agreement.raw_agreement.value == "0.750000000000"
    assert agreement.cohens_kappa is not None
    with pytest.raises(HumanReviewError, match="distinct reviewer"):
        validate_independent_reviews(packet, (first, first))
    with pytest.raises(HumanReviewError, match="at least two"):
        validate_independent_reviews(packet, (first,))


def test_incomplete_review_and_unreviewed_citation_are_rejected() -> None:
    packet, _mapping = build_blinded_review_packet(
        (_source("S2", "q-1"),), shuffle_seed="review-seed-3"
    )
    complete = _submission(packet, "reviewer-a", disagree_first=False)
    incomplete = build_review_submission(
        packet_sha256=packet.packet_sha256,
        reviewer_key="reviewer-b",
        reviewed_at="2099-01-02T00:00:00Z",
        claim_decisions=complete.claim_decisions[:-1],
        answer_decisions=complete.answer_decisions,
    )
    with pytest.raises(HumanReviewError, match="every claim"):
        validate_review_submission(packet, incomplete)

    cited = next(
        decision for decision in complete.claim_decisions if decision.citation_decisions
    )
    missing_citation = cited.model_copy(update={"citation_decisions": ()})
    altered = build_review_submission(
        packet_sha256=packet.packet_sha256,
        reviewer_key="reviewer-c",
        reviewed_at="2099-01-03T00:00:00Z",
        claim_decisions=tuple(
            missing_citation if decision == cited else decision
            for decision in complete.claim_decisions
        ),
        answer_decisions=complete.answer_decisions,
    )
    with pytest.raises(HumanReviewError, match="every citation"):
        validate_review_submission(packet, altered)


def test_disagreements_require_explicit_human_adjudication() -> None:
    packet, _mapping = build_blinded_review_packet(
        (_source("S2", "q-1"),), shuffle_seed="review-seed-4"
    )
    first = _submission(packet, "reviewer-a", disagree_first=False)
    second = _submission(packet, "reviewer-b", disagree_first=True)
    first_decisions = {
        (decision.blind_answer_id, decision.blind_claim_id): decision
        for decision in first.claim_decisions
    }
    second_decisions = {
        (decision.blind_answer_id, decision.blind_claim_id): decision
        for decision in second.claim_decisions
    }
    disagreement_key = next(
        key
        for key in first_decisions
        if first_decisions[key].label != second_decisions[key].label
    )
    source_decision = first_decisions[disagreement_key]
    adjudication = build_adjudication_submission(
        packet_sha256=packet.packet_sha256,
        reviewer_submission_sha256=(first.submission_sha256, second.submission_sha256),
        adjudicator_key="adjudicator-1",
        adjudicated_at="2099-01-04T00:00:00Z",
        decisions=(
            AdjudicationDecision(
                blind_answer_id=source_decision.blind_answer_id,
                blind_claim_id=source_decision.blind_claim_id,
                claim_sha256=source_decision.claim_sha256,
                final_label="fully_supported",
                rationale="The supplied passage directly supports the atomic claim.",
            ),
        ),
    )

    validate_adjudication(packet, first, second, adjudication)


def test_real_review_target_is_not_satisfied_by_a_small_software_fixture() -> None:
    packet, _mapping = build_blinded_review_packet(
        (_source("S2", "q-1"),), shuffle_seed="review-seed-5"
    )
    with pytest.raises(HumanReviewError, match="at least 20 real answers"):
        validate_review_target(packet)


def _source(system_key: str, question_id: str) -> ReviewSourceAnswer:
    passage = "Synthetic evidence used only to exercise review software."
    evidence = build_evidence_pack(
        question_id=question_id,
        question_text="What does the synthetic evidence say?",
        citations=(
            EvidenceCitation(
                citation_id="D1",
                document_key=DOCUMENT,
                chunk_key=CHUNK,
                locator_text="Synthetic paragraph 1",
                text=passage,
                text_sha256=hashlib.sha256(passage.encode()).hexdigest(),
            ),
        ),
        policy_sha256="c" * 64,
        tokenizer_key="tokenizer:synthetic",
        model_context_limit_tokens=4096,
        reserved_output_tokens=512,
        input_token_count=100,
        context_token_count=50,
    )
    answer = EvaluationAnswer(
        answer_text="The synthetic evidence describes a software fixture and a limitation.",
        abstained=False,
        claims=(
            EvaluationClaim(
                claim_id="C1",
                text="The evidence describes a software fixture.",
                claim_type="literature_fact",
                citation_ids=("D1",),
            ),
            EvaluationClaim(
                claim_id="C2",
                text="This does not constitute a scientific benchmark result.",
                claim_type="limitation",
                citation_ids=(),
            ),
        ),
        limitations=("Synthetic fixtures are tests only.",),
        cited_chunk_ids=(CHUNK,),
    )
    return ReviewSourceAnswer(
        system_key=system_key,
        question_id=question_id,
        family="literature",
        question_text=evidence.question_text,
        answer=answer,
        answer_sha256=canonical_json_sha256(answer),
        evidence=evidence,
    )


def _submission(packet, reviewer_key: str, *, disagree_first: bool):
    claim_decisions: list[ClaimReviewDecision] = []
    answer_decisions: list[AnswerReviewDecision] = []
    ordinal = 0
    for answer in packet.answers:
        answer_decisions.append(
            AnswerReviewDecision(
                blind_answer_id=answer.blind_answer_id,
                answer_sha256=answer.answer_sha256,
                required_limitation_present=True,
                refusal_appropriate=False,
                non_atomic_claim_ids=(),
            )
        )
        for claim in answer.claims:
            ordinal += 1
            label = "unsupported" if disagree_first and ordinal == 1 else "fully_supported"
            claim_decisions.append(
                ClaimReviewDecision(
                    blind_answer_id=answer.blind_answer_id,
                    blind_claim_id=claim.blind_claim_id,
                    claim_sha256=claim.claim_sha256,
                    label=label,
                    citation_decisions=tuple(
                        CitationReviewDecision(
                            citation_id=citation_id,
                            cited_document_correct=True,
                            cited_passage_correct=True,
                            cited_passage_supports_claim=True,
                        )
                        for citation_id in claim.citation_ids
                    ),
                    overinterpretation_present=False,
                )
            )
    return build_review_submission(
        packet_sha256=packet.packet_sha256,
        reviewer_key=reviewer_key,
        reviewed_at="2099-01-01T00:00:00Z",
        claim_decisions=claim_decisions,
        answer_decisions=answer_decisions,
    )
