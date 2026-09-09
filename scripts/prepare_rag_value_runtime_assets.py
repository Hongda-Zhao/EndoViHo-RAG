#!/usr/bin/env python3
"""Derive experiment runtime paths and hashes from the existing explicit local assets."""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import asdict
from pathlib import Path

from eve_relation_rag.experiments.rag_value_ablation.literature_adapter import (
    BgeAssetFiles,
    verify_bge_assets,
)
from eve_relation_rag.experiments.rag_value_ablation.local_generation import (
    LocalGenerationConfig,
    generation_identity,
    verify_generation_assets,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    model_policy = (
        root / ".artifacts/v0_activation/manifests/v0_local_model_policy_manifest.v2.json"
    )
    generation = LocalGenerationConfig(
        model_root=root / ".artifacts/v0_activation/model/Qwen3-4B-Instruct-2507-4bit",
        model_policy_path=model_policy,
        model_policy_file_sha256=digest(model_policy),
        python_executable=root / ".artifacts/v0_activation/provider-env/bin/python",
        worker_script=root / "scripts/rag_value_mlx_worker.py",
    )
    model = verify_generation_assets(generation)
    bge_python = root / ".artifacts/v0_activation/runtime/local-embeddings-venv/bin/python"
    bge_worker = root / "scripts/rag_value_bge_worker.py"
    bge_manifest = root / ".artifacts/milestone3/model/bge-small-en-v1.5-artifact-manifest.json"
    bge = BgeAssetFiles(
        model_root=str(root / ".artifacts/milestone3/model/BAAI-bge-small-en-v1.5"),
        artifact_manifest_path=str(bge_manifest),
        python_executable=str(bge_python),
        python_executable_sha256=digest(bge_python),
        worker_script=str(bge_worker),
        worker_script_sha256=digest(bge_worker),
    )
    payload = {
        "schema_version": "rag-value-local-runtime-assets-v1",
        "artifact_kind": "engineering_candidate_not_approval",
        "generation_config": {key: str(value) for key, value in asdict(generation).items()},
        "generation_identity": generation_identity(model),
        "bge_asset_files": bge,
        "bge_artifact_manifest_file_sha256": verify_bge_assets(bge, digest(bge_manifest)),
        "scientific_approval": None,
        "generation_executed": False,
        "production_settings_read": False,
    }
    with args.output.open("xb") as handle:
        handle.write(
            canonical_json_bytes(
                {
                    **payload,
                    "manifest_sha256": canonical_json_sha256(payload),
                }
            )
            + b"\n"
        )
    print("Local asset identities prepared; no model execution or scientific approval.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
