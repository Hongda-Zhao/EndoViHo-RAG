from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event, func, inspect, select, text, update
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from eve_relation_rag.config import get_settings
from eve_relation_rag.db import Base
from eve_relation_rag.db.models import (
    AssemblySequence,
    AssemblyTaxonAssignment,
    Dataset,
    DatasetRelease,
    DatasetValidationReceipt,
    DetectionCall,
    EVELocus,
    EVELocusPlacement,
    FlankAssessment,
    GenomeAssembly,
    ImportLedger,
    ImportRun,
    InclusionDecision,
    LineageClosure,
    LineageSnapshot,
    LineageTerm,
    MethodDefinition,
    ProcessRun,
    QuarantineIssue,
    ReleaseAssemblyMembership,
    ReleaseLineageSnapshot,
    ReleaseLocusMembership,
    ReleaseMethodDefinition,
    ReleaseSourceSnapshot,
    SourceArtifact,
    SourceAssessment,
    SourceRecord,
    SourceSnapshot,
)
from eve_relation_rag.releases.publication import (
    DatasetPublicationError,
    prepare_dataset_candidate_validation_input,
    prepare_dataset_validation_input,
    publish_dataset_release,
    record_dataset_validation_receipt,
)
from eve_relation_rag.releases.receipt_integrity import (
    ApprovedDatasetValidationInput,
    build_dataset_activation_evidence,
    build_dataset_candidate_activation_evidence,
)
from eve_relation_rag.releases.validator import ReleaseValidationRequest
from eve_relation_rag.retrieval.structured.errors import RetrievalRefusal
from eve_relation_rag.retrieval.structured.gate import PublishedReleaseGate
from tests.test_release_validator import _request

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 29, 12, tzinfo=UTC)
RELEASE_MANIFEST_SHA256 = "d" * 64
RELEASE_SCHEMA_VERSION = "endoviho-structured-v0"
SHA_E = "e" * 64
SHA_F = "f" * 64


@pytest.fixture(scope="module")
def postgres_engine() -> Iterator[Engine]:
    database_url = os.environ.get("EVE_RAG_TEST_DATABASE_URL", get_settings().database_url)
    admin_engine = create_engine(database_url, poolclass=NullPool)
    schema = f"test_dataset_publication_{uuid4().hex}"
    try:
        with admin_engine.begin() as connection:
            connection.execute(CreateSchema(schema))
    except OperationalError as exc:
        admin_engine.dispose()
        pytest.skip(f"PostgreSQL integration database is unavailable: {exc.orig}")

    with admin_engine.connect() as connection:
        connection.exec_driver_sql(f'SET search_path TO "{schema}", public')
        connection.commit()
        _upgrade_to_head(connection)

    def set_fixture_search_path(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        try:
            cursor.execute(f'SET search_path TO "{schema}", public')
        finally:
            cursor.close()
        dbapi_connection.commit()  # type: ignore[union-attr]

    event.listen(admin_engine, "connect", set_fixture_search_path)
    admin_engine.dispose()
    engine = admin_engine.execution_options(schema_translate_map={None: schema})
    try:
        with Session(engine) as session:
            _insert_global_dependencies(session)
            session.commit()
        yield engine
    finally:
        engine.dispose()
        event.remove(admin_engine, "connect", set_fixture_search_path)
        with admin_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin_engine.dispose()


def test_fresh_head_contains_receipt_and_requires_it_for_promotion(
    postgres_engine: Engine,
) -> None:
    request = _insert_candidate(postgres_engine, base_id=1_000)
    with postgres_engine.connect() as connection:
        database = inspect(connection)
        assert "dataset_validation_receipt" in database.get_table_names()
        assert "uq_dataset_validation_receipt_passing_release" in {
            item["name"] for item in database.get_indexes("dataset_validation_receipt")
        }

    with Session(postgres_engine) as session:
        with pytest.raises(DBAPIError, match="trusted passing dataset validation receipt"):
            session.execute(
                update(DatasetRelease)
                .where(DatasetRelease.release_key == request.release_key)
                .values(status="published", published_at=NOW)
            )
            session.commit()
        session.rollback()


def test_fresh_head_matches_orm_metadata(postgres_engine: Engine) -> None:
    with postgres_engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"compare_type": True, "target_metadata": Base.metadata},
        )

        assert compare_metadata(context, Base.metadata) == []


