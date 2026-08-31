from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import scripts.v0_release_preflight as release_preflight
from scripts.check_v0_governance import GovernanceError, validate_governance
from scripts.v0_release_preflight import (
    OCI_IMAGE,
    PACKAGE_VERSION,
    RELEASE_TAG,
    PreflightError,
    canonical_json_sha256,
    validate_assets,
    validate_repository,
    validate_restricted_paths,
    write_checksum_manifest,
    write_release_notes,
)

ROOT = Path(__file__).parents[2]
COMMIT = "a" * 40


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _bind(payload: dict[str, Any], field: str) -> dict[str, Any]:
    payload[field] = "0" * 64
    payload[field] = canonical_json_sha256(payload, field)
    return payload


def _activation_gates() -> list[dict[str, str]]:
    passing = {
        "M5-QUALITY",
        "V0-BENCHMARK-THRESHOLDS",
        "V0-FROZEN-INPUT-REBUILD",
        "V0-HUMAN-SEMANTIC-BENCHMARK",
        "V0-LINEAGE-SCHEME-SNAPSHOT",
        "V0-LOCUS-VERSION-COORDINATES",
        "V0-PRODUCTION-GENERATION",
        "V0-PUBLISHED-STRUCTURED-RELEASE",
        "V0-REAL-BINDING-ANCHORS",
        "V0-THREE-REAL-ROUTES",
    }
    external = {"PUB-GIT-TAG", "PUB-GITHUB-RELEASE", "PUB-PYPI-OR-REGISTRY"}
    items = [
        {"id": item, "category": "v0_definition_of_done", "status": "pass", "evidence": "ok"}
        for item in sorted(passing)
    ]
    items.extend(
        {
            "id": item,
            "category": "external_publication",
            "status": "block",
            "evidence": "created only after final approval",
        }
        for item in sorted(external)
    )
    return items


def _make_release_candidate(root: Path) -> None:
    (root / "docs").mkdir(parents=True)
    (root / "benchmark").mkdir()
    (root / "release").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "eve-relation-rag"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (root / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [0.1.0] - 2026-08-29\n\n- V0.\n", encoding="utf-8"
    )
    (root / "CITATION.cff").write_text(
        'cff-version: 1.2.0\nversion: "0.1.0"\ndate-released: "2026-08-29"\n',
        encoding="utf-8",
    )
    (root / "docs" / "v0_release_notes.md").write_text(
        f"# V0 {PACKAGE_VERSION} {RELEASE_TAG}\n\n{OCI_IMAGE}\n\nNo PyPI publication.\n",
        encoding="utf-8",
    )
    evidence = root / "release" / "typed-activation-evidence.json"
    evidence.write_text("{}\n", encoding="utf-8")
    evidence_sha256 = hashlib.sha256(evidence.read_bytes()).hexdigest()
    benchmark = _bind(
        {
            "benchmark_report_schema_version": "v0-benchmark-report-v1",
            "product_version": "V0",
            "package_version": "0.1.0",
            "engineering_benchmarks_passed": True,
            "real_hybrid_activation_qualified": True,
            "suites": [
                {
                    "suite_key": "v0-real-structured",
                    "tier": "approved_real_structured",
                    "case_count": 10,
                    "result": "passed",
                    "benchmark_report_sha256": "1" * 64,
                },
                {
                    "suite_key": "v0-real-hybrid",
                    "tier": "approved_real_hybrid",
                    "case_count": 10,
                    "result": "passed",
                    "benchmark_report_sha256": "2" * 64,
                    "human_review_evaluation_sha256": "3" * 64,
                }
            ],
            "source_artifacts": [
                {
                    "path": "release/typed-activation-evidence.json",
                    "file_sha256": evidence_sha256,
                }
            ],
            "human_semantic_support_review": {
                "status": "approved",
                "approved": True,
                "blocking": False,
                "reviewed_claim_count": 20,
                "reviewer_key": "reviewer:v0-test",
                "reviewer_name": "V0 Test Reviewer",
                "reviewed_at": "2026-08-29T00:00:00Z",
                "packet_sha256": "4" * 64,
                "submission_sha256": "5" * 64,
                "evaluation_sha256": "3" * 64,
            },
            "local_verification": {
                "status": "passed",
                "full_pytest_passed": True,
                "frozen_benchmark_pytest_passed": True,
                "ruff_passed": True,
                "mypy_passed": True,
                "lock_check_passed": True,
                "alembic_check_passed": True,
                "clean_history_replay_passed": True,
                "package_build_passed": True,
                "container_smoke_passed": True,
                "container_demo_api_wiring_passed": True,
                "container_cleanup_passed": True,
            },
        },
        "report_sha256",
    )
    _write_json(root / "benchmark" / "v0_benchmark_report.json", benchmark)
    checklist = _bind(
        {
            "release_checklist_schema_version": "v0-release-checklist-v1",
            "product_version": "V0",
            "package_version": "0.1.0",
            "benchmark_report_sha256": benchmark["report_sha256"],
            "milestone_5_engineering_status": "fulfilled",
            "software_distribution_status": "release_candidate",
            "v0_definition_of_done_status": "publication_pending",
            "real_hybrid_activation_qualified": True,
            "items": _activation_gates(),
        },
        "checklist_sha256",
    )
    _write_json(root / "release" / "v0_release_checklist.json", checklist)


