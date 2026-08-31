"""Fail-closed V0 repository and release-asset preflight checks.

Repository validation also loads the installed project's strict Pydantic activation contracts.
Asset-only commands retain a standard-library-only import path.  The checks run from the exact
release checkout before the protected publication job and again inside that job before any
external mutation.
"""

from __future__ import annotations

import argparse
import email
import hashlib
import json
import re
import subprocess
import tarfile
import tomllib
import zipfile
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

PRODUCT_VERSION = "V0"
PACKAGE_VERSION = "0.1.0"
RELEASE_TAG = "v0.1.0"
OCI_IMAGE = "ghcr.io/hongda-zhao/endoviho-rag:v0.1.0"

_SHA256 = re.compile(r"[0-9a-f]{64}")
_RELEASE_HEADING = re.compile(r"(?m)^## \[0\.1\.0\] - \d{4}-\d{2}-\d{2}\s*$")
_EXTERNAL_PUBLICATION_GATES = frozenset(
    {"PUB-GIT-TAG", "PUB-GITHUB-RELEASE", "PUB-PYPI-OR-REGISTRY"}
)
_REQUIRED_ACTIVATION_GATES = frozenset(
    {
        "V0-LOCUS-VERSION-COORDINATES",
        "V0-LINEAGE-SCHEME-SNAPSHOT",
        "V0-THREE-REAL-ROUTES",
        "V0-PUBLISHED-STRUCTURED-RELEASE",
        "V0-REAL-BINDING-ANCHORS",
        "V0-PRODUCTION-GENERATION",
        "V0-HUMAN-SEMANTIC-BENCHMARK",
        "V0-BENCHMARK-THRESHOLDS",
        "V0-FROZEN-INPUT-REBUILD",
    }
)
_REQUIRED_LOCAL_VERIFICATION_GATES = frozenset(
    {
        "full_pytest_passed",
        "frozen_benchmark_pytest_passed",
        "ruff_passed",
        "mypy_passed",
        "lock_check_passed",
        "alembic_check_passed",
        "clean_history_replay_passed",
        "package_build_passed",
        "container_smoke_passed",
        "container_demo_api_wiring_passed",
        "container_cleanup_passed",
    }
)
_FORBIDDEN_PARTS = frozenset({".artifacts", ".git", ".tools", ".venv", "__pycache__", "htmlcov"})
_FORBIDDEN_SUFFIXES = frozenset(
    {
        ".bam",
        ".bai",
        ".cif",
        ".db",
        ".fa",
        ".fasta",
        ".fastq",
        ".fna",
        ".gb",
        ".gbff",
        ".gguf",
        ".h5",
        ".hdf5",
        ".onnx",
        ".parquet",
        ".pkl",
        ".safetensors",
        ".sqlite",
        ".xls",
        ".xlsx",
    }
)
_MAX_TRACKED_FILE_BYTES = 20 * 1024 * 1024
_PRIVATE_KEY_MARKERS = (
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
)