def test_candidate_child_write_serializes_with_validation_promotion(
    postgres_engine: Engine,
) -> None:
    request = _insert_candidate(postgres_engine, base_id=8_000)
    with (
        postgres_engine.connect() as child_writer,
        postgres_engine.connect() as release_promoter,
    ):
        child_transaction = child_writer.begin()
        promoter_transaction = release_promoter.begin()
        try:
            child_writer.execute(
                update(EVELocus)
                .where(EVELocus.id == 8_060)
                .values(created_at=datetime(2026, 8, 29, 13, tzinfo=UTC))
            )
            release_promoter.exec_driver_sql("SET LOCAL lock_timeout = '250ms'")
            with pytest.raises(DBAPIError, match="lock timeout"):
                release_promoter.execute(
                    update(DatasetRelease)
                    .where(DatasetRelease.release_key == request.release_key)
                    .values(status="validated")
                )
        finally:
            if promoter_transaction.is_active:
                promoter_transaction.rollback()
            if child_transaction.is_active:
                child_transaction.rollback()

    with Session(postgres_engine) as session:
        release = session.get(DatasetRelease, 8_010)
        locus = session.get(EVELocus, 8_060)
        assert release is not None and release.status == "candidate"
        assert locus is not None and locus.created_at != datetime(
            2026,
            8,
            29,
            13,
            tzinfo=UTC,
        )


def test_receipt_and_publication_are_idempotent_and_gate_issues_capability(
    postgres_engine: Engine,
) -> None:
    request = _insert_candidate(postgres_engine, base_id=2_000)
    approved = _prepare(postgres_engine, request)

    first_receipt = record_dataset_validation_receipt(
        postgres_engine,
        approved_input=approved,
        approved_input_sha256=approved.input_sha256,
    )
    replayed_receipt = record_dataset_validation_receipt(
        postgres_engine,
        approved_input=approved,
        approved_input_sha256=approved.input_sha256,
    )
    assert first_receipt.status == "validated"
    assert first_receipt.replayed is False
    assert replayed_receipt.replayed is True
    assert replayed_receipt.receipt_sha256 == first_receipt.receipt_sha256

    with Session(postgres_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(DatasetValidationReceipt)
                .where(DatasetValidationReceipt.release_id == 2_010)
            )
            == 1
        )

    first_publication = publish_dataset_release(
        postgres_engine,
        release_key=request.release_key,
        expected_manifest_sha256=RELEASE_MANIFEST_SHA256,
        expected_receipt_sha256=first_receipt.receipt_sha256,
    )
    replayed_publication = publish_dataset_release(
        postgres_engine,
        release_key=request.release_key,
        expected_manifest_sha256=RELEASE_MANIFEST_SHA256,
        expected_receipt_sha256=first_receipt.receipt_sha256,
    )
    assert first_publication.replayed is False
    assert replayed_publication.replayed is True

    capability = PublishedReleaseGate(postgres_engine).authorize(request.release_key)
    assert capability.status == "published"
    assert capability.release_id == 2_010
    assert capability.validation_receipt_sha256 == first_receipt.receipt_sha256
    assert capability.complete_lineage_closure_roles == frozenset(
        {"assembly_source_taxonomy", "formal_viral_taxonomy"}
    )


