"""Trusted pilot receipt creation and explicit literature corpus publication."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from eve_relation_rag.db.models import CorpusRelease, CorpusValidationReceipt, EmbeddingModel
from eve_relation_rag.literature.anchors import CorpusAnchorManifest
from eve_relation_rag.literature.benchmarking import (
    BenchmarkDefinition,
    BenchmarkRuntimeFingerprint,
    run_benchmark,
)
from eve_relation_rag.literature.contracts import CorpusManifest, StrictFrozenSchema
from eve_relation_rag.literature.local_bge import LocalBgeProvider
from eve_relation_rag.literature.receipt_integrity import (
    TrustedReceiptEvidence,
    receipt_identity,
    validate_persisted_receipt,
)
from eve_relation_rag.literature.validation import validate_corpus_rebuild

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CorpusPublicationError(RuntimeError):
    """Raised when receipt or publication preconditions are not exact."""


class ReceiptReport(StrictFrozenSchema):
    """Identity of one newly recorded or exactly replayed trusted pilot receipt."""

    receipt_key: str = Field(pattern=r"^corpus-receipt:sha256:[0-9a-f]{64}$")
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    corpus_release_key: str
    replayed: bool


class PublicationReport(StrictFrozenSchema):
    """Stable output of an explicit exact publication transition."""

    corpus_release_key: str
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: str
    published_at: datetime
    replayed: bool


def record_pilot_validation_receipt(
    engine: Engine,
    *,
    manifest: CorpusManifest,
    import_root: Path,
    anchor_manifest: CorpusAnchorManifest,
    benchmark_definition: BenchmarkDefinition,
    runtime_fingerprint: BenchmarkRuntimeFingerprint,
    validator_code_sha256: str,
    provider: LocalBgeProvider,
) -> ReceiptReport:
    """Validate under a release lock, record exact evidence, and freeze the candidate."""

    if type(provider) is not LocalBgeProvider:
        raise CorpusPublicationError("trusted receipt requires the verified LocalBgeProvider")
    if not _SHA256_RE.fullmatch(validator_code_sha256):
        raise CorpusPublicationError("validator_code_sha256 must be lowercase SHA-256")
    if (
        not _SHA256_RE.fullmatch(provider.artifact_manifest_sha256)
        or benchmark_definition.corpus_release_key != manifest.corpus_release_key
        or benchmark_definition.corpus_manifest_sha256 != manifest.manifest_sha256
        or anchor_manifest.corpus_release_key != manifest.corpus_release_key
        or anchor_manifest.corpus_manifest_sha256 != manifest.manifest_sha256
        or anchor_manifest.anchor_policy_key != manifest.anchor_policy_key
    ):
        raise CorpusPublicationError("receipt inputs do not bind one exact approved corpus")

    with Session(engine) as session, session.begin():
        release_row = session.execute(
            select(
                CorpusRelease,
                EmbeddingModel.model_key.label("embedding_model_key"),
                EmbeddingModel.artifact_manifest_sha256.label(
                    "model_artifact_manifest_sha256"
                ),
            )
            .join(EmbeddingModel, EmbeddingModel.id == CorpusRelease.embedding_model_id)
            .where(CorpusRelease.corpus_release_key == manifest.corpus_release_key)
            .with_for_update(of=CorpusRelease)
        ).one_or_none()
        if release_row is None:
            raise CorpusPublicationError("receipt target corpus was not found")
        release = release_row.CorpusRelease
        if release.status not in {"candidate", "validated"}:
            raise CorpusPublicationError(
                "receipt target must be a candidate or an exactly replayed validated corpus"
            )
        if (
            release.corpus_release_key != manifest.corpus_release_key
            or release.manifest_sha256 != manifest.manifest_sha256
            or release_row.embedding_model_key != manifest.embedding_model_key
            or release_row.model_artifact_manifest_sha256
            != provider.artifact_manifest_sha256
        ):
            raise CorpusPublicationError("receipt target release differs from approved inputs")

        # The parent FOR UPDATE lock conflicts with the child-trigger FOR SHARE lock from
        # migration 0010. No membership, chunk, embedding, anchor, import, or receipt row can
        # change until the exact rebuild, benchmark, receipt insert, and validation transition
        # commit together.
        rebuild = validate_corpus_rebuild(
            engine,
            manifest=manifest,
            import_root=import_root,
            tokenizer=provider,
            provider=provider,
            anchor_manifest=anchor_manifest,
        )
        from eve_relation_rag.application.literature import CandidateBenchmarkService

        benchmark = run_benchmark(
            CandidateBenchmarkService(engine, provider, rebuild),
            benchmark_definition,
            runtime_fingerprint=runtime_fingerprint,
        )
        try:
            evidence = TrustedReceiptEvidence(
                receipt_evidence_schema_version="corpus-validation-evidence-v1",
                anchor_manifest_sha256=anchor_manifest.anchor_manifest_sha256,
                benchmark_definition=benchmark_definition,
                benchmark_report=benchmark,
                rebuild_report=rebuild,
                validator_code_sha256=validator_code_sha256,
            )
        except Exception as exc:
            raise CorpusPublicationError("trusted validation evidence did not pass") from exc
        if rebuild.model_artifact_manifest_sha256 != provider.artifact_manifest_sha256:
            raise CorpusPublicationError("rebuild used a different model artifact")
        receipt_key, receipt_sha256 = receipt_identity(evidence)

        existing = session.scalar(
            select(CorpusValidationReceipt).where(
                CorpusValidationReceipt.release_id == release.id,
                CorpusValidationReceipt.status == "passed",
            )
        )
        if existing is not None:
            try:
                validate_persisted_receipt(
                    release_corpus_key=release.corpus_release_key,
                    release_manifest_sha256=release.manifest_sha256,
                    release_policy_graph_sha256=release.policy_graph_sha256,
                    release_embedding_model_key=release_row.embedding_model_key,
                    release_model_artifact_manifest_sha256=(
                        release_row.model_artifact_manifest_sha256
                    ),
                    receipt_key=existing.receipt_key,
                    receipt_status=existing.status,
                    receipt_trusted=existing.trusted,
                    receipt_manifest_sha256=existing.manifest_sha256,
                    receipt_policy_graph_sha256=existing.policy_graph_sha256,
                    receipt_rebuild_sha256=existing.rebuild_sha256,
                    receipt_benchmark_sha256=existing.benchmark_sha256,
                    receipt_sha256=existing.receipt_sha256,
                    validation_report=existing.validation_report,
                )
            except Exception as exc:
                raise CorpusPublicationError("existing passing receipt is invalid") from exc
            if existing.receipt_key != receipt_key or existing.receipt_sha256 != receipt_sha256:
                raise CorpusPublicationError("release already has a different passing receipt")
            if release.status == "candidate":
                release.status = "validated"
                session.flush()
            return ReceiptReport(
                receipt_key=receipt_key,
                receipt_sha256=receipt_sha256,
                corpus_release_key=rebuild.corpus_release_key,
                replayed=True,
            )
        if release.status != "candidate":
            raise CorpusPublicationError("validated corpus is missing its passing receipt")
        session.add(
            CorpusValidationReceipt(
                receipt_key=receipt_key,
                release_id=release.id,
                status="passed",
                trusted=True,
                manifest_sha256=rebuild.manifest_sha256,
                policy_graph_sha256=rebuild.policy_graph_sha256,
                rebuild_sha256=rebuild.rebuild_sha256,
                benchmark_sha256=benchmark.benchmark_sha256,
                receipt_sha256=receipt_sha256,
                validation_report=evidence.model_dump(mode="json"),
            )
        )
        session.flush()
        release.status = "validated"
        session.flush()
    return ReceiptReport(
        receipt_key=receipt_key,
        receipt_sha256=receipt_sha256,
        corpus_release_key=rebuild.corpus_release_key,
        replayed=False,
    )


def publish_corpus(
    engine: Engine,
    *,
    corpus_release_key: str,
    expected_manifest_sha256: str,
    expected_receipt_sha256: str,
) -> PublicationReport:
    """Explicitly transition validated -> published using exact checksums."""

    if not _SHA256_RE.fullmatch(expected_manifest_sha256) or not _SHA256_RE.fullmatch(
        expected_receipt_sha256
    ):
        raise CorpusPublicationError("publication requires exact lowercase SHA-256 values")
    with Session(engine) as session, session.begin():
        release_row = session.execute(
            select(
                CorpusRelease,
                EmbeddingModel.model_key.label("embedding_model_key"),
                EmbeddingModel.artifact_manifest_sha256.label(
                    "model_artifact_manifest_sha256"
                ),
            )
            .join(EmbeddingModel, EmbeddingModel.id == CorpusRelease.embedding_model_id)
            .where(CorpusRelease.corpus_release_key == corpus_release_key)
            .with_for_update(of=CorpusRelease)
        ).one_or_none()
        if release_row is None:
            raise CorpusPublicationError("corpus release was not found")
        release = release_row.CorpusRelease
        if release.manifest_sha256 != expected_manifest_sha256:
            raise CorpusPublicationError("publication manifest checksum mismatch")
        if release.status not in {"validated", "published"}:
            raise CorpusPublicationError("corpus is not in a publishable lifecycle state")
        receipt = session.scalar(
            select(CorpusValidationReceipt).where(
                CorpusValidationReceipt.release_id == release.id,
                CorpusValidationReceipt.status == "passed",
                CorpusValidationReceipt.trusted,
                CorpusValidationReceipt.receipt_sha256 == expected_receipt_sha256,
                CorpusValidationReceipt.manifest_sha256 == release.manifest_sha256,
                CorpusValidationReceipt.policy_graph_sha256 == release.policy_graph_sha256,
            )
        )
        if receipt is None:
            raise CorpusPublicationError("exact trusted passing receipt was not found")
        try:
            validate_persisted_receipt(
                release_corpus_key=release.corpus_release_key,
                release_manifest_sha256=release.manifest_sha256,
                release_policy_graph_sha256=release.policy_graph_sha256,
                release_embedding_model_key=release_row.embedding_model_key,
                release_model_artifact_manifest_sha256=(
                    release_row.model_artifact_manifest_sha256
                ),
                receipt_key=receipt.receipt_key,
                receipt_status=receipt.status,
                receipt_trusted=receipt.trusted,
                receipt_manifest_sha256=receipt.manifest_sha256,
                receipt_policy_graph_sha256=receipt.policy_graph_sha256,
                receipt_rebuild_sha256=receipt.rebuild_sha256,
                receipt_benchmark_sha256=receipt.benchmark_sha256,
                receipt_sha256=receipt.receipt_sha256,
                validation_report=receipt.validation_report,
            )
        except Exception as exc:
            raise CorpusPublicationError("trusted receipt evidence is invalid") from exc
        if release.status == "published" and release.published_at is not None:
            return PublicationReport(
                corpus_release_key=release.corpus_release_key,
                manifest_sha256=release.manifest_sha256,
                receipt_sha256=receipt.receipt_sha256,
                status="published",
                published_at=release.published_at,
                replayed=True,
            )
        if release.status != "validated":
            raise CorpusPublicationError("corpus is not in a publishable lifecycle state")
        release.status = "published"
        release.published_at = datetime.now(UTC)
        session.flush()
        published_at = release.published_at
    if published_at is None:  # pragma: no cover - constrained by database lifecycle checks.
        raise CorpusPublicationError("publication timestamp was not persisted")
    return PublicationReport(
        corpus_release_key=corpus_release_key,
        manifest_sha256=expected_manifest_sha256,
        receipt_sha256=expected_receipt_sha256,
        status="published",
        published_at=published_at,
        replayed=False,
    )
