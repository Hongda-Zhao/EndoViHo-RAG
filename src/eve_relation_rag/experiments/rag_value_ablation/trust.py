"""Issuer-only publication authority for isolated RAG-value benchmark runs.

Serializable manifests describe a run but never authorize publication. Phase 2 is the
only implemented issuer and can grant ``test_only`` authority to an exact, revalidated
benchmark run backed by a checksum-valid synthetic fixture object.
"""

from __future__ import annotations

import weakref
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    ExperimentManifest,
    TrustStatus,
)
from eve_relation_rag.experiments.rag_value_ablation.systems import (
    validate_system_definitions,
)

if TYPE_CHECKING:
    from eve_relation_rag.experiments.rag_value_ablation.reporting import BenchmarkRun
    from eve_relation_rag.experiments.rag_value_ablation.synthetic import (
        SyntheticFixtureManifest,
    )

type ExperimentPhase = Literal[
    "phase2_synthetic",
    "phase3_retrieval",
    "phase4_llm",
    "phase5_human",
    "phase6_analysis",
]

PHASE2_SYNTHETIC_TRUST_REASONS = tuple(
    sorted(
        (
            "deterministic fake generation provider",
            "synthetic fixtures only",
        )
    )
)
_TRUST_ISSUER = object()


class TrustDecisionError(ValueError):
    """Raised when runtime evidence cannot authorize the requested run."""


@dataclass(frozen=True, slots=True, weakref_slot=True)
class RunTrustDecision:
    """Runtime authority bound to one complete run and experiment manifest."""

    status: TrustStatus
    phase: ExperimentPhase
    reasons: tuple[str, ...]
    manifest_sha256: str
    run_sha256: str
    synthetic_fixture_manifest_sha256: str | None
    _issuer: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _TRUST_ISSUER:
            raise TypeError("RunTrustDecision may only be issued by the trust gate")
        if self.reasons != tuple(sorted(set(self.reasons))):
            raise TrustDecisionError("trust-decision reasons must be sorted and unique")


_ISSUED_DECISIONS: dict[int, weakref.ReferenceType[RunTrustDecision]] = {}


def issue_phase2_synthetic_trust(
    *,
    run: object,
    fixture_manifest: object,
) -> RunTrustDecision:
    """Authorize one exact Phase 2 manifest and make trusted status impossible."""

    # The local import keeps the fixture module independent of publication authority.
    from eve_relation_rag.experiments.rag_value_ablation.reporting import BenchmarkRun
    from eve_relation_rag.experiments.rag_value_ablation.synthetic import (
        SyntheticFixtureManifest,
        validate_synthetic_fixture_manifest,
    )

    if type(run) is not BenchmarkRun:
        raise TrustDecisionError("Phase 2 requires an exact BenchmarkRun")
    try:
        validated_run = BenchmarkRun.model_validate_json(run.model_dump_json())
    except Exception as exc:
        raise TrustDecisionError("Phase 2 run failed checksum revalidation") from exc
    validated = _revalidate_manifest(validated_run.manifest)
    if type(fixture_manifest) is not SyntheticFixtureManifest:
        raise TrustDecisionError("Phase 2 requires an exact synthetic fixture manifest")
    fixture = validate_synthetic_fixture_manifest(fixture_manifest)
    if validated.phase != "phase2_synthetic":
        raise TrustDecisionError("Phase 2 authority requires a Phase 2 manifest")
    if validated.trust_status != "test_only":
        raise TrustDecisionError("Phase 2 authority can grant only test_only status")
    if validated.trust_reasons != PHASE2_SYNTHETIC_TRUST_REASONS:
        raise TrustDecisionError("Phase 2 trust reasons differ from the fixed policy")
    if (
        validated.generation_identity is None
        or validated.generation_identity.provider_kind != "deterministic_fake"
    ):
        raise TrustDecisionError("Phase 2 requires a deterministic fake generation identity")
    if validated.synthetic_fixture_manifest_sha256 != fixture.fixture_sha256:
        raise TrustDecisionError("fixture checksum differs from the experiment manifest")
    if validated.question_manifest_sha256 != fixture.fixture_sha256:
        raise TrustDecisionError("Phase 2 question identity must be the fixture manifest")
    _validate_phase2_fixture_projection(validated_run, fixture)
    validate_system_definitions(validated.systems, validated.generation_identity)
    return _issue(
        status="test_only",
        phase="phase2_synthetic",
        reasons=PHASE2_SYNTHETIC_TRUST_REASONS,
        manifest_sha256=validated.manifest_sha256,
        run_sha256=validated_run.run_sha256,
        synthetic_fixture_manifest_sha256=fixture.fixture_sha256,
    )


