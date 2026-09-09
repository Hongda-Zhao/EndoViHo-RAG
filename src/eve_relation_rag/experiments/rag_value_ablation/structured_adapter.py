"""Compile the 25 core-53 structured/hybrid subqueries into the existing grammar.

This experiment-only adapter preserves the scientific question in the manifest and
emits auditable controlled-English subrequests. No SQL or biological identity is
inferred. Production parser, resolver, release and repository gates remain mandatory.
"""

from __future__ import annotations

import secrets

from sqlalchemy import Engine

from eve_relation_rag.application.structured import StructuredQueryApplication
from eve_relation_rag.experiments.rag_value_ablation.association_projection import (
    AssociationProjectionBindings,
    ExactAssociationProjection,
    project_exact_associations,
)
from eve_relation_rag.experiments.rag_value_ablation.complete_sets import collect_complete_loci
from eve_relation_rag.experiments.rag_value_ablation.contracts import QuestionManifest
from eve_relation_rag.experiments.rag_value_ablation.display_labels import (
    ApprovedDisplayLabels,
    apply_display_labels,
)
from eve_relation_rag.experiments.rag_value_ablation.scoped_admission import (
    ApprovedEntityBinding,
    Core53QuestionScope,
    validate_core53_question_scope,
)
from eve_relation_rag.experiments.rag_value_ablation.structured_evidence import (
    StructuredEvidenceGroup,
)
from eve_relation_rag.hybrid.contracts import canonical_model_sha256
from eve_relation_rag.planning.parser import StructuredQueryRequest
from eve_relation_rag.planning.sqlalchemy_resolver import SqlAlchemyReleaseResolverFactory
from eve_relation_rag.retrieval.structured.gate import PublishedReleaseGate
from eve_relation_rag.retrieval.structured.repository import StructuredRepository
from eve_relation_rag.retrieval.structured.results import LocusDetailData, QuerySuccess
from eve_relation_rag.retrieval.structured.service import StructuredRetrievalService


class StructuredAdapterError(ValueError):
    """The exact approved scope cannot be expressed by this adapter."""


def _lineage(binding: ApprovedEntityBinding, *, source: bool) -> str:
    roles = {
        "formal_viral_taxonomy": "formal",
        "study_viral_lineage": "study",
        "extended_viral_lineage": "extended",
    }
    if source:
        if binding.selected_lineage_role != "assembly_source_taxonomy":
            raise StructuredAdapterError("source lineage role mismatch")
        marker = "assigned to source lineage"
    else:
        role = roles.get(binding.selected_lineage_role or "")
        if role is None:
            raise StructuredAdapterError("viral lineage role is not expressible")
        marker = f"with {role} viral lineage"
    suffix = "including descendants" if binding.include_descendants else "exactly"
    return (
        f"{marker} term {binding.selected_stable_key} "
        f"in snapshot {binding.selected_snapshot_key} {suffix}"
    )


