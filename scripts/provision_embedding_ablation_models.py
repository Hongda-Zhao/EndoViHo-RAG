#!/usr/bin/env python3
"""Provision the five approved, revision-pinned ablation models exactly once."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256


@dataclass(frozen=True, slots=True)
class ApprovedSnapshot:
    key: str
    model_id: str
    revision: str
    license: str
    files: tuple[str, ...]


_MEDCPT_SAFETENSORS_FILES = (
    ".gitattributes",
    "LICENSE",
    "README.md",
    "added_tokens.json",
    "config.json",
    "model.safetensors",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
)
_MEDCPT_CROSS_ENCODER_FILES = (
    ".gitattributes",
    "LICENSE",
    "README.md",
    "added_tokens.json",
    "config.json",
    "pytorch_model.bin",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
)
_QWEN3_EMBEDDING_FILES = (
    ".gitattributes",
    "1_Pooling/config.json",
    "README.md",
    "config.json",
    "config_sentence_transformers.json",
    "generation_config.json",
    "merges.txt",
    "model.safetensors",
    "modules.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
)
_QWEN3_RERANKER_FILES = (
    ".gitattributes",
    "1_LogitScore/config.json",
    "README.md",
    "chat_template.jinja",
    "config.json",
    "config_sentence_transformers.json",
    "generation_config.json",
    "merges.txt",
    "model.safetensors",
    "modules.json",
    "sentence_bert_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
)

APPROVED_SNAPSHOTS = (
    ApprovedSnapshot(
        key="medcpt_query",
        model_id="ncbi/MedCPT-Query-Encoder",
        revision="d83a36cc6b8e3a5c5e9d9d6ba156808c1643dcbc",
        license="public-domain",
        files=_MEDCPT_SAFETENSORS_FILES,
    ),
    ApprovedSnapshot(
        key="medcpt_article",
        model_id="ncbi/MedCPT-Article-Encoder",
        revision="d05a736da4bb84ee4057b7f7999485be6ed85465",
        license="public-domain",
        files=_MEDCPT_SAFETENSORS_FILES,
    ),
    ApprovedSnapshot(
        key="medcpt_cross_encoder",
        model_id="ncbi/MedCPT-Cross-Encoder",
        revision="71caf65d4927987813984f54c284405a13fcca49",
        license="public-domain",
        files=_MEDCPT_CROSS_ENCODER_FILES,
    ),
    ApprovedSnapshot(
        key="qwen3_embedding_0_6b",
        model_id="Qwen/Qwen3-Embedding-0.6B",
        revision="97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        license="apache-2.0",
        files=_QWEN3_EMBEDDING_FILES,
    ),
    ApprovedSnapshot(
        key="qwen3_reranker_0_6b",
        model_id="Qwen/Qwen3-Reranker-0.6B",
        revision="e61197ed45024b0ed8a2d74b80b4d909f1255473",
        license="apache-2.0",
        files=_QWEN3_RERANKER_FILES,
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _copy_verified_snapshot(snapshot: ApprovedSnapshot, source: Path, target: Path) -> None:
    if source.name != snapshot.revision:
        raise RuntimeError(f"resolved snapshot revision mismatch for {snapshot.model_id}")
    shutil.copytree(source, target, symlinks=False)
    actual_files = tuple(
        sorted(path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file())
    )
    if actual_files != tuple(sorted(snapshot.files)):
        raise RuntimeError(f"downloaded file set mismatch for {snapshot.model_id}")
    if any(path.is_symlink() for path in target.rglob("*")):
        raise RuntimeError(f"provisioned model contains a symbolic link: {snapshot.model_id}")


def _file_records(directory: Path) -> list[dict[str, object]]:
    return [
        {
            "relative_path": path.relative_to(directory).as_posix(),
            "byte_size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(item for item in directory.rglob("*") if item.is_file())
    ]


def provision(target_root: Path, cache_directory: Path) -> Path:
    """Download pinned snapshots, copy regular files, and atomically publish a receipt."""

    if target_root.exists() or target_root.is_symlink():
        raise RuntimeError("model target root already exists")
    parent = target_root.parent.resolve(strict=True)
    cache_directory.mkdir(parents=True, exist_ok=True)

    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    from huggingface_hub import __version__ as huggingface_hub_version
    from huggingface_hub import snapshot_download

    temporary = Path(tempfile.mkdtemp(prefix=f".{target_root.name}.", dir=parent))
    try:
        model_records: list[dict[str, object]] = []
        for snapshot in APPROVED_SNAPSHOTS:
            print(f"provisioning {snapshot.model_id}@{snapshot.revision}", flush=True)
            resolved = Path(
                snapshot_download(
                    repo_id=snapshot.model_id,
                    revision=snapshot.revision,
                    cache_dir=cache_directory,
                    allow_patterns=list(snapshot.files),
                    token=False,
                    local_files_only=False,
                    max_workers=4,
                )
            ).resolve(strict=True)
            target = temporary / snapshot.key
            _copy_verified_snapshot(snapshot, resolved, target)
            files = _file_records(target)
            model_records.append(
                {
                    "key": snapshot.key,
                    "model_id": snapshot.model_id,
                    "exact_revision": snapshot.revision,
                    "license": snapshot.license,
                    "license_review_status": "approved",
                    "relative_directory": snapshot.key,
                    "file_count": len(files),
                    "model_size_bytes": sum(int(item["byte_size"]) for item in files),
                    "files": files,
                }
            )

        receipt_payload: dict[str, object] = {
            "provisioning_schema_version": "embedding-ablation-provisioning-v1",
            "provisioned_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "network_scope": "huggingface-model-provisioning-only",
            "runtime_network_policy": "offline-required",
            "huggingface_hub_version": huggingface_hub_version,
            "models": model_records,
        }
        receipt = {
            **receipt_payload,
            "provisioning_receipt_sha256": canonical_json_sha256(receipt_payload),
        }
        (temporary / "provisioning_receipt.json").write_bytes(canonical_json_bytes(receipt) + b"\n")
        os.rename(temporary, target_root)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return target_root / "provisioning_receipt.json"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-root", required=True, type=Path)
    parser.add_argument("--cache-directory", required=True, type=Path)
    return parser


def main() -> int:
    arguments = _build_parser().parse_args()
    receipt = provision(arguments.target_root, arguments.cache_directory)
    print(receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