def test_operational_timestamp_does_not_change_semantic_graph(
    postgres_engine: Engine,
) -> None:
    request = _insert_candidate(postgres_engine, base_id=3_000)
    approved = _prepare(postgres_engine, request)

    with Session(postgres_engine) as session:
        locus = session.get(EVELocus, 3_060)
        assert locus is not None
        locus.created_at = datetime(2026, 8, 29, 13, tzinfo=UTC)
        session.commit()

    report = record_dataset_validation_receipt(
        postgres_engine,
        approved_input=approved,
        approved_input_sha256=approved.input_sha256,
    )
    assert report.status == "validated"


def test_quarantine_issue_drift_refuses_receipt(postgres_engine: Engine) -> None:
    request = _insert_candidate(postgres_engine, base_id=9_000)
    _insert_quarantine_issue(postgres_engine, base_id=9_000)
    approved = _prepare(postgres_engine, request)

    with Session(postgres_engine) as session:
        issue = session.get(QuarantineIssue, 9_120)
        assert issue is not None
        issue.message = "drifted after approval"
        session.commit()

    with pytest.raises(DatasetPublicationError, match="approved input"):
        record_dataset_validation_receipt(
            postgres_engine,
            approved_input=approved,
            approved_input_sha256=approved.input_sha256,
        )


def test_validated_quarantine_issue_is_database_immutable(
    postgres_engine: Engine,
) -> None:
    request = _insert_candidate(postgres_engine, base_id=10_000)
    _insert_quarantine_issue(postgres_engine, base_id=10_000)
    approved = _prepare(postgres_engine, request)
    record_dataset_validation_receipt(
        postgres_engine,
        approved_input=approved,
        approved_input_sha256=approved.input_sha256,
    )

    with Session(postgres_engine) as session:
        issue = session.get(QuarantineIssue, 10_120)
        assert issue is not None
        issue.message = "forged after validation"
        with pytest.raises(DBAPIError, match="quarantine issues are immutable"):
            session.commit()
        session.rollback()


def test_direct_sql_shape_forgery_cannot_unlock_validation(
    postgres_engine: Engine,
) -> None:
    request = _insert_candidate(postgres_engine, base_id=11_000)
    with Session(postgres_engine) as session:
        release = session.scalar(
            select(DatasetRelease).where(DatasetRelease.release_key == request.release_key)
        )
        assert release is not None
        session.add(
            DatasetValidationReceipt(
                id=11_120,
                receipt_key=f"dataset-receipt:sha256:{'a' * 64}",
                release_id=release.id,
                status="passed",
                trusted=True,
                manifest_sha256=RELEASE_MANIFEST_SHA256,
                dependency_graph_sha256="b" * 64,
                validation_request_sha256="c" * 64,
                activation_evidence_sha256="d" * 64,
                candidate_validation_input_sha256="3" * 64,
                validation_input_sha256="e" * 64,
                validation_report_sha256="f" * 64,
                validator_code_sha256="1" * 64,
                receipt_sha256="2" * 64,
                complete_lineage_closure_roles=[],
                validation_evidence={},
            )
        )
        with pytest.raises(DBAPIError, match="incomplete or incoherent"):
            session.commit()
        session.rollback()


def test_input_builder_refuses_candidate_evidence_not_equal_to_database(
    postgres_engine: Engine,
) -> None:
    request = _insert_candidate(postgres_engine, base_id=6_000)
    with Session(postgres_engine) as session:
        call = session.get(DetectionCall, 6_080)
        assert call is not None
        call.source_method_key = "method:forged"
        session.commit()

    with pytest.raises(DatasetPublicationError, match="does not match"):
        _prepare(postgres_engine, request)


