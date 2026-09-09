#!/usr/bin/env python3
"""Import two explicitly pinned review workbooks, or verify an existing authoring packet."""

from __future__ import annotations

import argparse
from pathlib import Path

from eve_relation_rag.experiments.rag_value_ablation.authoring_package import (
    verify_authoring_package,
    write_authoring_package,
)
from eve_relation_rag.experiments.rag_value_ablation.authoring_review import (
    AuthoringReviewError,
    AuthoringReviewLedger,
    load_reviewed_workbooks,
)
from eve_relation_rag.experiments.rag_value_ablation.family_assignment import (
    build_hybrid_family_assignment,
    verify_classified_package,
    write_classified_package,
)
from eve_relation_rag.experiments.rag_value_ablation.scope_amendment import (
    _read_input,
    build_core53_scope_amendment,
    verify_core53_package,
    write_core53_package,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    load = subparsers.add_parser("import", help="create a new authoring-only package")
    load.add_argument("--original", type=Path, required=True)
    load.add_argument("--original-sha256", required=True)
    load.add_argument("--incremental", type=Path, required=True)
    load.add_argument("--incremental-sha256", required=True)
    load.add_argument("--output", type=Path, required=True)
    check = subparsers.add_parser("verify", help="recompute all package files from its ledger")
    check.add_argument("directory", type=Path)
    amend = subparsers.add_parser("amend-core53", help="record the exact user core-53 decision")
    amend.add_argument("directory", type=Path)
    amend.add_argument("--decision-text", required=True)
    amend.add_argument("--output", type=Path, required=True)
    scope_check = subparsers.add_parser("verify-scope", help="verify the revised scope package")
    scope_check.add_argument("directory", type=Path)
    classify = subparsers.add_parser("classify-hybrid", help="record UNSUP-09 as hybrid")
    classify.add_argument("directory", type=Path)
    classify.add_argument("--decision-text", required=True)
    classify.add_argument("--output", type=Path, required=True)
    family_check = subparsers.add_parser("verify-classified", help="verify classified package")
    family_check.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "verify-classified":
            assignment = verify_classified_package(args.directory)
            print("Authoring only: 53 candidates; families=16/16/9/12; executable=false.")
            print(f"Family SHA-256: {assignment.manifest_sha256}")
            return 0
        if args.command == "classify-hybrid":
            scope = verify_core53_package(args.directory)
            # Read only the bounded, fully verified ledger; no database or provider.
            ledger = AuthoringReviewLedger.model_validate_json(
                _read_input(args.directory / "ledger.json")
            )
            assignment = build_hybrid_family_assignment(
                ledger, scope, decision_text=args.decision_text,
            )
            write_classified_package(ledger, scope, assignment, args.output)
            print("Authoring only: UNSUP-09=hybrid; families=16/16/9/12; executable=false.")
            print(f"Family SHA-256: {assignment.manifest_sha256}")
            return 0
        if args.command == "verify-scope":
            amendment = verify_core53_package(args.directory)
            print(f"Scope only: {amendment.fixed_candidate_count} candidates; executable=false.")
            print(f"Scope SHA-256: {amendment.manifest_sha256}")
            return 0
        if args.command in {"verify", "amend-core53"}:
            ledger = verify_authoring_package(args.directory)
            if args.command == "amend-core53":
                amendment = build_core53_scope_amendment(ledger, decision_text=args.decision_text)
                write_core53_package(ledger, amendment, args.output)
                print(f"Scope SHA-256: {amendment.manifest_sha256}")
        else:
            ledger = load_reviewed_workbooks(
                args.original,
                args.incremental,
                expected_original_sha256=args.original_sha256,
                expected_incremental_sha256=args.incremental_sha256,
            )
            write_authoring_package(ledger, args.output)
    except (AuthoringReviewError, OSError):
        print("Authoring import/verification failed. Check pinned inputs and a new output path.")
        return 2
    print(
        f"Authoring only: {ledger.core_count} core, {ledger.extension_count} separate extension, "
        f"{ledger.excluded_count} excluded; trusted approvals=0; executable=false."
    )
    print(f"Ledger SHA-256: {ledger.manifest_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
