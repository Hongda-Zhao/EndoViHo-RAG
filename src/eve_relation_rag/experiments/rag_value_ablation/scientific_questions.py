# ruff: noqa: E501 - exact preregistered question literals are intentionally kept on one line.
"""Authoring-only contracts for scientific RAG-value question templates.

These schemas cannot represent approved questions, Gold, Oracle evidence, results, or scores.
They keep natural researcher questions separate from the executable ``EvaluationQuestion``
contract until entities are release-bound and a human review has completed.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from eve_relation_rag.literature.contracts import (
    NonEmptyText,
    QuestionText,
    Sha256,
    StableToken,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256

type ScientificQuestionFamily = Literal["structured", "literature", "hybrid", "unsupported"]
type ScientificTask = Literal[
    "source_taxon_association",
    "viral_lineage_association",
    "source_viral_lineage_association",
    "assembly_locus_association",
    "unsupported_scientific_or_operational_boundary",
]
type ScientificIntent = Literal[
    "source_taxon_association",
    "viral_lineage_association",
    "source_viral_lineage_association",
    "assembly_locus_association",
    "unsupported_association_boundary",
    "unsupported_operational_boundary",
]
type EntitySlot = Literal[
    "HOST_LINEAGE_A",
    "HOST_SPECIES_A",
    "HOST_SPECIES_B",
    "VIRAL_LINEAGE_A",
    "VIRAL_LINEAGE_B",
    "EXTENDED_LINEAGE_A",
    "ASSEMBLY_A",
    "ASSEMBLY_B",
    "LOCUS_A",
    "LOCUS_B",
    "LOCUS_C",
]
type RequiredEntityType = Literal[
    "source_lineage",
    "source_species",
    "viral_lineage",
    "extended_viral_lineage",
    "assembly",
    "locus",
]
type ExpectedOutputType = Literal[
    "cross_source_association_set",
    "exact_association_set",
    "source_reported_association_set",
    "exact_locus_set",
    "exact_assembly_set",
    "exact_source_taxon_set",
    "exact_source_species_set",
    "exact_viral_lineage_set",
    "required_documents",
    "required_evidence_groups",
    "required_limitations",
    "forbidden_claims",
    "refusal_category",
    "prohibited_downstream_stages",
]
type RequiredCapability = Literal[
    "association_projection",
    "complete_paginated_relation_projection",
    "composite_structured_plan",
    "corpus_source_reported_scope",
    "cross_source_association_alignment",
    "relation_class_assertion",
    "explicit_unsupported_boundary",
    "source_taxonomy_projection",
    "lineage_role_and_scope_preservation",
    "list_assemblies",
    "list_loci",
    "list_source_taxa",
    "literature_association_extraction",
    "literature_entity_discoverability",
    "literature_entity_normalization",
    "literature_retrieval",
    "locus_detail",
    "multi_result_structured_envelope",
    "natural_hybrid_decomposition",
    "natural_literature_routing",
    "natural_structured_planning",
    "refusal_before_downstream_execution",
    "relation_contract",
    "release_represented_source_scope",
    "structured_anchor_resolution",
]
type CapabilityStatus = Literal[
    "supported_now",
    "requires_relation_contract",
    "unsupported_by_design",
]

_PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")
_FAKE_ALL_A_LOCUS = (
    "locus:eve:v1:sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
)
_MECHANICAL_HYBRID_PHRASE = ". and explain the literature"
_EXPLANATORY_QUESTION_RE = re.compile(
    r"(?:\bhow\b|\bwhy\b|\bmethods?\b|\bevidence\s+supports?\b|\blimitations?\b|"
    r"\buncertaint(?:y|ies)\b|\binterpret(?:ation|ed|ing)\b)",
    re.IGNORECASE,
)
_REQUIRED_RELATION_CLASSES = ("Transferred gene", "Integrated virus")
_ENTITY_TYPES: dict[EntitySlot, RequiredEntityType] = {
    "ASSEMBLY_A": "assembly",
    "ASSEMBLY_B": "assembly",
    "EXTENDED_LINEAGE_A": "extended_viral_lineage",
    "HOST_LINEAGE_A": "source_lineage",
    "HOST_SPECIES_A": "source_species",
    "HOST_SPECIES_B": "source_species",
    "LOCUS_A": "locus",
    "LOCUS_B": "locus",
    "LOCUS_C": "locus",
    "VIRAL_LINEAGE_A": "viral_lineage",
    "VIRAL_LINEAGE_B": "viral_lineage",
}
_ALLOWED_ENTITY_SLOTS: frozenset[str] = frozenset(_ENTITY_TYPES)
_PREREGISTERED_METADATA: dict[
    str,
    tuple[ScientificQuestionFamily, ScientificTask, ScientificIntent],
] = {}
_PREREGISTERED_RECORD_SHA256: dict[str, str] = {}
_MISSING_CAPABILITIES: frozenset[str] = frozenset(
    {
        "association_projection",
        "complete_paginated_relation_projection",
        "composite_structured_plan",
        "corpus_source_reported_scope",
        "cross_source_association_alignment",
        "relation_class_assertion",
        "explicit_unsupported_boundary",
        "source_taxonomy_projection",
        "lineage_role_and_scope_preservation",
        "literature_association_extraction",
        "literature_entity_normalization",
        "multi_result_structured_envelope",
        "natural_hybrid_decomposition",
        "natural_literature_routing",
        "natural_structured_planning",
        "relation_contract",
        "release_represented_source_scope",
    }
)


class ScientificQuestionTemplate(StrictFrozenSchema):
    """One pending natural-language authoring record, never a trusted question."""

    template_schema_version: Literal["rag-value-scientific-question-template-v1"] = (
        "rag-value-scientific-question-template-v1"
    )
    template_id: StableToken
    family: ScientificQuestionFamily
    scientific_task: ScientificTask
    scientific_intent: ScientificIntent
    question_text_template: QuestionText
    entity_slots: tuple[EntitySlot, ...] = ()
    expected_output_types: tuple[ExpectedOutputType, ...] = Field(min_length=1)
    required_capabilities: tuple[RequiredCapability, ...] = Field(min_length=1)
    capability_status: CapabilityStatus
    review_status: Literal["pending"] = "pending"
    gold: Literal[None] = None
    authoring_notes: NonEmptyText
    record_sha256: Sha256

    @field_validator("entity_slots", "expected_output_types", "required_capabilities")
    @classmethod
    def canonical_collections(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(values)) or len(values) != len(set(values)):
            raise ValueError("template collections must be sorted and unique")
        return values

    @model_validator(mode="after")
    def validate_authoring_boundary(self) -> Self:
        expected_metadata = _PREREGISTERED_METADATA.get(self.template_id)
        if expected_metadata is None:
            raise ValueError("scientific question template ID is not preregistered")
        if (
            self.family,
            self.scientific_task,
            self.scientific_intent,
        ) != expected_metadata:
            raise ValueError("template ID, family, scientific task, and intent do not match")
        placeholders = tuple(sorted(set(_PLACEHOLDER_RE.findall(self.question_text_template))))
        if any(slot not in _ALLOWED_ENTITY_SLOTS for slot in placeholders):
            raise ValueError("question template contains an undeclared placeholder vocabulary")
        if placeholders != self.entity_slots:
            raise ValueError("entity_slots must exactly declare placeholders used in the text")
        text_without_placeholders = _PLACEHOLDER_RE.sub("", self.question_text_template)
        if "{" in text_without_placeholders or "}" in text_without_placeholders:
            raise ValueError("question template contains a malformed placeholder")
        if _FAKE_ALL_A_LOCUS in self.question_text_template:
            raise ValueError("scientific templates cannot contain the fake all-a locus key")
        if _MECHANICAL_HYBRID_PHRASE in self.question_text_template.casefold():
            raise ValueError("scientific templates cannot use the mechanical Hybrid suffix")
        if self.family != "unsupported":
            if _EXPLANATORY_QUESTION_RE.search(self.question_text_template):
                raise ValueError("answerable association questions cannot request explanations")
            if any(
                relation_class not in self.question_text_template
                for relation_class in _REQUIRED_RELATION_CLASSES
            ):
                raise ValueError("answerable questions must name both relation classes")
            normalized_question = self.question_text_template.casefold()
            if (
                "viral lineage" not in normalized_question
                and "viral-lineage" not in normalized_question
                and "{viral_lineage_" not in normalized_question
                and "{extended_lineage_" not in normalized_question
            ):
                raise ValueError("answerable questions must retain the viral-lineage dimension")
            required_relation_capabilities = {
                "association_projection",
                "relation_class_assertion",
                "lineage_role_and_scope_preservation",
                "relation_contract",
            }
            if not required_relation_capabilities.issubset(self.required_capabilities):
                raise ValueError("answerable questions must declare every relation-class boundary")
            if "has not approved Transferred gene or Integrated virus" not in self.authoring_notes:
                raise ValueError("answerable question note must state unapproved relation classes")
            if "Integration, Viral contig, or HCVR" not in self.authoring_notes:
                raise ValueError("answerable question note must forbid source-label class mapping")
            outputs = set(self.expected_output_types)
            if not {"forbidden_claims", "required_limitations"}.issubset(outputs):
                raise ValueError("answerable questions must retain safety-scoring outputs")
            capabilities = set(self.required_capabilities)
            existing_query_primitives = {
                "list_assemblies",
                "list_loci",
                "list_source_taxa",
                "locus_detail",
            }
            if self.family in {"structured", "hybrid"} and any(
                marker in normalized_question
                for marker in ("host species", "host-species", "host taxonomic")
            ):
                raise ValueError("structured wording must preserve assembly-source semantics")
            if self.family in {"structured", "hybrid"} and not capabilities.intersection(
                existing_query_primitives
            ):
                raise ValueError("structured evidence must reuse an existing query primitive")
            if self.family == "literature" and capabilities.intersection(
                existing_query_primitives
            ):
                raise ValueError("literature-only questions cannot require structured queries")
            if self.family == "structured" and outputs & {
                "source_reported_association_set",
                "cross_source_association_set",
            }:
                raise ValueError("structured questions cannot contain literature association sets")
            if self.family == "structured":
                if "release_represented_source_scope" not in capabilities:
                    raise ValueError("structured questions require exact release source scope")
                if "corpus_source_reported_scope" in capabilities:
                    raise ValueError("structured questions cannot require corpus-only scope")
                if "assembly-source taxon is not an ancient or modern host" not in self.authoring_notes:
                    raise ValueError("structured question note must preserve source-taxon semantics")
            elif self.family == "literature":
                if "source_reported_association_set" not in outputs or any(
                    output.startswith("exact_") for output in outputs
                ) or "cross_source_association_set" in outputs:
                    raise ValueError("literature questions require only source-reported associations")
                if any(
                    marker in normalized_question
                    for marker in (
                        "both sources",
                        "cross-source",
                        "datasetrelease",
                        "exact",
                        "literature-only",
                        "selected release",
                        "structured-only",
                    )
                ):
                    raise ValueError("literature questions cannot imply structured or cross-source evidence")
                if "corpus_source_reported_scope" not in capabilities:
                    raise ValueError("literature questions require permitted-corpus source scope")
                if "release_represented_source_scope" in capabilities:
                    raise ValueError("literature questions cannot depend on DatasetRelease scope")
                if "permitted-corpus source-reported host wording and provenance" not in self.authoring_notes:
                    raise ValueError("literature question note must preserve source wording")
            else:
                if not {
                    "cross_source_association_set",
                    "exact_association_set",
                    "source_reported_association_set",
                }.issubset(outputs):
                    raise ValueError("hybrid questions must preserve all three association sets")
                if not {
                    "corpus_source_reported_scope",
                    "release_represented_source_scope",
                }.issubset(capabilities):
                    raise ValueError("hybrid questions require separate release and corpus scopes")
                if "assembly-source taxon is not an ancient or modern host" not in self.authoring_notes:
                    raise ValueError("hybrid question note must preserve source-taxon semantics")
                if "must not overwrite structured values" not in self.authoring_notes:
                    raise ValueError("hybrid question note must separate source-reported labels")
            if self.capability_status != "requires_relation_contract":
                raise ValueError("answerable association questions require the relation contract")
        if self.family == "unsupported":
            if self.capability_status != "unsupported_by_design":
                raise ValueError("unsupported questions must be marked unsupported_by_design")
        elif self.capability_status == "unsupported_by_design":
            raise ValueError("answerable families cannot be marked unsupported_by_design")
        if self.capability_status == "supported_now" and (
            set(self.required_capabilities) & _MISSING_CAPABILITIES
        ):
            raise ValueError("a missing capability cannot be silently marked supported_now")
        if self.record_sha256 != _self_sha256(self, "record_sha256"):
            raise ValueError("scientific question template checksum does not match")
        if self.record_sha256 != _PREREGISTERED_RECORD_SHA256[self.template_id]:
            raise ValueError("scientific question template differs from preregistered content")
        return self


class ScientificEntityBindingTemplate(StrictFrozenSchema):
    """One empty slot awaiting release-scoped human selection."""

    binding_schema_version: Literal["rag-value-scientific-entity-binding-template-v1"] = (
        "rag-value-scientific-entity-binding-template-v1"
    )
    entity_slot: EntitySlot
    required_entity_type: RequiredEntityType
    selected_stable_key: Literal[None] = None
    selected_display_name: Literal[None] = None
    release_key: Literal[None] = None
    release_manifest_sha256: Literal[None] = None
    selected_snapshot_key: Literal[None] = None
    selected_lineage_role: Literal[None] = None
    include_descendants: Literal[None] = None
    review_status: Literal["pending"] = "pending"
    record_sha256: Sha256

    @model_validator(mode="after")
    def validate_checksum(self) -> Self:
        if self.required_entity_type != _ENTITY_TYPES[self.entity_slot]:
            raise ValueError("entity slot and required entity type do not match")
        if self.record_sha256 != _self_sha256(self, "record_sha256"):
            raise ValueError("scientific entity binding checksum does not match")
        return self


class ScientificEntityBindingsTemplate(StrictFrozenSchema):
    """Canonical empty binding worksheet covering the complete slot vocabulary."""

    manifest_schema_version: Literal["rag-value-scientific-entity-bindings-template-v1"] = (
        "rag-value-scientific-entity-bindings-template-v1"
    )
    binding_count: Literal[11] = 11
    bindings: tuple[ScientificEntityBindingTemplate, ...] = Field(min_length=11, max_length=11)
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        slots = tuple(binding.entity_slot for binding in self.bindings)
        if slots != tuple(sorted(_ALLOWED_ENTITY_SLOTS)):
            raise ValueError("binding worksheet must cover every entity slot in canonical order")
        if self.manifest_sha256 != _self_sha256(self, "manifest_sha256"):
            raise ValueError("scientific entity binding manifest checksum does not match")
        return self


class ScientificTemplateSetError(ValueError):
    """Raised when the complete scientific template set violates its preregistration."""


@dataclass(frozen=True, slots=True)
class _TemplateSpec:
    template_id: str
    question_text_template: str
    scientific_intent: ScientificIntent
    expected_output_types: tuple[ExpectedOutputType, ...]
    required_capabilities: tuple[RequiredCapability, ...]
    capability_status: CapabilityStatus
    authoring_notes: str | None = None


_COMMON_PENDING_NOTE = (
    "Pending association-only authoring template. The current repository has not approved "
    "Transferred gene or Integrated virus as relation classes. Do not map Integration, Viral "
    "contig, or HCVR source labels to either class. Preserve every viral-lineage binding's role "
    "and exact-versus-descendant semantics. "
)
_STRUCTURED_NOTE = _COMMON_PENDING_NOTE + (
    "Enumerate only represented source species and assemblies in the exact selected release; "
    "this is not a complete biological descendant set. An assembly-source taxon is not an "
    "ancient or modern host. Bind every entity slot and obtain independent human review before "
    "conversion to an EvaluationQuestion."
)
_LITERATURE_NOTE = _COMMON_PENDING_NOTE + (
    "Preserve permitted-corpus source-reported host wording and provenance. Literature-only "
    "associations neither assert nor depend on DatasetRelease membership. Bind every entity slot "
    "and obtain independent human review before conversion to an EvaluationQuestion."
)
_HYBRID_NOTE = _COMMON_PENDING_NOTE + (
    "Enumerate structured taxa only as represented source species and assemblies in the exact "
    "selected release; this is not a complete biological descendant set. An assembly-source "
    "taxon is not an ancient or modern host. Preserve permitted-corpus source-reported wording "
    "and provenance separately; source-reported labels must not overwrite structured values. "
    "Bind every entity slot and obtain independent human review before conversion to an "
    "EvaluationQuestion."
)
_UNSUPPORTED_NOTE = (
    "Pending association-boundary template only. Human review must define the exact refusal "
    "category and prohibited downstream stages; no answer, evidence, or approval is supplied. "
    "The current repository has not approved Transferred gene or Integrated virus as relation "
    "classes, and Integration, Viral contig, or HCVR source labels must not be mapped to them."
)
_COMMON_RELATION_CAPABILITIES: tuple[RequiredCapability, ...] = (
    "association_projection",
    "relation_class_assertion",
    "lineage_role_and_scope_preservation",
    "relation_contract",
)


def _structured_caps(*extra: RequiredCapability) -> tuple[RequiredCapability, ...]:
    return (
        *_COMMON_RELATION_CAPABILITIES,
        "natural_structured_planning",
        "release_represented_source_scope",
        *extra,
    )


def _lit_caps(*extra: RequiredCapability) -> tuple[RequiredCapability, ...]:
    return (
        *_COMMON_RELATION_CAPABILITIES,
        "corpus_source_reported_scope",
        "literature_association_extraction",
        "literature_entity_normalization",
        "literature_retrieval",
        "natural_literature_routing",
        *extra,
    )


def _hybrid_caps(*structured: RequiredCapability) -> tuple[RequiredCapability, ...]:
    return (
        *_COMMON_RELATION_CAPABILITIES,
        "corpus_source_reported_scope",
        "cross_source_association_alignment",
        "literature_association_extraction",
        "literature_entity_normalization",
        "literature_retrieval",
        "natural_hybrid_decomposition",
        "release_represented_source_scope",
        "structured_anchor_resolution",
        *structured,
    )


def _structured_outputs(*extra: ExpectedOutputType) -> tuple[ExpectedOutputType, ...]:
    return ("exact_association_set", "forbidden_claims", "required_limitations", *extra)


def _literature_outputs() -> tuple[ExpectedOutputType, ...]:
    return (
        "forbidden_claims",
        "required_documents",
        "required_evidence_groups",
        "required_limitations",
        "source_reported_association_set",
    )


def _hybrid_outputs(*extra: ExpectedOutputType) -> tuple[ExpectedOutputType, ...]:
    return (
        "cross_source_association_set",
        "exact_association_set",
        "forbidden_claims",
        "required_documents",
        "required_evidence_groups",
        "required_limitations",
        "source_reported_association_set",
        *extra,
    )


_SPECS: tuple[_TemplateSpec, ...] = (
    _TemplateSpec(
        "HOST-S-01",
        "Which represented source species within {HOST_LINEAGE_A} have records classified as Transferred gene, and which have records classified as Integrated virus, grouped by viral lineage in the selected release?",
        "source_taxon_association",
        _structured_outputs("exact_source_species_set"),
        _structured_caps("source_taxonomy_projection", "list_source_taxa"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "HOST-S-02",
        "For each represented source species within {HOST_LINEAGE_A}, which assemblies contain Transferred gene records and which contain Integrated virus records, grouped by viral lineage?",
        "source_taxon_association",
        _structured_outputs("exact_assembly_set", "exact_source_species_set"),
        _structured_caps("source_taxonomy_projection", "list_assemblies"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "HOST-S-03",
        "For each represented source species within {HOST_LINEAGE_A}, which viral lineages are recorded for Transferred gene records and which are recorded for Integrated virus records?",
        "source_taxon_association",
        _structured_outputs("exact_source_species_set", "exact_viral_lineage_set"),
        _structured_caps(
            "complete_paginated_relation_projection",
            "list_loci",
            "source_taxonomy_projection",
        ),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "HOST-S-04",
        "Which exact association tuples link represented source species within {HOST_LINEAGE_A}, their assemblies and loci, the classes Transferred gene or Integrated virus, and their viral lineages in the selected release?",
        "source_taxon_association",
        _structured_outputs(
            "exact_assembly_set",
            "exact_locus_set",
            "exact_source_species_set",
            "exact_viral_lineage_set",
        ),
        _structured_caps(
            "composite_structured_plan",
            "source_taxonomy_projection",
            "list_assemblies",
            "list_loci",
            "complete_paginated_relation_projection",
        ),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "HOST-L-01",
        "Which host species within {HOST_LINEAGE_A} does the permitted literature report with Transferred gene records, and which does it report with Integrated virus records, grouped by viral lineage?",
        "source_taxon_association",
        _literature_outputs(),
        _lit_caps(),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "HOST-L-02",
        "For host species within {HOST_LINEAGE_A}, which assemblies does the permitted literature associate with Transferred gene records and which with Integrated virus records, grouped by viral lineage?",
        "source_taxon_association",
        _literature_outputs(),
        _lit_caps("literature_entity_discoverability"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "HOST-L-03",
        "For each host species within {HOST_LINEAGE_A}, which viral lineages does the permitted literature associate with Transferred gene records and which with Integrated virus records?",
        "source_taxon_association",
        _literature_outputs(),
        _lit_caps(),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "HOST-L-04",
        "Which literature-reported association tuples link host species within {HOST_LINEAGE_A}, their named assemblies or regions, the classes Transferred gene or Integrated virus, and viral lineages?",
        "source_taxon_association",
        _literature_outputs(),
        _lit_caps("literature_entity_discoverability"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "HOST-H-01",
        "Which represented source species within {HOST_LINEAGE_A} have Transferred gene associations in both the selected release and the permitted literature, and which have Integrated virus associations in both, grouped by viral lineage?",
        "source_taxon_association",
        _hybrid_outputs("exact_source_species_set"),
        _hybrid_caps("source_taxonomy_projection", "list_source_taxa"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "HOST-H-02",
        "For represented source species within {HOST_LINEAGE_A}, which assemblies have Transferred gene associations in both sources and which have Integrated virus associations in both, grouped by viral lineage?",
        "source_taxon_association",
        _hybrid_outputs("exact_assembly_set", "exact_source_species_set"),
        _hybrid_caps("source_taxonomy_projection", "list_assemblies"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "HOST-H-03",
        "For each represented source species within {HOST_LINEAGE_A}, which viral lineages have Transferred gene associations in both sources and which have Integrated virus associations in both?",
        "source_taxon_association",
        _hybrid_outputs("exact_source_species_set", "exact_viral_lineage_set"),
        _hybrid_caps(
            "complete_paginated_relation_projection",
            "list_loci",
            "source_taxonomy_projection",
        ),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "HOST-H-04",
        "Which exact source-species, assembly, locus, relation-class, and viral-lineage association tuples are structured-only, literature-only, or present in both within {HOST_LINEAGE_A}, separating Transferred gene from Integrated virus?",
        "source_taxon_association",
        _hybrid_outputs(
            "exact_assembly_set",
            "exact_locus_set",
            "exact_source_species_set",
            "exact_viral_lineage_set",
        ),
        _hybrid_caps(
            "composite_structured_plan",
            "source_taxonomy_projection",
            "list_assemblies",
            "list_loci",
            "complete_paginated_relation_projection",
            "multi_result_structured_envelope",
        ),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "VIRUS-S-01",
        "Which release-represented assembly-source taxonomic units have records assigned to {VIRAL_LINEAGE_A}, separated into Transferred gene and Integrated virus records?",
        "viral_lineage_association",
        _structured_outputs("exact_source_taxon_set"),
        _structured_caps("source_taxonomy_projection", "list_source_taxa"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "VIRUS-S-02",
        "Which release-represented source species have records assigned to {VIRAL_LINEAGE_A}, separated into Transferred gene and Integrated virus records?",
        "viral_lineage_association",
        _structured_outputs("exact_source_species_set"),
        _structured_caps("source_taxonomy_projection", "list_source_taxa"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "VIRUS-S-03",
        "For each release-represented source species associated with {VIRAL_LINEAGE_A}, which assemblies contain Transferred gene records and which contain Integrated virus records?",
        "viral_lineage_association",
        _structured_outputs("exact_assembly_set", "exact_source_species_set"),
        _structured_caps("source_taxonomy_projection", "list_assemblies"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "VIRUS-S-04",
        "Which exact loci are assigned to {VIRAL_LINEAGE_A}, grouped by release-represented source species and assembly and separated into Transferred gene and Integrated virus records?",
        "viral_lineage_association",
        _structured_outputs("exact_assembly_set", "exact_locus_set", "exact_source_species_set"),
        _structured_caps(
            "composite_structured_plan",
            "source_taxonomy_projection",
            "list_assemblies",
            "list_loci",
            "multi_result_structured_envelope",
        ),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "VIRUS-L-01",
        "Which host taxonomic units does the permitted literature associate with {VIRAL_LINEAGE_A} through Transferred gene records, and which through Integrated virus records?",
        "viral_lineage_association",
        _literature_outputs(),
        _lit_caps(),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "VIRUS-L-02",
        "Which host species does the permitted literature associate with {VIRAL_LINEAGE_A} through Transferred gene records, and which through Integrated virus records?",
        "viral_lineage_association",
        _literature_outputs(),
        _lit_caps(),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "VIRUS-L-03",
        "For host species associated with {VIRAL_LINEAGE_A}, which assemblies does the permitted literature link to Transferred gene records and which to Integrated virus records?",
        "viral_lineage_association",
        _literature_outputs(),
        _lit_caps("literature_entity_discoverability"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "VIRUS-L-04",
        "Which named loci or source regions does the permitted literature associate with {VIRAL_LINEAGE_A}, separated into Transferred gene and Integrated virus records?",
        "viral_lineage_association",
        _literature_outputs(),
        _lit_caps("literature_entity_discoverability"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "VIRUS-H-01",
        "Which release-represented assembly-source taxonomic units are also reported in the permitted literature with {VIRAL_LINEAGE_A} through Transferred gene records, and which through Integrated virus records?",
        "viral_lineage_association",
        _hybrid_outputs("exact_source_taxon_set"),
        _hybrid_caps("source_taxonomy_projection", "list_source_taxa"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "VIRUS-H-02",
        "Which release-represented source species are also reported in the permitted literature with {VIRAL_LINEAGE_A} through Transferred gene records, and which through Integrated virus records?",
        "viral_lineage_association",
        _hybrid_outputs("exact_source_species_set"),
        _hybrid_caps("source_taxonomy_projection", "list_source_taxa"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "VIRUS-H-03",
        "For release-represented source species associated with {VIRAL_LINEAGE_A}, which assemblies have Transferred gene associations in both sources and which have Integrated virus associations in both?",
        "viral_lineage_association",
        _hybrid_outputs("exact_assembly_set", "exact_source_species_set"),
        _hybrid_caps("source_taxonomy_projection", "list_assemblies"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "VIRUS-H-04",
        "Which exact release loci assigned to {VIRAL_LINEAGE_A} have matching literature-reported Transferred gene associations, and which have matching Integrated virus associations?",
        "viral_lineage_association",
        _hybrid_outputs("exact_locus_set"),
        _hybrid_caps("list_loci"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "REL-S-01",
        "Which release-represented source species within {HOST_LINEAGE_A} have Transferred gene associations with {VIRAL_LINEAGE_A}, and which have Integrated virus associations?",
        "source_viral_lineage_association",
        _structured_outputs("exact_source_species_set"),
        _structured_caps("source_taxonomy_projection", "list_source_taxa"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "REL-S-02",
        "For release-represented source species within {HOST_LINEAGE_A} associated with {VIRAL_LINEAGE_A}, which assemblies contain Transferred gene records and which contain Integrated virus records?",
        "source_viral_lineage_association",
        _structured_outputs("exact_assembly_set", "exact_source_species_set"),
        _structured_caps("source_taxonomy_projection", "list_assemblies"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "REL-S-03",
        "Which exact loci define the recorded association between {HOST_LINEAGE_A} and {VIRAL_LINEAGE_A}, separated by represented source species, assembly, Transferred gene, and Integrated virus?",
        "source_viral_lineage_association",
        _structured_outputs("exact_assembly_set", "exact_locus_set", "exact_source_species_set"),
        _structured_caps("source_taxonomy_projection", "list_loci"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "REL-S-04",
        "Which represented source species, assemblies, loci, Transferred gene records, and Integrated virus records are associated with {VIRAL_LINEAGE_A} within {HOST_LINEAGE_A}, and which are associated with {VIRAL_LINEAGE_B}?",
        "source_viral_lineage_association",
        _structured_outputs(
            "exact_assembly_set",
            "exact_locus_set",
            "exact_source_species_set",
            "exact_viral_lineage_set",
        ),
        _structured_caps(
            "composite_structured_plan",
            "source_taxonomy_projection",
            "list_assemblies",
            "list_loci",
            "multi_result_structured_envelope",
        ),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "REL-L-01",
        "Which species within {HOST_LINEAGE_A} does the permitted literature associate with {VIRAL_LINEAGE_A} through Transferred gene records, and which through Integrated virus records?",
        "source_viral_lineage_association",
        _literature_outputs(),
        _lit_caps(),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "REL-L-02",
        "For species within {HOST_LINEAGE_A} associated with {VIRAL_LINEAGE_A}, which assemblies does the permitted literature link to Transferred gene records and which to Integrated virus records?",
        "source_viral_lineage_association",
        _literature_outputs(),
        _lit_caps("literature_entity_discoverability"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "REL-L-03",
        "Which named loci or source regions does the permitted literature associate with {HOST_LINEAGE_A} and {VIRAL_LINEAGE_A}, separated into Transferred gene and Integrated virus records?",
        "source_viral_lineage_association",
        _literature_outputs(),
        _lit_caps("literature_entity_discoverability"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "REL-L-04",
        "Which literature-reported host species, assemblies, regions, Transferred gene records, and Integrated virus records are associated with {VIRAL_LINEAGE_A} within {HOST_LINEAGE_A}, and which are associated with {VIRAL_LINEAGE_B}?",
        "source_viral_lineage_association",
        _literature_outputs(),
        _lit_caps("literature_entity_discoverability"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "REL-H-01",
        "Which release-represented source species within {HOST_LINEAGE_A} are also reported in the permitted literature with Transferred gene associations to {VIRAL_LINEAGE_A}, and which with Integrated virus associations?",
        "source_viral_lineage_association",
        _hybrid_outputs("exact_source_species_set"),
        _hybrid_caps("source_taxonomy_projection", "list_source_taxa"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "REL-H-02",
        "For release-represented source species within {HOST_LINEAGE_A} associated with {VIRAL_LINEAGE_A}, which assemblies have Transferred gene associations in both sources and which have Integrated virus associations in both?",
        "source_viral_lineage_association",
        _hybrid_outputs("exact_assembly_set", "exact_source_species_set"),
        _hybrid_caps("source_taxonomy_projection", "list_assemblies"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "REL-H-03",
        "Which loci link {HOST_LINEAGE_A} to {VIRAL_LINEAGE_A} in structured records and permitted literature, separated by represented source species, assembly, Transferred gene, and Integrated virus?",
        "source_viral_lineage_association",
        _hybrid_outputs("exact_assembly_set", "exact_locus_set", "exact_source_species_set"),
        _hybrid_caps("source_taxonomy_projection", "list_loci"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "REL-H-04",
        "Which represented source species, assemblies, loci, Transferred gene records, and Integrated virus records occur across the structured and literature sources for {VIRAL_LINEAGE_A} within {HOST_LINEAGE_A}, and which occur for {VIRAL_LINEAGE_B}?",
        "source_viral_lineage_association",
        _hybrid_outputs(
            "exact_assembly_set",
            "exact_locus_set",
            "exact_source_species_set",
            "exact_viral_lineage_set",
        ),
        _hybrid_caps(
            "composite_structured_plan",
            "source_taxonomy_projection",
            "list_assemblies",
            "list_loci",
            "multi_result_structured_envelope",
        ),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "RECORD-S-01",
        "Which loci in assembly {ASSEMBLY_A} are classified as Transferred gene and which are classified as Integrated virus, grouped by viral lineage?",
        "assembly_locus_association",
        _structured_outputs("exact_locus_set", "exact_viral_lineage_set"),
        _structured_caps("list_loci", "complete_paginated_relation_projection"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "RECORD-S-02",
        "Which represented source species, assembly, locus identity, and viral lineage are recorded for locus {LOCUS_A}, including whether its relation class is Transferred gene or Integrated virus?",
        "assembly_locus_association",
        _structured_outputs(
            "exact_assembly_set",
            "exact_locus_set",
            "exact_source_species_set",
            "exact_viral_lineage_set",
        ),
        _structured_caps("locus_detail"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "RECORD-S-03",
        "Which loci in assembly {ASSEMBLY_A} are assigned to {VIRAL_LINEAGE_A} as Transferred gene records and which as Integrated virus records?",
        "assembly_locus_association",
        _structured_outputs("exact_locus_set"),
        _structured_caps("list_loci"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "RECORD-S-04",
        "Which represented source species, assemblies, relation classes, and viral lineages are recorded for {LOCUS_A}, {LOCUS_B}, and {LOCUS_C}, separating Transferred gene from Integrated virus?",
        "assembly_locus_association",
        _structured_outputs(
            "exact_assembly_set",
            "exact_locus_set",
            "exact_source_species_set",
            "exact_viral_lineage_set",
        ),
        _structured_caps(
            "composite_structured_plan",
            "locus_detail",
            "multi_result_structured_envelope",
        ),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "RECORD-L-01",
        "Which regions in assembly {ASSEMBLY_A} does the permitted literature report as Transferred gene, and which does it report as Integrated virus, grouped by viral lineage?",
        "assembly_locus_association",
        _literature_outputs(),
        _lit_caps("literature_entity_discoverability"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "RECORD-L-02",
        "Which host species and viral lineages does the permitted literature associate with named regions in assembly {ASSEMBLY_A}, separated into Transferred gene and Integrated virus records?",
        "assembly_locus_association",
        _literature_outputs(),
        _lit_caps("literature_entity_discoverability"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "RECORD-L-03",
        "Which named regions in {HOST_SPECIES_A} does the permitted literature report as Transferred gene and which as Integrated virus, grouped by assembly and viral lineage?",
        "assembly_locus_association",
        _literature_outputs(),
        _lit_caps("literature_entity_discoverability"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "RECORD-L-04",
        "Which regions in assembly {ASSEMBLY_A} does the permitted literature associate with {VIRAL_LINEAGE_A} as Transferred gene records and which as Integrated virus records?",
        "assembly_locus_association",
        _literature_outputs(),
        _lit_caps("literature_entity_discoverability"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "RECORD-H-01",
        "Which source-species, assembly, relation-class, and viral-lineage association for locus {LOCUS_A} is present in both sources, including whether the class is Transferred gene or Integrated virus?",
        "assembly_locus_association",
        _hybrid_outputs(
            "exact_assembly_set",
            "exact_locus_set",
            "exact_source_species_set",
            "exact_viral_lineage_set",
        ),
        _hybrid_caps("locus_detail"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "RECORD-H-02",
        "Which locus-level associations in assembly {ASSEMBLY_A} are present in both sources, separated into Transferred gene and Integrated virus records and grouped by viral lineage?",
        "assembly_locus_association",
        _hybrid_outputs("exact_assembly_set", "exact_locus_set", "exact_viral_lineage_set"),
        _hybrid_caps("list_loci", "complete_paginated_relation_projection"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "RECORD-H-03",
        "Which Transferred gene and Integrated virus associations in assembly {ASSEMBLY_A} are structured-only, literature-only, or present in both, grouped by locus and viral lineage?",
        "assembly_locus_association",
        _hybrid_outputs("exact_assembly_set", "exact_locus_set", "exact_viral_lineage_set"),
        _hybrid_caps("list_loci", "complete_paginated_relation_projection"),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "RECORD-H-04",
        "Which represented source species, loci, viral lineages, Transferred gene records, and Integrated virus records are associated with assembly {ASSEMBLY_A}, and which are associated with assembly {ASSEMBLY_B}, with cross-source presence retained?",
        "assembly_locus_association",
        _hybrid_outputs(
            "exact_assembly_set",
            "exact_locus_set",
            "exact_source_species_set",
            "exact_viral_lineage_set",
        ),
        _hybrid_caps(
            "composite_structured_plan",
            "list_loci",
            "complete_paginated_relation_projection",
            "multi_result_structured_envelope",
        ),
        "requires_relation_contract",
    ),
    _TemplateSpec(
        "UNSUP-01",
        "Which host taxonomic unit has the highest prevalence of {VIRAL_LINEAGE_A}-related records?",
        "unsupported_association_boundary",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("explicit_unsupported_boundary", "refusal_before_downstream_execution"),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-02",
        "Which species definitely has no association with {VIRAL_LINEAGE_A}?",
        "unsupported_association_boundary",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("explicit_unsupported_boundary", "refusal_before_downstream_execution"),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-03",
        "Which modern host species are currently infected by {VIRAL_LINEAGE_A} because an EVE association is recorded?",
        "unsupported_association_boundary",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("explicit_unsupported_boundary", "refusal_before_downstream_execution"),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-04",
        "Which exact independent integration event is represented by each recorded EVE locus?",
        "unsupported_association_boundary",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("explicit_unsupported_boundary", "refusal_before_downstream_execution"),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-05",
        "Which pairs of host and viral lineages have co-diverged because matching EVE associations are recorded?",
        "unsupported_association_boundary",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("explicit_unsupported_boundary", "refusal_before_downstream_execution"),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-06",
        "Classify every record as either Transferred gene or Integrated virus even though neither relation class has been approved.",
        "unsupported_association_boundary",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("explicit_unsupported_boundary", "refusal_before_downstream_execution"),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-07",
        "Treat every Integration source label as Integrated virus and every Viral contig source label as Transferred gene, then list the resulting host associations.",
        "unsupported_association_boundary",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("explicit_unsupported_boundary", "refusal_before_downstream_execution"),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-08",
        "Treat every HCVR source label as Transferred gene or Integrated virus, then list the resulting host associations.",
        "unsupported_association_boundary",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("explicit_unsupported_boundary", "refusal_before_downstream_execution"),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-09",
        "Merge study-defined, formal, and extended viral-lineage roles into one lineage and report one combined host association set.",
        "unsupported_association_boundary",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("explicit_unsupported_boundary", "refusal_before_downstream_execution"),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-10",
        "Assign locus {LOCUS_A} to {VIRAL_LINEAGE_A} from name similarity alone.",
        "unsupported_association_boundary",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("explicit_unsupported_boundary", "refusal_before_downstream_execution"),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-11",
        "Because {HOST_SPECIES_A} has an association with {VIRAL_LINEAGE_A}, report the same association for every species within {HOST_LINEAGE_A}.",
        "unsupported_association_boundary",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("explicit_unsupported_boundary", "refusal_before_downstream_execution"),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-12",
        "Merge host-virus associations from unapproved or unversioned releases and corpora into the selected release.",
        "unsupported_association_boundary",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("explicit_unsupported_boundary", "refusal_before_downstream_execution"),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-13",
        "Treat the first page or a truncated result as the complete host-species, assembly, and locus association set.",
        "unsupported_association_boundary",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("explicit_unsupported_boundary", "refusal_before_downstream_execution"),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-14",
        "Search the live web for additional host-virus associations outside the approved corpus.",
        "unsupported_operational_boundary",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("explicit_unsupported_boundary", "refusal_before_downstream_execution"),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-15",
        "Run BLAST or HMMER on a new sequence and add the inferred host-virus association to the selected release.",
        "unsupported_operational_boundary",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("explicit_unsupported_boundary", "refusal_before_downstream_execution"),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-16",
        "Execute an arbitrary SQL query across all database tables to construct a new host-virus association.",
        "unsupported_operational_boundary",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("explicit_unsupported_boundary", "refusal_before_downstream_execution"),
        "unsupported_by_design",
    ),
)


def _family_for(template_id: str) -> ScientificQuestionFamily:
    if template_id.startswith("UNSUP-"):
        return "unsupported"
    marker = template_id.split("-")[1]
    return {"S": "structured", "L": "literature", "H": "hybrid"}[marker]  # type: ignore[return-value]


def _task_for(template_id: str) -> ScientificTask:
    prefix = template_id.split("-")[0]
    return {
        "HOST": "source_taxon_association",
        "VIRUS": "viral_lineage_association",
        "REL": "source_viral_lineage_association",
        "RECORD": "assembly_locus_association",
        "UNSUP": "unsupported_scientific_or_operational_boundary",
    }[prefix]  # type: ignore[return-value]


def _template_payload(spec: _TemplateSpec) -> dict[str, object]:
    slots = tuple(sorted(set(_PLACEHOLDER_RE.findall(spec.question_text_template))))
    family = _family_for(spec.template_id)
    default_note = {
        "structured": _STRUCTURED_NOTE,
        "literature": _LITERATURE_NOTE,
        "hybrid": _HYBRID_NOTE,
        "unsupported": _UNSUPPORTED_NOTE,
    }[family]
    return {
        "template_schema_version": "rag-value-scientific-question-template-v1",
        "template_id": spec.template_id,
        "family": family,
        "scientific_task": _task_for(spec.template_id),
        "scientific_intent": spec.scientific_intent,
        "question_text_template": spec.question_text_template,
        "entity_slots": slots,
        "expected_output_types": tuple(sorted(spec.expected_output_types)),
        "required_capabilities": tuple(sorted(spec.required_capabilities)),
        "capability_status": spec.capability_status,
        "review_status": "pending",
        "gold": None,
        "authoring_notes": spec.authoring_notes or default_note,
    }


_PREREGISTERED_METADATA = {
    spec.template_id: (
        _family_for(spec.template_id),
        _task_for(spec.template_id),
        spec.scientific_intent,
    )
    for spec in _SPECS
}
_PREREGISTERED_RECORD_SHA256 = {
    spec.template_id: canonical_json_sha256(_template_payload(spec)) for spec in _SPECS
}


def _build_template(spec: _TemplateSpec) -> ScientificQuestionTemplate:
    payload = _template_payload(spec)
    return ScientificQuestionTemplate.model_validate(
        {**payload, "record_sha256": canonical_json_sha256(payload)}
    )


def build_scientific_question_templates() -> tuple[ScientificQuestionTemplate, ...]:
    """Build the exact 64 pending templates in preregistered scientific-task order."""

    templates = tuple(_build_template(spec) for spec in _SPECS)
    validate_scientific_question_templates(templates)
    return templates


def validate_scientific_question_templates(
    templates: Sequence[ScientificQuestionTemplate],
) -> None:
    """Validate set-level balance, identity, status, and natural-language invariants."""

    values = tuple(templates)
    if len(values) != 64:
        raise ScientificTemplateSetError("scientific benchmark requires exactly 64 templates")
    ids = tuple(value.template_id for value in values)
    if len(ids) != len(set(ids)):
        raise ScientificTemplateSetError("scientific template IDs must be unique")
    normalized_text = tuple(
        " ".join(value.question_text_template.split()).casefold() for value in values
    )
    if len(normalized_text) != len(set(normalized_text)):
        raise ScientificTemplateSetError("scientific question text must be unique")
    if Counter(value.family for value in values) != Counter(
        {"structured": 16, "literature": 16, "hybrid": 16, "unsupported": 16}
    ):
        raise ScientificTemplateSetError("scientific families must contain 16 templates each")
    expected_tasks = {
        "source_taxon_association": 12,
        "viral_lineage_association": 12,
        "source_viral_lineage_association": 12,
        "assembly_locus_association": 12,
        "unsupported_scientific_or_operational_boundary": 16,
    }
    if Counter(value.scientific_task for value in values) != Counter(expected_tasks):
        raise ScientificTemplateSetError("scientific-task counts do not match preregistration")
    if any(value.review_status != "pending" or value.gold is not None for value in values):
        raise ScientificTemplateSetError("scientific templates must remain pending without Gold")
    if ids != tuple(spec.template_id for spec in _SPECS):
        raise ScientificTemplateSetError("scientific templates are not in preregistered order")
    if tuple(value.record_sha256 for value in values) != tuple(
        _PREREGISTERED_RECORD_SHA256[spec.template_id] for spec in _SPECS
    ):
        raise ScientificTemplateSetError("scientific template content differs from preregistration")


def scientific_questions_template_bytes() -> bytes:
    """Serialize canonical JSONL without approving or binding a question."""

    return b"".join(
        canonical_json_bytes(template) + b"\n" for template in build_scientific_question_templates()
    )


def _build_binding(
    entity_slot: EntitySlot,
    required_entity_type: RequiredEntityType,
) -> ScientificEntityBindingTemplate:
    payload: dict[str, object] = {
        "binding_schema_version": "rag-value-scientific-entity-binding-template-v1",
        "entity_slot": entity_slot,
        "required_entity_type": required_entity_type,
        "selected_stable_key": None,
        "selected_display_name": None,
        "release_key": None,
        "release_manifest_sha256": None,
        "selected_snapshot_key": None,
        "selected_lineage_role": None,
        "include_descendants": None,
        "review_status": "pending",
    }
    return ScientificEntityBindingTemplate.model_validate(
        {**payload, "record_sha256": canonical_json_sha256(payload)}
    )


def build_scientific_entity_bindings_template() -> ScientificEntityBindingsTemplate:
    """Build an empty, checksum-bound worksheet for every approved slot vocabulary item."""

    bindings = tuple(_build_binding(slot, _ENTITY_TYPES[slot]) for slot in sorted(_ENTITY_TYPES))
    payload: dict[str, object] = {
        "manifest_schema_version": "rag-value-scientific-entity-bindings-template-v1",
        "binding_count": 11,
        "bindings": bindings,
    }
    return ScientificEntityBindingsTemplate.model_validate(
        {**payload, "manifest_sha256": canonical_json_sha256(payload)}
    )


def scientific_entity_bindings_template_bytes() -> bytes:
    """Serialize the empty binding worksheet as canonical JSON."""

    return canonical_json_bytes(build_scientific_entity_bindings_template()) + b"\n"


def _self_sha256(value: StrictFrozenSchema, field_name: str) -> str:
    payload = value.model_dump(mode="python")
    del payload[field_name]
    return canonical_json_sha256(payload)


__all__ = [
    "CapabilityStatus",
    "EntitySlot",
    "ExpectedOutputType",
    "RequiredCapability",
    "RequiredEntityType",
    "ScientificEntityBindingTemplate",
    "ScientificEntityBindingsTemplate",
    "ScientificIntent",
    "ScientificQuestionFamily",
    "ScientificQuestionTemplate",
    "ScientificTask",
    "ScientificTemplateSetError",
    "build_scientific_entity_bindings_template",
    "build_scientific_question_templates",
    "scientific_entity_bindings_template_bytes",
    "scientific_questions_template_bytes",
    "validate_scientific_question_templates",
]
