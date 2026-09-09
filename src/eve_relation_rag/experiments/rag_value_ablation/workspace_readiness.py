"""Deterministic, read-only audit of public Phase 3 workspace inputs.

This module is deliberately diagnostic. It does not read application settings,
connect to PostgreSQL, construct a retriever, load an embedding model, or issue
runtime authority. A blocked report is the expected result until independently
approved questions, Gold, live releases, and runtime evidence are supplied.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from eve_relation_rag.experiments.rag_value_ablation.associations import (
    association_contract_v1_bytes,
)
from eve_relation_rag.experiments.rag_value_ablation.question_revision import (
    ACTIVE_REVISION,
    HISTORICAL_PACKAGE,
    load_core53_candidates,
)
from eve_relation_rag.literature.contracts import (
    NonEmptyText,
    Sha256,
    StableToken,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.hashing import canonical_json_sha256
from eve_relation_rag.releases.mini_dataset import load_mini_dataset_release

PHASE3_EXECUTION_SYSTEM_KEYS: tuple[Literal["S1", "S2", "S3", "S4"], ...] = (
    "S1",
    "S2",
    "S3",
    "S4",
)
PHASE4_DEFERRED_SYSTEM_KEYS: tuple[Literal["S5"], ...] = ("S5",)


class Phase3WorkspaceCheck(StrictFrozenSchema):
    """One deterministic public-workspace observation and its blockers."""

    check_key: StableToken
    passed: bool
    observation: NonEmptyText
    blocker_codes: tuple[StableToken, ...] = ()

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.blocker_codes != tuple(sorted(set(self.blocker_codes))):
            raise ValueError("workspace blocker codes must be sorted and unique")
        if self.passed != (not self.blocker_codes):
            raise ValueError("workspace check status does not match blocker codes")
        return self


class Phase3WorkspaceAuditReport(StrictFrozenSchema):
    """Self-checksummed diagnostic report with no execution authority."""

    audit_schema_version: Literal["rag-value-phase3-workspace-audit-v1"] = (
        "rag-value-phase3-workspace-audit-v1"
    )
    execution_system_keys: tuple[Literal["S1", "S2", "S3", "S4"], ...]
    deferred_system_keys: tuple[Literal["S5"], ...]
    checks: tuple[Phase3WorkspaceCheck, ...] = Field(min_length=1)
    ready: bool
    production_dependencies_constructed: Literal[False] = False
    report_sha256: Sha256

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        if self.execution_system_keys != PHASE3_EXECUTION_SYSTEM_KEYS:
            raise ValueError("Phase 3 execution systems must be S1-S4")
        if self.deferred_system_keys != PHASE4_DEFERRED_SYSTEM_KEYS:
            raise ValueError("S5 must remain deferred to Phase 4")
        keys = tuple(check.check_key for check in self.checks)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("workspace checks must be canonically ordered and unique")
        if self.ready != all(check.passed for check in self.checks):
            raise ValueError("workspace readiness does not match checks")
        if self.report_sha256 != _self_sha256(self):
            raise ValueError("workspace audit checksum does not match")
        return self

    @property
    def blocker_codes(self) -> tuple[str, ...]:
        return tuple(
            sorted({code for check in self.checks for code in check.blocker_codes})
        )


def audit_public_phase3_workspace(
    repository_root: Path,
    *,
    source_tree_clean: bool,
) -> Phase3WorkspaceAuditReport:
    """Inspect only repository-distributed inputs and return diagnostic blockers."""

    root = _resolve_repository_root(repository_root)
    checks = tuple(
        sorted(
            (
                _association_contract_check(root),
                _approved_inputs_check(root),
                _entity_binding_check(root),
                _mini_release_check(root),
                _retrieval_runtime_check(),
                _runtime_authority_check(),
                _scientific_question_check(root),
                _source_tree_check(source_tree_clean),
            ),
            key=lambda check: check.check_key,
        )
    )
    payload: dict[str, object] = {
        "audit_schema_version": "rag-value-phase3-workspace-audit-v1",
        "execution_system_keys": PHASE3_EXECUTION_SYSTEM_KEYS,
        "deferred_system_keys": PHASE4_DEFERRED_SYSTEM_KEYS,
        "checks": checks,
        "ready": all(check.passed for check in checks),
        "production_dependencies_constructed": False,
    }
    return Phase3WorkspaceAuditReport.model_validate(
        {**payload, "report_sha256": canonical_json_sha256(payload)}
    )


def render_phase3_workspace_audit(report: Phase3WorkspaceAuditReport) -> str:
    """Render a compact text report without paths, credentials, or document bytes."""

    status = "READY" if report.ready else "BLOCKED"
    lines = [
        f"Phase 3 workspace readiness: {status}",
        "Execution scope: S1, S2, S3, S4",
        "Deferred to Phase 4: S5",
        "",
    ]
    for check in report.checks:
        marker = "PASS" if check.passed else "BLOCKED"
        lines.append(f"- [{marker}] {check.check_key}: {check.observation}")
    lines.extend(
        [
            "",
            "Blocker codes:",
            *(f"- {code}" for code in report.blocker_codes),
            "",
            "No database, retriever, embedding provider, or LLM was constructed.",
            f"Report SHA-256: {report.report_sha256}",
        ]
    )
    return "\n".join(lines) + "\n"


def _association_contract_check(root: Path) -> Phase3WorkspaceCheck:
    path = root / "benchmark/rag_value_ablation/association_contract_v1.json"
    valid = _read_bytes(path) == association_contract_v1_bytes()
    return _check(
        "association_contract",
        "canonical v1 association contract verified; relation classes are not assigned"
        if valid
        else "canonical v1 association contract is missing or invalid",
        () if valid else ("association_contract_invalid",),
    )


def _scientific_question_check(root: Path) -> Phase3WorkspaceCheck:
    try:
        templates = load_core53_candidates(root / HISTORICAL_PACKAGE)
    except (OSError, ValueError):
        return _check("scientific_questions", "core-53 authoring packet invalid", (
            "scientific_question_template_invalid",
        ))
    blockers = {
        "approved_questions_missing",
        "approved_gold_missing",
        "association_projection_runtime_missing",
    }
    return _check(
        "scientific_questions",
        f"revision={ACTIVE_REVISION}; templates={len(templates)}, approved=0; "
        "authoring accepted, scientific Gold and runtime coverage not supplied",
        tuple(blockers),
    )


def _entity_binding_check(root: Path) -> Phase3WorkspaceCheck:
    try:
        rows = load_core53_candidates(root / HISTORICAL_PACKAGE)
    except (OSError, ValueError):
        return _check("entity_bindings", "core-53 authoring packet invalid", (
            "entity_binding_template_invalid",
        ))
    slots = {slot for row in rows for slot in row.entity_slots}
    blockers = {"approved_entity_bindings_missing"}
    return _check(
        "entity_bindings",
        f"bindings={len(slots)}, approved=0; exact runtime bindings not supplied",
        tuple(blockers),
    )


def _approved_inputs_check(root: Path) -> Phase3WorkspaceCheck:
    benchmark = root / "benchmark/rag_value_ablation"
    question_rows = _nonempty_line_count(benchmark / "questions_template.jsonl")
    oracle_rows = _nonempty_line_count(benchmark / "oracle_evidence_template.jsonl")
    return _check(
        "approved_inputs",
        f"non-empty question/Gold template rows={question_rows}; "
        f"non-empty Oracle template rows={oracle_rows}; "
        "no approved CorpusRelease, S1 bundle, or association manifest is distributed",
        (
            "approved_association_manifest_missing",
            "approved_corpus_binding_missing",
            "approved_question_manifest_missing",
            "approved_s1_raw_context_missing",
            "request_validation_receipt_missing",
        ),
    )


def _mini_release_check(root: Path) -> Phase3WorkspaceCheck:
    try:
        loaded = load_mini_dataset_release(root / "data/releases/endoviho-mini-v1")
    except (OSError, ValueError):
        return _check(
            "portable_dataset_release",
            "portable mini DatasetRelease is missing or invalid",
            ("portable_dataset_release_invalid",),
        )
    manifest = loaded.manifest
    source_lineages = {
        row.viral_lineage_affinity.source_term_key for row in loaded.loci
    }
    evidence_sources = {row.evidence_and_source.source_snapshot_key for row in loaded.loci}
    return _check(
        "portable_dataset_release",
        f"release={manifest.release_key}; assemblies={manifest.counts.assemblies}; "
        f"loci={manifest.counts.loci}; biological viral lineages={len(source_lineages)}; "
        f"evidence sources={len(evidence_sources)}; database activation=not_performed",
        (
            "dataset_live_gate_not_observed",
            "dataset_postgresql_activation_missing",
            "evidence_source_diversity_insufficient",
            "viral_lineage_diversity_insufficient",
        ),
    )


def _runtime_authority_check() -> Phase3WorkspaceCheck:
    return _check(
        "runtime_authority",
        "live role audit and execution authority must be supplied to the implemented gate",
        (
            "database_role_audit_missing",
            "phase3_live_execution_authority_required",
        ),
    )


def _retrieval_runtime_check() -> Phase3WorkspaceCheck:
    return _check(
        "retrieval_runtime",
        "approved local BGE artifact and corpus-bound hybrid runtime validation are unavailable",
        (
            "approved_bge_artifact_missing",
            "retrieval_runtime_validation_missing",
        ),
    )


def _source_tree_check(source_tree_clean: bool) -> Phase3WorkspaceCheck:
    return _check(
        "source_tree",
        "source tree is clean" if source_tree_clean else "source tree has uncommitted changes",
        () if source_tree_clean else ("source_tree_not_clean",),
    )


def _check(
    check_key: str,
    observation: str,
    blocker_codes: tuple[str, ...] | set[str],
) -> Phase3WorkspaceCheck:
    blockers = tuple(sorted(blocker_codes))
    return Phase3WorkspaceCheck(
        check_key=check_key,
        passed=not blockers,
        observation=observation,
        blocker_codes=blockers,
    )


def _resolve_repository_root(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("repository root must not be a symbolic link")
    root = path.resolve(strict=True)
    if not root.is_dir() or not (root / "pyproject.toml").is_file():
        raise ValueError("repository root is invalid")
    return root


def _read_bytes(path: Path) -> bytes | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _nonempty_line_count(path: Path) -> int:
    raw = _read_bytes(path)
    if raw is None:
        return 0
    return sum(bool(line.strip()) for line in raw.splitlines())


def _self_sha256(report: Phase3WorkspaceAuditReport) -> str:
    payload = report.model_dump(mode="python")
    payload.pop("report_sha256")
    return canonical_json_sha256(payload)


__all__ = [
    "PHASE3_EXECUTION_SYSTEM_KEYS",
    "PHASE4_DEFERRED_SYSTEM_KEYS",
    "Phase3WorkspaceAuditReport",
    "Phase3WorkspaceCheck",
    "audit_public_phase3_workspace",
    "render_phase3_workspace_audit",
]
