#!/usr/bin/env python3
"""Read only explicit local asset roots; emit checksums and unapproved coverage gaps."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from eve_relation_rag.experiments.rag_value_ablation.annotation_workload import (
    build_annotation_workload,
)
from eve_relation_rag.experiments.rag_value_ablation.question_revision import HISTORICAL_PACKAGE
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def inventory(root: Path, manifest_path: Path, rows_key: str) -> dict:
    manifest = json.loads(manifest_path.read_bytes())
    rows = []
    for item in manifest[rows_key]:
        relative = Path(item["relative_path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("asset path must remain within its explicit root")
        path = root / relative
        available = (
            path.is_file()
            and not path.is_symlink()
            and path.resolve().is_relative_to(root.resolve())
        )
        actual = digest(path) if available else None
        expected = item.get("sha256", item.get("source_sha256"))
        rows.append(
            {
                "path": str(path),
                "sha256": actual,
                "expected_sha256": expected,
                "bytes": path.stat().st_size if available else None,
                "status": "verified"
                if available and actual == expected and path.stat().st_size == item["byte_size"]
                else "missing_or_mismatch",
                "declared_license": item.get("declared_license", manifest.get("license_key")),
                "license_review_status": item.get("license_review_status"),
                "retrieval_text_allowed": item.get("retrieval_text_allowed"),
                "document_key": item.get("expected_document_key"),
                "title": item.get("title"),
            }
        )
    expected_paths = {item["relative_path"] for item in manifest[rows_key]}
    extra = sorted(
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and str(p.relative_to(root)) not in expected_paths
    )
    return {
        "manifest_path": str(manifest_path),
        "manifest_file_sha256": digest(manifest_path),
        "model_revision": manifest.get("model_revision", manifest.get("revision")),
        "corpus_release_key": manifest.get("corpus_release_key"),
        "files": rows,
        "unlisted_files": extra,
        "all_declared_files_verified": all(r["status"] == "verified" for r in rows),
        "benchmark_approval": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    plan = build_annotation_workload(ROOT / HISTORICAL_PACKAGE)
    assets = {
        "generation": inventory(
            ROOT / ".artifacts/v0_activation/model/Qwen3-4B-Instruct-2507-4bit",
            ROOT / ".artifacts/v0_activation/manifests/v0_local_model_policy_manifest.v2.json",
            "artifacts",
        ),
        "embedding": inventory(
            ROOT / ".artifacts/milestone3/model/BAAI-bge-small-en-v1.5",
            ROOT / ".artifacts/milestone3/model/bge-small-en-v1.5-artifact-manifest.json",
            "files",
        ),
        "literature": inventory(
            ROOT / ".artifacts/milestone3/corpus-proposal/corpus",
            ROOT / ".artifacts/v0_activation/manifests/v0_corpus_manifest.json",
            "documents",
        ),
    }
    payload = {
        "schema_version": "rag-value-offline-inventory-v1",
        "artifact_kind": "engineering_only",
        "assets": assets,
        "annotation_workload": plan,
        "coverage": [
            {
                "question_id": row["question_id"],
                "family": row["family"],
                "entity_slots": row["entity_slots"],
                "status": "not_yet_bound",
                "dataset_release_key": None,
                "corpus_release_key": None,
                "gold": None,
                "oracle": None,
            }
            for row in plan["questions"]
        ],
        "experiment_releases": {"dataset": None, "corpus": None},
        "missing_is_negative_evidence": False,
        "generation_executed": False,
    }
    with args.output.open("xb") as handle:
        handle.write(
            canonical_json_bytes({**payload, "inventory_sha256": canonical_json_sha256(payload)})
            + b"\n"
        )
    print(json.dumps({key: value["all_declared_files_verified"] for key, value in assets.items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