def test_validated_rows_and_receipts_are_database_immutable(
    postgres_engine: Engine,
) -> None:
    request = _insert_candidate(postgres_engine, base_id=4_000)
    approved = _prepare(postgres_engine, request)
    receipt_report = record_dataset_validation_receipt(
        postgres_engine,
        approved_input=approved,
        approved_input_sha256=approved.input_sha256,
    )

    with Session(postgres_engine) as session:
        with pytest.raises(DBAPIError, match="release-scoped rows are immutable"):
            session.execute(
                update(EVELocus).where(EVELocus.id == 4_060).values(native_vr_token="forged")
            )
            session.commit()
        session.rollback()

    with Session(postgres_engine) as session:
        with pytest.raises(DBAPIError, match="receipts are immutable"):
            session.execute(
                update(DatasetValidationReceipt)
                .where(DatasetValidationReceipt.receipt_sha256 == receipt_report.receipt_sha256)
                .values(validation_report_sha256=SHA_F)
            )
            session.commit()
        session.rollback()

    with Session(postgres_engine) as session:
        session.add(
            SourceRecord(
                id=9_999,
                source_record_key="source-record:forged-after-validation",
                snapshot_id=20,
                artifact_id=21,
                worksheet="forged",
                row_number=99_999,
                native_vr_token="forged-after-validation",
                assembly_accession_version="GCA_945859735.2",
                sequence_accession_version="CAMAOU020000182.1",
                source_locator={"forged": True},
                raw_payload={"forged": True},
                raw_payload_sha256=SHA_F,
            )
        )
        with pytest.raises(DBAPIError, match="source snapshot is immutable"):
            session.commit()
        session.rollback()


def test_gate_independently_refuses_privileged_receipt_tampering(
    postgres_engine: Engine,
) -> None:
    request = _insert_candidate(postgres_engine, base_id=5_000)
    approved = _prepare(postgres_engine, request)
    receipt = record_dataset_validation_receipt(
        postgres_engine,
        approved_input=approved,
        approved_input_sha256=approved.input_sha256,
    )
    publish_dataset_release(
        postgres_engine,
        release_key=request.release_key,
        expected_manifest_sha256=RELEASE_MANIFEST_SHA256,
        expected_receipt_sha256=receipt.receipt_sha256,
    )

    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE dataset_validation_receipt "
                "DISABLE TRIGGER trg_dataset_validation_receipt_immutable"
            )
        )
        connection.execute(
            text(
                "UPDATE dataset_validation_receipt "
                "SET validation_report_sha256 = :forged "
                "WHERE release_id = :release_id"
            ),
            {"forged": SHA_F, "release_id": 5_010},
        )
        connection.execute(
            text(
                "ALTER TABLE dataset_validation_receipt "
                "ENABLE TRIGGER trg_dataset_validation_receipt_immutable"
            )
        )

    with pytest.raises(RetrievalRefusal) as refusal:
        PublishedReleaseGate(postgres_engine).authorize(request.release_key)
    assert refusal.value.code == "release_dependencies_incomplete"


def test_gate_refuses_privileged_lineage_closure_tampering(
    postgres_engine: Engine,
) -> None:
    request = _insert_candidate(postgres_engine, base_id=7_000)
    approved = _prepare(postgres_engine, request)
    receipt = record_dataset_validation_receipt(
        postgres_engine,
        approved_input=approved,
        approved_input_sha256=approved.input_sha256,
    )
    publish_dataset_release(
        postgres_engine,
        release_key=request.release_key,
        expected_manifest_sha256=RELEASE_MANIFEST_SHA256,
        expected_receipt_sha256=receipt.receipt_sha256,
    )

    with postgres_engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE lineage_closure DISABLE TRIGGER trg_lineage_closure_append_only")
        )
        connection.execute(
            text(
                "DELETE FROM lineage_closure "
                "WHERE snapshot_id = 200 "
                "AND ancestor_term_id = 300 "
                "AND descendant_term_id = 302"
            )
        )
        connection.execute(
            text("ALTER TABLE lineage_closure ENABLE TRIGGER trg_lineage_closure_append_only")
        )

    with pytest.raises(RetrievalRefusal) as refusal:
        PublishedReleaseGate(postgres_engine).authorize(request.release_key)
    assert refusal.value.code == "release_dependencies_incomplete"


