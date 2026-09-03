"""Strict association records used only by the RAG-value experiment.

These records describe the values that a future human-approved relation contract may expose.
They do not map source labels such as ``Integration``, ``Viral contig``, or ``HCVR`` to an
evaluation relation class and do not grant access to a release or corpus.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, Field, field_validator, model_validator

from eve_relation_rag.domain.keys import is_versioned_assembly_accession
from eve_relation_rag.literature.contracts import (
    NonEmptyText,
    Sha256,
    StableToken,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes

type RelationClass = Literal["Transferred gene", "Integrated virus"]
CANONICAL_RELATION_CLASSES: tuple[
    Literal["Transferred gene"], Literal["Integrated virus"]
] = ("Transferred gene", "Integrated virus")
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


def _validate_assembly_accession(value: str) -> str:
    if not is_versioned_assembly_accession(value):
        raise ValueError("association assembly must be an exact GCA_/GCF_ accession.version")
    return value


def _validate_relation_assertion_key(value: str) -> str:
    if not value.startswith("relation-assertion:") or value == "relation-assertion:":
        raise ValueError("relation assertion keys must use the relation-assertion namespace")
    return value


AssociationAssemblyAccessionVersion = Annotated[
    str,
    Field(min_length=1, max_length=32),
    AfterValidator(_validate_assembly_accession),
]
RelationAssertionKey = Annotated[
    str,
    Field(min_length=1, max_length=255),
    AfterValidator(_validate_relation_assertion_key),
]


class SourceSpeciesBinding(StrictFrozenSchema):
    """One exact assembly-source species identity in a frozen taxonomy snapshot."""

    term_key: StableToken
    canonical_name: NonEmptyText
    snapshot_key: StableToken
    role: Literal["assembly_source_taxonomy"] = "assembly_source_taxonomy"


class ViralLineageBinding(StrictFrozenSchema):
    """One role-qualified viral lineage and its exact-versus-descendant scope."""

    term_key: StableToken
    canonical_name: NonEmptyText
    role: ViralLineageRole
    snapshot_key: StableToken
    include_descendants: bool


class ExactAssociation(StrictFrozenSchema):
    """One release-exact source-species/assembly/locus/class/lineage tuple."""

    association_kind: Literal["exact"] = "exact"
    source_species: SourceSpeciesBinding
    assembly_accession_version: AssociationAssemblyAccessionVersion
    locus_key: StableToken
    relation_class: RelationClass
    relation_assertion_manifest_sha256: Sha256
    relation_assertion_key: RelationAssertionKey
    relation_assertion_sha256: Sha256
    viral_lineage: ViralLineageBinding


class SourceReportedAssociation(StrictFrozenSchema):
    """One corpus-source association preserving wording and evidence provenance."""

    association_kind: Literal["source_reported"] = "source_reported"
    source_taxon_text: NonEmptyText | None = None
    source_species_text: NonEmptyText | None = None
    named_assembly_or_region: NonEmptyText | None = None
    source_relation_text: NonEmptyText
    relation_class: RelationClass
    relation_assertion_manifest_sha256: Sha256
    relation_assertion_key: RelationAssertionKey
    relation_assertion_sha256: Sha256
    viral_lineage_text: NonEmptyText | None = None
    viral_lineage: ViralLineageBinding | None = None
    evidence_group_ids: tuple[StableToken, ...] = Field(min_length=1)

    @field_validator("evidence_group_ids")
    @classmethod
    def canonical_evidence_groups(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(values)) or len(values) != len(set(values)):
            raise ValueError("source-reported evidence groups must be sorted and unique")
        return values

    @model_validator(mode="after")
    def validate_reported_identity(self) -> Self:
        if not any(
            (
                self.source_taxon_text,
                self.source_species_text,
                self.named_assembly_or_region,
            )
        ):
            raise ValueError(
                "source-reported association requires at least one reported host or region "
                "descriptor"
            )
        if self.viral_lineage is not None and self.viral_lineage_text is None:
            raise ValueError(
                "a normalized viral-lineage binding requires source-reported lineage text"
            )
        return self


class CrossSourceAssociation(StrictFrozenSchema):
    """One human-reviewed alignment between structured and source-reported tuples."""

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


class PendingRelationContractTemplate(StrictFrozenSchema):
    """Empty ontology worksheet; it defines no scientific label or source mapping."""

    template_schema_version: Literal["rag-value-relation-contract-template-v1"] = (
        "rag-value-relation-contract-template-v1"
    )
    template_key: Literal["relation-contract:endoviho-rag:pending-v1"] = (
        "relation-contract:endoviho-rag:pending-v1"
    )
    relation_classes: tuple[
        Literal["Transferred gene"], Literal["Integrated virus"]
    ] = CANONICAL_RELATION_CLASSES
    definitions_supplied: Literal[False] = False
    source_label_mapping_supplied: Literal[False] = False
    unmapped_source_labels: tuple[
        Literal["HCVR"], Literal["Integration"], Literal["Viral contig"]
    ] = ("HCVR", "Integration", "Viral contig")
    assertion_template_schema_version: Literal[
        "rag-value-relation-class-assertion-template-v1"
    ] = "rag-value-relation-class-assertion-template-v1"
    review_status: Literal["pending"] = "pending"
    approval: Literal[None] = None
    record_sha256: Sha256

    @model_validator(mode="after")
    def validate_checksum(self) -> Self:
        payload = self.model_dump(mode="python")
        del payload["record_sha256"]
        if self.record_sha256 != hashlib.sha256(canonical_json_bytes(payload)).hexdigest():
            raise ValueError("pending relation contract template checksum does not match")
        return self


class PendingRelationClassAssertion(StrictFrozenSchema):
    """One unapproved annotation row; no target class may be prefilled."""

    assertion_schema_version: Literal[
        "rag-value-relation-class-assertion-template-v1"
    ] = "rag-value-relation-class-assertion-template-v1"
    assertion_key: RelationAssertionKey
    source_record_key: StableToken
    source_label: NonEmptyText
    relation_class: Literal[None] = None
    relation_contract_key: Literal[None] = None
    relation_contract_sha256: Literal[None] = None
    review_status: Literal["pending"] = "pending"
    approval: Literal[None] = None
    record_sha256: Sha256

    @model_validator(mode="after")
    def validate_checksum(self) -> Self:
        payload = self.model_dump(mode="python")
        del payload["record_sha256"]
        if self.record_sha256 != hashlib.sha256(canonical_json_bytes(payload)).hexdigest():
            raise ValueError("pending relation assertion checksum does not match")
        return self


def build_pending_relation_contract_template() -> PendingRelationContractTemplate:
    """Build the sole checksum-bound empty relation-contract worksheet."""

    payload = {
        "template_schema_version": "rag-value-relation-contract-template-v1",
        "template_key": "relation-contract:endoviho-rag:pending-v1",
        "relation_classes": CANONICAL_RELATION_CLASSES,
        "definitions_supplied": False,
        "source_label_mapping_supplied": False,
        "unmapped_source_labels": ("HCVR", "Integration", "Viral contig"),
        "assertion_template_schema_version": (
            "rag-value-relation-class-assertion-template-v1"
        ),
        "review_status": "pending",
        "approval": None,
    }
    return PendingRelationContractTemplate.model_validate(
        {
            **payload,
            "record_sha256": hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
        }
    )


def relation_contract_template_bytes() -> bytes:
    """Serialize the checksum-bound pending ontology worksheet as canonical JSON."""

    return canonical_json_bytes(build_pending_relation_contract_template()) + b"\n"


def relation_class_assertions_template_bytes() -> bytes:
    """Return an intentionally empty JSONL worksheet for future human assertions."""

    return b""


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
    dimension: Literal["class", "role", "scope"],
) -> int:
    """Count one-dimension substitutions between missing and extra association tuples.

    A tuple is counted only when every field outside that semantic dimension is byte-identical.
    The class dimension includes its assertion-record identity, but a changed assertion alone is
    not mislabeled as a changed class. This avoids assigning a cause when dimensions overlap.
    """

    if dimension == "class":
        return _class_corruption_count(missing, extra)
    missing_keys = Counter(_masked_key(value, dimension=dimension) for value in missing)
    extra_keys = Counter(_masked_key(value, dimension=dimension) for value in extra)
    return sum(
        min(missing_keys[key], extra_keys[key])
        for key in missing_keys.keys() & extra_keys.keys()
    )


def _class_corruption_count(
    missing: Sequence[AssociationRecord],
    extra: Sequence[AssociationRecord],
) -> int:
    missing_groups = _class_groups(missing)
    extra_groups = _class_groups(extra)
    count = 0
    for key in missing_groups.keys() & extra_groups.keys():
        missing_signatures = missing_groups[key]
        extra_signatures = extra_groups[key]
        missing_total = sum(missing_signatures.values())
        extra_total = sum(extra_signatures.values())
        largest_same_signature_pool = max(
            (
                missing_signatures[signature] + extra_signatures[signature]
                for signature in missing_signatures.keys() | extra_signatures.keys()
            ),
            default=0,
        )
        count += min(
            missing_total,
            extra_total,
            missing_total + extra_total - largest_same_signature_pool,
        )
    return count


def _class_groups(
    values: Sequence[AssociationRecord],
) -> dict[bytes, Counter[tuple[str, ...]]]:
    groups: dict[bytes, Counter[tuple[str, ...]]] = {}
    for value in values:
        key = _masked_key(value, dimension="class")
        groups.setdefault(key, Counter())[_relation_class_signature(value)] += 1
    return groups


def _relation_class_signature(value: AssociationRecord) -> tuple[str, ...]:
    classes: list[str] = []

    def collect(item: object) -> None:
        if isinstance(item, Mapping):
            for key in sorted(item):
                child = item[key]
                if key == "relation_class" and isinstance(child, str):
                    classes.append(child)
                else:
                    collect(child)
        elif isinstance(item, (tuple, list)):
            for child in item:
                collect(child)

    collect(value.model_dump(mode="python"))
    return tuple(classes)


def _masked_key(
    value: AssociationRecord,
    *,
    dimension: Literal["class", "role", "scope"],
) -> bytes:
    payload = value.model_dump(mode="python")
    masked = _mask_fields(payload, dimension=dimension)
    return canonical_json_bytes(masked)


def _mask_fields(
    value: object,
    *,
    dimension: Literal["class", "role", "scope"],
) -> object:
    if isinstance(value, Mapping):
        masked: dict[str, object] = {}
        is_viral_lineage = value.get("role") in {
            "formal_viral_taxonomy",
            "study_viral_lineage",
            "extended_viral_lineage",
        }
        for key, item in value.items():
            if dimension == "class" and key in {
                "relation_class",
                "relation_assertion_key",
                "relation_assertion_sha256",
            }:
                masked[key] = f"<{key}>"
            elif dimension == "role" and key == "role" and item != "assembly_source_taxonomy":
                masked[key] = "<viral-lineage-role>"
            elif (
                dimension == "scope"
                and is_viral_lineage
                and key in {"snapshot_key", "include_descendants"}
            ):
                masked[key] = f"<{key}>"
            else:
                masked[key] = _mask_fields(item, dimension=dimension)
        return masked
    if isinstance(value, (tuple, list)):
        return [_mask_fields(item, dimension=dimension) for item in value]
    return value
