from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from eve_relation_rag.experiments.rag_value_ablation.associations import (
    ASSOCIATION_DIMENSIONS,
    FORBIDDEN_AUTOMATIC_MAPPINGS,
    PRESERVED_SOURCE_FIELDS,
    AssemblySourceTaxonBinding,
    AssociationContractV1,
    CrossSourceAssociation,
    ExactAssociation,
    LiteratureEvidenceSource,
    SourceRecordAnnotations,
    SourceReportedAssociation,
    StructuredEvidenceSource,
    ViralLineageAffinity,
    association_contract_v1_bytes,
    association_sort_key,
    build_association_contract_v1,
)
from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    EvidenceGroup,
    HybridGold,
    LiteratureGold,
    StructuredGold,
)
from eve_relation_rag.experiments.rag_value_ablation.metrics import (
    StructuredPrediction,
    score_association_set,
    score_structured,
)

DOCUMENT = f"document:sha256:{'a' * 64}"
CHUNK = f"chunk:sha256:{'b' * 64}"
RELEASE = "release:test:v0:20990101:001"
RELEASE_SHA256 = "d" * 64
BENCHMARK_DIRECTORY = Path(__file__).resolve().parents[2] / "benchmark" / "rag_value_ablation"


def test_v1_contract_uses_only_supported_dimensions_and_preserves_source_fields() -> None:
    contract = build_association_contract_v1()

    assert contract.dimensions == ASSOCIATION_DIMENSIONS == (
        "assembly_source_taxon",
        "eve_locus_or_reported_viral_region",
        "viral_lineage_affinity",
        "evidence_and_source",
    )
    assert contract.relation_class_required is False
    assert contract.preserved_source_fields == PRESERVED_SOURCE_FIELDS == (
        "HCVR",
        "VR Type",
        "Viral Major Taxon",
    )
    assert contract.forbidden_automatic_mappings == FORBIDDEN_AUTOMATIC_MAPPINGS


def test_exact_association_requires_taxon_locus_affinity_and_release_evidence() -> None:
    association = _exact("01")

    assert association.assembly_source_taxon.role == "assembly_source_taxonomy"
    assert association.viral_lineage_affinity.role == "formal_viral_taxonomy"
    assert association.viral_lineage_affinity.include_descendants is False
    assert association.evidence_source.release_key == RELEASE
    assert association.source_annotations == SourceRecordAnnotations(
        hcvr="Yes",
        vr_type="Integration",
        viral_major_taxon="Synthetic viral major taxon",
    )
    assert "relation_class" not in association.model_dump()

    with pytest.raises(ValidationError):
        _lineage(role="assembly_source_taxonomy")
    with pytest.raises(ValidationError, match="accession.version"):
        ExactAssociation.model_validate(
            {
                **association.model_dump(mode="python"),
                "assembly_accession_version": "GCA_000001",
            }
        )
    without_evidence = association.model_dump(mode="python")
    del without_evidence["evidence_source"]
    with pytest.raises(ValidationError):
        ExactAssociation.model_validate(without_evidence)


def test_structured_gold_requires_matching_release_evidence_and_canonical_sets() -> None:
    first = _exact("01")
    second = _exact("02")
    ordered = _ordered(first, second)

    gold = StructuredGold(
        exact_association_set=ordered,
        release_key=RELEASE,
        release_manifest_sha256=RELEASE_SHA256,
    )

    assert gold.exact_association_set == ordered
    with pytest.raises(ValidationError, match="canonically ordered"):
        StructuredGold(
            exact_association_set=tuple(reversed(ordered)),
            release_key=RELEASE,
            release_manifest_sha256=RELEASE_SHA256,
        )
    with pytest.raises(ValidationError, match="bind the Gold release evidence"):
        StructuredGold(
            exact_association_set=(
                first.model_copy(
                    update={
                        "evidence_source": first.evidence_source.model_copy(
                            update={"release_manifest_sha256": "f" * 64}
                        )
                    }
                ),
            ),
            release_key=RELEASE,
            release_manifest_sha256=RELEASE_SHA256,
        )


