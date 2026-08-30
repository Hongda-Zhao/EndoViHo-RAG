from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from eve_relation_rag.activation.cohort import (
    AdjudicationSelectionError,
    CohortProjection,
    build_adjudication_cohort,
    select_final_adjudication,
)
from eve_relation_rag.activation.contracts import (
    APPROVED_ASSEMBLIES,
    AdjudicationCohortManifest,
    CohortRecord,
    InclusionDecisionRecord,
    PublicAssertionMembershipRecord,
)
from eve_relation_rag.activation.flanks import (
    LoadedFullSequenceBundle,
    assess_sequence_wrapper,
    build_flank_evidence_manifest,
    build_flank_request_plan,
    load_full_sequence_bundle,
    materialize_primary_flank_artifacts,
    materialize_range_wrapper,
)
from eve_relation_rag.activation.policy import (
    DependencyBindings,
    InclusionEvaluationInput,
    InclusionPolicyError,
    build_adjudication_manifest,
    build_inclusion_manifest,
    build_public_assertion_membership_manifest,
    build_public_locus_membership_manifest,
    evaluate_inclusion,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
RETRIEVED_AT = "2026-08-29T05:46:54Z"
SOURCE_URI = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
TOOL_VERSION = "sha256:" + "d" * 64


def test_full_primary_pipeline_materializes_71_loci_from_ten_contigs(tmp_path: Path) -> None:
    cohort = _cohort()
    bundle = _bundle(tmp_path, cohort)

    artifacts = materialize_primary_flank_artifacts(
        cohort,
        bundle,
        assessed_by="method:v0-flank-context-v1",
        assessed_at=RETRIEVED_AT,
    )

    assert len(artifacts.request_plan.requests) == 71
    assert len(artifacts.evidence_manifest.records) == 71
    assert all(row.left.verdict == "supported" for row in artifacts.evidence_manifest.records)
    assert all(row.right.verdict == "supported" for row in artifacts.evidence_manifest.records)
    first = min(artifacts.request_plan.requests, key=lambda row: row.source_row)
    assert first.ncbi_range_start1 == 1
    assert first.ncbi_range_end1 == first.request_end0

    flank_by_locus = {row.locus_key: row for row in artifacts.evidence_manifest.records}
    dependencies = DependencyBindings(
        ncbi_snapshot_manifest_sha256=SHA_A,
        ictv_snapshot_manifest_sha256=SHA_B,
        mapping_manifest_sha256=SHA_C,
    )
    evaluations = tuple(
        InclusionEvaluationInput(
            record=record,
            flank=flank_by_locus[record.locus_key],
            dependencies=dependencies,
            m1_gates_pass=True,
            exact_placement_count=1 if record.placement_key is not None else 0,
        )
        for record in cohort.primary_records
    )
    inclusions = build_inclusion_manifest(
        cohort,
        artifacts.evidence_manifest,
        evaluations,
    )
    assert sum(row.decision == "include" for row in inclusions.decisions) == 70
    assert sum(row.decision == "quarantine" for row in inclusions.decisions) == 1

    adjudication = build_adjudication_manifest(
        cohort,
        artifacts.evidence_manifest,
        inclusions,
    )
    public = build_public_locus_membership_manifest(
        cohort,
        artifacts.evidence_manifest,
        inclusions,
        adjudication,
    )
    assert public.membership_count == 70
    assert {row.assembly_accession_version for row in public.memberships} == set(
        APPROVED_ASSEMBLIES
    )
    assertion_records = tuple(
        PublicAssertionMembershipRecord(
            assertion_key=f"assertion:test:{index}",
            locus_key=membership.locus_key,
            assertion_type="hcvr",
            predicate_key="predicate:hcvr",
            evidence_sha256s=(membership.inclusion_decision_sha256,),
        )
        for index, membership in enumerate(public.memberships)
    )
    public_assertions = build_public_assertion_membership_manifest(public, assertion_records)
    assert public_assertions.membership_count == public.membership_count

    non_public = assertion_records[0].model_copy(
        update={"locus_key": cohort.primary_records[-1].locus_key}
    )
    with pytest.raises(InclusionPolicyError, match="non-public locus"):
        build_public_assertion_membership_manifest(public, (*assertion_records[1:], non_public))


def test_request_plan_converts_half_open_to_ncbi_inclusive_coordinates() -> None:
    cohort = _cohort()
    record = cohort.primary_records[0]

    request = build_flank_request_plan(cohort, (record,)).requests[0]

    assert request.request_start0 == 0
    assert request.ncbi_range_start1 == request.request_start0 + 1
    assert request.ncbi_range_end1 == request.request_end0
    assert request.expected_left_bp == record.start0
    assert request.expected_right_bp == request.request_end0 - record.end0


def test_inclusion_policy_fails_closed_without_dependency_binding(
    tmp_path: Path,
) -> None:
    cohort = _cohort()
    bundle = _bundle(tmp_path, cohort)
    record = cohort.primary_records[0]
    plan = build_flank_request_plan(cohort, (record,))
    flank = assess_sequence_wrapper(
        plan.requests[0],
        materialize_range_wrapper(plan.requests[0], bundle),
        assessed_by="method:v0-flank-context-v1",
        assessed_at=RETRIEVED_AT,
    )

    decision = evaluate_inclusion(
        InclusionEvaluationInput(
            record=record,
            flank=flank,
            dependencies=DependencyBindings(SHA_A, None, SHA_C),
            m1_gates_pass=True,
            exact_placement_count=1,
        )
    )

    assert decision.decision == "review"
    assert "dependency_snapshots_not_bound" in decision.reason_codes
    with pytest.raises(ValidationError, match="include decision"):
        InclusionDecisionRecord.model_validate(
            {**decision.model_dump(mode="python"), "decision": "include"}
        )


def test_expansion_must_be_prefix_and_stop_at_first_include(tmp_path: Path) -> None:
    cohort = _cohort(expansion_per_assembly=2)
    bundle = _bundle(tmp_path, cohort, include_expansion=True)
    target_assembly = APPROVED_ASSEMBLIES[0]
    target_primary = tuple(
        row for row in cohort.primary_records if row.assembly_accession_version == target_assembly
    )
    first_expansion = cohort.expansion_queues[0].records[0]
    second_expansion = cohort.expansion_queues[0].records[1]
    records = (*cohort.primary_records, first_expansion, second_expansion)
    plan = build_flank_request_plan(cohort, records)
    flank_records = tuple(
        assess_sequence_wrapper(
            request,
            materialize_range_wrapper(request, bundle),
            assessed_by="method:v0-flank-context-v1",
            assessed_at=RETRIEVED_AT,
        )
        for request in plan.requests
    )
    flanks = build_flank_evidence_manifest(cohort, plan, flank_records)
    flank_by_locus = {row.locus_key: row for row in flanks.records}
    dependencies = DependencyBindings(SHA_A, SHA_B, SHA_C)
    decisions: dict[str, InclusionDecisionRecord] = {}
    for row in cohort.primary_records:
        decisions[row.locus_key] = evaluate_inclusion(
            InclusionEvaluationInput(
                record=row,
                flank=flank_by_locus[row.locus_key],
                dependencies=dependencies,
                m1_gates_pass=row not in target_primary,
                exact_placement_count=1 if row.placement_key is not None else 0,
            )
        )
    decisions[first_expansion.locus_key] = evaluate_inclusion(
        InclusionEvaluationInput(
            record=first_expansion,
            flank=flank_by_locus[first_expansion.locus_key],
            dependencies=dependencies,
            m1_gates_pass=True,
            exact_placement_count=1,
        )
    )

    selected = select_final_adjudication(cohort, decisions)

    assert selected.assembly_outcomes[0].terminal_status == "passing_locus_found"
    assert any(row.locus_key == first_expansion.locus_key for row in selected.selections)

    decisions[second_expansion.locus_key] = evaluate_inclusion(
        InclusionEvaluationInput(
            record=second_expansion,
            flank=flank_by_locus[second_expansion.locus_key],
            dependencies=dependencies,
            m1_gates_pass=True,
            exact_placement_count=1,
        )
    )
    with pytest.raises(AdjudicationSelectionError, match="stop immediately"):
        select_final_adjudication(cohort, decisions)


def test_manifest_self_hash_rejects_tampering() -> None:
    cohort = _cohort()
    payload = cohort.model_dump(mode="python")
    payload["source_audit_sha256"] = "f" * 64

    with pytest.raises(ValidationError, match="manifest_sha256"):
        AdjudicationCohortManifest.model_validate(payload)


def _cohort(*, expansion_per_assembly: int = 1) -> AdjudicationCohortManifest:
    rows: list[CohortProjection] = []
    row_number = 2
    for index in range(71):
        assembly_index = index % len(APPROVED_ASSEMBLIES)
        rows.append(
            _projection(
                index=index,
                row_number=row_number,
                assembly_index=assembly_index,
                assessment="source_high",
                quarantine=index == 70,
            )
        )
        row_number += 1
    for assembly_index in range(len(APPROVED_ASSEMBLIES)):
        for offset in range(expansion_per_assembly):
            index = 1000 + assembly_index * 10 + offset
            rows.append(
                _projection(
                    index=index,
                    row_number=row_number,
                    assembly_index=assembly_index,
                    assessment="source_low",
                )
            )
            row_number += 1
    return build_adjudication_cohort(
        rows,
        source_manifest_sha256=SHA_A,
        source_audit_sha256=SHA_B,
    )


def _projection(
    *,
    index: int,
    row_number: int,
    assembly_index: int,
    assessment: str,
    quarantine: bool = False,
) -> CohortProjection:
    return CohortProjection(
        release_status="candidate",
        source_record_key=f"source-record:test:{index}",
        source_row=row_number,
        locus_key=f"locus:eve:v1:sha256:{index:064x}",
        placement_key=None if quarantine else f"placement:test:{index}",
        placement_sha256=None if quarantine else f"{index:064x}",
        assembly_accession_version=APPROVED_ASSEMBLIES[assembly_index],
        sequence_accession_version=f"NC_{assembly_index + 1:06d}.1",
        sequence_length=100,
        start0=5 + index % 5,
        end0=20 + index % 5,
        coordinate_system="0-based-half-open",
        precision=None if quarantine else "exact",
        source_assessment=assessment,
        import_outcome="quarantine" if quarantine else "normalized_candidate",
        quarantine_issue_codes=("viral_contig_policy_quarantine",) if quarantine else (),
    )


def _bundle(
    tmp_path: Path,
    cohort: AdjudicationCohortManifest,
    *,
    include_expansion: bool = False,
) -> LoadedFullSequenceBundle:
    records: tuple[CohortRecord, ...] = cohort.primary_records
    if include_expansion:
        records = (
            *records,
            *(row for queue in cohort.expansion_queues for row in queue.records),
        )
    by_accession = {row.sequence_accession_version: row.sequence_length for row in records}
    payload = [
        {
            "accession": accession,
            "header": f">{accession} synthetic",
            "sequence": "A" * length,
            "length": length,
        }
        for accession, length in sorted(by_accession.items())
    ]
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    raw = path.read_bytes()
    return load_full_sequence_bundle(
        path,
        expected_file_sha256=hashlib.sha256(raw).hexdigest(),
        expected_file_byte_size=len(raw),
        source_uri=SOURCE_URI,
        retrieved_at=RETRIEVED_AT,
        tool_version=TOOL_VERSION,
    )
