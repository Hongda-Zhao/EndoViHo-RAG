"""Canonical evidence and verification for trusted literature validation receipts."""

from __future__ import annotations

import json
from typing import Any, Literal, Self

from pydantic import model_validator

from eve_relation_rag.literature.benchmarking import (
    BenchmarkDefinition,
    BenchmarkReport,
    validate_benchmark_report_against_definition,
)
from eve_relation_rag.literature.contracts import (
    EMBEDDING_MODEL_KEY,
    RETRIEVAL_POLICY_KEY,
    Sha256,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.hashing import canonical_json_sha256, corpus_receipt_key
from eve_relation_rag.literature.validation import RebuildValidationReport


class ReceiptIntegrityError(ValueError):
    """Raised when persisted receipt evidence is incomplete or inconsistent."""


class TrustedReceiptEvidence(StrictFrozenSchema):
    """Complete self-validating evidence stored behind one trusted receipt."""

    receipt_evidence_schema_version: Literal["corpus-validation-evidence-v1"]
    anchor_manifest_sha256: Sha256
    benchmark_definition: BenchmarkDefinition
    benchmark_report: BenchmarkReport
    rebuild_report: RebuildValidationReport
    validator_code_sha256: Sha256

    @model_validator(mode="after")
    def validate_evidence_graph(self) -> Self:
        definition = self.benchmark_definition
        benchmark = self.benchmark_report
        rebuild = self.rebuild_report
        validate_benchmark_report_against_definition(benchmark, definition)
        if (
            definition.tier != "pilot_release"
            or not benchmark.passed
            or benchmark.runtime_fingerprint is None
        ):
            raise ValueError("trusted evidence requires a passing release-grade benchmark")
        if not rebuild.passed or rebuild.provider_kind != "local_bge":
            raise ValueError("trusted evidence requires a passing exact local-BGE rebuild")
        if (
            rebuild.corpus_release_key != definition.corpus_release_key
            or rebuild.manifest_sha256 != definition.corpus_manifest_sha256
            or rebuild.embedding_model_key != definition.embedding_model_key
            or definition.retrieval_policy_key != RETRIEVAL_POLICY_KEY
            or definition.embedding_model_key != EMBEDDING_MODEL_KEY
            or rebuild.anchor_manifest_sha256 != self.anchor_manifest_sha256
        ):
            raise ValueError("receipt evidence identities do not bind one exact release")
        return self


def receipt_payload(evidence: TrustedReceiptEvidence) -> dict[str, object]:
    """Return the complete canonical receipt-key preimage."""

    definition = evidence.benchmark_definition
    benchmark = evidence.benchmark_report
    rebuild = evidence.rebuild_report
    return {
        "receipt_schema_version": "corpus-validation-receipt-v2",
        "anchor_manifest_sha256": evidence.anchor_manifest_sha256,
        "benchmark_manifest_sha256": definition.benchmark_manifest_sha256,
        "benchmark_sha256": benchmark.benchmark_sha256,
        "corpus_release_key": rebuild.corpus_release_key,
        "embedding_model_key": definition.embedding_model_key,
        "gold_sha256": definition.gold_sha256,
        "manifest_sha256": rebuild.manifest_sha256,
        "model_artifact_manifest_sha256": rebuild.model_artifact_manifest_sha256,
        "policy_graph_sha256": rebuild.policy_graph_sha256,
        "provider_kind": rebuild.provider_kind,
        "rebuild_sha256": rebuild.rebuild_sha256,
        "retrieval_policy_key": definition.retrieval_policy_key,
        "validator_code_sha256": evidence.validator_code_sha256,
    }


def receipt_identity(evidence: TrustedReceiptEvidence) -> tuple[str, str]:
    """Derive the immutable receipt key and receipt checksum from validated evidence."""

    payload = receipt_payload(evidence)
    receipt_key = corpus_receipt_key(payload)
    receipt_sha256 = canonical_json_sha256(
        {
            "receipt_key": receipt_key,
            "status": "passed",
            "trusted": True,
            **payload,
        }
    )
    return receipt_key, receipt_sha256


def validate_persisted_receipt(
    *,
    release_corpus_key: str,
    release_manifest_sha256: str,
    release_policy_graph_sha256: str,
    release_embedding_model_key: str,
    release_model_artifact_manifest_sha256: str,
    receipt_key: str,
    receipt_status: str,
    receipt_trusted: bool,
    receipt_manifest_sha256: str,
    receipt_policy_graph_sha256: str,
    receipt_rebuild_sha256: str,
    receipt_benchmark_sha256: str,
    receipt_sha256: str,
    validation_report: dict[str, Any],
) -> TrustedReceiptEvidence:
    """Reload, recompute, and bind persisted evidence to its exact release and DB columns."""

    try:
        evidence = TrustedReceiptEvidence.model_validate_json(
            json.dumps(validation_report, ensure_ascii=False, allow_nan=False)
        )
        expected_key, expected_sha256 = receipt_identity(evidence)
    except Exception as exc:
        raise ReceiptIntegrityError("trusted receipt evidence is invalid") from exc

    definition = evidence.benchmark_definition
    benchmark = evidence.benchmark_report
    rebuild = evidence.rebuild_report
    expected_pairs = (
        (receipt_status, "passed"),
        (receipt_trusted, True),
        (release_corpus_key, rebuild.corpus_release_key),
        (release_manifest_sha256, rebuild.manifest_sha256),
        (release_policy_graph_sha256, rebuild.policy_graph_sha256),
        (release_embedding_model_key, definition.embedding_model_key),
        (
            release_model_artifact_manifest_sha256,
            rebuild.model_artifact_manifest_sha256,
        ),
        (receipt_manifest_sha256, rebuild.manifest_sha256),
        (receipt_policy_graph_sha256, rebuild.policy_graph_sha256),
        (receipt_rebuild_sha256, rebuild.rebuild_sha256),
        (receipt_benchmark_sha256, benchmark.benchmark_sha256),
        (receipt_key, expected_key),
        (receipt_sha256, expected_sha256),
    )
    if any(observed != expected for observed, expected in expected_pairs):
        raise ReceiptIntegrityError("trusted receipt does not match its release or evidence")
    return evidence
