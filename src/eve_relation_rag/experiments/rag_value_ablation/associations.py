"""Strict v1 association records for the RAG-value experiment.

The v1 contract follows only relationships represented by the available data::

    assembly-source taxon
      -> EVE locus or reported viral region
      -> viral-lineage affinity
      -> evidence and source

``HCVR``, ``VR Type``, and ``Viral Major Taxon`` are preserved as source annotations.
They are never converted into ``Transferred gene`` or ``Integrated virus`` labels.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, Field, field_validator, model_validator

from eve_relation_rag.domain.keys import is_versioned_assembly_accession
from eve_relation_rag.literature.contracts import (
    DocumentKey,
    NonEmptyText,
    Sha256,
    StableToken,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes

type ViralLineageRole = Literal[
    "formal_viral_taxonomy",
    "study_viral_lineage",
    "extended_viral_lineage",
]
type CrossSourceAlignmentState = Literal[
    "both",
    "structured_only",
    "literature_only",
    "unmatched",
    "ambiguous",
]
type AssociationDimension = Literal[
    "taxon",
    "region",
    "lineage",
    "role",
    "scope",
    "evidence_source",
    "source_annotations",
]

ASSOCIATION_DIMENSIONS: tuple[
    Literal["assembly_source_taxon"],
    Literal["eve_locus_or_reported_viral_region"],
    Literal["viral_lineage_affinity"],
    Literal["evidence_and_source"],
] = (
    "assembly_source_taxon",
    "eve_locus_or_reported_viral_region",
    "viral_lineage_affinity",
    "evidence_and_source",
)
PRESERVED_SOURCE_FIELDS: tuple[
    Literal["HCVR"], Literal["VR Type"], Literal["Viral Major Taxon"]
] = ("HCVR", "VR Type", "Viral Major Taxon")
FORBIDDEN_AUTOMATIC_MAPPINGS: tuple[str, ...] = (
    "HCVR -> Transferred gene",
    "HCVR -> Integrated virus",
    "VR Type: Integration -> Integrated virus",
    "VR Type: Viral contig -> Transferred gene",
)


def _validate_assembly_accession(value: str) -> str:
    if not is_versioned_assembly_accession(value):
        raise ValueError("association assembly must be an exact GCA_/GCF_ accession.version")
    return value


AssociationAssemblyAccessionVersion = Annotated[
    str,
    Field(min_length=1, max_length=32),
    AfterValidator(_validate_assembly_accession),
]


class AssemblySourceTaxonBinding(StrictFrozenSchema):
    """One exact assembly-source taxon in a frozen taxonomy snapshot."""

    term_key: StableToken
    canonical_name: NonEmptyText
    snapshot_key: StableToken
    role: Literal["assembly_source_taxonomy"] = "assembly_source_taxonomy"


class ViralLineageAffinity(StrictFrozenSchema):
    """One role-qualified viral-lineage affinity and its query scope."""

    term_key: StableToken
    canonical_name: NonEmptyText
    role: ViralLineageRole
    snapshot_key: StableToken
    include_descendants: bool


class SourceRecordAnnotations(StrictFrozenSchema):
    """Source-native fields retained verbatim without biological reclassification."""

    hcvr: NonEmptyText | None = None
    vr_type: NonEmptyText | None = None
    viral_major_taxon: NonEmptyText | None = None

    @model_validator(mode="after")
    def require_one_annotation(self) -> Self:
        if not any((self.hcvr, self.vr_type, self.viral_major_taxon)):
            raise ValueError("source annotations require at least one source-native value")
        return self


class StructuredEvidenceSource(StrictFrozenSchema):
    """Release and source-record provenance for one exact structured association."""

    evidence_kind: Literal["dataset_release"] = "dataset_release"
    release_key: StableToken
    release_manifest_sha256: Sha256
    source_record_keys: tuple[StableToken, ...] = Field(min_length=1)

    @field_validator("source_record_keys")
    @classmethod
    def canonical_source_records(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(values)) or len(values) != len(set(values)):
            raise ValueError("structured source record keys must be sorted and unique")
        return values


class LiteratureEvidenceSource(StrictFrozenSchema):
    """Document and evidence-group provenance for one reported association."""

    evidence_kind: Literal["permitted_literature"] = "permitted_literature"
    document_keys: tuple[DocumentKey, ...] = Field(min_length=1)
    evidence_group_ids: tuple[StableToken, ...] = Field(min_length=1)

    @field_validator("document_keys", "evidence_group_ids")
    @classmethod
    def canonical_evidence_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(values)) or len(values) != len(set(values)):
            raise ValueError("literature evidence values must be sorted and unique")
        return values


class ExactAssociation(StrictFrozenSchema):
    """One release-exact taxon/locus/lineage-affinity/evidence tuple."""

    association_kind: Literal["exact"] = "exact"
    assembly_source_taxon: AssemblySourceTaxonBinding
    assembly_accession_version: AssociationAssemblyAccessionVersion
    eve_locus_key: StableToken
    viral_lineage_affinity: ViralLineageAffinity
    evidence_source: StructuredEvidenceSource
    source_annotations: SourceRecordAnnotations | None = None


class SourceReportedAssociation(StrictFrozenSchema):
    """One literature-reported taxon/region/lineage-affinity/evidence tuple."""

    association_kind: Literal["source_reported"] = "source_reported"
    assembly_source_taxon_text: NonEmptyText
    named_eve_locus_or_viral_region: NonEmptyText
    viral_lineage_affinity_text: NonEmptyText
    viral_lineage_affinity: ViralLineageAffinity | None = None
    evidence_source: LiteratureEvidenceSource
    source_annotations: SourceRecordAnnotations | None = None


class CrossSourceAssociation(StrictFrozenSchema):
    """One human-reviewed alignment between structured and reported tuples."""

    association_kind: Literal["cross_source"] = "cross_source"
    alignment_state: CrossSourceAlignmentState
    structured_association: ExactAssociation | None = None
    source_reported_association: SourceReportedAssociation | None = None

    @model_validator(mode="after")
    def validate_alignment_state(self) -> Self:
        has_structured = self.structured_association is not None
        has_literature = self.source_reported_association is not None
        expected_shape = {
            "both": (True, True),
            "structured_only": (True, False),
            "literature_only": (False, True),
            "unmatched": (True, True),
            "ambiguous": (True, True),
        }[self.alignment_state]
        if (has_structured, has_literature) != expected_shape:
            raise ValueError("cross-source alignment state does not match association presence")
        return self


type AssociationRecord = ExactAssociation | SourceReportedAssociation | CrossSourceAssociation


class AssociationContractV1(StrictFrozenSchema):
    """Checksum-bound first-version association boundary used by the benchmark."""

    contract_schema_version: Literal["rag-value-association-contract-v1"] = (
        "rag-value-association-contract-v1"
    )
    contract_key: Literal["association-contract:endoviho-rag:v1"] = (
        "association-contract:endoviho-rag:v1"
    )
    dimensions: tuple[
        Literal["assembly_source_taxon"],
        Literal["eve_locus_or_reported_viral_region"],
        Literal["viral_lineage_affinity"],
        Literal["evidence_and_source"],
    ] = ASSOCIATION_DIMENSIONS
    relation_class_required: Literal[False] = False
    preserved_source_fields: tuple[
        Literal["HCVR"], Literal["VR Type"], Literal["Viral Major Taxon"]
    ] = PRESERVED_SOURCE_FIELDS
    forbidden_automatic_mappings: tuple[NonEmptyText, ...] = FORBIDDEN_AUTOMATIC_MAPPINGS
    record_sha256: Sha256

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.dimensions != ASSOCIATION_DIMENSIONS:
            raise ValueError("association dimensions must preserve the v1 chain order")
        if self.preserved_source_fields != PRESERVED_SOURCE_FIELDS:
            raise ValueError("source annotation fields must preserve source column order")
        if self.forbidden_automatic_mappings != FORBIDDEN_AUTOMATIC_MAPPINGS:
            raise ValueError("automatic-mapping prohibitions differ from the v1 boundary")
        payload = self.model_dump(mode="python")
        del payload["record_sha256"]
        if self.record_sha256 != hashlib.sha256(canonical_json_bytes(payload)).hexdigest():
            raise ValueError("association contract checksum does not match")
        return self


def build_association_contract_v1() -> AssociationContractV1:
    """Build the canonical first-version association contract."""

    payload = {
        "contract_schema_version": "rag-value-association-contract-v1",
        "contract_key": "association-contract:endoviho-rag:v1",
        "dimensions": ASSOCIATION_DIMENSIONS,
        "relation_class_required": False,
        "preserved_source_fields": PRESERVED_SOURCE_FIELDS,
        "forbidden_automatic_mappings": FORBIDDEN_AUTOMATIC_MAPPINGS,
    }
    return AssociationContractV1.model_validate(
        {
            **payload,
            "record_sha256": hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
        }
    )


def association_contract_v1_bytes() -> bytes:
    """Serialize the canonical v1 association contract."""

    return canonical_json_bytes(build_association_contract_v1()) + b"\n"


def association_sort_key(value: AssociationRecord) -> bytes:
    """Return the canonical byte key used to order an association set."""

    return canonical_json_bytes(value)


def validate_canonical_association_set(
    values: tuple[AssociationRecord, ...],
    *,
    association_kind: Literal["exact", "source_reported", "cross_source"],
) -> tuple[AssociationRecord, ...]:
    """Require one homogeneous, canonically ordered set without silently sorting it."""

    if any(value.association_kind != association_kind for value in values):
        raise ValueError("association set contains the wrong association kind")
    keys = tuple(association_sort_key(value) for value in values)
    if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
        raise ValueError("association set must be canonically ordered and unique")
    return values


def association_corruption_count(
    missing: Sequence[AssociationRecord],
    extra: Sequence[AssociationRecord],
    *,
    dimension: AssociationDimension,
) -> int:
    """Count substitutions differing in exactly one v1 association dimension."""

    missing_keys = Counter(_masked_key(value, dimension=dimension) for value in missing)
    extra_keys = Counter(_masked_key(value, dimension=dimension) for value in extra)
    return sum(
        min(missing_keys[key], extra_keys[key])
        for key in missing_keys.keys() & extra_keys.keys()
    )


def _masked_key(value: AssociationRecord, *, dimension: AssociationDimension) -> bytes:
    payload = value.model_dump(mode="python")
    return canonical_json_bytes(_mask_dimension(payload, dimension=dimension))


def _mask_dimension(
    value: object,
    *,
    dimension: AssociationDimension,
    parent_key: str | None = None,
) -> object:
    if isinstance(value, Mapping):
        masked: dict[str, object] = {}
        for key in sorted(value):
            if _field_belongs_to_dimension(key, parent_key=parent_key, dimension=dimension):
                continue
            masked[key] = _mask_dimension(value[key], dimension=dimension, parent_key=key)
        return masked
    if isinstance(value, (tuple, list)):
        return tuple(
            _mask_dimension(item, dimension=dimension, parent_key=parent_key)
            for item in value
        )
    return value


def _field_belongs_to_dimension(
    key: str,
    *,
    parent_key: str | None,
    dimension: AssociationDimension,
) -> bool:
    if dimension == "taxon":
        return key in {"assembly_source_taxon", "assembly_source_taxon_text"}
    if dimension == "region":
        return key in {
            "assembly_accession_version",
            "eve_locus_key",
            "named_eve_locus_or_viral_region",
        }
    if dimension == "lineage":
        return (
            parent_key == "viral_lineage_affinity"
            and key in {"term_key", "canonical_name", "snapshot_key"}
        ) or key == "viral_lineage_affinity_text"
    if dimension == "role":
        return parent_key == "viral_lineage_affinity" and key == "role"
    if dimension == "scope":
        return parent_key == "viral_lineage_affinity" and key == "include_descendants"
    if dimension == "evidence_source":
        return key == "evidence_source"
    return key == "source_annotations"


__all__ = [
    "ASSOCIATION_DIMENSIONS",
    "FORBIDDEN_AUTOMATIC_MAPPINGS",
    "PRESERVED_SOURCE_FIELDS",
    "AssociationContractV1",
    "AssemblySourceTaxonBinding",
    "CrossSourceAssociation",
    "ExactAssociation",
    "LiteratureEvidenceSource",
    "SourceRecordAnnotations",
    "SourceReportedAssociation",
    "StructuredEvidenceSource",
    "ViralLineageAffinity",
    "association_contract_v1_bytes",
    "association_corruption_count",
    "association_sort_key",
    "build_association_contract_v1",
    "validate_canonical_association_set",
]