def test_literature_and_cross_source_sets_preserve_separate_truth_domains() -> None:
    exact = _exact("01")
    reported = _reported("01")
    literature = _literature_gold(reported)
    structured = StructuredGold(
        exact_association_set=(exact,),
        release_key=RELEASE,
        release_manifest_sha256=RELEASE_SHA256,
    )
    cross = CrossSourceAssociation(
        alignment_state="both",
        structured_association=exact,
        source_reported_association=reported,
    )
    hybrid = HybridGold(
        structured=structured,
        literature=literature,
        required_relationships=("The two reviewed records refer to the same association.",),
        cross_source_association_set=(cross,),
    )

    assert hybrid.cross_source_association_set == (cross,)
    with pytest.raises(ValidationError, match="association presence"):
        CrossSourceAssociation(
            alignment_state="structured_only",
            structured_association=exact,
            source_reported_association=reported,
        )


def test_reported_association_requires_taxon_region_affinity_and_literature_evidence() -> None:
    reported = _reported("01")

    assert reported.viral_lineage_affinity is not None
    assert reported.evidence_source.document_keys == (DOCUMENT,)
    assert reported.evidence_source.evidence_group_ids == ("evidence-001",)
    assert "relation_class" not in reported.model_dump()

    for field_name in (
        "assembly_source_taxon_text",
        "named_eve_locus_or_viral_region",
        "viral_lineage_affinity_text",
        "evidence_source",
    ):
        missing = reported.model_dump(mode="python")
        del missing[field_name]
        with pytest.raises(ValidationError):
            SourceReportedAssociation.model_validate(missing)
    with pytest.raises(ValidationError, match="sorted and unique"):
        LiteratureEvidenceSource(
            document_keys=(DOCUMENT,),
            evidence_group_ids=("evidence-002", "evidence-001"),
        )


def test_association_metrics_cover_each_v1_dimension_without_a_class_metric() -> None:
    taxon_gold = _exact("01")
    region_gold = _exact("02")
    lineage_gold = _exact("03")
    role_gold = _exact("04")
    scope_gold = _exact("05")
    evidence_gold = _exact("06")
    annotation_gold = _exact("07")
    taxon_changed = taxon_gold.model_copy(
        update={
            "assembly_source_taxon": AssemblySourceTaxonBinding(
                term_key="taxon:ncbi:9999",
                canonical_name="Changed taxon",
                snapshot_key="lineage-snapshot:source:test-v1",
            )
        }
    )
    region_changed = region_gold.model_copy(update={"eve_locus_key": "locus:eve:changed"})
    lineage_changed = lineage_gold.model_copy(
        update={
            "viral_lineage_affinity": _lineage(
                term_key="taxon:ictv:changed",
                canonical_name="Changed lineage",
            )
        }
    )
    role_changed = role_gold.model_copy(
        update={"viral_lineage_affinity": _lineage(role="study_viral_lineage")}
    )
    scope_changed = scope_gold.model_copy(
        update={"viral_lineage_affinity": _lineage(include_descendants=True)}
    )
    evidence_changed = evidence_gold.model_copy(
        update={
            "evidence_source": evidence_gold.evidence_source.model_copy(
                update={"source_record_keys": ("source-record:changed",)}
            )
        }
    )
    annotation_changed = annotation_gold.model_copy(
        update={"source_annotations": SourceRecordAnnotations(hcvr="No")}
    )

    metrics = score_association_set(
        (
            taxon_gold,
            region_gold,
            lineage_gold,
            role_gold,
            scope_gold,
            evidence_gold,
            annotation_gold,
        ),
        (
            taxon_changed,
            region_changed,
            lineage_changed,
            role_changed,
            scope_changed,
            evidence_changed,
            annotation_changed,
        ),
    )

    assert metrics.taxon_corrupted_count == 1
    assert metrics.region_corrupted_count == 1
    assert metrics.lineage_corrupted_count == 1
    assert metrics.role_corrupted_count == 1
    assert metrics.scope_corrupted_count == 1
    assert metrics.evidence_source_corrupted_count == 1
    assert metrics.source_annotations_corrupted_count == 1
    assert "class_corrupted_count" not in metrics.model_dump()