def is_issued_trust_decision(value: object) -> bool:
    """Return whether ``value`` is the exact instance issued by this module."""

    if not isinstance(value, RunTrustDecision) or value._issuer is not _TRUST_ISSUER:
        return False
    registered = _ISSUED_DECISIONS.get(id(value))
    return registered is not None and registered() is value


def validate_run_authority(
    run: object,
    decision: RunTrustDecision,
) -> BenchmarkRun:
    """Revalidate the complete run and match it to runtime authority."""

    if not is_issued_trust_decision(decision):
        raise TrustDecisionError("benchmark output requires an issued trust decision")
    from eve_relation_rag.experiments.rag_value_ablation.reporting import BenchmarkRun

    if type(run) is not BenchmarkRun:
        raise TrustDecisionError("publication requires an exact BenchmarkRun")
    try:
        validated_run = BenchmarkRun.model_validate_json(run.model_dump_json())
    except Exception as exc:
        raise TrustDecisionError("benchmark run failed checksum revalidation") from exc
    validated = _revalidate_manifest(validated_run.manifest)
    validate_system_definitions(validated.systems, validated.generation_identity)
    if (
        validated.trust_status,
        validated.phase,
        validated.trust_reasons,
        validated.manifest_sha256,
        validated_run.run_sha256,
        validated.synthetic_fixture_manifest_sha256,
    ) != (
        decision.status,
        decision.phase,
        decision.reasons,
        decision.manifest_sha256,
        decision.run_sha256,
        decision.synthetic_fixture_manifest_sha256,
    ):
        raise TrustDecisionError("trust decision does not match the experiment manifest")
    return validated_run


def _validate_phase2_fixture_projection(
    run: BenchmarkRun,
    fixture: SyntheticFixtureManifest,
) -> None:
    """Bind every synthetic result identity to the admitted fixture cases."""

    results = run.results
    expected_pairs = tuple(
        (system.system_key, case.question_id)
        for system in run.manifest.systems
        for case in fixture.cases
    )
    observed_pairs = tuple((result.system_key, result.question_id) for result in results)
    if observed_pairs != expected_pairs:
        raise TrustDecisionError("Phase 2 results do not exactly project the fixture cases")
    case_by_id = {case.question_id: case for case in fixture.cases}
    for result in results:
        case = case_by_id[result.question_id]
        if (
            result.family != case.family
            or result.question_text_sha256 != case.question_text_sha256
        ):
            raise TrustDecisionError("Phase 2 result question differs from its fixture")
        if result.grounding_metrics is not None or result.trust_status != "test_only":
            raise TrustDecisionError("Phase 2 fixture results contain non-test review state")
        observation = result.refusal_observation
        if result.status != "not_applicable" and observation is None:
            raise TrustDecisionError(
                "Phase 2 requires a refusal observation for every applicable result"
            )
        if observation is not None and observation.expected_refusal != case.expected_refusal:
            raise TrustDecisionError("Phase 2 refusal label differs from its fixture")
        if observation is not None and (result.status == "refused") != observation.abstained:
            raise TrustDecisionError("Phase 2 refusal status differs from the observed answer")
        if observation is not None and (
            observation.refusal_appropriate
            != (case.expected_refusal and observation.abstained)
            or observation.unsafe_acceptance
            != (case.expected_refusal and not observation.abstained)
        ):
            raise TrustDecisionError(
                "Phase 2 refusal metrics differ from the fixture and observed answer"
            )
        if (
            observation is not None
            and observation.downstream_call_count_after_refusal != 0
        ):
            raise TrustDecisionError(
                "Phase 2 post-refusal call count differs from the fail-closed trace"
            )
        if observation is not None and observation.refusal_origin != (
            _phase2_expected_refusal_origin(
                system_key=result.system_key,
                question_text=case.question_text,
                abstained=observation.abstained,
            )
        ):
            raise TrustDecisionError(
                "Phase 2 refusal origin differs from the frozen scope/route execution"
            )
        expected_not_applicable = (
            case.family == "literature" and result.system_key in {"S4", "S5"}
        )
        if (result.status == "not_applicable") != expected_not_applicable:
            raise TrustDecisionError("synthetic applicability matrix drifted")
    expected_eligible = tuple(
        case.question_id
        for case in fixture.cases
        if case.family in {"structured", "hybrid"}
    )
    if run.comparison_eligible_question_ids != expected_eligible:
        raise TrustDecisionError("Phase 2 comparison denominator differs from the fixture")


