"""Pure response schemas for Milestone 2 structured retrieval.

This module deliberately contains no parser, resolver, compiler, repository,
database, API, or CLI behavior.  It only validates the immutable public
projections and response envelopes approved by the Milestone 2 contract.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import Annotated, Literal, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

from eve_relation_rag.domain.keys import (
    JsonValue,
    canonical_json,
    is_release_key,
    is_versioned_assembly_accession,
    is_versioned_contig_accession,
)
from eve_relation_rag.planning.query_plans import (
    AggregatePlan,
    AssemblyDetailPlan,
    AssemblyFilter,
    FilteredScope,
    ListAssembliesPlan,
    ListLociPlan,
    ListSourceTaxaPlan,
    LocusDetailPlan,
    LocusFilter,
    PlanningAudit,
    SourceLineageFilter,
    StructuredPlan,
    ViralLineageFilter,
    canonical_plan_sha256,
)


def _validate_non_empty_text(value: str) -> str:
    if not value.strip():
        raise ValueError("text must contain non-whitespace characters")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValueError("text must not contain control or format characters")
    return value


def _validate_stable_token(value: str) -> str:
    if any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in value
    ):
        raise ValueError("stable tokens must not contain whitespace or control characters")
    return value


class _FrozenJsonMapping(Mapping[str, JsonValue]):
    """Canonical JSON object whose accessors cannot mutate the stored value."""

    __slots__ = ("_canonical", "_keys")

    def __init__(self, value: Mapping[str, JsonValue]) -> None:
        self._canonical = canonical_json(value)
        self._keys = tuple(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json.loads(self._canonical))

    def __getitem__(self, key: str) -> JsonValue:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)


def _freeze_json_mapping(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    return _FrozenJsonMapping(value)


def _serialize_json_mapping(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    if isinstance(value, _FrozenJsonMapping):
        return value.to_dict()
    return dict(value)


type Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
type NonEmptyString = Annotated[
    str,
    Field(min_length=1),
    AfterValidator(_validate_non_empty_text),
]
type StableToken = Annotated[
    str,
    Field(min_length=1, max_length=255, pattern=r"^\S+$"),
    AfterValidator(_validate_stable_token),
]
type FrozenJsonObject = Annotated[
    Mapping[str, JsonValue],
    Field(min_length=1),
    AfterValidator(_freeze_json_mapping),
    PlainSerializer(_serialize_json_mapping, return_type=dict[str, JsonValue]),
]

type LineageRole = Literal[
    "assembly_source_taxonomy",
    "formal_viral_taxonomy",
    "study_viral_lineage",
    "extended_viral_lineage",
]
type SchemeKind = Literal["formal_taxonomy", "study_defined"]
type EntityKind = Literal["assembly", "locus", "source_lineage", "viral_lineage"]
type MatchMode = Literal[
    "exact_identifier",
    "exact_stable_key",
    "exact_canonical_name",
    "exact_curated_alias",
]
type MetricKey = Literal[
    "distinct_included_locus_count",
    "distinct_contig_count",
    "distinct_assembly_count",
    "distinct_source_taxon_count",
    "detection_call_count",
]
type MetricUnit = Literal["loci", "contigs", "assemblies", "source_taxa", "source_calls"]
type LimitationCode = Literal[
    "assembly_source_taxon_is_not_ancient_host",
    "assembly_local_locus_is_not_independent_integration_event",
    "zero_matches_do_not_establish_biological_absence",
    "source_confidence_is_not_release_validation",
    "coordinates_are_zero_based_half_open",
    "detection_calls_are_not_loci",
]
type ErrorCode = Literal[
    "request_schema_invalid",
    "query_plan_version_unsupported",
    "unsupported_question",
    "intent_unsupported",
    "unsupported_capability",
    "condition_unmapped",
    "full_release_scope_not_explicit",
    "intent_filter_incompatible",
    "filter_unsupported",
    "metric_required",
    "metric_unsupported",
    "pagination_not_allowed",
    "limit_invalid",
    "release_required",
    "release_key_invalid",
    "release_alias_forbidden",
    "release_not_found",
    "release_not_published",
    "release_dependencies_incomplete",
    "release_manifest_invalid",
    "assembly_accession_version_required",
    "entity_unresolved",
    "entity_ambiguous",
    "entity_not_in_release",
    "lineage_snapshot_mismatch",
    "lineage_role_ambiguous",
    "lineage_scope_ambiguous",
    "lineage_closure_incomplete",
    "cursor_invalid",
    "cursor_plan_mismatch",
    "compiler_constraint_unmapped",
    "result_integrity_error",
    "structured_query_failed",
]

_POST_QUERY_ERROR_CODES: frozenset[ErrorCode] = frozenset(
    {"result_integrity_error", "structured_query_failed"}
)
_RELEASE_ERROR_CODES: frozenset[ErrorCode] = frozenset(
    {
        "release_required",
        "release_key_invalid",
        "release_alias_forbidden",
        "release_not_found",
        "release_not_published",
        "release_dependencies_incomplete",
        "release_manifest_invalid",
    }
)

_LOCUS_KEY_RE = re.compile(r"^locus:eve:v1:sha256:[0-9a-f]{64}$")
_DIAGNOSTIC_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_RELEASE_PREFIX = "release:endoviho-rag:v0:"


class FrozenSchema(BaseModel):
    """Strict, immutable base for every public result fragment."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _require_sorted_unique(keys: tuple[tuple[str, ...], ...], *, field_name: str) -> None:
    if keys != tuple(sorted(keys)):
        raise ValueError(f"{field_name} must be in canonical order")
    if len(keys) != len(set(keys)):
        raise ValueError(f"{field_name} must not contain duplicates")


