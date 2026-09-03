from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from eve_relation_rag.experiments.rag_value_ablation.associations import (
    CANONICAL_RELATION_CLASSES,
    CrossSourceAssociation,
    ExactAssociation,
    PendingRelationClassAssertion,
    PendingRelationContractTemplate,
    SourceReportedAssociation,
    SourceSpeciesBinding,
    ViralLineageBinding,
    association_sort_key,
    build_pending_relation_contract_template,
    relation_class_assertions_template_bytes,
    relation_contract_template_bytes,
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
RELATION_CONTRACT = "relation-contract:test:v1"
RELATION_ASSERTION_MANIFEST_SHA256 = "e" * 64
BENCHMARK_DIRECTORY = Path(__file__).resolve().parents[2] / "benchmark" / "rag_value_ablation"


def test_relation_class_lineage_scope_and_assembly_identity_are_strict() -> None:
    association = _exact("01")

    assert association.relation_class == "Transferred gene"
    assert association.source_species.role == "assembly_source_taxonomy"
    assert association.viral_lineage.role == "formal_viral_taxonomy"
    assert association.viral_lineage.include_descendants is False

    with pytest.raises(ValidationError):
        _exact("02", relation_class="Integration")
    with pytest.raises(ValidationError):
        _lineage(role="assembly_source_taxonomy")
    with pytest.raises(ValidationError, match="accession.version"):
        ExactAssociation(
            **{
                **association.model_dump(mode="python"),
                "assembly_accession_version": "GCA_000001",
            }
        )
    without_assertion = association.model_dump(mode="python")
    del without_assertion["relation_assertion_key"]
    with pytest.raises(ValidationError):
        ExactAssociation.model_validate(without_assertion)
    with pytest.raises(ValidationError, match="relation-assertion namespace"):
        ExactAssociation.model_validate(
            {
                **association.model_dump(mode="python"),
                "relation_assertion_key": "unscoped-assertion-key",
            }
        )


def test_association_gold_requires_contract_identity_and_canonical_sets() -> None:
    first = _exact("01")
    second = _exact("02")
    ordered = _ordered(first, second)

    gold = StructuredGold(
        exact_association_set=ordered,
        relation_contract_key=RELATION_CONTRACT,
        relation_contract_sha256="c" * 64,
        relation_assertion_manifest_sha256=RELATION_ASSERTION_MANIFEST_SHA256,
        release_key=RELEASE,
        release_manifest_sha256="d" * 64,
    )

    assert gold.exact_association_set == ordered
    with pytest.raises(ValidationError, match="relation contract and assertion identities"):
        StructuredGold(
            exact_association_set=ordered,
            release_key=RELEASE,
            release_manifest_sha256="d" * 64,
        )
    with pytest.raises(ValidationError, match="canonically ordered"):
        StructuredGold(
            exact_association_set=tuple(reversed(ordered)),
            relation_contract_key=RELATION_CONTRACT,
            relation_contract_sha256="c" * 64,
            relation_assertion_manifest_sha256=RELATION_ASSERTION_MANIFEST_SHA256,
            release_key=RELEASE,
            release_manifest_sha256="d" * 64,
        )
    with pytest.raises(ValidationError, match="bind the Gold assertion manifest"):
        StructuredGold(
            exact_association_set=(
                first.model_copy(
                    update={"relation_assertion_manifest_sha256": "f" * 64}
                ),
            ),
            relation_contract_key=RELATION_CONTRACT,
            relation_contract_sha256="c" * 64,
            relation_assertion_manifest_sha256=RELATION_ASSERTION_MANIFEST_SHA256,
            release_key=RELEASE,
            release_manifest_sha256="d" * 64,
        )


def test_literature_and_cross_source_sets_preserve_separate_truth_domains() -> None:
    exact = _exact("01")
    reported = _reported("01")
    literature = _literature_gold(reported)
    structured = StructuredGold(
        exact_association_set=(exact,),
        relation_contract_key=RELATION_CONTRACT,
        relation_contract_sha256="c" * 64,
        relation_assertion_manifest_sha256=RELATION_ASSERTION_MANIFEST_SHA256,
        release_key=RELEASE,
        release_manifest_sha256="d" * 64,
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
    with pytest.raises(ValidationError, match="cover each exact association once"):
        HybridGold(
            structured=structured,
            literature=literature,
            required_relationships=("The records were reviewed independently.",),
            cross_source_association_set=(
                CrossSourceAssociation(
                    alignment_state="literature_only",
                    source_reported_association=reported,
                ),
            ),
        )


def test_source_reported_association_preserves_unreported_fields_as_missing() -> None:
    taxon_only = SourceReportedAssociation(
        source_taxon_text="Synthetic host lineage",
        source_relation_text="A manually reviewed relation label",
        relation_class="Transferred gene",
        relation_assertion_manifest_sha256=RELATION_ASSERTION_MANIFEST_SHA256,
        relation_assertion_key="relation-assertion:test:taxon-only",
        relation_assertion_sha256="9" * 64,
        evidence_group_ids=("evidence-001",),
    )

    assert taxon_only.source_species_text is None
    assert taxon_only.named_assembly_or_region is None
    assert taxon_only.viral_lineage_text is None
    assert taxon_only.viral_lineage is None

    text_without_binding = SourceReportedAssociation.model_validate(
        {
            **taxon_only.model_dump(mode="python"),
            "viral_lineage_text": "Unresolved source lineage",
        }
    )
    assert text_without_binding.viral_lineage is None

    with pytest.raises(ValidationError, match="reported host or region descriptor"):
        SourceReportedAssociation.model_validate(
            {
                **taxon_only.model_dump(mode="python"),
                "source_taxon_text": None,
            }
        )
    with pytest.raises(ValidationError, match="requires source-reported lineage text"):
        SourceReportedAssociation.model_validate(
            {
                **taxon_only.model_dump(mode="python"),
                "viral_lineage": _lineage(),
            }
        )
    without_assertion_identity = taxon_only.model_dump(mode="python")
    del without_assertion_identity["relation_assertion_key"]
    with pytest.raises(ValidationError):
        SourceReportedAssociation.model_validate(without_assertion_identity)
    with pytest.raises(ValidationError, match="sorted and unique"):
        SourceReportedAssociation.model_validate(
            {
                **taxon_only.model_dump(mode="python"),
                "evidence_group_ids": ("evidence-002", "evidence-001"),
            }
        )
    with pytest.raises(ValidationError):
        SourceReportedAssociation.model_validate(
            {
                **taxon_only.model_dump(mode="python"),
                "relation_assertion_sha256": "not-a-checksum",
            }
        )


def test_missing_source_lineage_is_not_mislabeled_as_role_or_scope_corruption() -> None:
    reported = _reported("01")
    missing_lineage = SourceReportedAssociation.model_validate(
        {
            **reported.model_dump(mode="python"),
            "viral_lineage_text": None,
            "viral_lineage": None,
        }
    )

    metrics = score_association_set((reported,), (missing_lineage,))

    assert metrics.association_set_exact is False
    assert metrics.missing_association_count == 1
    assert metrics.extra_association_count == 1
    assert metrics.role_corrupted_count == 0
    assert metrics.scope_corrupted_count == 0


def test_association_metrics_distinguish_class_role_scope_and_unmatched_records() -> None:
    class_gold = _exact("01")
    role_gold = _exact("02")
    scope_gold = _exact("03")
    missing_gold = _exact("04")
    class_changed = class_gold.model_copy(
        update={
            "relation_class": "Integrated virus",
            "relation_assertion_key": "relation-assertion:test:integrated-01",
            "relation_assertion_sha256": "a" * 64,
        }
    )
    role_changed = role_gold.model_copy(
        update={"viral_lineage": _lineage(role="study_viral_lineage")}
    )
    scope_changed = scope_gold.model_copy(
        update={"viral_lineage": _lineage(include_descendants=True)}
    )
    unrelated_extra = _exact("05")

    metrics = score_association_set(
        (class_gold, role_gold, scope_gold, missing_gold),
        (class_changed, role_changed, scope_changed, unrelated_extra),
    )

    assert metrics.association_set_exact is False
    assert metrics.missing_association_count == 4
    assert metrics.extra_association_count == 4
    assert metrics.class_corrupted_count == 1
    assert metrics.role_corrupted_count == 1
    assert metrics.scope_corrupted_count == 1

    assertion_only_changed = class_gold.model_copy(
        update={
            "relation_assertion_key": "relation-assertion:test:replacement",
            "relation_assertion_sha256": "f" * 64,
        }
    )
    assertion_metrics = score_association_set((class_gold,), (assertion_only_changed,))
    assert assertion_metrics.class_corrupted_count == 0


def test_structured_scoring_includes_association_and_relation_contract_exactness() -> None:
    association = _exact("01")
    gold = StructuredGold(
        exact_association_set=(association,),
        relation_contract_key=RELATION_CONTRACT,
        relation_contract_sha256="c" * 64,
        relation_assertion_manifest_sha256=RELATION_ASSERTION_MANIFEST_SHA256,
        release_key=RELEASE,
        release_manifest_sha256="d" * 64,
    )
    prediction = StructuredPrediction(
        exact_association_set=(association,),
        relation_contract_key=RELATION_CONTRACT,
        relation_contract_sha256="c" * 64,
        relation_assertion_manifest_sha256=RELATION_ASSERTION_MANIFEST_SHA256,
        release_key=RELEASE,
        release_manifest_sha256="d" * 64,
    )

    metrics = score_structured(gold, prediction)

    assert metrics.association_metrics is not None
    assert metrics.association_metrics.association_set_exact is True
    assert metrics.relation_contract_exact is True
    assert metrics.relation_assertion_manifest_exact is True
    assert metrics.identifier_preservation.value == "1.000000000000"


def test_relation_authoring_templates_are_empty_pending_and_checksum_bound() -> None:
    contract_path = BENCHMARK_DIRECTORY / "relation_contract_template.json"
    assertions_path = BENCHMARK_DIRECTORY / "relation_class_assertions_template.jsonl"

    assert contract_path.read_bytes() == relation_contract_template_bytes()
    assert assertions_path.read_bytes() == relation_class_assertions_template_bytes() == b""
    assert hashlib.sha256(assertions_path.read_bytes()).hexdigest() == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    contract = PendingRelationContractTemplate.model_validate_json(
        contract_path.read_bytes()
    )
    assert contract == build_pending_relation_contract_template()
    assert contract.review_status == "pending"
    assert contract.approval is None
    assert contract.definitions_supplied is False
    assert contract.source_label_mapping_supplied is False
    assert contract.unmapped_source_labels == ("HCVR", "Integration", "Viral contig")
    assert contract.relation_classes == CANONICAL_RELATION_CLASSES
    assert hashlib.sha256(contract_path.read_bytes()).hexdigest() == (
        "740b806cc7105ea2c7c3d8a32035ef4c6273c2f95cbde39a7e0e12d5184a586c"
    )


def test_pending_relation_assertion_cannot_prefill_a_class_or_approval() -> None:
    payload = {
        "assertion_schema_version": "rag-value-relation-class-assertion-template-v1",
        "assertion_key": "relation-assertion:pending:test-001",
        "source_record_key": "source-record:test-001",
        "source_label": "Integration",
        "relation_class": "Integrated virus",
        "relation_contract_key": None,
        "relation_contract_sha256": None,
        "review_status": "pending",
        "approval": None,
        "record_sha256": "f" * 64,
    }
    with pytest.raises(ValidationError):
        PendingRelationClassAssertion.model_validate(payload)


def _exact(
    suffix: str,
    *,
    relation_class: str = "Transferred gene",
) -> ExactAssociation:
    return ExactAssociation(
        source_species=SourceSpeciesBinding(
            term_key=f"taxon:ncbi:{1000 + int(suffix)}",
            canonical_name=f"Synthetic species {suffix}",
            snapshot_key="lineage-snapshot:source:test-v1",
        ),
        assembly_accession_version=f"GCA_{int(suffix):06d}.1",
        locus_key=f"locus:eve:v1:sha256:{int(suffix):064x}",
        relation_class=relation_class,  # type: ignore[arg-type]
        relation_assertion_manifest_sha256=RELATION_ASSERTION_MANIFEST_SHA256,
        relation_assertion_key=f"relation-assertion:test:{suffix}",
        relation_assertion_sha256=f"{int(suffix):064x}",
        viral_lineage=_lineage(),
    )


def _lineage(
    *,
    role: str = "formal_viral_taxonomy",
    include_descendants: bool = False,
) -> ViralLineageBinding:
    return ViralLineageBinding(
        term_key="taxon:ictv:synthetic-virus",
        canonical_name="Synthetic viral lineage",
        role=role,  # type: ignore[arg-type]
        snapshot_key="lineage-snapshot:viral:test-v1",
        include_descendants=include_descendants,
    )


def _reported(suffix: str) -> SourceReportedAssociation:
    return SourceReportedAssociation(
        source_taxon_text="Synthetic host lineage",
        source_species_text=f"Synthetic species {suffix}",
        named_assembly_or_region=f"Synthetic region {suffix}",
        source_relation_text="A manually reviewed relation label",
        relation_class="Transferred gene",
        relation_assertion_manifest_sha256=RELATION_ASSERTION_MANIFEST_SHA256,
        relation_assertion_key=f"relation-assertion:test:{suffix}",
        relation_assertion_sha256=f"{int(suffix):064x}",
        viral_lineage_text="Synthetic viral lineage",
        viral_lineage=_lineage(),
        evidence_group_ids=("evidence-001",),
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
        required_concepts=("The reviewed association tuple is present.",),
        source_reported_association_set=(association,),
        relation_contract_key=RELATION_CONTRACT,
        relation_contract_sha256="c" * 64,
        relation_assertion_manifest_sha256=RELATION_ASSERTION_MANIFEST_SHA256,
    )


def _ordered(*values: ExactAssociation) -> tuple[ExactAssociation, ...]:
    return tuple(sorted(values, key=association_sort_key))