class PreflightError(RuntimeError):
    """A release invariant did not hold."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PreflightError(message)


def _read_json_object(path: Path) -> dict[str, Any]:
    _require(path.is_file() and not path.is_symlink(), f"missing regular file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PreflightError(f"invalid JSON file: {path}") from exc
    _require(isinstance(value, dict), f"JSON root must be an object: {path}")
    return value


def canonical_json_sha256(payload: dict[str, Any], checksum_field: str) -> str:
    body = dict(payload)
    _require(checksum_field in body, f"missing checksum field: {checksum_field}")
    del body[checksum_field]
    encoded = json.dumps(
        body,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_self_hash(payload: dict[str, Any], field: str, label: str) -> None:
    observed = payload.get(field)
    _require(
        isinstance(observed, str) and _SHA256.fullmatch(observed) is not None,
        f"{label} has an invalid {field}",
    )
    _require(
        observed == canonical_json_sha256(payload, field),
        f"{label} has a stale {field}",
    )


def _yaml_scalar(text: str, key: str) -> str | None:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*([^#\r\n]+?)\s*$", text)
    if match is None:
        return None
    return match.group(1).strip().strip("\"'")


def _validate_activation_state(root: Path, expected_commit: str | None) -> Any:
    """Import the installed typed validator only for repository publication checks."""

    try:
        from eve_relation_rag.activation.release_state import validate_v0_activation_state

        return validate_v0_activation_state(root, publication_commit=expected_commit)
    except Exception as exc:
        raise PreflightError(f"typed V0 activation state is unavailable or invalid: {exc}") from exc


def _validate_benchmark(root: Path, activation: Any) -> tuple[str, frozenset[str]]:
    path = root / "benchmark" / "v0_benchmark_report.json"
    payload = _read_json_object(path)
    _validate_self_hash(payload, "report_sha256", "benchmark report")
    _require(
        payload.get("benchmark_report_schema_version") == "v0-benchmark-report-v1",
        "unexpected benchmark report schema",
    )
    _require(payload.get("product_version") == PRODUCT_VERSION, "benchmark product version drifted")
    _require(
        payload.get("package_version") == PACKAGE_VERSION,
        "benchmark package version is not 0.1.0",
    )
    _require(payload.get("engineering_benchmarks_passed") is True, "engineering benchmarks failed")
    _require(
        payload.get("real_hybrid_activation_qualified") is True,
        "real hybrid activation is not qualified",
    )

    suites = payload.get("suites")
    _require(isinstance(suites, list) and suites, "benchmark suites are missing")
    seen_suite_keys: set[str] = set()
    suite_by_key: dict[str, dict[str, Any]] = {}
    for suite in suites:
        _require(isinstance(suite, dict), "benchmark suite must be an object")
        suite_key = suite.get("suite_key")
        _require(isinstance(suite_key, str) and suite_key, "benchmark suite key is missing")
        _require(suite_key not in seen_suite_keys, f"duplicate benchmark suite: {suite_key}")
        seen_suite_keys.add(suite_key)
        _require(suite.get("result") == "passed", f"benchmark suite did not pass: {suite_key}")
        suite_by_key[suite_key] = suite

    structured_suite = suite_by_key.get("v0-real-structured")
    _require(isinstance(structured_suite, dict), "typed real structured suite is missing")
    _require(
        structured_suite.get("tier") == "approved_real_structured"
        and structured_suite.get("case_count") == 10
        and structured_suite.get("benchmark_report_sha256")
        == activation.structured_benchmark_report_sha256,
        "real structured suite is not bound to its typed ten-case report",
    )
    hybrid_suite = suite_by_key.get("v0-real-hybrid")
    _require(isinstance(hybrid_suite, dict), "typed real hybrid suite is missing")
    _require(
        hybrid_suite.get("tier") == "approved_real_hybrid"
        and hybrid_suite.get("case_count") == 10
        and hybrid_suite.get("benchmark_report_sha256") == activation.hybrid_benchmark_report_sha256
        and hybrid_suite.get("human_review_evaluation_sha256")
        == activation.human_evaluation_sha256,
        "real hybrid suite is not bound to its reviewed typed ten-case report",
    )

    review = payload.get("human_semantic_support_review")
    _require(isinstance(review, dict), "human semantic-support review is missing")
    _require(
        frozenset(review)
        == frozenset(
            {
                "status",
                "approved",
                "blocking",
                "reviewed_claim_count",
                "reviewer_key",
                "reviewer_name",
                "reviewed_at",
                "packet_sha256",
                "submission_sha256",
                "evaluation_sha256",
            }
        ),
        "human semantic-support review identity fields drifted",
    )
    _require(
        review
        == {
            "status": "approved",
            "approved": True,
            "blocking": False,
            "reviewed_claim_count": activation.reviewed_claim_count,
            "reviewer_key": activation.reviewer_key,
            "reviewer_name": activation.reviewer_name,
            "reviewed_at": activation.reviewed_at,
            "packet_sha256": activation.human_packet_sha256,
            "submission_sha256": activation.human_submission_sha256,
            "evaluation_sha256": activation.human_evaluation_sha256,
        },
        "human semantic-support summary is not the typed named-reviewer evaluation",
    )

    verification = payload.get("local_verification")
    _require(isinstance(verification, dict), "local verification record is missing")
    _require(verification.get("status") == "passed", "local verification did not pass")
    missing_boolean_gates = sorted(
        key for key in _REQUIRED_LOCAL_VERIFICATION_GATES if verification.get(key) is not True
    )
    _require(
        not missing_boolean_gates,
        f"local verification gates are absent or failed: {missing_boolean_gates}",
    )

    source_artifacts = payload.get("source_artifacts")
    _require(
        isinstance(source_artifacts, list) and source_artifacts,
        "benchmark source_artifacts must be a non-empty list",
    )
    seen_paths: set[str] = set()
    observed_sources: dict[str, str] = {}
    for artifact in source_artifacts:
        _require(isinstance(artifact, dict), "benchmark source artifact must be an object")
        _require(
            frozenset(artifact)
            in {
                frozenset({"path", "file_sha256"}),
                frozenset({"path", "file_sha256", "canonical_payload_sha256"}),
            },
            "benchmark source artifact fields drifted",
        )
        relative = artifact.get("path")
        digest = artifact.get("file_sha256")
        _require(isinstance(relative, str) and relative, "benchmark source path is missing")
        _require(relative not in seen_paths, f"duplicate benchmark source: {relative}")
        seen_paths.add(relative)
        source_path = PurePosixPath(relative)
        _require(
            not source_path.is_absolute() and ".." not in source_path.parts, "unsafe source path"
        )
        local_path = root.joinpath(*source_path.parts)
        _require(
            local_path.is_file() and not local_path.is_symlink(), f"missing source: {relative}"
        )
        _require(
            isinstance(digest, str)
            and digest == hashlib.sha256(local_path.read_bytes()).hexdigest(),
            f"benchmark source digest drifted: {relative}",
        )
        observed_sources[relative] = digest
    expected_activation_sources = dict(activation.source_artifact_sha256s)
    missing_or_drifted = sorted(
        relative
        for relative, digest in expected_activation_sources.items()
        if observed_sources.get(relative) != digest
    )
    _require(
        not missing_or_drifted,
        f"typed activation sources are absent or drifted: {missing_or_drifted}",
    )
    report_sha256 = payload["report_sha256"]
    _require(isinstance(report_sha256, str), "benchmark report checksum is not a string")
    return report_sha256, frozenset(seen_paths)


def _validate_checklist(root: Path, benchmark_sha256: str) -> None:
    path = root / "release" / "v0_release_checklist.json"
    payload = _read_json_object(path)
    _validate_self_hash(payload, "checklist_sha256", "release checklist")
    _require(
        payload.get("release_checklist_schema_version") == "v0-release-checklist-v1",
        "unexpected release checklist schema",
    )
    _require(payload.get("product_version") == PRODUCT_VERSION, "checklist product version drifted")
    _require(
        payload.get("package_version") == PACKAGE_VERSION,
        "checklist package version is not 0.1.0",
    )
    _require(
        payload.get("benchmark_report_sha256") == benchmark_sha256,
        "checklist is not bound to the current benchmark report",
    )
    _require(
        payload.get("milestone_5_engineering_status") == "fulfilled",
        "Milestone 5 engineering status is not fulfilled",
    )
    _require(
        payload.get("software_distribution_status") == "release_candidate",
        "software distribution is not an exact release candidate",
    )
    _require(
        payload.get("v0_definition_of_done_status") == "publication_pending",
        "V0 definition-of-done status is not publication_pending",
    )
    _require(
        payload.get("real_hybrid_activation_qualified") is True,
        "checklist does not qualify real hybrid activation",
    )

    items = payload.get("items")
    _require(isinstance(items, list) and items, "release checklist items are missing")
    by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        _require(isinstance(item, dict), "release checklist item must be an object")
        item_id = item.get("id")
        _require(isinstance(item_id, str) and item_id, "release checklist item id is missing")
        _require(item_id not in by_id, f"duplicate release checklist item: {item_id}")
        evidence = item.get("evidence")
        _require(
            isinstance(evidence, str) and evidence.strip() and evidence.isascii(),
            f"release checklist evidence is invalid: {item_id}",
        )
        by_id[item_id] = item

    missing_activation = sorted(_REQUIRED_ACTIVATION_GATES - by_id.keys())
    _require(not missing_activation, f"required activation gates are absent: {missing_activation}")
    missing_external = sorted(_EXTERNAL_PUBLICATION_GATES - by_id.keys())
    _require(not missing_external, f"external publication gates are absent: {missing_external}")
    for item_id, item in by_id.items():
        if item_id in _EXTERNAL_PUBLICATION_GATES:
            _require(
                item.get("category") == "external_publication" and item.get("status") == "block",
                f"external gate must remain blocked until publication: {item_id}",
            )
        else:
            _require(item.get("status") == "pass", f"release gate did not pass: {item_id}")


def _validate_release_metadata(root: Path) -> None:
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    _require(
        pyproject.get("project", {}).get("version") == PACKAGE_VERSION,
        "package version is not 0.1.0",
    )

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    _require(
        _RELEASE_HEADING.search(changelog) is not None, "CHANGELOG lacks a dated 0.1.0 release"
    )

    citation = (root / "CITATION.cff").read_text(encoding="utf-8")
    _require(_yaml_scalar(citation, "version") == PACKAGE_VERSION, "CITATION version is not 0.1.0")
    released = _yaml_scalar(citation, "date-released")
    _require(
        released is not None and re.fullmatch(r"\d{4}-\d{2}-\d{2}", released) is not None,
        "CITATION date-released is missing or invalid",
    )

    notes = (root / "docs" / "v0_release_notes.md").read_text(encoding="utf-8")
    for required in ("V0", PACKAGE_VERSION, RELEASE_TAG, OCI_IMAGE, "PyPI"):
        _require(required in notes, f"release-note template is missing: {required}")
    _require("TODO" not in notes and "TBD" not in notes, "release-note template has placeholders")


def _git_output(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise PreflightError(f"git {' '.join(arguments)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def tracked_paths(root: Path) -> tuple[Path, ...]:
    raw = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if raw.returncode != 0:
        raise PreflightError("git ls-files failed")
    return tuple(Path(item.decode("utf-8")) for item in raw.stdout.split(b"\0") if item)


def validate_restricted_paths(root: Path, relative_paths: Iterable[Path]) -> None:
    for relative in relative_paths:
        pure = PurePosixPath(relative.as_posix())
        _require(
            not pure.is_absolute() and ".." not in pure.parts, f"unsafe tracked path: {relative}"
        )
        lower_parts = {part.casefold() for part in pure.parts}
        _require(
            not lower_parts.intersection(_FORBIDDEN_PARTS),
            f"restricted path is tracked: {relative}",
        )
        _require(pure.name.casefold() != ".env", "credential-bearing .env is tracked")
        _require(
            pure.suffix.casefold() not in _FORBIDDEN_SUFFIXES,
            f"restricted artifact type is tracked: {relative}",
        )
        path = root.joinpath(*pure.parts)
        _require(
            path.exists() and not path.is_symlink(),
            f"tracked path is missing or symlinked: {relative}",
        )
        if path.is_dir():
            continue
        size = path.stat().st_size
        _require(
            size <= _MAX_TRACKED_FILE_BYTES, f"oversized tracked file requires review: {relative}"
        )
        with path.open("rb") as handle:
            prefix = handle.read(8192)
        _require(b"\0" not in prefix, f"binary bytes are tracked without approval: {relative}")
        _require(
            not any(marker in prefix for marker in _PRIVATE_KEY_MARKERS),
            f"private-key material is tracked: {relative}",
        )
        _require(
            not prefix.startswith(b"SQLite format 3\0"), f"database bytes are tracked: {relative}"
        )
        _require(not prefix.startswith(b"GGUF"), f"model-weight bytes are tracked: {relative}")


def validate_repository(root: Path, expected_commit: str | None = None) -> None:
    root = root.resolve()
    if expected_commit is not None:
        # A Git object ID is 40 hex for this repository; SHA-256 is reserved for artifact digests.
        _require(
            re.fullmatch(r"[0-9a-f]{40}", expected_commit) is not None,
            "release commit must be 40 lowercase hex",
        )
        _require(
            _git_output(root, "rev-parse", "HEAD") == expected_commit,
            "HEAD differs from release commit",
        )
        _require(not _git_output(root, "status", "--porcelain"), "release checkout is dirty")

    activation = _validate_activation_state(root, expected_commit)
    _validate_release_metadata(root)
    benchmark_sha256, benchmark_sources = _validate_benchmark(root, activation)
    _validate_checklist(root, benchmark_sha256)
    tracked = tracked_paths(root)
    tracked_names = {path.as_posix() for path in tracked}
    untracked_benchmark_sources = sorted(benchmark_sources - tracked_names)
    _require(
        not untracked_benchmark_sources,
        f"benchmark evidence is not tracked at the release commit: {untracked_benchmark_sources}",
    )
    untracked_activation_sources = sorted(set(activation.source_artifact_sha256s) - tracked_names)
    _require(
        not untracked_activation_sources,
        f"activation evidence is not tracked at the release commit: {untracked_activation_sources}",
    )
    validate_restricted_paths(root, tracked)


def _member_is_restricted(name: str, *, strip_root: bool) -> bool:
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts:
        return True
    parts = pure.parts[1:] if strip_root and pure.parts else pure.parts
    if not parts:
        return False
    relative = PurePosixPath(*parts)
    lower_parts = {part.casefold() for part in relative.parts}
    return bool(
        lower_parts.intersection(_FORBIDDEN_PARTS)
        or relative.name.casefold() == ".env"
        or relative.suffix.casefold() in _FORBIDDEN_SUFFIXES
    )


def _validate_wheel(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        _require(
            names and not any(_member_is_restricted(name, strip_root=False) for name in names),
            "wheel contains restricted members",
        )
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        _require(len(metadata_names) == 1, "wheel must contain exactly one METADATA file")
        metadata = email.message_from_bytes(archive.read(metadata_names[0]))
    _require(metadata["Name"] == "eve-relation-rag", "wheel distribution name drifted")
    _require(metadata["Version"] == PACKAGE_VERSION, "wheel version is not 0.1.0")


def _validate_sdist(path: Path) -> None:
    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
        _require(members, "sdist is empty")
        _require(
            not any(member.issym() or member.islnk() for member in members),
            "sdist contains links",
        )
        _require(
            not any(_member_is_restricted(member.name, strip_root=True) for member in members),
            "sdist contains restricted members",
        )
        pyprojects = [member for member in members if member.name.endswith("/pyproject.toml")]
        _require(len(pyprojects) == 1, "sdist must contain exactly one pyproject.toml")
        extracted = archive.extractfile(pyprojects[0])
        _require(extracted is not None, "sdist pyproject.toml is unreadable")
        metadata = tomllib.loads(extracted.read().decode("utf-8"))
    _require(metadata.get("project", {}).get("version") == PACKAGE_VERSION, "sdist version drifted")


def _validate_source_archive(path: Path) -> None:
    expected_root = f"EndoViHo-RAG-{PACKAGE_VERSION}"
    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
        _require(members, "source archive is empty")
        roots = {PurePosixPath(member.name).parts[0] for member in members if member.name}
        _require(roots == {expected_root}, "source archive root prefix drifted")
        _require(
            not any(member.issym() or member.islnk() for member in members),
            "source archive contains links",
        )
        _require(
            not any(_member_is_restricted(member.name, strip_root=True) for member in members),
            "source archive contains restricted members",
        )


def _validate_sbom(path: Path) -> None:
    payload = _read_json_object(path)
    _require(payload.get("spdxVersion") == "SPDX-2.3", "SBOM is not SPDX 2.3 JSON")
    namespace = payload.get("documentNamespace")
    _require(isinstance(namespace, str) and namespace, "SBOM document namespace is missing")
    packages = payload.get("packages")
    _require(isinstance(packages, list) and packages, "SBOM package inventory is empty")
    matching = [
        package
        for package in packages
        if isinstance(package, dict)
        and str(package.get("name", "")).replace("_", "-").casefold() == "eve-relation-rag"
        and package.get("versionInfo") == PACKAGE_VERSION
    ]
    _require(matching, "SBOM lacks the exact eve-relation-rag 0.1.0 package")


def _artifact_names() -> dict[str, str]:
    return {
        "wheel": f"eve_relation_rag-{PACKAGE_VERSION}-py3-none-any.whl",
        "sdist": f"eve_relation_rag-{PACKAGE_VERSION}.tar.gz",
        "source": f"eve-relation-rag-{RELEASE_TAG}-source.tar.gz",
        "sbom": f"eve-relation-rag-{RELEASE_TAG}.spdx.json",
        "notes": "RELEASE_NOTES.md",
        "checksums": "SHA256SUMS",
    }


def write_release_notes(root: Path, assets_dir: Path, commit: str) -> Path:
    _require(re.fullmatch(r"[0-9a-f]{40}", commit) is not None, "invalid release commit")
    template = (root / "docs" / "v0_release_notes.md").read_text(encoding="utf-8").rstrip()
    destination = assets_dir / _artifact_names()["notes"]
    destination.write_text(
        f"{template}\n\n## Immutable release identity\n\n"
        f"- Audited commit: `{commit}`\n"
        f"- Annotated tag: `{RELEASE_TAG}`\n"
        f"- OCI tag: `{OCI_IMAGE}` (the immutable digest is attached after publication)\n",
        encoding="utf-8",
    )
    return destination


def write_checksum_manifest(assets_dir: Path) -> Path:
    names = _artifact_names()
    subjects = sorted(names[key] for key in ("wheel", "sdist", "source", "sbom"))
    lines: list[str] = []
    for name in subjects:
        path = assets_dir / name
        _require(path.is_file() and not path.is_symlink(), f"missing release artifact: {name}")
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {name}")
    manifest = assets_dir / names["checksums"]
    manifest.write_text("\n".join(lines) + "\n", encoding="ascii")
    return manifest


def _validate_checksum_manifest(assets_dir: Path) -> None:
    names = _artifact_names()
    expected_names = sorted(names[key] for key in ("wheel", "sdist", "source", "sbom"))
    lines = (assets_dir / names["checksums"]).read_text(encoding="ascii").splitlines()
    _require(len(lines) == len(expected_names), "SHA256SUMS subject set drifted")
    observed_names: list[str] = []
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)", line)
        _require(match is not None, "SHA256SUMS contains an invalid line")
        digest, name = match.groups()
        path = assets_dir / name
        _require(
            path.is_file() and not path.is_symlink(), f"checksummed artifact is missing: {name}"
        )
        _require(
            hashlib.sha256(path.read_bytes()).hexdigest() == digest,
            f"artifact digest drifted: {name}",
        )
        observed_names.append(name)
    _require(observed_names == expected_names, "SHA256SUMS names are incomplete or unsorted")


def validate_assets(assets_dir: Path, expected_commit: str) -> None:
    _require(re.fullmatch(r"[0-9a-f]{40}", expected_commit) is not None, "invalid release commit")
    names = _artifact_names()
    expected = frozenset(names.values())
    observed = frozenset(path.name for path in assets_dir.iterdir())
    _require(observed == expected, f"release asset set drifted: {sorted(observed ^ expected)}")
    for path in assets_dir.iterdir():
        _require(
            path.is_file() and not path.is_symlink(), f"release asset is not regular: {path.name}"
        )

    _validate_wheel(assets_dir / names["wheel"])
    _validate_sdist(assets_dir / names["sdist"])
    _validate_source_archive(assets_dir / names["source"])
    _validate_sbom(assets_dir / names["sbom"])
    _validate_checksum_manifest(assets_dir)

    notes = (assets_dir / names["notes"]).read_text(encoding="utf-8")
    _require(f"`{expected_commit}`" in notes, "release notes do not name the audited commit")
    _require(RELEASE_TAG in notes and OCI_IMAGE in notes, "release notes have version drift")


def prepare_assets(root: Path, assets_dir: Path, commit: str) -> None:
    write_release_notes(root, assets_dir, commit)
    write_checksum_manifest(assets_dir)
    validate_assets(assets_dir, commit)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    repository = subparsers.add_parser("repository", help="validate the exact repository checkout")
    repository.add_argument("--root", type=Path, default=Path("."))
    repository.add_argument("--expected-commit", required=True)

    prepare = subparsers.add_parser("prepare-assets", help="bind and validate built release assets")
    prepare.add_argument("--root", type=Path, default=Path("."))
    prepare.add_argument("--assets-dir", type=Path, required=True)
    prepare.add_argument("--expected-commit", required=True)

    assets = subparsers.add_parser("assets", help="revalidate immutable release assets")
    assets.add_argument("--assets-dir", type=Path, required=True)
    assets.add_argument("--expected-commit", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "repository":
        validate_repository(args.root, args.expected_commit)
    elif args.command == "prepare-assets":
        prepare_assets(args.root.resolve(), args.assets_dir.resolve(), args.expected_commit)
    elif args.command == "assets":
        validate_assets(args.assets_dir.resolve(), args.expected_commit)
    else:  # pragma: no cover - argparse enforces the command set.
        raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