class LineageRef(FrozenSchema):
    """One displayable term in one exact, role-qualified lineage snapshot."""

    term_key: StableToken
    canonical_name: NonEmptyString
    rank: NonEmptyString | None = None
    snapshot_key: StableToken
    authority_namespace: StableToken
    snapshot_version: NonEmptyString
    scheme_kind: SchemeKind
    role: LineageRole

    @model_validator(mode="after")
    def validate_role_scheme(self) -> LineageRef:
        expected = (
            "study_defined"
            if self.role in {"study_viral_lineage", "extended_viral_lineage"}
            else "formal_taxonomy"
        )
        if self.scheme_kind != expected:
            raise ValueError("lineage role and scheme_kind are inconsistent")
        return self


class ExactPlacement(FrozenSchema):
    """The exact membership-selected public placement of one locus."""

    sequence_key: StableToken
    sequence_accession_version: StableToken
    start0: int = Field(ge=0)
    end0: int = Field(gt=0)
    strand: Literal["+", "-", "unknown"]
    coordinate_system: Literal["0-based-half-open"] = "0-based-half-open"
    precision: Literal["exact"] = "exact"

    @model_validator(mode="after")
    def validate_interval_and_identity(self) -> ExactPlacement:
        if not is_versioned_contig_accession(self.sequence_accession_version):
            raise ValueError("sequence_accession_version must be an exact accession.version")
        expected_key = f"sequence:insdc:{self.sequence_accession_version}"
        if self.sequence_key != expected_key:
            raise ValueError("sequence_key does not match sequence_accession_version")
        if self.start0 >= self.end0:
            raise ValueError("exact placement requires start0 < end0")
        return self


class LocusSummary(FrozenSchema):
    """Minimal public projection for one included assembly-local locus."""

    locus_key: StableToken
    assembly_key: StableToken
    assembly_accession_version: StableToken
    source_organism_name: NonEmptyString
    source_taxon: LineageRef
    placement: ExactPlacement
    viral_lineages: tuple[LineageRef, ...] = ()

    @model_validator(mode="after")
    def validate_public_identity(self) -> LocusSummary:
        if _LOCUS_KEY_RE.fullmatch(self.locus_key) is None:
            raise ValueError("locus_key does not follow the approved V1 grammar")
        if not is_versioned_assembly_accession(self.assembly_accession_version):
            raise ValueError("assembly_accession_version must be an exact accession.version")
        if self.assembly_key != f"assembly:ncbi:{self.assembly_accession_version}":
            raise ValueError("assembly_key does not match assembly_accession_version")
        if self.source_taxon.role != "assembly_source_taxonomy":
            raise ValueError("source_taxon must use the assembly_source_taxonomy role")
        if any(lineage.role == "assembly_source_taxonomy" for lineage in self.viral_lineages):
            raise ValueError("viral_lineages cannot contain an assembly-source term")
        _require_sorted_unique(
            tuple(
                (lineage.snapshot_key, lineage.term_key, lineage.role)
                for lineage in self.viral_lineages
            ),
            field_name="viral_lineages",
        )
        return self


