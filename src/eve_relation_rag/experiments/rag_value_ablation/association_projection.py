"""Offline exact-association projection of an existing structured locus detail.

This adapter is not a retriever, release gate, question parser, or source of Gold.
Its caller must obtain ``QuerySuccess`` through the existing structured capability
gates, then bind the complete response and projection policy by their expected
checksums. Serializable bindings verify integrity, not execution authority.

Only a complete ``locus_detail`` has the source-call and public-assertion provenance
needed here. Pages and aggregates cannot supply it and are rejected, not expanded
into extra queries. Each output is one source-qualified exact membership; ancestor
closure, synonym mapping, relation classes, literature extraction, and cross-source
alignment are deliberately absent. The original immutable ``StructuredResult`` is
retained, including its exact placement and scientific limitations.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from eve_relation_rag.experiments.rag_value_ablation.associations import (
    AssemblySourceTaxonBinding,
    ExactAssociation,
    SourceRecordAnnotations,
    StructuredEvidenceSource,
    ViralLineageAffinity,
    association_sort_key,
)
from eve_relation_rag.hybrid.contracts import canonical_model_sha256
from eve_relation_rag.literature.contracts import (
    NonEmptyText,
    Sha256,
    StableToken,
    StrictFrozenSchema,
)
from eve_relation_rag.retrieval.structured.results import (
    CallDetail,
    LineageRef,
    LocusDetailData,
    PublicAssertionDetail,
    PublishedReleaseRef,
    QuerySuccess,
    StructuredResult,
)

type AssertionType = Literal["hcvr", "viral_major_taxon", "vr_type"]
type LocatorPolicy = Literal["data-s1-excel-row-v1", "worksheet-row-v1"]


class AssociationProjectionError(ValueError):
    """Fail-closed projection error with no SQL, raw data, or credentials."""


class SourceAssertionProjectionBinding(StrictFrozenSchema):
    """Exact approved source/method shape supplied by the caller's frozen policy."""

    assertion_type: AssertionType
    predicate_key: StableToken
    method_definition_key: StableToken
    method_version: NonEmptyText
    artifact_key: StableToken
    artifact_sha256: Sha256
    evidence_type: NonEmptyText
    verified_license_key: StableToken
    locator_policy: LocatorPolicy


class AssociationProjectionBindings(StrictFrozenSchema):
    """Input allowlist; neither a human annotation nor a release capability."""

    schema_version: Literal["rag-value-association-projection-bindings-v1"] = (
        "rag-value-association-projection-bindings-v1"
    )
    purpose: Literal["input_integrity_only_not_execution_authority"] = (
        "input_integrity_only_not_execution_authority"
    )
    release: PublishedReleaseRef
    permitted_lineages: tuple[LineageRef, ...] = Field(min_length=2)
    permitted_assertions: tuple[SourceAssertionProjectionBinding, ...] = Field(min_length=1)
    binding_sha256: Sha256

    @model_validator(mode="after")
    def validate_bindings(self) -> Self:
        lineage_keys = tuple(_lineage_key(item) for item in self.permitted_lineages)
        if lineage_keys != tuple(sorted(set(lineage_keys))):
            raise ValueError("permitted lineages must be canonically ordered and unique")
        if not any(item.role == "assembly_source_taxonomy" for item in self.permitted_lineages):
            raise ValueError("projection bindings require an assembly-source lineage")
        if not any(item.role != "assembly_source_taxonomy" for item in self.permitted_lineages):
            raise ValueError("projection bindings require a role-qualified viral lineage")
        assertion_keys = tuple(_binding_key(item) for item in self.permitted_assertions)
        if assertion_keys != tuple(sorted(set(assertion_keys))):
            raise ValueError("permitted assertion bindings must be canonically ordered and unique")
        if not any(
            item.assertion_type == "viral_major_taxon" for item in self.permitted_assertions
        ):
            raise ValueError("projection bindings require a viral-lineage assertion binding")
        payload = self.model_dump(mode="json", exclude={"binding_sha256"})
        if self.binding_sha256 != canonical_model_sha256(payload):
            raise ValueError("projection binding checksum differs from its contents")
        return self


