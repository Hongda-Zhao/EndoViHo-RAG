"""Semantic validation after exact resolution and before SQL compilation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from eve_relation_rag.planning.query_plans import (
    AggregatePlan,
    AssemblyFilter,
    EntireReleaseScope,
    FilteredScope,
    LocusFilter,
    PlanningAudit,
    SourceLineageFilter,
    StructuredPlan,
    ViralLineageFilter,
)
from eve_relation_rag.retrieval.structured.capability import ReleaseCapability
from eve_relation_rag.retrieval.structured.errors import RetrievalRefusal
from eve_relation_rag.retrieval.structured.results import ResolvedEntity


@dataclass(frozen=True, slots=True)
class ValidatedQuery:
    """A condition-complete plan bound to one gate-authorized release."""

    release: ReleaseCapability
    plan: StructuredPlan
    planning_audit: PlanningAudit
    resolved_entities: tuple[ResolvedEntity, ...]


class StructuredSemanticValidator:
    """Reject condition loss, binding mismatches, and unsafe closure use."""

    def validate(
        self,
        release: ReleaseCapability,
        plan: StructuredPlan,
        planning_audit: PlanningAudit,
        resolved_entities: tuple[ResolvedEntity, ...],
    ) -> ValidatedQuery:
        status_is_authorized = release.status in {"published", "validation_candidate"}
        candidate_identity_is_complete = release.status != "validation_candidate" or (
            release.candidate_validation_input_sha256 is not None
            and release.candidate_capability_sha256 is not None
            and release.validation_receipt_key == "validation-candidate:no-receipt"
            and release.validation_receipt_sha256 == "0" * 64
        )
        if (
            release.release_key != plan.release_key
            or not status_is_authorized
            or not candidate_identity_is_complete
        ):
            raise RetrievalRefusal(
                "release_dependencies_incomplete",
                "authorized release capability does not match the query plan",
            )
        if not planning_audit.extracted_conditions:
            raise RetrievalRefusal(
                "condition_unmapped",
                "a structured query requires a non-empty planning audit",
            )
        if planning_audit.unresolved_condition_ids or planning_audit.unconsumed_semantic_spans:
            raise RetrievalRefusal(
                "condition_unmapped",
                "every extracted condition and semantic span must be consumed",
            )
        self._validate_planning_audit(plan, planning_audit)

        expected_entities: list[tuple[str, str, str | None, str | None]] = []
        if isinstance(plan.scope, FilteredScope):
            for query_filter in plan.scope.filters:
                if isinstance(query_filter, AssemblyFilter):
                    expected_entities.append(("assembly", query_filter.assembly_key, None, None))
                elif isinstance(query_filter, LocusFilter):
                    expected_entities.append(("locus", query_filter.locus_key, None, None))
                elif isinstance(query_filter, SourceLineageFilter):
                    self._validate_lineage_filter(release, query_filter)
                    expected_entities.append(
                        (
                            "source_lineage",
                            query_filter.term_key,
                            query_filter.snapshot_key,
                            query_filter.role,
                        )
                    )
                elif isinstance(query_filter, ViralLineageFilter):
                    self._validate_lineage_filter(release, query_filter)
                    expected_entities.append(
                        (
                            "viral_lineage",
                            query_filter.term_key,
                            query_filter.snapshot_key,
                            query_filter.role,
                        )
                    )
                else:  # pragma: no cover - Pydantic's closed union prevents this.
                    raise RetrievalRefusal(
                        "compiler_constraint_unmapped",
                        "query filter has no fixed compiler constraint",
                    )

        observed_entities = [
            (entity.entity_kind, entity.stable_key, entity.snapshot_key, entity.role)
            for entity in resolved_entities
        ]
        if sorted(expected_entities) != sorted(observed_entities):
            raise RetrievalRefusal(
                "condition_unmapped",
                "resolved entities do not exactly match the query plan filters",
            )

        return ValidatedQuery(
            release=release,
            plan=plan,
            planning_audit=planning_audit,
            resolved_entities=resolved_entities,
        )

    @staticmethod
    def _validate_planning_audit(
        plan: StructuredPlan,
        planning_audit: PlanningAudit,
    ) -> None:
        """Bind every successful audit condition to one exact plan component.

        ``PlanningAudit`` is intentionally not compiler input, but it is the
        proof that the planner did not discard a condition.  Merely marking all
        extracted IDs as mapped is insufficient: a buggy planner could otherwise
        claim that an entity was consumed while emitting an entire-release plan.
        """

        expected = StructuredSemanticValidator._expected_audit_targets(plan)
        observed: dict[str, str] = {}
        question = plan.original_question
        ordered_conditions = sorted(
            planning_audit.extracted_conditions,
            key=lambda item: (item.source_start, item.source_end, item.condition_id),
        )
        previous_end = 0

        for condition in ordered_conditions:
            if condition.source_end > len(question) or (
                question[condition.source_start : condition.source_end] != condition.source_text
            ):
                raise RetrievalRefusal(
                    "condition_unmapped",
                    "planning audit source spans do not match the original question",
                )
            if condition.source_start < previous_end:
                raise RetrievalRefusal(
                    "condition_unmapped",
                    "planning audit conditions overlap and are not consumed exactly once",
                )
            previous_end = condition.source_end

            target = condition.mapped_target
            if target is None or target in observed:
                raise RetrievalRefusal(
                    "condition_unmapped",
                    "planning audit targets must be present and unique",
                )
            expected_kind = expected.get(target)
            if expected_kind is None or condition.condition_kind != expected_kind:
                raise RetrievalRefusal(
                    "condition_unmapped",
                    "planning audit target does not match the emitted query plan",
                )
            observed[target] = condition.condition_kind

        if set(observed) != set(expected):
            raise RetrievalRefusal(
                "condition_unmapped",
                "planning audit does not map every query-plan component exactly once",
            )

    @staticmethod
    def _expected_audit_targets(
        plan: StructuredPlan,
    ) -> dict[
        str,
        Literal["intent", "entity", "logical_operator", "metric", "scope"],
    ]:
        expected: dict[
            str,
            Literal["intent", "entity", "logical_operator", "metric", "scope"],
        ] = {f"intent:{plan.intent}": "intent"}

        if isinstance(plan, AggregatePlan):
            expected[f"metric_key:{plan.metric_key}"] = "metric"

        if isinstance(plan.scope, EntireReleaseScope):
            expected["scope:entire_release"] = "scope"
            return expected

        for query_filter in plan.scope.filters:
            if isinstance(query_filter, (AssemblyFilter, LocusFilter)):
                expected[f"scope.filter:{query_filter.filter_type}"] = "entity"
            elif isinstance(query_filter, (SourceLineageFilter, ViralLineageFilter)):
                prefix = f"scope.filter:{query_filter.filter_type}"
                expected[f"{prefix}.term"] = "entity"
                expected[f"{prefix}.include_descendants"] = "scope"

        for index in range(1, len(plan.scope.filters)):
            expected[f"scope.and:{index}"] = "logical_operator"
        return expected

    @staticmethod
    def _validate_lineage_filter(
        release: ReleaseCapability,
        query_filter: SourceLineageFilter | ViralLineageFilter,
    ) -> None:
        binding = release.lineage_dependencies.get(query_filter.role)
        if binding is None:
            raise RetrievalRefusal(
                "release_dependencies_incomplete",
                f"release is missing the {query_filter.role} lineage dependency",
            )
        if binding.snapshot_key != query_filter.snapshot_key:
            raise RetrievalRefusal(
                "lineage_snapshot_mismatch",
                "lineage filter does not use the release-pinned snapshot",
            )
        if (
            query_filter.include_descendants
            and query_filter.role not in release.complete_lineage_closure_roles
        ):
            raise RetrievalRefusal(
                "lineage_closure_incomplete",
                "descendant traversal is not attested complete for this lineage role",
            )