class AssemblySummary(FrozenSchema):
    """One assembly represented by the current filtered public locus set."""

    assembly_key: StableToken
    assembly_accession_version: StableToken
    source_organism_name: NonEmptyString
    source_taxon: LineageRef
    included_locus_count: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_public_identity(self) -> AssemblySummary:
        if not is_versioned_assembly_accession(self.assembly_accession_version):
            raise ValueError("assembly_accession_version must be an exact accession.version")
        if self.assembly_key != f"assembly:ncbi:{self.assembly_accession_version}":
            raise ValueError("assembly_key does not match assembly_accession_version")
        if self.source_taxon.role != "assembly_source_taxonomy":
            raise ValueError("source_taxon must use the assembly_source_taxonomy role")
        return self


class SourceTaxonSummary(FrozenSchema):
    """One direct assembly-source term represented in the current match set."""

    lineage: LineageRef
    represented_assembly_count: int = Field(gt=0)
    included_locus_count: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_counts_and_role(self) -> SourceTaxonSummary:
        if self.lineage.role != "assembly_source_taxonomy":
            raise ValueError("source-taxon summaries require assembly_source_taxonomy")
        if self.represented_assembly_count > self.included_locus_count:
            raise ValueError("represented assemblies cannot exceed included loci")
        return self


class CallDetail(FrozenSchema):
    """Typed, non-raw source provenance for one detection call."""

    call_key: StableToken
    source_method_key: StableToken
    process_run_key: StableToken
    source_record_key: StableToken
    artifact_key: StableToken
    artifact_sha256: Sha256
    worksheet: NonEmptyString
    row_number: int = Field(gt=0)


class EvidenceDetail(FrozenSchema):
    """The single supporting evidence item selected by assertion membership."""

    evidence_key: StableToken
    evidence_type: NonEmptyString
    evidence_sha256: Sha256
    source_locator: FrozenJsonObject
    summary: NonEmptyString | None = None
    artifact_key: StableToken
    artifact_sha256: Sha256
    source_uri: NonEmptyString
    verified_license_key: StableToken


class PublicAssertionDetail(FrozenSchema):
    """One public, membership-selected assertion and its supporting evidence."""

    assertion_key: StableToken
    assertion_type: Literal["hcvr", "viral_major_taxon", "vr_type"]
    predicate_key: StableToken
    asserted_value: NonEmptyString
    source_label: NonEmptyString | None = None
    source_confidence: Literal["source_high", "source_low"] | None = None
    lineage: LineageRef | None = None
    method_definition_key: StableToken
    method_version: NonEmptyString
    process_run_key: StableToken
    supporting_evidence: EvidenceDetail

    @model_validator(mode="after")
    def validate_typed_assertion(self) -> PublicAssertionDetail:
        if self.assertion_type == "hcvr":
            if (
                self.source_label is None
                or self.source_confidence is None
                or self.lineage is not None
            ):
                raise ValueError("hcvr assertions require source fields and forbid lineage")
            expected = "source_high" if self.source_label == "Yes" else "source_low"
            if self.source_confidence != expected:
                raise ValueError("source HCVR label and confidence are inconsistent")
            if self.asserted_value != self.source_label:
                raise ValueError("HCVR asserted_value must equal source_label")
        elif self.assertion_type == "viral_major_taxon":
            if self.source_label is not None or self.source_confidence is not None:
                raise ValueError("viral lineage assertions forbid HCVR source fields")
            if self.lineage is None or self.lineage.role == "assembly_source_taxonomy":
                raise ValueError("viral lineage assertions require a viral LineageRef")
        elif any(
            value is not None for value in (self.source_label, self.source_confidence, self.lineage)
        ):
            raise ValueError("vr_type assertions forbid HCVR and lineage detail")
        return self


class PageInfo(FrozenSchema):
    """Forward-keyset page metadata with an unpaginated total."""

    limit: int = Field(ge=1, le=100)
    returned_count: int = Field(ge=0)
    total_count: int = Field(ge=0)
    next_cursor: (
        Annotated[
            str,
            Field(min_length=1, max_length=4096, pattern=r"^[A-Za-z0-9_-]+$"),
        ]
        | None
    ) = None
    sort_key: Literal["locus_key", "assembly_accession", "source_taxon_key"]
    sort_direction: Literal["asc"] = "asc"

    @model_validator(mode="after")
    def validate_counts(self) -> PageInfo:
        if self.returned_count > self.limit:
            raise ValueError("returned_count cannot exceed limit")
        if self.returned_count > self.total_count:
            raise ValueError("returned_count cannot exceed total_count")
        if self.next_cursor is not None and self.returned_count != self.limit:
            raise ValueError("next_cursor requires a full page")
        if self.next_cursor is not None and self.returned_count >= self.total_count:
            raise ValueError("next_cursor requires additional results")
        return self