class ExactAssociationProjection(StrictFrozenSchema):
    """Deterministic one-locus projection, explicitly not whole-release evidence."""

    schema_version: Literal["rag-value-exact-association-projection-v1"] = (
        "rag-value-exact-association-projection-v1"
    )
    scope: Literal["one_complete_locus_detail"] = "one_complete_locus_detail"
    complete_for_release: Literal[False] = False
    literature_alignment_performed: Literal[False] = False
    source_response_sha256: Sha256
    binding_sha256: Sha256
    structured_result: StructuredResult
    associations: tuple[ExactAssociation, ...] = Field(min_length=1)
    projection_sha256: Sha256

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        if not isinstance(self.structured_result.data, LocusDetailData):
            raise ValueError("an exact association projection requires locus detail")
        locus = self.structured_result.data.locus
        release = self.structured_result.release
        keys = tuple(association_sort_key(item) for item in self.associations)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("projected associations must be canonically ordered and unique")
        for item in self.associations:
            if (
                item.eve_locus_key != locus.locus_key
                or item.assembly_accession_version != locus.assembly_accession_version
                or item.evidence_source.release_key != release.release_key
                or item.evidence_source.release_manifest_sha256 != release.manifest_sha256
                or item.viral_lineage_affinity.include_descendants
            ):
                raise ValueError("projected association differs from its exact locus/release scope")
        payload = self.model_dump(mode="json", exclude={"projection_sha256"})
        if self.projection_sha256 != canonical_model_sha256(payload):
            raise ValueError("projection checksum differs from its contents")
        return self


def build_association_projection_bindings(
    *,
    release: PublishedReleaseRef,
    permitted_lineages: tuple[LineageRef, ...],
    permitted_assertions: tuple[SourceAssertionProjectionBinding, ...],
) -> AssociationProjectionBindings:
    """Checksum explicit caller inputs without approving or inferring any value."""

    payload = {
        "schema_version": "rag-value-association-projection-bindings-v1",
        "purpose": "input_integrity_only_not_execution_authority",
        "release": release,
        "permitted_lineages": tuple(sorted(permitted_lineages, key=_lineage_key)),
        "permitted_assertions": tuple(sorted(permitted_assertions, key=_binding_key)),
    }
    value = AssociationProjectionBindings.model_validate(
        {**payload, "binding_sha256": canonical_model_sha256(payload)}
    )
    # Frozen Pydantic instances can still arrive through model_construct/model_copy.
    return AssociationProjectionBindings.model_validate_json(value.model_dump_json())


def project_exact_associations(
    success: QuerySuccess,
    *,
    bindings: AssociationProjectionBindings,
    expected_binding_sha256: str,
    expected_source_sha256: str,
) -> ExactAssociationProjection:
    """Project only pinned, public, source-qualified assertions of one exact locus.

    No fallback runs after a rejection. This function makes no provider, filesystem,
    database, or network calls, and produces no benchmark approval or trust decision.
    The expected checksums must come from the caller's independently verified inputs,
    not from an untrusted response self-declaration.
    """

    if type(success) is not QuerySuccess or type(bindings) is not AssociationProjectionBindings:
        raise AssociationProjectionError(
            "exact structured response and projection bindings required"
        )
    try:
        verified = QuerySuccess.model_validate_json(success.model_dump_json())
        policy = AssociationProjectionBindings.model_validate_json(bindings.model_dump_json())
    except (ValueError, TypeError) as exc:
        raise AssociationProjectionError("projection inputs failed contract revalidation") from exc
    if policy.binding_sha256 != expected_binding_sha256:
        raise AssociationProjectionError("projection policy differs from its expected checksum")
    source_sha256 = canonical_model_sha256(verified)
    if source_sha256 != expected_source_sha256:
        raise AssociationProjectionError("structured response differs from its expected checksum")
    result = verified.structured_result
    if type(result.release) is not PublishedReleaseRef:
        raise AssociationProjectionError("projection requires a published release response")
    if result.release != policy.release:
        raise AssociationProjectionError(
            "structured release differs from the pinned projection release"
        )
    if not isinstance(result.data, LocusDetailData):
        raise AssociationProjectionError(
            "only complete locus_detail supports source-exact projection"
        )
    associations = _project_locus(result.data, policy)
    payload = {
        "schema_version": "rag-value-exact-association-projection-v1",
        "scope": "one_complete_locus_detail",
        "complete_for_release": False,
        "literature_alignment_performed": False,
        "source_response_sha256": source_sha256,
        "binding_sha256": policy.binding_sha256,
        "structured_result": result,
        "associations": associations,
    }
    return ExactAssociationProjection.model_validate(
        {**payload, "projection_sha256": canonical_model_sha256(payload)}
    )


