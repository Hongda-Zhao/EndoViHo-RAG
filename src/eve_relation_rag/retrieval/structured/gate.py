"""Fail-closed production authorization for immutable published releases."""

from __future__ import annotations

from typing import Final, Literal, cast

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from eve_relation_rag.db.models import Dataset, DatasetRelease, DatasetValidationReceipt
from eve_relation_rag.domain.keys import is_release_key
from eve_relation_rag.releases.dependencies import (
    release_dependency_graph_sha256,
    verify_release_evidence_bindings,
)
from eve_relation_rag.releases.receipt_integrity import (
    validate_persisted_dataset_receipt,
    validation_request_from_payload,
)
from eve_relation_rag.retrieval.structured.capability import (
    LineageRole,
    ReleaseCapability,
    _issue_queryable_release,
)
from eve_relation_rag.retrieval.structured.errors import RetrievalRefusal

_DATASET_KEY: Final = "dataset:endoviho-rag"
_RELEASE_PREFIX: Final = "release:endoviho-rag:v0:"


class PublishedReleaseGate:
    """Authorize only exact published releases after independent receipt replay."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def authorize(self, release_key: str) -> ReleaseCapability:
        """Verify one exact stable release key before any public fact lookup."""

        if not release_key.startswith(_RELEASE_PREFIX) or not is_release_key(release_key):
            raise RetrievalRefusal(
                "release_key_invalid",
                "release_key must be an exact EndoViHo-RAG release key",
            )

        try:
            with self._engine.connect().execution_options(postgresql_readonly=True) as connection:
                with Session(bind=connection) as session, session.begin():
                    row = session.execute(
                        select(
                            Dataset.dataset_key,
                            DatasetRelease.id,
                            DatasetRelease.release_key,
                            DatasetRelease.status,
                            DatasetRelease.schema_version,
                            DatasetRelease.manifest_sha256,
                            DatasetRelease.published_at,
                            DatasetValidationReceipt.receipt_key,
                            DatasetValidationReceipt.status.label("receipt_status"),
                            DatasetValidationReceipt.trusted.label("receipt_trusted"),
                            DatasetValidationReceipt.manifest_sha256.label(
                                "receipt_manifest_sha256"
                            ),
                            DatasetValidationReceipt.dependency_graph_sha256.label(
                                "receipt_dependency_graph_sha256"
                            ),
                            DatasetValidationReceipt.validation_request_sha256.label(
                                "receipt_validation_request_sha256"
                            ),
                            DatasetValidationReceipt.activation_evidence_sha256.label(
                                "receipt_activation_evidence_sha256"
                            ),
                            DatasetValidationReceipt.candidate_validation_input_sha256.label(
                                "receipt_candidate_validation_input_sha256"
                            ),
                            DatasetValidationReceipt.validation_input_sha256.label(
                                "receipt_validation_input_sha256"
                            ),
                            DatasetValidationReceipt.validation_report_sha256.label(
                                "receipt_validation_report_sha256"
                            ),
                            DatasetValidationReceipt.validator_code_sha256.label(
                                "receipt_validator_code_sha256"
                            ),
                            DatasetValidationReceipt.receipt_sha256,
                            DatasetValidationReceipt.complete_lineage_closure_roles,
                            DatasetValidationReceipt.validation_evidence,
                        )
                        .select_from(DatasetRelease)
                        .join(Dataset, Dataset.id == DatasetRelease.dataset_id)
                        .outerjoin(
                            DatasetValidationReceipt,
                            (DatasetValidationReceipt.release_id == DatasetRelease.id)
                            & (DatasetValidationReceipt.status == "passed")
                            & DatasetValidationReceipt.trusted,
                        )
                        .where(DatasetRelease.release_key == release_key)
                    ).one_or_none()

                    if row is None or row.dataset_key != _DATASET_KEY:
                        raise RetrievalRefusal("release_not_found", "release was not found")
                    if row.status != "published":
                        raise RetrievalRefusal(
                            "release_not_published",
                            "release is not published and cannot be queried",
                        )
                    if row.manifest_sha256 is None or len(row.manifest_sha256) != 64:
                        raise RetrievalRefusal(
                            "release_manifest_invalid",
                            "published release manifest is missing or invalid",
                        )
                    if row.published_at is None:
                        raise RetrievalRefusal(
                            "release_manifest_invalid",
                            "published release timestamp is missing",
                        )
                    try:
                        graph_sha256 = release_dependency_graph_sha256(session, row.id)
                        evidence = validate_persisted_dataset_receipt(
                            release_key=row.release_key,
                            release_schema_version=row.schema_version,
                            release_manifest_sha256=row.manifest_sha256,
                            current_dependency_graph_sha256=graph_sha256,
                            receipt_key=row.receipt_key,
                            receipt_status=row.receipt_status,
                            receipt_trusted=row.receipt_trusted,
                            receipt_manifest_sha256=row.receipt_manifest_sha256,
                            receipt_dependency_graph_sha256=(row.receipt_dependency_graph_sha256),
                            receipt_validation_request_sha256=(
                                row.receipt_validation_request_sha256
                            ),
                            receipt_activation_evidence_sha256=(
                                row.receipt_activation_evidence_sha256
                            ),
                            receipt_candidate_validation_input_sha256=(
                                row.receipt_candidate_validation_input_sha256
                            ),
                            receipt_validation_input_sha256=(row.receipt_validation_input_sha256),
                            receipt_validation_report_sha256=(row.receipt_validation_report_sha256),
                            receipt_validator_code_sha256=(row.receipt_validator_code_sha256),
                            receipt_sha256=row.receipt_sha256,
                            receipt_complete_lineage_closure_roles=(
                                row.complete_lineage_closure_roles
                            ),
                            validation_evidence=row.validation_evidence,
                        )
                        approved = evidence.validation_input
                        request = validation_request_from_payload(approved.validation_request)
                        source_dependencies, lineage_dependencies = (
                            verify_release_evidence_bindings(
                                session,
                                release_id=row.id,
                                request=request,
                                complete_lineage_closure_roles=(
                                    approved.complete_lineage_closure_roles
                                ),
                            )
                        )
                    except Exception as exc:
                        raise RetrievalRefusal(
                            "release_dependencies_incomplete",
                            "trusted validation receipt or dependency attestation is invalid",
                        ) from exc
        except RetrievalRefusal:
            raise
        except Exception as exc:
            raise RetrievalRefusal(
                "structured_query_failed",
                "release authorization failed",
                fact_retrieval_executed=False,
            ) from exc

        complete_roles = frozenset[LineageRole](approved.complete_lineage_closure_roles)
        return cast(
            ReleaseCapability,
            _issue_queryable_release(
                release_id=row.id,
                dataset_key=cast(Literal["dataset:endoviho-rag"], row.dataset_key),
                release_key=row.release_key,
                status="published",
                schema_version=row.schema_version,
                published_at=row.published_at,
                manifest_sha256=row.manifest_sha256,
                validation_receipt_key=row.receipt_key,
                validation_receipt_sha256=row.receipt_sha256,
                source_dependencies=source_dependencies,
                lineage_dependencies=lineage_dependencies,
                complete_lineage_closure_roles=complete_roles,
            ),
        )