def test_structured_scoring_includes_association_and_release_provenance() -> None:
    association = _exact("01")
    gold = StructuredGold(
        exact_association_set=(association,),
        release_key=RELEASE,
        release_manifest_sha256=RELEASE_SHA256,
    )
    prediction = StructuredPrediction(
        exact_association_set=(association,),
        release_key=RELEASE,
        release_manifest_sha256=RELEASE_SHA256,
    )

    metrics = score_structured(gold, prediction)

    assert metrics.association_metrics is not None
    assert metrics.association_metrics.association_set_exact is True
    assert metrics.release_provenance_exact is True
    assert metrics.identifier_preservation.value == "1.000000000000"
    assert "relation_contract_exact" not in metrics.model_dump()


def test_committed_association_contract_is_canonical() -> None:
    contract_path = BENCHMARK_DIRECTORY / "association_contract_v1.json"

    assert contract_path.read_bytes() == association_contract_v1_bytes()
    assert AssociationContractV1.model_validate_json(contract_path.read_bytes()) == (
        build_association_contract_v1()
    )


def _exact(suffix: str) -> ExactAssociation:
    return ExactAssociation(
        assembly_source_taxon=AssemblySourceTaxonBinding(
            term_key=f"taxon:ncbi:{1000 + int(suffix)}",
            canonical_name=f"Synthetic taxon {suffix}",
            snapshot_key="lineage-snapshot:source:test-v1",
        ),
        assembly_accession_version=f"GCA_{int(suffix):06d}.1",
        eve_locus_key=f"locus:eve:v1:sha256:{int(suffix):064x}",
        viral_lineage_affinity=_lineage(),
        evidence_source=StructuredEvidenceSource(
            release_key=RELEASE,
            release_manifest_sha256=RELEASE_SHA256,
            source_record_keys=(f"source-record:test:{suffix}",),
        ),
        source_annotations=SourceRecordAnnotations(
            hcvr="Yes",
            vr_type="Integration",
            viral_major_taxon="Synthetic viral major taxon",
        ),
    )


def _lineage(
    *,
    role: str = "formal_viral_taxonomy",
    include_descendants: bool = False,
    term_key: str = "taxon:ictv:synthetic-virus",
    canonical_name: str = "Synthetic viral lineage",
) -> ViralLineageAffinity:
    return ViralLineageAffinity(
        term_key=term_key,
        canonical_name=canonical_name,
        role=role,  # type: ignore[arg-type]
        snapshot_key="lineage-snapshot:viral:test-v1",
        include_descendants=include_descendants,
    )


def _reported(suffix: str) -> SourceReportedAssociation:
    return SourceReportedAssociation(
        assembly_source_taxon_text=f"Synthetic taxon {suffix}",
        named_eve_locus_or_viral_region=f"Synthetic region {suffix}",
        viral_lineage_affinity_text="Synthetic viral lineage",
        viral_lineage_affinity=_lineage(),
        evidence_source=LiteratureEvidenceSource(
            document_keys=(DOCUMENT,),
            evidence_group_ids=("evidence-001",),
        ),
        source_annotations=SourceRecordAnnotations(vr_type="Integration"),
    )


def _literature_gold(association: SourceReportedAssociation) -> LiteratureGold:
    return LiteratureGold(
        required_document_keys=(DOCUMENT,),
        evidence_groups=(
            EvidenceGroup(
                group_id="evidence-001",
                required_document_key=DOCUMENT,
                required_chunk_key=CHUNK,
            ),
        ),
        required_concepts=("The reviewed v1 association tuple is present.",),
        source_reported_association_set=(association,),
    )


def _ordered(*values: ExactAssociation) -> tuple[ExactAssociation, ...]:
    return tuple(sorted(values, key=association_sort_key))
