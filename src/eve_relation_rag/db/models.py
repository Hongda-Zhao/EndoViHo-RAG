"""Milestone 1 SQLAlchemy models for the auditable EVE truth layer.

The schema deliberately keeps source assessments, inclusion decisions, and public
release membership separate.  A source label can therefore never create public
membership by itself.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from eve_relation_rag.db.base import Base

SHA256_CHECK = "{column} ~ '^[0-9a-f]{{64}}$'"


class Dataset(Base):
    """A persistent data-product identity with multiple immutable releases."""

    __tablename__ = "dataset"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dataset_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DatasetRelease(Base):
    """A candidate or immutable published snapshot of one Dataset."""

    __tablename__ = "dataset_release"
    __table_args__ = (
        UniqueConstraint("dataset_id", "id", name="uq_dataset_release_dataset_id_id"),
        CheckConstraint(
            "status IN ('candidate', 'validated', 'published', 'deprecated', 'rejected')",
            name="valid_status",
        ),
        CheckConstraint(
            "(status IN ('published', 'deprecated') AND published_at IS NOT NULL "
            "AND manifest_sha256 IS NOT NULL) OR "
            "(status NOT IN ('published', 'deprecated') AND published_at IS NULL)",
            name="publication_fields_match_status",
        ),
        CheckConstraint(
            "manifest_sha256 IS NULL OR " + SHA256_CHECK.format(column="manifest_sha256"),
            name="valid_manifest_sha256",
        ),
        CheckConstraint(
            "supersedes_release_id IS NULL OR supersedes_release_id <> id",
            name="does_not_supersede_self",
        ),
        ForeignKeyConstraint(
            ["dataset_id", "supersedes_release_id"],
            ["dataset_release.dataset_id", "dataset_release.id"],
            name="fk_dataset_release_supersedes_same_dataset",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("dataset.id", ondelete="RESTRICT"), nullable=False
    )
    release_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'candidate'")
    )
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_release_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SourceSnapshot(Base):
    """A frozen, checksummed version of an external source."""

    __tablename__ = "source_snapshot"
    __table_args__ = (
        CheckConstraint(
            "declared_manifest_sha256 IS NULL OR "
            + SHA256_CHECK.format(column="declared_manifest_sha256"),
            name="valid_declared_manifest",
        ),
        CheckConstraint(
            SHA256_CHECK.format(column="verified_manifest_sha256"),
            name="valid_verified_manifest",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_version: Mapped[str] = mapped_column(String(255), nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    declared_manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    verified_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    declared_license_key: Mapped[str | None] = mapped_column(String(255))
    verified_license_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SourceArtifact(Base):
    """One immutable file or response contained by a SourceSnapshot."""

    __tablename__ = "source_artifact"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "id", name="uq_source_artifact_snapshot_id_id"),
        UniqueConstraint(
            "snapshot_id",
            "verified_sha256",
            "filename",
            name="uq_source_artifact_snapshot_content",
        ),
        CheckConstraint("byte_size >= 0", name="nonnegative_byte_size"),
        CheckConstraint(
            "declared_sha256 IS NULL OR " + SHA256_CHECK.format(column="declared_sha256"),
            name="valid_declared_sha256",
        ),
        CheckConstraint(
            SHA256_CHECK.format(column="verified_sha256"), name="valid_verified_sha256"
        ),
        CheckConstraint(
            "NOT remote_checksum_verified OR "
            "(remote_verification_at IS NOT NULL AND remote_verification_uri IS NOT NULL)",
            name="remote_verification_has_provenance",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_snapshot.id", ondelete="RESTRICT"), nullable=False
    )
    artifact_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    declared_sha256: Mapped[str | None] = mapped_column(String(64))
    verified_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    declared_license_key: Mapped[str | None] = mapped_column(String(255))
    verified_license_key: Mapped[str] = mapped_column(String(255), nullable=False)
    remote_checksum_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    remote_verification_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remote_verification_uri: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReleaseSourceSnapshot(Base):
    """Typed dependency pinning a source snapshot to one release."""

    __tablename__ = "release_source_snapshot"
    __table_args__ = (
        UniqueConstraint("release_id", "role", name="uq_release_source_snapshot_role"),
    )

    release_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("dataset_release.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    source_snapshot_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_snapshot.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False)


class MethodDefinition(Base):
    """A versioned method contract used to interpret a ProcessRun result."""

    __tablename__ = "method_definition"
    __table_args__ = (
        UniqueConstraint("method_key", "version", name="uq_method_definition_key_version"),
        CheckConstraint(
            "method_kind IN ('source_import', 'source_assessment', 'manual_curation')",
            name="valid_method_kind",
        ),
        CheckConstraint(SHA256_CHECK.format(column="definition_sha256"), name="valid_sha256"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    method_definition_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    method_key: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    method_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    definition_artifact_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("source_artifact.id", ondelete="RESTRICT")
    )
    definition_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parameter_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReleaseMethodDefinition(Base):
    """A typed release dependency on one exact MethodDefinition."""

    __tablename__ = "release_method_definition"
    __table_args__ = (
        UniqueConstraint(
            "release_id",
            "method_definition_id",
            "role",
            name="uq_release_method_definition_role",
        ),
    )

    release_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("dataset_release.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    method_definition_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("method_definition.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False)


class LineageSnapshot(Base):
    """A versioned formal or study-defined lineage namespace."""

    __tablename__ = "lineage_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "id", "domain", "scheme_kind", name="uq_lineage_snapshot_id_domain_scheme"
        ),
        UniqueConstraint(
            "domain",
            "scheme_kind",
            "authority_namespace",
            "version",
            name="uq_lineage_snapshot_authority_version",
        ),
        CheckConstraint("domain IN ('host', 'viral')", name="valid_domain"),
        CheckConstraint(
            "scheme_kind IN ('formal_taxonomy', 'study_defined')",
            name="valid_scheme_kind",
        ),
        CheckConstraint(
            "NOT (domain = 'host' AND scheme_kind = 'study_defined')",
            name="host_snapshot_is_formal",
        ),
        CheckConstraint(SHA256_CHECK.format(column="snapshot_sha256"), name="valid_sha256"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    scheme_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    authority_namespace: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    source_artifact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_artifact.id", ondelete="RESTRICT"), nullable=False
    )
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReleaseLineageSnapshot(Base):
    """A release-scoped, role-qualified lineage dependency."""

    __tablename__ = "release_lineage_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "release_id",
            "snapshot_id",
            "role",
            name="uq_release_lineage_snapshot_pair_role",
        ),
        UniqueConstraint("release_id", "role", name="uq_release_lineage_snapshot_role"),
        CheckConstraint(
            "role IN ('assembly_source_taxonomy', 'formal_viral_taxonomy', 'study_viral_lineage')",
            name="valid_role",
        ),
        CheckConstraint(
            "(role = 'assembly_source_taxonomy' AND domain = 'host' "
            "AND scheme_kind = 'formal_taxonomy') OR "
            "(role = 'formal_viral_taxonomy' AND domain = 'viral' "
            "AND scheme_kind = 'formal_taxonomy') OR "
            "(role = 'study_viral_lineage' AND domain = 'viral' "
            "AND scheme_kind = 'study_defined')",
            name="role_matches_namespace",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "domain", "scheme_kind"],
            ["lineage_snapshot.id", "lineage_snapshot.domain", "lineage_snapshot.scheme_kind"],
            name="fk_release_lineage_snapshot_namespace",
            ondelete="RESTRICT",
        ),
    )

    release_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("dataset_release.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    snapshot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    scheme_kind: Mapped[str] = mapped_column(String(32), nullable=False)


class LineageTerm(Base):
    """A term whose identity is scoped to one LineageSnapshot."""

    __tablename__ = "lineage_term"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "term_key", name="uq_lineage_term_snapshot_key"),
        UniqueConstraint("snapshot_id", "id", name="uq_lineage_term_snapshot_id_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("lineage_snapshot.id", ondelete="RESTRICT"), nullable=False
    )
    term_key: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    rank: Mapped[str | None] = mapped_column(String(128))
    authority_local_id: Mapped[str | None] = mapped_column(String(255))
    source_locator: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class LineageAlias(Base):
    """A curated alias; collisions across terms intentionally remain legal."""

    __tablename__ = "lineage_alias"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "term_id",
            "normalized_alias",
            "alias_type",
            name="uq_lineage_alias_term_value",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "term_id"],
            ["lineage_term.snapshot_id", "lineage_term.id"],
            name="fk_lineage_alias_term_same_snapshot",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    term_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_alias: Mapped[str] = mapped_column(Text, nullable=False)
    alias_type: Mapped[str] = mapped_column(String(64), nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'en'"))


class LineageClosure(Base):
    """Transitive closure constrained so both endpoints share a snapshot."""

    __tablename__ = "lineage_closure"
    __table_args__ = (
        CheckConstraint("depth >= 0", name="nonnegative_depth"),
        CheckConstraint(
            "(depth = 0 AND ancestor_term_id = descendant_term_id) OR "
            "(depth > 0 AND ancestor_term_id <> descendant_term_id)",
            name="depth_matches_identity",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "ancestor_term_id"],
            ["lineage_term.snapshot_id", "lineage_term.id"],
            name="fk_lineage_closure_ancestor_same_snapshot",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "descendant_term_id"],
            ["lineage_term.snapshot_id", "lineage_term.id"],
            name="fk_lineage_closure_descendant_same_snapshot",
            ondelete="CASCADE",
        ),
    )

    snapshot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ancestor_term_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    descendant_term_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)


class GenomeAssembly(Base):
    """An exact versioned assembly identity."""

    __tablename__ = "genome_assembly"
    __table_args__ = (
        UniqueConstraint(
            "namespace", "accession_version", name="uq_genome_assembly_namespace_accession"
        ),
        CheckConstraint(
            "accession_version ~ '^(GCA|GCF)_[0-9]+\\.[0-9]+$'",
            name="versioned_accession",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    assembly_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    namespace: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'ncbi'")
    )
    accession_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_organism_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_artifact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_artifact.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AssemblySequence(Base):
    """An exact versioned contig belonging to one exact assembly."""

    __tablename__ = "assembly_sequence"
    __table_args__ = (
        UniqueConstraint("assembly_id", "id", name="uq_assembly_sequence_assembly_id_id"),
        UniqueConstraint(
            "assembly_id",
            "accession_version",
            name="uq_assembly_sequence_assembly_accession",
        ),
        CheckConstraint(
            "accession_version ~ '^[A-Za-z0-9_]+\\.[0-9]+$'",
            name="versioned_accession",
        ),
        CheckConstraint("sequence_length > 0", name="positive_length"),
        CheckConstraint(
            "sequence_sha256 IS NULL OR " + SHA256_CHECK.format(column="sequence_sha256"),
            name="valid_sha256",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    assembly_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("genome_assembly.id", ondelete="RESTRICT"), nullable=False
    )
    sequence_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    namespace: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'insdc'")
    )
    accession_version: Mapped[str] = mapped_column(String(128), nullable=False)
    sequence_length: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sequence_sha256: Mapped[str | None] = mapped_column(String(64))
    source_artifact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_artifact.id", ondelete="RESTRICT"), nullable=False
    )


class ReleaseAssemblyMembership(Base):
    """The exact assembly allow-list for a release (ten assemblies in Draft B)."""

    __tablename__ = "release_assembly_membership"

    release_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("dataset_release.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    assembly_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("genome_assembly.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    membership_role: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'pilot_scope'")
    )


class AssemblyTaxonAssignment(Base):
    """A release-pinned source taxon assignment, not an ancient-host claim."""

    __tablename__ = "assembly_taxon_assignment"
    __table_args__ = (
        UniqueConstraint(
            "release_id",
            "assembly_id",
            "snapshot_id",
            "assignment_policy_key",
            name="uq_assembly_taxon_assignment_policy",
        ),
        CheckConstraint(
            "snapshot_role = 'assembly_source_taxonomy'",
            name="requires_assembly_source_snapshot",
        ),
        ForeignKeyConstraint(
            ["release_id", "assembly_id"],
            ["release_assembly_membership.release_id", "release_assembly_membership.assembly_id"],
            name="fk_assembly_taxon_assignment_release_assembly",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["release_id", "snapshot_id", "snapshot_role"],
            [
                "release_lineage_snapshot.release_id",
                "release_lineage_snapshot.snapshot_id",
                "release_lineage_snapshot.role",
            ],
            name="fk_assembly_taxon_assignment_pinned_snapshot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "term_id"],
            ["lineage_term.snapshot_id", "lineage_term.id"],
            name="fk_assembly_taxon_assignment_term_same_snapshot",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    assignment_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    release_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    assembly_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    snapshot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    snapshot_role: Mapped[str] = mapped_column(String(64), nullable=False)
    term_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    assignment_policy_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_artifact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_artifact.id", ondelete="RESTRICT"), nullable=False
    )
    source_locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class SourceRecord(Base):
    """One immutable source row before any scientific promotion."""

    __tablename__ = "source_record"
    __table_args__ = (
        UniqueConstraint("id", "snapshot_id", name="uq_source_record_id_snapshot"),
        UniqueConstraint(
            "snapshot_id", "artifact_id", "worksheet", "row_number", name="uq_source_record_row"
        ),
        UniqueConstraint(
            "snapshot_id",
            "assembly_accession_version",
            "sequence_accession_version",
            "native_vr_token",
            name="uq_source_record_occurrence",
        ),
        UniqueConstraint(
            "id", "snapshot_id", "native_vr_token", name="uq_source_record_id_snapshot_token"
        ),
        CheckConstraint("row_number > 0", name="positive_row_number"),
        CheckConstraint(
            SHA256_CHECK.format(column="raw_payload_sha256"),
            name="valid_payload_sha256",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "artifact_id"],
            ["source_artifact.snapshot_id", "source_artifact.id"],
            name="fk_source_record_artifact_same_snapshot",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_record_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    snapshot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    artifact_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    worksheet: Mapped[str] = mapped_column(Text, nullable=False)
    row_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    native_vr_token: Mapped[str] = mapped_column(String(255), nullable=False)
    assembly_accession_version: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence_accession_version: Mapped[str] = mapped_column(String(128), nullable=False)
    source_locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    raw_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class ImportRun(Base):
    """One deterministic attempt to import a frozen source artifact."""

    __tablename__ = "import_run"
    __table_args__ = (
        UniqueConstraint("id", "release_id", name="uq_import_run_id_release"),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled')",
            name="valid_status",
        ),
        CheckConstraint(
            "(status = 'running' AND finished_at IS NULL) OR "
            "(status <> 'running' AND finished_at IS NOT NULL)",
            name="finish_matches_status",
        ),
        CheckConstraint(SHA256_CHECK.format(column="code_sha256"), name="valid_code_sha256"),
        CheckConstraint(
            SHA256_CHECK.format(column="parameters_sha256"),
            name="valid_parameters_sha256",
        ),
        ForeignKeyConstraint(
            ["release_id", "source_snapshot_id"],
            ["release_source_snapshot.release_id", "release_source_snapshot.source_snapshot_id"],
            name="fk_import_run_pinned_snapshot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_snapshot_id", "source_artifact_id"],
            ["source_artifact.snapshot_id", "source_artifact.id"],
            name="fk_import_run_artifact_same_snapshot",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    release_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_snapshot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_artifact_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    importer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    importer_version: Mapped[str] = mapped_column(String(128), nullable=False)
    code_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    parameters_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessRun(Base):
    """A method-qualified scientific or curation activity."""

    __tablename__ = "process_run"
    __table_args__ = (
        UniqueConstraint("id", "release_id", name="uq_process_run_id_release"),
        UniqueConstraint(
            "id", "release_id", "execution_status", name="uq_process_run_membership_ref"
        ),
        CheckConstraint(
            "execution_status IN ('running', 'succeeded', 'failed', 'cancelled')",
            name="valid_execution_status",
        ),
        CheckConstraint(
            "(execution_status = 'running' AND finished_at IS NULL) OR "
            "(execution_status <> 'running' AND finished_at IS NOT NULL)",
            name="finish_matches_status",
        ),
        CheckConstraint(SHA256_CHECK.format(column="parameters_sha256"), name="valid_parameters"),
        ForeignKeyConstraint(
            ["release_id", "method_definition_id", "method_role"],
            [
                "release_method_definition.release_id",
                "release_method_definition.method_definition_id",
                "release_method_definition.role",
            ],
            name="fk_process_run_pinned_method",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["import_run_id", "release_id"],
            ["import_run.id", "import_run.release_id"],
            name="fk_process_run_import_run_same_release",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    process_run_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    release_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    method_definition_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    method_role: Mapped[str] = mapped_column(String(64), nullable=False)
    import_run_id: Mapped[int | None] = mapped_column(BigInteger)
    execution_status: Mapped[str] = mapped_column(String(32), nullable=False)
    software_agent_key: Mapped[str] = mapped_column(String(255), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    parameters_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EVELocus(Base):
    """A source occurrence anchored to one assembly and versioned contig.

    Coordinates intentionally do not live on this identity object.
    """

    __tablename__ = "eve_locus"
    __table_args__ = (
        UniqueConstraint("release_id", "id", name="uq_eve_locus_release_id_id"),
        UniqueConstraint(
            "release_id",
            "id",
            "assembly_id",
            "sequence_id",
            name="uq_eve_locus_release_id_assembly_sequence",
        ),
        UniqueConstraint("release_id", "locus_key", name="uq_eve_locus_release_key"),
        UniqueConstraint("release_id", "source_record_id", name="uq_eve_locus_source_record"),
        UniqueConstraint(
            "release_id",
            "id",
            "source_record_id",
            name="uq_eve_locus_release_id_source_record",
        ),
        UniqueConstraint(
            "release_id",
            "source_snapshot_id",
            "assembly_id",
            "sequence_id",
            "native_vr_token",
            "identity_policy_key",
            name="uq_eve_locus_source_occurrence",
        ),
        CheckConstraint(
            "locus_key ~ '^locus:eve:v1:sha256:[0-9a-f]{64}$'",
            name="valid_locus_key",
        ),
        ForeignKeyConstraint(
            ["release_id", "assembly_id"],
            ["release_assembly_membership.release_id", "release_assembly_membership.assembly_id"],
            name="fk_eve_locus_release_assembly",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["assembly_id", "sequence_id"],
            ["assembly_sequence.assembly_id", "assembly_sequence.id"],
            name="fk_eve_locus_sequence_same_assembly",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["release_id", "source_snapshot_id"],
            ["release_source_snapshot.release_id", "release_source_snapshot.source_snapshot_id"],
            name="fk_eve_locus_pinned_snapshot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_record_id", "source_snapshot_id", "native_vr_token"],
            ["source_record.id", "source_record.snapshot_id", "source_record.native_vr_token"],
            name="fk_eve_locus_source_occurrence",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    locus_key: Mapped[str] = mapped_column(String(255), nullable=False)
    release_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    assembly_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sequence_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_snapshot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_record_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    native_vr_token: Mapped[str] = mapped_column(String(255), nullable=False)
    identity_policy_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EVELocusPlacement(Base):
    """An optional, single canonical interval for an EVELocus."""

    __tablename__ = "eve_locus_placement"
    __table_args__ = (
        UniqueConstraint("release_id", "locus_id", name="uq_eve_locus_placement_locus"),
        UniqueConstraint(
            "id",
            "release_id",
            "locus_id",
            "precision",
            name="uq_eve_locus_placement_membership_ref",
        ),
        UniqueConstraint("id", "release_id", "locus_id", name="uq_eve_locus_placement_flank_ref"),
        CheckConstraint("start0 >= 0 AND start0 < end0", name="valid_interval"),
        CheckConstraint("strand IN ('+', '-', 'unknown')", name="valid_strand"),
        CheckConstraint("precision IN ('exact', 'approximate')", name="valid_precision"),
        CheckConstraint(
            "coordinate_system = '0-based-half-open'", name="canonical_coordinate_system"
        ),
        CheckConstraint(SHA256_CHECK.format(column="placement_sha256"), name="valid_sha256"),
        ForeignKeyConstraint(
            ["release_id", "locus_id", "assembly_id", "sequence_id"],
            [
                "eve_locus.release_id",
                "eve_locus.id",
                "eve_locus.assembly_id",
                "eve_locus.sequence_id",
            ],
            name="fk_eve_locus_placement_locus_sequence",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    placement_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    release_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    locus_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    assembly_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sequence_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    start0: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end0: Mapped[int] = mapped_column(BigInteger, nullable=False)
    strand: Mapped[str] = mapped_column(String(16), nullable=False)
    precision: Mapped[str] = mapped_column(String(16), nullable=False)
    coordinate_system: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'0-based-half-open'")
    )
    raw_location: Mapped[str | None] = mapped_column(Text)
    raw_coordinate_system: Mapped[str | None] = mapped_column(String(128))
    source_artifact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_artifact.id", ondelete="RESTRICT"), nullable=False
    )
    source_locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    placement_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


Index(
    "ix_eve_locus_placement_exact_interval",
    EVELocusPlacement.release_id,
    EVELocusPlacement.sequence_id,
    EVELocusPlacement.start0,
    EVELocusPlacement.end0,
    unique=False,
    postgresql_where=text("precision = 'exact'"),
)


class DetectionCall(Base):
    """One source-reported VR occurrence, distinct from locus membership."""

    __tablename__ = "detection_call"
    __table_args__ = (
        UniqueConstraint("release_id", "id", name="uq_detection_call_release_id_id"),
        UniqueConstraint(
            "release_id",
            "id",
            "process_run_id",
            name="uq_detection_call_release_id_process_run",
        ),
        UniqueConstraint(
            "release_id",
            "id",
            "source_record_id",
            name="uq_detection_call_release_id_source_record",
        ),
        UniqueConstraint("release_id", "call_key", name="uq_detection_call_release_key"),
        UniqueConstraint(
            "release_id",
            "source_record_id",
            "process_run_id",
            name="uq_detection_call_source_record_process_run",
        ),
        CheckConstraint("process_run_status = 'succeeded'", name="requires_succeeded_process_run"),
        ForeignKeyConstraint(
            ["source_record_id", "source_snapshot_id"],
            ["source_record.id", "source_record.snapshot_id"],
            name="fk_detection_call_source_record_snapshot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["release_id", "locus_id"],
            ["eve_locus.release_id", "eve_locus.id"],
            name="fk_detection_call_locus_same_release",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["process_run_id", "release_id", "process_run_status"],
            ["process_run.id", "process_run.release_id", "process_run.execution_status"],
            name="fk_detection_call_process_run_same_release",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    call_key: Mapped[str] = mapped_column(String(255), nullable=False)
    release_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_snapshot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_record_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    locus_id: Mapped[int | None] = mapped_column(BigInteger)
    process_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    process_run_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'succeeded'")
    )
    source_method_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    raw_result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class SourceAssessment(Base):
    """The source HCVR label and its normalized confidence band.

    This table has no path to release membership.  ``HCVR == Yes`` maps to
    ``source_high``; every other literal label maps to ``source_low``.
    """

    __tablename__ = "source_assessment"
    __table_args__ = (
        UniqueConstraint(
            "release_id", "call_id", "assessment_type", name="uq_source_assessment_call_type"
        ),
        UniqueConstraint(
            "id",
            "release_id",
            "call_id",
            "process_run_id",
            "source_label",
            "confidence",
            name="uq_source_assessment_assertion_ref",
        ),
        CheckConstraint("assessment_type = 'hcvr'", name="only_hcvr_assessment"),
        CheckConstraint("confidence IN ('source_high', 'source_low')", name="valid_confidence"),
        CheckConstraint(
            "(source_label = 'Yes' AND confidence = 'source_high') OR "
            "(source_label <> 'Yes' AND confidence = 'source_low')",
            name="label_maps_to_confidence",
        ),
        ForeignKeyConstraint(
            ["release_id", "call_id", "process_run_id"],
            [
                "detection_call.release_id",
                "detection_call.id",
                "detection_call.process_run_id",
            ],
            name="fk_source_assessment_call_same_release",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    assessment_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    release_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    call_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    process_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    assessment_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'hcvr'")
    )
    source_label: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False)
    source_artifact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_artifact.id", ondelete="RESTRICT"), nullable=False
    )
    source_locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class EvidenceItem(Base):
    """A release-scoped, checksummed and directly locatable evidence item."""

    __tablename__ = "evidence_item"
    __table_args__ = (
        UniqueConstraint("release_id", "id", name="uq_evidence_item_release_id_id"),
        UniqueConstraint("release_id", "evidence_key", name="uq_evidence_item_release_key"),
        CheckConstraint(SHA256_CHECK.format(column="evidence_sha256"), name="valid_sha256"),
        ForeignKeyConstraint(
            ["release_id", "source_snapshot_id"],
            ["release_source_snapshot.release_id", "release_source_snapshot.source_snapshot_id"],
            name="fk_evidence_item_pinned_snapshot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_snapshot_id", "source_artifact_id"],
            ["source_artifact.snapshot_id", "source_artifact.id"],
            name="fk_evidence_item_artifact_same_snapshot",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    evidence_key: Mapped[str] = mapped_column(String(255), nullable=False)
    release_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_snapshot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_artifact_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(128), nullable=False)
    source_locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)


class ScientificAssertion(Base):
    """A source- and method-qualified assertion, never membership by itself."""

    __tablename__ = "scientific_assertion"
    __table_args__ = (
        UniqueConstraint("release_id", "id", name="uq_scientific_assertion_release_id_id"),
        UniqueConstraint(
            "id",
            "release_id",
            "locus_id",
            "process_run_id",
            name="uq_scientific_assertion_membership_ref",
        ),
        UniqueConstraint("release_id", "assertion_key", name="uq_scientific_assertion_release_key"),
        CheckConstraint(
            "assertion_type IN ('hcvr', 'viral_major_taxon', 'vr_type')",
            name="valid_assertion_type",
        ),
        CheckConstraint("process_run_status = 'succeeded'", name="requires_succeeded_process_run"),
        CheckConstraint(
            "(assertion_type = 'hcvr' AND source_assessment_id IS NOT NULL "
            "AND source_label IS NOT NULL AND source_confidence IS NOT NULL "
            "AND lineage_snapshot_id IS NULL AND lineage_term_id IS NULL "
            "AND lineage_snapshot_role IS NULL) OR "
            "(assertion_type = 'viral_major_taxon' AND source_assessment_id IS NULL "
            "AND source_label IS NULL AND source_confidence IS NULL "
            "AND lineage_snapshot_id IS NOT NULL AND lineage_term_id IS NOT NULL "
            "AND lineage_snapshot_role IN "
            "('formal_viral_taxonomy', 'study_viral_lineage')) OR "
            "(assertion_type = 'vr_type' AND source_assessment_id IS NULL "
            "AND source_label IS NULL AND source_confidence IS NULL "
            "AND lineage_snapshot_id IS NULL AND lineage_term_id IS NULL "
            "AND lineage_snapshot_role IS NULL)",
            name="typed_detail_matches_assertion_type",
        ),
        ForeignKeyConstraint(
            ["release_id", "call_id", "process_run_id"],
            [
                "detection_call.release_id",
                "detection_call.id",
                "detection_call.process_run_id",
            ],
            name="fk_scientific_assertion_call_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["release_id", "locus_id"],
            ["eve_locus.release_id", "eve_locus.id"],
            name="fk_scientific_assertion_locus_same_release",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["process_run_id", "release_id", "process_run_status"],
            ["process_run.id", "process_run.release_id", "process_run.execution_status"],
            name="fk_scientific_assertion_succeeded_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "source_assessment_id",
                "release_id",
                "call_id",
                "process_run_id",
                "source_label",
                "source_confidence",
            ],
            [
                "source_assessment.id",
                "source_assessment.release_id",
                "source_assessment.call_id",
                "source_assessment.process_run_id",
                "source_assessment.source_label",
                "source_assessment.confidence",
            ],
            name="fk_scientific_assertion_hcvr_source_assessment",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["release_id", "lineage_snapshot_id", "lineage_snapshot_role"],
            [
                "release_lineage_snapshot.release_id",
                "release_lineage_snapshot.snapshot_id",
                "release_lineage_snapshot.role",
            ],
            name="fk_scientific_assertion_pinned_lineage_snapshot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["lineage_snapshot_id", "lineage_term_id"],
            ["lineage_term.snapshot_id", "lineage_term.id"],
            name="fk_scientific_assertion_lineage_term_same_snapshot",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    assertion_key: Mapped[str] = mapped_column(String(255), nullable=False)
    release_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    call_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    locus_id: Mapped[int | None] = mapped_column(BigInteger)
    process_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    process_run_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'succeeded'")
    )
    assertion_type: Mapped[str] = mapped_column(String(64), nullable=False)
    predicate_key: Mapped[str] = mapped_column(String(255), nullable=False)
    asserted_value: Mapped[str] = mapped_column(Text, nullable=False)
    source_assessment_id: Mapped[int | None] = mapped_column(BigInteger)
    source_label: Mapped[str | None] = mapped_column(Text)
    source_confidence: Mapped[str | None] = mapped_column(String(32))
    lineage_snapshot_id: Mapped[int | None] = mapped_column(BigInteger)
    lineage_snapshot_role: Mapped[str | None] = mapped_column(String(64))
    lineage_term_id: Mapped[int | None] = mapped_column(BigInteger)
    result_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class AssertionEvidence(Base):
    """A typed evidence edge with real FKs on both endpoints."""

    __tablename__ = "assertion_evidence"
    __table_args__ = (
        CheckConstraint(
            "relation IN ('supports', 'contradicts', 'context')", name="valid_relation"
        ),
        ForeignKeyConstraint(
            ["release_id", "assertion_id"],
            ["scientific_assertion.release_id", "scientific_assertion.id"],
            name="fk_assertion_evidence_assertion_same_release",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["release_id", "evidence_id"],
            ["evidence_item.release_id", "evidence_item.id"],
            name="fk_assertion_evidence_item_same_release",
            ondelete="RESTRICT",
        ),
    )

    release_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    assertion_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    evidence_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    relation: Mapped[str] = mapped_column(String(32), primary_key=True)


class ImportLedger(Base):
    """Exactly one terminal outcome for each source row processed by a run."""

    __tablename__ = "import_ledger"
    __table_args__ = (
        UniqueConstraint("run_id", "source_record_id", name="uq_import_ledger_run_record"),
        UniqueConstraint(
            "id", "release_id", "locus_id", "outcome", name="uq_import_ledger_decision_ref"
        ),
        CheckConstraint(
            "outcome IN ('normalized_candidate', 'review', 'quarantine', 'exclude')",
            name="valid_outcome",
        ),
        CheckConstraint(
            "outcome <> 'normalized_candidate' OR (call_id IS NOT NULL AND locus_id IS NOT NULL)",
            name="candidate_has_call_and_locus",
        ),
        CheckConstraint(SHA256_CHECK.format(column="result_sha256"), name="valid_result_sha256"),
        ForeignKeyConstraint(
            ["run_id", "release_id"],
            ["import_run.id", "import_run.release_id"],
            name="fk_import_ledger_run_same_release",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["release_id", "call_id", "source_record_id"],
            [
                "detection_call.release_id",
                "detection_call.id",
                "detection_call.source_record_id",
            ],
            name="fk_import_ledger_call_same_source_record",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["release_id", "locus_id", "source_record_id"],
            ["eve_locus.release_id", "eve_locus.id", "eve_locus.source_record_id"],
            name="fk_import_ledger_locus_same_source_record",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    release_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_record_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_record.id", ondelete="RESTRICT"), nullable=False
    )
    call_id: Mapped[int | None] = mapped_column(BigInteger)
    locus_id: Mapped[int | None] = mapped_column(BigInteger)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    result_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class QuarantineIssue(Base):
    """A structured, auditable reason that blocks normalization or release."""

    __tablename__ = "quarantine_issue"
    __table_args__ = (
        CheckConstraint("severity IN ('error', 'warning')", name="valid_severity"),
        CheckConstraint("status IN ('open', 'resolved', 'waived')", name="valid_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    issue_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    ledger_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("import_ledger.id", ondelete="CASCADE"), nullable=False
    )
    issue_code: Mapped[str] = mapped_column(String(128), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'open'"))
    field_name: Mapped[str | None] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    raw_value: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FlankAssessment(Base):
    """An independent left- or right-flank assessment for one placement."""

    __tablename__ = "flank_assessment"
    __table_args__ = (
        UniqueConstraint(
            "release_id",
            "locus_id",
            "placement_id",
            "side",
            "assessment_policy_key",
            name="uq_flank_assessment_policy_side",
        ),
        UniqueConstraint(
            "id",
            "release_id",
            "locus_id",
            "placement_id",
            "side",
            "verdict",
            name="uq_flank_assessment_membership_ref",
        ),
        CheckConstraint("side IN ('left', 'right')", name="valid_side"),
        CheckConstraint(
            "verdict IN ('supported', 'contradicted', 'insufficient', 'not_assessed')",
            name="valid_verdict",
        ),
        CheckConstraint("inspection_window_bp > 0", name="positive_window"),
        CheckConstraint("available_bp >= 0", name="nonnegative_available"),
        CheckConstraint(
            "inspected_bp >= 0 AND inspected_bp <= available_bp",
            name="valid_inspected_length",
        ),
        CheckConstraint(
            "verdict <> 'supported' OR inspected_bp > 0",
            name="supported_has_inspected_sequence",
        ),
        ForeignKeyConstraint(
            ["placement_id", "release_id", "locus_id"],
            [
                "eve_locus_placement.id",
                "eve_locus_placement.release_id",
                "eve_locus_placement.locus_id",
            ],
            name="fk_flank_assessment_placement_same_locus",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    assessment_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    release_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    locus_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    placement_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    inspection_window_bp: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("20000")
    )
    available_bp: Mapped[int] = mapped_column(Integer, nullable=False)
    inspected_bp: Mapped[int] = mapped_column(Integer, nullable=False)
    assessment_policy_key: Mapped[str] = mapped_column(String(255), nullable=False)
    method_or_curator_key: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence_artifact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_artifact.id", ondelete="RESTRICT"), nullable=False
    )
    evidence_locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InclusionDecision(Base):
    """A policy- or human-authorized decision, still separate from membership."""

    __tablename__ = "inclusion_decision"
    __table_args__ = (
        UniqueConstraint(
            "release_id", "locus_id", "policy_key", name="uq_inclusion_decision_policy"
        ),
        UniqueConstraint(
            "id",
            "release_id",
            "locus_id",
            "decision_code",
            "placement_id",
            name="uq_inclusion_decision_membership_ref",
        ),
        CheckConstraint(
            "decision_code IN ('include', 'review', 'quarantine', 'exclude')",
            name="valid_decision",
        ),
        CheckConstraint(
            "decision_code <> 'include' OR placement_id IS NOT NULL",
            name="include_has_placement",
        ),
        CheckConstraint(
            "decision_code <> 'include' OR import_outcome = 'normalized_candidate'",
            name="include_has_normalized_import",
        ),
        ForeignKeyConstraint(
            ["release_id", "locus_id"],
            ["eve_locus.release_id", "eve_locus.id"],
            name="fk_inclusion_decision_locus_same_release",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["placement_id", "release_id", "locus_id"],
            [
                "eve_locus_placement.id",
                "eve_locus_placement.release_id",
                "eve_locus_placement.locus_id",
            ],
            name="fk_inclusion_decision_placement_same_locus",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["import_ledger_id", "release_id", "locus_id", "import_outcome"],
            [
                "import_ledger.id",
                "import_ledger.release_id",
                "import_ledger.locus_id",
                "import_ledger.outcome",
            ],
            name="fk_inclusion_decision_import_outcome",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    decision_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    release_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    locus_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    placement_id: Mapped[int | None] = mapped_column(BigInteger)
    import_ledger_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    import_outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    decision_code: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_key: Mapped[str] = mapped_column(String(255), nullable=False)
    authorized_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReleaseLocusMembership(Base):
    """Public membership whose gates are encoded as typed composite FKs."""

    __tablename__ = "release_locus_membership"
    __table_args__ = (
        CheckConstraint("placement_precision = 'exact'", name="requires_exact_placement"),
        CheckConstraint("decision_code = 'include'", name="requires_include_decision"),
        CheckConstraint(
            "left_flank_side = 'left' AND left_flank_verdict = 'supported'",
            name="requires_supported_left_flank",
        ),
        CheckConstraint(
            "right_flank_side = 'right' AND right_flank_verdict = 'supported'",
            name="requires_supported_right_flank",
        ),
        CheckConstraint(
            "left_flank_assessment_id <> right_flank_assessment_id",
            name="distinct_flank_assessments",
        ),
        ForeignKeyConstraint(
            ["release_id", "locus_id"],
            ["eve_locus.release_id", "eve_locus.id"],
            name="fk_release_locus_membership_locus",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["placement_id", "release_id", "locus_id", "placement_precision"],
            [
                "eve_locus_placement.id",
                "eve_locus_placement.release_id",
                "eve_locus_placement.locus_id",
                "eve_locus_placement.precision",
            ],
            name="fk_release_locus_membership_exact_placement",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "inclusion_decision_id",
                "release_id",
                "locus_id",
                "decision_code",
                "placement_id",
            ],
            [
                "inclusion_decision.id",
                "inclusion_decision.release_id",
                "inclusion_decision.locus_id",
                "inclusion_decision.decision_code",
                "inclusion_decision.placement_id",
            ],
            name="fk_release_locus_membership_include_decision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "left_flank_assessment_id",
                "release_id",
                "locus_id",
                "placement_id",
                "left_flank_side",
                "left_flank_verdict",
            ],
            [
                "flank_assessment.id",
                "flank_assessment.release_id",
                "flank_assessment.locus_id",
                "flank_assessment.placement_id",
                "flank_assessment.side",
                "flank_assessment.verdict",
            ],
            name="fk_release_locus_membership_left_flank",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "right_flank_assessment_id",
                "release_id",
                "locus_id",
                "placement_id",
                "right_flank_side",
                "right_flank_verdict",
            ],
            [
                "flank_assessment.id",
                "flank_assessment.release_id",
                "flank_assessment.locus_id",
                "flank_assessment.placement_id",
                "flank_assessment.side",
                "flank_assessment.verdict",
            ],
            name="fk_release_locus_membership_right_flank",
            ondelete="RESTRICT",
        ),
    )

    release_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    locus_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    placement_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    placement_precision: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'exact'")
    )
    inclusion_decision_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    decision_code: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'include'")
    )
    left_flank_assessment_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    left_flank_side: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'left'")
    )
    left_flank_verdict: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'supported'")
    )
    right_flank_assessment_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    right_flank_side: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'right'")
    )
    right_flank_verdict: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'supported'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReleaseAssertionMembership(Base):
    """Explicit public membership for an evidence-backed scientific assertion."""

    __tablename__ = "release_assertion_membership"
    __table_args__ = (
        CheckConstraint("process_run_status = 'succeeded'", name="requires_succeeded_process_run"),
        CheckConstraint("evidence_relation = 'supports'", name="requires_supporting_evidence"),
        ForeignKeyConstraint(
            ["release_id", "locus_id"],
            ["release_locus_membership.release_id", "release_locus_membership.locus_id"],
            name="fk_release_assertion_membership_public_locus",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["assertion_id", "release_id", "locus_id", "process_run_id"],
            [
                "scientific_assertion.id",
                "scientific_assertion.release_id",
                "scientific_assertion.locus_id",
                "scientific_assertion.process_run_id",
            ],
            name="fk_release_assertion_membership_assertion",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["process_run_id", "release_id", "process_run_status"],
            ["process_run.id", "process_run.release_id", "process_run.execution_status"],
            name="fk_release_assertion_membership_succeeded_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["release_id", "assertion_id", "supporting_evidence_id", "evidence_relation"],
            [
                "assertion_evidence.release_id",
                "assertion_evidence.assertion_id",
                "assertion_evidence.evidence_id",
                "assertion_evidence.relation",
            ],
            name="fk_release_assertion_membership_supporting_evidence",
            ondelete="RESTRICT",
        ),
    )

    release_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    assertion_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    locus_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    process_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    process_run_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'succeeded'")
    )
    supporting_evidence_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    evidence_relation: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'supports'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
