"""Pure schemas for Milestone 2 structured query plans.

This module deliberately contains no parser, resolver, compiler, repository, API, or
database access.  A release key passing these syntax checks is not thereby published;
the later publication gate remains solely responsible for that authorization decision.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Any, Final, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from eve_relation_rag.domain.keys import (
    canonical_json,
    canonical_json_sha256,
    is_release_key,
    is_versioned_assembly_accession,
)

PLAN_VERSION: Final = "endoviho-query-plan-v0.1"

_RELEASE_PREFIX: Final = "release:endoviho-rag:v0:"
_ASSEMBLY_PREFIX: Final = "assembly:ncbi:"
_LOCUS_KEY_RE: Final = re.compile(r"^locus:eve:v1:sha256:[0-9a-f]{64}$")
_FILTER_ORDER: Final = {
    "assembly": 0,
    "locus": 1,
    "source_lineage": 2,
    "viral_lineage": 3,
}


class StrictFrozenModel(BaseModel):
    """Immutable strict base that rejects every unknown field."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _validate_exact_token(value: str) -> str:
    if any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in value
    ):
        raise ValueError("stable tokens must not contain whitespace or control characters")
    return value


def _validate_question(value: str) -> str:
    if not value.strip():
        raise ValueError("original_question must contain non-whitespace text")
    if any(
        unicodedata.category(character).startswith("C") or character in {"\u2028", "\u2029"}
        for character in value
    ):
        raise ValueError("original_question must be one line without control characters")
    return value


def _validate_release_key(value: str) -> str:
    if not value.startswith(_RELEASE_PREFIX) or not is_release_key(value):
        raise ValueError(
            "release_key must match release:endoviho-rag:v0:YYYYMMDD:NNN with a valid date"
        )
    return value


def _validate_assembly_key(value: str) -> str:
    if not value.startswith(_ASSEMBLY_PREFIX):
        raise ValueError("assembly_key must use the assembly:ncbi namespace")
    accession_version = value.removeprefix(_ASSEMBLY_PREFIX)
    if not is_versioned_assembly_accession(accession_version):
        raise ValueError("assembly_key must contain an exact GCA_/GCF_ accession.version")
    return value


def _validate_locus_key(value: str) -> str:
    if _LOCUS_KEY_RE.fullmatch(value) is None:
        raise ValueError("locus_key must match locus:eve:v1:sha256:<64 lowercase hex>")
    return value


ExactToken = Annotated[
    str,
    Field(min_length=1, max_length=255),
    AfterValidator(_validate_exact_token),
]
PublishedReleaseKey = Annotated[
    str,
    Field(min_length=1, max_length=255),
    AfterValidator(_validate_release_key),
]
AssemblyKey = Annotated[
    str,
    Field(min_length=1, max_length=255),
    AfterValidator(_validate_assembly_key),
]
LocusKey = Annotated[
    str,
    Field(min_length=1, max_length=255),
    AfterValidator(_validate_locus_key),
]
QuestionText = Annotated[
    str,
    Field(min_length=1, max_length=2000),
    AfterValidator(_validate_question),
]
CursorToken = Annotated[
    str,
    Field(min_length=1, max_length=4096, pattern=r"^[A-Za-z0-9_-]+$"),
]

MetricKey = Literal[
    "distinct_included_locus_count",
    "distinct_contig_count",
    "distinct_assembly_count",
    "distinct_source_taxon_count",
    "detection_call_count",
]


class AssemblyFilter(StrictFrozenModel):
    """Select one exact versioned NCBI assembly stable key."""

    filter_type: Literal["assembly"]
    assembly_key: AssemblyKey


class LocusFilter(StrictFrozenModel):
    """Select one exact coordinate-free EVELocus stable key."""

    filter_type: Literal["locus"]
    locus_key: LocusKey


class SourceLineageFilter(StrictFrozenModel):
    """Select one release-pinned assembly-source lineage term."""

    filter_type: Literal["source_lineage"]
    snapshot_key: ExactToken
    term_key: ExactToken
    role: Literal["assembly_source_taxonomy"]
    include_descendants: bool


