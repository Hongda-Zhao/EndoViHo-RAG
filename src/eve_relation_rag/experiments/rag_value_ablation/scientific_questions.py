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
    "host_eve_profile",
    "viral_lineage_distribution",
    "host_virus_relationship",
    "assembly_locus_evidence",
    "unsupported_scientific_or_operational_boundary",
]
type ScientificIntent = Literal[
    "host_eve_profile",
    "viral_lineage_distribution",
    "host_virus_relationship",
    "assembly_eve_profile",
    "locus_evidence_profile",
    "method_explanation",
    "interpretation_limitation",
    "unsupported_inference",
    "unsupported_analysis",
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
    "exact_locus_set",
    "exact_assembly_set",
    "exact_source_taxon_set",
    "exact_viral_lineage_set",
    "exact_coordinates",
    "exact_counts",
    "detection_call_set",
    "public_assertion_set",
    "structured_evidence_set",
    "required_documents",
    "required_evidence_groups",
    "required_methods",
    "required_limitations",
    "forbidden_claims",
    "refusal_category",
    "prohibited_downstream_stages",
]
type RequiredCapability = Literal[
    "aggregate",
    "assembly_eve_profile",
    "composite_structured_plan",
    "explicit_unsupported_boundary",
    "host_eve_profile",
    "host_virus_relationship",
    "list_assemblies",
    "list_loci",
    "list_source_taxa",
    "list_viral_lineages",
    "literature_entity_discoverability",
    "literature_retrieval",
    "locus_detail",
    "locus_inclusion_provenance",
    "multi_result_structured_envelope",
    "natural_hybrid_decomposition",
    "natural_literature_routing",
    "natural_structured_planning",
    "public_assertion_evidence",
    "refusal_before_downstream_execution",
    "safe_methods_and_limitations_routing",
    "self_contained_question_context",
    "structured_anchor_resolution",
    "viral_lineage_distribution",
]
type CapabilityStatus = Literal[
    "supported_now",
    "requires_natural_structured_planning",
    "requires_natural_literature_routing",
    "requires_new_intent",
    "requires_composite_plan",
    "requires_natural_hybrid_decomposition",
    "future_only",
    "unsupported_by_design",
]

_PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")
_FAKE_ALL_A_LOCUS = (
    "locus:eve:v1:sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
)
_MECHANICAL_HYBRID_PHRASE = ". and explain the literature"
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
        "assembly_eve_profile",
        "composite_structured_plan",
        "explicit_unsupported_boundary",
        "host_eve_profile",
        "host_virus_relationship",
        "list_viral_lineages",
        "locus_inclusion_provenance",
        "multi_result_structured_envelope",
        "natural_hybrid_decomposition",
        "natural_literature_routing",
        "natural_structured_planning",
        "safe_methods_and_limitations_routing",
        "self_contained_question_context",
        "viral_lineage_distribution",
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


_PENDING_NOTE = (
    "Pending authoring template only. Bind every entity slot to an approved release object, "
    "instantiate the exact text, and obtain independent human review before conversion to an "
    "EvaluationQuestion."
)
_UNSUPPORTED_NOTE = (
    "Pending unsupported-boundary template only. Human review must define the exact refusal "
    "category and prohibited downstream stages; no answer, evidence, or approval is supplied."
)
_POLICY_CONTEXT_NOTE = (
    "Pending template. The current blanket scope policy rejects wording inside this legitimate "
    "methods or limitations question; a future typed safe-context rule is required before use."
)


def _lit_caps(*extra: RequiredCapability) -> tuple[RequiredCapability, ...]:
    return ("literature_retrieval", "natural_literature_routing", *extra)


def _hybrid_caps(*structured: RequiredCapability) -> tuple[RequiredCapability, ...]:
    return (
        "literature_retrieval",
        "natural_hybrid_decomposition",
        "structured_anchor_resolution",
        *structured,
    )


