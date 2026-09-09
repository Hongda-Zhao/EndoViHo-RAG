#!/usr/bin/env python3
"""Check the exact core-53 annotations without starting benchmark dependencies."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import Never

from eve_relation_rag.experiments.rag_value_ablation.annotations import (
    require_trusted_question_set,
    validate_oracle_coverage,
)
from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    OracleEvidenceManifest,
    QuestionManifest,
)
from eve_relation_rag.experiments.rag_value_ablation.family_assignment import (
    ClassifiedCandidate,
    verify_classified_package,
)
from eve_relation_rag.experiments.rag_value_ablation.question_revision import (
    ACTIVE_REVISION,
    load_core53_candidates,
)
from eve_relation_rag.experiments.rag_value_ablation.scoped_admission import (
    CORE53_CLASSIFIED_FILE_SHA256,
    CORE53_CLASSIFIED_PACKAGE_SHA256,
    ApprovedEntityBindings,
    build_core53_question_scope,
)
from eve_relation_rag.literature.contracts import StrictFrozenSchema
from eve_relation_rag.literature.hashing import canonical_json_bytes

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGE = (
    REPOSITORY_ROOT / "benchmark/rag_value_ablation/authoring_review_core53_classified"
)
MAX_INPUT_BYTES = 4 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class InputCheckError(ValueError):
    """A local input is missing, unpinned, oversized, or invalid."""


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        # argparse's default error can echo arbitrary values supplied on the command line.
        raise InputCheckError("invalid arguments; use --help for the accepted flags")


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = _SafeArgumentParser(
        prog="check_rag_value_core53.py", description=__doc__, allow_abbrev=False,
    )
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument(
        "--revision", choices=("classified-v1", ACTIVE_REVISION), default=ACTIVE_REVISION,
    )
    for name in ("entities", "questions", "oracle"):
        parser.add_argument(f"--{name}", type=Path, help=f"approved {name} JSON manifest")
        parser.add_argument(f"--{name}-sha256", help="externally approved exact file SHA-256")
    args = parser.parse_args(argv)
    for name in ("entities", "questions", "oracle"):
        if (getattr(args, name) is None) != (getattr(args, f"{name}_sha256") is None):
            raise InputCheckError("each supplied manifest requires its pinned checksum")
    if (args.entities is None) != (args.questions is None):
        raise InputCheckError("entity and question manifests must be supplied together")
    if args.oracle is not None and args.questions is None:
        raise InputCheckError("Oracle checking requires approved entities and questions")
    return args


def _read_bounded(path: Path) -> bytes:
    """Read at most four MiB from one regular file, rejecting final-component symlinks."""

    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_INPUT_BYTES:
            raise InputCheckError("input must be a bounded regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise InputCheckError("input exceeds the byte limit")
        return raw
    finally:
        os.close(descriptor)


def _load_pinned[T: StrictFrozenSchema](
    path: Path, file_sha256: str, contract: type[T],
) -> T:
    """Validate exact approved bytes and the existing typed contract, without approval creation."""

    if _SHA256_RE.fullmatch(file_sha256) is None:
        raise InputCheckError("a file checksum is invalid")
    raw = _read_bounded(path)
    if hashlib.sha256(raw).hexdigest() != file_sha256:
        raise InputCheckError("file bytes differ from the approved checksum")
    value = contract.model_validate_json(raw)
    if raw != canonical_json_bytes(value) + b"\n":
        raise InputCheckError("manifest JSON must be canonical")
    return value


def _verify_package(package: Path) -> tuple[ClassifiedCandidate, ...]:
    """Reuse authoring verification and pin this CLI to the exact accepted classified package."""

    verify_classified_package(package)
    package_raw = _read_bounded(package / "package_manifest.json")
    candidate_raw = _read_bounded(package / "classified_candidates.jsonl")
    if (
        hashlib.sha256(package_raw).hexdigest() != CORE53_CLASSIFIED_PACKAGE_SHA256
        or hashlib.sha256(candidate_raw).hexdigest() != CORE53_CLASSIFIED_FILE_SHA256
    ):
        raise InputCheckError("package differs from the accepted core-53 scope")
    return tuple(
        ClassifiedCandidate.model_validate_json(line) for line in candidate_raw.splitlines()
    )


def _runtime_boundary() -> None:
    print("Runtime ready: false; this command grants no execution or publication authority.")
    print("Blocked: phase3_gate_issued_execution_evidence_not_implemented.")
    print("No database, retrieval, provider, model, network, or release execution occurred.")


def main(argv: Sequence[str] | None = None) -> int:
    """Return zero only for complete checked annotations; runtime remains blocked."""

    try:
        args = _arguments(argv)
        _verify_package(args.package)
        candidates = load_core53_candidates(args.package, revision=args.revision)
        print(f"Active wording revision: {args.revision}; scientific approvals remain separate.")
        slots = sorted({slot for row in candidates for slot in row.entity_slots})
        print(
            f"Verified authoring scope: {len(candidates)} questions; "
            "families structured=16, literature=16, hybrid=9, unsupported=12."
        )
        print(f"Required shared entity selections: {len(slots)}; ASSEMBLY_B is unused.")
        print("Entity slots: " + ", ".join(slots) + ".")
        if args.questions is None:
            print(
                "Annotations: BLOCKED; approved entities, question Gold, and Oracle not supplied."
            )
            _runtime_boundary()
            return 2
        entities = _load_pinned(args.entities, args.entities_sha256, ApprovedEntityBindings)
        scope = build_core53_question_scope(args.package, entities)
        questions = _load_pinned(args.questions, args.questions_sha256, QuestionManifest)
        approved = require_trusted_question_set(questions, scope=scope, revision=args.revision)
        print(f"Approved entity and question/Gold contracts validated: {len(approved)} questions.")
        if args.oracle is None:
            print("Annotations: BLOCKED; separately approved Oracle not supplied.")
            _runtime_boundary()
            return 2
        oracle = _load_pinned(args.oracle, args.oracle_sha256, OracleEvidenceManifest)
        validate_oracle_coverage(questions, oracle)
        print("Annotations: READY; entity, question/Gold, and Oracle contracts validated.")
        print("These checks do not independently verify human identities or scientific truth.")
        _runtime_boundary()
        return 0
    except Exception:
        # Never expose Pydantic excerpts, user paths, credentials, or raw annotation bytes.
        print("Annotations: BLOCKED; local input or approval validation failed.")
        print("Check paired manifest/SHA-256 flags, exact package, canonical JSON, and approvals.")
        _runtime_boundary()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