class AssemblyDetailData(FrozenSchema):
    """Structured assembly detail without an unbounded nested locus page."""

    kind: Literal["assembly_detail"] = "assembly_detail"
    assembly: AssemblySummary


class LocusDetailData(FrozenSchema):
    """Structured public locus detail with deterministic nested ordering."""

    kind: Literal["locus_detail"] = "locus_detail"
    locus: LocusSummary
    calls: tuple[CallDetail, ...] = ()
    public_assertions: tuple[PublicAssertionDetail, ...] = ()

    @model_validator(mode="after")
    def validate_nested_order(self) -> LocusDetailData:
        _require_sorted_unique(
            tuple((call.call_key,) for call in self.calls),
            field_name="calls",
        )
        _require_sorted_unique(
            tuple((assertion.assertion_key,) for assertion in self.public_assertions),
            field_name="public_assertions",
        )
        summary_lineages = {
            (lineage.snapshot_key, lineage.term_key, lineage.role)
            for lineage in self.locus.viral_lineages
        }
        assertion_lineages = {
            (
                assertion.lineage.snapshot_key,
                assertion.lineage.term_key,
                assertion.lineage.role,
            )
            for assertion in self.public_assertions
            if assertion.assertion_type == "viral_major_taxon" and assertion.lineage is not None
        }
        if summary_lineages != assertion_lineages:
            raise ValueError("locus viral_lineages must equal the public viral lineage assertions")
        return self


class LocusPageData(FrozenSchema):
    """One canonical locus-key page."""

    kind: Literal["locus_page"] = "locus_page"
    items: tuple[LocusSummary, ...]
    page: PageInfo

    @model_validator(mode="after")
    def validate_page(self) -> LocusPageData:
        if self.page.sort_key != "locus_key":
            raise ValueError("locus pages require locus_key sorting")
        if self.page.returned_count != len(self.items):
            raise ValueError("returned_count must equal the number of locus items")
        if not self.items and self.page.total_count != 0:
            raise ValueError("an empty locus page requires total_count = 0")
        _require_sorted_unique(
            tuple((item.locus_key,) for item in self.items),
            field_name="items",
        )
        return self


class AssemblyPageData(FrozenSchema):
    """One canonical assembly-accession page."""

    kind: Literal["assembly_page"] = "assembly_page"
    items: tuple[AssemblySummary, ...]
    page: PageInfo

    @model_validator(mode="after")
    def validate_page(self) -> AssemblyPageData:
        if self.page.sort_key != "assembly_accession":
            raise ValueError("assembly pages require assembly_accession sorting")
        if self.page.returned_count != len(self.items):
            raise ValueError("returned_count must equal the number of assembly items")
        if not self.items and self.page.total_count != 0:
            raise ValueError("an empty assembly page requires total_count = 0")
        _require_sorted_unique(
            tuple((item.assembly_accession_version, item.assembly_key) for item in self.items),
            field_name="items",
        )
        return self


class SourceTaxonPageData(FrozenSchema):
    """One canonical snapshot/term-key source-taxon page."""

    kind: Literal["source_taxon_page"] = "source_taxon_page"
    items: tuple[SourceTaxonSummary, ...]
    page: PageInfo

    @model_validator(mode="after")
    def validate_page(self) -> SourceTaxonPageData:
        if self.page.sort_key != "source_taxon_key":
            raise ValueError("source-taxon pages require source_taxon_key sorting")
        if self.page.returned_count != len(self.items):
            raise ValueError("returned_count must equal the number of source-taxon items")
        if not self.items and self.page.total_count != 0:
            raise ValueError("an empty source-taxon page requires total_count = 0")
        _require_sorted_unique(
            tuple((item.lineage.snapshot_key, item.lineage.term_key) for item in self.items),
            field_name="items",
        )
        return self


_METRIC_METADATA: dict[MetricKey, tuple[MetricUnit, str]] = {
    "distinct_included_locus_count": ("loci", "release_key+locus_key"),
    "distinct_contig_count": (
        "contigs",
        "assembly_accession_version+sequence_accession_version",
    ),
    "distinct_assembly_count": ("assemblies", "assembly_accession_version"),
    "distinct_source_taxon_count": ("source_taxa", "snapshot_key+term_key"),
    "detection_call_count": ("source_calls", "release_key+call_key"),
}


