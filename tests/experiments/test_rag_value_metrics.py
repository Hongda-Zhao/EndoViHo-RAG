from __future__ import annotations

from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    CoordinateGold,
    EvidenceGroup,
    LiteratureGold,
    StructuredGold,
)
from eve_relation_rag.experiments.rag_value_ablation.metrics import (
    CitationJudgment,
    EfficiencyObservation,
    GroundingObservation,
    RefusalObservation,
    StructuredPrediction,
    ratio,
    score_grounding,
    score_retrieval,
    score_structured,
    summarize_efficiency,
    summarize_refusal,
)

DOCUMENT_A = f"document:sha256:{'a' * 64}"
DOCUMENT_B = f"document:sha256:{'b' * 64}"
CHUNK_A = f"chunk:sha256:{'1' * 64}"
CHUNK_B = f"chunk:sha256:{'2' * 64}"
CHUNK_C = f"chunk:sha256:{'3' * 64}"
CHUNK_D = f"chunk:sha256:{'4' * 64}"


def test_structured_metrics_preserve_exact_types_sets_coordinates_and_identifiers() -> None:
    locus = "locus:eve:v1:sha256:" + "c" * 64
    gold_coordinate = CoordinateGold(
        sequence_accession_version="NC_000001.1",
        start0=10,
        end0=20,
        strand="+",
    )
    gold = StructuredGold(
        exact_count=1,
        metric_key="distinct_included_locus_count",
        record_keys=(locus,),
        assembly_accession_versions=("GCA_000001.1",),
        sequence_accession_versions=("NC_000001.1",),
        locus_keys=(locus,),
        coordinates=(gold_coordinate,),
        detection_call_keys=(),
        release_key="release:test:v0:20990101:001",
        release_manifest_sha256="d" * 64,
        required_limitation_codes=("coordinates_are_zero_based_half_open",),
    )
    prediction = StructuredPrediction(
        exact_count=2,
        metric_key="distinct_included_locus_count",
        record_keys=(locus,),
        assembly_accession_versions=("GCF_999999.1",),
        sequence_accession_versions=(),
        locus_keys=(locus,),
        coordinates=(
            CoordinateGold(
                sequence_accession_version="NC_000001.1",
                start0=10,
                end0=21,
                strand="+",
            ),
        ),
        detection_call_keys=(),
        release_key=gold.release_key,
        release_manifest_sha256=gold.release_manifest_sha256,
        limitation_codes=("coordinates_are_zero_based_half_open",),
    )

    metrics = score_structured(gold, prediction)

    assert metrics.numeric_exact_match is False
    assert metrics.metric_key_exact_match is True
    assert metrics.record_set_exact is True
    assert metrics.assembly_set_exact is False
    assert metrics.sequence_set_exact is False
    assert metrics.locus_set_exact is True
    assert metrics.detection_call_set_exact is True
    assert metrics.coordinate_set_exact is False
    assert metrics.missing_record_count == 2
    assert metrics.extra_record_count == 1
    assert metrics.missing_coordinate_count == metrics.extra_coordinate_count == 1
    assert metrics.identifier_preservation.value == "0.500000000000"
    assert metrics.all_identifiers_exact is False
    assert metrics.release_provenance_exact is True
    assert metrics.invented_identifier_count == 1
    assert metrics.required_limitation_coverage.value == "1.000000000000"


def test_retrieval_metrics_use_expert_groups_and_do_not_double_count_alternatives() -> None:
    gold = LiteratureGold(
        required_document_keys=(DOCUMENT_A, DOCUMENT_B),
        evidence_groups=(
            EvidenceGroup(
                group_id="evidence-001",
                required_document_key=DOCUMENT_A,
                required_chunk_key=CHUNK_A,
                acceptable_alternative_chunk_keys=(CHUNK_B,),
            ),
            EvidenceGroup(
                group_id="evidence-002",
                required_document_key=DOCUMENT_B,
                required_chunk_key=CHUNK_C,
            ),
        ),
        excluded_chunk_keys=(CHUNK_D,),
        required_concepts=("Detection method",),
    )

    metrics = score_retrieval(gold, (CHUNK_D, CHUNK_B, CHUNK_A, CHUNK_C))

    assert metrics.recall_at_1 == "0.000000000000"
    assert metrics.recall_at_3 == "0.500000000000"
    assert metrics.recall_at_5 == metrics.recall_at_10 == "1.000000000000"
    assert metrics.mrr_at_10 == "0.500000000000"
    assert metrics.ndcg_at_10 == "0.650920929807"
    assert metrics.excluded_hit_count_at_10 == 1