def _prepare(
    engine: Engine,
    request: ReleaseValidationRequest,
) -> ApprovedDatasetValidationInput:
    candidate_activation_evidence = build_dataset_candidate_activation_evidence(
        release_key=request.release_key,
        structured_activation_manifest_sha256=RELEASE_MANIFEST_SHA256,
        source_manifest_sha256="1" * 64,
        source_audit_sha256="2" * 64,
        ncbi_artifact_manifest_sha256="3" * 64,
        ncbi_snapshot_manifest_sha256="4" * 64,
        ictv_artifact_manifest_sha256="5" * 64,
        ictv_snapshot_manifest_sha256="6" * 64,
        flank_manifest_sha256="7" * 64,
        inclusion_manifest_sha256="8" * 64,
        adjudication_manifest_sha256="9" * 64,
        public_locus_membership_manifest_sha256="a" * 64,
        public_assertion_membership_manifest_sha256="b" * 64,
    )
    candidate = prepare_dataset_candidate_validation_input(
        engine,
        request=request,
        candidate_activation_evidence=candidate_activation_evidence,
        complete_lineage_closure_roles=(
            "assembly_source_taxonomy",
            "formal_viral_taxonomy",
        ),
    )
    activation_evidence = build_dataset_activation_evidence(
        candidate_validation_input_sha256=candidate.input_sha256,
        release_key=request.release_key,
        clean_rebuild_report_sha256="c" * 64,
        structured_benchmark_report_sha256="d" * 64,
        hybrid_benchmark_report_sha256="e" * 64,
        human_review_report_sha256="f" * 64,
    )
    return prepare_dataset_validation_input(
        engine,
        candidate_validation_input=candidate,
        activation_evidence=activation_evidence,
    )


def _request_for(base_id: int) -> ReleaseValidationRequest:
    original = _request()
    candidate = replace(
        original.candidates[0],
        import_run_key=f"import-run:dataset-publication:{base_id}",
    )
    return replace(
        original,
        release_key=f"release:endoviho-rag:v0:20260829:{base_id // 1000:03d}",
        candidates=(candidate,),
    )


def _insert_quarantine_issue(engine: Engine, *, base_id: int) -> None:
    with Session(engine) as session:
        session.add(
            QuarantineIssue(
                id=base_id + 120,
                issue_key=f"quarantine-issue:dataset-publication:{base_id}",
                ledger_id=base_id + 90,
                issue_code="fixture-review",
                severity="warning",
                status="resolved",
                field_name="fixture",
                message="Resolved fixture issue retained for graph integrity.",
                details={"fixture": base_id},
            )
        )
        session.commit()


