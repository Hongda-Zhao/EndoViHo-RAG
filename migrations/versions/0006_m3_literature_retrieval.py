"""Add the fixed-corpus literature retrieval persistence layer

Revision ID: 0006_m3_literature_retrieval
Revises: 0005_m1_fail_closed_publication
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision: str = "0006_m3_literature_retrieval"
down_revision: str | Sequence[str] | None = "0005_m1_fail_closed_publication"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CORPUS_LIFECYCLE_SQL = """
CREATE OR REPLACE FUNCTION eve_guard_corpus_release_lifecycle()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    has_receipt boolean;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'candidate' THEN
            RAISE EXCEPTION 'new corpus releases must start as candidate'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        IF OLD.status IN ('published', 'retired') THEN
            RAISE EXCEPTION 'published or retired corpus releases are immutable'
                USING ERRCODE = '55000';
        END IF;
        RETURN OLD;
    END IF;

    IF ROW(
        NEW.id, NEW.corpus_release_key, NEW.title, NEW.purpose,
        NEW.manifest_sha256, NEW.policy_graph_sha256,
        NEW.manifest_document_count, NEW.expected_chunk_count_min,
        NEW.expected_chunk_count_max, NEW.parser_policy_id,
        NEW.chunking_policy_id, NEW.fts_policy_id, NEW.retrieval_policy_id,
        NEW.anchor_policy_id, NEW.embedding_model_id,
        NEW.supersedes_release_id, NEW.created_at
    ) IS DISTINCT FROM ROW(
        OLD.id, OLD.corpus_release_key, OLD.title, OLD.purpose,
        OLD.manifest_sha256, OLD.policy_graph_sha256,
        OLD.manifest_document_count, OLD.expected_chunk_count_min,
        OLD.expected_chunk_count_max, OLD.parser_policy_id,
        OLD.chunking_policy_id, OLD.fts_policy_id, OLD.retrieval_policy_id,
        OLD.anchor_policy_id, OLD.embedding_model_id,
        OLD.supersedes_release_id, OLD.created_at
    ) THEN
        RAISE EXCEPTION 'corpus release identity and dependency graph are immutable'
            USING ERRCODE = '55000';
    END IF;

    IF NEW.status = OLD.status
       AND NEW.published_at IS NOT DISTINCT FROM OLD.published_at THEN
        RETURN NEW;
    END IF;

    IF OLD.status IN ('published', 'retired', 'rejected') THEN
        IF OLD.status = 'published'
           AND NEW.status = 'retired'
           AND NEW.published_at IS NOT DISTINCT FROM OLD.published_at THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'terminal corpus release state is immutable'
            USING ERRCODE = '55000';
    END IF;

    IF NEW.status IN ('validated', 'published') THEN
        SELECT EXISTS (
            SELECT 1
              FROM corpus_validation_receipt AS receipt
             WHERE receipt.release_id = OLD.id
               AND receipt.status = 'passed'
               AND receipt.trusted
               AND receipt.manifest_sha256 = OLD.manifest_sha256
               AND receipt.policy_graph_sha256 = OLD.policy_graph_sha256
        ) INTO has_receipt;
        IF NOT has_receipt THEN
            RAISE EXCEPTION 'trusted passing corpus validation receipt is required'
                USING ERRCODE = '55000';
        END IF;
    END IF;

    IF OLD.status = 'candidate' AND NEW.status = 'validated'
       AND NEW.published_at IS NULL THEN
        RETURN NEW;
    ELSIF OLD.status = 'validated' AND NEW.status = 'published'
          AND NEW.published_at IS NOT NULL THEN
        RETURN NEW;
    ELSIF OLD.status IN ('candidate', 'validated') AND NEW.status = 'rejected'
          AND NEW.published_at IS NULL THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'invalid corpus release lifecycle transition'
        USING ERRCODE = '23514';
END;
$$
"""


_IMMUTABILITY_SQL = """
CREATE OR REPLACE FUNCTION eve_guard_immutable_literature_row()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'immutable literature identity row cannot be changed'
        USING ERRCODE = '55000';
END;
$$;