def test_grounding_metrics_keep_human_labels_and_undefined_denominators_explicit() -> None:
    observation = GroundingObservation(
        required_fact_ids=("fact-1", "fact-2"),
        covered_fact_ids=("fact-1",),
        supplied_structured_fact_ids=("structured-1",),
        preserved_structured_fact_ids=("structured-1",),
        claim_labels=(
            "fully_supported",
            "partially_supported",
            "unsupported",
            "not_assessable",
        ),
        citation_judgments=(
            CitationJudgment(
                link_id="link-1",
                correct_document=True,
                correct_passage=True,
                passage_supports_claim=True,
            ),
            CitationJudgment(
                link_id="link-2",
                correct_document=True,
                correct_passage=False,
                passage_supports_claim=False,
            ),
        ),
        required_evidence_group_ids=("evidence-1", "evidence-2"),
        cited_supporting_group_ids=("evidence-1",),
        required_limitation_ids=(),
        present_limitation_ids=(),
        contradictory_claim_count=1,
    )

    metrics = score_grounding(observation)

    assert metrics.required_fact_coverage.value == "0.500000000000"
    assert metrics.structured_fact_preservation.value == "1.000000000000"
    assert metrics.fully_supported_claim_rate.value == "0.333333333333"
    assert metrics.partially_supported_claim_rate.value == "0.333333333333"
    assert metrics.unsupported_claim_rate.value == "0.333333333333"
    assert metrics.not_assessable_claim_count == 1
    assert metrics.citation_precision.value == "0.500000000000"
    assert metrics.required_limitation_coverage.value is None
    assert (
        metrics.required_limitation_coverage.undefined_reason
        == "no_required_limitations"
    )
    assert ratio(0, 0, undefined_reason="empty").value is None


def test_refusal_metrics_separate_unsupported_and_answerable_denominators() -> None:
    metrics = summarize_refusal(
        (
            RefusalObservation(
                question_id="unsupported-1",
                expected_refusal=True,
                abstained=True,
                refusal_appropriate=True,
                unsafe_acceptance=False,
                downstream_call_count_after_refusal=1,
            ),
            RefusalObservation(
                question_id="unsupported-2",
                expected_refusal=True,
                abstained=False,
                refusal_appropriate=False,
                unsafe_acceptance=True,
                downstream_call_count_after_refusal=0,
            ),
            RefusalObservation(
                question_id="answerable-1",
                expected_refusal=False,
                abstained=True,
                refusal_appropriate=False,
                unsafe_acceptance=False,
                downstream_call_count_after_refusal=0,
            ),
        )
    )

    assert metrics.correct_refusal_rate.value == "0.500000000000"
    assert metrics.false_refusal_rate.value == "1.000000000000"
    assert metrics.unsafe_acceptance_rate.value == "0.500000000000"
    assert metrics.downstream_calls_after_refusal == 1
    assert metrics.downstream_call_violation_rate.value == "0.500000000000"


def test_efficiency_uses_discrete_nearest_rank_and_never_invents_cost() -> None:
    observations = tuple(
        EfficiencyObservation(
            system_key="S0",
            question_id=f"q-{value:02d}",
            latency_ns=value,
            input_tokens=10,
            output_tokens=2,
            context_tokens=3,
            cost=None,
            peak_process_rss_bytes=100 + value,
            peak_accelerator_memory_bytes=None,
        )
        for value in range(1, 21)
    )

    summary = summarize_efficiency(observations)

    assert summary.p50_latency_ns == 10
    assert summary.p95_latency_ns == 19
    assert summary.total_input_tokens == 200
    assert summary.total_cost is None
    assert summary.peak_process_rss_bytes == 120