def _insert_candidate(engine: Engine, *, base_id: int) -> ReleaseValidationRequest:
    request = _request_for(base_id)
    candidate = request.candidates[0]
    placement = candidate.placements[0]
    left, right = candidate.flank_assessments
    release_id = base_id + 10
    with Session(engine) as session:
        session.add(
            DatasetRelease(
                id=release_id,
                dataset_id=1,
                release_key=request.release_key,
                schema_version=RELEASE_SCHEMA_VERSION,
                status="candidate",
                manifest_sha256=RELEASE_MANIFEST_SHA256,
            )
        )
        session.flush()
        session.add(
            ReleaseSourceSnapshot(
                release_id=release_id,
                source_snapshot_id=20,
                role="primary_data",
            )
        )
        session.add_all(
            (
                ReleaseLineageSnapshot(
                    release_id=release_id,
                    snapshot_id=200,
                    role="assembly_source_taxonomy",
                    domain="host",
                    scheme_kind="formal_taxonomy",
                ),
                ReleaseLineageSnapshot(
                    release_id=release_id,
                    snapshot_id=201,
                    role="formal_viral_taxonomy",
                    domain="viral",
                    scheme_kind="formal_taxonomy",
                ),
            )
        )
        session.add(ReleaseAssemblyMembership(release_id=release_id, assembly_id=500))
        session.flush()
        session.add(
            AssemblyTaxonAssignment(
                id=base_id + 20,
                assignment_key=f"assignment:dataset-publication:{base_id}",
                release_id=release_id,
                assembly_id=500,
                snapshot_id=200,
                snapshot_role="assembly_source_taxonomy",
                term_id=300,
                assignment_policy_key="policy:ncbi-source-taxon:v1",
                source_artifact_id=21,
                source_locator={"fixture": base_id},
            )
        )
        session.add(
            ReleaseMethodDefinition(
                release_id=release_id,
                method_definition_id=30,
                role="source_import",
            )
        )
        session.flush()
        session.add(
            ImportRun(
                id=base_id + 40,
                run_key=candidate.import_run_key,
                release_id=release_id,
                source_snapshot_id=20,
                source_artifact_id=21,
                importer_name="dataset-publication-fixture",
                importer_version="v1",
                code_sha256=SHA_E,
                parameters={},
                parameters_sha256=SHA_F,
                status="succeeded",
                started_at=NOW,
                finished_at=NOW,
            )
        )
        session.flush()
        session.add(
            ProcessRun(
                id=base_id + 50,
                process_run_key=f"process-run:dataset-publication:{base_id}",
                release_id=release_id,
                method_definition_id=30,
                method_role="source_import",
                import_run_id=base_id + 40,
                execution_status="succeeded",
                software_agent_key="fixture-agent",
                parameters={},
                parameters_sha256=SHA_E,
                started_at=NOW,
                finished_at=NOW,
            )
        )
        session.add(
            EVELocus(
                id=base_id + 60,
                locus_key=candidate.locus_key,
                release_id=release_id,
                assembly_id=500,
                sequence_id=510,
                source_snapshot_id=20,
                source_record_id=600,
                native_vr_token=candidate.identity.native_vr_token,
                identity_policy_key=candidate.identity.identity_policy_version,
            )
        )
        session.flush()
        session.add(
            EVELocusPlacement(
                id=base_id + 70,
                placement_key=f"placement:dataset-publication:{base_id}",
                release_id=release_id,
                locus_id=base_id + 60,
                assembly_id=500,
                sequence_id=510,
                start0=placement.start0,
                end0=placement.end0,
                strand="unknown",
                precision=placement.precision,
                coordinate_system=placement.coordinate_system,
                source_artifact_id=21,
                source_locator={"provenance_key": placement.provenance_key},
                placement_sha256=SHA_E,
            )
        )
        session.add(
            DetectionCall(
                id=base_id + 80,
                call_key=f"call:dataset-publication:{base_id}",
                release_id=release_id,
                source_snapshot_id=20,
                source_record_id=600,
                locus_id=base_id + 60,
                process_run_id=base_id + 50,
                process_run_status="succeeded",
                source_method_key=candidate.method_key,
                source_locator={"fixture": base_id},
                raw_result={
                    "assembly_resolution": candidate.assembly_resolution,
                    "contig_resolution": candidate.contig_resolution,
                    "fixture": True,
                },
            )
        )
        session.flush()
        session.add(
            SourceAssessment(
                id=base_id + 81,
                assessment_key=f"assessment:dataset-publication:{base_id}",
                release_id=release_id,
                call_id=base_id + 80,
                process_run_id=base_id + 50,
                assessment_type="hcvr",
                source_label="Yes",
                confidence=candidate.source_assessment,
                source_artifact_id=21,
                source_locator={"fixture": base_id},
            )
        )
        session.add(
            ImportLedger(
                id=base_id + 90,
                run_id=base_id + 40,
                release_id=release_id,
                source_record_id=600,
                call_id=base_id + 80,
                locus_id=base_id + 60,
                outcome="normalized_candidate",
                result_payload={"fixture": base_id},
                result_sha256=SHA_E,
                processed_at=NOW,
            )
        )
        session.flush()
        for assessment_id, evidence_artifact_id, flank in (
            (base_id + 100, 22, left),
            (base_id + 101, 23, right),
        ):
            session.add(
                FlankAssessment(
                    id=assessment_id,
                    assessment_key=f"flank:dataset-publication:{base_id}:{flank.side}",
                    release_id=release_id,
                    locus_id=base_id + 60,
                    placement_id=base_id + 70,
                    side=flank.side,
                    verdict=flank.verdict,
                    inspection_window_bp=flank.inspection_window_bp,
                    available_bp=flank.available_bp,
                    inspected_bp=flank.inspected_bp,
                    assessment_policy_key=flank.policy_key,
                    method_or_curator_key=flank.method_or_curator_key,
                    evidence_artifact_id=evidence_artifact_id,
                    evidence_locator={"evidence_key": flank.evidence_key},
                    assessed_at=NOW,
                )
            )
        session.add(
            InclusionDecision(
                id=base_id + 110,
                decision_key=f"decision:dataset-publication:{base_id}",
                release_id=release_id,
                locus_id=base_id + 60,
                placement_id=base_id + 70,
                import_ledger_id=base_id + 90,
                import_outcome="normalized_candidate",
                decision_code=candidate.inclusion.decision,
                policy_key=candidate.inclusion.policy_key,
                authorized_by=candidate.inclusion.authorized_by,
                reason_code="fixture-supported",
                rationale="Synthetic trusted-receipt fixture.",
                decided_at=NOW,
            )
        )
        session.flush()
        session.add(
            ReleaseLocusMembership(
                release_id=release_id,
                locus_id=base_id + 60,
                placement_id=base_id + 70,
                placement_precision="exact",
                inclusion_decision_id=base_id + 110,
                decision_code="include",
                left_flank_assessment_id=base_id + 100,
                left_flank_side="left",
                left_flank_verdict="supported",
                right_flank_assessment_id=base_id + 101,
                right_flank_side="right",
                right_flank_verdict="supported",
            )
        )
        session.commit()
    return request