CREATE OR REPLACE FUNCTION eve_guard_published_corpus_child()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    target_release_id bigint;
    target_status text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        target_release_id := OLD.release_id;
    ELSE
        target_release_id := NEW.release_id;
    END IF;

    SELECT status INTO target_status
      FROM corpus_release
     WHERE id = target_release_id;

    IF target_status IN ('published', 'retired') THEN
        RAISE EXCEPTION 'published or retired corpus content is immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$
"""


def upgrade() -> None:
    """Install pgvector and the approved immutable literature object graph."""

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "literature_policy",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("policy_key", sa.String(length=255), nullable=False),
        sa.Column("policy_kind", sa.String(length=32), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("policy_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("policy_sha256", sa.String(length=64), nullable=False),
        sa.Column("code_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "policy_kind IN ('parser', 'chunking', 'fts', 'retrieval', 'anchor')",
            name=op.f("ck_literature_policy_valid_policy_kind"),
        ),
        sa.CheckConstraint(
            "policy_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_literature_policy_valid_policy_sha256"),
        ),
        sa.CheckConstraint(
            "code_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_literature_policy_valid_code_sha256"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(policy_json) = 'object'",
            name=op.f("ck_literature_policy_policy_json_is_object"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_literature_policy")),
        sa.UniqueConstraint("id", "policy_kind", name="uq_literature_policy_id_kind"),
        sa.UniqueConstraint("policy_key", name=op.f("uq_literature_policy_policy_key")),
    )

    op.create_table(
        "embedding_model",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("model_key", sa.String(length=255), nullable=False),
        sa.Column("provider_kind", sa.String(length=32), nullable=False),
        sa.Column("repository_id", sa.String(length=255), nullable=False),
        sa.Column("revision", sa.String(length=128), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("max_sequence_tokens", sa.Integer(), nullable=False),
        sa.Column("pooling", sa.String(length=32), nullable=False),
        sa.Column("l2_normalized", sa.Boolean(), nullable=False),
        sa.Column("passage_prefix", sa.Text(), nullable=False),
        sa.Column("query_prefix", sa.Text(), nullable=False),
        sa.Column("similarity", sa.String(length=32), nullable=False),
        sa.Column("license_key", sa.String(length=255), nullable=False),
        sa.Column("artifact_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("model_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "provider_kind IN ('local_hf', 'deterministic_fake')",
            name=op.f("ck_embedding_model_valid_provider_kind"),
        ),
        sa.CheckConstraint("dimension = 384", name=op.f("ck_embedding_model_dimension_is_384")),
        sa.CheckConstraint(
            "max_sequence_tokens = 512", name=op.f("ck_embedding_model_max_sequence_is_512")
        ),
        sa.CheckConstraint("pooling = 'cls'", name=op.f("ck_embedding_model_pooling_is_cls")),
        sa.CheckConstraint(
            "l2_normalized", name=op.f("ck_embedding_model_requires_l2_normalization")
        ),
        sa.CheckConstraint(
            "similarity = 'cosine'", name=op.f("ck_embedding_model_similarity_is_cosine")
        ),
        sa.CheckConstraint(
            "artifact_manifest_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_embedding_model_valid_artifact_manifest_sha256"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(model_metadata) = 'object'",
            name=op.f("ck_embedding_model_metadata_is_object"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_embedding_model")),
        sa.UniqueConstraint("id", "model_key", name="uq_embedding_model_id_key"),
        sa.UniqueConstraint("model_key", name=op.f("uq_embedding_model_model_key")),
    )

    op.create_table(
        "corpus_release",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("corpus_release_key", sa.String(length=255), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default=sa.text("'candidate'"), nullable=False
        ),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("policy_graph_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest_document_count", sa.Integer(), nullable=False),
        sa.Column("expected_chunk_count_min", sa.Integer(), nullable=False),
        sa.Column("expected_chunk_count_max", sa.Integer(), nullable=False),
        sa.Column("parser_policy_id", sa.BigInteger(), nullable=False),
        sa.Column("chunking_policy_id", sa.BigInteger(), nullable=False),
        sa.Column("fts_policy_id", sa.BigInteger(), nullable=False),
        sa.Column("retrieval_policy_id", sa.BigInteger(), nullable=False),
        sa.Column("anchor_policy_id", sa.BigInteger(), nullable=False),
        sa.Column("embedding_model_id", sa.BigInteger(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_release_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "corpus_release_key ~ '^corpus:endoviho-rag:v0:[0-9]{8}:[0-9]{3}$'",
            name=op.f("ck_corpus_release_valid_corpus_release_key"),
        ),
        sa.CheckConstraint(
            "status IN ('candidate', 'validated', 'published', 'retired', 'rejected')",
            name=op.f("ck_corpus_release_valid_status"),
        ),
        sa.CheckConstraint(
            "(status IN ('published', 'retired') AND published_at IS NOT NULL) OR "
            "(status NOT IN ('published', 'retired') AND published_at IS NULL)",
            name=op.f("ck_corpus_release_publication_fields_match_status"),
        ),
        sa.CheckConstraint(
            "manifest_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_corpus_release_valid_manifest")
        ),
        sa.CheckConstraint(
            "policy_graph_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_corpus_release_valid_policy_graph"),
        ),
        sa.CheckConstraint(
            "manifest_document_count > 0", name=op.f("ck_corpus_release_positive_document_count")
        ),
        sa.CheckConstraint(
            "expected_chunk_count_min > 0 AND expected_chunk_count_max >= expected_chunk_count_min",
            name=op.f("ck_corpus_release_valid_chunk_count_range"),
        ),
        sa.CheckConstraint(
            "supersedes_release_id IS NULL OR supersedes_release_id <> id",
            name=op.f("ck_corpus_release_does_not_supersede_self"),
        ),
        sa.ForeignKeyConstraint(
            ["anchor_policy_id"],
            ["literature_policy.id"],
            name=op.f("fk_corpus_release_anchor_policy_id_literature_policy"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["chunking_policy_id"],
            ["literature_policy.id"],
            name=op.f("fk_corpus_release_chunking_policy_id_literature_policy"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["embedding_model_id"],
            ["embedding_model.id"],
            name=op.f("fk_corpus_release_embedding_model_id_embedding_model"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fts_policy_id"],
            ["literature_policy.id"],
            name=op.f("fk_corpus_release_fts_policy_id_literature_policy"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parser_policy_id"],
            ["literature_policy.id"],
            name=op.f("fk_corpus_release_parser_policy_id_literature_policy"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["retrieval_policy_id"],
            ["literature_policy.id"],
            name=op.f("fk_corpus_release_retrieval_policy_id_literature_policy"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_release_id"],
            ["corpus_release.id"],
            name=op.f("fk_corpus_release_supersedes_release_id_corpus_release"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_corpus_release")),
        sa.UniqueConstraint(
            "id", "parser_policy_id", "chunking_policy_id", name="uq_corpus_release_chunk_policies"
        ),
        sa.UniqueConstraint("id", "embedding_model_id", name="uq_corpus_release_embedding_model"),
        sa.UniqueConstraint(
            "corpus_release_key", name=op.f("uq_corpus_release_corpus_release_key")
        ),
    )
    op.create_index(op.f("ix_corpus_release_status"), "corpus_release", ["status"], unique=False)

    op.create_table(
        "document",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("document_key", sa.String(length=255), nullable=False),
        sa.Column("source_artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("normalized_document_sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=64), nullable=False),
        sa.Column("document_version", sa.String(length=255), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("authors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("doi", sa.String(length=255), nullable=True),
        sa.Column("pmid", sa.String(length=32), nullable=True),
        sa.Column("pmcid", sa.String(length=35), nullable=True),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("declared_license", sa.String(length=255), nullable=False),
        sa.Column("license_evidence_uri", sa.Text(), nullable=False),
        sa.Column("license_review_status", sa.String(length=32), nullable=False),
        sa.Column("retrieval_text_allowed", sa.Boolean(), nullable=False),
        sa.Column(
            "bibliographic_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "document_key ~ '^document:sha256:[0-9a-f]{64}$'",
            name=op.f("ck_document_valid_document_key"),
        ),
        sa.CheckConstraint(
            "source_artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_document_valid_source_sha256"),
        ),
        sa.CheckConstraint(
            "normalized_document_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_document_valid_normalized_sha256"),
        ),
        sa.CheckConstraint(
            "byte_size > 0 AND byte_size <= 52428800", name=op.f("ck_document_valid_byte_size")
        ),
        sa.CheckConstraint(
            "media_type IN ('text/markdown', 'text/plain', 'application/xml')",
            name=op.f("ck_document_valid_media_type"),
        ),
        sa.CheckConstraint(
            "license_review_status IN "
            "('approved', 'pending', 'rejected', 'unknown', 'incompatible')",
            name=op.f("ck_document_valid_license_status"),
        ),
        sa.CheckConstraint(
            "NOT retrieval_text_allowed OR license_review_status = 'approved'",
            name=op.f("ck_document_text_return_requires_approved_license"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(authors) = 'array'", name=op.f("ck_document_authors_is_array")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(bibliographic_metadata) = 'object'",
            name=op.f("ck_document_metadata_is_object"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document")),
        sa.UniqueConstraint("document_key", name=op.f("uq_document_document_key")),
    )

    op.create_table(
        "corpus_document_membership",
        sa.Column("release_id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("manifest_row", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "manifest_row > 0", name=op.f("ck_corpus_document_membership_positive_manifest_row")
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["document.id"],
            name=op.f("fk_corpus_document_membership_document_id_document"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["release_id"],
            ["corpus_release.id"],
            name=op.f("fk_corpus_document_membership_release_id_corpus_release"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "release_id", "document_id", name=op.f("pk_corpus_document_membership")
        ),
        sa.UniqueConstraint("release_id", "manifest_row", name="uq_corpus_membership_manifest_row"),
    )

    op.create_table(
        "document_chunk",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("chunk_key", sa.String(length=255), nullable=False),
        sa.Column("release_id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("parser_policy_id", sa.BigInteger(), nullable=False),
        sa.Column("chunking_policy_id", sa.BigInteger(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("section_path", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("block_type", sa.String(length=64), nullable=False),
        sa.Column("locator", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("locator_text", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("fts_document", postgresql.TSVECTOR(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "chunk_key ~ '^chunk:sha256:[0-9a-f]{64}$'",
            name=op.f("ck_document_chunk_valid_chunk_key"),
        ),
        sa.CheckConstraint(
            "text_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_document_chunk_valid_text_sha256")
        ),
        sa.CheckConstraint("length(text) > 0", name=op.f("ck_document_chunk_nonempty_text")),
        sa.CheckConstraint(
            "token_count > 0 AND token_count <= 448",
            name=op.f("ck_document_chunk_valid_token_count"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(section_path) = 'array'",
            name=op.f("ck_document_chunk_section_path_is_array"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(locator) = 'object'", name=op.f("ck_document_chunk_locator_is_object")
        ),
        sa.ForeignKeyConstraint(
            ["release_id", "document_id"],
            ["corpus_document_membership.release_id", "corpus_document_membership.document_id"],
            name="fk_document_chunk_corpus_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["release_id", "parser_policy_id", "chunking_policy_id"],
            [
                "corpus_release.id",
                "corpus_release.parser_policy_id",
                "corpus_release.chunking_policy_id",
            ],
            name="fk_document_chunk_release_policies",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_chunk")),
        sa.UniqueConstraint("chunk_key", name=op.f("uq_document_chunk_chunk_key")),
        sa.UniqueConstraint("release_id", "id", name="uq_document_chunk_release_id"),
        sa.UniqueConstraint(
            "release_id", "document_id", "chunk_index", name="uq_document_chunk_document_index"
        ),
    )
    op.create_index(
        "ix_document_chunk_fts_document_gin",
        "document_chunk",
        ["fts_document"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_document_chunk_release_document",
        "document_chunk",
        ["release_id", "document_id"],
        unique=False,
    )

    op.create_table(
        "document_embedding",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("release_id", sa.BigInteger(), nullable=False),
        sa.Column("chunk_id", sa.BigInteger(), nullable=False),
        sa.Column("embedding_model_id", sa.BigInteger(), nullable=False),
        sa.Column("embedding", VECTOR(dim=384), nullable=False),
        sa.Column("embedding_mode", sa.String(length=16), nullable=False),
        sa.Column("embedding_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "embedding_mode = 'passage'",
            name=op.f("ck_document_embedding_passage_embeddings_only"),
        ),
        sa.CheckConstraint(
            "embedding_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_document_embedding_valid_embedding_sha256"),
        ),
        sa.CheckConstraint(
            "vector_norm(embedding) BETWEEN 0.99999 AND 1.00001",
            name=op.f("ck_document_embedding_unit_normalized_embedding"),
        ),
        sa.ForeignKeyConstraint(
            ["release_id", "chunk_id"],
            ["document_chunk.release_id", "document_chunk.id"],
            name="fk_document_embedding_chunk_same_release",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["release_id", "embedding_model_id"],
            ["corpus_release.id", "corpus_release.embedding_model_id"],
            name="fk_document_embedding_release_model",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_embedding")),
        sa.UniqueConstraint(
            "release_id", "chunk_id", "embedding_model_id", name="uq_chunk_model_embedding"
        ),
    )
    op.create_index(
        "ix_document_embedding_hnsw_cosine",
        "document_embedding",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "document_anchor",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("anchor_key", sa.String(length=255), nullable=False),
        sa.Column("release_id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("anchor_type", sa.String(length=32), nullable=False),
        sa.Column("locus_key", sa.String(length=255), nullable=True),
        sa.Column("assembly_key", sa.String(length=255), nullable=True),
        sa.Column("lineage_snapshot_key", sa.String(length=255), nullable=True),
        sa.Column("lineage_term_key", sa.String(length=255), nullable=True),
        sa.Column("method_definition_key", sa.String(length=255), nullable=True),
        sa.Column("target_document_key", sa.String(length=255), nullable=True),
        sa.Column("doi", sa.String(length=255), nullable=True),
        sa.Column("pmid", sa.String(length=32), nullable=True),
        sa.Column("pmcid", sa.String(length=35), nullable=True),
        sa.Column("keyword_phrase", sa.Text(), nullable=True),
        sa.Column("manifest_row", sa.Integer(), nullable=False),
        sa.Column("curation_method", sa.String(length=255), nullable=False),
        sa.Column("source_locator", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("anchor_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "anchor_key ~ '^anchor:sha256:[0-9a-f]{64}$'",
            name=op.f("ck_document_anchor_valid_anchor_key"),
        ),
        sa.CheckConstraint(
            "anchor_type IN ('locus', 'assembly', 'lineage', 'method', 'document', 'keyword')",
            name=op.f("ck_document_anchor_valid_anchor_type"),
        ),
        sa.CheckConstraint(
            "(anchor_type = 'locus' AND locus_key IS NOT NULL AND "
            "assembly_key IS NULL AND lineage_snapshot_key IS NULL AND lineage_term_key IS NULL "
            "AND method_definition_key IS NULL AND target_document_key IS NULL AND doi IS NULL "
            "AND pmid IS NULL AND pmcid IS NULL AND keyword_phrase IS NULL) OR "
            "(anchor_type = 'assembly' AND assembly_key IS NOT NULL AND locus_key IS NULL "
            "AND lineage_snapshot_key IS NULL AND lineage_term_key IS NULL "
            "AND method_definition_key IS NULL AND target_document_key IS NULL AND doi IS NULL "
            "AND pmid IS NULL AND pmcid IS NULL AND keyword_phrase IS NULL) OR "
            "(anchor_type = 'lineage' AND lineage_snapshot_key IS NOT NULL "
            "AND lineage_term_key IS NOT NULL AND locus_key IS NULL AND assembly_key IS NULL "
            "AND method_definition_key IS NULL AND target_document_key IS NULL AND doi IS NULL "
            "AND pmid IS NULL AND pmcid IS NULL AND keyword_phrase IS NULL) OR "
            "(anchor_type = 'method' AND method_definition_key IS NOT NULL AND locus_key IS NULL "
            "AND assembly_key IS NULL AND lineage_snapshot_key IS NULL "
            "AND lineage_term_key IS NULL "
            "AND target_document_key IS NULL AND doi IS NULL AND pmid IS NULL AND pmcid IS NULL "
            "AND keyword_phrase IS NULL) OR "
            "(anchor_type = 'document' AND num_nonnulls(target_document_key, doi, pmid, pmcid) = 1 "
            "AND locus_key IS NULL AND assembly_key IS NULL AND lineage_snapshot_key IS NULL "
            "AND lineage_term_key IS NULL AND method_definition_key IS NULL "
            "AND keyword_phrase IS NULL) OR "
            "(anchor_type = 'keyword' AND keyword_phrase IS NOT NULL AND locus_key IS NULL "
            "AND assembly_key IS NULL AND lineage_snapshot_key IS NULL "
            "AND lineage_term_key IS NULL "
            "AND method_definition_key IS NULL AND target_document_key IS NULL AND doi IS NULL "
            "AND pmid IS NULL AND pmcid IS NULL)",
            name=op.f("ck_document_anchor_typed_target_matches_anchor_type"),
        ),
        sa.CheckConstraint(
            "anchor_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_document_anchor_valid_anchor_sha256"),
        ),
        sa.CheckConstraint(
            "manifest_row > 0", name=op.f("ck_document_anchor_positive_manifest_row")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_locator) = 'object'",
            name=op.f("ck_document_anchor_locator_is_object"),
        ),
        sa.ForeignKeyConstraint(
            ["release_id", "document_id"],
            ["corpus_document_membership.release_id", "corpus_document_membership.document_id"],
            name="fk_document_anchor_corpus_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_anchor")),
        sa.UniqueConstraint("anchor_key", name=op.f("uq_document_anchor_anchor_key")),
    )
    op.create_index(
        "ix_document_anchor_release_type",
        "document_anchor",
        ["release_id", "anchor_type"],
        unique=False,
    )

    op.create_table(
        "corpus_import_run",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("run_key", sa.String(length=255), nullable=False),
        sa.Column("release_id", sa.BigInteger(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("importer_version", sa.String(length=128), nullable=False),
        sa.Column("code_sha256", sa.String(length=64), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("parameters_sha256", sa.String(length=64), nullable=False),
        sa.Column("terminal_counts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "run_key ~ '^corpus-import:sha256:[0-9a-f]{64}$'",
            name=op.f("ck_corpus_import_run_valid_run_key"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_corpus_import_run_valid_status"),
        ),
        sa.CheckConstraint(
            "(status = 'running' AND finished_at IS NULL) OR "
            "(status <> 'running' AND finished_at IS NOT NULL)",
            name=op.f("ck_corpus_import_run_finish_matches_status"),
        ),
        sa.CheckConstraint(
            "manifest_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_corpus_import_run_valid_manifest"),
        ),
        sa.CheckConstraint(
            "code_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_corpus_import_run_valid_code")
        ),
        sa.CheckConstraint(
            "parameters_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_corpus_import_run_valid_parameters"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(parameters) = 'object'",
            name=op.f("ck_corpus_import_run_parameters_is_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(terminal_counts) = 'object'",
            name=op.f("ck_corpus_import_run_counts_is_object"),
        ),
        sa.ForeignKeyConstraint(
            ["release_id"],
            ["corpus_release.id"],
            name=op.f("fk_corpus_import_run_release_id_corpus_release"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_corpus_import_run")),
        sa.UniqueConstraint("id", "release_id", name="uq_corpus_import_run_release_id"),
        sa.UniqueConstraint("run_key", name=op.f("uq_corpus_import_run_run_key")),
    )

    op.create_table(
        "corpus_import_ledger",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("release_id", sa.BigInteger(), nullable=False),
        sa.Column("manifest_row", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=True),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "manifest_row > 0", name=op.f("ck_corpus_import_ledger_positive_manifest_row")
        ),
        sa.CheckConstraint(
            "outcome IN ('imported', 'reused', 'rejected', 'failed')",
            name=op.f("ck_corpus_import_ledger_valid_outcome"),
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_corpus_import_ledger_valid_source_sha256"),
        ),
        sa.CheckConstraint(
            "chunk_count >= 0", name=op.f("ck_corpus_import_ledger_nonnegative_chunk_count")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(details) = 'object'",
            name=op.f("ck_corpus_import_ledger_details_is_object"),
        ),
        sa.ForeignKeyConstraint(
            ["release_id", "document_id"],
            ["corpus_document_membership.release_id", "corpus_document_membership.document_id"],
            name="fk_corpus_ledger_document_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "release_id"],
            ["corpus_import_run.id", "corpus_import_run.release_id"],
            name="fk_corpus_ledger_run_same_release",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_corpus_import_ledger")),
        sa.UniqueConstraint("run_id", "manifest_row", name="uq_corpus_ledger_run_row"),
    )

    op.create_table(
        "corpus_validation_receipt",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("receipt_key", sa.String(length=255), nullable=False),
        sa.Column("release_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("trusted", sa.Boolean(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("policy_graph_sha256", sa.String(length=64), nullable=False),
        sa.Column("rebuild_sha256", sa.String(length=64), nullable=False),
        sa.Column("benchmark_sha256", sa.String(length=64), nullable=False),
        sa.Column("receipt_sha256", sa.String(length=64), nullable=False),
        sa.Column("validation_report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "receipt_key ~ '^corpus-receipt:sha256:[0-9a-f]{64}$'",
            name=op.f("ck_corpus_validation_receipt_valid_receipt_key"),
        ),
        sa.CheckConstraint(
            "status IN ('passed', 'failed')",
            name=op.f("ck_corpus_validation_receipt_valid_status"),
        ),
        sa.CheckConstraint(
            "NOT trusted OR status = 'passed'",
            name=op.f("ck_corpus_validation_receipt_trusted_receipt_must_pass"),
        ),
        sa.CheckConstraint(
            "manifest_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_corpus_validation_receipt_valid_manifest"),
        ),
        sa.CheckConstraint(
            "policy_graph_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_corpus_validation_receipt_valid_policy_graph"),
        ),
        sa.CheckConstraint(
            "rebuild_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_corpus_validation_receipt_valid_rebuild"),
        ),
        sa.CheckConstraint(
            "benchmark_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_corpus_validation_receipt_valid_benchmark"),
        ),
        sa.CheckConstraint(
            "receipt_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_corpus_validation_receipt_valid_receipt"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(validation_report) = 'object'",
            name=op.f("ck_corpus_validation_receipt_report_is_object"),
        ),
        sa.ForeignKeyConstraint(
            ["release_id"],
            ["corpus_release.id"],
            name=op.f("fk_corpus_validation_receipt_release_id_corpus_release"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_corpus_validation_receipt")),
        sa.UniqueConstraint("receipt_key", name=op.f("uq_corpus_validation_receipt_receipt_key")),
    )
    op.create_index(
        "uq_corpus_validation_receipt_passing_release",
        "corpus_validation_receipt",
        ["release_id"],
        unique=True,
        postgresql_where=sa.text("status = 'passed' AND trusted"),
    )

    op.execute(_CORPUS_LIFECYCLE_SQL)
    op.execute(_IMMUTABILITY_SQL)
    op.execute(
        """
        CREATE TRIGGER trg_corpus_release_lifecycle
        BEFORE INSERT OR UPDATE OR DELETE ON corpus_release
        FOR EACH ROW EXECUTE FUNCTION eve_guard_corpus_release_lifecycle()
        """
    )
    for table_name in (
        "literature_policy",
        "embedding_model",
        "document",
        "corpus_validation_receipt",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION eve_guard_immutable_literature_row()
            """
        )
    for table_name in (
        "corpus_document_membership",
        "document_chunk",
        "document_embedding",
        "document_anchor",
        "corpus_import_run",
        "corpus_import_ledger",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_published_guard
            BEFORE INSERT OR UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION eve_guard_published_corpus_child()
            """
        )


def downgrade() -> None:
    """Remove only M3 literature objects; retain a pre-existing vector extension."""

    op.drop_table("corpus_validation_receipt")
    op.drop_table("corpus_import_ledger")
    op.drop_table("corpus_import_run")
    op.drop_index("ix_document_anchor_release_type", table_name="document_anchor")
    op.drop_table("document_anchor")
    op.drop_index("ix_document_embedding_hnsw_cosine", table_name="document_embedding")
    op.drop_table("document_embedding")
    op.drop_index("ix_document_chunk_release_document", table_name="document_chunk")
    op.drop_index("ix_document_chunk_fts_document_gin", table_name="document_chunk")
    op.drop_table("document_chunk")
    op.drop_table("corpus_document_membership")
    op.drop_table("document")
    op.drop_index(op.f("ix_corpus_release_status"), table_name="corpus_release")
    op.drop_table("corpus_release")
    op.drop_table("embedding_model")
    op.drop_table("literature_policy")
    op.execute("DROP FUNCTION IF EXISTS eve_guard_published_corpus_child()")
    op.execute("DROP FUNCTION IF EXISTS eve_guard_immutable_literature_row()")
    op.execute("DROP FUNCTION IF EXISTS eve_guard_corpus_release_lifecycle()")
