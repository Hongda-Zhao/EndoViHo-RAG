#!/usr/bin/env python3
"""Build canonical experiment manifests from one checksum-bound provisioning receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from eve_relation_rag.experiments.embedding_ablation.contracts import (
    ArtifactFileRecord,
    ModelArtifactManifest,
    ModelRepresentationContract,
)
from eve_relation_rag.experiments.embedding_ablation.model_adapters import (
    MEDCPT_ARTICLE_MODEL_ID,
    MEDCPT_CROSS_ENCODER_MODEL_ID,
    MEDCPT_QUERY_MODEL_ID,
    QWEN3_EMBEDDING_MODEL_ID,
    QWEN3_RERANKER_MODEL_ID,
    QWEN3_RETRIEVAL_INSTRUCTION,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256

_RUNTIME_KEY = "runtime:transformers-5.16.1:torch-2.13.0:cpu-eager-v1"


@dataclass(frozen=True, slots=True)
class ManifestPolicy:
    key: str
    model_id: str
    model_key: str
    representation: ModelRepresentationContract


POLICIES = (
    ManifestPolicy(
        key="medcpt_query",
        model_id=MEDCPT_QUERY_MODEL_ID,
        model_key=(
            "embedding:hf:ncbi-MedCPT-Query-Encoder@"
            "d83a36cc6b8e3a5c5e9d9d6ba156808c1643dcbc:cls768-none-dot-v1"
        ),
        representation=ModelRepresentationContract(
            task_kind="embedding",
            dimension=768,
            pooling="cls",
            normalization="none",
            similarity="dot_product",
            query_format="{query}",
            passage_format="unsupported-for-indexing",
            max_sequence_length=64,
            truncation_policy="truncate_tail",
            truncation_side="right",
            output_dtype="float32",
        ),
    ),
    ManifestPolicy(
        key="medcpt_article",
        model_id=MEDCPT_ARTICLE_MODEL_ID,
        model_key=(
            "embedding:hf:ncbi-MedCPT-Article-Encoder@"
            "d05a736da4bb84ee4057b7f7999485be6ed85465:cls768-none-dot-title-chunk-v1"
        ),
        representation=ModelRepresentationContract(
            task_kind="embedding",
            dimension=768,
            pooling="cls",
            normalization="none",
            similarity="dot_product",
            query_format="unsupported-for-querying",
            passage_format=(
                'canonical-json:{"text":"{chunk.text}","title":"{document.title}"};'
                "tokenizer_pair(title,text)"
            ),
            max_sequence_length=512,
            truncation_policy="truncate_tail",
            truncation_side="right",
            output_dtype="float32",
        ),
    ),
    ManifestPolicy(
        key="medcpt_cross_encoder",
        model_id=MEDCPT_CROSS_ENCODER_MODEL_ID,
        model_key=(
            "reranker:hf:ncbi-MedCPT-Cross-Encoder@"
            "71caf65d4927987813984f54c284405a13fcca49:raw-logit-v1"
        ),
        representation=ModelRepresentationContract(
            task_kind="reranker",
            dimension=None,
            pooling="sequence_classification_logit",
            normalization="none",
            similarity="not_applicable",
            query_format="tokenizer_pair(query,passage):first-sequence",
            passage_format="tokenizer_pair(query,passage):second-sequence",
            max_sequence_length=512,
            truncation_policy="truncate_tail",
            truncation_side="right",
            output_dtype="float32",
        ),
    ),
    ManifestPolicy(
        key="qwen3_embedding_0_6b",
        model_id=QWEN3_EMBEDDING_MODEL_ID,
        model_key=(
            "embedding:hf:Qwen-Qwen3-Embedding-0.6B@"
            "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3:lasttoken-mrl384-l2-v1"
        ),
        representation=ModelRepresentationContract(
            task_kind="embedding",
            dimension=384,
            pooling="last_token_then_mrl_prefix_384",
            normalization="l2",
            similarity="cosine",
            query_format=f"Instruct: {QWEN3_RETRIEVAL_INSTRUCTION}\\nQuery:{{query}}",
            passage_format="{chunk.text}",
            max_sequence_length=512,
            truncation_policy="truncate_tail",
            truncation_side="right",
            output_dtype="float32",
        ),
    ),
    ManifestPolicy(
        key="qwen3_reranker_0_6b",
        model_id=QWEN3_RERANKER_MODEL_ID,
        model_key=(
            "reranker:hf:Qwen-Qwen3-Reranker-0.6B@"
            "e61197ed45024b0ed8a2d74b80b4d909f1255473:yes-prob-v1"
        ),
        representation=ModelRepresentationContract(
            task_kind="reranker",
            dimension=None,
            pooling="causal_lm_yes_probability",
            normalization="none",
            similarity="not_applicable",
            query_format=(
                f"<Instruct>: {QWEN3_RETRIEVAL_INSTRUCTION}\\n<Query>: {{query}}"
            ),
            passage_format="<Document>: {passage};official-system-and-assistant-suffix-v1",
            max_sequence_length=512,
            truncation_policy="truncate_tail",
            truncation_side="right",
            output_dtype="float32",
        ),
    ),
)


def build_manifests(receipt_path: Path, model_root: Path, output_directory: Path) -> Path:
    """Build five canonical manifests without loading models or changing their directories."""

    if output_directory.exists() or output_directory.is_symlink():
        raise RuntimeError("model manifest output directory already exists")
    receipt_bytes = receipt_path.read_bytes()
    receipt_file_sha256 = hashlib.sha256(receipt_bytes).hexdigest()
    receipt = json.loads(receipt_bytes)
    if not isinstance(receipt, dict):
        raise RuntimeError("provisioning receipt must be a JSON object")
    receipt_payload = dict(receipt)
    observed_receipt_sha256 = receipt_payload.pop("provisioning_receipt_sha256", None)
    if (
        receipt.get("provisioning_schema_version")
        != "embedding-ablation-provisioning-v1"
        or receipt.get("runtime_network_policy") != "offline-required"
        or observed_receipt_sha256 != canonical_json_sha256(receipt_payload)
    ):
        raise RuntimeError("provisioning receipt identity or checksum is invalid")
    model_rows = receipt.get("models")
    if not isinstance(model_rows, list):
        raise RuntimeError("provisioning receipt has no model rows")
    by_key = {
        str(row.get("key")): row for row in model_rows if isinstance(row, dict)
    }
    if set(by_key) != {policy.key for policy in POLICIES}:
        raise RuntimeError("provisioning receipt model set is not exact")

    parent = output_directory.parent.resolve(strict=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_directory.name}.", dir=parent))
    try:
        records: list[dict[str, object]] = []
        for policy in POLICIES:
            row = by_key[policy.key]
            relative_directory = row.get("relative_directory")
            files = row.get("files")
            if (
                row.get("model_id") != policy.model_id
                or row.get("license_review_status") != "approved"
                or not isinstance(relative_directory, str)
                or not isinstance(files, list)
            ):
                raise RuntimeError(f"provisioned identity is invalid for {policy.model_id}")
            model_directory = (model_root / relative_directory).resolve(strict=True)
            if not model_directory.is_relative_to(model_root.resolve(strict=True)):
                raise RuntimeError("provisioned model path escapes the model root")
            file_records = tuple(ArtifactFileRecord.model_validate(item) for item in files)
            manifest = ModelArtifactManifest(
                manifest_schema_version="embedding-ablation-model-artifact-v1",
                model_key=policy.model_key,
                model_id=policy.model_id,
                exact_revision=str(row.get("exact_revision")),
                license=str(row.get("license")),
                license_review_status="approved",
                representation=policy.representation,
                runtime_key=_RUNTIME_KEY,
                local_files_only=True,
                trust_remote_code=False,
                files=file_records,
            )
            manifest_bytes = canonical_json_bytes(manifest)
            manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
            manifest_name = f"{policy.key}.artifact-manifest.json"
            (temporary / manifest_name).write_bytes(manifest_bytes)
            records.append(
                {
                    "artifact_manifest_sha256": manifest_sha256,
                    "exact_revision": manifest.exact_revision,
                    "manifest_file": manifest_name,
                    "model_directory": model_directory.relative_to(
                        model_root.parent.resolve(strict=True)
                    ).as_posix(),
                    "model_id": manifest.model_id,
                    "model_key": manifest.model_key,
                    "model_size_bytes": int(row.get("model_size_bytes", -1)),
                }
            )
        approval_payload: dict[str, object] = {
            "approval_schema_version": "embedding-ablation-model-manifest-approval-v1",
            "provisioning_receipt_file_sha256": receipt_file_sha256,
            "provisioning_receipt_sha256": observed_receipt_sha256,
            "runtime_network_policy": "offline-required",
            "models": records,
        }
        approval = {
            **approval_payload,
            "approval_manifest_sha256": canonical_json_sha256(approval_payload),
        }
        (temporary / "approved_model_artifacts.json").write_bytes(
            canonical_json_bytes(approval) + b"\n"
        )
        os.rename(temporary, output_directory)
    except Exception:
        for path in sorted(temporary.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        temporary.rmdir()
        raise
    return output_directory / "approved_model_artifacts.json"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    return parser


def main() -> int:
    arguments = _build_parser().parse_args()
    output = build_manifests(
        arguments.receipt,
        arguments.model_root,
        arguments.output_directory,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
