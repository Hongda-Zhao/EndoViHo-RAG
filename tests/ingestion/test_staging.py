from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, delete, event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from eve_relation_rag.config import get_settings
from eve_relation_rag.db import Base
from eve_relation_rag.db.models import (
    AssemblySequence,
    AssemblyTaxonAssignment,
    AssertionEvidence,
    DatasetRelease,
    DetectionCall,
    EVELocus,
    EVELocusPlacement,
    EvidenceItem,
    FlankAssessment,
    GenomeAssembly,
    ImportLedger,
    InclusionDecision,
    LineageSnapshot,
    LineageTerm,
    MethodDefinition,
    ProcessRun,
    QuarantineIssue,
    ReleaseAssemblyMembership,
    ReleaseAssertionMembership,
    ReleaseLineageSnapshot,
    ReleaseLocusMembership,
    ReleaseSourceSnapshot,
    ScientificAssertion,
    SourceArtifact,
    SourceAssessment,
    SourceRecord,
    SourceSnapshot,
)
from eve_relation_rag.domain.keys import locus_key, stable_key
from eve_relation_rag.importers.data_s1 import (
    DATA_S1_ARTIFACT_SHA256,
    DATA_S1_ASSEMBLY_ALLOWLIST,
    DATA_S1_IDENTITY_POLICY_KEY,
    DATA_S1_METHOD_RUN_IDENTITY,
    DATA_S1_SOURCE_ASSESSMENT_SCHEME,
    DATA_S1_SOURCE_COLUMNS,
    DATA_S1_SOURCE_SNAPSHOT_KEY,
    DataS1ValidationIssue,
    ImportedDataS1Record,
    NcbiResolutionIndex,
    QuarantinedDataS1Record,
    SourceRowLocator,
    data_s1_record_key,
    data_s1_source_record_key,
)
from eve_relation_rag.ingestion.staging import (
    AssemblySpec,
    DataS1StagingRequest,
    DatasetReleaseSpec,
    ImportExecutionSpec,
    SourceArtifactSpec,
    SourceSnapshotSpec,
    StagingConflictError,
    StagingExpectation,
    StagingInputError,
    deterministic_key_set_sha256,
    persist_data_s1_staging,
)

ASSEMBLY = "GCA_015947965.1"
CONTIG = "ABCD010000001.1"
NOW = datetime(2026, 8, 26, 4, 0, tzinfo=UTC)
ASSEMBLY_TAXA = {
    "GCA_015947965.1": ("Margaritifera margaritifera", 2_505_931),
    "GCA_016617855.1": ("Megalonaias nervosa", 52_375),
    "GCA_016746295.1": ("Potamilus streckersoni", 2_493_646),
    "GCA_028554795.2": ("Sinohyriopsis cumingii", 165_450),
    "GCA_029931535.1": ("Margaritifera margaritifera", 2_505_931),
    "GCA_943736005.1": ("Tridacna crocea", 80_833),
    "GCA_944589985.1": ("Limnoperna fortunei", 356_393),
    "GCA_945859735.2": ("Tridacna gigas", 80_829),
    "GCA_946811455.1": ("Hippopus hippopus", 80_818),
    "GCA_963210365.1": ("Tridacna derasa", 80_831),
}


@pytest.fixture(scope="module")
def postgres_engine() -> Iterator[Engine]:
    database_url = os.environ.get("EVE_RAG_TEST_DATABASE_URL", get_settings().database_url)
    admin_engine = create_engine(database_url, poolclass=NullPool)
    schema = f"test_m1_staging_{uuid4().hex}"
    try:
        with admin_engine.begin() as connection:
            connection.execute(CreateSchema(schema))
    except OperationalError as exc:
        admin_engine.dispose()
        pytest.skip(f"PostgreSQL integration database is unavailable: {exc.orig}")

    engine = admin_engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin_engine.dispose()


