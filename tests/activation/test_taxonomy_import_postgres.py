from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from eve_relation_rag.activation.contracts import ACTIVATION_RELEASE_KEY, APPROVED_ASSEMBLIES
from eve_relation_rag.activation.taxonomy import (
    build_assembly_taxon_assignment_manifest,
    build_ncbi_taxonomy_artifact_manifest,
    import_taxonomy_snapshot,
    load_ncbi_taxonomy_snapshot,
)
from eve_relation_rag.config import get_settings
from eve_relation_rag.db.base import Base
from eve_relation_rag.db.models import (
    AssemblyTaxonAssignment,
    Dataset,
    DatasetRelease,
    GenomeAssembly,
    LineageTerm,
    ReleaseAssemblyMembership,
    ReleaseLineageSnapshot,
    SourceArtifact,
    SourceSnapshot,
)

RETRIEVED_AT = "2026-08-29T00:00:00Z"


@pytest.fixture(scope="module")
def postgres_engine() -> Iterator[Engine]:
    database_url = os.environ.get("EVE_RAG_TEST_DATABASE_URL", get_settings().database_url)
    admin_engine = create_engine(database_url, poolclass=NullPool)
    schema = f"test_activation_taxonomy_{uuid4().hex}"
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


def test_taxonomy_import_is_candidate_only_exact_and_idempotent(
    postgres_engine: Engine,
    tmp_path: Path,
) -> None:
    archive = _taxdump(tmp_path)
    policy = tmp_path / "ncbi-policy.html"
    policy.write_text("frozen policy capture", encoding="utf-8")
    raw = archive.read_bytes()
    manifest = build_ncbi_taxonomy_artifact_manifest(
        archive,
        expected_sha256=hashlib.sha256(raw).hexdigest(),
        expected_byte_size=len(raw),
        upstream_md5=hashlib.md5(raw, usedforsecurity=False).hexdigest(),
        version="taxdump-test-import",
        source_uri="https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz",
        checksum_source_uri="https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz.md5",
        retrieved_at=RETRIEVED_AT,
        usage_policy_source_uri="https://www.ncbi.nlm.nih.gov/home/about/policies/",
        usage_policy_retrieved_at=RETRIEVED_AT,
        usage_policy_capture_path=policy,
        expected_usage_policy_sha256=hashlib.sha256(policy.read_bytes()).hexdigest(),
    )
    loaded = load_ncbi_taxonomy_snapshot(manifest, archive, required_tax_ids=(11,))
    snapshot = loaded.manifest
    assembly_report = tmp_path / "assembly_data_report.jsonl"
    assembly_report.write_text(
        "".join(
            json.dumps({"accession": accession, "organism": {"tax_id": 11}}) + "\n"
            for accession in APPROVED_ASSEMBLIES
        ),
        encoding="utf-8",
    )
    assembly_report_raw = assembly_report.read_bytes()
    assembly_artifact_key = "source-artifact:ncbi-assembly-report:test-import"
    assignments = build_assembly_taxon_assignment_manifest(
        loaded,
        assembly_report_path=assembly_report,
        expected_assembly_report_sha256=hashlib.sha256(assembly_report_raw).hexdigest(),
        expected_assembly_report_byte_size=len(assembly_report_raw),
        assembly_report_artifact_key=assembly_artifact_key,
    )

    with Session(postgres_engine) as session:
        dataset = Dataset(dataset_key="dataset:endoviho-rag", title="Activation import test")
        session.add(dataset)
        session.flush()
        release = DatasetRelease(
            dataset_id=dataset.id,
            release_key=ACTIVATION_RELEASE_KEY,
            schema_version="endoviho-structured-v0",
            status="candidate",
        )
        session.add(release)
        resolution_snapshot = SourceSnapshot(
            snapshot_key="source-snapshot:ncbi-assembly-report:test-import",
            source_name="NCBI Datasets",
            source_version="test",
            source_uri="https://api.ncbi.nlm.nih.gov/datasets/v2alpha/genome/dataset_report",
            retrieved_at=datetime(2026, 8, 29, tzinfo=UTC),
            declared_manifest_sha256="c" * 64,
            verified_manifest_sha256="c" * 64,
            declared_license_key="NCBI-MOLECULAR-DATA-USAGE-POLICY",
            verified_license_key="NCBI-MOLECULAR-DATA-USAGE-POLICY",
        )
        session.add(resolution_snapshot)
        session.flush()
        resolution_artifact = SourceArtifact(
            snapshot_id=resolution_snapshot.id,
            artifact_key=assembly_artifact_key,
            filename=assembly_report.name,
            media_type="application/x-ndjson",
            byte_size=len(assembly_report_raw),
            declared_sha256=hashlib.sha256(assembly_report_raw).hexdigest(),
            verified_sha256=hashlib.sha256(assembly_report_raw).hexdigest(),
            source_uri="https://api.ncbi.nlm.nih.gov/datasets/v2alpha/genome/dataset_report",
            retrieved_at=datetime(2026, 8, 29, tzinfo=UTC),
            declared_license_key="NCBI-MOLECULAR-DATA-USAGE-POLICY",
            verified_license_key="NCBI-MOLECULAR-DATA-USAGE-POLICY",
            remote_checksum_verified=False,
        )
        session.add(resolution_artifact)
        session.flush()
        for index, accession in enumerate(APPROVED_ASSEMBLIES, start=1):
            assembly = GenomeAssembly(
                assembly_key=f"assembly:test:{index}",
                namespace="ncbi",
                accession_version=accession,
                source_organism_name=f"Test organism {index}",
                source_artifact_id=resolution_artifact.id,
            )
            session.add(assembly)
            session.flush()
            session.add(
                ReleaseAssemblyMembership(
                    release_id=release.id,
                    assembly_id=assembly.id,
                    membership_role="pilot_scope",
                )
            )
        session.commit()

    with Session(postgres_engine) as session:
        report = import_taxonomy_snapshot(
            session,
            artifact_manifest=manifest,
            snapshot_manifest=snapshot,
            assignment_manifest=assignments,
        )
        session.commit()
    assert report.created is True
    assert report.term_count == 3
    assert report.assignment_count == 10

    with Session(postgres_engine) as session:
        replay = import_taxonomy_snapshot(
            session,
            artifact_manifest=manifest,
            snapshot_manifest=snapshot,
            assignment_manifest=assignments,
        )
        session.commit()
        loaded_release = session.scalar(
            select(DatasetRelease).where(DatasetRelease.release_key == ACTIVATION_RELEASE_KEY)
        )
        term_count = session.scalar(select(func.count()).select_from(LineageTerm))
        binding_count = session.scalar(select(func.count()).select_from(ReleaseLineageSnapshot))
        assignment_count = session.scalar(select(func.count()).select_from(AssemblyTaxonAssignment))
    assert replay.created is False
    assert loaded_release is not None and loaded_release.status == "candidate"
    assert term_count == 3
    assert binding_count == 1
    assert assignment_count == 10


def _taxdump(tmp_path: Path) -> Path:
    path = tmp_path / "taxdump.tar.gz"
    members = {
        "nodes.dmp": (b"1\t|\t1\t|\tno rank\t|\n10\t|\t1\t|\tgenus\t|\n11\t|\t10\t|\tspecies\t|\n"),
        "names.dmp": (
            b"1\t|\troot\t|\t\t|\tscientific name\t|\n"
            b"10\t|\tExample genus\t|\t\t|\tscientific name\t|\n"
            b"11\t|\tCurrent species\t|\t\t|\tscientific name\t|\n"
            b"11\t|\tOld species\t|\t\t|\tsynonym\t|\n"
        ),
        "merged.dmp": b"99\t|\t11\t|\n",
        "delnodes.dmp": b"100\t|\n",
    }
    with tarfile.open(path, "w:gz") as archive:
        for filename, raw in members.items():
            info = tarfile.TarInfo(filename)
            info.size = len(raw)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(raw))
    return path
