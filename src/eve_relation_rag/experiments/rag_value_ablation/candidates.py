"""Human-reviewable pending question candidates and deterministic grammar audit."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    CandidateQuestionMetadata,
    CandidateRoute,
    CandidateStructuredIntent,
    EvaluationQuestion,
    OracleEvidenceEntry,
    QuestionFamily,
    build_evaluation_question,
    build_oracle_entry,
    build_question_manifest,
)
from eve_relation_rag.hybrid.contracts import RagQueryRequest
from eve_relation_rag.literature.contracts import Sha256, StrictFrozenSchema
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256
from eve_relation_rag.planning.parser import ControlledEnglishPlanner, StructuredQueryRequest
from eve_relation_rag.planning.resolver import (
    AssemblyResolverRecord,
    CatalogReleaseResolver,
    LineageResolverRecord,
    LocusResolverRecord,
)
from eve_relation_rag.planning.router import DeterministicRouter
from eve_relation_rag.retrieval.structured.results import PlanSuccess

FIXTURE_RELEASE_KEY = "release:endoviho-rag:v0:20990101:001"
FIXTURE_CORPUS_KEY = "corpus:endoviho-rag:v0:20990101:001"
FIXTURE_ASSEMBLY = "GCA_029931535.1"
FIXTURE_LOCUS = f"locus:eve:v1:sha256:{'a' * 64}"
PARSER_FIXTURE_PROFILE = "rag-value-candidate-parser-fixture-v1"

_STRUCTURED_SOURCES = (
    "README.md",
    "docs/data_semantics.md",
    "query-plan:controlled-english-v0.1",
    "src/eve_relation_rag/planning/query_plans.py",
    "tests/planning/test_parser.py",
)
_LITERATURE_SOURCES = (
    "README.md",
    "docs/data_semantics.md",
    "tests/planning/test_m4_router.py",
)
_HYBRID_SOURCES = (
    "README.md",
    "docs/data_semantics.md",
    "query-plan:controlled-english-v0.1",
    "src/eve_relation_rag/planning/query_plans.py",
    "tests/planning/test_m4_router.py",
    "tests/planning/test_parser.py",
)
_UNSUPPORTED_SOURCES = (
    "README.md",
    "docs/data_semantics.md",
    "src/eve_relation_rag/planning/scope_policy.py",
    "tests/planning/test_m4_router.py",
)

_ALLOWED_SEMANTIC_CODES: dict[QuestionFamily, frozenset[str]] = {
    "structured": frozenset(
        {
            "assembly_identity",
            "coordinates",
            "detection_calls",
            "locus_identity",
            "release_provenance",
            "source_taxonomy",
            "structured_count",
            "structured_record_set",
            "viral_lineage_identity",
        }
    ),
    "literature": frozenset(
        {
            "assembly_fragmentation_limitation",
            "biological_interpretation_limitation",
            "coordinate_provenance",
            "detection_method",
            "endogeneity_evidence",
            "false_positive_control",
            "high_confidence_definition",
            "lineage_assignment",
        }
    ),
    "hybrid": frozenset(
        {
            "assembly_identity",
            "biological_interpretation_limitation",
            "coordinates",
            "detection_calls",
            "detection_method",
            "lineage_assignment",
            "locus_identity",
            "release_provenance",
            "source_taxonomy",
            "structured_count",
            "structured_record_set",
            "viral_lineage_identity",
        }
    ),
    "unsupported": frozenset(
        {
            "arbitrary_sql_requested",
            "biological_absence_not_established",
            "codivergence_not_established",
            "conversation_memory_requested",
            "external_computation_requested",
            "external_knowledge_requested",
            "host_lineage_comparison_unsupported",
            "independent_event_not_established",
            "live_web_search_requested",
            "modern_infection_not_established",
            "multilingual_output_requested",
            "prevalence_not_established",
        }
    ),
}


class CandidateSetError(ValueError):
    """Raised when candidate wording, routing, parsing, or semantics drift."""


@dataclass(frozen=True, slots=True)
class _CandidateSpec:
    question_id: str
    family: QuestionFamily
    question_text: str
    expected_route: CandidateRoute
    expected_structured_intent: CandidateStructuredIntent | None
    evaluation_focus: str
    semantic_boundary_codes: tuple[str, ...]
    wording_sources: tuple[str, ...]
    uses_fixture_entities: bool = False


def _spec(
    question_id: str,
    family: QuestionFamily,
    question_text: str,
    *,
    route: CandidateRoute,
    intent: CandidateStructuredIntent | None,
    focus: str,
    semantics: tuple[str, ...],
    sources: tuple[str, ...],
    fixture_entities: bool = False,
) -> _CandidateSpec:
    return _CandidateSpec(
        question_id=question_id,
        family=family,
        question_text=question_text,
        expected_route=route,
        expected_structured_intent=intent,
        evaluation_focus=focus,
        semantic_boundary_codes=tuple(sorted(semantics)),
        wording_sources=tuple(sorted(sources)),
        uses_fixture_entities=fixture_entities,
    )


_CANDIDATE_SPECS: tuple[_CandidateSpec, ...] = (
    _spec(
        "structured-001",
        "structured",
        "Count distinct included loci in this release.",
        route="structured",
        intent="aggregate",
        focus="release-included-locus-count",
        semantics=("release_provenance", "structured_count"),
        sources=_STRUCTURED_SOURCES,
    ),
    _spec(
        "structured-002",
        "structured",
        "Count distinct contigs in this release.",
        route="structured",
        intent="aggregate",
        focus="release-contig-count",
        semantics=("release_provenance", "structured_count"),
        sources=_STRUCTURED_SOURCES,
    ),
    _spec(
        "structured-003",
        "structured",
        "Count distinct assemblies in this release.",
        route="structured",
        intent="aggregate",
        focus="release-assembly-count",
        semantics=("assembly_identity", "release_provenance", "structured_count"),
        sources=_STRUCTURED_SOURCES,
    ),
    _spec(
        "structured-004",
        "structured",
        "Count distinct source taxa in this release.",
        route="structured",
        intent="aggregate",
        focus="release-source-taxon-count",
        semantics=("release_provenance", "source_taxonomy", "structured_count"),
        sources=_STRUCTURED_SOURCES,
    ),
    _spec(
        "structured-005",
        "structured",
        "Count detection calls in this release.",
        route="structured",
        intent="aggregate",
        focus="release-detection-call-count",
        semantics=("detection_calls", "release_provenance", "structured_count"),
        sources=_STRUCTURED_SOURCES,
    ),
    _spec(
        "structured-006",
        "structured",
        "List all loci in this release.",
        route="structured",
        intent="list_loci",
        focus="release-locus-record-set",
        semantics=("locus_identity", "release_provenance", "structured_record_set"),
        sources=_STRUCTURED_SOURCES,
    ),
    _spec(
        "structured-007",
        "structured",
        "List all assemblies in this release.",
        route="structured",
        intent="list_assemblies",
        focus="release-assembly-record-set",
        semantics=("assembly_identity", "release_provenance", "structured_record_set"),
        sources=_STRUCTURED_SOURCES,
    ),
    _spec(
        "structured-008",
        "structured",
        "List all source taxa represented in this release.",
        route="structured",
        intent="list_source_taxa",
        focus="release-source-taxon-record-set",
        semantics=("release_provenance", "source_taxonomy", "structured_record_set"),
        sources=_STRUCTURED_SOURCES,
    ),
    _spec(
        "structured-009",
        "structured",
        f"Show assembly {FIXTURE_ASSEMBLY}.",
        route="structured",
        intent="assembly_detail",
        focus="assembly-detail-and-accession",
        semantics=("assembly_identity", "release_provenance"),
        sources=_STRUCTURED_SOURCES,
        fixture_entities=True,
    ),
    _spec(
        "structured-010",
        "structured",
        f"Show locus {FIXTURE_LOCUS}.",
        route="structured",
        intent="locus_detail",
        focus="locus-detail-coordinate-and-sequence",
        semantics=("coordinates", "locus_identity", "release_provenance"),
        sources=_STRUCTURED_SOURCES,
        fixture_entities=True,
    ),
    _spec(
        "structured-011",
        "structured",
        f"List loci in assembly {FIXTURE_ASSEMBLY}.",
        route="structured",
        intent="list_loci",
        focus="assembly-filtered-locus-record-set",
        semantics=("assembly_identity", "locus_identity", "structured_record_set"),
        sources=_STRUCTURED_SOURCES,
        fixture_entities=True,
    ),
    _spec(
        "structured-012",
        "structured",
        f"Count detection calls in assembly {FIXTURE_ASSEMBLY}.",
        route="structured",
        intent="aggregate",
        focus="assembly-filtered-detection-call-count",
        semantics=("assembly_identity", "detection_calls", "structured_count"),
        sources=_STRUCTURED_SOURCES,
        fixture_entities=True,
    ),
    _spec(
        "structured-013",
        "structured",
        f"Count distinct included loci in assembly {FIXTURE_ASSEMBLY}.",
        route="structured",
        intent="aggregate",
        focus="assembly-filtered-included-locus-count",
        semantics=("assembly_identity", "locus_identity", "structured_count"),
        sources=_STRUCTURED_SOURCES,
        fixture_entities=True,
    ),
    _spec(
        "structured-014",
        "structured",
        "List loci assigned exactly to source lineage Bivalvia.",
        route="structured",
        intent="list_loci",
        focus="source-lineage-filtered-locus-set",
        semantics=("locus_identity", "source_taxonomy", "structured_record_set"),
        sources=_STRUCTURED_SOURCES,
        fixture_entities=True,
    ),
    _spec(
        "structured-015",
        "structured",
        "List assemblies with study viral lineage Orthopolintovirales exactly.",
        route="structured",
        intent="list_assemblies",
        focus="study-lineage-filtered-assembly-set",
        semantics=(
            "assembly_identity",
            "structured_record_set",
            "viral_lineage_identity",
        ),
        sources=_STRUCTURED_SOURCES,
        fixture_entities=True,
    ),
    _spec(
        "structured-016",
        "structured",
        "Count distinct included loci with extended viral lineage Asfa-like including descendants.",
        route="structured",
        intent="aggregate",
        focus="extended-lineage-descendant-locus-count",
        semantics=("structured_count", "viral_lineage_identity"),
        sources=_STRUCTURED_SOURCES,
        fixture_entities=True,
    ),
    _spec(
        "literature-001",
        "literature",
        "Explain the literature methods for how ViralRecall screened candidate viral regions",
        route="literature",
        intent=None,
        focus="viralrecall-screening-method",
        semantics=("detection_method",),
        sources=_LITERATURE_SOURCES,
    ),
    _spec(
        "literature-002",
        "literature",
        "Explain the literature methods for defining high-confidence viral regions",
        route="literature",
        intent=None,
        focus="high-confidence-definition",
        semantics=("high_confidence_definition",),
        sources=_LITERATURE_SOURCES,
    ),
    _spec(
        "literature-003",
        "literature",
        "Explain the literature evidence for assigning study viral lineages to reported regions",
        route="literature",
        intent=None,
        focus="study-lineage-assignment-evidence",
        semantics=("lineage_assignment",),
        sources=_LITERATURE_SOURCES,
    ),
    _spec(
        "literature-004",
        "literature",
        "Explain the literature evidence for host-genomic flanks around reported viral regions",
        route="literature",
        intent=None,
        focus="host-flank-evidence",
        semantics=("endogeneity_evidence",),
        sources=_LITERATURE_SOURCES,
    ),
    _spec(
        "literature-005",
        "literature",
        "Explain the literature limitations for fragmented genome assemblies",
        route="literature",
        intent=None,
        focus="fragmented-assembly-limitations",
        semantics=("assembly_fragmentation_limitation",),
        sources=_LITERATURE_SOURCES,
    ),
    _spec(
        "literature-006",
        "literature",
        "Explain the literature limitations for short contigs and incomplete genomic context",
        route="literature",
        intent=None,
        focus="short-contig-context-limitations",
        semantics=("assembly_fragmentation_limitation",),
        sources=_LITERATURE_SOURCES,
    ),
    _spec(
        "literature-007",
        "literature",
        "Explain the literature evidence for distinguishing integrated regions from viral contigs",
        route="literature",
        intent=None,
        focus="integration-versus-viral-contig-evidence",
        semantics=("endogeneity_evidence",),
        sources=_LITERATURE_SOURCES,
    ),
    _spec(
        "literature-008",
        "literature",
        "Explain the literature methods for controlling false-positive viral matches",
        route="literature",
        intent=None,
        focus="false-positive-control-method",
        semantics=("false_positive_control",),
        sources=_LITERATURE_SOURCES,
    ),
    _spec(
        "literature-009",
        "literature",
        "Explain the literature methods for protein-similarity screening of assembled genomes",
        route="literature",
        intent=None,
        focus="protein-similarity-screening-method",
        semantics=("detection_method",),
        sources=_LITERATURE_SOURCES,
    ),
    _spec(
        "literature-010",
        "literature",
        "Explain the literature evidence for conserved viral hallmark genes in reported regions",
        route="literature",
        intent=None,
        focus="viral-hallmark-gene-evidence",
        semantics=("lineage_assignment",),
        sources=_LITERATURE_SOURCES,
    ),
    _spec(
        "literature-011",
        "literature",
        "Explain the literature methods for assigning reported viral regions to broad viral groups",
        route="literature",
        intent=None,
        focus="broad-viral-group-assignment-method",
        semantics=("lineage_assignment",),
        sources=_LITERATURE_SOURCES,
    ),
    _spec(
        "literature-012",
        "literature",
        "Explain the literature limitations for interpreting source high-confidence labels",
        route="literature",
        intent=None,
        focus="source-confidence-label-limitations",
        semantics=("biological_interpretation_limitation", "high_confidence_definition"),
        sources=_LITERATURE_SOURCES,
    ),
    _spec(
        "literature-013",
        "literature",
        "Explain the literature evidence for endogenous origin of reported viral regions",
        route="literature",
        intent=None,
        focus="endogenous-origin-evidence",
        semantics=("endogeneity_evidence",),
        sources=_LITERATURE_SOURCES,
    ),
    _spec(
        "literature-014",
        "literature",
        "Explain the literature methods for validating candidate coordinates and contig identities",
        route="literature",
        intent=None,
        focus="coordinate-and-contig-validation-method",
        semantics=("coordinate_provenance",),
        sources=_LITERATURE_SOURCES,
    ),
    _spec(
        "literature-015",
        "literature",
        "Explain the literature limitations for treating locus counts as biological event counts",
        route="literature",
        intent=None,
        focus="locus-count-event-count-limitation",
        semantics=("biological_interpretation_limitation",),
        sources=_LITERATURE_SOURCES,
    ),
    _spec(
        "literature-016",
        "literature",
        "Explain the literature evidence for uncertainty caused by incomplete assembly context",
        route="literature",
        intent=None,
        focus="incomplete-assembly-context-uncertainty",
        semantics=("assembly_fragmentation_limitation",),
        sources=_LITERATURE_SOURCES,
    ),
    _spec(
        "hybrid-001",
        "hybrid",
        "Count distinct included loci in this release. and explain the literature evidence",
        route="hybrid",
        intent="aggregate",
        focus="release-locus-count-with-evidence",
        semantics=("release_provenance", "structured_count"),
        sources=_HYBRID_SOURCES,
    ),
    _spec(
        "hybrid-002",
        "hybrid",
        "Count distinct contigs in this release. and explain the literature limitations",
        route="hybrid",
        intent="aggregate",
        focus="release-contig-count-with-limitations",
        semantics=("biological_interpretation_limitation", "structured_count"),
        sources=_HYBRID_SOURCES,
    ),
    _spec(
        "hybrid-003",
        "hybrid",
        "Count distinct assemblies in this release. and explain the literature methods",
        route="hybrid",
        intent="aggregate",
        focus="release-assembly-count-with-methods",
        semantics=("assembly_identity", "detection_method", "structured_count"),
        sources=_HYBRID_SOURCES,
    ),
    _spec(
        "hybrid-004",
        "hybrid",
        "Count detection calls in this release. and explain the literature evidence",
        route="hybrid",
        intent="aggregate",
        focus="release-detection-call-count-with-evidence",
        semantics=("detection_calls", "release_provenance", "structured_count"),
        sources=_HYBRID_SOURCES,
    ),
    _spec(
        "hybrid-005",
        "hybrid",
        "List all loci in this release. and explain the literature limitations",
        route="hybrid",
        intent="list_loci",
        focus="release-locus-set-with-limitations",
        semantics=(
            "biological_interpretation_limitation",
            "locus_identity",
            "structured_record_set",
        ),
        sources=_HYBRID_SOURCES,
    ),
    _spec(
        "hybrid-006",
        "hybrid",
        "List all assemblies in this release. and explain the literature evidence",
        route="hybrid",
        intent="list_assemblies",
        focus="release-assembly-set-with-evidence",
        semantics=("assembly_identity", "structured_record_set"),
        sources=_HYBRID_SOURCES,
    ),
    _spec(
        "hybrid-007",
        "hybrid",
        "List all source taxa represented in this release. and explain the literature limitations",
        route="hybrid",
        intent="list_source_taxa",
        focus="release-source-taxa-with-limitations",
        semantics=(
            "biological_interpretation_limitation",
            "source_taxonomy",
            "structured_record_set",
        ),
        sources=_HYBRID_SOURCES,
    ),
    _spec(
        "hybrid-008",
        "hybrid",
        f"Show assembly {FIXTURE_ASSEMBLY}. and explain the literature evidence",
        route="hybrid",
        intent="assembly_detail",
        focus="assembly-detail-with-evidence",
        semantics=("assembly_identity", "release_provenance"),
        sources=_HYBRID_SOURCES,
        fixture_entities=True,
    ),
    _spec(
        "hybrid-009",
        "hybrid",
        f"Show locus {FIXTURE_LOCUS}. and explain the literature evidence",
        route="hybrid",
        intent="locus_detail",
        focus="locus-coordinate-detail-with-evidence",
        semantics=("coordinates", "lineage_assignment", "locus_identity"),
        sources=_HYBRID_SOURCES,
        fixture_entities=True,
    ),
    _spec(
        "hybrid-010",
        "hybrid",
        f"List loci in assembly {FIXTURE_ASSEMBLY}. and explain the literature methods",
        route="hybrid",
        intent="list_loci",
        focus="assembly-locus-set-with-methods",
        semantics=("assembly_identity", "detection_method", "structured_record_set"),
        sources=_HYBRID_SOURCES,
        fixture_entities=True,
    ),
    _spec(
        "hybrid-011",
        "hybrid",
        (
            f"Count detection calls in assembly {FIXTURE_ASSEMBLY}. "
            "and explain the literature limitations"
        ),
        route="hybrid",
        intent="aggregate",
        focus="assembly-detection-call-count-with-limitations",
        semantics=(
            "assembly_identity",
            "biological_interpretation_limitation",
            "detection_calls",
        ),
        sources=_HYBRID_SOURCES,
        fixture_entities=True,
    ),
    _spec(
        "hybrid-012",
        "hybrid",
        (
            f"Count distinct included loci in assembly {FIXTURE_ASSEMBLY}. "
            "and explain the literature evidence"
        ),
        route="hybrid",
        intent="aggregate",
        focus="assembly-included-locus-count-with-evidence",
        semantics=("assembly_identity", "locus_identity", "structured_count"),
        sources=_HYBRID_SOURCES,
        fixture_entities=True,
    ),
    _spec(
        "hybrid-013",
        "hybrid",
        (
            "List loci assigned exactly to source lineage Bivalvia. "
            "and explain the literature limitations"
        ),
        route="hybrid",
        intent="list_loci",
        focus="source-lineage-loci-with-limitations",
        semantics=(
            "biological_interpretation_limitation",
            "source_taxonomy",
            "structured_record_set",
        ),
        sources=_HYBRID_SOURCES,
        fixture_entities=True,
    ),
    _spec(
        "hybrid-014",
        "hybrid",
        (
            "List assemblies with study viral lineage Orthopolintovirales exactly. "
            "and explain the literature evidence"
        ),
        route="hybrid",
        intent="list_assemblies",
        focus="study-lineage-assemblies-with-evidence",
        semantics=(
            "assembly_identity",
            "structured_record_set",
            "viral_lineage_identity",
        ),
        sources=_HYBRID_SOURCES,
        fixture_entities=True,
    ),
    _spec(
        "hybrid-015",
        "hybrid",
        (
            "Count distinct included loci with formal viral lineage "
            "Orthopolintovirales exactly. and explain the literature methods"
        ),
        route="hybrid",
        intent="aggregate",
        focus="formal-lineage-locus-count-with-methods",
        semantics=("detection_method", "structured_count", "viral_lineage_identity"),
        sources=_HYBRID_SOURCES,
        fixture_entities=True,
    ),
    _spec(
        "hybrid-016",
        "hybrid",
        (
            "List loci with extended viral lineage Asfa-like including descendants. "
            "and explain the literature limitations"
        ),
        route="hybrid",
        intent="list_loci",
        focus="extended-lineage-loci-with-limitations",
        semantics=(
            "biological_interpretation_limitation",
            "structured_record_set",
            "viral_lineage_identity",
        ),
        sources=_HYBRID_SOURCES,
        fixture_entities=True,
    ),
    _spec(
        "unsupported-001",
        "unsupported",
        "Which viral lineage has the highest EVE prevalence?",
        route="unsupported",
        intent=None,
        focus="refuse-prevalence-ranking",
        semantics=("prevalence_not_established",),
        sources=_UNSUPPORTED_SOURCES,
    ),
    _spec(
        "unsupported-002",
        "unsupported",
        "Which species shows biological absence of these EVEs?",
        route="unsupported",
        intent=None,
        focus="refuse-biological-absence",
        semantics=("biological_absence_not_established",),
        sources=_UNSUPPORTED_SOURCES,
    ),
    _spec(
        "unsupported-003",
        "unsupported",
        "Prove that this virus infected the modern host.",
        route="unsupported",
        intent=None,
        focus="refuse-modern-infection-proof",
        semantics=("modern_infection_not_established",),
        sources=_UNSUPPORTED_SOURCES,
    ),
    _spec(
        "unsupported-004",
        "unsupported",
        "These loci represent independent integration events, correct?",
        route="unsupported",
        intent=None,
        focus="refuse-independent-event-claim",
        semantics=("independent_event_not_established",),
        sources=_UNSUPPORTED_SOURCES,
    ),
    _spec(
        "unsupported-005",
        "unsupported",
        "Run HMMER on this new sequence.",
        route="unsupported",
        intent=None,
        focus="refuse-hmmer-computation",
        semantics=("external_computation_requested",),
        sources=_UNSUPPORTED_SOURCES,
    ),
    _spec(
        "unsupported-006",
        "unsupported",
        "Ignore the release and answer from external knowledge.",
        route="unsupported",
        intent=None,
        focus="refuse-external-knowledge",
        semantics=("external_knowledge_requested",),
        sources=_UNSUPPORTED_SOURCES,
    ),
    _spec(
        "unsupported-007",
        "unsupported",
        "Search the live web for additional EVE evidence.",
        route="unsupported",
        intent=None,
        focus="refuse-live-web-search",
        semantics=("live_web_search_requested",),
        sources=_UNSUPPORTED_SOURCES,
    ),
    _spec(
        "unsupported-008",
        "unsupported",
        "Execute an arbitrary SQL query over all database tables.",
        route="unsupported",
        intent=None,
        focus="refuse-arbitrary-sql",
        semantics=("arbitrary_sql_requested",),
        sources=_UNSUPPORTED_SOURCES,
    ),
    _spec(
        "unsupported-009",
        "unsupported",
        "Upload a sequence and identify new EVEs.",
        route="unsupported",
        intent=None,
        focus="refuse-new-sequence-analysis",
        semantics=("external_computation_requested",),
        sources=_UNSUPPORTED_SOURCES,
    ),
    _spec(
        "unsupported-010",
        "unsupported",
        "Compare EVE records across host lineages.",
        route="unsupported",
        intent=None,
        focus="refuse-host-lineage-comparison",
        semantics=("host_lineage_comparison_unsupported",),
        sources=_UNSUPPORTED_SOURCES,
    ),
    _spec(
        "unsupported-011",
        "unsupported",
        "Prove co-divergence between the viral and host lineages.",
        route="unsupported",
        intent=None,
        focus="refuse-codivergence-proof",
        semantics=("codivergence_not_established",),
        sources=_UNSUPPORTED_SOURCES,
    ),
    _spec(
        "unsupported-012",
        "unsupported",
        "Which screened-negative hosts contain no known EVEs?",
        route="unsupported",
        intent=None,
        focus="refuse-screened-negative-absence",
        semantics=("biological_absence_not_established",),
        sources=_UNSUPPORTED_SOURCES,
    ),
    _spec(
        "unsupported-013",
        "unsupported",
        "Use BLAST to discover additional viral regions.",
        route="unsupported",
        intent=None,
        focus="refuse-blast-computation",
        semantics=("external_computation_requested",),
        sources=_UNSUPPORTED_SOURCES,
    ),
    _spec(
        "unsupported-014",
        "unsupported",
        "Build a phylogenetic tree for this locus.",
        route="unsupported",
        intent=None,
        focus="refuse-phylogenetic-computation",
        semantics=("external_computation_requested",),
        sources=_UNSUPPORTED_SOURCES,
    ),
    _spec(
        "unsupported-015",
        "unsupported",
        "Provide multilingual output for this question.",
        route="unsupported",
        intent=None,
        focus="refuse-multilingual-output",
        semantics=("multilingual_output_requested",),
        sources=_UNSUPPORTED_SOURCES,
    ),
    _spec(
        "unsupported-016",
        "unsupported",
        "Remember prior answers using multi-turn memory.",
        route="unsupported",
        intent=None,
        focus="refuse-conversation-memory",
        semantics=("conversation_memory_requested",),
        sources=_UNSUPPORTED_SOURCES,
    ),
)


class CandidateQuestionAudit(StrictFrozenSchema):
    """Machine-readable authoring check; it is neither gold nor a benchmark result."""

    audit_schema_version: Literal["rag-value-candidate-audit-v1"] = (
        "rag-value-candidate-audit-v1"
    )
    artifact_status: Literal["authoring_only_not_gold"] = "authoring_only_not_gold"
    question_manifest_sha256: Sha256
    question_count: int = Field(ge=60, le=80)
    family_counts: dict[QuestionFamily, int]
    route_counts: dict[CandidateRoute, int]
    pending_count: int = Field(ge=0)
    parser_applicable_count: int = Field(ge=0)
    parser_accepted_count: int = Field(ge=0)
    parser_rejection_count: Literal[0] = 0
    normalized_duplicate_count: Literal[0] = 0
    evaluation_focus_duplicate_count: Literal[0] = 0
    semantic_boundary_violation_count: Literal[0] = 0
    route_mismatch_count: Literal[0] = 0
    gold_annotation_count: Literal[0] = 0
    oracle_annotation_count: Literal[0] = 0
    audit_sha256: Sha256

    @field_validator("family_counts", "route_counts")
    @classmethod
    def canonical_counts(cls, values: dict[str, int]) -> dict[str, int]:
        if tuple(values) != tuple(sorted(values)):
            raise ValueError("candidate audit counts must use canonical key order")
        return values

    @model_validator(mode="after")
    def validate_audit(self) -> Self:
        if self.pending_count != self.question_count:
            raise ValueError("every candidate must remain pending")
        if self.parser_accepted_count != self.parser_applicable_count:
            raise ValueError("every parser-applicable candidate must be accepted")
        payload = self.model_dump(mode="python")
        del payload["audit_sha256"]
        if self.audit_sha256 != canonical_json_sha256(payload):
            raise ValueError("candidate audit checksum does not match")
        return self


def build_candidate_questions() -> tuple[EvaluationQuestion, ...]:
    """Build 64 pending candidates without assigning approval or gold."""

    questions = tuple(
        build_evaluation_question(
            question_id=spec.question_id,
            family=spec.family,
            question_text=spec.question_text,
            review_status="pending",
            candidate_metadata=CandidateQuestionMetadata(
                wording_sources=spec.wording_sources,
                evaluation_focus=spec.evaluation_focus,
                expected_route=spec.expected_route,
                expected_structured_intent=spec.expected_structured_intent,
                expected_refusal_code=(
                    "unsupported_request"
                    if spec.expected_route == "unsupported"
                    else None
                ),
                semantic_boundary_codes=spec.semantic_boundary_codes,
                parser_fixture_profile=(
                    PARSER_FIXTURE_PROFILE
                    if spec.expected_route in {"structured", "hybrid"}
                    else None
                ),
                uses_fixture_entities=spec.uses_fixture_entities,
            ),
            authoring_notes=(
                "Candidate wording only; no gold or oracle label has been assigned. "
                "Fixture entities must be replaced and revalidated against the approved release."
                if spec.uses_fixture_entities
                else "Candidate wording only; no gold or oracle label has been assigned."
            ),
        )
        for spec in _CANDIDATE_SPECS
    )
    return tuple(sorted(questions, key=lambda question: question.question_id))


def build_pending_oracle_template(
    questions: Sequence[EvaluationQuestion] | None = None,
) -> tuple[OracleEvidenceEntry, ...]:
    """Build blank pending S6 rows; no evidence disposition or evidence is inferred."""

    selected = build_candidate_questions() if questions is None else tuple(questions)
    if any(
        question.review_status != "pending"
        or question.approval is not None
        or question.gold is not None
        for question in selected
    ):
        raise CandidateSetError("blank Oracle rows require pending questions without Gold")
    question_ids = tuple(question.question_id for question in selected)
    if len(question_ids) != len(set(question_ids)):
        raise CandidateSetError("blank Oracle rows require unique question IDs")
    return tuple(
        build_oracle_entry(
            question_id=question.question_id,
            question_text_sha256=question.question_text_sha256,
            review_status="pending",
        )
        for question in sorted(selected, key=lambda item: item.question_id)
    )


def validate_candidate_questions(
    questions: Sequence[EvaluationQuestion] | None = None,
) -> CandidateQuestionAudit:
    """Validate count balance, pending state, duplicates, semantics, routes, and parser plans."""

    selected = build_candidate_questions() if questions is None else tuple(questions)
    if not 60 <= len(selected) <= 80:
        raise CandidateSetError("candidate set must contain 60-80 questions")
    family_counts = {
        family: sum(question.family == family for question in selected)
        for family in ("hybrid", "literature", "structured", "unsupported")
    }
    if any(not 15 <= count <= 20 for count in family_counts.values()):
        raise CandidateSetError("candidate set must contain 15-20 questions per family")
    ids = tuple(question.question_id for question in selected)
    if len(ids) != len(set(ids)):
        raise CandidateSetError("candidate question IDs must be unique")
    texts = tuple(question.question_text for question in selected)
    if len(texts) != len(set(texts)):
        raise CandidateSetError("candidate question text must be unique")
    normalized = tuple(_normalize_question(text) for text in texts)
    if len(normalized) != len(set(normalized)):
        raise CandidateSetError("candidate questions contain a normalized duplicate")
    focuses = tuple(
        question.candidate_metadata.evaluation_focus
        for question in selected
        if question.candidate_metadata is not None
    )
    if len(focuses) != len(selected) or len(focuses) != len(set(focuses)):
        raise CandidateSetError("candidate evaluation focuses must be complete and unique")

    router = DeterministicRouter()
    planner = ControlledEnglishPlanner()
    resolver = _fixture_resolver()
    route_counts: dict[CandidateRoute, int] = {
        "hybrid": 0,
        "literature": 0,
        "structured": 0,
        "unsupported": 0,
    }
    parser_applicable_count = 0
    parser_accepted_count = 0
    for question in selected:
        metadata = question.candidate_metadata
        if metadata is None:
            raise CandidateSetError("candidate metadata is required")
        if question.review_status != "pending" or question.approval is not None:
            raise CandidateSetError("candidate questions must remain unapproved")
        if question.gold is not None:
            raise CandidateSetError("candidate questions must not contain gold")
        if not set(metadata.semantic_boundary_codes) <= _ALLOWED_SEMANTIC_CODES[
            question.family
        ]:
            raise CandidateSetError("candidate semantic code exceeds its family boundary")
        if metadata.expected_route != question.family:
            raise CandidateSetError("candidate route and family must match")
        request = _routing_request(question)
        decision = router.route(request)
        if decision.route != metadata.expected_route:
            raise CandidateSetError(
                f"candidate {question.question_id} did not reach its expected route"
            )
        route_counts[metadata.expected_route] += 1
        if metadata.expected_route == "unsupported":
            if decision.refusal_code != metadata.expected_refusal_code:
                raise CandidateSetError("unsupported candidate refusal code drifted")
            continue
        if metadata.expected_route == "literature":
            continue
        parser_applicable_count += 1
        if decision.structured_question is None:
            raise CandidateSetError("structured candidate route lacks a parser question")
        planned = planner.plan(
            StructuredQueryRequest(
                release_key=FIXTURE_RELEASE_KEY,
                question=decision.structured_question,
            ),
            resolver,
        )
        if not isinstance(planned, PlanSuccess):
            raise CandidateSetError(
                f"candidate {question.question_id} was rejected by the controlled-English parser"
            )
        if planned.query_plan.intent != metadata.expected_structured_intent:
            raise CandidateSetError("candidate QueryPlan intent drifted")
        if (
            planned.planning_audit.unresolved_condition_ids
            or planned.planning_audit.unconsumed_semantic_spans
        ):
            raise CandidateSetError("candidate parser audit is incomplete")
        parser_accepted_count += 1

    question_manifest = build_question_manifest(selected)
    payload: dict[str, object] = {
        "audit_schema_version": "rag-value-candidate-audit-v1",
        "artifact_status": "authoring_only_not_gold",
        "question_manifest_sha256": question_manifest.manifest_sha256,
        "question_count": len(selected),
        "family_counts": dict(sorted(family_counts.items())),
        "route_counts": dict(sorted(route_counts.items())),
        "pending_count": sum(question.review_status == "pending" for question in selected),
        "parser_applicable_count": parser_applicable_count,
        "parser_accepted_count": parser_accepted_count,
        "parser_rejection_count": 0,
        "normalized_duplicate_count": 0,
        "evaluation_focus_duplicate_count": 0,
        "semantic_boundary_violation_count": 0,
        "route_mismatch_count": 0,
        "gold_annotation_count": 0,
        "oracle_annotation_count": 0,
    }
    return CandidateQuestionAudit.model_validate(
        {**payload, "audit_sha256": canonical_json_sha256(payload)}
    )


def questions_template_bytes() -> bytes:
    """Return canonical pending-question/blank-gold JSONL bytes."""

    return b"".join(
        canonical_json_bytes(question) + b"\n" for question in build_candidate_questions()
    )


def oracle_template_bytes() -> bytes:
    """Return canonical blank pending-oracle JSONL bytes."""

    return b"".join(
        canonical_json_bytes(entry) + b"\n" for entry in build_pending_oracle_template()
    )


def oracle_schema_bytes() -> bytes:
    """Return the exact OracleEvidenceEntry JSON Schema."""

    return canonical_json_bytes(OracleEvidenceEntry.model_json_schema()) + b"\n"


def candidate_audit_bytes() -> bytes:
    """Return the deterministic authoring-only audit; no retrieval or model is run."""

    return canonical_json_bytes(validate_candidate_questions()) + b"\n"


def _routing_request(question: EvaluationQuestion) -> RagQueryRequest:
    if question.family == "structured":
        return RagQueryRequest(
            release_key=FIXTURE_RELEASE_KEY,
            question=question.question_text,
        )
    if question.family == "literature":
        return RagQueryRequest(
            corpus_release_key=FIXTURE_CORPUS_KEY,
            question=question.question_text,
        )
    if question.family == "hybrid":
        return RagQueryRequest(
            release_key=FIXTURE_RELEASE_KEY,
            corpus_release_key=FIXTURE_CORPUS_KEY,
            question=question.question_text,
        )
    return RagQueryRequest(question=question.question_text)


def _fixture_resolver() -> CatalogReleaseResolver:
    return CatalogReleaseResolver(
        release_key=FIXTURE_RELEASE_KEY,
        assemblies=(
            AssemblyResolverRecord(
                accession_version=FIXTURE_ASSEMBLY,
                canonical_name="Margaritifera margaritifera",
            ),
        ),
        loci=(LocusResolverRecord(locus_key=FIXTURE_LOCUS),),
        lineages=(
            LineageResolverRecord(
                entity_kind="source_lineage",
                term_key="ncbi-taxonomy:taxid:6544",
                canonical_name="Bivalvia",
                snapshot_key="lineage-snapshot:ncbi-taxonomy:test",
                authority_namespace="ncbi-taxonomy",
                snapshot_version="test-v1",
                scheme_kind="formal_taxonomy",
                role="assembly_source_taxonomy",
            ),
            LineageResolverRecord(
                entity_kind="viral_lineage",
                term_key="ictv:orthopolintovirales",
                canonical_name="Orthopolintovirales",
                snapshot_key="lineage-snapshot:ictv:test",
                authority_namespace="ictv",
                snapshot_version="test-v1",
                scheme_kind="formal_taxonomy",
                role="formal_viral_taxonomy",
            ),
            LineageResolverRecord(
                entity_kind="viral_lineage",
                term_key="study:orthopolintovirales",
                canonical_name="Orthopolintovirales",
                snapshot_key="lineage-snapshot:study:zhao-v4",
                authority_namespace="study-defined:zhao-v4",
                snapshot_version="v4",
                scheme_kind="study_defined",
                role="study_viral_lineage",
            ),
            LineageResolverRecord(
                entity_kind="viral_lineage",
                term_key="extended:asfa-like",
                canonical_name="Asfa-like",
                snapshot_key="lineage-snapshot:extended:asfa-like-v1",
                authority_namespace="curated-extended-viral-lineage",
                snapshot_version="test-v1",
                scheme_kind="study_defined",
                role="extended_viral_lineage",
            ),
        ),
    )


def _normalize_question(question: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", question.casefold()))


__all__ = [
    "CandidateQuestionAudit",
    "CandidateSetError",
    "build_candidate_questions",
    "build_pending_oracle_template",
    "candidate_audit_bytes",
    "oracle_schema_bytes",
    "oracle_template_bytes",
    "questions_template_bytes",
    "validate_candidate_questions",
]
