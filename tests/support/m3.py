"""Test-only builders for internally consistent Milestone 3 receipt evidence."""

from __future__ import annotations

from typing import Any

from eve_relation_rag.literature.benchmarking import (
    BenchmarkQuestion,
    BenchmarkQuestionResult,
    BenchmarkReport,
    BenchmarkRuntimeFingerprint,
    build_benchmark_definition,
)
from eve_relation_rag.literature.contracts import (
    EMBEDDING_MODEL_KEY,
    LiteratureRetrievalRequest,
)
from eve_relation_rag.literature.hashing import canonical_json_sha256, canonical_query_sha256
from eve_relation_rag.literature.receipt_integrity import (
    TrustedReceiptEvidence,
    receipt_identity,
)
from eve_relation_rag.literature.validation import RebuildValidationReport


def build_trusted_receipt_fixture(
    *,
    corpus_release_key: str,
    manifest_sha256: str,
    policy_graph_sha256: str,
    model_artifact_manifest_sha256: str,
    document_count: int,
    chunk_count: int,
    embedding_count: int,
    anchor_count: int,
    relevant_chunk_key: str,
    seed: str,
) -> dict[str, Any]:
    """Build self-validating synthetic evidence; never use this outside tests."""

    question = BenchmarkQuestion(
        question_key=f"benchmark:fixture:{seed}",
        question="Which synthetic fixture chunk is relevant?",
        relevant_chunk_keys=(relevant_chunk_key,),
    )
    definition = build_benchmark_definition(
        tier="pilot_release",
        corpus_release_key=corpus_release_key,
        corpus_manifest_sha256=manifest_sha256,
        questions=(question,),
    )
    request = LiteratureRetrievalRequest(
        request_schema_version="literature-retrieval-request-v1",
        corpus_release_key=corpus_release_key,
        question=question.question,
        top_k=10,
    )
    question_result = BenchmarkQuestionResult(
        question_key=question.question_key,
        query_sha256=canonical_query_sha256(request, ()),
        status="ok",
        error_code=None,
        returned_chunk_keys=(relevant_chunk_key,),
        recall_at_5="1.000000000000",
        recall_at_10="1.000000000000",
        citation_ids_valid=True,
        locators_valid=True,
    )
    runtime = BenchmarkRuntimeFingerprint(
        python_version="3.12.test",
        platform_system="test",
        platform_release="test",
        platform_machine="test",
        uv_lock_sha256=canonical_json_sha256({"seed": seed, "kind": "uv-lock"}),
        postgresql_version="PostgreSQL 16 test",
        pgvector_version="0.8.test",
    )
    benchmark_payload: dict[str, Any] = {
        "report_schema_version": "literature-benchmark-report-v1",
        "tier": "pilot_release",
        "corpus_release_key": corpus_release_key,
        "corpus_manifest_sha256": manifest_sha256,
        "retrieval_policy_key": definition.retrieval_policy_key,
        "embedding_model_key": definition.embedding_model_key,
        "gold_sha256": definition.gold_sha256,
        "question_count": 1,
        "benchmark_manifest_sha256": definition.benchmark_manifest_sha256,
        "runtime_fingerprint": runtime,
        "passed": True,
        "recall_at_5": "1.000000000000",
        "recall_at_10": "1.000000000000",
        "citation_id_validity": "1.000000000000",
        "locator_validity": "1.000000000000",
        "question_results": (question_result,),
    }
    benchmark = BenchmarkReport(
        **benchmark_payload,
        benchmark_sha256=canonical_json_sha256(benchmark_payload),
    )
    rebuild_payload: dict[str, Any] = {
        "validation_schema_version": "corpus-rebuild-validation-v2",
        "corpus_release_key": corpus_release_key,
        "manifest_sha256": manifest_sha256,
        "policy_graph_sha256": policy_graph_sha256,
        "embedding_model_key": EMBEDDING_MODEL_KEY,
        "model_artifact_manifest_sha256": model_artifact_manifest_sha256,
        "anchor_manifest_sha256": canonical_json_sha256(
            {"seed": seed, "kind": "anchor-manifest"}
        ),
        "provider_kind": "local_bge",
        "passed": True,
        "findings": (),
        "document_count": document_count,
        "chunk_count": chunk_count,
        "embedding_count": embedding_count,
        "anchor_count": anchor_count,
        "document_keys_sha256": canonical_json_sha256(
            {"seed": seed, "kind": "document-keys"}
        ),
        "document_rebuild_sha256": canonical_json_sha256(
            {"seed": seed, "kind": "documents"}
        ),
        "chunk_rebuild_sha256": canonical_json_sha256(
            {"seed": seed, "kind": "chunks"}
        ),
        "embedding_rebuild_sha256": canonical_json_sha256(
            {"seed": seed, "kind": "embeddings"}
        ),
        "anchor_rebuild_sha256": canonical_json_sha256(
            {"seed": seed, "kind": "anchors"}
        ),
    }
    rebuild = RebuildValidationReport(
        **rebuild_payload,
        rebuild_sha256=canonical_json_sha256(rebuild_payload),
    )
    evidence = TrustedReceiptEvidence(
        receipt_evidence_schema_version="corpus-validation-evidence-v1",
        anchor_manifest_sha256=rebuild.anchor_manifest_sha256,
        benchmark_definition=definition,
        benchmark_report=benchmark,
        rebuild_report=rebuild,
        validator_code_sha256=canonical_json_sha256(
            {"seed": seed, "kind": "validator"}
        ),
    )
    receipt_key, receipt_sha256 = receipt_identity(evidence)
    return {
        "receipt_key": receipt_key,
        "status": "passed",
        "trusted": True,
        "manifest_sha256": manifest_sha256,
        "policy_graph_sha256": policy_graph_sha256,
        "rebuild_sha256": rebuild.rebuild_sha256,
        "benchmark_sha256": benchmark.benchmark_sha256,
        "receipt_sha256": receipt_sha256,
        "validation_report": evidence.model_dump(mode="json"),
    }