def compile_core53_structured_requests(
    scope: Core53QuestionScope,
    manifest: QuestionManifest,
    question_id: str,
) -> tuple[StructuredQueryRequest, ...]:
    """Bind IDs, complete text, family, entity versions and release before compilation."""
    validate_core53_question_scope(scope, manifest, revision="single-source-v2")
    question = next((q for q in manifest.questions if q.question_id == question_id), None)
    if question is None or question.family not in {"structured", "hybrid"}:
        raise StructuredAdapterError(
            "only exact approved structured/hybrid subqueries are supported"
        )
    entities: dict[str, ApprovedEntityBinding] = {
        binding.entity_slot: binding for binding in scope.entities.bindings
    }
    commands: list[str]
    if question_id.startswith(("HOST-S-", "HOST-H-")):
        commands = ["List loci " + _lineage(entities["SOURCE_TAXON_LINEAGE_A"], source=True)]
    elif question_id.startswith(("VIRUS-S-", "VIRUS-H-")):
        commands = ["List loci " + _lineage(entities["VIRAL_LINEAGE_A"], source=False)]
    elif question_id.startswith(("REL-S-", "REL-H-")):
        viral_slots = (
            ("VIRAL_LINEAGE_A", "VIRAL_LINEAGE_B")
            if question_id == "REL-S-04"
            else ("VIRAL_LINEAGE_A",)
        )
        commands = [
            "List loci "
            + _lineage(entities["SOURCE_TAXON_LINEAGE_A"], source=True)
            + " and "
            + _lineage(entities[slot], source=False)
            for slot in viral_slots
        ]
    elif question_id in {"RECORD-S-02", "RECORD-S-04", "RECORD-H-01"}:
        slots = (
            ("EVE_LOCUS_A",)
            if question_id in {"RECORD-S-02", "RECORD-H-01"}
            else (
                "EVE_LOCUS_A",
                "EVE_LOCUS_B",
                "EVE_LOCUS_C",
            )
        )
        commands = ["Show locus " + entities[slot].selected_stable_key for slot in slots]
    elif question_id in {"RECORD-S-01", "RECORD-S-03", "RECORD-H-02"}:
        command = "List loci in assembly " + entities["ASSEMBLY_A"].question_value
        if question_id == "RECORD-S-03":
            command += " and " + _lineage(entities["VIRAL_LINEAGE_A"], source=False)
        commands = [command]
    elif question_id == "UNSUP-09":
        # The approved normalization question has no taxon/locus filter. Its
        # structured component explicitly inventories this fixed release; this
        # is a preregistered query, never a fallback from a refused narrow query.
        commands = ["List all loci in this release"]
    else:
        raise StructuredAdapterError("structured question has no frozen mapping")
    return tuple(
        StructuredQueryRequest(
            release_key=scope.entities.dataset_release_key,
            question=command,
        )
        for command in commands
    )


def build_experiment_structured_application(engine: Engine) -> StructuredQueryApplication:
    """Construct inside the live execution gate; never read production settings."""
    gate = PublishedReleaseGate(engine)
    return StructuredQueryApplication(
        gate=gate,
        resolver_factory=SqlAlchemyReleaseResolverFactory(engine),
        retrieval=StructuredRetrievalService(
            gate=gate,
            repository=StructuredRepository(engine),
            cursor_secret=secrets.token_bytes(32),
        ),
    )