def _insert_global_dependencies(session: Session) -> None:
    request = _request()
    source = request.source
    ncbi = request.ncbi_taxonomy
    ictv = request.ictv
    candidate = request.candidates[0]
    assert ncbi is not None
    assert ictv is not None
    session.add(Dataset(id=1, dataset_key="dataset:endoviho-rag", title="Synthetic V0"))
    session.add(
        SourceSnapshot(
            id=20,
            snapshot_key=source.source_snapshot_key,
            source_name="Zhao et al. Data S1",
            source_version="v4",
            source_uri=source.provenance_uri,
            retrieved_at=source.remote_retrieved_at,
            declared_manifest_sha256=source.manifest_sha256,
            verified_manifest_sha256=source.verified_manifest_sha256,
            declared_license_key=source.license_key,
            verified_license_key=source.verified_license_key,
        )
    )
    session.flush()
    left, right = candidate.flank_assessments
    session.add_all(
        (
            SourceArtifact(
                id=21,
                snapshot_id=20,
                artifact_key=source.artifact_key,
                filename="data-s1.xlsx",
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                byte_size=1,
                declared_sha256=source.artifact_sha256,
                verified_sha256=source.verified_artifact_sha256,
                source_uri=source.provenance_uri,
                retrieved_at=source.remote_retrieved_at,
                declared_license_key=source.license_key,
                verified_license_key=source.verified_license_key,
                remote_checksum_verified=True,
                remote_verification_at=source.remote_retrieved_at,
                remote_verification_uri=source.remote_artifact_uri,
            ),
            SourceArtifact(
                id=22,
                snapshot_id=20,
                artifact_key=left.evidence_key,
                filename="left-flank.json",
                media_type="application/json",
                byte_size=1,
                verified_sha256=left.evidence_sha256,
                source_uri="https://example.invalid/left-flank.json",
                retrieved_at=NOW,
                verified_license_key="internal-curation",
            ),
            SourceArtifact(
                id=23,
                snapshot_id=20,
                artifact_key=right.evidence_key,
                filename="right-flank.json",
                media_type="application/json",
                byte_size=1,
                verified_sha256=right.evidence_sha256,
                source_uri="https://example.invalid/right-flank.json",
                retrieved_at=NOW,
                verified_license_key="internal-curation",
            ),
        )
    )
    session.flush()
    session.add(
        MethodDefinition(
            id=30,
            method_definition_key="method-definition:zhao-v4-hcvr:v1",
            method_key=candidate.method_key,
            version="v1",
            method_kind="source_import",
            definition_artifact_id=21,
            definition_sha256=SHA_E,
            parameter_schema={},
            output_schema={},
        )
    )
    session.add_all(
        (
            LineageSnapshot(
                id=200,
                snapshot_key=ncbi.snapshot_key,
                domain="host",
                scheme_kind="formal_taxonomy",
                authority_namespace=ncbi.authority,
                version=ncbi.version,
                source_artifact_id=21,
                snapshot_sha256=ncbi.verified_artifact_sha256,
            ),
            LineageSnapshot(
                id=201,
                snapshot_key=ictv.msl_snapshot_key,
                domain="viral",
                scheme_kind="formal_taxonomy",
                authority_namespace="ICTV",
                version=ictv.msl_version,
                source_artifact_id=21,
                snapshot_sha256=ictv.verified_msl_artifact_sha256,
            ),
        )
    )
    session.flush()
    session.add_all(
        (
            LineageTerm(
                id=300,
                snapshot_id=200,
                term_key="taxon:fixture-host",
                canonical_name="Fixture host",
                rank="species",
            ),
            LineageTerm(
                id=301,
                snapshot_id=201,
                term_key="taxon:fixture-virus",
                canonical_name="Fixture virus",
                rank="species",
            ),
            LineageTerm(
                id=302,
                snapshot_id=200,
                term_key="taxon:fixture-host-child",
                canonical_name="Fixture host child",
                rank="species",
            ),
        )
    )
    session.flush()
    session.add_all(
        (
            LineageClosure(
                snapshot_id=200,
                ancestor_term_id=300,
                descendant_term_id=300,
                depth=0,
            ),
            LineageClosure(
                snapshot_id=201,
                ancestor_term_id=301,
                descendant_term_id=301,
                depth=0,
            ),
            LineageClosure(
                snapshot_id=200,
                ancestor_term_id=302,
                descendant_term_id=302,
                depth=0,
            ),
            LineageClosure(
                snapshot_id=200,
                ancestor_term_id=300,
                descendant_term_id=302,
                depth=1,
            ),
        )
    )
    session.add(
        GenomeAssembly(
            id=500,
            assembly_key=f"assembly:ncbi:{candidate.assembly_accession_version}",
            namespace="ncbi",
            accession_version=candidate.assembly_accession_version,
            source_organism_name="Fixture host",
            source_artifact_id=21,
        )
    )
    session.flush()
    session.add(
        AssemblySequence(
            id=510,
            assembly_id=500,
            sequence_key=f"sequence:insdc:{candidate.contig_accession_version}",
            namespace="insdc",
            accession_version=candidate.contig_accession_version,
            sequence_length=candidate.contig_length,
            source_artifact_id=21,
        )
    )
    session.add(
        SourceRecord(
            id=600,
            source_record_key=candidate.source_record_key,
            snapshot_id=20,
            artifact_id=21,
            worksheet="Data S1",
            row_number=19_239,
            native_vr_token=candidate.identity.native_vr_token,
            assembly_accession_version=candidate.assembly_accession_version,
            sequence_accession_version=candidate.contig_accession_version,
            source_locator={"worksheet": "Data S1", "row": 19_239},
            raw_payload={"fixture": True},
            raw_payload_sha256=SHA_E,
        )
    )


def _upgrade_to_head(connection: object) -> None:
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    revisions = list(reversed(list(script.walk_revisions(base="base", head="heads"))))
    for revision in revisions:
        with connection.begin():  # type: ignore[union-attr]
            context = MigrationContext.configure(
                connection,  # type: ignore[arg-type]
                opts={"target_metadata": Base.metadata},
            )
            with Operations.context(context):
                revision.module.upgrade()