def _validated_activation_summary(root: Path) -> SimpleNamespace:
    evidence = root / "release" / "typed-activation-evidence.json"
    return SimpleNamespace(
        structured_benchmark_report_sha256="1" * 64,
        hybrid_benchmark_report_sha256="2" * 64,
        human_evaluation_sha256="3" * 64,
        human_packet_sha256="4" * 64,
        human_submission_sha256="5" * 64,
        reviewer_key="reviewer:v0-test",
        reviewer_name="V0 Test Reviewer",
        reviewed_at="2026-08-29T00:00:00Z",
        reviewed_claim_count=20,
        source_artifact_sha256s={
            "release/typed-activation-evidence.json": hashlib.sha256(
                evidence.read_bytes()
            ).hexdigest()
        },
    )


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_exact_release_candidate_repository_passes_and_preview_metadata_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_release_candidate(tmp_path)
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.name", "V0 Test")
    _git(tmp_path, "config", "user.email", "v0@example.invalid")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "release candidate")
    commit = _git(tmp_path, "rev-parse", "HEAD")
    monkeypatch.setattr(
        release_preflight,
        "_validate_activation_state",
        lambda _root, _commit: _validated_activation_summary(tmp_path),
    )

    validate_repository(tmp_path, commit)

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "eve-relation-rag"\nversion = "0"\n', encoding="utf-8"
    )
    with pytest.raises(PreflightError, match="dirty"):
        validate_repository(tmp_path, commit)


def test_repository_rejects_self_reported_activation_without_typed_state(
    tmp_path: Path,
) -> None:
    _make_release_candidate(tmp_path)
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.name", "V0 Test")
    _git(tmp_path, "config", "user.email", "v0@example.invalid")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "unbacked release candidate")
    commit = _git(tmp_path, "rev-parse", "HEAD")

    with pytest.raises(PreflightError, match="typed V0 activation state"):
        validate_repository(tmp_path, commit)


def test_repository_rejects_empty_benchmark_source_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_release_candidate(tmp_path)
    benchmark_path = tmp_path / "benchmark" / "v0_benchmark_report.json"
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    benchmark["source_artifacts"] = []
    _write_json(benchmark_path, _bind(benchmark, "report_sha256"))
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.name", "V0 Test")
    _git(tmp_path, "config", "user.email", "v0@example.invalid")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "empty source list")
    commit = _git(tmp_path, "rev-parse", "HEAD")
    monkeypatch.setattr(
        release_preflight,
        "_validate_activation_state",
        lambda _root, _commit: _validated_activation_summary(tmp_path),
    )

    with pytest.raises(PreflightError, match="non-empty"):
        validate_repository(tmp_path, commit)


@pytest.mark.parametrize(
    "relative,content,error",
    [
        (Path(".artifacts/model.json"), b"{}", "restricted path"),
        (Path("inputs/taxonomy.xlsx"), b"PK", "restricted artifact type"),
        (Path("secrets/key.pem"), b"-----BEGIN PRIVATE KEY-----", "private-key material"),
        (Path("weights/model.bin"), b"GGUFpayload", "model-weight bytes"),
    ],
)
def test_restricted_byte_audit_fails_closed(
    tmp_path: Path,
    relative: Path,
    content: bytes,
    error: str,
) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    with pytest.raises(PreflightError, match=error):
        validate_restricted_paths(tmp_path, [relative])


def _write_tar(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, mode="w:gz") as archive:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(content))