def _phase2_expected_refusal_origin(
    *,
    system_key: str,
    question_text: str,
    abstained: bool,
) -> str:
    """Replay the I/O-free admission policy before classifying a synthetic refusal."""

    from eve_relation_rag.experiments.rag_value_ablation.synthetic import (
        SYNTHETIC_RELEASE_KEY,
    )
    from eve_relation_rag.hybrid.contracts import RagQueryRequest
    from eve_relation_rag.planning.router import DeterministicRouter
    from eve_relation_rag.planning.scope_policy import contains_forbidden_topic

    if contains_forbidden_topic(question_text):
        return "shared_scope_policy"
    if system_key in {"S4", "S5"}:
        route = DeterministicRouter().route(
            RagQueryRequest(
                release_key=SYNTHETIC_RELEASE_KEY,
                question=question_text,
            )
        )
        if route.route != "structured":
            return "system_route_policy"
    return "model_abstention" if abstained else "none"


def _revalidate_manifest(manifest: ExperimentManifest) -> ExperimentManifest:
    if type(manifest) is not ExperimentManifest:
        raise TrustDecisionError("publication requires an exact ExperimentManifest")
    try:
        return ExperimentManifest.model_validate_json(manifest.model_dump_json())
    except Exception as exc:
        raise TrustDecisionError("experiment manifest failed checksum revalidation") from exc


def _issue(
    *,
    status: TrustStatus,
    phase: ExperimentPhase,
    reasons: tuple[str, ...],
    manifest_sha256: str,
    run_sha256: str,
    synthetic_fixture_manifest_sha256: str | None,
) -> RunTrustDecision:
    decision = RunTrustDecision(
        status=status,
        phase=phase,
        reasons=reasons,
        manifest_sha256=manifest_sha256,
        run_sha256=run_sha256,
        synthetic_fixture_manifest_sha256=synthetic_fixture_manifest_sha256,
        _issuer=_TRUST_ISSUER,
    )
    identity = id(decision)

    def discard(reference: weakref.ReferenceType[RunTrustDecision]) -> None:
        if _ISSUED_DECISIONS.get(identity) is reference:
            _ISSUED_DECISIONS.pop(identity, None)

    _ISSUED_DECISIONS[identity] = weakref.ref(decision, discard)
    return decision


__all__ = [
    "PHASE2_SYNTHETIC_TRUST_REASONS",
    "RunTrustDecision",
    "TrustDecisionError",
    "is_issued_trust_decision",
    "issue_phase2_synthetic_trust",
    "validate_run_authority",
]
