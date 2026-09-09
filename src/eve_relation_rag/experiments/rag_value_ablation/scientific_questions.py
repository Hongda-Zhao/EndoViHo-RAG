# ruff: noqa: E501 - preregistered question literals intentionally remain on one line.
"""Authoring-only scientific questions for the v1 association contract.

The answerable templates use only the four supported dimensions: assembly-source taxon,
EVE locus or reported viral region, viral-lineage affinity, and evidence/source. They do not
require or permit a ``Transferred gene`` versus ``Integrated virus`` classification.
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
    "ASSEMBLY_A",
    "ASSEMBLY_B",
    "ASSEMBLY_SOURCE_TAXON_A",
    "EVE_LOCUS_A",
    "EVE_LOCUS_B",
    "EVE_LOCUS_C",
    "REPORTED_REGION_A",
    "SOURCE_TAXON_LINEAGE_A",
    "VIRAL_LINEAGE_A",
    "VIRAL_LINEAGE_B",
]
type RequiredEntityType = Literal[
    "assembly",
    "assembly_source_taxon",
    "eve_locus",
    "reported_viral_region",
    "source_lineage",
    "viral_lineage",
]
type ExpectedOutputType = Literal[
    "cross_source_association_set",
    "exact_association_set",
    "source_reported_association_set",
    "exact_locus_set",
    "exact_assembly_set",
    "exact_source_taxon_set",
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
    "evidence_source_preservation",
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
    "locus_region_identity_preservation",
    "multi_result_structured_envelope",
    "natural_hybrid_decomposition",
    "natural_literature_routing",
    "natural_structured_planning",
    "refusal_before_downstream_execution",
    "release_represented_source_scope",
    "source_annotation_preservation",
    "structured_anchor_resolution",
]
type CapabilityStatus = Literal[
    "supported_now",
    "requires_v1_association_projection",
    "unsupported_by_design",
]

_PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")
_FAKE_ALL_A_LOCUS = (
    "locus:eve:v1:sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
)
_MECHANICAL_HYBRID_PHRASE = ". and explain the literature"
_EXPLANATORY_QUESTION_RE = re.compile(
    r"(?:\bhow\b|\bwhy\b|\bmethods?\b|\blimitations?\b|"
    r"\buncertaint(?:y|ies)\b|\binterpret(?:ation|ed|ing)\b)",
    re.IGNORECASE,
)
_PROHIBITED_RELATION_CLASSES = ("Transferred gene", "Integrated virus")
_ENTITY_TYPES: dict[EntitySlot, RequiredEntityType] = {
    "ASSEMBLY_A": "assembly",
    "ASSEMBLY_B": "assembly",
    "ASSEMBLY_SOURCE_TAXON_A": "assembly_source_taxon",
    "EVE_LOCUS_A": "eve_locus",
    "EVE_LOCUS_B": "eve_locus",
    "EVE_LOCUS_C": "eve_locus",
    "REPORTED_REGION_A": "reported_viral_region",
    "SOURCE_TAXON_LINEAGE_A": "source_lineage",
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
        "evidence_source_preservation",
        "explicit_unsupported_boundary",
        "source_taxonomy_projection",
        "lineage_role_and_scope_preservation",
        "literature_association_extraction",
        "literature_entity_normalization",
        "locus_region_identity_preservation",
        "multi_result_structured_envelope",
        "natural_hybrid_decomposition",
        "natural_literature_routing",
        "natural_structured_planning",
        "release_represented_source_scope",
        "source_annotation_preservation",
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
        if (self.family, self.scientific_task, self.scientific_intent) != expected_metadata:
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
            self._validate_answerable_question()
        elif self.capability_status != "unsupported_by_design":
            raise ValueError("unsupported questions must be marked unsupported_by_design")
        if self.family != "unsupported" and self.capability_status == "unsupported_by_design":
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

    def _validate_answerable_question(self) -> None:
        normalized_question = self.question_text_template.casefold()
        if _EXPLANATORY_QUESTION_RE.search(self.question_text_template):
            raise ValueError("answerable association questions cannot request explanations")
        if any(label in self.question_text_template for label in _PROHIBITED_RELATION_CLASSES):
            raise ValueError("v1 answerable questions cannot require an unsupported relation class")
        if not any(
            marker in normalized_question
            for marker in ("eve locus", "eve loci", "eve-locus", "viral region", "viral-region")
        ):
            raise ValueError("answerable questions must retain the locus-or-region dimension")
        if "viral-lineage" not in normalized_question and "{viral_lineage_" not in normalized_question:
            raise ValueError("answerable questions must retain the viral-lineage-affinity dimension")
        if "evidence" not in normalized_question:
            raise ValueError("answerable questions must retain evidence/source provenance")
        required_capabilities = {
            "association_projection",
            "evidence_source_preservation",
            "lineage_role_and_scope_preservation",
            "locus_region_identity_preservation",
            "source_annotation_preservation",
        }
        if not required_capabilities.issubset(self.required_capabilities):
            raise ValueError("answerable questions must declare every v1 association boundary")
        if "does not require a Transferred gene or Integrated virus classification" not in self.authoring_notes:
            raise ValueError("answerable question note must omit unsupported relation classes")
        if "HCVR, VR Type, and Viral Major Taxon" not in self.authoring_notes:
            raise ValueError("answerable question note must preserve source annotations")
        outputs = set(self.expected_output_types)
        if not {"forbidden_claims", "required_limitations"}.issubset(outputs):
            raise ValueError("answerable questions must retain safety-scoring outputs")
        capabilities = set(self.required_capabilities)
        existing_query_primitives = {"list_assemblies", "list_loci", "list_source_taxa", "locus_detail"}
        if self.family in {"structured", "hybrid"} and any(
            marker in normalized_question for marker in ("host species", "host-species", "host taxonomic")
        ):
            raise ValueError("structured wording must preserve assembly-source semantics")
        if self.family in {"structured", "hybrid"} and not capabilities.intersection(existing_query_primitives):
            raise ValueError("structured evidence must reuse an existing query primitive")
        if self.family == "literature" and capabilities.intersection(existing_query_primitives):
            raise ValueError("literature-only questions cannot require structured queries")
        if self.family == "structured":
            if outputs & {"source_reported_association_set", "cross_source_association_set"}:
                raise ValueError("structured questions cannot contain literature association sets")
            if "release_represented_source_scope" not in capabilities:
                raise ValueError("structured questions require exact release source scope")
            if "assembly-source taxon is not an ancient or modern host" not in self.authoring_notes:
                raise ValueError("structured question note must preserve source-taxon semantics")
        elif self.family == "literature":
            if "source_reported_association_set" not in outputs or any(
                output.startswith("exact_") for output in outputs
            ) or "cross_source_association_set" in outputs:
                raise ValueError("literature questions require only source-reported associations")
            if "corpus_source_reported_scope" not in capabilities:
                raise ValueError("literature questions require permitted-corpus source scope")
            if "permitted-corpus source-reported taxon wording and provenance" not in self.authoring_notes:
                raise ValueError("literature question note must preserve source wording")
        else:
            if not {"cross_source_association_set", "exact_association_set", "source_reported_association_set"}.issubset(outputs):
                raise ValueError("hybrid questions must preserve all three association sets")
            if not {"corpus_source_reported_scope", "release_represented_source_scope"}.issubset(capabilities):
                raise ValueError("hybrid questions require separate release and corpus scopes")
            if "must not overwrite structured values" not in self.authoring_notes:
                raise ValueError("hybrid question note must separate source-reported values")
        if self.capability_status != "requires_v1_association_projection":
            raise ValueError("answerable questions require the v1 association projection")


class ScientificEntityBindingTemplate(StrictFrozenSchema):
    """One empty slot awaiting release- or corpus-scoped human selection."""

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
    """Canonical empty binding worksheet covering the complete v1 slot vocabulary."""

    manifest_schema_version: Literal["rag-value-scientific-entity-bindings-template-v1"] = (
        "rag-value-scientific-entity-bindings-template-v1"
    )
    binding_count: Literal[10] = 10
    bindings: tuple[ScientificEntityBindingTemplate, ...] = Field(min_length=10, max_length=10)
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
    """Raised when the complete scientific template set violates preregistration."""


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
    "Pending v1 association-only authoring template. This version does not require a Transferred "
    "gene or Integrated virus classification. Preserve HCVR, VR Type, and Viral Major Taxon as "
    "source-native annotations without mapping them to a relation class. Preserve EVE-locus or "
    "reported-viral-region identity, every viral-lineage affinity role and scope, and evidence/source provenance. "
)
_STRUCTURED_NOTE = _COMMON_PENDING_NOTE + (
    "Enumerate only assembly-source taxa, assemblies, and EVE loci represented in the exact selected release. "
    "An assembly-source taxon is not an ancient or modern host. Bind every entity slot and obtain independent human review."
)
_LITERATURE_NOTE = _COMMON_PENDING_NOTE + (
    "Preserve permitted-corpus source-reported taxon wording and provenance. Literature associations do not assert DatasetRelease membership. "
    "Bind every entity slot and obtain independent human review."
)
_HYBRID_NOTE = _COMMON_PENDING_NOTE + (
    "Keep exact-release assembly-source taxa and EVE loci separate from permitted-corpus reported regions. "
    "An assembly-source taxon is not an ancient or modern host, and source-reported values must not overwrite structured values. "
    "Bind every entity slot and obtain independent human review."
)
_UNSUPPORTED_NOTE = (
    "Pending association-boundary template only. Human review must define the refusal category and prohibited downstream stages. "
    "No answer, evidence, Gold, or approval is supplied. HCVR, Integration, Viral contig, VR Type, and Viral Major Taxon must not be mapped to Transferred gene or Integrated virus."
)
_COMMON_ASSOCIATION_CAPABILITIES: tuple[RequiredCapability, ...] = (
    "association_projection",
    "evidence_source_preservation",
    "lineage_role_and_scope_preservation",
    "locus_region_identity_preservation",
    "source_annotation_preservation",
)


def _structured_caps(*extra: RequiredCapability) -> tuple[RequiredCapability, ...]:
    return (*_COMMON_ASSOCIATION_CAPABILITIES, "natural_structured_planning", "release_represented_source_scope", *extra)


def _lit_caps(*extra: RequiredCapability) -> tuple[RequiredCapability, ...]:
    return (*_COMMON_ASSOCIATION_CAPABILITIES, "corpus_source_reported_scope", "literature_association_extraction", "literature_entity_normalization", "literature_retrieval", "natural_literature_routing", *extra)


def _hybrid_caps(*structured: RequiredCapability) -> tuple[RequiredCapability, ...]:
    return (*_COMMON_ASSOCIATION_CAPABILITIES, "corpus_source_reported_scope", "cross_source_association_alignment", "literature_association_extraction", "literature_entity_normalization", "literature_retrieval", "natural_hybrid_decomposition", "release_represented_source_scope", "structured_anchor_resolution", *structured)


def _structured_outputs(*extra: ExpectedOutputType) -> tuple[ExpectedOutputType, ...]:
    return ("exact_association_set", "forbidden_claims", "required_limitations", *extra)


def _literature_outputs() -> tuple[ExpectedOutputType, ...]:
    return ("forbidden_claims", "required_documents", "required_evidence_groups", "required_limitations", "source_reported_association_set")


def _hybrid_outputs(*extra: ExpectedOutputType) -> tuple[ExpectedOutputType, ...]:
    return ("cross_source_association_set", "exact_association_set", "forbidden_claims", "required_documents", "required_evidence_groups", "required_limitations", "source_reported_association_set", *extra)


_STATUS: CapabilityStatus = "requires_v1_association_projection"
_REFUSAL_OUTPUTS: tuple[ExpectedOutputType, ...] = ("forbidden_claims", "prohibited_downstream_stages", "refusal_category")
_REFUSAL_CAPS: tuple[RequiredCapability, ...] = ("explicit_unsupported_boundary", "refusal_before_downstream_execution")

_SPECS: tuple[_TemplateSpec, ...] = (
    _TemplateSpec("HOST-S-01", "Which EVE loci are recorded for assembly-source taxa within {SOURCE_TAXON_LINEAGE_A}, grouped by viral-lineage affinity and evidence source in the selected release?", "source_taxon_association", _structured_outputs("exact_locus_set", "exact_source_taxon_set"), _structured_caps("source_taxonomy_projection", "list_loci", "list_source_taxa"), _STATUS),
    _TemplateSpec("HOST-S-02", "For each represented assembly-source taxon within {SOURCE_TAXON_LINEAGE_A}, which assemblies and EVE loci occur, grouped by viral-lineage affinity and evidence source?", "source_taxon_association", _structured_outputs("exact_assembly_set", "exact_locus_set", "exact_source_taxon_set"), _structured_caps("source_taxonomy_projection", "list_assemblies", "list_loci"), _STATUS),
    _TemplateSpec("HOST-S-03", "Which viral-lineage affinities occur across EVE loci from assembly-source taxa within {SOURCE_TAXON_LINEAGE_A}, and which source-record evidence identifies each association?", "source_taxon_association", _structured_outputs("exact_source_taxon_set", "exact_viral_lineage_set"), _structured_caps("source_taxonomy_projection", "list_loci"), _STATUS),
    _TemplateSpec("HOST-S-04", "Which exact association tuples link assembly-source taxa within {SOURCE_TAXON_LINEAGE_A}, their assemblies and EVE loci, viral-lineage affinities, and evidence sources?", "source_taxon_association", _structured_outputs("exact_assembly_set", "exact_locus_set", "exact_source_taxon_set", "exact_viral_lineage_set"), _structured_caps("composite_structured_plan", "source_taxonomy_projection", "list_assemblies", "list_loci", "complete_paginated_relation_projection", "multi_result_structured_envelope"), _STATUS),
    _TemplateSpec("HOST-L-01", "Which source-reported taxa within {SOURCE_TAXON_LINEAGE_A} are linked to reported viral regions, grouped by viral-lineage affinity and literature evidence source?", "source_taxon_association", _literature_outputs(), _lit_caps(), _STATUS),
    _TemplateSpec("HOST-L-02", "For source-reported taxa within {SOURCE_TAXON_LINEAGE_A}, which assemblies or reported viral regions are named, grouped by viral-lineage affinity and evidence source?", "source_taxon_association", _literature_outputs(), _lit_caps("literature_entity_discoverability"), _STATUS),
    _TemplateSpec("HOST-L-03", "Which viral-lineage affinities does the permitted literature report for viral regions from taxa within {SOURCE_TAXON_LINEAGE_A}, and which evidence groups support each report?", "source_taxon_association", _literature_outputs(), _lit_caps(), _STATUS),
    _TemplateSpec("HOST-L-04", "Which source-reported association tuples link taxa within {SOURCE_TAXON_LINEAGE_A}, named viral regions, viral-lineage affinities, and literature evidence sources?", "source_taxon_association", _literature_outputs(), _lit_caps("literature_entity_discoverability"), _STATUS),
    _TemplateSpec("HOST-H-01", "Which assembly-source taxa within {SOURCE_TAXON_LINEAGE_A} have EVE-locus associations that align with reported viral regions in the permitted literature, grouped by viral-lineage affinity and evidence source?", "source_taxon_association", _hybrid_outputs("exact_locus_set", "exact_source_taxon_set"), _hybrid_caps("source_taxonomy_projection", "list_loci", "list_source_taxa"), _STATUS),
    _TemplateSpec("HOST-H-02", "For assembly-source taxa within {SOURCE_TAXON_LINEAGE_A}, which assemblies, EVE loci, and reported viral regions align across sources, grouped by viral-lineage affinity and evidence source?", "source_taxon_association", _hybrid_outputs("exact_assembly_set", "exact_locus_set", "exact_source_taxon_set"), _hybrid_caps("source_taxonomy_projection", "list_assemblies", "list_loci"), _STATUS),
    _TemplateSpec("HOST-H-03", "Which viral-lineage affinities are shared or source-specific across EVE loci and reported viral regions for taxa within {SOURCE_TAXON_LINEAGE_A}, with evidence provenance retained?", "source_taxon_association", _hybrid_outputs("exact_source_taxon_set", "exact_viral_lineage_set"), _hybrid_caps("complete_paginated_relation_projection", "list_loci", "source_taxonomy_projection"), _STATUS),
    _TemplateSpec("HOST-H-04", "Which taxon, EVE-locus or reported-viral-region, viral-lineage-affinity, and evidence-source tuples are structured-only, literature-only, or present in both within {SOURCE_TAXON_LINEAGE_A}?", "source_taxon_association", _hybrid_outputs("exact_assembly_set", "exact_locus_set", "exact_source_taxon_set", "exact_viral_lineage_set"), _hybrid_caps("composite_structured_plan", "source_taxonomy_projection", "list_assemblies", "list_loci", "complete_paginated_relation_projection", "multi_result_structured_envelope"), _STATUS),

    _TemplateSpec("VIRUS-S-01", "Which represented assembly-source taxa have EVE loci assigned an affinity to {VIRAL_LINEAGE_A}, and what evidence source supports each association?", "viral_lineage_association", _structured_outputs("exact_source_taxon_set"), _structured_caps("source_taxonomy_projection", "list_source_taxa"), _STATUS),
    _TemplateSpec("VIRUS-S-02", "Which assemblies and EVE loci are associated with viral-lineage affinity {VIRAL_LINEAGE_A}, grouped by assembly-source taxon and evidence source?", "viral_lineage_association", _structured_outputs("exact_assembly_set", "exact_locus_set"), _structured_caps("list_assemblies", "list_loci"), _STATUS),
    _TemplateSpec("VIRUS-S-03", "For each represented assembly-source taxon associated with viral-lineage affinity {VIRAL_LINEAGE_A}, which EVE loci and source-record evidence are recorded?", "viral_lineage_association", _structured_outputs("exact_locus_set", "exact_source_taxon_set"), _structured_caps("source_taxonomy_projection", "list_loci"), _STATUS),
    _TemplateSpec("VIRUS-S-04", "Which exact assembly-source-taxon, EVE-locus, viral-lineage-affinity {VIRAL_LINEAGE_A}, and evidence-source tuples occur in the selected release?", "viral_lineage_association", _structured_outputs("exact_assembly_set", "exact_locus_set", "exact_source_taxon_set"), _structured_caps("composite_structured_plan", "source_taxonomy_projection", "list_assemblies", "list_loci", "multi_result_structured_envelope"), _STATUS),
    _TemplateSpec("VIRUS-L-01", "Which source-reported taxa are linked to viral regions with affinity to {VIRAL_LINEAGE_A}, and which literature evidence source supports each report?", "viral_lineage_association", _literature_outputs(), _lit_caps(), _STATUS),
    _TemplateSpec("VIRUS-L-02", "Which named viral regions does the permitted literature associate with viral-lineage affinity {VIRAL_LINEAGE_A}, grouped by source-reported taxon and evidence source?", "viral_lineage_association", _literature_outputs(), _lit_caps("literature_entity_discoverability"), _STATUS),
    _TemplateSpec("VIRUS-L-03", "For source-reported taxa associated with viral-lineage affinity {VIRAL_LINEAGE_A}, which assemblies or viral regions and evidence groups are named?", "viral_lineage_association", _literature_outputs(), _lit_caps("literature_entity_discoverability"), _STATUS),
    _TemplateSpec("VIRUS-L-04", "Which source-reported taxon, viral-region, viral-lineage-affinity {VIRAL_LINEAGE_A}, and evidence-source tuples occur in the permitted literature?", "viral_lineage_association", _literature_outputs(), _lit_caps("literature_entity_discoverability"), _STATUS),
    _TemplateSpec("VIRUS-H-01", "Which assembly-source taxa have EVE loci with affinity to {VIRAL_LINEAGE_A} that align to reported viral regions, with each source's evidence retained?", "viral_lineage_association", _hybrid_outputs("exact_source_taxon_set"), _hybrid_caps("source_taxonomy_projection", "list_source_taxa", "list_loci"), _STATUS),
    _TemplateSpec("VIRUS-H-02", "Which EVE loci and reported viral regions associated with viral-lineage affinity {VIRAL_LINEAGE_A} are structured-only, literature-only, or present in both, grouped by taxon and evidence source?", "viral_lineage_association", _hybrid_outputs("exact_locus_set"), _hybrid_caps("list_loci"), _STATUS),
    _TemplateSpec("VIRUS-H-03", "For assembly-source taxa associated with viral-lineage affinity {VIRAL_LINEAGE_A}, which assemblies, EVE loci, reported viral regions, and evidence sources align?", "viral_lineage_association", _hybrid_outputs("exact_assembly_set", "exact_locus_set", "exact_source_taxon_set"), _hybrid_caps("source_taxonomy_projection", "list_assemblies", "list_loci"), _STATUS),
    _TemplateSpec("VIRUS-H-04", "Which exact-release EVE loci assigned affinity to {VIRAL_LINEAGE_A} have human-reviewed matches to literature-reported viral regions, and what evidence source supports each side?", "viral_lineage_association", _hybrid_outputs("exact_locus_set"), _hybrid_caps("list_loci"), _STATUS),

    _TemplateSpec("REL-S-01", "Which assembly-source taxa within {SOURCE_TAXON_LINEAGE_A} have EVE loci with affinity to {VIRAL_LINEAGE_A}, and what source-record evidence supports each association?", "source_viral_lineage_association", _structured_outputs("exact_locus_set", "exact_source_taxon_set"), _structured_caps("source_taxonomy_projection", "list_source_taxa", "list_loci"), _STATUS),
    _TemplateSpec("REL-S-02", "For assembly-source taxa within {SOURCE_TAXON_LINEAGE_A} associated with viral-lineage affinity {VIRAL_LINEAGE_A}, which assemblies, EVE loci, and evidence sources are recorded?", "source_viral_lineage_association", _structured_outputs("exact_assembly_set", "exact_locus_set", "exact_source_taxon_set"), _structured_caps("source_taxonomy_projection", "list_assemblies", "list_loci"), _STATUS),
    _TemplateSpec("REL-S-03", "Which exact EVE loci define recorded associations between {SOURCE_TAXON_LINEAGE_A} and viral-lineage affinity {VIRAL_LINEAGE_A}, grouped by assembly-source taxon and evidence source?", "source_viral_lineage_association", _structured_outputs("exact_locus_set", "exact_source_taxon_set"), _structured_caps("source_taxonomy_projection", "list_loci"), _STATUS),
    _TemplateSpec("REL-S-04", "Which assembly-source taxa and EVE loci within {SOURCE_TAXON_LINEAGE_A} have affinity to {VIRAL_LINEAGE_A}, and which have affinity to {VIRAL_LINEAGE_B}, with evidence sources retained?", "source_viral_lineage_association", _structured_outputs("exact_locus_set", "exact_source_taxon_set", "exact_viral_lineage_set"), _structured_caps("composite_structured_plan", "source_taxonomy_projection", "list_loci", "multi_result_structured_envelope"), _STATUS),
    _TemplateSpec("REL-L-01", "Which taxa within {SOURCE_TAXON_LINEAGE_A} does the permitted literature link to viral regions with affinity to {VIRAL_LINEAGE_A}, and what evidence source supports each report?", "source_viral_lineage_association", _literature_outputs(), _lit_caps(), _STATUS),
    _TemplateSpec("REL-L-02", "For source-reported taxa within {SOURCE_TAXON_LINEAGE_A} associated with viral-lineage affinity {VIRAL_LINEAGE_A}, which assemblies or viral regions and evidence groups are named?", "source_viral_lineage_association", _literature_outputs(), _lit_caps("literature_entity_discoverability"), _STATUS),
    _TemplateSpec("REL-L-03", "Which named viral regions does the permitted literature associate with {SOURCE_TAXON_LINEAGE_A} and viral-lineage affinity {VIRAL_LINEAGE_A}, grouped by taxon and evidence source?", "source_viral_lineage_association", _literature_outputs(), _lit_caps("literature_entity_discoverability"), _STATUS),
    _TemplateSpec("REL-L-04", "Which source-reported taxa and viral regions within {SOURCE_TAXON_LINEAGE_A} have affinity to {VIRAL_LINEAGE_A}, and which have affinity to {VIRAL_LINEAGE_B}, with evidence sources retained?", "source_viral_lineage_association", _literature_outputs(), _lit_caps("literature_entity_discoverability"), _STATUS),
    _TemplateSpec("REL-H-01", "Which assembly-source taxa within {SOURCE_TAXON_LINEAGE_A} have EVE loci with affinity to {VIRAL_LINEAGE_A} that align to reported viral regions, with evidence from both sources retained?", "source_viral_lineage_association", _hybrid_outputs("exact_locus_set", "exact_source_taxon_set"), _hybrid_caps("source_taxonomy_projection", "list_source_taxa", "list_loci"), _STATUS),
    _TemplateSpec("REL-H-02", "For assembly-source taxa within {SOURCE_TAXON_LINEAGE_A} associated with viral-lineage affinity {VIRAL_LINEAGE_A}, which EVE loci and reported viral regions align across evidence sources?", "source_viral_lineage_association", _hybrid_outputs("exact_locus_set", "exact_source_taxon_set"), _hybrid_caps("source_taxonomy_projection", "list_loci"), _STATUS),
    _TemplateSpec("REL-H-03", "Which EVE loci and reported viral regions link {SOURCE_TAXON_LINEAGE_A} to viral-lineage affinity {VIRAL_LINEAGE_A}, with taxon identity and evidence source preserved?", "source_viral_lineage_association", _hybrid_outputs("exact_locus_set", "exact_source_taxon_set"), _hybrid_caps("source_taxonomy_projection", "list_loci"), _STATUS),
    _TemplateSpec("REL-H-04", "Which taxon, EVE-locus or reported-viral-region, viral-lineage-affinity, and evidence-source tuples occur for {VIRAL_LINEAGE_A} within {SOURCE_TAXON_LINEAGE_A}, and which occur for {VIRAL_LINEAGE_B}?", "source_viral_lineage_association", _hybrid_outputs("exact_locus_set", "exact_source_taxon_set", "exact_viral_lineage_set"), _hybrid_caps("composite_structured_plan", "source_taxonomy_projection", "list_loci", "multi_result_structured_envelope"), _STATUS),

    _TemplateSpec("RECORD-S-01", "Which EVE loci in assembly {ASSEMBLY_A} are recorded, grouped by viral-lineage affinity and evidence source?", "assembly_locus_association", _structured_outputs("exact_locus_set", "exact_viral_lineage_set"), _structured_caps("list_loci", "complete_paginated_relation_projection"), _STATUS),
    _TemplateSpec("RECORD-S-02", "Which assembly-source taxon, assembly, EVE-locus identity, viral-lineage affinity, and evidence source are recorded for {EVE_LOCUS_A}?", "assembly_locus_association", _structured_outputs("exact_assembly_set", "exact_locus_set", "exact_source_taxon_set", "exact_viral_lineage_set"), _structured_caps("locus_detail"), _STATUS),
    _TemplateSpec("RECORD-S-03", "Which EVE loci in assembly {ASSEMBLY_A} have affinity to {VIRAL_LINEAGE_A}, and what source-record evidence supports each association?", "assembly_locus_association", _structured_outputs("exact_locus_set"), _structured_caps("list_loci"), _STATUS),
    _TemplateSpec("RECORD-S-04", "Which assembly-source taxa, assemblies, viral-lineage affinities, and evidence sources are recorded for EVE loci {EVE_LOCUS_A}, {EVE_LOCUS_B}, and {EVE_LOCUS_C}?", "assembly_locus_association", _structured_outputs("exact_assembly_set", "exact_locus_set", "exact_source_taxon_set", "exact_viral_lineage_set"), _structured_caps("composite_structured_plan", "locus_detail", "multi_result_structured_envelope"), _STATUS),
    _TemplateSpec("RECORD-L-01", "Which viral regions in assembly {ASSEMBLY_A} does the permitted literature report, grouped by viral-lineage affinity and evidence source?", "assembly_locus_association", _literature_outputs(), _lit_caps("literature_entity_discoverability"), _STATUS),
    _TemplateSpec("RECORD-L-02", "Which source-reported taxa and viral-lineage affinities does the literature associate with viral regions in assembly {ASSEMBLY_A}, with evidence sources retained?", "assembly_locus_association", _literature_outputs(), _lit_caps("literature_entity_discoverability"), _STATUS),
    _TemplateSpec("RECORD-L-03", "Which taxon and viral-lineage affinity does the permitted literature report for viral region {REPORTED_REGION_A}, and which evidence source supports it?", "assembly_locus_association", _literature_outputs(), _lit_caps("literature_entity_discoverability"), _STATUS),
    _TemplateSpec("RECORD-L-04", "Which viral regions in assembly {ASSEMBLY_A} are associated with viral-lineage affinity {VIRAL_LINEAGE_A}, and what literature evidence source supports each report?", "assembly_locus_association", _literature_outputs(), _lit_caps("literature_entity_discoverability"), _STATUS),
    _TemplateSpec("RECORD-H-01", "Which taxon, EVE-locus, viral-lineage-affinity, and evidence-source association for {EVE_LOCUS_A} aligns with a literature-reported viral region?", "assembly_locus_association", _hybrid_outputs("exact_assembly_set", "exact_locus_set", "exact_source_taxon_set", "exact_viral_lineage_set"), _hybrid_caps("locus_detail"), _STATUS),
    _TemplateSpec("RECORD-H-02", "Which EVE loci in assembly {ASSEMBLY_A} align with literature-reported viral regions, grouped by viral-lineage affinity and evidence source?", "assembly_locus_association", _hybrid_outputs("exact_assembly_set", "exact_locus_set", "exact_viral_lineage_set"), _hybrid_caps("list_loci", "complete_paginated_relation_projection"), _STATUS),
    _TemplateSpec("RECORD-H-03", "Which EVE-locus and reported-viral-region associations in assembly {ASSEMBLY_A} are structured-only, literature-only, or present in both, with viral-lineage affinity and evidence source retained?", "assembly_locus_association", _hybrid_outputs("exact_assembly_set", "exact_locus_set", "exact_viral_lineage_set"), _hybrid_caps("list_loci", "complete_paginated_relation_projection"), _STATUS),
    _TemplateSpec("RECORD-H-04", "Which assembly-source taxa, EVE loci, viral-lineage affinities, and evidence sources are associated with assembly {ASSEMBLY_A}, and which are associated with assembly {ASSEMBLY_B}, with reported viral regions retained separately?", "assembly_locus_association", _hybrid_outputs("exact_assembly_set", "exact_locus_set", "exact_source_taxon_set", "exact_viral_lineage_set"), _hybrid_caps("composite_structured_plan", "list_loci", "complete_paginated_relation_projection", "multi_result_structured_envelope"), _STATUS),

    _TemplateSpec("UNSUP-01", "Which source taxonomic unit has the highest prevalence of {VIRAL_LINEAGE_A}-related records?", "unsupported_association_boundary", _REFUSAL_OUTPUTS, _REFUSAL_CAPS, "unsupported_by_design"),
    _TemplateSpec("UNSUP-02", "Which taxon definitely has no association with {VIRAL_LINEAGE_A}?", "unsupported_association_boundary", _REFUSAL_OUTPUTS, _REFUSAL_CAPS, "unsupported_by_design"),
    _TemplateSpec("UNSUP-03", "Which modern host species are currently infected by {VIRAL_LINEAGE_A} because an EVE association is recorded?", "unsupported_association_boundary", _REFUSAL_OUTPUTS, _REFUSAL_CAPS, "unsupported_by_design"),
    _TemplateSpec("UNSUP-04", "Which exact independent integration event is represented by each recorded EVE locus?", "unsupported_association_boundary", _REFUSAL_OUTPUTS, _REFUSAL_CAPS, "unsupported_by_design"),
    _TemplateSpec("UNSUP-05", "Which pairs of source taxa and viral lineages have co-diverged because matching EVE associations are recorded?", "unsupported_association_boundary", _REFUSAL_OUTPUTS, _REFUSAL_CAPS, "unsupported_by_design"),
    _TemplateSpec("UNSUP-06", "Classify every record as either Transferred gene or Integrated virus even though the v1 association contract has no relation-class dimension.", "unsupported_association_boundary", _REFUSAL_OUTPUTS, _REFUSAL_CAPS, "unsupported_by_design"),
    _TemplateSpec("UNSUP-07", "Treat every Integration source label as Integrated virus and every Viral contig source label as Transferred gene, then list the resulting associations.", "unsupported_association_boundary", _REFUSAL_OUTPUTS, _REFUSAL_CAPS, "unsupported_by_design"),
    _TemplateSpec("UNSUP-08", "Treat every HCVR source label as Transferred gene or Integrated virus, then list the resulting associations.", "unsupported_association_boundary", _REFUSAL_OUTPUTS, _REFUSAL_CAPS, "unsupported_by_design"),
    _TemplateSpec("UNSUP-09", "Merge study-defined, formal, and extended viral-lineage roles into one lineage and report one combined association set.", "unsupported_association_boundary", _REFUSAL_OUTPUTS, _REFUSAL_CAPS, "unsupported_by_design"),
    _TemplateSpec("UNSUP-10", "Assign EVE locus {EVE_LOCUS_A} to {VIRAL_LINEAGE_A} from name similarity alone.", "unsupported_association_boundary", _REFUSAL_OUTPUTS, _REFUSAL_CAPS, "unsupported_by_design"),
    _TemplateSpec("UNSUP-11", "Because {ASSEMBLY_SOURCE_TAXON_A} has an association with {VIRAL_LINEAGE_A}, report the same association for every taxon within {SOURCE_TAXON_LINEAGE_A}.", "unsupported_association_boundary", _REFUSAL_OUTPUTS, _REFUSAL_CAPS, "unsupported_by_design"),
    _TemplateSpec("UNSUP-12", "Merge associations from unapproved or unversioned releases and corpora into the selected release.", "unsupported_association_boundary", _REFUSAL_OUTPUTS, _REFUSAL_CAPS, "unsupported_by_design"),
    _TemplateSpec("UNSUP-13", "Treat the first page or a truncated result as the complete assembly-source-taxon, EVE-locus, viral-lineage-affinity, and evidence set.", "unsupported_association_boundary", _REFUSAL_OUTPUTS, _REFUSAL_CAPS, "unsupported_by_design"),
    _TemplateSpec("UNSUP-14", "Search the live web for additional taxon-virus associations outside the approved corpus.", "unsupported_operational_boundary", _REFUSAL_OUTPUTS, _REFUSAL_CAPS, "unsupported_by_design"),
    _TemplateSpec("UNSUP-15", "Run BLAST or HMMER on a new sequence and add the inferred association to the selected release.", "unsupported_operational_boundary", _REFUSAL_OUTPUTS, _REFUSAL_CAPS, "unsupported_by_design"),
    _TemplateSpec("UNSUP-16", "Execute an arbitrary SQL query across all database tables to construct a new taxon-virus association.", "unsupported_operational_boundary", _REFUSAL_OUTPUTS, _REFUSAL_CAPS, "unsupported_by_design"),
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
    family = _family_for(spec.template_id)
    return {
        "template_schema_version": "rag-value-scientific-question-template-v1",
        "template_id": spec.template_id,
        "family": family,
        "scientific_task": _task_for(spec.template_id),
        "scientific_intent": spec.scientific_intent,
        "question_text_template": spec.question_text_template,
        "entity_slots": tuple(sorted(set(_PLACEHOLDER_RE.findall(spec.question_text_template)))),
        "expected_output_types": tuple(sorted(set(spec.expected_output_types))),
        "required_capabilities": tuple(sorted(set(spec.required_capabilities))),
        "capability_status": spec.capability_status,
        "review_status": "pending",
        "gold": None,
        "authoring_notes": spec.authoring_notes or {"structured": _STRUCTURED_NOTE, "literature": _LITERATURE_NOTE, "hybrid": _HYBRID_NOTE, "unsupported": _UNSUPPORTED_NOTE}[family],
    }


_PREREGISTERED_METADATA = {
    spec.template_id: (_family_for(spec.template_id), _task_for(spec.template_id), spec.scientific_intent)
    for spec in _SPECS
}
_PREREGISTERED_RECORD_SHA256 = {
    spec.template_id: canonical_json_sha256(_template_payload(spec)) for spec in _SPECS
}


def _build_template(spec: _TemplateSpec) -> ScientificQuestionTemplate:
    payload = _template_payload(spec)
    return ScientificQuestionTemplate.model_validate({**payload, "record_sha256": canonical_json_sha256(payload)})


def build_scientific_question_templates() -> tuple[ScientificQuestionTemplate, ...]:
    """Build the exact 64 pending templates in preregistered order."""

    templates = tuple(_build_template(spec) for spec in _SPECS)
    validate_scientific_question_templates(templates)
    return templates


def validate_scientific_question_templates(templates: Sequence[ScientificQuestionTemplate]) -> None:
    """Validate set-level balance, identity, status, and wording invariants."""

    values = tuple(templates)
    if len(values) != 64:
        raise ScientificTemplateSetError("scientific benchmark requires exactly 64 templates")
    ids = tuple(value.template_id for value in values)
    if len(ids) != len(set(ids)):
        raise ScientificTemplateSetError("scientific template IDs must be unique")
    normalized_text = tuple(" ".join(value.question_text_template.split()).casefold() for value in values)
    if len(normalized_text) != len(set(normalized_text)):
        raise ScientificTemplateSetError("scientific question text must be unique")
    if Counter(value.family for value in values) != Counter({"structured": 16, "literature": 16, "hybrid": 16, "unsupported": 16}):
        raise ScientificTemplateSetError("scientific families must contain 16 templates each")
    expected_tasks = {"source_taxon_association": 12, "viral_lineage_association": 12, "source_viral_lineage_association": 12, "assembly_locus_association": 12, "unsupported_scientific_or_operational_boundary": 16}
    if Counter(value.scientific_task for value in values) != Counter(expected_tasks):
        raise ScientificTemplateSetError("scientific-task counts do not match preregistration")
    if any(value.review_status != "pending" or value.gold is not None for value in values):
        raise ScientificTemplateSetError("scientific templates must remain pending without Gold")
    if ids != tuple(spec.template_id for spec in _SPECS):
        raise ScientificTemplateSetError("scientific templates are not in preregistered order")
    if tuple(value.record_sha256 for value in values) != tuple(_PREREGISTERED_RECORD_SHA256[spec.template_id] for spec in _SPECS):
        raise ScientificTemplateSetError("scientific template content differs from preregistration")


def scientific_questions_template_bytes() -> bytes:
    """Serialize canonical JSONL without approving or binding a question."""

    return b"".join(canonical_json_bytes(template) + b"\n" for template in build_scientific_question_templates())


def _build_binding(entity_slot: EntitySlot, required_entity_type: RequiredEntityType) -> ScientificEntityBindingTemplate:
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
    return ScientificEntityBindingTemplate.model_validate({**payload, "record_sha256": canonical_json_sha256(payload)})


def build_scientific_entity_bindings_template() -> ScientificEntityBindingsTemplate:
    """Build an empty checksum-bound worksheet for the complete v1 slot vocabulary."""

    bindings = tuple(_build_binding(slot, _ENTITY_TYPES[slot]) for slot in sorted(_ENTITY_TYPES))
    payload: dict[str, object] = {
        "manifest_schema_version": "rag-value-scientific-entity-bindings-template-v1",
        "binding_count": 10,
        "bindings": bindings,
    }
    return ScientificEntityBindingsTemplate.model_validate({**payload, "manifest_sha256": canonical_json_sha256(payload)})


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
