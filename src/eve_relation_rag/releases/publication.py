"""Trusted receipt recording and explicit structured dataset publication."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from eve_relation_rag.db.models import (
    Dataset,
    DatasetRelease,
    DatasetValidationReceipt,
)
from eve_relation_rag.releases.dependencies import (
    release_dependency_graph_sha256,
    verify_release_evidence_bindings,
)
from eve_relation_rag.releases.receipt_integrity import (
    ApprovedDatasetValidationInput,
    DatasetActivationEvidence,
    DatasetCandidateActivationEvidence,
    DatasetCandidateValidationInput,
    build_approved_validation_input,
    build_dataset_candidate_validation_input,
    build_trusted_receipt_evidence,
    receipt_identity,
    validate_persisted_dataset_receipt,
    validation_request_from_payload,
)
from eve_relation_rag.releases.validator import ReleaseValidationRequest
from eve_relation_rag.retrieval.structured.capability import LineageRole

_DATASET_KEY = "dataset:endoviho-rag"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class DatasetPublicationError(RuntimeError):
    """Raised when structured receipt or publication preconditions are not exact."""


class _StrictReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DatasetReceiptReport(_StrictReport):
    """Identity of one newly recorded or exactly replayed trusted receipt."""

    receipt_key: str = Field(pattern=r"^dataset-receipt:sha256:[0-9a-f]{64}$")
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    release_key: str
    status: Literal["validated", "published"]
    replayed: bool


class DatasetPublicationReport(_StrictReport):
    """Stable result of an explicit validated-to-published transition."""

    release_key: str
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["published"]
    published_at: datetime
    replayed: bool


def prepare_dataset_candidate_validation_input(
    engine: Engine,
    *,
    request: ReleaseValidationRequest,
    candidate_activation_evidence: DatasetCandidateActivationEvidence,
    complete_lineage_closure_roles: tuple[LineageRole, ...],
) -> DatasetCandidateValidationInput:
    """Bind an external validator request to one exact live candidate graph.

    The returned self-checksum is the separate human/operational approval
    boundary shared by route benchmarks and clean rebuilds.  It deliberately
    contains no report or receipt identity.
    """

    with engine.connect().execution_options(
        isolation_level="REPEATABLE READ",
        postgresql_readonly=True,
    ) as connection:
        with Session(bind=connection) as session, session.begin():
            release_row = session.execute(
                select(DatasetRelease, Dataset.dataset_key)
                .join(Dataset, Dataset.id == DatasetRelease.dataset_id)
                .where(DatasetRelease.release_key == request.release_key)
            ).one_or_none()
            if release_row is None or release_row.dataset_key != _DATASET_KEY:
                raise DatasetPublicationError("validation input target was not found")
            release = release_row.DatasetRelease
            if release.status != "candidate":
                raise DatasetPublicationError("validation input target is not a candidate")
            if release.manifest_sha256 is None:
                raise DatasetPublicationError("candidate release manifest is missing")
            if (
                candidate_activation_evidence.release_key != release.release_key
                or candidate_activation_evidence.structured_activation_manifest_sha256
                != release.manifest_sha256
            ):
                raise DatasetPublicationError(
                    "candidate activation evidence does not bind the candidate manifest"
                )
            try:
                graph_sha256 = release_dependency_graph_sha256(session, release.id)
                verify_release_evidence_bindings(
                    session,
                    release_id=release.id,
                    request=request,
                    complete_lineage_closure_roles=complete_lineage_closure_roles,
                )
                candidate = build_dataset_candidate_validation_input(
                    release_schema_version=release.schema_version,
                    release_manifest_sha256=release.manifest_sha256,
                    expected_dependency_graph_sha256=graph_sha256,
                    candidate_activation_evidence=candidate_activation_evidence,
                    complete_lineage_closure_roles=complete_lineage_closure_roles,
                    request=request,
                )
            except Exception as exc:
                raise DatasetPublicationError(
                    "candidate database evidence does not match validation request"
                ) from exc
            return candidate


def prepare_dataset_validation_input(
    engine: Engine,
    *,
    candidate_validation_input: DatasetCandidateValidationInput,
    activation_evidence: DatasetActivationEvidence,
) -> ApprovedDatasetValidationInput:
    """Finalize the approved receipt input after all pre-receipt reports exist."""

    candidate = candidate_validation_input
    with engine.connect().execution_options(
        isolation_level="REPEATABLE READ",
        postgresql_readonly=True,
    ) as connection:
        with Session(bind=connection) as session, session.begin():
            release_row = session.execute(
                select(DatasetRelease, Dataset.dataset_key)
                .join(Dataset, Dataset.id == DatasetRelease.dataset_id)
                .where(DatasetRelease.release_key == candidate.release_key)
            ).one_or_none()
            if release_row is None or release_row.dataset_key != _DATASET_KEY:
                raise DatasetPublicationError("validation input target was not found")
            release = release_row.DatasetRelease
            if release.status != "candidate":
                raise DatasetPublicationError("validation input target is not a candidate")
            if (
                release.schema_version != candidate.release_schema_version
                or release.manifest_sha256 != candidate.release_manifest_sha256
                or activation_evidence.release_key != candidate.release_key
                or activation_evidence.candidate_validation_input_sha256
                != candidate.input_sha256
            ):
                raise DatasetPublicationError(
                    "candidate input, activation evidence, and live release differ"
                )
            try:
                graph_sha256 = release_dependency_graph_sha256(session, release.id)
                if graph_sha256 != candidate.expected_dependency_graph_sha256:
                    raise DatasetPublicationError("candidate dependency graph drifted")
                request = validation_request_from_payload(candidate.validation_request)
                verify_release_evidence_bindings(
                    session,
                    release_id=release.id,
                    request=request,
                    complete_lineage_closure_roles=(
                        candidate.complete_lineage_closure_roles
                    ),
                )
                approved = build_approved_validation_input(
                    candidate_validation_input=candidate,
                    activation_evidence=activation_evidence,
                )
                build_trusted_receipt_evidence(
                    approved,
                    dependency_graph_sha256=graph_sha256,
                )
            except DatasetPublicationError:
                raise
            except Exception as exc:
                raise DatasetPublicationError(
                    "candidate database evidence does not match final validation input"
                ) from exc
            return approved


def _validate_existing_receipt(
    *,
    release: DatasetRelease,
    receipt: DatasetValidationReceipt,
    current_graph_sha256: str,
) -> ApprovedDatasetValidationInput:
    try:
        evidence = validate_persisted_dataset_receipt(
            release_key=release.release_key,
            release_schema_version=release.schema_version,
            release_manifest_sha256=release.manifest_sha256 or "",
            current_dependency_graph_sha256=current_graph_sha256,
            receipt_key=receipt.receipt_key,
            receipt_status=receipt.status,
            receipt_trusted=receipt.trusted,
            receipt_manifest_sha256=receipt.manifest_sha256,
            receipt_dependency_graph_sha256=receipt.dependency_graph_sha256,
            receipt_validation_request_sha256=receipt.validation_request_sha256,
            receipt_activation_evidence_sha256=receipt.activation_evidence_sha256,
            receipt_candidate_validation_input_sha256=(
                receipt.candidate_validation_input_sha256
            ),
            receipt_validation_input_sha256=receipt.validation_input_sha256,
            receipt_validation_report_sha256=receipt.validation_report_sha256,
            receipt_validator_code_sha256=receipt.validator_code_sha256,
            receipt_sha256=receipt.receipt_sha256,
            receipt_complete_lineage_closure_roles=receipt.complete_lineage_closure_roles,
            validation_evidence=receipt.validation_evidence,
        )
    except Exception as exc:
        raise DatasetPublicationError("trusted dataset receipt evidence is invalid") from exc
    return evidence.validation_input


def record_dataset_validation_receipt(
    engine: Engine,
    *,
    approved_input: ApprovedDatasetValidationInput,
    approved_input_sha256: str,
) -> DatasetReceiptReport:
    """Validate under a release lock, persist exact evidence, and freeze the candidate."""

    if not _SHA256_RE.fullmatch(approved_input_sha256):
        raise DatasetPublicationError("approved_input_sha256 must be lowercase SHA-256")
    if approved_input.input_sha256 != approved_input_sha256:
        raise DatasetPublicationError("approved validation input checksum does not match")

    with Session(engine) as session, session.begin():
        release_row = session.execute(
            select(DatasetRelease, Dataset.dataset_key)
            .join(Dataset, Dataset.id == DatasetRelease.dataset_id)
            .where(DatasetRelease.release_key == approved_input.release_key)
            .with_for_update(of=DatasetRelease)
        ).one_or_none()
        if release_row is None:
            raise DatasetPublicationError("receipt target release was not found")
        release = release_row.DatasetRelease
        if release_row.dataset_key != _DATASET_KEY:
            raise DatasetPublicationError("receipt target belongs to a different dataset")
        if release.status not in {"candidate", "validated", "published"}:
            raise DatasetPublicationError("receipt target is not in a replayable lifecycle state")
        if (
            release.schema_version != approved_input.release_schema_version
            or release.manifest_sha256 != approved_input.release_manifest_sha256
        ):
            raise DatasetPublicationError("receipt target release differs from approved input")

        try:
            graph_sha256 = release_dependency_graph_sha256(session, release.id)
            evidence, request, _report = build_trusted_receipt_evidence(
                approved_input,
                dependency_graph_sha256=graph_sha256,
            )
            verify_release_evidence_bindings(
                session,
                release_id=release.id,
                request=request,
                complete_lineage_closure_roles=(approved_input.complete_lineage_closure_roles),
            )
        except Exception as exc:
            raise DatasetPublicationError(
                "candidate graph or approved input validation failed"
            ) from exc
        receipt_key, receipt_sha256 = receipt_identity(evidence)
        existing = session.scalar(
            select(DatasetValidationReceipt).where(
                DatasetValidationReceipt.release_id == release.id,
                DatasetValidationReceipt.status == "passed",
                DatasetValidationReceipt.trusted,
            )
        )
        if existing is not None:
            persisted_input = _validate_existing_receipt(
                release=release,
                receipt=existing,
                current_graph_sha256=graph_sha256,
            )
            if (
                persisted_input.input_sha256 != approved_input.input_sha256
                or existing.receipt_key != receipt_key
                or existing.receipt_sha256 != receipt_sha256
            ):
                raise DatasetPublicationError("release already has a different passing receipt")
            if release.status == "candidate":
                release.status = "validated"
                session.flush()
            return DatasetReceiptReport(
                receipt_key=existing.receipt_key,
                receipt_sha256=existing.receipt_sha256,
                release_key=release.release_key,
                status=release.status,
                replayed=True,
            )
        if release.status != "candidate":
            raise DatasetPublicationError("validated release is missing its passing receipt")

        session.add(
            DatasetValidationReceipt(
                receipt_key=receipt_key,
                release_id=release.id,
                status="passed",
                trusted=True,
                manifest_sha256=approved_input.release_manifest_sha256,
                dependency_graph_sha256=graph_sha256,
                validation_request_sha256=approved_input.validation_request_sha256,
                activation_evidence_sha256=approved_input.activation_evidence_sha256,
                candidate_validation_input_sha256=(
                    approved_input.candidate_validation_input_sha256
                ),
                validation_input_sha256=approved_input.input_sha256,
                validation_report_sha256=evidence.validation_report_sha256,
                validator_code_sha256=approved_input.validator_code_sha256,
                receipt_sha256=receipt_sha256,
                complete_lineage_closure_roles=list(approved_input.complete_lineage_closure_roles),
                validation_evidence=evidence.model_dump(mode="json"),
            )
        )
        session.flush()
        release.status = "validated"
        session.flush()

    return DatasetReceiptReport(
        receipt_key=receipt_key,
        receipt_sha256=receipt_sha256,
        release_key=approved_input.release_key,
        status="validated",
        replayed=False,
    )


def publish_dataset_release(
    engine: Engine,
    *,
    release_key: str,
    expected_manifest_sha256: str,
    expected_receipt_sha256: str,
) -> DatasetPublicationReport:
    """Explicitly publish one exact validated release after replaying its receipt."""

    if not _SHA256_RE.fullmatch(expected_manifest_sha256) or not _SHA256_RE.fullmatch(
        expected_receipt_sha256
    ):
        raise DatasetPublicationError("publication requires exact lowercase SHA-256 values")

    with Session(engine) as session, session.begin():
        release_row = session.execute(
            select(DatasetRelease, Dataset.dataset_key)
            .join(Dataset, Dataset.id == DatasetRelease.dataset_id)
            .where(DatasetRelease.release_key == release_key)
            .with_for_update(of=DatasetRelease)
        ).one_or_none()
        if release_row is None or release_row.dataset_key != _DATASET_KEY:
            raise DatasetPublicationError("dataset release was not found")
        release = release_row.DatasetRelease
        if release.manifest_sha256 != expected_manifest_sha256:
            raise DatasetPublicationError("publication manifest checksum mismatch")
        if release.status not in {"validated", "published"}:
            raise DatasetPublicationError("dataset release is not publishable")
        receipt = session.scalar(
            select(DatasetValidationReceipt).where(
                DatasetValidationReceipt.release_id == release.id,
                DatasetValidationReceipt.status == "passed",
                DatasetValidationReceipt.trusted,
                DatasetValidationReceipt.receipt_sha256 == expected_receipt_sha256,
                DatasetValidationReceipt.manifest_sha256 == release.manifest_sha256,
            )
        )
        if receipt is None:
            raise DatasetPublicationError("exact trusted passing receipt was not found")
        graph_sha256 = release_dependency_graph_sha256(session, release.id)
        approved = _validate_existing_receipt(
            release=release,
            receipt=receipt,
            current_graph_sha256=graph_sha256,
        )
        request = validation_request_from_payload(approved.validation_request)
        verify_release_evidence_bindings(
            session,
            release_id=release.id,
            request=request,
            complete_lineage_closure_roles=approved.complete_lineage_closure_roles,
        )

        if release.status == "published" and release.published_at is not None:
            return DatasetPublicationReport(
                release_key=release.release_key,
                manifest_sha256=release.manifest_sha256,
                receipt_sha256=receipt.receipt_sha256,
                status="published",
                published_at=release.published_at,
                replayed=True,
            )
        release.status = "published"
        release.published_at = datetime.now(UTC)
        session.flush()
        published_at = release.published_at

    if published_at is None:  # pragma: no cover - database constraint guarantees this.
        raise DatasetPublicationError("publication timestamp was not persisted")
    return DatasetPublicationReport(
        release_key=release_key,
        manifest_sha256=expected_manifest_sha256,
        receipt_sha256=expected_receipt_sha256,
        status="published",
        published_at=published_at,
        replayed=False,
    )


__all__ = [
    "DatasetPublicationError",
    "DatasetPublicationReport",
    "DatasetReceiptReport",
    "prepare_dataset_candidate_validation_input",
    "prepare_dataset_validation_input",
    "publish_dataset_release",
    "record_dataset_validation_receipt",
]