def _make_assets(root: Path, assets: Path) -> None:
    assets.mkdir()
    wheel = assets / "eve_relation_rag-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, mode="w") as archive:
        archive.writestr(
            "eve_relation_rag-0.1.0.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: eve-relation-rag\nVersion: 0.1.0\n",
        )
    _write_tar(
        assets / "eve_relation_rag-0.1.0.tar.gz",
        {
            "eve_relation_rag-0.1.0/pyproject.toml": (
                b'[project]\nname = "eve-relation-rag"\nversion = "0.1.0"\n'
            )
        },
    )
    _write_tar(
        assets / "eve-relation-rag-v0.1.0-source.tar.gz",
        {"EndoViHo-RAG-0.1.0/README.md": b"V0\n"},
    )
    _write_json(
        assets / "eve-relation-rag-v0.1.0.spdx.json",
        {
            "spdxVersion": "SPDX-2.3",
            "documentNamespace": "https://example.invalid/v0-sbom",
            "packages": [{"name": "eve-relation-rag", "versionInfo": "0.1.0"}],
        },
    )
    write_release_notes(root, assets, COMMIT)
    write_checksum_manifest(assets)


def test_release_asset_set_is_exact_and_checksum_bound(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    assets = tmp_path / "assets"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "v0_release_notes.md").write_text(
        f"# V0 {PACKAGE_VERSION} {RELEASE_TAG}\n\n{OCI_IMAGE}\n\nNo PyPI publication.\n",
        encoding="utf-8",
    )
    _make_assets(root, assets)

    validate_assets(assets, COMMIT)
    (assets / "eve_relation_rag-0.1.0-py3-none-any.whl").write_bytes(b"tampered")
    with pytest.raises((PreflightError, zipfile.BadZipFile)):
        validate_assets(assets, COMMIT)


def _governance() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rulesets = [
        {
            "target": "branch",
            "enforcement": "active",
            "conditions": {
                "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []},
            },
            "rules": [
                {"type": "pull_request"},
                {"type": "deletion"},
                {"type": "non_fast_forward"},
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": True,
                        "required_status_checks": [
                            {"context": "quality"},
                            {"context": "container-smoke"},
                        ],
                    },
                },
            ],
        },
        {
            "target": "tag",
            "enforcement": "active",
            "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
            "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
        },
    ]
    environment = {
        "name": "v0-production",
        "protection_rules": [
            {"type": "required_reviewers", "reviewers": [{"type": "User", "id": 1}]}
        ],
        "deployment_branch_policy": {"protected_branches": True},
    }
    return rulesets, environment


def test_governance_requires_rules_checks_tags_and_human_environment() -> None:
    rulesets, environment = _governance()
    validate_governance(rulesets, environment)

    environment["protection_rules"] = []
    with pytest.raises(GovernanceError, match="human reviewers"):
        validate_governance(rulesets, environment)


def test_release_workflow_is_manual_pinned_and_ordered() -> None:
    workflow_text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    workflow = yaml.load(workflow_text, Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    dispatch_inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert set(dispatch_inputs) == {"release_commit", "confirmation"}
    assert workflow["permissions"] == {"contents": "read"}

    preflight = workflow["jobs"]["preflight"]
    publish = workflow["jobs"]["publish"]
    assert "environment" not in preflight
    assert preflight["permissions"] == {
        "actions": "read",
        "contents": "read",
        "deployments": "read",
    }
    assert publish["needs"] == "preflight"
    assert publish["environment"] == "v0-production"
    assert publish["permissions"] == {
        "actions": "read",
        "attestations": "write",
        "contents": "write",
        "deployments": "read",
        "id-token": "write",
        "packages": "write",
    }

    preflight_names = [step["name"] for step in preflight["steps"]]
    assert preflight_names.index("Install exact locked dependencies") < preflight_names.index(
        "Verify typed repository activation and restricted-byte gates"
    )
    assert workflow_text.count("check_m5_artifacts.py --check --profile activation") == 2

    all_steps = preflight["steps"] + publish["steps"]
    for step in all_steps:
        action = step.get("uses")
        if action is not None:
            assert re_full_action_sha(action), action

    publish_names = [step["name"] for step in publish["steps"]]
    assert publish_names.index("Install exact locked runtime dependencies") < publish_names.index(
        "Revalidate main, governance, and repository state"
    )
    assert publish_names.index("Create and push protected annotated tag") < publish_names.index(
        "Publish GitHub Release and artifact set"
    )
    assert publish_names.index("Publish GitHub Release and artifact set") < publish_names.index(
        "Publish the multi-platform GHCR image"
    )
    assert "pypi" not in workflow_text.casefold()
    assert "git tag --annotate" in workflow_text
    assert "subject-checksums: release-assets/SHA256SUMS" in workflow_text
    assert "ghcr-image-digest.txt" in workflow_text


def re_full_action_sha(action: str) -> bool:
    head, separator, revision = action.rpartition("@")
    return bool(
        head
        and separator
        and len(revision) == 40
        and all(ch in "0123456789abcdef" for ch in revision)
    )