@pytest.fixture(scope="module")
def verified_resolution_index(
    tmp_path_factory: pytest.TempPathFactory,
) -> NcbiResolutionIndex:
    report_dir = tmp_path_factory.mktemp("m1-ncbi-reports")
    assembly_report = report_dir / "assembly_data_report.jsonl"
    sequence_report = report_dir / "sequence_report.jsonl"
    assembly_report.write_text(
        "".join(
            json.dumps(
                {
                    "accession": accession,
                    "organism": {
                        "organism_name": ASSEMBLY_TAXA[accession][0],
                        "tax_id": ASSEMBLY_TAXA[accession][1],
                    },
                }
            )
            + "\n"
            for accession in sorted(DATA_S1_ASSEMBLY_ALLOWLIST)
        ),
        encoding="utf-8",
    )
    sequence_report.write_text(
        json.dumps(
            {
                "assembly_accession": ASSEMBLY,
                "genbank_accession": CONTIG,
                "length": 1_000,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return NcbiResolutionIndex.from_jsonl_reports(
        assembly_report,
        sequence_report,
    )


def test_atomic_idempotent_staging_preserves_source_occurrences(
    postgres_engine: Engine,
    verified_resolution_index: NcbiResolutionIndex,
) -> None:
    rows = (
        imported_row(excel_row=2, native_vr_token="vr1"),
        imported_row(excel_row=3, native_vr_token="vr2"),
        viral_contig_row(excel_row=4, native_vr_token="vr3"),
    )
    request = staging_request(1, rows, verified_resolution_index)

    with Session(postgres_engine) as session:
        first = persist_data_s1_staging(session, request, rows, batch_size=1)

    assert first.replayed is False
    assert first.input_rows == 3
    assert first.normalized_candidates == 2
    assert first.quarantined_rows == 1
    assert first.accounted_policy_quarantines == 1
    assert first.open_quarantine_issues == 0

    with Session(postgres_engine) as session:
        release_id = session.scalar(
            select(DatasetRelease.id).where(
                DatasetRelease.release_key == request.release.release_key
            )
        )
        assert release_id is not None
        assert _count(session, SourceSnapshot) == 2
        assert _count(session, SourceArtifact) == 3
        assert _count(session, ReleaseSourceSnapshot, release_id=release_id) == 2
        assert _count(session, GenomeAssembly) == 10
        assert _count(session, ReleaseAssemblyMembership, release_id=release_id) == 10
        assert _count(session, AssemblyTaxonAssignment, release_id=release_id) == 10
        assert _count(session, ReleaseLineageSnapshot, release_id=release_id) == 2
        host_snapshot_id = session.scalar(
            select(LineageSnapshot.id).where(LineageSnapshot.domain == "host")
        )
        assert host_snapshot_id is not None
        assert (
            session.scalar(
                select(func.count())
                .select_from(LineageTerm)
                .where(LineageTerm.snapshot_id == host_snapshot_id)
            )
            == 9
        )
        taxon_assignment = session.execute(
            select(LineageTerm.authority_local_id, AssemblyTaxonAssignment.source_locator)
            .join(
                AssemblyTaxonAssignment,
                AssemblyTaxonAssignment.term_id == LineageTerm.id,
            )
            .join(
                GenomeAssembly,
                GenomeAssembly.id == AssemblyTaxonAssignment.assembly_id,
            )
            .where(
                (AssemblyTaxonAssignment.release_id == release_id)
                & (GenomeAssembly.accession_version == ASSEMBLY)
            )
        ).one()
        assert taxon_assignment.authority_local_id == "2505931"
        assert "taxdump" in taxon_assignment.source_locator["public_release_blocker"]
        assert _count(session, AssemblySequence) == 1
        assert _count(session, SourceRecord) == 3
        assert _count(session, DetectionCall, release_id=release_id) == 3
        source_keys = set(session.scalars(select(SourceRecord.source_record_key)).all())
        call_keys = set(
            session.scalars(
                select(DetectionCall.call_key).where(DetectionCall.release_id == release_id)
            ).all()
        )
        assert all(key.startswith("source-record:zhao2026-v4:sha256:") for key in source_keys)
        assert source_keys.isdisjoint(call_keys)
        method = session.scalar(select(MethodDefinition))
        assert method is not None
        assert method.definition_artifact_id is None
        assert _count(session, EVELocus, release_id=release_id) == 3
        assert _count(session, EVELocusPlacement, release_id=release_id) == 2
        assert _count(session, SourceAssessment, release_id=release_id) == 3
        assert _count(session, EvidenceItem, release_id=release_id) == 3
        assert _count(session, ScientificAssertion, release_id=release_id) == 9
        assertion_keys = session.scalars(
            select(ScientificAssertion.assertion_key).where(
                ScientificAssertion.release_id == release_id
            )
        ).all()
        assert all(key.startswith("assertion:eve:v1:sha256:") for key in assertion_keys)
        process_run_key = session.scalar(select(ProcessRun.process_run_key))
        hcvr_assertion_key = session.scalar(
            select(ScientificAssertion.assertion_key)
            .join(DetectionCall, DetectionCall.id == ScientificAssertion.call_id)
            .where(
                (ScientificAssertion.release_id == release_id)
                & (ScientificAssertion.assertion_type == "hcvr")
                & (DetectionCall.call_key == rows[0].record_key)
            )
        )
        assert process_run_key is not None
        assert hcvr_assertion_key == stable_key(
            "assertion:eve:v1",
            {
                "assertion_type": "hcvr",
                "method_run_key": process_run_key,
                "object": {
                    "asserted_value": "Yes",
                    "source_confidence": "source_high",
                },
                "predicate_key": "source:hcvr",
                "scheme_snapshot": {
                    "kind": "source_assessment_scheme",
                    "key": DATA_S1_SOURCE_ASSESSMENT_SCHEME,
                },
                "source_locator": {
                    "excel_row": 2,
                    "label": "S3!2",
                    "worksheet": "S3",
                },
                "subject": {
                    "call_key": rows[0].record_key,
                    "locus_key": rows[0].locus_key,
                },
            },
        )
        assert _count(session, AssertionEvidence, release_id=release_id) == 9
        assert _count(session, ImportLedger, release_id=release_id) == 3
        assert _count(session, QuarantineIssue) == 1
        issue = session.scalar(select(QuarantineIssue))
        assert issue is not None
        assert issue.issue_code == "viral_contig_policy_quarantine"
        assert issue.status == "resolved"
        assert issue.severity == "warning"
        assert _count(session, FlankAssessment, release_id=release_id) == 0
        assert _count(session, InclusionDecision, release_id=release_id) == 0
        assert _count(session, ReleaseLocusMembership, release_id=release_id) == 0
        assert _count(session, ReleaseAssertionMembership, release_id=release_id) == 0

        assembly_key = session.scalar(
            select(GenomeAssembly.assembly_key).where(GenomeAssembly.accession_version == ASSEMBLY)
        )
        sequence_key = session.scalar(select(AssemblySequence.sequence_key))
        assert assembly_key == f"assembly:ncbi:{ASSEMBLY}"
        assert sequence_key == f"sequence:insdc:{CONTIG}"

        placements = session.execute(
            select(EVELocusPlacement.start0, EVELocusPlacement.end0).where(
                EVELocusPlacement.release_id == release_id
            )
        ).all()
        assert placements == [(100, 200), (100, 200)]

    replay_edge_queries: list[tuple[str, int]] = []

    def capture_replay_edge_query(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if "assertion_evidence" in statement:
            assert hasattr(parameters, "__len__")
            replay_edge_queries.append((statement, len(parameters)))  # type: ignore[arg-type]

    event.listen(postgres_engine, "before_cursor_execute", capture_replay_edge_query)
    try:
        with Session(postgres_engine) as session:
            replay = persist_data_s1_staging(session, request, rows, batch_size=2)
    finally:
        event.remove(postgres_engine, "before_cursor_execute", capture_replay_edge_query)
    assert replay.replayed is True
    assert replay.created_counts == {}
    assert len(replay_edge_queries) == 1
    edge_query, parameter_count = replay_edge_queries[0]
    assert "scientific_assertion ON" in edge_query
    assert "evidence_item ON" in edge_query
    assert " IN (" not in edge_query
    assert parameter_count <= 4

    with Session(postgres_engine) as session:
        edge = session.scalar(
            select(AssertionEvidence).order_by(AssertionEvidence.assertion_id).limit(1)
        )
        assert edge is not None
        alternate_evidence_id = session.scalar(
            select(EvidenceItem.id)
            .where(
                (EvidenceItem.release_id == edge.release_id) & (EvidenceItem.id != edge.evidence_id)
            )
            .limit(1)
        )
        assert alternate_evidence_id is not None
        session.execute(
            delete(AssertionEvidence).where(
                (AssertionEvidence.release_id == edge.release_id)
                & (AssertionEvidence.assertion_id == edge.assertion_id)
                & (AssertionEvidence.evidence_id == edge.evidence_id)
                & (AssertionEvidence.relation == edge.relation)
            )
        )
        session.add(
            AssertionEvidence(
                release_id=edge.release_id,
                assertion_id=edge.assertion_id,
                evidence_id=alternate_evidence_id,
                relation=edge.relation,
            )
        )
        session.commit()

    with Session(postgres_engine) as session:
        with pytest.raises(StagingConflictError, match="assertion evidence") as error:
            persist_data_s1_staging(session, request, rows)
    assert error.value.code == "replay_ledger_mismatch"


def test_row_level_ncbi_unresolved_is_retained_without_locus(
    postgres_engine: Engine,
    verified_resolution_index: NcbiResolutionIndex,
) -> None:
    row = unresolved_row(excel_row=20)
    request = staging_request(2, (row,), verified_resolution_index)

    with Session(postgres_engine) as session:
        result = persist_data_s1_staging(session, request, (row,))

    assert result.normalized_candidates == 0
    assert result.quarantined_rows == 1
    assert result.open_quarantine_issues == 1
    with Session(postgres_engine) as session:
        release_id = session.scalar(
            select(DatasetRelease.id).where(
                DatasetRelease.release_key == request.release.release_key
            )
        )
        assert release_id is not None
        assert _count(session, SourceRecord) >= 4
        assert _count(session, DetectionCall, release_id=release_id) == 1
        assert _count(session, SourceAssessment, release_id=release_id) == 1
        assert _count(session, ScientificAssertion, release_id=release_id) == 3
        assert _count(session, ImportLedger, release_id=release_id) == 1
        assert _count(session, EVELocus, release_id=release_id) == 0
        assert _count(session, EVELocusPlacement, release_id=release_id) == 0


@pytest.mark.parametrize(
    ("mutation", "error_code"),
    (
        ("artifact", "source_artifact_checksum_mismatch"),
        ("resolution", "ncbi_resolution_mismatch"),
        ("record_key", "record_key_mismatch"),
        ("report_observation_missing", "ncbi_report_observation_missing"),
        ("report_artifact_mismatch", "ncbi_report_artifact_mismatch"),
        ("unbound_index", "ncbi_index_not_byte_bound"),
    ),
)
def test_preflight_conflicts_write_nothing(
    postgres_engine: Engine,
    verified_resolution_index: NcbiResolutionIndex,
    mutation: str,
    error_code: str,
) -> None:
    ordinal = {
        "artifact": 3,
        "resolution": 4,
        "record_key": 5,
        "report_observation_missing": 9,
        "report_artifact_mismatch": 10,
        "unbound_index": 11,
    }[mutation]
    row = imported_row(excel_row=30 + ordinal, native_vr_token=f"vr{ordinal + 10}")
    request = staging_request(ordinal, (row,), verified_resolution_index)
    if mutation == "artifact":
        request = replace(
            request,
            data_artifact=replace(request.data_artifact, declared_sha256="0" * 64),
        )
    elif mutation == "resolution":
        row = replace(row, contig_resolution="not_checked")
    elif mutation == "report_observation_missing":
        request = replace(
            request,
            resolution_index=replace(
                request.resolution_index,
                assembly_report_sha256=None,
            ),
        )
    elif mutation == "report_artifact_mismatch":
        request = replace(
            request,
            resolution_index=replace(
                request.resolution_index,
                sequence_report_byte_size=(request.sequence_report_artifact.byte_size + 1),
            ),
        )
    elif mutation == "unbound_index":
        request = replace(
            request,
            resolution_index=replace(request.resolution_index),
        )
    else:
        row = replace(row, record_key="call:zhao2026-v4:sha256:" + "0" * 64)

    with Session(postgres_engine) as session:
        with pytest.raises(StagingInputError) as error:
            persist_data_s1_staging(session, request, (row,))
    assert error.value.code == error_code
    with Session(postgres_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(DatasetRelease)
                .where(DatasetRelease.release_key == request.release.release_key)
            )
            == 0
        )


@pytest.mark.parametrize("slice_end", (0, 2))
def test_empty_or_partial_run_cannot_be_marked_succeeded(
    postgres_engine: Engine,
    verified_resolution_index: NcbiResolutionIndex,
    slice_end: int,
) -> None:
    rows = (
        imported_row(excel_row=50, native_vr_token="vr50"),
        imported_row(excel_row=51, native_vr_token="vr51"),
        viral_contig_row(excel_row=52, native_vr_token="vr52"),
    )
    ordinal = 6 + slice_end
    request = staging_request(ordinal, rows, verified_resolution_index)

    with Session(postgres_engine) as session:
        with pytest.raises(StagingInputError) as error:
            persist_data_s1_staging(session, request, rows[:slice_end])
    assert error.value.code == "staging_expectation_mismatch"
    with Session(postgres_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(DatasetRelease)
                .where(DatasetRelease.release_key == request.release.release_key)
            )
            == 0
        )


def staging_request(
    ordinal: int,
    rows: tuple[ImportedDataS1Record | QuarantinedDataS1Record, ...],
    resolution_index: NcbiResolutionIndex,
) -> DataS1StagingRequest:
    data_artifact = artifact_spec(
        "source-artifact:zhao-data-s1",
        "media-6-file12.xlsx",
        DATA_S1_ARTIFACT_SHA256,
        remote=True,
    )
    assembly_artifact = artifact_spec(
        "source-artifact:ncbi-assembly-report",
        "assembly_data_report.jsonl",
        _required_value(resolution_index.assembly_report_sha256),
        byte_size=_required_value(resolution_index.assembly_report_byte_size),
    )
    sequence_artifact = artifact_spec(
        "source-artifact:ncbi-sequence-report",
        "sequence_report.jsonl",
        _required_value(resolution_index.sequence_report_sha256),
        byte_size=_required_value(resolution_index.sequence_report_byte_size),
    )
    return DataS1StagingRequest(
        release=DatasetReleaseSpec(
            dataset_key="dataset:eve-relation",
            dataset_title="EVE Relation",
            release_key=f"release:eve-relation:v1:20260826:{ordinal:03d}",
            schema_version="m1-v1",
            manifest_sha256="f" * 64,
        ),
        source_snapshot=SourceSnapshotSpec(
            snapshot_key=DATA_S1_SOURCE_SNAPSHOT_KEY,
            source_name="Zhao et al. Data S1",
            source_version="bioRxiv v4 DC6 media-6 file12",
            source_uri="https://www.biorxiv.org/content/early/2025/04/24/2025.04.19.649669/DC6",
            retrieved_at=NOW,
            declared_manifest_sha256="c" * 64,
            verified_manifest_sha256="c" * 64,
            declared_license_key="CC-BY-NC-ND-4.0",
            verified_license_key="CC-BY-NC-ND-4.0",
        ),
        data_artifact=data_artifact,
        resolution_snapshot=SourceSnapshotSpec(
            snapshot_key="ncbi-datasets-v2:20260826:pilot-resolution-package",
            source_name="NCBI Datasets v2",
            source_version="2026-08-26",
            source_uri="https://api.ncbi.nlm.nih.gov/datasets/v2/genome/accession",
            retrieved_at=NOW,
            declared_manifest_sha256="d" * 64,
            verified_manifest_sha256="d" * 64,
            declared_license_key="NCBI-PUBLIC-DOMAIN",
            verified_license_key="NCBI-PUBLIC-DOMAIN",
        ),
        assembly_report_artifact=assembly_artifact,
        sequence_report_artifact=sequence_artifact,
        assemblies=tuple(
            AssemblySpec(accession, *ASSEMBLY_TAXA[accession])
            for accession in sorted(DATA_S1_ASSEMBLY_ALLOWLIST)
        ),
        resolution_index=resolution_index,
        execution=ImportExecutionSpec(
            run_key=stable_key("import-run:data-s1", {"ordinal": ordinal}),
            importer_name="eve_relation_rag.importers.data_s1",
            importer_version=DATA_S1_METHOD_RUN_IDENTITY,
            code_sha256="e" * 64,
            software_agent_key="eve-relation-rag:m1",
            started_at=NOW,
            finished_at=NOW,
        ),
        expectation=staging_expectation(rows),
    )


def staging_expectation(
    rows: tuple[ImportedDataS1Record | QuarantinedDataS1Record, ...],
) -> StagingExpectation:
    locus_keys = [
        row.locus_key
        for row in rows
        if row.assembly_resolution == "exact"
        and row.contig_resolution == "exact"
        and row.locus_key is not None
    ]
    return StagingExpectation(
        source_records=len(rows),
        source_high=sum(row.source_assessment == "source_high" for row in rows),
        source_low=sum(row.source_assessment == "source_low" for row in rows),
        normalized_candidates=sum(isinstance(row, ImportedDataS1Record) for row in rows),
        quarantined_rows=sum(isinstance(row, QuarantinedDataS1Record) for row in rows),
        loci=len(locus_keys),
        placements=sum(isinstance(row, ImportedDataS1Record) for row in rows),
        quarantine_issues=sum(
            len(row.issues) if isinstance(row, QuarantinedDataS1Record) else 0 for row in rows
        ),
        call_key_set_sha256=deterministic_key_set_sha256(row.record_key for row in rows),
        locus_key_set_sha256=deterministic_key_set_sha256(locus_keys),
    )


def artifact_spec(
    artifact_key: str,
    filename: str,
    sha256: str,
    *,
    remote: bool = False,
    byte_size: int | None = None,
) -> SourceArtifactSpec:
    return SourceArtifactSpec(
        artifact_key=artifact_key,
        filename=filename,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if filename.endswith(".xlsx")
            else "application/x-ndjson"
        ),
        byte_size=(
            byte_size
            if byte_size is not None
            else (83_851_778 if filename.endswith(".xlsx") else 1_000)
        ),
        declared_sha256=sha256,
        verified_sha256=sha256,
        source_uri=f"https://example.invalid/{filename}",
        retrieved_at=NOW,
        declared_license_key="NCBI-PUBLIC-DOMAIN" if "ncbi" in artifact_key else "CC-BY-NC-ND-4.0",
        verified_license_key="NCBI-PUBLIC-DOMAIN" if "ncbi" in artifact_key else "CC-BY-NC-ND-4.0",
        remote_checksum_verified=remote,
        remote_verification_at=NOW if remote else None,
        remote_verification_uri=(
            "https://www.biorxiv.org/content/early/2025/04/24/2025.04.19.649669/DC6"
            if remote
            else None
        ),
    )


def _required_value[T](value: T | None) -> T:
    assert value is not None
    return value


def imported_row(*, excel_row: int, native_vr_token: str) -> ImportedDataS1Record:
    raw = source_row(native_vr_token=native_vr_token)
    locator = SourceRowLocator("S3", excel_row)
    record_key = call_key(locator, native_vr_token=native_vr_token)
    return ImportedDataS1Record(
        record_key=record_key,
        source_record_key=data_s1_source_record_key(
            DATA_S1_ARTIFACT_SHA256,
            DATA_S1_SOURCE_SNAPSHOT_KEY,
            locator,
        ),
        method_run_identity=DATA_S1_METHOD_RUN_IDENTITY,
        locus_key=locus_key(
            source_snapshot_key=DATA_S1_SOURCE_SNAPSHOT_KEY,
            assembly_accession_version=ASSEMBLY,
            contig_accession_version=CONTIG,
            native_vr_token=native_vr_token,
            identity_policy_version=DATA_S1_IDENTITY_POLICY_KEY,
        ),
        artifact_sha256=DATA_S1_ARTIFACT_SHA256,
        source_snapshot_key=DATA_S1_SOURCE_SNAPSHOT_KEY,
        identity_policy_key=DATA_S1_IDENTITY_POLICY_KEY,
        source_assessment_scheme=DATA_S1_SOURCE_ASSESSMENT_SCHEME,
        source_assessment="source_high",
        assembly_accession_version=ASSEMBLY,
        sequence_accession_version=CONTIG,
        native_vr_token=native_vr_token,
        assembly_resolution="exact",
        contig_resolution="exact",
        authority_contig_length=1_000,
        contig_length=1_000,
        start0=100,
        end0=200,
        length=100,
        coordinate_system="0-based-half-open",
        viral_major_taxon="Orthopolintovirales",
        host_class="Bivalvia",
        vr_type="Integration",
        source_hcvr="Yes",
        locator=locator,
        raw_row=raw,
    )


def viral_contig_row(*, excel_row: int, native_vr_token: str) -> QuarantinedDataS1Record:
    raw = source_row(
        native_vr_token=native_vr_token,
        hcvr="No",
        vr_type="Viral contig",
        start="300",
        end="400",
    )
    locator = SourceRowLocator("S3", excel_row)
    return QuarantinedDataS1Record(
        record_key=call_key(locator, native_vr_token=native_vr_token),
        source_record_key=data_s1_source_record_key(
            DATA_S1_ARTIFACT_SHA256,
            DATA_S1_SOURCE_SNAPSHOT_KEY,
            locator,
        ),
        method_run_identity=DATA_S1_METHOD_RUN_IDENTITY,
        locus_key=locus_key(
            source_snapshot_key=DATA_S1_SOURCE_SNAPSHOT_KEY,
            assembly_accession_version=ASSEMBLY,
            contig_accession_version=CONTIG,
            native_vr_token=native_vr_token,
            identity_policy_version=DATA_S1_IDENTITY_POLICY_KEY,
        ),
        artifact_sha256=DATA_S1_ARTIFACT_SHA256,
        source_snapshot_key=DATA_S1_SOURCE_SNAPSHOT_KEY,
        identity_policy_key=DATA_S1_IDENTITY_POLICY_KEY,
        source_assessment_scheme=DATA_S1_SOURCE_ASSESSMENT_SCHEME,
        source_assessment="source_low",
        assembly_accession_version=ASSEMBLY,
        sequence_accession_version=CONTIG,
        native_vr_token=native_vr_token,
        assembly_resolution="exact",
        contig_resolution="exact",
        authority_contig_length=1_000,
        locator=locator,
        raw_row=raw,
        issues=(
            DataS1ValidationIssue(
                code="viral_contig_policy_quarantine",
                field="VR Type",
                message=(
                    "viral-contig-like source records are auditable but not normalized candidates"
                ),
                raw_value="Viral contig",
            ),
        ),
    )


def unresolved_row(*, excel_row: int) -> QuarantinedDataS1Record:
    contig = "MISSING000001.1"
    raw = source_row(contig=contig, native_vr_token="vr20")
    locator = SourceRowLocator("S3", excel_row)
    return QuarantinedDataS1Record(
        record_key=call_key(locator, native_vr_token="vr20", contig=contig),
        source_record_key=data_s1_source_record_key(
            DATA_S1_ARTIFACT_SHA256,
            DATA_S1_SOURCE_SNAPSHOT_KEY,
            locator,
        ),
        method_run_identity=DATA_S1_METHOD_RUN_IDENTITY,
        locus_key=locus_key(
            source_snapshot_key=DATA_S1_SOURCE_SNAPSHOT_KEY,
            assembly_accession_version=ASSEMBLY,
            contig_accession_version=contig,
            native_vr_token="vr20",
            identity_policy_version=DATA_S1_IDENTITY_POLICY_KEY,
        ),
        artifact_sha256=DATA_S1_ARTIFACT_SHA256,
        source_snapshot_key=DATA_S1_SOURCE_SNAPSHOT_KEY,
        identity_policy_key=DATA_S1_IDENTITY_POLICY_KEY,
        source_assessment_scheme=DATA_S1_SOURCE_ASSESSMENT_SCHEME,
        source_assessment="source_high",
        assembly_accession_version=ASSEMBLY,
        sequence_accession_version=contig,
        native_vr_token="vr20",
        assembly_resolution="exact",
        contig_resolution="unresolved",
        authority_contig_length=None,
        locator=locator,
        raw_row=raw,
        issues=(
            DataS1ValidationIssue(
                code="unresolved_sequence_accession",
                field="Contig",
                message="sequence accession is absent from the frozen NCBI report",
                raw_value=contig,
            ),
        ),
    )


def source_row(
    *,
    native_vr_token: str,
    contig: str = CONTIG,
    hcvr: str = "Yes",
    vr_type: str = "Integration",
    start: str = "100",
    end: str = "200",
) -> dict[str, str]:
    values = {
        "Assembly": ASSEMBLY,
        "Contig": contig,
        "VR": native_vr_token,
        "HCVR": hcvr,
        "Contig Length": "1000",
        "Start": start,
        "End": end,
        "Length": "100",
        "Annoated Viral Proportion": "75",
        "Viral Major Taxon": "Orthopolintovirales",
        "Eukaryote Classification": "Metazoa",
        "Phylum": "Mollusca",
        "Class": "Bivalvia",
        "Order": "Testida",
        "Family": "Testidae",
        "Genus": "Testus",
        "Organism Name": "Testus bivalvis",
        "VR Type": vr_type,
        "Unique Rate": "1",
        "Conserved OG": "Passed",
        "Busco score": "99",
    }
    assert set(values) == {header for _, header in DATA_S1_SOURCE_COLUMNS}
    return values


def call_key(
    locator: SourceRowLocator,
    *,
    native_vr_token: str,
    contig: str = CONTIG,
) -> str:
    return data_s1_record_key(
        DATA_S1_ARTIFACT_SHA256,
        DATA_S1_SOURCE_SNAPSHOT_KEY,
        locator,
        assembly_accession_version=ASSEMBLY,
        sequence_accession_version=contig,
        native_vr_token=native_vr_token,
        method_run_identity=DATA_S1_METHOD_RUN_IDENTITY,
    )


def _count(session: Session, model: type[Base], *, release_id: int | None = None) -> int:
    statement = select(func.count()).select_from(model)
    if release_id is not None:
        statement = statement.where(model.release_id == release_id)
    return int(session.scalar(statement) or 0)
