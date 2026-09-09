"""Carry complete, separately scoped subqueries into the common generation envelope.

This verifies transported data, not execution authority or scientific annotation.
Each page and hydrated detail retains its original release and query provenance.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import model_validator

from eve_relation_rag.experiments.rag_value_ablation.association_projection import (
    AssociationProjectionBindings,
    ExactAssociationProjection,
    project_exact_associations,
)
from eve_relation_rag.experiments.rag_value_ablation.complete_sets import collect_complete_loci
from eve_relation_rag.hybrid.contracts import canonical_model_sha256
from eve_relation_rag.literature.contracts import StrictFrozenSchema
from eve_relation_rag.planning.parser import StructuredQueryRequest
from eve_relation_rag.retrieval.structured.results import LocusDetailData, QuerySuccess


class StructuredEvidenceGroup(StrictFrozenSchema):
    """One complete list with details, or one exact locus detail; never a merged scope."""

    request: StructuredQueryRequest
    pages: tuple[QuerySuccess, ...] = ()
    details: tuple[QuerySuccess, ...] = ()
    projection_bindings: AssociationProjectionBindings | None = None
    association_projections: tuple[ExactAssociationProjection, ...] = ()
    completeness_scope: Literal["exact_subquery_only"] = "exact_subquery_only"
    literature_alignment_performed: Literal[False] = False

    @model_validator(mode="after")
    def validate_group(self) -> Self:
        details = tuple(QuerySuccess.model_validate_json(d.model_dump_json()) for d in self.details)
        if any(not isinstance(d.structured_result.data, LocusDetailData) for d in details):
            raise ValueError("structured evidence group requires locus details")
        if self.pages:
            responses = iter(self.pages)

            def replay(request: StructuredQueryRequest) -> QuerySuccess:
                try:
                    return next(responses)
                except StopIteration as exc:
                    raise ValueError("structured evidence pages are incomplete") from exc

            complete = collect_complete_loci(self.request, replay, max_pages=len(self.pages))
            if len(complete.pages) != len(self.pages):
                raise ValueError("structured evidence contains pages after the terminal page")
            expected = complete.items
            observed = tuple(
                d.structured_result.data.locus for d in details
                if isinstance(d.structured_result.data, LocusDetailData)
            )
            if observed != expected:
                raise ValueError("hydrated details do not match the complete ordered locus set")
            release = self.pages[0].structured_result.release
            for detail, locus in zip(details, expected, strict=True):
                if detail.query_plan.original_question != "Show locus " + locus.locus_key:
                    raise ValueError("hydrated detail query does not match its locus")
                if detail.structured_result.release != release:
                    raise ValueError("release changed between list and detail")
        else:
            if len(details) != 1:
                raise ValueError("a direct locus query requires exactly one detail")
            detail = details[0]
            data = detail.structured_result.data
            assert isinstance(data, LocusDetailData)
            if (
                detail.query_plan.original_question != self.request.question
                or self.request.question.removesuffix(".") != "Show locus " + data.locus.locus_key
            ):
                raise ValueError("direct detail differs from the requested locus")
        for detail in details:
            if detail.query_plan.release_key != self.request.release_key:
                raise ValueError("detail differs from the requested release")
        if self.projection_bindings is None:
            if self.association_projections:
                raise ValueError("association projections require their source bindings")
        else:
            bindings = AssociationProjectionBindings.model_validate_json(
                self.projection_bindings.model_dump_json()
            )
            if self.pages and self.pages[0].structured_result.release != bindings.release:
                raise ValueError("projection bindings differ from the list release")
            expected_projections = tuple(
                project_exact_associations(
                    detail, bindings=bindings,
                    expected_binding_sha256=bindings.binding_sha256,
                    expected_source_sha256=canonical_model_sha256(detail),
                ) for detail in details
            )
            if self.association_projections != expected_projections:
                raise ValueError("association projection does not reproduce from its source")
        return self

    def visible(self) -> dict[str, object]:
        """Expose subquery scope and source facts without any Gold or review fields."""
        return {
            "controlled_question": self.request.question,
            "completeness_scope": self.completeness_scope,
            "literature_alignment_performed": False,
            "pages": [p.structured_result.model_dump(mode="json") for p in self.pages],
            "details": [d.structured_result.model_dump(mode="json") for d in self.details],
            "associations": [a.model_dump(mode="json") for p in self.association_projections
                             for a in p.associations],
        }