def _project_locus(
    detail: LocusDetailData,
    policy: AssociationProjectionBindings,
) -> tuple[ExactAssociation, ...]:
    permitted_lineages = {_lineage_key(item): item for item in policy.permitted_lineages}
    for referenced_lineage in (detail.locus.source_taxon, *detail.locus.viral_lineages):
        if permitted_lineages.get(_lineage_key(referenced_lineage)) != referenced_lineage:
            raise AssociationProjectionError(
                "lineage identity, role, name, or version is not pinned"
            )
    if not detail.locus.viral_lineages:
        raise AssociationProjectionError("locus detail has no public viral-lineage assertion")
    # One source record cannot refer to conflicting physical rows or process runs.
    calls_by_source: dict[str, tuple[str, str, str, int, str]] = {}
    for call in detail.calls:
        identity = _call_source_identity(call)
        if calls_by_source.setdefault(call.source_record_key, identity) != identity:
            raise AssociationProjectionError("source record has conflicting physical provenance")
    grouped: dict[str, list[PublicAssertionDetail]] = {}
    for assertion in detail.public_assertions:
        if assertion.lineage is not None and (
            permitted_lineages.get(_lineage_key(assertion.lineage)) != assertion.lineage
        ):
            raise AssociationProjectionError(
                "assertion lineage differs from its pinned full identity"
            )
        binding = _resolve_assertion_binding(assertion, policy)
        source_record = _resolve_source_record(assertion, binding, detail.calls)
        grouped.setdefault(source_record, []).append(assertion)
    projected: dict[bytes, ExactAssociation] = {}
    for source_record, assertions in grouped.items():
        by_type: dict[AssertionType, set[str]] = {}
        for assertion in assertions:
            by_type.setdefault(assertion.assertion_type, set()).add(assertion.asserted_value)
        if any(len(values) != 1 for values in by_type.values()):
            raise AssociationProjectionError(
                "source record contains conflicting source annotations"
            )
        viral_assertions = [
            item for item in assertions if item.assertion_type == "viral_major_taxon"
        ]
        if not viral_assertions:
            raise AssociationProjectionError(
                "source annotations lack a source-linked viral affinity"
            )
        for assertion in viral_assertions:
            lineage = assertion.lineage
            if lineage is None or lineage.role == "assembly_source_taxonomy":
                raise AssociationProjectionError(
                    "viral assertion lacks an exact viral lineage role"
                )
            association = ExactAssociation(
                assembly_source_taxon=AssemblySourceTaxonBinding(
                    term_key=detail.locus.source_taxon.term_key,
                    canonical_name=detail.locus.source_taxon.canonical_name,
                    snapshot_key=detail.locus.source_taxon.snapshot_key,
                ),
                assembly_accession_version=detail.locus.assembly_accession_version,
                eve_locus_key=detail.locus.locus_key,
                viral_lineage_affinity=ViralLineageAffinity(
                    term_key=lineage.term_key,
                    canonical_name=lineage.canonical_name,
                    role=lineage.role,
                    snapshot_key=lineage.snapshot_key,
                    include_descendants=False,
                ),
                evidence_source=StructuredEvidenceSource(
                    release_key=policy.release.release_key,
                    release_manifest_sha256=policy.release.manifest_sha256,
                    source_record_keys=(source_record,),
                ),
                source_annotations=SourceRecordAnnotations(
                    hcvr=_annotation_value(by_type, "hcvr"),
                    vr_type=_annotation_value(by_type, "vr_type"),
                    viral_major_taxon=assertion.asserted_value,
                ),
            )
            projected[association_sort_key(association)] = association
    if not projected:
        raise AssociationProjectionError("locus detail has no projectable source-exact association")
    return tuple(projected[key] for key in sorted(projected))


