"""Versioned core-53 wording amendment; never creates scientific approvals.

The historical packet is verified byte-for-byte before deriving the three
user-requested single-publication amendments. Every other candidate is unchanged.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from eve_relation_rag.experiments.rag_value_ablation.family_assignment import (
    ClassifiedCandidate,
    verify_classified_package,
)
from eve_relation_rag.experiments.rag_value_ablation.scope_amendment import _read_input
from eve_relation_rag.experiments.rag_value_ablation.scoped_admission import (
    CORE53_CLASSIFIED_FILE_SHA256,
    CORE53_CLASSIFIED_PACKAGE_SHA256,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256

type QuestionRevision = Literal["classified-v1", "single-source-v2"]
ACTIVE_REVISION: QuestionRevision = "single-source-v2"
HISTORICAL_PACKAGE = "benchmark/rag_value_ablation/authoring_review_core53_classified"
ACTIVE_PACKAGE = "benchmark/rag_value_ablation/authoring_review_core53_single_source_v2"

REVISED_ENGLISH = {
    "HOST-H-02": (
        "For assembly-source taxa within {SOURCE_TAXON_LINEAGE_A}, which associations "
        "involving assemblies, EVE loci, and reported viral regions are directly supported "
        "by at least one publication? Group by viral-lineage affinity. For every relevant "
        "publication in the permitted corpus, list its evidence, the author's claim, and "
        "any explicitly reported confidence or uncertainty, preserving the identity of "
        "each assembly, locus, and reported region."
    ),
    "VIRUS-H-02": (
        "For viral-lineage affinity {VIRAL_LINEAGE_A}, which associations involving "
        "assemblies, EVE loci, and reported viral regions are directly supported by at "
        "least one publication? Group by assembly-source taxon. For every relevant "
        "publication in the permitted corpus, list its evidence, the author's claim, and "
        "any explicitly reported confidence or uncertainty, preserving the identity of "
        "each assembly, locus, and reported region."
    ),
    "REL-H-02": (
        "For assembly-source taxa within {SOURCE_TAXON_LINEAGE_A} associated with "
        "viral-lineage affinity {VIRAL_LINEAGE_A}, which associations involving EVE loci "
        "and reported viral regions are directly supported by at least one publication? "
        "For every relevant publication in the permitted corpus, list its evidence, the "
        "author's claim, and any explicitly reported confidence or uncertainty, preserving "
        "the identity of each locus and reported region."
    ),
}
REVISED_CHINESE = {
    "HOST-H-02": "分类群 A 下，哪些 Assembly、EVE 位点和文献病毒区域的关联有至少一篇文献支持？"
    "按病毒谱系分组，逐篇记录相关文献的证据、作者判断及其确定程度。",
    "VIRUS-H-02": "病毒谱系 A 下，哪些 Assembly、EVE 位点和文献病毒区域的关联有至少一篇文献支持？"
    "按来源分类单元分组，逐篇记录相关文献的证据、作者判断及其确定程度。",
    "REL-H-02": "分类群 A 与病毒谱系 A 相关的 EVE 位点和文献病毒区域，哪些关联有至少一篇文献支持？"
    "逐篇记录相关文献的证据、作者判断及其确定程度，保留每个位点和区域的身份。",
}


def revision_policy() -> dict[str, object]:
    """An authoring policy, not runtime expansion or an expert signature."""

    return {
        "revision": ACTIVE_REVISION,
        "source_package_sha256": CORE53_CLASSIFIED_PACKAGE_SHA256,
        "source_candidates_sha256": CORE53_CLASSIFIED_FILE_SHA256,
        "wording_amendments": dict(sorted(REVISED_ENGLISH.items())),
        "minimum_direct_publications": 1,
        "record_each_relevant_source_claim_and_certainty": True,
        "priority_eve_terms": ["viral fossil", "viral fossils"],
        "preserve_original_term_and_endogenous_context": True,
        "runtime_query_expansion_enabled": False,
        "scientific_approval": None,
        "runtime_authorization": None,
    }


def revise_candidates(
    candidates: tuple[ClassifiedCandidate, ...],
) -> tuple[ClassifiedCandidate, ...]:
    """Require exact historical inputs even if caller recomputes individual hashes."""

    raw = b"".join(canonical_json_bytes(row) + b"\n" for row in candidates)
    if hashlib.sha256(raw).hexdigest() != CORE53_CLASSIFIED_FILE_SHA256:
        raise ValueError("revision requires the exact historical core-53 candidates")
    revised = []
    for row in candidates:
        if row.template_id not in REVISED_ENGLISH:
            revised.append(row)
            continue
        text = REVISED_ENGLISH[row.template_id]
        payload = row.model_dump(mode="json", exclude={"record_sha256"})
        payload.update({
            "source_record_sha256": row.record_sha256,
            "question_text_template": text,
            "question_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        })
        revised.append(ClassifiedCandidate.model_validate_json(canonical_json_bytes({
            **payload, "record_sha256": canonical_json_sha256(payload),
        })))
    return tuple(revised)


def load_core53_candidates(
    historical_package: Path, *, revision: QuestionRevision = ACTIVE_REVISION,
) -> tuple[ClassifiedCandidate, ...]:
    """One version selector shared by CLI, workload, admission and diagnostics."""

    if revision not in {"classified-v1", ACTIVE_REVISION}:
        raise ValueError("unknown core-53 revision")
    verify_classified_package(historical_package)
    if hashlib.sha256(_read_input(historical_package / "package_manifest.json")).hexdigest() != (
        CORE53_CLASSIFIED_PACKAGE_SHA256
    ):
        raise ValueError("historical package identity mismatch")
    raw = _read_input(historical_package / "classified_candidates.jsonl")
    if hashlib.sha256(raw).hexdigest() != CORE53_CLASSIFIED_FILE_SHA256:
        raise ValueError("historical candidate identity mismatch")
    candidates = tuple(ClassifiedCandidate.model_validate_json(line) for line in raw.splitlines())
    return revise_candidates(candidates) if revision == ACTIVE_REVISION else candidates


def revision_package_files(historical_package: Path) -> dict[str, bytes]:
    """Generate a minimal immutable derivative, retaining the historical packet."""

    rows = load_core53_candidates(historical_package)
    files = {
        "classified_candidates.jsonl": b"".join(
            canonical_json_bytes(row) + b"\n" for row in rows
        ),
        "authoring_policy.json": canonical_json_bytes(revision_policy()) + b"\n",
        "QUESTION_REVIEW.cn.md": (
            "# 53题单篇文献修订\n\n只修订三题，其他50题与历史包逐字一致。"
            "全部科学标注仍为 pending，未生成 Gold 或 Oracle。\n\n"
            + "\n\n".join(f"- {key}：{value}" for key, value in REVISED_CHINESE.items())
            + "\n"
        ).encode("utf-8"),
    }
    payload = {
        "schema_version": "rag-value-core53-revision-package-v1",
        "revision": ACTIVE_REVISION,
        "artifact_kind": "authoring_only",
        "source_package_sha256": CORE53_CLASSIFIED_PACKAGE_SHA256,
        "question_count": len(rows),
        "executable": False,
        "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(files.items())},
    }
    files["package_manifest.json"] = canonical_json_bytes({
        **payload, "manifest_sha256": canonical_json_sha256(payload),
    }) + b"\n"
    return files


def verify_revision_package(historical_package: Path, directory: Path) -> None:
    """Verify the exact derivative, not merely self-reported checksums."""

    expected = revision_package_files(historical_package)
    if directory.is_symlink() or {p.name for p in directory.iterdir()} != set(expected):
        raise ValueError("revision package member mismatch")
    if any(_read_input(directory / name) != raw for name, raw in expected.items()):
        raise ValueError("revision package differs from approved authoring policy")