class ViralLineageFilter(StrictFrozenModel):
    """Select one formal or explicitly study-defined viral lineage term."""

    filter_type: Literal["viral_lineage"]
    snapshot_key: ExactToken
    term_key: ExactToken
    role: Literal["formal_viral_taxonomy", "study_viral_lineage"]
    include_descendants: bool


QueryFilter = Annotated[
    AssemblyFilter | LocusFilter | SourceLineageFilter | ViralLineageFilter,
    Field(discriminator="filter_type"),
]


class EntireReleaseScope(StrictFrozenModel):
    """An explicitly requested query over the complete public release universe."""

    scope_type: Literal["entire_release"]
    explicitly_requested: Literal[True]


class FilteredScope(StrictFrozenModel):
    """One to three unique AND-combined filters in canonical order."""

    scope_type: Literal["filtered"]
    filters: tuple[QueryFilter, ...] = Field(min_length=1, max_length=3)

    @field_validator("filters")
    @classmethod
    def validate_and_order_filters(
        cls, filters: tuple[QueryFilter, ...]
    ) -> tuple[QueryFilter, ...]:
        filter_types = tuple(query_filter.filter_type for query_filter in filters)
        if len(set(filter_types)) != len(filter_types):
            raise ValueError("each filter_type may appear at most once")
        return tuple(
            sorted(filters, key=lambda query_filter: _FILTER_ORDER[query_filter.filter_type])
        )


QueryScope = Annotated[
    EntireReleaseScope | FilteredScope,
    Field(discriminator="scope_type"),
]


class PageSpec(StrictFrozenModel):
    """Forward-only transport pagination for list intents."""

    limit: int = Field(default=50, ge=1, le=100)
    cursor: CursorToken | None = None


class StructuredPlanBase(StrictFrozenModel):
    """Fields shared by every server-generated structured plan."""

    plan_version: Literal["endoviho-query-plan-v0.1"]
    route: Literal["structured"]
    release_key: PublishedReleaseKey
    original_question: QuestionText
    scope: QueryScope


def _validate_scope(
    scope: QueryScope,
    *,
    allowed_filter_types: frozenset[str],
    allow_entire_release: bool,
    exact_filter_types: tuple[str, ...] | None = None,
) -> None:
    if isinstance(scope, EntireReleaseScope):
        if not allow_entire_release:
            raise ValueError("this intent requires a filtered scope")
        return

    observed = tuple(query_filter.filter_type for query_filter in scope.filters)
    if exact_filter_types is not None and observed != exact_filter_types:
        expected = ", ".join(exact_filter_types)
        raise ValueError(f"this intent requires exactly these filters: {expected}")
    unsupported = set(observed).difference(allowed_filter_types)
    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise ValueError(f"filters incompatible with this intent: {names}")


class AssemblyDetailPlan(StructuredPlanBase):
    """Return one public assembly summary."""

    intent: Literal["assembly_detail"]

    @model_validator(mode="after")
    def validate_intent_scope(self) -> Self:
        _validate_scope(
            self.scope,
            allowed_filter_types=frozenset({"assembly"}),
            allow_entire_release=False,
            exact_filter_types=("assembly",),
        )
        return self


class LocusDetailPlan(StructuredPlanBase):
    """Return one public locus and its typed public provenance."""

    intent: Literal["locus_detail"]

    @model_validator(mode="after")
    def validate_intent_scope(self) -> Self:
        _validate_scope(
            self.scope,
            allowed_filter_types=frozenset({"locus"}),
            allow_entire_release=False,
            exact_filter_types=("locus",),
        )
        return self


class ListLociPlan(StructuredPlanBase):
    """Page public loci under approved filters or explicit full-release scope."""

    intent: Literal["list_loci"]
    page: PageSpec

    @model_validator(mode="after")
    def validate_intent_scope(self) -> Self:
        _validate_scope(
            self.scope,
            allowed_filter_types=frozenset({"assembly", "source_lineage", "viral_lineage"}),
            allow_entire_release=True,
        )
        return self


class ListAssembliesPlan(StructuredPlanBase):
    """Page assemblies represented by matched public loci."""

    intent: Literal["list_assemblies"]
    page: PageSpec

    @model_validator(mode="after")
    def validate_intent_scope(self) -> Self:
        _validate_scope(
            self.scope,
            allowed_filter_types=frozenset({"source_lineage", "viral_lineage"}),
            allow_entire_release=True,
        )
        return self


