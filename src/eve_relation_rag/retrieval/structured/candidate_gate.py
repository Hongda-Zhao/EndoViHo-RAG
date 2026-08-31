"""Validation-only structured capability issuance before a trusted receipt exists."""

from __future__ import annotations

from typing import Final, Literal, cast

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from eve_relation_rag.db.models import Dataset, DatasetRelease
from eve_relation_rag.releases.dependencies import (
    release_dependency_graph_sha256,
    verify_release_evidence_bindings,
)
from eve_relation_rag.releases.receipt_integrity import (
    DatasetCandidateValidationInput,
    release_validator_code_sha256,
    structured_activation_policy_code_sha256,
    structured_candidate_capability_sha256,
    validation_request_from_payload,
)
from eve_relation_rag.releases.validator import validate_release
from eve_relation_rag.retrieval.structured.capability import (
    LineageRole,
    ReleaseCapability,
    _issue_queryable_release,
)
from eve_relation_rag.retrieval.structured.errors import RetrievalRefusal

_DATASET_KEY: Final = "dataset:endoviho-rag"


class ValidatedCandidateReleaseGate:
    """Issue a non-public capability from one exact acyclic candidate input.

    This gate never accepts a request-layer release key and never queries a
    published release.  It is intended only for pre-receipt route benchmarks.
    The production :class:`PublishedReleaseGate` remains receipt-backed.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def bind(self, candidate_input: DatasetCandidateValidationInput) -> BoundCandidateReleaseGate:
        """Bind this issuer to one approved candidate for application wiring."""

        try:
            candidate = DatasetCandidateValidationInput.model_validate_json(
                candidate_input.model_dump_json(), strict=True
            )
        except Exception as exc:
            raise RetrievalRefusal(
                "release_manifest_invalid",
                "candidate validation input is invalid",
            ) from exc
        return BoundCandidateReleaseGate(self, candidate)

    def authorize(self, candidate_input: DatasetCandidateValidationInput) -> ReleaseCapability:
        try:
            candidate = DatasetCandidateValidationInput.model_validate_json(
                candidate_input.model_dump_json(), strict=True
            )
        except Exception as exc:
            raise RetrievalRefusal(
                "release_manifest_invalid",
                "candidate validation input is invalid",
            ) from exc
        activation = candidate.candidate_activation_evidence
        if (
            candidate.validator_code_sha256 != release_validator_code_sha256()
            or activation.activation_policy_code_sha256
            != structured_activation_policy_code_sha256()
        ):
            raise RetrievalRefusal(
                "release_manifest_invalid",
                "candidate validator or activation policy identity is stale",
            )
        request = validation_request_from_payload(candidate.validation_request)
        if not validate_release(request).valid:
            raise RetrievalRefusal(
                "release_dependencies_incomplete",
                "candidate scientific validation does not pass",
            )

        try:
            with self._engine.connect().execution_options(
                isolation_level="REPEATABLE READ",
                postgresql_readonly=True,
            ) as connection:
                with Session(bind=connection) as session, session.begin():
                    row = session.execute(
                        select(
                            Dataset.dataset_key,
                            DatasetRelease.id,
                            DatasetRelease.release_key,
                            DatasetRelease.status,
                            DatasetRelease.schema_version,
                            DatasetRelease.manifest_sha256,
                            DatasetRelease.created_at,
                        )
                        .select_from(DatasetRelease)
                        .join(Dataset, Dataset.id == DatasetRelease.dataset_id)
                        .where(DatasetRelease.release_key == candidate.release_key)
                    ).one_or_none()
                    if row is None or row.dataset_key != _DATASET_KEY:
                        raise RetrievalRefusal(
                            "release_not_found", "candidate release was not found"
                        )
                    if row.status != "candidate":
                        raise RetrievalRefusal(
                            "release_not_published",
                            "validation target is not a candidate release",
                        )
                    if (
                        row.schema_version != candidate.release_schema_version
                        or row.manifest_sha256 != candidate.release_manifest_sha256
                    ):
                        raise RetrievalRefusal(
                            "release_manifest_invalid",
                            "candidate release identity differs from the approved input",
                        )
                    graph_sha256 = release_dependency_graph_sha256(session, row.id)
                    if graph_sha256 != candidate.expected_dependency_graph_sha256:
                        raise RetrievalRefusal(
                            "release_dependencies_incomplete",
                            "candidate dependency graph changed after approval",
                        )
                    source_dependencies, lineage_dependencies = verify_release_evidence_bindings(
                        session,
                        release_id=row.id,
                        request=request,
                        complete_lineage_closure_roles=(candidate.complete_lineage_closure_roles),
                    )
        except RetrievalRefusal:
            raise
        except Exception as exc:
            raise RetrievalRefusal(
                "structured_query_failed",
                "candidate release authorization failed",
                fact_retrieval_executed=False,
            ) from exc

        capability_sha256 = structured_candidate_capability_sha256(candidate)
        return cast(
            ReleaseCapability,
            _issue_queryable_release(
                release_id=row.id,
                dataset_key=cast(Literal["dataset:endoviho-rag"], row.dataset_key),
                release_key=row.release_key,
                status="validation_candidate",
                schema_version=row.schema_version,
                published_at=row.created_at,
                manifest_sha256=row.manifest_sha256,
                validation_receipt_key="validation-candidate:no-receipt",
                validation_receipt_sha256="0" * 64,
                candidate_validation_input_sha256=candidate.input_sha256,
                candidate_capability_sha256=capability_sha256,
                source_dependencies=source_dependencies,
                lineage_dependencies=lineage_dependencies,
                complete_lineage_closure_roles=frozenset[LineageRole](
                    candidate.complete_lineage_closure_roles
                ),
            ),
        )


class BoundCandidateReleaseGate:
    """Application-compatible gate fixed to one pre-approved candidate input."""

    def __init__(
        self,
        issuer: ValidatedCandidateReleaseGate,
        candidate_input: DatasetCandidateValidationInput,
    ) -> None:
        self._issuer = issuer
        self._candidate_input = candidate_input

    @property
    def candidate_validation_input_sha256(self) -> str:
        return self._candidate_input.input_sha256

    def authorize(self, release_key: str) -> ReleaseCapability:
        if release_key != self._candidate_input.release_key:
            raise RetrievalRefusal(
                "release_manifest_invalid",
                "request release differs from the bound validation candidate",
            )
        return self._issuer.authorize(self._candidate_input)


__all__ = ["BoundCandidateReleaseGate", "ValidatedCandidateReleaseGate"]
