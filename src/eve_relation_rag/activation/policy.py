"""Deterministic V0 pilot inclusion and final adjudication assembly."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from pydantic import ValidationError

from eve_relation_rag.activation.cohort import select_final_adjudication
from eve_relation_rag.activation.contracts import (
    ACTIVATION_RELEASE_KEY,
    INCLUSION_POLICY_KEY,
    AdjudicationCohortManifest,
    AssemblyTaxonAssignmentManifest,
    CohortRecord,
    FlankEvidenceManifest,
    FlankEvidenceRecord,
    FlankEvidenceRequestPlan,
    FullSequenceBundleManifest,
    IctvArtifactManifest,
    InclusionDecisionManifest,
    InclusionDecisionRecord,
    NcbiTaxonomyArtifactManifest,
    PublicAssertionMembershipManifest,
    PublicAssertionMembershipRecord,
    PublicLocusMembershipManifest,
    PublicLocusMembershipRecord,
    StructuredActivationCounts,
    StructuredActivationManifest,
    StructuredAdjudicationManifest,
    StudyFormalMappingManifest,
    TaxonomySnapshotManifest,
    canonical_model_sha256,
    canonical_revalidate,
    seal_manifest_payload,
)


class InclusionPolicyError(ValueError):
    """Raised when policy inputs do not bind to the frozen cohort and flank evidence."""


@dataclass(frozen=True, slots=True)
class DependencyBindings:
    """Exact activation manifests required by every include decision."""

    ncbi_snapshot_manifest_sha256: str | None
    ictv_snapshot_manifest_sha256: str | None
    mapping_manifest_sha256: str | None

    @property
    def all_bound(self) -> bool:
        return all(
            value is not None
            for value in (
                self.ncbi_snapshot_manifest_sha256,
                self.ictv_snapshot_manifest_sha256,
                self.mapping_manifest_sha256,
            )
        )


@dataclass(frozen=True, slots=True)
class InclusionEvaluationInput:
    """Complete, caller-supplied non-flank gates for one frozen cohort record."""

    record: CohortRecord
    flank: FlankEvidenceRecord | None
    dependencies: DependencyBindings
    m1_gates_pass: bool
    exact_placement_count: int
    import_outcome: str | None = None
    unresolved_issue_codes: tuple[str, ...] = ()
    quarantine_issue_codes: tuple[str, ...] = ()
    conflict_codes: tuple[str, ...] = ()


def evaluate_inclusion(value: InclusionEvaluationInput) -> InclusionDecisionRecord:
    """Apply exactly ``policy:v0-pilot-inclusion-v1`` without scientific inference."""

    try:
        record = canonical_revalidate(value.record)
        flank = None if value.flank is None else canonical_revalidate(value.flank)
    except ValidationError as exc:
        raise InclusionPolicyError("policy input failed canonical validation") from exc
    if flank is not None and (
        flank.source_record_key != record.source_record_key
        or flank.source_row != record.source_row
        or flank.locus_key != record.locus_key
        or flank.interval_key != record.interval_key
        or flank.placement_key != record.placement_key
        or flank.interval_basis != record.interval_basis
    ):
        raise InclusionPolicyError("flank evidence does not match the cohort record")
    if value.exact_placement_count < 0:
        raise InclusionPolicyError("exact_placement_count cannot be negative")
    expected_placement_count = 1 if record.placement_key is not None else 0
    if value.exact_placement_count != expected_placement_count:
        raise InclusionPolicyError("exact_placement_count disagrees with the frozen cohort")
    import_outcome = record.import_outcome if value.import_outcome is None else value.import_outcome
    if import_outcome != record.import_outcome:
        raise InclusionPolicyError("import_outcome disagrees with the frozen cohort")

    unresolved = _canonical_codes(value.unresolved_issue_codes)
    quarantine = _canonical_codes(
        set(record.quarantine_issue_codes).union(value.quarantine_issue_codes)
    )
    conflicts = _canonical_codes(value.conflict_codes)
    left = "not_assessed" if flank is None else flank.left.verdict
    right = "not_assessed" if flank is None else flank.right.verdict

    reasons: set[str] = set()
    if import_outcome != "normalized_candidate":
        reasons.add(f"import_outcome:{import_outcome}")
    if not value.m1_gates_pass:
        reasons.add("m1_gates_failed")
    if value.exact_placement_count != 1:
        reasons.add("exact_placement_count_not_one")
    if not value.dependencies.all_bound:
        reasons.add("dependency_snapshots_not_bound")
    if flank is None:
        reasons.add("flank_evidence_missing")
    if left != "supported":
        reasons.add(f"left_flank:{left}")
    if right != "supported":
        reasons.add(f"right_flank:{right}")
    reasons.update(f"unresolved_issue:{code}" for code in unresolved)
    reasons.update(f"quarantine_issue:{code}" for code in quarantine)
    reasons.update(f"conflict:{code}" for code in conflicts)

    eligible = not reasons
    if eligible:
        decision = "include"
        reasons.add("eligible_for_v0_pilot_inclusion")
    elif quarantine or import_outcome == "quarantine":
        decision = "quarantine"
    elif conflicts or left == "contradicted" or right == "contradicted":
        decision = "exclude"
    else:
        decision = "review"

    payload: dict[str, object] = {
        "source_record_key": record.source_record_key,
        "source_row": record.source_row,
        "locus_key": record.locus_key,
        "interval_key": record.interval_key,
        "placement_key": record.placement_key,
        "import_outcome": import_outcome,
        "exact_placement_count": value.exact_placement_count,
        "m1_gates_pass": value.m1_gates_pass,
        "dependency_snapshots_bound": value.dependencies.all_bound,
        "ncbi_snapshot_manifest_sha256": value.dependencies.ncbi_snapshot_manifest_sha256,
        "ictv_snapshot_manifest_sha256": value.dependencies.ictv_snapshot_manifest_sha256,
        "mapping_manifest_sha256": value.dependencies.mapping_manifest_sha256,
        "flank_record_sha256": None if flank is None else flank.record_sha256,
        "left_flank_verdict": left,
        "right_flank_verdict": right,
        "unresolved_issue_codes": unresolved,
        "quarantine_issue_codes": quarantine,
        "conflict_codes": conflicts,
        "decision": decision,
        "reason_codes": tuple(sorted(reasons)),
        "policy_key": INCLUSION_POLICY_KEY,
        "authorized_by": INCLUSION_POLICY_KEY,
    }
    sealed = dict(payload)
    sealed["decision_sha256"] = canonical_model_sha256(payload)
    return InclusionDecisionRecord.model_validate(sealed)


def build_inclusion_manifest(
    cohort: AdjudicationCohortManifest,
    flanks: FlankEvidenceManifest,
    evaluations: Iterable[InclusionEvaluationInput],
) -> InclusionDecisionManifest:
    """Evaluate and checksum exactly the records represented by the flank manifest."""

    try:
        cohort = canonical_revalidate(cohort)
        flanks = canonical_revalidate(flanks)
    except ValidationError as exc:
        raise InclusionPolicyError("inclusion manifest input failed validation") from exc

    if flanks.cohort_manifest_sha256 != cohort.manifest_sha256:
        raise InclusionPolicyError("flank evidence belongs to a different cohort")
    decisions = tuple(
        sorted(
            (evaluate_inclusion(value) for value in evaluations),
            key=lambda row: (row.source_row, row.locus_key),
        )
    )
    flank_by_locus = {row.locus_key: row for row in flanks.records}
    if {row.locus_key for row in decisions} != set(flank_by_locus):
        raise InclusionPolicyError("inclusion decisions must cover exactly the flank manifest")
    for decision in decisions:
        flank = flank_by_locus[decision.locus_key]
        if decision.flank_record_sha256 != flank.record_sha256:
            raise InclusionPolicyError("decision does not bind the exact flank evidence record")
    payload: dict[str, object] = {
        "manifest_schema_version": "inclusion-decision-manifest-v1",
        "release_key": ACTIVATION_RELEASE_KEY,
        "cohort_manifest_sha256": cohort.manifest_sha256,
        "flank_manifest_sha256": flanks.manifest_sha256,
        "policy_key": INCLUSION_POLICY_KEY,
        "decisions": decisions,
    }
    return InclusionDecisionManifest.model_validate(seal_manifest_payload(payload))


def build_adjudication_manifest(
    cohort: AdjudicationCohortManifest,
    flanks: FlankEvidenceManifest,
    inclusions: InclusionDecisionManifest,
) -> StructuredAdjudicationManifest:
    """Validate the all-primary/expansion-prefix rule and freeze terminal outcomes."""

    try:
        cohort = canonical_revalidate(cohort)
        flanks = canonical_revalidate(flanks)
        inclusions = canonical_revalidate(inclusions)
    except ValidationError as exc:
        raise InclusionPolicyError("adjudication manifest input failed validation") from exc

    if (
        flanks.cohort_manifest_sha256 != cohort.manifest_sha256
        or inclusions.cohort_manifest_sha256 != cohort.manifest_sha256
        or inclusions.flank_manifest_sha256 != flanks.manifest_sha256
    ):
        raise InclusionPolicyError("adjudication component manifests are incoherent")
    decisions = {row.locus_key: row for row in inclusions.decisions}
    selected = select_final_adjudication(cohort, decisions)
    payload: dict[str, object] = {
        "manifest_schema_version": "structured-adjudication-manifest-v1",
        "release_key": ACTIVATION_RELEASE_KEY,
        "cohort_manifest_sha256": cohort.manifest_sha256,
        "flank_manifest_sha256": flanks.manifest_sha256,
        "inclusion_manifest_sha256": inclusions.manifest_sha256,
        "selections": selected.selections,
        "assembly_outcomes": selected.assembly_outcomes,
    }
    return StructuredAdjudicationManifest.model_validate(seal_manifest_payload(payload))


def build_public_locus_membership_manifest(
    cohort: AdjudicationCohortManifest,
    flanks: FlankEvidenceManifest,
    inclusions: InclusionDecisionManifest,
    adjudication: StructuredAdjudicationManifest,
) -> PublicLocusMembershipManifest:
    """Project only policy-authorized include decisions into a candidate manifest."""

    try:
        cohort = canonical_revalidate(cohort)
        flanks = canonical_revalidate(flanks)
        inclusions = canonical_revalidate(inclusions)
        adjudication = canonical_revalidate(adjudication)
    except ValidationError as exc:
        raise InclusionPolicyError("public membership input failed validation") from exc

    if (
        adjudication.cohort_manifest_sha256 != cohort.manifest_sha256
        or adjudication.flank_manifest_sha256 != flanks.manifest_sha256
        or adjudication.inclusion_manifest_sha256 != inclusions.manifest_sha256
    ):
        raise InclusionPolicyError("public membership inputs are incoherent")
    cohort_by_locus = _cohort_by_locus(cohort)
    flank_by_locus = {row.locus_key: row for row in flanks.records}
    memberships: list[PublicLocusMembershipRecord] = []
    for decision in inclusions.decisions:
        if decision.decision != "include":
            continue
        record = cohort_by_locus[decision.locus_key]
        flank = flank_by_locus[decision.locus_key]
        if record.placement_key is None:
            raise InclusionPolicyError("included locus lacks an exact placement")
        memberships.append(
            PublicLocusMembershipRecord(
                locus_key=record.locus_key,
                placement_key=record.placement_key,
                assembly_accession_version=record.assembly_accession_version,
                sequence_accession_version=record.sequence_accession_version,
                start0=record.start0,
                end0=record.end0,
                coordinate_system=record.coordinate_system,
                left_flank_record_sha256=canonical_model_sha256(flank.left),
                right_flank_record_sha256=canonical_model_sha256(flank.right),
                inclusion_decision_sha256=decision.decision_sha256,
            )
        )
    canonical = tuple(sorted(memberships, key=lambda row: row.locus_key))
    payload: dict[str, object] = {
        "manifest_schema_version": "public-locus-membership-manifest-v1",
        "release_key": ACTIVATION_RELEASE_KEY,
        "adjudication_manifest_sha256": adjudication.manifest_sha256,
        "membership_count": len(canonical),
        "memberships": canonical,
    }
    return PublicLocusMembershipManifest.model_validate(seal_manifest_payload(payload))


def build_public_assertion_membership_manifest(
    loci: PublicLocusMembershipManifest,
    memberships: Iterable[PublicAssertionMembershipRecord],
) -> PublicAssertionMembershipManifest:
    """Bind canonical evidence-backed assertions only to candidate public loci.

    This builder is deliberately database-neutral: the caller must first construct
    assertion records from exact, supporting evidence.  It prevents an assertion
    from entering the candidate public set through a locus that did not pass the
    frozen inclusion/adjudication policy.
    """

    try:
        loci = canonical_revalidate(loci)
        canonical = tuple(
            sorted(
                (canonical_revalidate(record) for record in memberships),
                key=lambda row: row.assertion_key,
            )
        )
    except ValidationError as exc:
        raise InclusionPolicyError("public assertion membership input failed validation") from exc
    if not canonical:
        raise InclusionPolicyError("public assertion memberships cannot be empty")
    public_locus_keys = {row.locus_key for row in loci.memberships}
    if any(row.locus_key not in public_locus_keys for row in canonical):
        raise InclusionPolicyError("public assertion belongs to a non-public locus")
    payload: dict[str, object] = {
        "manifest_schema_version": "public-assertion-membership-manifest-v1",
        "release_key": ACTIVATION_RELEASE_KEY,
        "locus_membership_manifest_sha256": loci.manifest_sha256,
        "membership_count": len(canonical),
        "memberships": canonical,
    }
    return PublicAssertionMembershipManifest.model_validate(seal_manifest_payload(payload))


def build_structured_activation_manifest(
    *,
    ncbi_artifact: NcbiTaxonomyArtifactManifest,
    ncbi_snapshot: TaxonomySnapshotManifest,
    assembly_assignments: AssemblyTaxonAssignmentManifest,
    ictv_artifact: IctvArtifactManifest,
    ictv_snapshot: TaxonomySnapshotManifest,
    study_formal_mapping: StudyFormalMappingManifest,
    cohort: AdjudicationCohortManifest,
    full_sequence_bundle: FullSequenceBundleManifest,
    flank_request_plan: FlankEvidenceRequestPlan,
    flanks: FlankEvidenceManifest,
    inclusions: InclusionDecisionManifest,
    adjudication: StructuredAdjudicationManifest,
    public_loci: PublicLocusMembershipManifest,
    public_assertions: PublicAssertionMembershipManifest,
) -> StructuredActivationManifest:
    """Seal the candidate packet after replaying every component binding.

    Passing this builder means the packet is internally coherent only.  It does
    not create a receipt, mutate the database, or authorize publication.
    """

    try:
        ncbi_artifact = canonical_revalidate(ncbi_artifact)
        ncbi_snapshot = canonical_revalidate(ncbi_snapshot)
        assembly_assignments = canonical_revalidate(assembly_assignments)
        ictv_artifact = canonical_revalidate(ictv_artifact)
        ictv_snapshot = canonical_revalidate(ictv_snapshot)
        study_formal_mapping = canonical_revalidate(study_formal_mapping)
        cohort = canonical_revalidate(cohort)
        full_sequence_bundle = canonical_revalidate(full_sequence_bundle)
        flank_request_plan = canonical_revalidate(flank_request_plan)
        flanks = canonical_revalidate(flanks)
        inclusions = canonical_revalidate(inclusions)
        adjudication = canonical_revalidate(adjudication)
        public_loci = canonical_revalidate(public_loci)
        public_assertions = canonical_revalidate(public_assertions)
    except ValidationError as exc:
        raise InclusionPolicyError("activation manifest input failed validation") from exc
    if (
        ncbi_snapshot.snapshot_key != ncbi_artifact.snapshot_key
        or ncbi_snapshot.artifact_manifest_sha256 != ncbi_artifact.manifest_sha256
        or assembly_assignments.ncbi_snapshot_manifest_sha256 != ncbi_snapshot.manifest_sha256
        or ictv_snapshot.snapshot_key != ictv_artifact.snapshot_key
        or ictv_snapshot.artifact_manifest_sha256 != ictv_artifact.manifest_sha256
        or study_formal_mapping.formal_snapshot_key != ictv_snapshot.snapshot_key
        or study_formal_mapping.formal_snapshot_manifest_sha256
        != ictv_snapshot.manifest_sha256
        or flank_request_plan.cohort_manifest_sha256 != cohort.manifest_sha256
        or flanks.cohort_manifest_sha256 != cohort.manifest_sha256
        or flanks.request_plan_manifest_sha256 != flank_request_plan.manifest_sha256
        or inclusions.cohort_manifest_sha256 != cohort.manifest_sha256
        or inclusions.flank_manifest_sha256 != flanks.manifest_sha256
        or adjudication.cohort_manifest_sha256 != cohort.manifest_sha256
        or adjudication.flank_manifest_sha256 != flanks.manifest_sha256
        or adjudication.inclusion_manifest_sha256 != inclusions.manifest_sha256
        or public_loci.adjudication_manifest_sha256 != adjudication.manifest_sha256
        or public_assertions.locus_membership_manifest_sha256 != public_loci.manifest_sha256
    ):
        raise InclusionPolicyError("activation component manifests are incoherent")
    bundle_index = {row.accession_version: row for row in full_sequence_bundle.records}
    for request in flank_request_plan.requests:
        bundle_row = bundle_index.get(request.sequence_accession_version)
        if bundle_row is None or bundle_row.sequence_length != request.sequence_length:
            raise InclusionPolicyError("flank request is not covered by the exact sequence bundle")
    if any(row.source_uri != full_sequence_bundle.source_uri for row in flanks.records):
        raise InclusionPolicyError("flank evidence source URI differs from the frozen bundle")
    included_loci = sum(row.decision == "include" for row in inclusions.decisions)
    if included_loci != public_loci.membership_count:
        raise InclusionPolicyError("public locus count disagrees with include decisions")
    for decision in inclusions.decisions:
        if (
            decision.ncbi_snapshot_manifest_sha256 != ncbi_snapshot.manifest_sha256
            or decision.ictv_snapshot_manifest_sha256 != ictv_snapshot.manifest_sha256
            or decision.mapping_manifest_sha256 != study_formal_mapping.manifest_sha256
        ):
            raise InclusionPolicyError("inclusion decision has drifted dependency bindings")
    selected_decisions = {row.decision_sha256 for row in inclusions.decisions}
    if any(row.decision_sha256 not in selected_decisions for row in adjudication.selections):
        raise InclusionPolicyError("adjudication selection is absent from inclusion decisions")
    counts = StructuredActivationCounts(
        audited_source_records=39_495,
        exact_placements=38_968,
        accounted_quarantine=527,
        adjudicated_records=len(adjudication.selections),
        included_loci=included_loci,
        public_locus_memberships=public_loci.membership_count,
        public_assertion_memberships=public_assertions.membership_count,
    )
    payload: dict[str, object] = {
        "manifest_schema_version": "structured-activation-manifest-v1",
        "release_key": ACTIVATION_RELEASE_KEY,
        "source_manifest_sha256": cohort.source_manifest_sha256,
        "source_audit_sha256": cohort.source_audit_sha256,
        "ncbi_artifact_manifest_sha256": ncbi_artifact.manifest_sha256,
        "ncbi_snapshot_manifest_sha256": ncbi_snapshot.manifest_sha256,
        "assembly_taxon_assignment_manifest_sha256": assembly_assignments.manifest_sha256,
        "ictv_artifact_manifest_sha256": ictv_artifact.manifest_sha256,
        "ictv_snapshot_manifest_sha256": ictv_snapshot.manifest_sha256,
        "study_formal_mapping_manifest_sha256": study_formal_mapping.manifest_sha256,
        "cohort_manifest_sha256": cohort.manifest_sha256,
        "full_sequence_bundle_manifest_sha256": full_sequence_bundle.manifest_sha256,
        "flank_request_plan_manifest_sha256": flank_request_plan.manifest_sha256,
        "adjudication_manifest_sha256": adjudication.manifest_sha256,
        "flank_manifest_sha256": flanks.manifest_sha256,
        "inclusion_manifest_sha256": inclusions.manifest_sha256,
        "public_locus_membership_manifest_sha256": public_loci.manifest_sha256,
        "public_assertion_membership_manifest_sha256": public_assertions.manifest_sha256,
        "counts": counts,
    }
    return StructuredActivationManifest.model_validate(seal_manifest_payload(payload))


def _canonical_codes(values: Iterable[str]) -> tuple[str, ...]:
    canonical = tuple(sorted(values))
    if len(canonical) != len(set(canonical)):
        raise InclusionPolicyError("issue codes must be unique")
    return canonical


def _cohort_by_locus(cohort: AdjudicationCohortManifest) -> Mapping[str, CohortRecord]:
    return {
        row.locus_key: row
        for row in (
            *cohort.primary_records,
            *(record for queue in cohort.expansion_queues for record in queue.records),
        )
    }


__all__ = [
    "DependencyBindings",
    "InclusionEvaluationInput",
    "InclusionPolicyError",
    "build_adjudication_manifest",
    "build_inclusion_manifest",
    "build_public_assertion_membership_manifest",
    "build_public_locus_membership_manifest",
    "build_structured_activation_manifest",
    "evaluate_inclusion",
]