class ListSourceTaxaPlan(StructuredPlanBase):
    """Page source taxa represented by matched public loci."""

    intent: Literal["list_source_taxa"]
    page: PageSpec

    @model_validator(mode="after")
    def validate_intent_scope(self) -> Self:
        _validate_scope(
            self.scope,
            allowed_filter_types=frozenset({"viral_lineage"}),
            allow_entire_release=True,
        )
        return self


class AggregatePlan(StructuredPlanBase):
    """Compute one approved exact integer metric over public loci."""

    intent: Literal["aggregate"]
    metric_key: MetricKey

    @model_validator(mode="after")
    def validate_intent_scope(self) -> Self:
        _validate_scope(
            self.scope,
            allowed_filter_types=frozenset({"assembly", "source_lineage", "viral_lineage"}),
            allow_entire_release=True,
        )
        return self


StructuredPlan = Annotated[
    AssemblyDetailPlan
    | LocusDetailPlan
    | ListLociPlan
    | ListAssembliesPlan
    | ListSourceTaxaPlan
    | AggregatePlan,
    Field(discriminator="intent"),
]


ConditionKind = Literal[
    "intent",
    "entity",
    "negation",
    "logical_operator",
    "comparator",
    "metric",
    "scope",
    "pagination",
]


class SemanticSpan(StrictFrozenModel):
    """One source-text span not safely consumed by the future planner."""

    source_text: QuestionText
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_span(self) -> Self:
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        return self


class ExtractedCondition(SemanticSpan):
    """One semantic condition and its optional unique plan target."""

    condition_id: ExactToken
    condition_kind: ConditionKind
    mapped_target: ExactToken | None


class PlanningAudit(StrictFrozenModel):
    """Condition-preservation evidence produced by a future parser/planner."""

    extracted_conditions: tuple[ExtractedCondition, ...] = ()
    mapped_condition_ids: tuple[ExactToken, ...] = ()
    unresolved_condition_ids: tuple[ExactToken, ...] = ()
    unconsumed_semantic_spans: tuple[SemanticSpan, ...] = ()

    @model_validator(mode="after")
    def validate_condition_partition(self) -> Self:
        extracted_ids = tuple(item.condition_id for item in self.extracted_conditions)
        mapped_ids = self.mapped_condition_ids
        unresolved_ids = self.unresolved_condition_ids

        if len(set(extracted_ids)) != len(extracted_ids):
            raise ValueError("extracted condition_id values must be unique")
        if len(set(mapped_ids)) != len(mapped_ids):
            raise ValueError("mapped_condition_ids must be unique")
        if len(set(unresolved_ids)) != len(unresolved_ids):
            raise ValueError("unresolved_condition_ids must be unique")
        if set(mapped_ids).intersection(unresolved_ids):
            raise ValueError("a condition cannot be both mapped and unresolved")
        if set(extracted_ids) != set(mapped_ids).union(unresolved_ids):
            raise ValueError("mapped and unresolved IDs must partition extracted conditions")

        condition_by_id = {item.condition_id: item for item in self.extracted_conditions}
        if any(condition_by_id[item].mapped_target is None for item in mapped_ids):
            raise ValueError("mapped conditions require mapped_target")
        if any(condition_by_id[item].mapped_target is not None for item in unresolved_ids):
            raise ValueError("unresolved conditions must not have mapped_target")
        return self


def _canonical_plan_payload(plan: StructuredPlan) -> dict[str, Any]:
    payload = plan.model_dump(mode="json")
    scope = payload.get("scope")
    if isinstance(scope, dict):
        filters = scope.get("filters")
        if isinstance(filters, list):
            filters.sort(key=lambda item: _FILTER_ORDER[item["filter_type"]])
    page = payload.get("page")
    if isinstance(page, dict):
        page["cursor"] = None
    return payload


def canonical_plan_json(plan: StructuredPlan) -> str:
    """Return canonical UTF-8 JSON with canonical filters and a null cursor."""

    return canonical_json(_canonical_plan_payload(plan))


def canonical_plan_sha256(plan: StructuredPlan) -> str:
    """Return the lowercase SHA-256 digest of :func:`canonical_plan_json`."""

    return canonical_json_sha256(_canonical_plan_payload(plan))