class AggregateData(FrozenSchema):
    """One exact integer metric over the filtered, unpaginated public locus set."""

    kind: Literal["aggregate"] = "aggregate"
    metric_key: MetricKey
    value: int = Field(ge=0)
    unit: MetricUnit
    deduplication_key: NonEmptyString

    @model_validator(mode="after")
    def validate_metric_metadata(self) -> AggregateData:
        expected_unit, expected_key = _METRIC_METADATA[self.metric_key]
        if self.unit != expected_unit or self.deduplication_key != expected_key:
            raise ValueError("metric unit or deduplication_key differs from the approved contract")
        return self


type StructuredData = Annotated[
    AssemblyDetailData
    | LocusDetailData
    | LocusPageData
    | AssemblyPageData
    | SourceTaxonPageData
    | AggregateData,
    Field(discriminator="kind"),
]


class PublishedReleaseRef(FrozenSchema):
    """Immutable published-release provenance returned with every fact result."""

    dataset_key: Literal["dataset:endoviho-rag"]
    release_key: StableToken
    schema_version: NonEmptyString
    status: Literal["published"] = "published"
    manifest_sha256: Sha256
    published_at: datetime

    @field_validator("release_key")
    @classmethod
    def validate_release_key(cls, value: str) -> str:
        if not value.startswith(_RELEASE_PREFIX) or not is_release_key(value):
            raise ValueError("release_key does not follow the approved immutable grammar")
        return value

    @field_validator("published_at")
    @classmethod
    def validate_published_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at must be timezone-aware")
        return value