_SPECS: tuple[_TemplateSpec, ...] = (
    _TemplateSpec(
        "HOST-S-01",
        "What included EVE loci are recorded in assemblies assigned to {HOST_SPECIES_A}?",
        "host_eve_profile",
        ("exact_locus_set",),
        ("list_loci", "natural_structured_planning"),
        "requires_natural_structured_planning",
    ),
    _TemplateSpec(
        "HOST-S-02",
        "Which assemblies assigned to {HOST_LINEAGE_A} contain included EVE loci?",
        "host_eve_profile",
        ("exact_assembly_set",),
        ("list_assemblies", "natural_structured_planning"),
        "requires_natural_structured_planning",
    ),
    _TemplateSpec(
        "HOST-S-03",
        "Which viral lineages are represented among included EVE loci in {HOST_LINEAGE_A}?",
        "host_eve_profile",
        ("exact_viral_lineage_set",),
        ("list_viral_lineages", "natural_structured_planning"),
        "requires_new_intent",
    ),
    _TemplateSpec(
        "HOST-S-04",
        "What is the EVE profile of {HOST_SPECIES_A} in the selected release?",
        "host_eve_profile",
        ("exact_assembly_set", "exact_counts", "exact_locus_set", "exact_viral_lineage_set"),
        (
            "aggregate",
            "composite_structured_plan",
            "host_eve_profile",
            "list_assemblies",
            "list_loci",
            "list_viral_lineages",
            "multi_result_structured_envelope",
            "natural_structured_planning",
        ),
        "requires_composite_plan",
    ),
    _TemplateSpec(
        "HOST-L-01",
        "How did the source studies identify EVE candidates reported for {HOST_LINEAGE_A}?",
        "method_explanation",
        ("required_documents", "required_evidence_groups", "required_methods"),
        _lit_caps("safe_methods_and_limitations_routing"),
        "requires_natural_literature_routing",
        _POLICY_CONTEXT_NOTE,
    ),
    _TemplateSpec(
        "HOST-L-02",
        "What evidence did the literature use to support the endogenous origin of EVE records in {HOST_LINEAGE_A}?",
        "locus_evidence_profile",
        ("required_documents", "required_evidence_groups"),
        _lit_caps(),
        "requires_natural_literature_routing",
    ),
    _TemplateSpec(
        "HOST-L-03",
        "What limitations do the source studies discuss when interpreting EVE records in {HOST_LINEAGE_A}?",
        "interpretation_limitation",
        ("required_documents", "required_evidence_groups", "required_limitations"),
        _lit_caps(),
        "requires_natural_literature_routing",
    ),
    _TemplateSpec(
        "HOST-L-04",
        "How did the source literature classify the viral origins of EVE records reported in {HOST_LINEAGE_A}?",
        "method_explanation",
        ("required_documents", "required_evidence_groups", "required_methods"),
        _lit_caps(),
        "requires_natural_literature_routing",
    ),
    _TemplateSpec(
        "HOST-H-01",
        "What EVE loci are recorded in {HOST_SPECIES_A}, and what evidence supports their endogenous origin?",
        "host_eve_profile",
        ("exact_locus_set", "required_documents", "required_evidence_groups"),
        _hybrid_caps("list_loci"),
        "requires_natural_hybrid_decomposition",
    ),
    _TemplateSpec(
        "HOST-H-02",
        "Which viral lineages are represented among EVE loci in {HOST_LINEAGE_A}, and how were those assignments made?",
        "host_eve_profile",
        (
            "exact_viral_lineage_set",
            "required_documents",
            "required_evidence_groups",
            "required_methods",
        ),
        _hybrid_caps("list_viral_lineages"),
        "requires_natural_hybrid_decomposition",
    ),
    _TemplateSpec(
        "HOST-H-03",
        "Which assemblies assigned to {HOST_LINEAGE_A} contain EVE loci, and what assembly-related limitations apply to those records?",
        "host_eve_profile",
        (
            "exact_assembly_set",
            "required_documents",
            "required_evidence_groups",
            "required_limitations",
        ),
        _hybrid_caps("list_assemblies"),
        "requires_natural_hybrid_decomposition",
    ),
    _TemplateSpec(
        "HOST-H-04",
        "What is the EVE profile of {HOST_SPECIES_A}, and how should those records be interpreted according to the source literature?",
        "host_eve_profile",
        (
            "exact_assembly_set",
            "exact_counts",
            "exact_locus_set",
            "exact_viral_lineage_set",
            "required_documents",
            "required_evidence_groups",
            "required_limitations",
        ),
        _hybrid_caps(
            "composite_structured_plan",
            "host_eve_profile",
            "multi_result_structured_envelope",
        ),
        "requires_natural_hybrid_decomposition",
    ),
    _TemplateSpec(
        "VIRUS-S-01",
        "In which assembly-source taxa are {VIRAL_LINEAGE_A}-related EVE loci recorded?",
        "viral_lineage_distribution",
        ("exact_source_taxon_set",),
        ("list_source_taxa", "natural_structured_planning"),
        "requires_natural_structured_planning",
    ),
    _TemplateSpec(
        "VIRUS-S-02",
        "Which assemblies contain included EVE loci with affinity to {VIRAL_LINEAGE_A}?",
        "viral_lineage_distribution",
        ("exact_assembly_set",),
        ("list_assemblies", "natural_structured_planning"),
        "requires_natural_structured_planning",
    ),
    _TemplateSpec(
        "VIRUS-S-03",
        "How many distinct source taxa, assemblies, and EVE loci are represented for {VIRAL_LINEAGE_A}?",
        "viral_lineage_distribution",
        ("exact_counts",),
        (
            "aggregate",
            "composite_structured_plan",
            "multi_result_structured_envelope",
            "natural_structured_planning",
            "viral_lineage_distribution",
        ),
        "requires_composite_plan",
    ),
    _TemplateSpec(
        "VIRUS-S-04",
        "Which exact EVE loci have supported affinity to {VIRAL_LINEAGE_A}?",
        "viral_lineage_distribution",
        ("exact_locus_set",),
        ("list_loci", "natural_structured_planning"),
        "requires_natural_structured_planning",
    ),
    _TemplateSpec(
        "VIRUS-L-01",
        "What evidence does the literature use to assign reported regions to {VIRAL_LINEAGE_A}?",
        "method_explanation",
        ("required_documents", "required_evidence_groups"),
        _lit_caps(),
        "requires_natural_literature_routing",
    ),
    _TemplateSpec(
        "VIRUS-L-02",
        "How do the source studies distinguish {VIRAL_LINEAGE_A}-related signals from false-positive protein similarities?",
        "method_explanation",
        ("required_documents", "required_evidence_groups", "required_methods"),
        _lit_caps(),
        "requires_natural_literature_routing",
    ),
    _TemplateSpec(
        "VIRUS-L-03",
        "What taxonomic uncertainty or naming limitations apply to {VIRAL_LINEAGE_A} assignments?",
        "interpretation_limitation",
        ("required_documents", "required_evidence_groups", "required_limitations"),
        _lit_caps(),
        "requires_natural_literature_routing",
    ),
    _TemplateSpec(
        "VIRUS-L-04",
        "What limitations affect interpretation of the recorded host distribution of {VIRAL_LINEAGE_A}-related EVE records?",
        "interpretation_limitation",
        ("required_documents", "required_evidence_groups", "required_limitations"),
        _lit_caps(),
        "requires_natural_literature_routing",
    ),
    _TemplateSpec(
        "VIRUS-H-01",
        "In which source taxa are {VIRAL_LINEAGE_A}-related EVE loci recorded, and how were those loci assigned to this viral lineage?",
        "viral_lineage_distribution",
        (
            "exact_source_taxon_set",
            "required_documents",
            "required_evidence_groups",
            "required_methods",
        ),
        _hybrid_caps("list_source_taxa"),
        "requires_natural_hybrid_decomposition",
    ),
    _TemplateSpec(
        "VIRUS-H-02",
        "Which assemblies contain {VIRAL_LINEAGE_A}-related EVE loci, and what evidence supports their endogenous status?",
        "viral_lineage_distribution",
        ("exact_assembly_set", "required_documents", "required_evidence_groups"),
        _hybrid_caps("list_assemblies"),
        "requires_natural_hybrid_decomposition",
    ),
    _TemplateSpec(
        "VIRUS-H-03",
        "What is the recorded distribution of {VIRAL_LINEAGE_A}-related EVE loci, and what limitations apply to interpreting that distribution?",
        "viral_lineage_distribution",
        (
            "exact_assembly_set",
            "exact_counts",
            "exact_locus_set",
            "exact_source_taxon_set",
            "required_documents",
            "required_evidence_groups",
            "required_limitations",
        ),
        _hybrid_caps(
            "composite_structured_plan",
            "multi_result_structured_envelope",
            "viral_lineage_distribution",
        ),
        "requires_natural_hybrid_decomposition",
    ),
    _TemplateSpec(
        "VIRUS-H-04",
        "Which exact loci have affinity to {EXTENDED_LINEAGE_A}, and what evidence supports the use of this extended-lineage label?",
        "viral_lineage_distribution",
        ("exact_locus_set", "required_documents", "required_evidence_groups"),
        _hybrid_caps("list_loci"),
        "requires_natural_hybrid_decomposition",
    ),
    _TemplateSpec(
        "REL-S-01",
        "Which {HOST_LINEAGE_A} assemblies contain {VIRAL_LINEAGE_A}-related EVE loci?",
        "host_virus_relationship",
        ("exact_assembly_set",),
        ("list_assemblies", "natural_structured_planning"),
        "requires_natural_structured_planning",
    ),
    _TemplateSpec(
        "REL-S-02",
        "Which EVE loci support the recorded association between {HOST_LINEAGE_A} and {VIRAL_LINEAGE_A}?",
        "host_virus_relationship",
        ("exact_locus_set",),
        ("list_loci", "natural_structured_planning"),
        "requires_natural_structured_planning",
    ),
    _TemplateSpec(
        "REL-S-03",
        "How many distinct loci and assemblies support the recorded association between {HOST_LINEAGE_A} and {VIRAL_LINEAGE_A}?",
        "host_virus_relationship",
        ("exact_counts",),
        (
            "aggregate",
            "composite_structured_plan",
            "host_virus_relationship",
            "multi_result_structured_envelope",
            "natural_structured_planning",
        ),
        "requires_composite_plan",
    ),
    _TemplateSpec(
        "REL-S-04",
        "What are the exact coordinates and assembly-source taxa of the loci supporting this association?",
        "host_virus_relationship",
        ("exact_coordinates", "exact_locus_set", "exact_source_taxon_set"),
        ("list_loci", "natural_structured_planning", "self_contained_question_context"),
        "future_only",
        (
            "Pending and not self-contained: 'this association' cannot rely on conversation "
            "memory. Human authoring must revise or explicitly instantiate the relationship "
            "before approval."
        ),
    ),
    _TemplateSpec(
        "REL-L-01",
        "How did the source studies detect candidate {VIRAL_LINEAGE_A}-related regions in {HOST_LINEAGE_A} assemblies?",
        "method_explanation",
        ("required_documents", "required_evidence_groups", "required_methods"),
        _lit_caps(),
        "requires_natural_literature_routing",
    ),
    _TemplateSpec(
        "REL-L-02",
        "What evidence supports the endogenous origin of {VIRAL_LINEAGE_A}-related regions reported from {HOST_LINEAGE_A} assemblies?",
        "locus_evidence_profile",
        ("required_documents", "required_evidence_groups"),
        _lit_caps(),
        "requires_natural_literature_routing",
    ),
    _TemplateSpec(
        "REL-L-03",
        "What alternative explanations or uncertainties could affect the recorded association between {HOST_LINEAGE_A} and {VIRAL_LINEAGE_A}?",
        "interpretation_limitation",
        ("required_documents", "required_evidence_groups", "required_limitations"),
        _lit_caps(),
        "requires_natural_literature_routing",
    ),
    _TemplateSpec(
        "REL-L-04",
        "Why does the recorded association between {HOST_LINEAGE_A} and {VIRAL_LINEAGE_A} not by itself demonstrate modern infection or host–virus co-divergence?",
        "interpretation_limitation",
        (
            "forbidden_claims",
            "required_documents",
            "required_evidence_groups",
            "required_limitations",
        ),
        _lit_caps("safe_methods_and_limitations_routing"),
        "requires_natural_literature_routing",
        _POLICY_CONTEXT_NOTE,
    ),
    _TemplateSpec(
        "REL-H-01",
        "Which {HOST_LINEAGE_A} assemblies contain {VIRAL_LINEAGE_A}-related EVE loci, and how were those loci detected?",
        "host_virus_relationship",
        (
            "exact_assembly_set",
            "required_documents",
            "required_evidence_groups",
            "required_methods",
        ),
        _hybrid_caps("list_assemblies", "safe_methods_and_limitations_routing"),
        "requires_natural_hybrid_decomposition",
        _POLICY_CONTEXT_NOTE,
    ),
    _TemplateSpec(
        "REL-H-02",
        "How many EVE loci support the recorded {HOST_LINEAGE_A}–{VIRAL_LINEAGE_A} association, and why should this count not be interpreted as the number of independent integration events?",
        "host_virus_relationship",
        (
            "exact_counts",
            "forbidden_claims",
            "required_documents",
            "required_evidence_groups",
            "required_limitations",
        ),
        _hybrid_caps("aggregate", "safe_methods_and_limitations_routing"),
        "requires_natural_hybrid_decomposition",
        _POLICY_CONTEXT_NOTE,
    ),
    _TemplateSpec(
        "REL-H-03",
        "Which records support the {HOST_LINEAGE_A}–{VIRAL_LINEAGE_A} association, and what evidence supports their viral-lineage assignments?",
        "host_virus_relationship",
        (
            "exact_locus_set",
            "public_assertion_set",
            "required_documents",
            "required_evidence_groups",
        ),
        _hybrid_caps("list_loci"),
        "requires_natural_hybrid_decomposition",
    ),
    _TemplateSpec(
        "REL-H-04",
        "Summarize the recorded association between {HOST_LINEAGE_A} and {VIRAL_LINEAGE_A}, including the exact records, supporting literature, and major interpretation limits.",
        "host_virus_relationship",
        (
            "exact_locus_set",
            "required_documents",
            "required_evidence_groups",
            "required_limitations",
        ),
        _hybrid_caps("list_loci"),
        "requires_natural_hybrid_decomposition",
    ),
    _TemplateSpec(
        "RECORD-S-01",
        "What EVE loci are recorded in assembly {ASSEMBLY_A}?",
        "assembly_eve_profile",
        ("exact_locus_set",),
        ("list_loci", "natural_structured_planning"),
        "requires_natural_structured_planning",
    ),
    _TemplateSpec(
        "RECORD-S-02",
        "Show the exact genomic location, detection calls, and public assertions for locus {LOCUS_A}.",
        "locus_evidence_profile",
        ("detection_call_set", "exact_coordinates", "public_assertion_set"),
        ("locus_detail", "natural_structured_planning"),
        "requires_natural_structured_planning",
    ),
    _TemplateSpec(
        "RECORD-S-03",
        "Which viral-lineage affinities are represented among the EVE loci in assembly {ASSEMBLY_A}?",
        "assembly_eve_profile",
        ("exact_viral_lineage_set",),
        ("list_viral_lineages", "natural_structured_planning"),
        "requires_new_intent",
    ),
    _TemplateSpec(
        "RECORD-S-04",
        "Which structured evidence items and source locators are linked to locus {LOCUS_A}?",
        "locus_evidence_profile",
        ("public_assertion_set", "structured_evidence_set"),
        ("locus_detail", "natural_structured_planning", "public_assertion_evidence"),
        "requires_natural_structured_planning",
        (
            "Pending template. Gold must be limited to supporting evidence selected through "
            "public assertion membership unless a broader evidence intent is separately approved."
        ),
    ),
    _TemplateSpec(
        "RECORD-L-01",
        "How did the source study define the detection criteria used for records such as locus {LOCUS_A}?",
        "method_explanation",
        ("required_documents", "required_evidence_groups", "required_methods"),
        _lit_caps("literature_entity_discoverability"),
        "requires_natural_literature_routing",
    ),
    _TemplateSpec(
        "RECORD-L-02",
        "What methods were used to evaluate host-genomic flanks around reported EVE loci?",
        "method_explanation",
        ("required_documents", "required_evidence_groups", "required_methods"),
        _lit_caps(),
        "requires_natural_literature_routing",
    ),
    _TemplateSpec(
        "RECORD-L-03",
        "What assembly or contig limitations could affect interpretation of locus {LOCUS_A}?",
        "interpretation_limitation",
        ("required_documents", "required_evidence_groups", "required_limitations"),
        _lit_caps("literature_entity_discoverability"),
        "requires_natural_literature_routing",
    ),
    _TemplateSpec(
        "RECORD-L-04",
        "What evidence did the source literature use to distinguish integrated regions from viral contigs?",
        "method_explanation",
        ("required_documents", "required_evidence_groups", "required_methods"),
        _lit_caps(),
        "requires_natural_literature_routing",
    ),
    _TemplateSpec(
        "RECORD-H-01",
        "Show locus {LOCUS_A} and explain what evidence supports its endogenous origin.",
        "locus_evidence_profile",
        (
            "exact_coordinates",
            "public_assertion_set",
            "required_documents",
            "required_evidence_groups",
        ),
        _hybrid_caps("locus_detail"),
        "requires_natural_hybrid_decomposition",
    ),
    _TemplateSpec(
        "RECORD-H-02",
        "What EVE profile is recorded for assembly {ASSEMBLY_A}, and how were those records identified?",
        "assembly_eve_profile",
        (
            "exact_locus_set",
            "exact_viral_lineage_set",
            "required_documents",
            "required_evidence_groups",
            "required_methods",
        ),
        _hybrid_caps(
            "assembly_eve_profile",
            "composite_structured_plan",
            "list_loci",
            "list_viral_lineages",
            "multi_result_structured_envelope",
        ),
        "requires_natural_hybrid_decomposition",
    ),
    _TemplateSpec(
        "RECORD-H-03",
        "Why was locus {LOCUS_A} included in this release, and what literature supports its viral-lineage assignment?",
        "locus_evidence_profile",
        (
            "public_assertion_set",
            "required_documents",
            "required_evidence_groups",
            "structured_evidence_set",
        ),
        _hybrid_caps("locus_detail", "locus_inclusion_provenance"),
        "requires_natural_hybrid_decomposition",
    ),
    _TemplateSpec(
        "RECORD-H-04",
        "What uncertainties remain for locus {LOCUS_C}, considering both its structured assertions and the limitations described in the literature?",
        "locus_evidence_profile",
        (
            "public_assertion_set",
            "required_documents",
            "required_evidence_groups",
            "required_limitations",
        ),
        _hybrid_caps("locus_detail"),
        "requires_natural_hybrid_decomposition",
    ),
    _TemplateSpec(
        "UNSUP-01",
        "Which host lineage has the highest prevalence of {VIRAL_LINEAGE_A}-related EVEs?",
        "unsupported_inference",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("refusal_before_downstream_execution",),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-02",
        "Which species definitely lacks {VIRAL_LINEAGE_A}-related EVEs?",
        "unsupported_inference",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("explicit_unsupported_boundary", "refusal_before_downstream_execution"),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-03",
        "Prove that {VIRAL_LINEAGE_A} infected modern members of {HOST_LINEAGE_A}.",
        "unsupported_inference",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("refusal_before_downstream_execution",),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-04",
        "Do the recorded EVE loci represent the same number of independent integration events?",
        "unsupported_inference",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("refusal_before_downstream_execution",),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-05",
        "Prove host–virus co-divergence between {HOST_LINEAGE_A} and {VIRAL_LINEAGE_A}.",
        "unsupported_inference",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("refusal_before_downstream_execution",),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-06",
        "Determine the exact date when locus {LOCUS_A} integrated into the host genome.",
        "unsupported_inference",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("explicit_unsupported_boundary", "refusal_before_downstream_execution"),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-07",
        "Is locus {LOCUS_A} transcriptionally active?",
        "unsupported_inference",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("explicit_unsupported_boundary", "refusal_before_downstream_execution"),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-08",
        "Does locus {LOCUS_A} provide an adaptive function to its host?",
        "unsupported_inference",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("explicit_unsupported_boundary", "refusal_before_downstream_execution"),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-09",
        "Which host lineage is most susceptible to infection by {VIRAL_LINEAGE_A}?",
        "unsupported_inference",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("refusal_before_downstream_execution",),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-10",
        "Which screened-negative species contain no endogenous viral elements?",
        "unsupported_inference",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("refusal_before_downstream_execution",),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-11",
        "Run HMMER or BLAST on this new sequence and identify previously unknown EVEs.",
        "unsupported_analysis",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("refusal_before_downstream_execution",),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-12",
        "Build a phylogenetic tree and place locus {LOCUS_A} within it.",
        "unsupported_analysis",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("refusal_before_downstream_execution",),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-13",
        "Search the live web for additional evidence that is not present in the approved corpus.",
        "unsupported_analysis",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("refusal_before_downstream_execution",),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-14",
        "Estimate the global natural distribution of {VIRAL_LINEAGE_A}-related EVEs from this pilot release.",
        "unsupported_inference",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("explicit_unsupported_boundary", "refusal_before_downstream_execution"),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-15",
        "Execute an arbitrary SQL query across all database tables.",
        "unsupported_analysis",
        ("forbidden_claims", "prohibited_downstream_stages", "refusal_category"),
        ("refusal_before_downstream_execution",),
        "unsupported_by_design",
    ),
    _TemplateSpec(
        "UNSUP-16",
        "Determine with certainty the ancestral host species in which locus {LOCUS_A} first originated.",
        "unsupported_inference",
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
        "HOST": "host_eve_profile",
        "VIRUS": "viral_lineage_distribution",
        "REL": "host_virus_relationship",
        "RECORD": "assembly_locus_evidence",
        "UNSUP": "unsupported_scientific_or_operational_boundary",
    }[prefix]  # type: ignore[return-value]


def _template_payload(spec: _TemplateSpec) -> dict[str, object]:
    slots = tuple(sorted(set(_PLACEHOLDER_RE.findall(spec.question_text_template))))
    return {
        "template_schema_version": "rag-value-scientific-question-template-v1",
        "template_id": spec.template_id,
        "family": _family_for(spec.template_id),
        "scientific_task": _task_for(spec.template_id),
        "scientific_intent": spec.scientific_intent,
        "question_text_template": spec.question_text_template,
        "entity_slots": slots,
        "expected_output_types": tuple(sorted(spec.expected_output_types)),
        "required_capabilities": tuple(sorted(spec.required_capabilities)),
        "capability_status": spec.capability_status,
        "review_status": "pending",
        "gold": None,
        "authoring_notes": spec.authoring_notes
        or (
            _UNSUPPORTED_NOTE
            if spec.capability_status == "unsupported_by_design"
            else _PENDING_NOTE
        ),
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
        "host_eve_profile": 12,
        "viral_lineage_distribution": 12,
        "host_virus_relationship": 12,
        "assembly_locus_evidence": 12,
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