def _resolve_assertion_binding(
    assertion: PublicAssertionDetail,
    policy: AssociationProjectionBindings,
) -> SourceAssertionProjectionBinding:
    evidence = assertion.supporting_evidence
    matches = [
        binding
        for binding in policy.permitted_assertions
        if (
            binding.assertion_type,
            binding.predicate_key,
            binding.method_definition_key,
            binding.method_version,
            binding.artifact_key,
            binding.artifact_sha256,
            binding.evidence_type,
            binding.verified_license_key,
        )
        == (
            assertion.assertion_type,
            assertion.predicate_key,
            assertion.method_definition_key,
            assertion.method_version,
            evidence.artifact_key,
            evidence.artifact_sha256,
            evidence.evidence_type,
            evidence.verified_license_key,
        )
    ]
    if len(matches) != 1:
        raise AssociationProjectionError(
            "source assertion is unbound or has ambiguous policy bindings"
        )
    return matches[0]


def _resolve_source_record(
    assertion: PublicAssertionDetail,
    binding: SourceAssertionProjectionBinding,
    calls: tuple[CallDetail, ...],
) -> str:
    evidence = assertion.supporting_evidence
    locator = evidence.source_locator
    if binding.locator_policy == "data-s1-excel-row-v1":
        if set(locator) != {"excel_row", "label", "worksheet"}:
            raise AssociationProjectionError(
                "source locator differs from the approved Data S1 shape"
            )
        row = locator["excel_row"]
        worksheet = locator["worksheet"]
        if locator["label"] != f"{worksheet}!{row}":
            raise AssociationProjectionError("source locator label differs from its physical row")
    else:
        if set(locator) != {"row", "worksheet"}:
            raise AssociationProjectionError(
                "source locator differs from the approved worksheet shape"
            )
        row = locator["row"]
        worksheet = locator["worksheet"]
    if type(row) is not int or row < 1 or not isinstance(worksheet, str) or not worksheet.strip():
        raise AssociationProjectionError(
            "source locator requires an exact worksheet and integer row"
        )
    matching_keys = {
        call.source_record_key
        for call in calls
        if _call_source_identity(call)
        == (
            evidence.artifact_key,
            evidence.artifact_sha256,
            worksheet,
            row,
            assertion.process_run_key,
        )
    }
    if len(matching_keys) != 1:
        raise AssociationProjectionError("source evidence does not match one exact source record")
    return next(iter(matching_keys))


def _call_source_identity(call: CallDetail) -> tuple[str, str, str, int, str]:
    return (
        call.artifact_key,
        call.artifact_sha256,
        call.worksheet,
        call.row_number,
        call.process_run_key,
    )


def _annotation_value(values: dict[AssertionType, set[str]], kind: AssertionType) -> str | None:
    return next(iter(values[kind])) if kind in values else None


def _lineage_key(lineage: LineageRef) -> tuple[str, str, str]:
    return (lineage.snapshot_key, lineage.term_key, lineage.role)


def _binding_key(binding: SourceAssertionProjectionBinding) -> str:
    return canonical_model_sha256(binding)


__all__ = [
    "AssociationProjectionBindings",
    "AssociationProjectionError",
    "ExactAssociationProjection",
    "SourceAssertionProjectionBinding",
    "build_association_projection_bindings",
    "project_exact_associations",
]