class ValidationCandidateReleaseRef(FrozenSchema):
    """Non-public provenance emitted only by an approved validation run."""

    dataset_key: Literal["dataset:endoviho-rag"]
    release_key: StableToken
    schema_version: NonEmptyString
    status: Literal["validation_candidate"] = "validation_candidate"
    manifest_sha256: Sha256
    candidate_created_at: datetime
    candidate_validation_input_sha256: Sha256
    candidate_capability_sha256: Sha256

    @field_validator("release_key")
    @classmethod
    def validate_release_key(cls, value: str) -> str:
        if not value.startswith(_RELEASE_PREFIX) or not is_release_key(value):
            raise ValueError("release_key does not follow the approved immutable grammar")
        return value

    @field_validator("candidate_created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("candidate_created_at must be timezone-aware")
        return value


type StructuredReleaseRef = Annotated[
    PublishedReleaseRef | ValidationCandidateReleaseRef,
    Field(discriminator="status"),
]


class Diagnostic(FrozenSchema):
    """Stable warning with a machine code and concise English message."""

    code: NonEmptyString
    message: NonEmptyString

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        if _DIAGNOSTIC_CODE_RE.fullmatch(value) is None:
            raise ValueError("diagnostic code must be lowercase snake_case")
        return value


class Limitation(FrozenSchema):
    """Approved scientific limitation attached deterministically to a result."""

    code: LimitationCode
    message: NonEmptyString


class ResolvedEntity(FrozenSchema):
    """One exact, release-scoped entity resolution returned to the caller."""

    original_input: NonEmptyString
    entity_kind: EntityKind
    match_mode: MatchMode
    stable_key: StableToken
    canonical_name: NonEmptyString | None = None
    snapshot_key: StableToken | None = None
    authority_namespace: StableToken | None = None
    snapshot_version: NonEmptyString | None = None
    scheme_kind: SchemeKind | None = None
    role: LineageRole | None = None

    @model_validator(mode="after")
    def validate_entity_shape(self) -> ResolvedEntity:
        lineage_fields = (
            self.canonical_name,
            self.snapshot_key,
            self.authority_namespace,
            self.snapshot_version,
            self.scheme_kind,
            self.role,
        )
        if self.entity_kind in {"source_lineage", "viral_lineage"}:
            if any(value is None for value in lineage_fields):
                raise ValueError("resolved lineage entities require complete snapshot provenance")
            if self.entity_kind == "source_lineage" and self.role != "assembly_source_taxonomy":
                raise ValueError("source lineage resolutions require assembly_source_taxonomy")
            if self.entity_kind == "viral_lineage" and self.role == "assembly_source_taxonomy":
                raise ValueError("viral lineage resolutions require a viral role")
            expected_scheme = (
                "study_defined"
                if self.role in {"study_viral_lineage", "extended_viral_lineage"}
                else "formal_taxonomy"
            )
            if self.scheme_kind != expected_scheme:
                raise ValueError("resolved lineage role and scheme_kind are inconsistent")
            if self.match_mode not in {
                "exact_stable_key",
                "exact_canonical_name",
                "exact_curated_alias",
            }:
                raise ValueError("lineage resolutions require a lineage match mode")
        else:
            if any(value is not None for value in lineage_fields[1:]):
                raise ValueError("assembly/locus resolutions cannot carry lineage snapshot fields")
            if self.entity_kind == "assembly":
                if self.match_mode not in {"exact_identifier", "exact_stable_key"}:
                    raise ValueError("assembly resolutions require an identifier match mode")
                accession = self.stable_key.removeprefix("assembly:ncbi:")
                if self.stable_key != f"assembly:ncbi:{accession}" or not (
                    is_versioned_assembly_accession(accession)
                ):
                    raise ValueError("resolved assembly stable_key is invalid")
            else:
                if self.match_mode != "exact_stable_key":
                    raise ValueError("locus resolutions require exact_stable_key")
                if _LOCUS_KEY_RE.fullmatch(self.stable_key) is None:
                    raise ValueError("resolved locus stable_key is invalid")
        return self


class EntitySuggestion(FrozenSchema):
    """A safe public-universe suggestion that is never auto-executed."""

    entity_kind: EntityKind
    stable_key: StableToken
    canonical_name: NonEmptyString | None = None
    snapshot_key: StableToken | None = None
    role: LineageRole | None = None

    @model_validator(mode="after")
    def validate_entity_shape(self) -> EntitySuggestion:
        if self.entity_kind in {"source_lineage", "viral_lineage"}:
            if self.canonical_name is None or self.snapshot_key is None or self.role is None:
                raise ValueError("lineage suggestions require name, snapshot, and role")
            if self.entity_kind == "source_lineage" and self.role != "assembly_source_taxonomy":
                raise ValueError("source lineage suggestions require assembly_source_taxonomy")
            if self.entity_kind == "viral_lineage" and self.role == "assembly_source_taxonomy":
                raise ValueError("viral lineage suggestions require a viral role")
        else:
            if self.snapshot_key is not None or self.role is not None:
                raise ValueError("assembly/locus suggestions cannot carry lineage fields")
            if self.entity_kind == "assembly":
                accession = self.stable_key.removeprefix("assembly:ncbi:")
                if self.stable_key != f"assembly:ncbi:{accession}" or not (
                    is_versioned_assembly_accession(accession)
                ):
                    raise ValueError("suggested assembly stable_key is invalid")
            elif _LOCUS_KEY_RE.fullmatch(self.stable_key) is None:
                raise ValueError("suggested locus stable_key is invalid")
        return self


class FieldError(FrozenSchema):
    """One stable request-field validation finding."""

    field: NonEmptyString
    code: NonEmptyString
    message: NonEmptyString


class StructuredError(FrozenSchema):
    """Machine-readable failure without database or candidate-state detail."""

    code: ErrorCode
    message: NonEmptyString
    field_errors: tuple[FieldError, ...] = ()
    suggestions: tuple[EntitySuggestion, ...] = ()

    @model_validator(mode="after")
    def validate_stable_order(self) -> StructuredError:
        if len(self.suggestions) > 5:
            raise ValueError("at most five entity suggestions are allowed")
        _require_sorted_unique(
            tuple((item.entity_kind, item.stable_key) for item in self.suggestions),
            field_name="suggestions",
        )
        _require_sorted_unique(
            tuple((item.field, item.code, item.message) for item in self.field_errors),
            field_name="field_errors",
        )
        return self


def _required_limitation_codes(data: StructuredData) -> set[LimitationCode]:
    required: set[LimitationCode] = set()
    if isinstance(data, AssemblyDetailData):
        required.add("assembly_source_taxon_is_not_ancient_host")
    elif isinstance(data, LocusDetailData):
        required.update(
            {
                "assembly_source_taxon_is_not_ancient_host",
                "assembly_local_locus_is_not_independent_integration_event",
                "coordinates_are_zero_based_half_open",
            }
        )
        if data.calls:
            required.add("detection_calls_are_not_loci")
        if any(item.source_confidence is not None for item in data.public_assertions):
            required.add("source_confidence_is_not_release_validation")
    elif isinstance(data, LocusPageData):
        if data.page.total_count > 0:
            required.update(
                {
                    "assembly_source_taxon_is_not_ancient_host",
                    "assembly_local_locus_is_not_independent_integration_event",
                    "coordinates_are_zero_based_half_open",
                }
            )
        else:
            required.add("zero_matches_do_not_establish_biological_absence")
    elif isinstance(data, (AssemblyPageData, SourceTaxonPageData)):
        if data.page.total_count > 0:
            required.add("assembly_source_taxon_is_not_ancient_host")
        else:
            required.add("zero_matches_do_not_establish_biological_absence")
    else:
        if data.metric_key == "distinct_source_taxon_count":
            required.add("assembly_source_taxon_is_not_ancient_host")
        elif data.metric_key in {"distinct_included_locus_count", "distinct_contig_count"}:
            required.add("assembly_local_locus_is_not_independent_integration_event")
        elif data.metric_key == "detection_call_count":
            required.add("detection_calls_are_not_loci")
        if data.value == 0:
            required.add("zero_matches_do_not_establish_biological_absence")
    return required


class StructuredResult(FrozenSchema):
    """One fact result bound to explicit published or validation-only provenance."""

    result_schema_version: Literal["structured-result-v1"] = "structured-result-v1"
    plan_sha256: Sha256
    release: StructuredReleaseRef
    data: StructuredData
    warnings: tuple[Diagnostic, ...] = ()
    limitations: tuple[Limitation, ...] = ()

    @model_validator(mode="after")
    def validate_diagnostics(self) -> StructuredResult:
        _require_sorted_unique(
            tuple((item.code, item.message) for item in self.warnings),
            field_name="warnings",
        )
        _require_sorted_unique(
            tuple((item.code, item.message) for item in self.limitations),
            field_name="limitations",
        )
        observed = {item.code for item in self.limitations}
        required = _required_limitation_codes(self.data)
        missing = required - observed
        if missing:
            raise ValueError(f"required limitation codes are missing: {sorted(missing)}")
        unexpected = observed - required
        if unexpected:
            raise ValueError(f"unexpected limitation codes are present: {sorted(unexpected)}")
        return self


class ResponseBase(FrozenSchema):
    """Fields shared by every structured-response variant."""

    response_schema_version: Literal["structured-query-response-v1"] = (
        "structured-query-response-v1"
    )
    resolved_entities: tuple[ResolvedEntity, ...] = ()

    @model_validator(mode="after")
    def validate_entity_order(self) -> ResponseBase:
        _require_sorted_unique(
            tuple((item.entity_kind, item.stable_key) for item in self.resolved_entities),
            field_name="resolved_entities",
        )
        return self


def _require_complete_planning_audit(audit: PlanningAudit) -> None:
    if not audit.extracted_conditions:
        raise ValueError("successful planning requires at least one extracted condition")
    if audit.unresolved_condition_ids:
        raise ValueError("successful planning requires every extracted condition to be mapped")
    if audit.unconsumed_semantic_spans:
        raise ValueError("successful planning requires every semantic span to be consumed")


def _require_resolved_entities_match_plan(
    plan: StructuredPlan,
    resolved_entities: tuple[ResolvedEntity, ...],
) -> None:
    expected: list[tuple[str, str, str | None, str | None]] = []
    if isinstance(plan.scope, FilteredScope):
        for query_filter in plan.scope.filters:
            if isinstance(query_filter, AssemblyFilter):
                expected.append(("assembly", query_filter.assembly_key, None, None))
            elif isinstance(query_filter, LocusFilter):
                expected.append(("locus", query_filter.locus_key, None, None))
            elif isinstance(query_filter, SourceLineageFilter):
                expected.append(
                    (
                        "source_lineage",
                        query_filter.term_key,
                        query_filter.snapshot_key,
                        query_filter.role,
                    )
                )
            elif isinstance(query_filter, ViralLineageFilter):
                expected.append(
                    (
                        "viral_lineage",
                        query_filter.term_key,
                        query_filter.snapshot_key,
                        query_filter.role,
                    )
                )

    observed = [
        (entity.entity_kind, entity.stable_key, entity.snapshot_key, entity.role)
        for entity in resolved_entities
    ]
    if sorted(observed) != sorted(expected):
        raise ValueError("resolved_entities do not exactly match the query plan filters")


class PlanSuccess(ResponseBase):
    """Successful planning response; no public fact query has executed."""

    response_kind: Literal["plan_success"] = "plan_success"
    query_plan: StructuredPlan
    planning_audit: PlanningAudit
    structured_result: None = None
    error: None = None
    fact_retrieval_executed: Literal[False] = False

    @model_validator(mode="after")
    def validate_planning_audit(self) -> PlanSuccess:
        _require_complete_planning_audit(self.planning_audit)
        _require_resolved_entities_match_plan(self.query_plan, self.resolved_entities)
        return self


class QuerySuccess(ResponseBase):
    """Successful public fact response with its exact validated plan."""

    response_kind: Literal["query_success"] = "query_success"
    query_plan: StructuredPlan
    planning_audit: PlanningAudit
    structured_result: StructuredResult
    error: None = None
    fact_retrieval_executed: Literal[True] = True

    @model_validator(mode="after")
    def validate_plan_result_binding(self) -> QuerySuccess:
        _require_complete_planning_audit(self.planning_audit)
        plan = self.query_plan
        result = self.structured_result
        data = result.data

        if result.plan_sha256 != canonical_plan_sha256(plan):
            raise ValueError("structured_result plan_sha256 does not match query_plan")
        if result.release.release_key != plan.release_key:
            raise ValueError("structured_result release_key does not match query_plan")

        expected_kind = {
            "assembly_detail": "assembly_detail",
            "locus_detail": "locus_detail",
            "list_loci": "locus_page",
            "list_assemblies": "assembly_page",
            "list_source_taxa": "source_taxon_page",
            "aggregate": "aggregate",
        }[plan.intent]
        if data.kind != expected_kind:
            raise ValueError("structured_result data kind does not match query intent")

        if isinstance(plan, AssemblyDetailPlan) and isinstance(data, AssemblyDetailData):
            if not isinstance(plan.scope, FilteredScope):
                raise ValueError("assembly detail plan is missing its filtered scope")
            query_filter = plan.scope.filters[0]
            if not isinstance(query_filter, AssemblyFilter):
                raise ValueError("assembly detail plan is missing its assembly filter")
            if data.assembly.assembly_key != query_filter.assembly_key:
                raise ValueError("assembly detail result does not match its plan filter")
        elif isinstance(plan, LocusDetailPlan) and isinstance(data, LocusDetailData):
            if not isinstance(plan.scope, FilteredScope):
                raise ValueError("locus detail plan is missing its filtered scope")
            query_filter = plan.scope.filters[0]
            if not isinstance(query_filter, LocusFilter):
                raise ValueError("locus detail plan is missing its locus filter")
            if data.locus.locus_key != query_filter.locus_key:
                raise ValueError("locus detail result does not match its plan filter")
        elif isinstance(plan, AggregatePlan) and isinstance(data, AggregateData):
            if data.metric_key != plan.metric_key:
                raise ValueError("aggregate result metric does not match query_plan")
        elif isinstance(plan, ListLociPlan) and isinstance(data, LocusPageData):
            if data.page.limit != plan.page.limit:
                raise ValueError("locus page limit does not match query_plan")
        elif isinstance(plan, ListAssembliesPlan) and isinstance(data, AssemblyPageData):
            if data.page.limit != plan.page.limit:
                raise ValueError("assembly page limit does not match query_plan")
        elif isinstance(plan, ListSourceTaxaPlan) and isinstance(data, SourceTaxonPageData):
            if data.page.limit != plan.page.limit:
                raise ValueError("source-taxon page limit does not match query_plan")
        _require_resolved_entities_match_plan(plan, self.resolved_entities)
        return self


class ErrorResponse(ResponseBase):
    """Stable refusal or failure envelope with no partial scientific result."""

    response_kind: Literal["error"] = "error"
    query_plan: StructuredPlan | None = None
    planning_audit: PlanningAudit | None = None
    structured_result: None = None
    error: StructuredError
    fact_retrieval_executed: bool = False

    @model_validator(mode="after")
    def validate_execution_context(self) -> ErrorResponse:
        if self.fact_retrieval_executed and (
            self.query_plan is None or self.planning_audit is None
        ):
            raise ValueError("post-query errors require their validated plan and planning audit")
        if self.fact_retrieval_executed and self.error.code not in _POST_QUERY_ERROR_CODES:
            raise ValueError("this error code cannot follow public fact retrieval")
        if self.fact_retrieval_executed and self.planning_audit is not None:
            _require_complete_planning_audit(self.planning_audit)
        if self.error.code in _RELEASE_ERROR_CODES and (
            self.resolved_entities or self.error.suggestions
        ):
            raise ValueError("release errors cannot expose resolved entities or suggestions")
        return self


type StructuredResponse = Annotated[
    PlanSuccess | QuerySuccess | ErrorResponse,
    Field(discriminator="response_kind"),
]
