"""Manually curated, tests-only gold cases for the M2 planning/contract benchmark.

The release capability and catalog in this module are synthetic test doubles.  They are
deliberately named as such and must never be interpreted as a real published EndoViHo release.
Production SQL fact paths are verified separately by the PostgreSQL matrix under
``tests/retrieval/structured``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

RELEASE_KEY = "release:endoviho-rag:v0:20991231:999"
CATALOG_KEY = "tests-only:m2-gold-catalog:v1"
CAPABILITY_KIND = "tests_only_synthetic_published_capability"

SOURCE_SNAPSHOT = "snapshot:ncbi-taxonomy:synthetic-v1"
FORMAL_SNAPSHOT = "snapshot:ictv:synthetic-v1"
STUDY_SNAPSHOT = "snapshot:study-zhao-v4:synthetic-v1"

BIVALVIA = "ncbi-taxonomy:taxid:6544"
MYTILIDA = "ncbi-taxonomy:taxid:6545"
OSTREIDA = "ncbi-taxonomy:taxid:6565"
UNIONIDA = "ncbi-taxonomy:taxid:10236"
PECTINIDA = "ncbi-taxonomy:taxid:7777"
ORTHOPOLINTOVIRALES = "ictv:2025:orthopolintovirales"
ADENOVIRIDAE = "ictv:2025:adenoviridae"
STUDY_POLINTON = "study:zhao-v4:orthopolintovirales"
STUDY_MAVERICK = "study:zhao-v4:maverick-like"

A1 = "GCA_900000001.1"
A2 = "GCA_900000002.1"
A3 = "GCF_900000003.2"
A4 = "GCA_900000004.3"

L1 = f"locus:eve:v1:sha256:{'a' * 64}"
L2 = f"locus:eve:v1:sha256:{'b' * 64}"
L3 = f"locus:eve:v1:sha256:{'c' * 64}"
L4 = f"locus:eve:v1:sha256:{'d' * 64}"
L5 = f"locus:eve:v1:sha256:{'e' * 64}"
L6 = f"locus:eve:v1:sha256:{'f' * 64}"
L7 = f"locus:eve:v1:sha256:{'1' * 64}"
L8 = f"locus:eve:v1:sha256:{'2' * 64}"

ASSEMBLIES: dict[str, str] = {
    A1: "Synthetic bivalve alpha",
    A2: "Synthetic oyster beta",
    A3: "Synthetic mussel gamma",
    A4: "Synthetic scallop delta",
}

LINEAGES: tuple[dict[str, Any], ...] = (
    {
        "entity_kind": "source_lineage",
        "term_key": BIVALVIA,
        "canonical_name": "Bivalvia",
        "aliases": ("Pelecypoda",),
        "snapshot_key": SOURCE_SNAPSHOT,
        "authority_namespace": "ncbi-taxonomy",
        "snapshot_version": "synthetic-v1",
        "scheme_kind": "formal_taxonomy",
        "role": "assembly_source_taxonomy",
    },
    {
        "entity_kind": "source_lineage",
        "term_key": MYTILIDA,
        "canonical_name": "Mytilida",
        "aliases": (),
        "snapshot_key": SOURCE_SNAPSHOT,
        "authority_namespace": "ncbi-taxonomy",
        "snapshot_version": "synthetic-v1",
        "scheme_kind": "formal_taxonomy",
        "role": "assembly_source_taxonomy",
    },
    {
        "entity_kind": "source_lineage",
        "term_key": OSTREIDA,
        "canonical_name": "Ostreida",
        "aliases": (),
        "snapshot_key": SOURCE_SNAPSHOT,
        "authority_namespace": "ncbi-taxonomy",
        "snapshot_version": "synthetic-v1",
        "scheme_kind": "formal_taxonomy",
        "role": "assembly_source_taxonomy",
    },
    {
        "entity_kind": "source_lineage",
        "term_key": UNIONIDA,
        "canonical_name": "Unionida",
        "aliases": (),
        "snapshot_key": SOURCE_SNAPSHOT,
        "authority_namespace": "ncbi-taxonomy",
        "snapshot_version": "synthetic-v1",
        "scheme_kind": "formal_taxonomy",
        "role": "assembly_source_taxonomy",
    },
    {
        "entity_kind": "source_lineage",
        "term_key": PECTINIDA,
        "canonical_name": "Pectinida",
        "aliases": (),
        "snapshot_key": SOURCE_SNAPSHOT,
        "authority_namespace": "ncbi-taxonomy",
        "snapshot_version": "synthetic-v1",
        "scheme_kind": "formal_taxonomy",
        "role": "assembly_source_taxonomy",
    },
    {
        "entity_kind": "viral_lineage",
        "term_key": ORTHOPOLINTOVIRALES,
        "canonical_name": "Orthopolintovirales",
        "aliases": (),
        "snapshot_key": FORMAL_SNAPSHOT,
        "authority_namespace": "ictv",
        "snapshot_version": "synthetic-v1",
        "scheme_kind": "formal_taxonomy",
        "role": "formal_viral_taxonomy",
    },
    {
        "entity_kind": "viral_lineage",
        "term_key": ADENOVIRIDAE,
        "canonical_name": "Adenoviridae",
        "aliases": (),
        "snapshot_key": FORMAL_SNAPSHOT,
        "authority_namespace": "ictv",
        "snapshot_version": "synthetic-v1",
        "scheme_kind": "formal_taxonomy",
        "role": "formal_viral_taxonomy",
    },
    {
        "entity_kind": "viral_lineage",
        "term_key": STUDY_POLINTON,
        "canonical_name": "Orthopolintovirales",
        "aliases": ("Polinton-like",),
        "snapshot_key": STUDY_SNAPSHOT,
        "authority_namespace": "study-defined:zhao-v4",
        "snapshot_version": "synthetic-v1",
        "scheme_kind": "study_defined",
        "role": "study_viral_lineage",
    },
    {
        "entity_kind": "viral_lineage",
        "term_key": STUDY_MAVERICK,
        "canonical_name": "Maverick-like",
        "aliases": (),
        "snapshot_key": STUDY_SNAPSHOT,
        "authority_namespace": "study-defined:zhao-v4",
        "snapshot_version": "synthetic-v1",
        "scheme_kind": "study_defined",
        "role": "study_viral_lineage",
    },
)


@dataclass(frozen=True, slots=True)
class SyntheticLocus:
    locus_key: str
    assembly_accession: str
    contig_accession: str
    start0: int
    end0: int
    source_term: str
    source_ancestors: tuple[str, ...]
    viral_terms: tuple[tuple[str, str], ...]
    source_record_keys: tuple[str, ...]


LOCI: tuple[SyntheticLocus, ...] = (
    SyntheticLocus(
        L1,
        A1,
        "CM900001.1",
        100,
        400,
        MYTILIDA,
        (MYTILIDA, BIVALVIA),
        (("formal_viral_taxonomy", ORTHOPOLINTOVIRALES), ("study_viral_lineage", STUDY_POLINTON)),
        ("source-record:001", "source-record:002"),
    ),
    SyntheticLocus(
        L2,
        A1,
        "CM900001.1",
        500,
        800,
        MYTILIDA,
        (MYTILIDA, BIVALVIA),
        (("formal_viral_taxonomy", ORTHOPOLINTOVIRALES),),
        ("source-record:003",),
    ),
    SyntheticLocus(
        L3,
        A2,
        "CM900002.1",
        1000,
        1500,
        OSTREIDA,
        (OSTREIDA, BIVALVIA),
        (("formal_viral_taxonomy", ORTHOPOLINTOVIRALES), ("study_viral_lineage", STUDY_POLINTON)),
        ("source-record:004", "source-record:005", "source-record:006"),
    ),
    SyntheticLocus(
        L4,
        A2,
        "CM900003.1",
        2000,
        2600,
        OSTREIDA,
        (OSTREIDA, BIVALVIA),
        (("study_viral_lineage", STUDY_MAVERICK),),
        ("source-record:007",),
    ),
    SyntheticLocus(
        L5,
        A3,
        "CM900004.1",
        3000,
        3500,
        UNIONIDA,
        (UNIONIDA, BIVALVIA),
        (("formal_viral_taxonomy", ORTHOPOLINTOVIRALES), ("study_viral_lineage", STUDY_MAVERICK)),
        ("source-record:008", "source-record:009"),
    ),
    SyntheticLocus(
        L6,
        A3,
        "CM900004.1",
        4000,
        4500,
        UNIONIDA,
        (UNIONIDA, BIVALVIA),
        (("formal_viral_taxonomy", ADENOVIRIDAE),),
        ("source-record:010",),
    ),
    SyntheticLocus(
        L7,
        A4,
        "CM900005.1",
        5000,
        5600,
        PECTINIDA,
        (PECTINIDA, BIVALVIA),
        (("formal_viral_taxonomy", ORTHOPOLINTOVIRALES), ("study_viral_lineage", STUDY_POLINTON)),
        ("source-record:011",),
    ),
    SyntheticLocus(
        L8,
        A4,
        "CM900006.1",
        6000,
        6800,
        PECTINIDA,
        (PECTINIDA, BIVALVIA),
        (("formal_viral_taxonomy", ADENOVIRIDAE), ("study_viral_lineage", STUDY_MAVERICK)),
        ("source-record:012", "source-record:013"),
    ),
)

type Category = Literal[
    "assembly_detail",
    "locus_detail",
    "source_lineage",
    "viral_lineage",
    "combined",
    "aggregate",
    "invalid",
]


@dataclass(frozen=True, slots=True)
class GoldCase:
    case_id: str
    category: Category
    request: dict[str, Any]
    expected_http_status: int
    expected_response_kind: Literal["plan_success", "error"]
    expected_intent: str | None
    expected_resolved_entities: tuple[dict[str, Any], ...]
    expected_canonical_plan: dict[str, Any] | None
    exact_result_keys: dict[str, tuple[str, ...]]
    exact_numbers: dict[str, int]
    limitations: tuple[str, ...]
    provenance: dict[str, Any]
    applied_constraints: tuple[str, ...]
    expected_error_code: str | None = None


def _request(question: str) -> dict[str, Any]:
    return {
        "request_schema_version": "structured-query-request-v1",
        "release_key": RELEASE_KEY,
        "question": question,
        "page": None,
    }


def _assembly_filter(accession: str) -> dict[str, Any]:
    return {"filter_type": "assembly", "assembly_key": f"assembly:ncbi:{accession}"}


def _locus_filter(locus_key: str) -> dict[str, Any]:
    return {"filter_type": "locus", "locus_key": locus_key}


def _source_filter(term_key: str, *, descendants: bool) -> dict[str, Any]:
    return {
        "filter_type": "source_lineage",
        "snapshot_key": SOURCE_SNAPSHOT,
        "term_key": term_key,
        "role": "assembly_source_taxonomy",
        "include_descendants": descendants,
    }


def _viral_filter(
    term_key: str,
    *,
    role: Literal["formal_viral_taxonomy", "study_viral_lineage"],
    descendants: bool,
) -> dict[str, Any]:
    return {
        "filter_type": "viral_lineage",
        "snapshot_key": FORMAL_SNAPSHOT if role == "formal_viral_taxonomy" else STUDY_SNAPSHOT,
        "term_key": term_key,
        "role": role,
        "include_descendants": descendants,
    }


def _entity_for_assembly(accession: str) -> dict[str, Any]:
    return {
        "original_input": accession,
        "entity_kind": "assembly",
        "match_mode": "exact_identifier",
        "stable_key": f"assembly:ncbi:{accession}",
        "canonical_name": ASSEMBLIES[accession],
        "snapshot_key": None,
        "authority_namespace": None,
        "snapshot_version": None,
        "scheme_kind": None,
        "role": None,
    }


def _entity_for_locus(locus_key: str) -> dict[str, Any]:
    return {
        "original_input": locus_key,
        "entity_kind": "locus",
        "match_mode": "exact_stable_key",
        "stable_key": locus_key,
        "canonical_name": f"Synthetic locus {locus_key[-1].upper()}",
        "snapshot_key": None,
        "authority_namespace": None,
        "snapshot_version": None,
        "scheme_kind": None,
        "role": None,
    }


def _entity_for_lineage(
    original_input: str,
    term_key: str,
    *,
    match_mode: Literal["exact_canonical_name", "exact_curated_alias"] = "exact_canonical_name",
) -> dict[str, Any]:
    record = next(item for item in LINEAGES if item["term_key"] == term_key)
    return {
        "original_input": original_input,
        "entity_kind": record["entity_kind"],
        "match_mode": match_mode,
        "stable_key": term_key,
        "canonical_name": record["canonical_name"],
        "snapshot_key": record["snapshot_key"],
        "authority_namespace": record["authority_namespace"],
        "snapshot_version": record["snapshot_version"],
        "scheme_kind": record["scheme_kind"],
        "role": record["role"],
    }


def _plan(
    question: str,
    intent: str,
    *,
    filters: tuple[dict[str, Any], ...] = (),
    metric_key: str | None = None,
) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "plan_version": "endoviho-query-plan-v0.1",
        "route": "structured",
        "release_key": RELEASE_KEY,
        "original_question": question,
        "scope": (
            {"scope_type": "filtered", "filters": list(filters)}
            if filters
            else {"scope_type": "entire_release", "explicitly_requested": True}
        ),
        "intent": intent,
    }
    if intent in {"list_loci", "list_assemblies", "list_source_taxa"}:
        plan["page"] = {"limit": 50, "cursor": None}
    if metric_key is not None:
        plan["metric_key"] = metric_key
    return plan


def _constraint_for_filter(query_filter: dict[str, Any]) -> str:
    filter_type = query_filter["filter_type"]
    if filter_type == "assembly":
        return f"assembly={query_filter['assembly_key']}"
    if filter_type == "locus":
        return f"locus={query_filter['locus_key']}"
    return (
        f"{filter_type}={query_filter['snapshot_key']}:{query_filter['term_key']}:"
        f"{query_filter['role']}:descendants={str(query_filter['include_descendants']).lower()}"
    )


def _provenance(matched_loci: tuple[str, ...]) -> dict[str, Any]:
    source_records = sorted(
        record_key
        for locus in LOCI
        if locus.locus_key in matched_loci
        for record_key in locus.source_record_keys
    )
    return {
        "capability_kind": CAPABILITY_KIND,
        "catalog_key": CATALOG_KEY,
        "release_key": RELEASE_KEY,
        "real_public_release": False,
        "source_record_keys": tuple(source_records),
    }


def _success(
    case_id: str,
    category: Category,
    question: str,
    intent: str,
    *,
    filters: tuple[dict[str, Any], ...] = (),
    resolved_entities: tuple[dict[str, Any], ...] = (),
    item_keys: tuple[str, ...],
    matched_loci: tuple[str, ...],
    exact_numbers: dict[str, int],
    limitations: tuple[str, ...],
    metric_key: str | None = None,
) -> GoldCase:
    constraints = (
        "public_membership:release_assertion",
        "public_membership:release_locus",
        f"release_key={RELEASE_KEY}",
        *(_constraint_for_filter(query_filter) for query_filter in filters),
    )
    return GoldCase(
        case_id=case_id,
        category=category,
        request=_request(question),
        expected_http_status=200,
        expected_response_kind="plan_success",
        expected_intent=intent,
        expected_resolved_entities=resolved_entities,
        expected_canonical_plan=_plan(
            question,
            intent,
            filters=filters,
            metric_key=metric_key,
        ),
        exact_result_keys={
            "items": tuple(sorted(item_keys)),
            "matched_loci": tuple(sorted(matched_loci)),
        },
        exact_numbers=exact_numbers,
        limitations=tuple(sorted(limitations)),
        provenance=_provenance(matched_loci),
        applied_constraints=tuple(sorted(constraints)),
    )


def _invalid(
    case_id: str,
    question: str,
    error_code: str,
    *,
    http_status: int,
) -> GoldCase:
    return GoldCase(
        case_id=case_id,
        category="invalid",
        request=_request(question),
        expected_http_status=http_status,
        expected_response_kind="error",
        expected_intent=None,
        expected_resolved_entities=(),
        expected_canonical_plan=None,
        exact_result_keys={"items": (), "matched_loci": ()},
        exact_numbers={},
        limitations=(),
        provenance={
            "capability_kind": CAPABILITY_KIND,
            "catalog_key": CATALOG_KEY,
            "release_key": RELEASE_KEY,
            "real_public_release": False,
            "source_record_keys": (),
        },
        applied_constraints=("fact_retrieval:forbidden",),
        expected_error_code=error_code,
    )


ASSEMBLY_LIMITATION = ("assembly_source_taxon_is_not_ancient_host",)
LOCUS_DETAIL_LIMITATIONS = (
    "assembly_local_locus_is_not_independent_integration_event",
    "assembly_source_taxon_is_not_ancient_host",
    "coordinates_are_zero_based_half_open",
    "detection_calls_are_not_loci",
    "source_confidence_is_not_release_validation",
)
LOCUS_LIST_LIMITATIONS = (
    "assembly_local_locus_is_not_independent_integration_event",
    "assembly_source_taxon_is_not_ancient_host",
    "coordinates_are_zero_based_half_open",
)

CASES: tuple[GoldCase, ...] = (
    _success(
        "assembly-detail-01",
        "assembly_detail",
        f"Show assembly {A1}.",
        "assembly_detail",
        filters=(_assembly_filter(A1),),
        resolved_entities=(_entity_for_assembly(A1),),
        item_keys=(f"assembly:ncbi:{A1}",),
        matched_loci=(L1, L2),
        exact_numbers={
            "included_locus_count": 2,
            "distinct_contig_count": 1,
            "detection_call_count": 3,
        },
        limitations=ASSEMBLY_LIMITATION,
    ),
    _success(
        "assembly-detail-02",
        "assembly_detail",
        f"Show assembly {A2}.",
        "assembly_detail",
        filters=(_assembly_filter(A2),),
        resolved_entities=(_entity_for_assembly(A2),),
        item_keys=(f"assembly:ncbi:{A2}",),
        matched_loci=(L3, L4),
        exact_numbers={
            "included_locus_count": 2,
            "distinct_contig_count": 2,
            "detection_call_count": 4,
        },
        limitations=ASSEMBLY_LIMITATION,
    ),
    _success(
        "assembly-detail-03",
        "assembly_detail",
        f"Show assembly {A3}.",
        "assembly_detail",
        filters=(_assembly_filter(A3),),
        resolved_entities=(_entity_for_assembly(A3),),
        item_keys=(f"assembly:ncbi:{A3}",),
        matched_loci=(L5, L6),
        exact_numbers={
            "included_locus_count": 2,
            "distinct_contig_count": 1,
            "detection_call_count": 3,
        },
        limitations=ASSEMBLY_LIMITATION,
    ),
    _success(
        "assembly-detail-04",
        "assembly_detail",
        f"Show assembly {A4}.",
        "assembly_detail",
        filters=(_assembly_filter(A4),),
        resolved_entities=(_entity_for_assembly(A4),),
        item_keys=(f"assembly:ncbi:{A4}",),
        matched_loci=(L7, L8),
        exact_numbers={
            "included_locus_count": 2,
            "distinct_contig_count": 2,
            "detection_call_count": 3,
        },
        limitations=ASSEMBLY_LIMITATION,
    ),
    _success(
        "locus-detail-01",
        "locus_detail",
        f"Show locus {L1}.",
        "locus_detail",
        filters=(_locus_filter(L1),),
        resolved_entities=(_entity_for_locus(L1),),
        item_keys=(L1,),
        matched_loci=(L1,),
        exact_numbers={"interval_length": 300, "detection_call_count": 2},
        limitations=LOCUS_DETAIL_LIMITATIONS,
    ),
    _success(
        "locus-detail-02",
        "locus_detail",
        f"Show locus {L2}.",
        "locus_detail",
        filters=(_locus_filter(L2),),
        resolved_entities=(_entity_for_locus(L2),),
        item_keys=(L2,),
        matched_loci=(L2,),
        exact_numbers={"interval_length": 300, "detection_call_count": 1},
        limitations=LOCUS_DETAIL_LIMITATIONS,
    ),
    _success(
        "locus-detail-03",
        "locus_detail",
        f"Show locus {L3}.",
        "locus_detail",
        filters=(_locus_filter(L3),),
        resolved_entities=(_entity_for_locus(L3),),
        item_keys=(L3,),
        matched_loci=(L3,),
        exact_numbers={"interval_length": 500, "detection_call_count": 3},
        limitations=LOCUS_DETAIL_LIMITATIONS,
    ),
    _success(
        "locus-detail-04",
        "locus_detail",
        f"Show locus {L4}.",
        "locus_detail",
        filters=(_locus_filter(L4),),
        resolved_entities=(_entity_for_locus(L4),),
        item_keys=(L4,),
        matched_loci=(L4,),
        exact_numbers={"interval_length": 600, "detection_call_count": 1},
        limitations=LOCUS_DETAIL_LIMITATIONS,
    ),
    _success(
        "source-lineage-01",
        "source_lineage",
        "List loci assigned exactly to source lineage Mytilida.",
        "list_loci",
        filters=(_source_filter(MYTILIDA, descendants=False),),
        resolved_entities=(_entity_for_lineage("Mytilida", MYTILIDA),),
        item_keys=(L1, L2),
        matched_loci=(L1, L2),
        exact_numbers={"total_count": 2},
        limitations=LOCUS_LIST_LIMITATIONS,
    ),
    _success(
        "source-lineage-02",
        "source_lineage",
        "List assemblies assigned to source lineage Pelecypoda including descendants.",
        "list_assemblies",
        filters=(_source_filter(BIVALVIA, descendants=True),),
        resolved_entities=(
            _entity_for_lineage("Pelecypoda", BIVALVIA, match_mode="exact_curated_alias"),
        ),
        item_keys=tuple(f"assembly:ncbi:{item}" for item in (A1, A2, A3, A4)),
        matched_loci=(L1, L2, L3, L4, L5, L6, L7, L8),
        exact_numbers={"total_count": 4},
        limitations=ASSEMBLY_LIMITATION,
    ),
    _success(
        "source-lineage-03",
        "source_lineage",
        "List loci assigned to source lineage Ostreida including descendants.",
        "list_loci",
        filters=(_source_filter(OSTREIDA, descendants=True),),
        resolved_entities=(_entity_for_lineage("Ostreida", OSTREIDA),),
        item_keys=(L3, L4),
        matched_loci=(L3, L4),
        exact_numbers={"total_count": 2},
        limitations=LOCUS_LIST_LIMITATIONS,
    ),
    _success(
        "source-lineage-04",
        "source_lineage",
        "List assemblies assigned exactly to source lineage Unionida.",
        "list_assemblies",
        filters=(_source_filter(UNIONIDA, descendants=False),),
        resolved_entities=(_entity_for_lineage("Unionida", UNIONIDA),),
        item_keys=(f"assembly:ncbi:{A3}",),
        matched_loci=(L5, L6),
        exact_numbers={"total_count": 1},
        limitations=ASSEMBLY_LIMITATION,
    ),
    _success(
        "viral-lineage-01",
        "viral_lineage",
        "List loci with formal viral lineage Orthopolintovirales exactly.",
        "list_loci",
        filters=(
            _viral_filter(ORTHOPOLINTOVIRALES, role="formal_viral_taxonomy", descendants=False),
        ),
        resolved_entities=(_entity_for_lineage("Orthopolintovirales", ORTHOPOLINTOVIRALES),),
        item_keys=(L1, L2, L3, L5, L7),
        matched_loci=(L1, L2, L3, L5, L7),
        exact_numbers={"total_count": 5},
        limitations=LOCUS_LIST_LIMITATIONS,
    ),
    _success(
        "viral-lineage-02",
        "viral_lineage",
        "List loci with formal viral lineage Adenoviridae exactly.",
        "list_loci",
        filters=(_viral_filter(ADENOVIRIDAE, role="formal_viral_taxonomy", descendants=False),),
        resolved_entities=(_entity_for_lineage("Adenoviridae", ADENOVIRIDAE),),
        item_keys=(L6, L8),
        matched_loci=(L6, L8),
        exact_numbers={"total_count": 2},
        limitations=LOCUS_LIST_LIMITATIONS,
    ),
    _success(
        "viral-lineage-03",
        "viral_lineage",
        "List loci with study viral lineage Orthopolintovirales exactly.",
        "list_loci",
        filters=(_viral_filter(STUDY_POLINTON, role="study_viral_lineage", descendants=False),),
        resolved_entities=(_entity_for_lineage("Orthopolintovirales", STUDY_POLINTON),),
        item_keys=(L1, L3, L7),
        matched_loci=(L1, L3, L7),
        exact_numbers={"total_count": 3},
        limitations=LOCUS_LIST_LIMITATIONS,
    ),
    _success(
        "viral-lineage-04",
        "viral_lineage",
        "List source taxa with study viral lineage Maverick-like exactly.",
        "list_source_taxa",
        filters=(_viral_filter(STUDY_MAVERICK, role="study_viral_lineage", descendants=False),),
        resolved_entities=(_entity_for_lineage("Maverick-like", STUDY_MAVERICK),),
        item_keys=(OSTREIDA, UNIONIDA, PECTINIDA),
        matched_loci=(L4, L5, L8),
        exact_numbers={"total_count": 3},
        limitations=ASSEMBLY_LIMITATION,
    ),
    _success(
        "combined-01",
        "combined",
        "List loci assigned exactly to source lineage Mytilida and with formal viral "
        "lineage Orthopolintovirales exactly.",
        "list_loci",
        filters=(
            _source_filter(MYTILIDA, descendants=False),
            _viral_filter(ORTHOPOLINTOVIRALES, role="formal_viral_taxonomy", descendants=False),
        ),
        resolved_entities=(
            _entity_for_lineage("Mytilida", MYTILIDA),
            _entity_for_lineage("Orthopolintovirales", ORTHOPOLINTOVIRALES),
        ),
        item_keys=(L1, L2),
        matched_loci=(L1, L2),
        exact_numbers={"total_count": 2},
        limitations=LOCUS_LIST_LIMITATIONS,
    ),
    _success(
        "combined-02",
        "combined",
        "List loci assigned to source lineage Ostreida including descendants and with study "
        "viral lineage Orthopolintovirales exactly.",
        "list_loci",
        filters=(
            _source_filter(OSTREIDA, descendants=True),
            _viral_filter(STUDY_POLINTON, role="study_viral_lineage", descendants=False),
        ),
        resolved_entities=(
            _entity_for_lineage("Ostreida", OSTREIDA),
            _entity_for_lineage("Orthopolintovirales", STUDY_POLINTON),
        ),
        item_keys=(L3,),
        matched_loci=(L3,),
        exact_numbers={"total_count": 1},
        limitations=LOCUS_LIST_LIMITATIONS,
    ),
    _success(
        "combined-03",
        "combined",
        "List loci assigned exactly to source lineage Unionida and with formal viral lineage "
        "Adenoviridae exactly.",
        "list_loci",
        filters=(
            _source_filter(UNIONIDA, descendants=False),
            _viral_filter(ADENOVIRIDAE, role="formal_viral_taxonomy", descendants=False),
        ),
        resolved_entities=(
            _entity_for_lineage("Unionida", UNIONIDA),
            _entity_for_lineage("Adenoviridae", ADENOVIRIDAE),
        ),
        item_keys=(L6,),
        matched_loci=(L6,),
        exact_numbers={"total_count": 1},
        limitations=LOCUS_LIST_LIMITATIONS,
    ),
    _success(
        "combined-04",
        "combined",
        "List loci assigned to source lineage Bivalvia including descendants and with study "
        "viral lineage Maverick-like exactly.",
        "list_loci",
        filters=(
            _source_filter(BIVALVIA, descendants=True),
            _viral_filter(STUDY_MAVERICK, role="study_viral_lineage", descendants=False),
        ),
        resolved_entities=(
            _entity_for_lineage("Bivalvia", BIVALVIA),
            _entity_for_lineage("Maverick-like", STUDY_MAVERICK),
        ),
        item_keys=(L4, L5, L8),
        matched_loci=(L4, L5, L8),
        exact_numbers={"total_count": 3},
        limitations=LOCUS_LIST_LIMITATIONS,
    ),
    _success(
        "combined-05",
        "combined",
        "List loci assigned exactly to source lineage Pectinida and with formal viral lineage "
        "Orthopolintovirales exactly.",
        "list_loci",
        filters=(
            _source_filter(PECTINIDA, descendants=False),
            _viral_filter(ORTHOPOLINTOVIRALES, role="formal_viral_taxonomy", descendants=False),
        ),
        resolved_entities=(
            _entity_for_lineage("Pectinida", PECTINIDA),
            _entity_for_lineage("Orthopolintovirales", ORTHOPOLINTOVIRALES),
        ),
        item_keys=(L7,),
        matched_loci=(L7,),
        exact_numbers={"total_count": 1},
        limitations=LOCUS_LIST_LIMITATIONS,
    ),
    _success(
        "aggregate-01",
        "aggregate",
        "Count distinct included loci in this release.",
        "aggregate",
        item_keys=(),
        matched_loci=(L1, L2, L3, L4, L5, L6, L7, L8),
        exact_numbers={"metric_value": 8},
        limitations=("assembly_local_locus_is_not_independent_integration_event",),
        metric_key="distinct_included_locus_count",
    ),
    _success(
        "aggregate-02",
        "aggregate",
        "Count distinct contigs in this release.",
        "aggregate",
        item_keys=(),
        matched_loci=(L1, L2, L3, L4, L5, L6, L7, L8),
        exact_numbers={"metric_value": 6},
        limitations=("assembly_local_locus_is_not_independent_integration_event",),
        metric_key="distinct_contig_count",
    ),
    _success(
        "aggregate-03",
        "aggregate",
        "Count distinct assemblies with formal viral lineage Orthopolintovirales exactly.",
        "aggregate",
        filters=(
            _viral_filter(ORTHOPOLINTOVIRALES, role="formal_viral_taxonomy", descendants=False),
        ),
        resolved_entities=(_entity_for_lineage("Orthopolintovirales", ORTHOPOLINTOVIRALES),),
        item_keys=(),
        matched_loci=(L1, L2, L3, L5, L7),
        exact_numbers={"metric_value": 4},
        limitations=(),
        metric_key="distinct_assembly_count",
    ),
    _success(
        "aggregate-04",
        "aggregate",
        "Count distinct source taxa with study viral lineage Orthopolintovirales exactly.",
        "aggregate",
        filters=(_viral_filter(STUDY_POLINTON, role="study_viral_lineage", descendants=False),),
        resolved_entities=(_entity_for_lineage("Orthopolintovirales", STUDY_POLINTON),),
        item_keys=(),
        matched_loci=(L1, L3, L7),
        exact_numbers={"metric_value": 3},
        limitations=("assembly_source_taxon_is_not_ancient_host",),
        metric_key="distinct_source_taxon_count",
    ),
    _success(
        "aggregate-05",
        "aggregate",
        f"Count detection calls in assembly {A1}.",
        "aggregate",
        filters=(_assembly_filter(A1),),
        resolved_entities=(_entity_for_assembly(A1),),
        item_keys=(),
        matched_loci=(L1, L2),
        exact_numbers={"metric_value": 3},
        limitations=("detection_calls_are_not_loci",),
        metric_key="detection_call_count",
    ),
    _invalid(
        "invalid-01",
        "Show assembly GCA_900000001.",
        "assembly_accession_version_required",
        http_status=422,
    ),
    _invalid(
        "invalid-02",
        "List loci with viral lineage Orthopolintovirales exactly.",
        "lineage_role_ambiguous",
        http_status=422,
    ),
    _invalid(
        "invalid-03",
        f"List loci in assembly {A1} or in assembly {A2}.",
        "unsupported_question",
        http_status=422,
    ),
    _invalid(
        "invalid-04",
        "List loci assigned to source lineage Bivalvia.",
        "lineage_scope_ambiguous",
        http_status=422,
    ),
    _invalid(
        "invalid-05",
        "Show assembly GCA_999999999.1.",
        "entity_not_in_release",
        http_status=404,
    ),
)
