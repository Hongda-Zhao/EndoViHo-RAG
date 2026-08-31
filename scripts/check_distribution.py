"""Inspect built wheel/sdist contents and release metadata without extracting them."""

from __future__ import annotations

import argparse
import email
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

FORBIDDEN_PARTS = {
    ".artifacts",
    ".env",
    ".git",
    ".tools",
    ".venv",
    "benchmark",
    "data",
    "release",
    "tests",
}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".onnx",
    ".parquet",
    ".safetensors",
    ".sqlite",
    ".xls",
    ".xlsx",
}


def _validate_names(names: list[str]) -> None:
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(f"unsafe distribution member: {name}")
        if FORBIDDEN_PARTS.intersection(path.parts) or path.suffix in FORBIDDEN_SUFFIXES:
            raise RuntimeError(f"forbidden distribution member: {name}")


def _check_wheel(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        _validate_names(names)
        if "eve_relation_rag/demo/examples.json" not in names:
            raise RuntimeError("wheel is missing strict Demo examples")
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        license_names = [name for name in names if ".dist-info/licenses/LICENSE" in name]
        if len(metadata_names) != 1 or len(license_names) != 1:
            raise RuntimeError("wheel metadata or MIT license is incomplete")
        metadata = email.message_from_bytes(archive.read(metadata_names[0]))
    if metadata["Name"] != "eve-relation-rag" or metadata["Version"] != "0":
        raise RuntimeError("wheel name/version metadata is incorrect")
    if metadata["License-Expression"] != "MIT" or metadata["Author"] != "Hongda Zhao":
        raise RuntimeError("wheel license/author metadata is incorrect")
    urls = metadata.get_all("Project-URL", [])
    if not any(item.startswith("Repository, ") for item in urls):
        raise RuntimeError("wheel is missing its repository URL")
    description_type = metadata["Description-Content-Type"]
    if (
        description_type is None
        or description_type.split(";", maxsplit=1)[0].strip().casefold() != "text/markdown"
    ):
        raise RuntimeError("wheel is missing its Markdown README metadata")


def _check_sdist(path: Path) -> None:
    with tarfile.open(path, mode="r:gz") as archive:
        names = archive.getnames()
    _validate_names(names)
    stripped = {"/".join(PurePosixPath(name).parts[1:]) for name in names}
    required = {
        ".dockerignore",
        ".env.example",
        ".streamlit/config.toml",
        "alembic.ini",
        "app/streamlit_app.py",
        "CHANGELOG.md",
        "CITATION.cff",
        "compose.yaml",
        "DATA_LICENSE",
        "Dockerfile",
        "LICENSE",
        "migrations/env.py",
        "migrations/script.py.mako",
        "migrations/versions/0001_empty_baseline.py",
        "migrations/versions/0002_milestone_1_truth_layer.py",
        "migrations/versions/0003_milestone_1_assertion_evidence.py",
        "migrations/versions/0004_m1_shared_intervals.py",
        "migrations/versions/0005_m1_fail_closed_publication.py",
        "migrations/versions/0006_m3_literature_retrieval.py",
        "migrations/versions/0007_m3_anchor_release_scope.py",
        "migrations/versions/0008_m3_published_child_reparent_guard.py",
        "migrations/versions/0009_m3_validated_release_freeze.py",
        "migrations/versions/0010_m3_validation_lock_hardening.py",
        "migrations/versions/0011_dataset_validation_receipt.py",
        "migrations/versions/0012_extended_viral_lineage.py",
        "README.md",
        "uv.lock",
    }
    missing = required - stripped
    if missing:
        raise RuntimeError(f"sdist is missing release assets: {sorted(missing)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()
    wheels = sorted(args.dist.glob("*.whl"))
    sdists = sorted(args.dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise RuntimeError("expected exactly one wheel and one sdist")
    _check_wheel(wheels[0])
    _check_sdist(sdists[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