def execute_core53_structured_question(
    application: StructuredQueryApplication,
    scope: Core53QuestionScope,
    manifest: QuestionManifest,
    question_id: str,
    *,
    projection_bindings: AssociationProjectionBindings | None = None,
    approved_projection_binding_sha256: str | None = None,
    display_labels: ApprovedDisplayLabels | None = None,
    approved_display_labels_sha256: str | None = None,
    permitted_document_keys: frozenset[str] = frozenset(),
) -> dict[str, object]:
    """Collect exact subquery sets and hydrate details without generation or scoring."""
    requests = compile_core53_structured_requests(scope, manifest, question_id)
    if display_labels is not None and projection_bindings is None:
        raise StructuredAdapterError("display aliases require exact association projections")
    # Revalidate aliases before any database calls, including an empty input set.
    apply_display_labels(
        (),
        manifest=display_labels,
        approved_manifest_sha256=approved_display_labels_sha256,
        corpus_release_key=str(manifest.corpus_release_key),
        corpus_manifest_sha256=str(manifest.corpus_manifest_sha256),
        permitted_document_keys=permitted_document_keys,
    )
    if (projection_bindings is None) != (approved_projection_binding_sha256 is None):
        raise StructuredAdapterError("projection requires both explicit bindings and approval hash")
    if projection_bindings is not None:
        projection_bindings = AssociationProjectionBindings.model_validate_json(
            projection_bindings.model_dump_json()
        )
        if projection_bindings.binding_sha256 != approved_projection_binding_sha256:
            raise StructuredAdapterError("projection bindings differ from the approved identity")
    groups = []
    answers = []
    for request in requests:
        if request.question.startswith(("List loci ", "List all loci ")):
            complete = collect_complete_loci(request, application.query)
            if (
                complete.pages[0].structured_result.release.manifest_sha256
                != manifest.dataset_manifest_sha256
            ):
                raise StructuredAdapterError("list release differs from approved manifest")
            details = []
            for summary in complete.items:
                response = application.query(
                    StructuredQueryRequest(
                        release_key=request.release_key,
                        question="Show locus " + summary.locus_key,
                    )
                )
                if not isinstance(response, QuerySuccess):
                    raise StructuredAdapterError("detail query failed; complete answer unavailable")
                response = QuerySuccess.model_validate_json(response.model_dump_json())
                if not isinstance(response.structured_result.data, LocusDetailData):
                    raise StructuredAdapterError("detail query returned a different result kind")
                if (
                    response.structured_result.data.locus != summary
                    or response.structured_result.release
                    != complete.pages[0].structured_result.release
                    or response.query_plan.original_question != "Show locus " + summary.locus_key
                    or response.query_plan.release_key != request.release_key
                ):
                    raise StructuredAdapterError(
                        "locus identity or release changed during hydration"
                    )
                details.append(response)
            groups.append({"request": request, "pages": complete.pages, "details": tuple(details)})
        else:
            response = application.query(request)
            if not isinstance(response, QuerySuccess):
                raise StructuredAdapterError("locus query refused; no fallback executed")
            response = QuerySuccess.model_validate_json(response.model_dump_json())
            if (
                not isinstance(response.structured_result.data, LocusDetailData)
                or response.query_plan.original_question != request.question
                or response.query_plan.release_key != request.release_key
                or response.structured_result.data.locus.locus_key
                != request.question.removeprefix("Show locus ").removesuffix(".")
            ):
                raise StructuredAdapterError("expected exact requested locus detail")
            if (
                response.structured_result.release.manifest_sha256
                != manifest.dataset_manifest_sha256
            ):
                raise StructuredAdapterError("detail release differs from the approved manifest")
            groups.append({"request": request, "pages": (), "details": (response,)})
            details = [response]
        projections: tuple[ExactAssociationProjection, ...] = ()
        if projection_bindings is not None:
            projections = tuple(
                project_exact_associations(
                    detail,
                    bindings=projection_bindings,
                    expected_binding_sha256=str(approved_projection_binding_sha256),
                    expected_source_sha256=canonical_model_sha256(detail),
                )
                for detail in details
            )
        groups[-1]["association_projections"] = projections
        groups[-1]["displayed_associations"] = apply_display_labels(
            tuple(association for p in projections for association in p.associations),
            manifest=display_labels,
            approved_manifest_sha256=approved_display_labels_sha256,
            corpus_release_key=str(manifest.corpus_release_key),
            corpus_manifest_sha256=str(manifest.corpus_manifest_sha256),
            permitted_document_keys=permitted_document_keys,
        )
        answers.append(
            {
                "controlled_question": request.question,
                "release_key": request.release_key,
                "complete_for_exact_subquery": True,
                "locus_count": len(details),
                "locus_keys": tuple(
                    sorted(
                        detail.structured_result.data.locus.locus_key
                        for detail in details
                        if isinstance(detail.structured_result.data, LocusDetailData)
                    )
                ),
                "association_projection_complete": projection_bindings is not None,
                "literature_coverage": "not_assessed",
            }
        )
    evidence_groups = tuple(
        StructuredEvidenceGroup.model_validate({
            "request": group["request"], "pages": group["pages"], "details": group["details"],
            "projection_bindings": projection_bindings,
            "association_projections": group["association_projections"],
        })
        for group in groups
    )
    return {
        "question_id": question_id,
        "groups": tuple(groups),
        "evidence_groups": evidence_groups,
        "generation_executed": False,
        "score": None,
        "scope": "exact_structured_subrequests",
        "literature_alignment_performed": False,
        "deterministic_answer": tuple(answers),
    }
