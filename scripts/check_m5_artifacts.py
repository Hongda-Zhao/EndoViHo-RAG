"""Validate canonical M5 JSON identities and their deterministic Markdown projections."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[1]
BENCHMARK_JSON = ROOT / "benchmark" / "v0_benchmark_report.json"
BENCHMARK_MARKDOWN = ROOT / "docs" / "benchmark_report.md"
CHECKLIST_JSON = ROOT / "release" / "v0_release_checklist.json"
CHECKLIST_MARKDOWN = ROOT / "docs" / "v0_release_checklist.md"

_SHA256 = re.compile(r"[0-9a-f]{64}")
_BENCHMARK_TOP_LEVEL_KEYS = frozenset(
    {
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
)
_BENCHMARK_SUITE_SPECS: dict[str, tuple[str, int, frozenset[str]]] = {
    "m2-structured-gold": (
        "tests_only_synthetic",
        31,
        frozenset(
            {
                "suite_key",
                "tier",
                "case_count",
                "accepted_case_count",
                "fail_closed_case_count",
                "thresholds",
                "result",
                "real_public_release",
            }
        ),
    ),
    "m3-deterministic-literature": (
        "tests_only_synthetic",
        5,
        frozenset(
            {
                "suite_key",
                "tier",
                "case_count",
                "result",
                "benchmark_manifest_sha256",
                "gold_sha256",
            }
        ),
    ),
    "m3-pinned-model-pilot": (
        "approved_real_corpus_local_model",
        13,
        frozenset(
            {
                "suite_key",
                "tier",
                "case_count",
                "thresholds",
                "metrics",
                "result",
                "corpus_release_key",
                "corpus_manifest_sha256",
                "benchmark_manifest_sha256",
                "gold_sha256",
                "benchmark_report_sha256",
                "receipt_sha256",
                "rebuild_sha256",
            }
        ),
    ),
    "m4-router": (
        "tests_only_mechanical",
        30,
        frozenset(
            {
                "suite_key",
                "tier",
                "case_count",
                "route_counts",
                "result",
                "benchmark_sha256",
            }
        ),
    ),
    "m4-generation": (
        "tests_only_mechanical",
        14,
        frozenset(
            {
                "suite_key",
                "tier",
                "case_count",
                "accepted_case_count",
                "rejected_case_count",
                "unsupported_case_count",
                "metrics",
                "result",
                "benchmark_sha256",
            }
        ),
    ),
}
_SOURCE_ARTIFACT_PATHS = frozenset(
    {
        "tests/benchmark/gold_cases.py",
        "tests/fixtures/literature/synthetic_benchmark.json",
        "tests/fixtures/m4/router_cases.json",
        "tests/fixtures/m4/generation_cases.json",
    }
)
_SOURCE_CANONICAL_HASHES = {
    "tests/fixtures/m4/router_cases.json": (
        "ad4142226ec986efec6dc26ee8125e679b12489d5322ec797e0acfd7fd66e356"
    ),
    "tests/fixtures/m4/generation_cases.json": (
        "538294e55050d9f1d2a56949849878d94cf5383e1c1049785f219c49c8e20cfa"
    ),
}
_M3_DETERMINISTIC_HASHES = {
    "benchmark_manifest_sha256": "cca5a1fef9a75581d961d2961ceb4e9f4d710211f7b01f6816873d2ba3e22446",
    "gold_sha256": "2e11b046bba37359c90d36849a583477453ad2437b4b540d1f58c42f1166278f",
}
_M3_PILOT_HASHES = {
    "corpus_manifest_sha256": "1497ea3383bea64d2bc4f17d2376dceb537b4f6c6f57ccb6eaf667b6589732f0",
    "benchmark_manifest_sha256": "856c46bc2ca5402151b95da2fddb8bf8ae44e7b535ed8c45382797b5a9e2db2e",
    "gold_sha256": "470a4191c43c63833b508ce36937767b762fe380143cedc6fb3f2799432d6e82",
    "benchmark_report_sha256": "894dc74002c27e3f2cdf6a47970041d88cb91a8625ec8fad8f00f6c87d7c2565",
    "receipt_sha256": "28f436d57630edd8403b71a503d23528fb7a1640432d8f623eca256b68858e7e",
    "rebuild_sha256": "cb7f81388b9d79bc4588a81afd9a351df1ab87f7d479f8a3b3dc8ee10adac9c5",
}
_LOCAL_VERIFICATION_KEYS = frozenset(
    {
        "status",
        "verified_at",
        "full_pytest_passed",
        "full_pytest_case_count",
        "full_pytest_warning_count",
        "frozen_benchmark_pytest_passed",
        "frozen_benchmark_case_count",
        "ruff_passed",
        "mypy_passed",
        "mypy_source_file_count",
        "lock_check_passed",
        "locked_package_count",
        "alembic_check_passed",
        "alembic_head",
        "clean_history_replay_passed",
        "package_build_passed",
        "wheel_member_count",
        "sdist_member_count",
        "container_smoke_passed",
        "container_demo_api_wiring_passed",
        "container_cleanup_passed",
        "tools",
    }
)
_TOOL_KEYS = frozenset(
    {
        "python",
        "uv",
        "pytest",
        "ruff",
        "mypy",
        "hatchling_build_backend",
        "docker_client",
        "docker_engine",
        "docker_compose",
        "colima",
    }
)
_TOOL_VERSIONS = {
    "python": "3.12.14",
    "uv": "0.12.5",
    "pytest": "8.4.2",
    "ruff": "0.16.4",
    "mypy": "1.20.2",
    "hatchling_build_backend": "1.32.0",
    "docker_client": "29.7.2",
    "docker_engine": "29.5.2",
    "docker_compose": "5.5.0",
    "colima": "0.10.3",
}
_CHECKLIST_TOP_LEVEL_KEYS = frozenset(
    {
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
)
_CHECKLIST_ITEM_SPECS: dict[str, tuple[str, str]] = {
    "M5-DEMO": ("m5_packaging", "pass"),
    "M5-DOCKER": ("m5_packaging", "pass"),
    "M5-DOCS": ("m5_packaging", "pass"),
    "M5-METADATA": ("m5_packaging", "pass"),
    "M5-QUALITY": ("m5_packaging", "pass"),
    "M5-MIGRATIONS": ("m5_packaging", "pass"),
    "M5-PACKAGE": ("m5_packaging", "pass"),
    "M1-TRUTH-SCHEMA": ("engineering", "pass"),
    "M2-STRUCTURED-MECHANISM": ("engineering", "pass"),
    "M3-LITERATURE-MECHANISM": ("engineering", "pass"),
    "M4-HYBRID-MECHANISM": ("engineering", "pass"),
    "V0-ENGLISH-ONLY": ("v0_definition_of_done", "pass"),
    "V0-POSTGRES-TRUTH": ("v0_definition_of_done", "pass"),
    "V0-LAYER-SEPARATION": ("v0_definition_of_done", "pass"),
    "V0-LOCUS-VERSION-COORDINATES": ("v0_definition_of_done", "block"),
    "V0-AUDIT-LAYER-SEPARATION": ("v0_definition_of_done", "pass"),
    "V0-LINEAGE-SCHEME-SNAPSHOT": ("v0_definition_of_done", "block"),
    "V0-AMBIGUITY-FAIL-CLOSED": ("v0_definition_of_done", "pass"),
    "V0-LLM-NO-SQL-NO-MUTATION": ("v0_definition_of_done", "pass"),
    "V0-FIXED-LITERATURE-RETRIEVAL": ("v0_definition_of_done", "pass"),
    "V0-DOCUMENT-CLAIM-CITATIONS": ("v0_definition_of_done", "pass"),
    "V0-DOCKER-COLD-START": ("v0_definition_of_done", "pass"),
    "V0-REPOSITORY-RELEASE-ASSETS": ("v0_definition_of_done", "pass"),
    "V0-README-COVERAGE-LIMITS": ("v0_definition_of_done", "pass"),
    "V0-THREE-REAL-ROUTES": ("v0_definition_of_done", "block"),
    "V0-PUBLISHED-STRUCTURED-RELEASE": ("real_activation", "block"),
    "V0-REAL-BINDING-ANCHORS": ("real_activation", "block"),
    "V0-PRODUCTION-GENERATION": ("real_activation", "block"),
    "V0-HUMAN-SEMANTIC-BENCHMARK": ("real_activation", "block"),
    "V0-BENCHMARK-THRESHOLDS": ("v0_definition_of_done", "block"),
    "V0-FROZEN-INPUT-REBUILD": ("v0_definition_of_done", "block"),
    "PUB-GIT-TAG": ("external_publication", "block"),
    "PUB-GITHUB-RELEASE": ("external_publication", "block"),
    "PUB-PYPI-OR-REGISTRY": ("external_publication", "block"),
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _require_exact_keys(payload: dict[str, Any], expected: frozenset[str], label: str) -> None:
    actual = frozenset(payload)
    _require(actual == expected, f"{label} keys drifted: {sorted(actual ^ expected)}")


def _package_version() -> str:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return str(project["version"])


def _validate_benchmark(payload: dict[str, Any]) -> None:
    _require_exact_keys(payload, _BENCHMARK_TOP_LEVEL_KEYS, "benchmark report")
    _require(
        payload["benchmark_report_schema_version"] == "v0-benchmark-report-v1",
        "unexpected benchmark schema version",
    )
    _require(payload["product_version"] == "V0", "benchmark product version must be V0")
    _require(
        payload["package_version"] == _package_version(),
        "benchmark package version differs from pyproject.toml",
    )
    _require(payload["engineering_benchmarks_passed"] is True, "engineering gates must pass")
    _require(
        payload["real_hybrid_activation_qualified"] is False,
        "M5 report must not qualify real hybrid activation",
    )
    _require(
        isinstance(payload["report_sha256"], str)
        and _SHA256.fullmatch(payload["report_sha256"]) is not None,
        "invalid benchmark report checksum",
    )

    suites = payload["suites"]
    _require(isinstance(suites, list), "benchmark suites must be a list")
    suite_by_key: dict[str, dict[str, Any]] = {}
    for suite in suites:
        _require(isinstance(suite, dict), "each benchmark suite must be an object")
        suite_key = suite.get("suite_key")
        _require(isinstance(suite_key, str), "each benchmark suite requires a string suite_key")
        _require(suite_key not in suite_by_key, f"duplicate benchmark suite: {suite_key}")
        _require(suite_key in _BENCHMARK_SUITE_SPECS, f"unexpected benchmark suite: {suite_key}")
        tier, case_count, expected_keys = _BENCHMARK_SUITE_SPECS[suite_key]
        _require_exact_keys(suite, expected_keys, f"benchmark suite {suite_key}")
        _require(suite["tier"] == tier, f"benchmark suite {suite_key} tier drifted")
        _require(suite["case_count"] == case_count, f"benchmark suite {suite_key} count drifted")
        _require(suite["result"] == "passed", f"benchmark suite {suite_key} did not pass")
        suite_by_key[suite_key] = suite
    _require(
        frozenset(suite_by_key) == frozenset(_BENCHMARK_SUITE_SPECS),
        "benchmark suite set is incomplete",
    )
    m2 = suite_by_key["m2-structured-gold"]
    _require(m2["accepted_case_count"] == 26, "M2 accepted-case count drifted")
    _require(m2["fail_closed_case_count"] == 5, "M2 fail-closed count drifted")
    _require(
        m2["thresholds"]
        == {
            "entity_resolution_exact_percent": 100,
            "plan_exact_percent": 100,
            "result_set_exact_percent": 100,
            "numeric_exact_percent": 100,
            "provenance_exact_percent": 100,
        },
        "M2 thresholds drifted",
    )
    _require(m2["real_public_release"] is False, "M2 must remain tests-only")

    m3_deterministic = suite_by_key["m3-deterministic-literature"]
    for field, expected_hash in _M3_DETERMINISTIC_HASHES.items():
        _require(
            m3_deterministic[field] == expected_hash,
            f"M3 deterministic {field} drifted",
        )

    m3_pilot = suite_by_key["m3-pinned-model-pilot"]
    _require(
        m3_pilot["thresholds"]
        == {
            "recall_at_5_minimum": "0.800000000000",
            "recall_at_10_minimum": "0.900000000000",
            "citation_id_validity_required": "1.000000000000",
            "locator_validity_required": "1.000000000000",
        },
        "M3 pilot thresholds drifted",
    )
    _require(
        m3_pilot["metrics"]
        == {
            "recall_at_5": "0.846153846154",
            "recall_at_10": "1.000000000000",
            "citation_id_validity": "1.000000000000",
            "locator_validity": "1.000000000000",
        },
        "M3 pilot metrics drifted",
    )
    _require(
        m3_pilot["corpus_release_key"] == "corpus:endoviho-rag:v0:20260828:001",
        "M3 corpus release drifted",
    )
    for field, expected_hash in _M3_PILOT_HASHES.items():
        _require(
            m3_pilot[field] == expected_hash,
            f"M3 pilot {field} drifted",
        )

    m4_router = suite_by_key["m4-router"]
    _require(
        m4_router["route_counts"]
        == {"structured": 5, "literature": 5, "hybrid": 10, "unsupported": 10},
        "M4 route counts drifted",
    )
    _require(
        m4_router["benchmark_sha256"] == _SOURCE_CANONICAL_HASHES[
            "tests/fixtures/m4/router_cases.json"
        ],
        "M4 router identity drifted",
    )
    m4_generation = suite_by_key["m4-generation"]
    _require(m4_generation["accepted_case_count"] == 7, "M4 accepted count drifted")
    _require(m4_generation["rejected_case_count"] == 6, "M4 rejected count drifted")
    _require(m4_generation["unsupported_case_count"] == 1, "M4 unsupported count drifted")
    _require(
        m4_generation["metrics"]
        == {
            "structured_values_and_identifiers_unchanged_percent": 100,
            "document_claims_with_current_citations_percent": 100,
            "exact_evidence_spans_percent": 100,
            "invented_identifier_accept_count": 0,
            "unsupported_refusal_percent": 100,
            "unsupported_downstream_call_count": 0,
        },
        "M4 generation metrics drifted",
    )
    _require(
        m4_generation["benchmark_sha256"] == _SOURCE_CANONICAL_HASHES[
            "tests/fixtures/m4/generation_cases.json"
        ],
        "M4 generation identity drifted",
    )

    artifacts = payload["source_artifacts"]
    _require(isinstance(artifacts, list), "source_artifacts must be a list")
    artifact_paths: set[str] = set()
    for artifact in artifacts:
        _require(isinstance(artifact, dict), "each source artifact must be an object")
        _require(
            frozenset(artifact) in {
                frozenset({"path", "file_sha256"}),
                frozenset({"path", "file_sha256", "canonical_payload_sha256"}),
            },
            "source artifact keys drifted",
        )
        relative_path = artifact["path"]
        _require(isinstance(relative_path, str), "source artifact path must be a string")
        source = Path(relative_path)
        _require(not source.is_absolute() and ".." not in source.parts, "unsafe source path")
        _require(relative_path not in artifact_paths, f"duplicate source artifact: {relative_path}")
        artifact_paths.add(relative_path)
        source_path = ROOT / source
        _require(
            source_path.is_file() and not source_path.is_symlink(),
            f"missing source: {relative_path}",
        )
        actual_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
        _require(artifact["file_sha256"] == actual_hash, f"source hash drifted: {relative_path}")
        expected_canonical = _SOURCE_CANONICAL_HASHES.get(relative_path)
        if expected_canonical is None:
            _require(
                "canonical_payload_sha256" not in artifact,
                f"unexpected canonical payload hash: {relative_path}",
            )
        else:
            _require(
                artifact.get("canonical_payload_sha256") == expected_canonical,
                f"canonical payload hash drifted: {relative_path}",
            )
    _require(frozenset(artifact_paths) == _SOURCE_ARTIFACT_PATHS, "source artifact set drifted")

    _require(
        payload["human_semantic_support_review"]
        == {"status": "not_run", "approved": False, "blocking": True, "reviewed_claim_count": 0},
        "human semantic-review block drifted",
    )
    verification = payload["local_verification"]
    _require(isinstance(verification, dict), "local_verification must be an object")
    _require_exact_keys(verification, _LOCAL_VERIFICATION_KEYS, "local verification")
    _require(verification["status"] == "passed", "local verification must be passed")
    for gate in (
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
    ):
        _require(verification[gate] is True, f"local gate did not pass: {gate}")
    expected_snapshot = {
        "verified_at": "2026-08-28",
        "full_pytest_case_count": 724,
        "full_pytest_warning_count": 1,
        "frozen_benchmark_case_count": 72,
        "mypy_source_file_count": 84,
        "locked_package_count": 114,
        "alembic_head": "0010_m3_lock_hardening",
        "wheel_member_count": 89,
        "sdist_member_count": 129,
    }
    for field, expected_value in expected_snapshot.items():
        _require(verification[field] == expected_value, f"local verification drifted: {field}")
    tools = verification["tools"]
    _require(isinstance(tools, dict), "local verification tools must be an object")
    _require_exact_keys(tools, _TOOL_KEYS, "local verification tools")
    _require(tools == _TOOL_VERSIONS, "local verification tool versions drifted")
    limitations = payload["limitations"]
    _require(
        isinstance(limitations, list)
        and len(limitations) == 4
        and all(isinstance(item, str) and item for item in limitations),
        "benchmark limitations must contain four non-empty statements",
    )


def _validate_checklist(payload: dict[str, Any]) -> None:
    _require_exact_keys(payload, _CHECKLIST_TOP_LEVEL_KEYS, "release checklist")
    _require(
        payload["release_checklist_schema_version"] == "v0-release-checklist-v1",
        "unexpected release-checklist schema version",
    )
    _require(payload["product_version"] == "V0", "checklist product version must be V0")
    _require(
        payload["package_version"] == _package_version(),
        "checklist package version differs from pyproject.toml",
    )
    _require(
        isinstance(payload["benchmark_report_sha256"], str)
        and _SHA256.fullmatch(payload["benchmark_report_sha256"]) is not None,
        "invalid benchmark report identity",
    )
    _require(payload["milestone_5_engineering_status"] == "fulfilled", "M5 is not fulfilled")
    _require(
        payload["software_distribution_status"] == "preview_ready_not_published",
        "software distribution status drifted",
    )
    _require(payload["v0_definition_of_done_status"] == "blocked", "V0 DoD must remain blocked")
    _require(
        payload["real_hybrid_activation_qualified"] is False,
        "checklist must not qualify real hybrid activation",
    )
    _require(
        isinstance(payload["checklist_sha256"], str)
        and _SHA256.fullmatch(payload["checklist_sha256"]) is not None,
        "invalid release checklist checksum",
    )
    items = payload["items"]
    _require(isinstance(items, list), "checklist items must be a list")
    item_by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        _require(isinstance(item, dict), "each checklist item must be an object")
        _require_exact_keys(
            item,
            frozenset({"id", "category", "status", "evidence"}),
            "release checklist item",
        )
        item_id = item["id"]
        _require(isinstance(item_id, str), "checklist item id must be a string")
        _require(item_id not in item_by_id, f"duplicate checklist item: {item_id}")
        _require(item_id in _CHECKLIST_ITEM_SPECS, f"unexpected checklist item: {item_id}")
        category, status = _CHECKLIST_ITEM_SPECS[item_id]
        _require(item["category"] == category, f"checklist category drifted: {item_id}")
        _require(item["status"] == status, f"checklist status drifted: {item_id}")
        _require(
            isinstance(item["evidence"], str) and item["evidence"] and item["evidence"].isascii(),
            f"checklist evidence must be non-empty ASCII: {item_id}",
        )
        item_by_id[item_id] = item
    _require(
        frozenset(item_by_id) == frozenset(_CHECKLIST_ITEM_SPECS),
        "release checklist item set is incomplete",
    )


def _canonical_sha256(payload: dict[str, Any], checksum_field: str) -> str:
    body = dict(payload)
    del body[checksum_field]
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_checked(path: Path, checksum_field: str) -> dict[str, Any]:
    raw_payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_payload, dict):
        raise RuntimeError(f"{path.relative_to(ROOT)} must contain a JSON object")
    payload: dict[str, Any] = raw_payload
    if path == BENCHMARK_JSON:
        _validate_benchmark(payload)
    elif path == CHECKLIST_JSON:
        _validate_checklist(payload)
    expected = _canonical_sha256(payload, checksum_field)
    if payload.get(checksum_field) != expected:
        raise RuntimeError(f"{path.relative_to(ROOT)} has a stale {checksum_field}: {expected}")
    return payload


def _render_benchmark(payload: dict[str, Any]) -> str:
    suites = payload["suites"]
    engineering_passed = str(payload["engineering_benchmarks_passed"]).lower()
    activation_qualified = str(payload["real_hybrid_activation_qualified"]).lower()
    human_review_status = payload["human_semantic_support_review"]["status"]
    lines = [
        "# V0 benchmark report",
        "",
        "> Deterministic projection of `benchmark/v0_benchmark_report.json`.",
        "",
        "## Qualification boundary",
        "",
        f"- Engineering benchmarks passed: `{engineering_passed}`",
        f"- Real hybrid activation qualified: `{activation_qualified}`",
        f"- Human semantic-support review: `{human_review_status}` (blocking)",
        "",
        "Mechanical citation, quote, and identifier checks do not establish semantic "
        "entailment or biological truth.",
        "",
        "## Frozen suites",
        "",
        "| Suite | Tier | Cases | Result | Canonical identity |",
        "|---|---|---:|---|---|",
    ]
    for suite in suites:
        identity = (
            suite.get("benchmark_sha256")
            or suite.get("benchmark_report_sha256")
            or "defined in tests"
        )
        lines.append(
            f"| `{suite['suite_key']}` | `{suite['tier']}` | {suite['case_count']} | "
            f"{suite['result']} | `{identity}` |"
        )
    m3 = next(item for item in suites if item["suite_key"] == "m3-pinned-model-pilot")
    metrics = m3["metrics"]
    thresholds = m3["thresholds"]
    lines.extend(
        [
            "",
            "## M3 pinned-model pilot",
            "",
            f"Exact corpus: `{m3['corpus_release_key']}`. Recall@5 was `{metrics['recall_at_5']}` "
            f"against `>={thresholds['recall_at_5_minimum']}`; Recall@10 was "
            f"`{metrics['recall_at_10']}` against `>={thresholds['recall_at_10_minimum']}`. "
            "Citation-ID and locator validity were both `1.0`.",
            "",
            "These metrics describe one fixed approved pilot corpus and pinned local embedding "
            "model, not the full virology literature.",
            "",
            "## Tracked benchmark sources",
            "",
        ]
    )
    lines.extend(
        f"- `{item['path']}` — file SHA-256 `{item['file_sha256']}`"
        for item in payload["source_artifacts"]
    )
    verification = payload["local_verification"]
    lines.extend(
        [
            "",
            "## Local M5 verification",
            "",
            f"Status: `{verification['status']}` on `{verification['verified_at']}`.",
            "",
            f"- Full suite: `{verification['full_pytest_case_count']} passed`, "
            f"`{verification['full_pytest_warning_count']} warning`.",
            f"- Frozen benchmark selection: `{verification['frozen_benchmark_case_count']} "
            "passed`.",
            f"- Static gates: Ruff passed; strict mypy passed over "
            f"`{verification['mypy_source_file_count']}` source files; "
            f"`{verification['locked_package_count']}` packages are locked.",
            f"- Migration gates: head `{verification['alembic_head']}`, no model drift, and "
            "clean-history replay passed.",
            f"- Distribution gates: wheel `{verification['wheel_member_count']}` members; sdist "
            f"`{verification['sdist_member_count']}` members; package audit passed.",
            "- Container gates: fresh-volume startup, Demo-to-API wiring, fail-closed responses, "
            "and isolated cleanup passed.",
            "",
            "## Limitations",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in payload["limitations"])
    lines.extend(["", f"Report SHA-256: `{payload['report_sha256']}`", ""])
    return "\n".join(lines)


def _render_checklist(payload: dict[str, Any]) -> str:
    activation_qualified = str(payload["real_hybrid_activation_qualified"]).lower()
    lines = [
        "# V0 release checklist",
        "",
        "> Deterministic projection of `release/v0_release_checklist.json`.",
        "",
        "## Status",
        "",
        f"- M5 engineering: `{payload['milestone_5_engineering_status']}`",
        f"- Software distribution: `{payload['software_distribution_status']}`",
        f"- V0 Definition of Done: `{payload['v0_definition_of_done_status']}`",
        f"- Real hybrid activation qualified: `{activation_qualified}`",
        "",
        "## Gates",
        "",
        "| ID | Category | Status | Evidence |",
        "|---|---|---|---|",
    ]
    lines.extend(
        f"| `{item['id']}` | `{item['category']}` | **{item['status'].upper()}** | "
        f"{item['evidence']} |"
        for item in payload["items"]
    )
    lines.extend(
        [
            "",
            "A packaging pass does not override a real-activation or external-publication block.",
            "",
            f"Checklist SHA-256: `{payload['checklist_sha256']}`",
            "",
        ]
    )
    return "\n".join(lines)


def expected_artifacts() -> dict[Path, str]:
    benchmark = _load_checked(BENCHMARK_JSON, "report_sha256")
    checklist = _load_checked(CHECKLIST_JSON, "checklist_sha256")
    if checklist["benchmark_report_sha256"] != benchmark["report_sha256"]:
        raise RuntimeError("release checklist points to a stale benchmark report")
    return {
        BENCHMARK_MARKDOWN: _render_benchmark(benchmark),
        CHECKLIST_MARKDOWN: _render_checklist(checklist),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail when Markdown projections drift")
    parser.add_argument("--write", action="store_true", help="rewrite Markdown projections")
    args = parser.parse_args()
    if args.check == args.write:
        parser.error("choose exactly one of --check or --write")
    for path, expected in expected_artifacts().items():
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != expected:
                raise RuntimeError(f"{path.relative_to(ROOT)} is stale")
        else:
            path.write_text(expected, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
