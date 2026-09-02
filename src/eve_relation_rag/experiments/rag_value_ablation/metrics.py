"""Exact structured, retrieval, grounding, refusal, and efficiency metrics."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Self

from pydantic import Field, field_validator, model_validator

from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    AssemblyAccessionVersion,
    CoordinateGold,
    EvidenceGroup,
    LiteratureGold,
    SequenceAccessionVersion,
    StructuredGold,
    SupportLabel,
    SystemKey,
)
from eve_relation_rag.literature.contracts import ChunkKey, Sha256, StableToken, StrictFrozenSchema

_QUANTUM = Decimal("0.000000000001")
_RATIO_PATTERN = r"^(?:0|1)\.[0-9]{12}$"
_NONNEGATIVE_DECIMAL_PATTERN = r"^(?:0|[1-9][0-9]*)\.[0-9]{12}$"


class MetricError(ValueError):
    """Raised when metric inputs have ambiguous or invalid semantics."""


class RatioMetric(StrictFrozenSchema):
    """Exact ratio retaining its numerator, denominator, and undefined reason."""

    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    value: str | None = Field(default=None, pattern=_RATIO_PATTERN)
    undefined_reason: StableToken | None = None

    @model_validator(mode="after")
    def validate_ratio(self) -> Self:
        if self.numerator > self.denominator:
            raise ValueError("ratio numerator cannot exceed denominator")
        if self.denominator == 0:
            if self.value is not None or self.undefined_reason is None:
                raise ValueError("zero-denominator ratio requires only an undefined reason")
        elif (
            self.value != _decimal_ratio(self.numerator, self.denominator)
            or self.undefined_reason is not None
        ):
            raise ValueError("defined ratio does not match its exact counts")
        return self


def ratio(numerator: int, denominator: int, *, undefined_reason: str) -> RatioMetric:
    """Build an exact ratio, never coercing an undefined denominator to zero."""

    if denominator == 0:
        return RatioMetric(
            numerator=numerator,
            denominator=denominator,
            value=None,
            undefined_reason=undefined_reason,
        )
    return RatioMetric(
        numerator=numerator,
        denominator=denominator,
        value=_decimal_ratio(numerator, denominator),
        undefined_reason=None,
    )


class StructuredPrediction(StrictFrozenSchema):
    """Typed projection of exact structured values emitted by a system."""

    exact_count: int | None = Field(default=None, ge=0)
    metric_key: StableToken | None = None
    record_keys: tuple[StableToken, ...] | None = None
    assembly_accession_versions: tuple[AssemblyAccessionVersion, ...] | None = None
    sequence_accession_versions: tuple[SequenceAccessionVersion, ...] | None = None
    locus_keys: tuple[StableToken, ...] | None = None
    coordinates: tuple[CoordinateGold, ...] | None = None
    detection_call_keys: tuple[StableToken, ...] | None = None
    release_key: StableToken | None = None
    release_manifest_sha256: Sha256 | None = None
    limitation_codes: tuple[StableToken, ...] = ()
    observed_identifier_tokens: tuple[StableToken, ...] = ()

    @field_validator(
        "record_keys",
        "assembly_accession_versions",
        "sequence_accession_versions",
        "locus_keys",
        "detection_call_keys",
        "limitation_codes",
        "observed_identifier_tokens",
    )
    @classmethod
    def canonical_collections(cls, values: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if values is None:
            return None
        if len(values) != len(set(values)):
            raise ValueError("structured prediction collections must be unique")
        if values != tuple(sorted(values)):
            raise ValueError("structured prediction collections must be sorted")
        return values

    @field_validator("coordinates")
    @classmethod
    def canonical_coordinates(
        cls, values: tuple[CoordinateGold, ...] | None
    ) -> tuple[CoordinateGold, ...] | None:
        if values is None:
            return None
        keys = tuple(value.sort_key() for value in values)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("predicted coordinates must be canonically ordered and unique")
        return values

    @model_validator(mode="after")
    def validate_pairs(self) -> Self:
        if (self.exact_count is None) != (self.metric_key is None):
            raise ValueError("predicted exact_count and metric_key must be supplied together")
        if (self.release_key is None) != (self.release_manifest_sha256 is None):
            raise ValueError("predicted release identity must be supplied together")
        return self

    @property
    def identifiers(self) -> frozenset[str]:
        return frozenset(
            (
                *(self.record_keys or ()),
                *(self.assembly_accession_versions or ()),
                *(self.sequence_accession_versions or ()),
                *(self.locus_keys or ()),
                *(self.detection_call_keys or ()),
                *((self.release_key,) if self.release_key is not None else ()),
                *self.observed_identifier_tokens,
            )
        )


class StructuredMetrics(StrictFrozenSchema):
    """Exact structured correctness with explicit non-applicable fields."""

    numeric_exact_match: bool | None
    metric_key_exact_match: bool | None
    record_set_exact: bool | None
    assembly_set_exact: bool | None
    sequence_set_exact: bool | None
    locus_set_exact: bool | None
    coordinate_set_exact: bool | None
    detection_call_set_exact: bool | None
    missing_record_count: int = Field(ge=0)
    extra_record_count: int = Field(ge=0)
    missing_coordinate_count: int = Field(ge=0)
    extra_coordinate_count: int = Field(ge=0)
    identifier_preservation: RatioMetric
    all_identifiers_exact: bool
    release_provenance_exact: bool
    invented_identifier_count: int = Field(ge=0)
    required_limitation_coverage: RatioMetric


def score_structured(
    gold: StructuredGold,
    prediction: StructuredPrediction,
    *,
    permitted_identifiers: Iterable[str] = (),
) -> StructuredMetrics:
    """Compare typed values exactly; no text similarity or coercion is used."""

    set_fields = (
        ("record_keys", "record_set_exact"),
        ("assembly_accession_versions", "assembly_set_exact"),
        ("sequence_accession_versions", "sequence_set_exact"),
        ("locus_keys", "locus_set_exact"),
        ("detection_call_keys", "detection_call_set_exact"),
    )
    exact: dict[str, bool | None] = {}
    missing_records = 0
    extra_records = 0
    for field_name, metric_name in set_fields:
        expected_value = getattr(gold, field_name)
        observed_value = getattr(prediction, field_name)
        if expected_value is None:
            exact[metric_name] = None
            continue
        expected = set(expected_value)
        observed = set(observed_value or ())
        exact[metric_name] = expected == observed
        missing_records += len(expected - observed)
        extra_records += len(observed - expected)

    expected_coordinates = gold.coordinates
    if expected_coordinates is None:
        coordinate_exact = None
        missing_coordinates = 0
        extra_coordinates = 0
    else:
        expected_coordinate_set = set(expected_coordinates)
        observed_coordinate_set = set(prediction.coordinates or ())
        coordinate_exact = expected_coordinate_set == observed_coordinate_set
        missing_coordinates = len(expected_coordinate_set - observed_coordinate_set)
        extra_coordinates = len(observed_coordinate_set - expected_coordinate_set)

    required_identifiers = gold.required_identifiers
    observed_identifiers = prediction.identifiers
    preserved = len(required_identifiers & observed_identifiers)
    allowed = required_identifiers | set(permitted_identifiers)
    required_limitations = set(gold.required_limitation_codes)
    observed_limitations = set(prediction.limitation_codes)
    return StructuredMetrics(
        numeric_exact_match=(
            None if gold.exact_count is None else prediction.exact_count == gold.exact_count
        ),
        metric_key_exact_match=(
            None if gold.metric_key is None else prediction.metric_key == gold.metric_key
        ),
        record_set_exact=exact["record_set_exact"],
        assembly_set_exact=exact["assembly_set_exact"],
        sequence_set_exact=exact["sequence_set_exact"],
        locus_set_exact=exact["locus_set_exact"],
        coordinate_set_exact=coordinate_exact,
        detection_call_set_exact=exact["detection_call_set_exact"],
        missing_record_count=missing_records,
        extra_record_count=extra_records,
        missing_coordinate_count=missing_coordinates,
        extra_coordinate_count=extra_coordinates,
        identifier_preservation=ratio(
            preserved,
            len(required_identifiers),
            undefined_reason="no_required_identifiers",
        ),
        all_identifiers_exact=required_identifiers <= observed_identifiers,
        release_provenance_exact=(
            prediction.release_key == gold.release_key
            and prediction.release_manifest_sha256 == gold.release_manifest_sha256
        ),
        invented_identifier_count=len(observed_identifiers - allowed),
        required_limitation_coverage=ratio(
            len(required_limitations & observed_limitations),
            len(required_limitations),
            undefined_reason="no_required_limitations",
        ),
    )


class RetrievalMetrics(StrictFrozenSchema):
    """Group-aware retrieval metrics through depth ten."""

    returned_chunk_keys: tuple[ChunkKey, ...] = Field(max_length=10)
    recall_at_1: str = Field(pattern=_RATIO_PATTERN)
    recall_at_3: str = Field(pattern=_RATIO_PATTERN)
    recall_at_5: str = Field(pattern=_RATIO_PATTERN)
    recall_at_10: str = Field(pattern=_RATIO_PATTERN)
    mrr_at_10: str = Field(pattern=_RATIO_PATTERN)
    ndcg_at_10: str = Field(pattern=_RATIO_PATTERN)
    excluded_hit_count_at_10: int = Field(ge=0)


def score_retrieval(
    gold: LiteratureGold,
    returned_chunk_keys: Sequence[str],
) -> RetrievalMetrics:
    """Score exact expert-approved evidence groups without lexical substitution."""

    returned = tuple(returned_chunk_keys)
    if len(returned) > 10:
        raise MetricError("retrieval metrics accept at most ten returned chunks")
    if len(returned) != len(set(returned)):
        raise MetricError("returned retrieval chunks must be unique")
    group_members = tuple(group.member_chunk_keys for group in gold.evidence_groups)
    recalls = {
        cutoff: _recall(returned, group_members, cutoff)
        for cutoff in (1, 3, 5, 10)
    }
    first_relevant = next(
        (
            rank
            for rank, chunk in enumerate(returned[:10], start=1)
            if any(chunk in members for members in group_members)
        ),
        None,
    )
    mrr = Decimal(0) if first_relevant is None else Decimal(1) / Decimal(first_relevant)
    return RetrievalMetrics(
        returned_chunk_keys=returned,
        recall_at_1=_metric(recalls[1]),
        recall_at_3=_metric(recalls[3]),
        recall_at_5=_metric(recalls[5]),
        recall_at_10=_metric(recalls[10]),
        mrr_at_10=_metric(mrr),
        ndcg_at_10=_metric(_ndcg(returned, gold.evidence_groups)),
        excluded_hit_count_at_10=sum(
            chunk in set(gold.excluded_chunk_keys) for chunk in returned[:10]
        ),
    )


class CitationJudgment(StrictFrozenSchema):
    """One human judgment of a claim-to-passage link."""

    link_id: StableToken
    correct_document: bool
    correct_passage: bool
    passage_supports_claim: bool


class GroundingObservation(StrictFrozenSchema):
    """Validated machine/human observations used to calculate grounding metrics."""

    required_fact_ids: tuple[StableToken, ...] = ()
    covered_fact_ids: tuple[StableToken, ...] = ()
    supplied_structured_fact_ids: tuple[StableToken, ...] = ()
    preserved_structured_fact_ids: tuple[StableToken, ...] = ()
    claim_labels: tuple[SupportLabel, ...] = ()
    citation_judgments: tuple[CitationJudgment, ...] = ()
    required_evidence_group_ids: tuple[StableToken, ...] = ()
    cited_supporting_group_ids: tuple[StableToken, ...] = ()
    required_limitation_ids: tuple[StableToken, ...] = ()
    present_limitation_ids: tuple[StableToken, ...] = ()
    contradictory_claim_count: int = Field(ge=0)

    @field_validator(
        "required_fact_ids",
        "covered_fact_ids",
        "supplied_structured_fact_ids",
        "preserved_structured_fact_ids",
        "required_evidence_group_ids",
        "cited_supporting_group_ids",
        "required_limitation_ids",
        "present_limitation_ids",
    )
    @classmethod
    def canonical_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(values)) or len(values) != len(set(values)):
            raise ValueError("grounding IDs must be sorted and unique")
        return values

    @model_validator(mode="after")
    def validate_subsets(self) -> Self:
        for observed, expected, label in (
            (self.covered_fact_ids, self.required_fact_ids, "covered facts"),
            (
                self.preserved_structured_fact_ids,
                self.supplied_structured_fact_ids,
                "preserved structured facts",
            ),
            (
                self.cited_supporting_group_ids,
                self.required_evidence_group_ids,
                "cited evidence groups",
            ),
            (self.present_limitation_ids, self.required_limitation_ids, "limitations"),
        ):
            if not set(observed) <= set(expected):
                raise ValueError(f"{label} must be a subset of its required set")
        link_ids = tuple(judgment.link_id for judgment in self.citation_judgments)
        if len(link_ids) != len(set(link_ids)):
            raise ValueError("citation judgment link IDs must be unique")
        return self


class GroundingMetrics(StrictFrozenSchema):
    required_fact_coverage: RatioMetric
    structured_fact_preservation: RatioMetric
    fully_supported_claim_rate: RatioMetric
    partially_supported_claim_rate: RatioMetric
    unsupported_claim_rate: RatioMetric
    not_assessable_claim_count: int = Field(ge=0)
    citation_document_accuracy: RatioMetric
    citation_passage_accuracy: RatioMetric
    citation_precision: RatioMetric
    citation_recall: RatioMetric
    required_limitation_coverage: RatioMetric
    contradictory_claim_count: int = Field(ge=0)


def score_grounding(observation: GroundingObservation) -> GroundingMetrics:
    """Calculate exact grounding rates from validated observations or human labels."""

    assessable = tuple(
        label for label in observation.claim_labels if label != "not_assessable"
    )
    judgments = observation.citation_judgments
    return GroundingMetrics(
        required_fact_coverage=ratio(
            len(observation.covered_fact_ids),
            len(observation.required_fact_ids),
            undefined_reason="no_required_facts",
        ),
        structured_fact_preservation=ratio(
            len(observation.preserved_structured_fact_ids),
            len(observation.supplied_structured_fact_ids),
            undefined_reason="no_supplied_structured_facts",
        ),
        fully_supported_claim_rate=ratio(
            assessable.count("fully_supported"),
            len(assessable),
            undefined_reason="no_assessable_claims",
        ),
        partially_supported_claim_rate=ratio(
            assessable.count("partially_supported"),
            len(assessable),
            undefined_reason="no_assessable_claims",
        ),
        unsupported_claim_rate=ratio(
            assessable.count("unsupported"),
            len(assessable),
            undefined_reason="no_assessable_claims",
        ),
        not_assessable_claim_count=observation.claim_labels.count("not_assessable"),
        citation_document_accuracy=ratio(
            sum(judgment.correct_document for judgment in judgments),
            len(judgments),
            undefined_reason="no_reviewed_citations",
        ),
        citation_passage_accuracy=ratio(
            sum(judgment.correct_passage for judgment in judgments),
            len(judgments),
            undefined_reason="no_reviewed_citations",
        ),
        citation_precision=ratio(
            sum(judgment.passage_supports_claim for judgment in judgments),
            len(judgments),
            undefined_reason="no_reviewed_citations",
        ),
        citation_recall=ratio(
            len(observation.cited_supporting_group_ids),
            len(observation.required_evidence_group_ids),
            undefined_reason="no_required_evidence_groups",
        ),
        required_limitation_coverage=ratio(
            len(observation.present_limitation_ids),
            len(observation.required_limitation_ids),
            undefined_reason="no_required_limitations",
        ),
        contradictory_claim_count=observation.contradictory_claim_count,
    )


class RefusalObservation(StrictFrozenSchema):
    """One answer-level refusal outcome bound to a reviewed question."""

    question_id: StableToken
    expected_refusal: bool
    abstained: bool
    refusal_appropriate: bool
    unsafe_acceptance: bool
    downstream_call_count_after_refusal: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_semantics(self) -> Self:
        if self.refusal_appropriate and (not self.expected_refusal or not self.abstained):
            raise ValueError("appropriate refusal requires expected_refusal and abstention")
        if self.unsafe_acceptance and (not self.expected_refusal or self.abstained):
            raise ValueError("unsafe acceptance requires a non-abstaining unsupported answer")
        if self.downstream_call_count_after_refusal and not self.abstained:
            raise ValueError("post-refusal calls require an observed refusal")
        return self


class RefusalMetrics(StrictFrozenSchema):
    correct_refusal_rate: RatioMetric
    false_refusal_rate: RatioMetric
    unsafe_acceptance_rate: RatioMetric
    downstream_calls_after_refusal: int = Field(ge=0)
    downstream_call_violation_rate: RatioMetric


def summarize_refusal(observations: Sequence[RefusalObservation]) -> RefusalMetrics:
    """Calculate refusal metrics with answerable and unsupported denominators separated."""

    ids = tuple(observation.question_id for observation in observations)
    if len(ids) != len(set(ids)):
        raise MetricError("refusal observations must have unique question IDs")
    unsupported = tuple(item for item in observations if item.expected_refusal)
    answerable = tuple(item for item in observations if not item.expected_refusal)
    refused = tuple(item for item in observations if item.abstained)
    post_refusal_violations = sum(
        item.downstream_call_count_after_refusal > 0 for item in refused
    )
    return RefusalMetrics(
        correct_refusal_rate=ratio(
            sum(item.refusal_appropriate for item in unsupported),
            len(unsupported),
            undefined_reason="no_unsupported_questions",
        ),
        false_refusal_rate=ratio(
            sum(item.abstained for item in answerable),
            len(answerable),
            undefined_reason="no_answerable_questions",
        ),
        unsafe_acceptance_rate=ratio(
            sum(item.unsafe_acceptance for item in unsupported),
            len(unsupported),
            undefined_reason="no_unsupported_questions",
        ),
        downstream_calls_after_refusal=sum(
            item.downstream_call_count_after_refusal for item in observations
        ),
        downstream_call_violation_rate=ratio(
            post_refusal_violations,
            len(refused),
            undefined_reason="no_refusals",
        ),
    )


class EfficiencyObservation(StrictFrozenSchema):
    """One measured system/question execution."""

    system_key: SystemKey
    question_id: StableToken
    latency_ns: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    context_tokens: int = Field(ge=0)
    cost: str | None = Field(default=None, pattern=_NONNEGATIVE_DECIMAL_PATTERN)
    peak_process_rss_bytes: int = Field(ge=0)
    peak_accelerator_memory_bytes: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_tokens(self) -> Self:
        if self.context_tokens > self.input_tokens:
            raise ValueError("context tokens cannot exceed input tokens")
        return self


class EfficiencySummary(StrictFrozenSchema):
    sample_count: int = Field(ge=1)
    p50_latency_ns: int = Field(ge=0)
    p95_latency_ns: int = Field(ge=0)
    total_input_tokens: int = Field(ge=0)
    total_output_tokens: int = Field(ge=0)
    total_context_tokens: int = Field(ge=0)
    total_cost: str | None = Field(default=None, pattern=_NONNEGATIVE_DECIMAL_PATTERN)
    peak_process_rss_bytes: int = Field(ge=0)
    peak_accelerator_memory_bytes: int | None = Field(default=None, ge=0)


def summarize_efficiency(observations: Sequence[EfficiencyObservation]) -> EfficiencySummary:
    """Use discrete nearest-rank p50/p95 and preserve unavailable cost as null."""

    if not observations:
        raise MetricError("efficiency summary requires at least one observation")
    systems = {observation.system_key for observation in observations}
    if len(systems) != 1:
        raise MetricError("efficiency summary must cover exactly one system")
    ids = tuple(observation.question_id for observation in observations)
    if len(ids) != len(set(ids)):
        raise MetricError("efficiency observations must have unique question IDs")
    ordered_latency = tuple(sorted(observation.latency_ns for observation in observations))
    costs = tuple(observation.cost for observation in observations)
    total_cost = None
    if all(cost is not None for cost in costs):
        total_cost = _nonnegative_metric(
            sum((Decimal(cost) for cost in costs if cost is not None), start=Decimal(0))
        )
    accelerator_values = tuple(
        value
        for value in (
            observation.peak_accelerator_memory_bytes for observation in observations
        )
        if value is not None
    )
    return EfficiencySummary(
        sample_count=len(observations),
        p50_latency_ns=_nearest_rank(ordered_latency, 50),
        p95_latency_ns=_nearest_rank(ordered_latency, 95),
        total_input_tokens=sum(observation.input_tokens for observation in observations),
        total_output_tokens=sum(observation.output_tokens for observation in observations),
        total_context_tokens=sum(observation.context_tokens for observation in observations),
        total_cost=total_cost,
        peak_process_rss_bytes=max(
            observation.peak_process_rss_bytes for observation in observations
        ),
        peak_accelerator_memory_bytes=(
            None if not accelerator_values else max(accelerator_values)
        ),
    )


def _recall(
    returned: Sequence[str],
    groups: Sequence[frozenset[str]],
    cutoff: int,
) -> Decimal:
    observed = set(returned[:cutoff])
    return Decimal(sum(bool(observed & group) for group in groups)) / Decimal(len(groups))


def _ndcg(returned: Sequence[str], groups: Sequence[EvidenceGroup]) -> Decimal:
    members = tuple(group.member_chunk_keys for group in groups)
    satisfied: set[int] = set()
    with localcontext() as context:
        context.prec = 50
        ln_two = Decimal(2).ln()
        dcg = Decimal(0)
        for rank, chunk in enumerate(returned[:10], start=1):
            group_index = next(
                (
                    index
                    for index, group in enumerate(members)
                    if index not in satisfied and chunk in group
                ),
                None,
            )
            if group_index is not None:
                satisfied.add(group_index)
                dcg += ln_two / Decimal(rank + 1).ln()
        ideal_count = min(len(groups), 10)
        idcg = sum(
            (ln_two / Decimal(rank + 1).ln() for rank in range(1, ideal_count + 1)),
            start=Decimal(0),
        )
        return dcg / idcg


def _nearest_rank(ordered: Sequence[int], percentile: int) -> int:
    rank = (percentile * len(ordered) + 99) // 100
    return ordered[rank - 1]


def _decimal_ratio(numerator: int, denominator: int) -> str:
    return _metric(Decimal(numerator) / Decimal(denominator))


def _metric(value: Decimal) -> str:
    return f"{value.quantize(_QUANTUM, rounding=ROUND_HALF_EVEN):.12f}"


def _nonnegative_metric(value: Decimal) -> str:
    return f"{value.quantize(_QUANTUM, rounding=ROUND_HALF_EVEN):.12f}"
