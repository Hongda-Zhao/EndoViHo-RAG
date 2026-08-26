#!/usr/bin/env python3
"""Verify and stage the complete frozen Milestone 1 pilot source."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from eve_relation_rag.config import get_settings
from eve_relation_rag.ingestion.milestone1 import (
    DEFAULT_RELEASE_KEY,
    FrozenMilestone1Inputs,
    stage_milestone1,
)


def main(argv: list[str] | None = None) -> int:
    """Run full verification/staging and emit exactly one JSON document."""

    parser = _parser()
    arguments = parser.parse_args(argv)
    inputs = FrozenMilestone1Inputs(
        manifest_path=arguments.manifest,
        workbook_path=arguments.workbook,
        assembly_report_path=arguments.assembly_report,
        sequence_report_path=arguments.sequence_report,
    )
    engine = None
    try:
        engine = create_engine(get_settings().database_url, poolclass=NullPool)
        with Session(engine) as session:
            report = stage_milestone1(
                session,
                inputs,
                release_key=arguments.release_key,
                batch_size=arguments.batch_size,
            )
    except Exception as exc:
        error = {
            "schema_version": "endoviho-milestone1-staging-entry-v1",
            "status": "error",
            "error": {
                "code": getattr(exc, "code", "milestone1_staging_failed"),
                "type": type(exc).__name__,
                "message": str(exc),
            },
        }
        print(json.dumps(error, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()

    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify and atomically stage the frozen Milestone 1 Data S1 pilot"
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/manifests/milestone1_zhao_v4_data_s1.json"),
    )
    parser.add_argument(
        "--workbook",
        type=Path,
        default=Path(".artifacts/milestone1/biorxiv_data_s1_remote.xlsx"),
    )
    parser.add_argument(
        "--assembly-report",
        type=Path,
        default=Path(".artifacts/milestone1/ncbi/assembly_data_report.jsonl"),
    )
    parser.add_argument(
        "--sequence-report",
        type=Path,
        default=Path(".artifacts/milestone1/ncbi/sequence_report.jsonl"),
    )
    parser.add_argument("--release-key", default=DEFAULT_RELEASE_KEY)
    parser.add_argument("--batch-size", type=int, default=1_000)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
