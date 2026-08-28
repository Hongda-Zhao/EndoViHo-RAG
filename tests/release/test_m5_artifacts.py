from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path
from typing import Any

from eve_relation_rag.domain.keys import canonical_json_sha256
from scripts.check_m5_artifacts import expected_artifacts

ROOT = Path(__file__).parents[2]


def _load(path: str) -> dict[str, Any]:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _self_hash(payload: dict[str, Any], field: str) -> str:
    body = dict(payload)
    del body[field]
    return canonical_json_sha256(body)


def test_benchmark_report_is_checksum_bound_and_matches_frozen_suites() -> None:
    report = _load("benchmark/v0_benchmark_report.json")
    suites = {suite["suite_key"]: suite for suite in report["suites"]}

    assert set(report) == {
        "benchmark_report_schema_version",
        "product_version",
        "package_version",
        "engineering_benchmarks_passed",
        "real_hybrid_activation_qualified",
        "suites",
        "source_artifacts",
        "human_semantic_support_review",
        "local_verification",
        "limitations",
        "report_sha256",
    }
    assert report["benchmark_report_schema_version"] == "v0-benchmark-report-v1"
    assert report["product_version"] == "V0"
    assert report["package_version"] == tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    assert report["report_sha256"] == _self_hash(report, "report_sha256")
    assert report["engineering_benchmarks_passed"] is True
    assert report["real_hybrid_activation_qualified"] is False
    assert set(suites) == {
        "m2-structured-gold",
        "m3-deterministic-literature",
        "m3-pinned-model-pilot",
        "m4-router",
        "m4-generation",
    }
    assert len(suites) == len(report["suites"])
    assert all(suite["result"] == "passed" for suite in report["suites"])
    assert suites["m2-structured-gold"]["case_count"] == 31
    assert suites["m2-structured-gold"]["real_public_release"] is False
    assert suites["m3-pinned-model-pilot"]["metrics"] == {
        "recall_at_5": "0.846153846154",
        "recall_at_10": "1.000000000000",
        "citation_id_validity": "1.000000000000",
        "locator_validity": "1.000000000000",
    }
    assert suites["m4-router"]["case_count"] == 30
    assert suites["m4-router"]["benchmark_sha256"] == (
        "ad4142226ec986efec6dc26ee8125e679b12489d5322ec797e0acfd7fd66e356"
    )
    assert suites["m4-generation"]["case_count"] == 14
    assert suites["m4-generation"]["benchmark_sha256"] == (
        "538294e55050d9f1d2a56949849878d94cf5383e1c1049785f219c49c8e20cfa"
    )
    assert report["human_semantic_support_review"] == {
        "status": "not_run",
        "approved": False,
        "blocking": True,
        "reviewed_claim_count": 0,
    }
    seen_paths: set[str] = set()
    for artifact in report["source_artifacts"]:
        source = Path(artifact["path"])
        assert not source.is_absolute()
        assert ".." not in source.parts
        assert artifact["path"] not in seen_paths
        seen_paths.add(artifact["path"])
        assert artifact["file_sha256"] == hashlib.sha256(
            (ROOT / source).read_bytes()
        ).hexdigest()


def test_release_checklist_cannot_hide_current_activation_blocks() -> None:
    checklist = _load("release/v0_release_checklist.json")
    items = checklist["items"]
    by_id = {item["id"]: item for item in items}

    expected_items = {
        "M5-DEMO",
        "M5-DOCKER",
        "M5-DOCS",
        "M5-METADATA",
        "M5-QUALITY",
        "M5-MIGRATIONS",
        "M5-PACKAGE",
        "M1-TRUTH-SCHEMA",
        "M2-STRUCTURED-MECHANISM",
        "M3-LITERATURE-MECHANISM",
        "M4-HYBRID-MECHANISM",
        "V0-ENGLISH-ONLY",
        "V0-POSTGRES-TRUTH",
        "V0-LAYER-SEPARATION",
        "V0-THREE-REAL-ROUTES",
        "V0-PUBLISHED-STRUCTURED-RELEASE",
        "V0-LOCUS-VERSION-COORDINATES",
        "V0-AUDIT-LAYER-SEPARATION",
        "V0-LINEAGE-SCHEME-SNAPSHOT",
        "V0-AMBIGUITY-FAIL-CLOSED",
        "V0-LLM-NO-SQL-NO-MUTATION",
        "V0-FIXED-LITERATURE-RETRIEVAL",
        "V0-DOCUMENT-CLAIM-CITATIONS",
        "V0-BENCHMARK-THRESHOLDS",
        "V0-DOCKER-COLD-START",
        "V0-FROZEN-INPUT-REBUILD",
        "V0-REPOSITORY-RELEASE-ASSETS",
        "V0-README-COVERAGE-LIMITS",
        "V0-REAL-BINDING-ANCHORS",
        "V0-PRODUCTION-GENERATION",
        "V0-HUMAN-SEMANTIC-BENCHMARK",
        "PUB-GIT-TAG",
        "PUB-GITHUB-RELEASE",
        "PUB-PYPI-OR-REGISTRY",
    }
    assert set(checklist) == {
        "release_checklist_schema_version",
        "product_version",
        "package_version",
        "benchmark_report_sha256",
        "milestone_5_engineering_status",
        "software_distribution_status",
        "v0_definition_of_done_status",
        "real_hybrid_activation_qualified",
        "items",
        "checklist_sha256",
    }
    assert checklist["release_checklist_schema_version"] == "v0-release-checklist-v1"
    assert checklist["product_version"] == "V0"
    assert checklist["package_version"] == tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    assert checklist["checklist_sha256"] == _self_hash(checklist, "checklist_sha256")
    assert checklist["benchmark_report_sha256"] == _load(
        "benchmark/v0_benchmark_report.json"
    )["report_sha256"]
    assert set(by_id) == expected_items
    assert len(by_id) == len(items)
    assert all(set(item) == {"id", "category", "status", "evidence"} for item in items)
    assert checklist["milestone_5_engineering_status"] == "fulfilled"
    assert checklist["software_distribution_status"] == "preview_ready_not_published"
    assert checklist["v0_definition_of_done_status"] == "blocked"
    assert checklist["real_hybrid_activation_qualified"] is False
    for item_id in (
        "V0-PUBLISHED-STRUCTURED-RELEASE",
        "V0-LOCUS-VERSION-COORDINATES",
        "V0-LINEAGE-SCHEME-SNAPSHOT",
        "V0-REAL-BINDING-ANCHORS",
        "V0-PRODUCTION-GENERATION",
        "V0-HUMAN-SEMANTIC-BENCHMARK",
        "V0-BENCHMARK-THRESHOLDS",
        "V0-FROZEN-INPUT-REBUILD",
        "PUB-GIT-TAG",
        "PUB-GITHUB-RELEASE",
        "PUB-PYPI-OR-REGISTRY",
    ):
        assert by_id[item_id]["status"] == "block"


def test_markdown_release_artifacts_are_exact_projections() -> None:
    for path, expected in expected_artifacts().items():
        assert path.read_text(encoding="utf-8") == expected
