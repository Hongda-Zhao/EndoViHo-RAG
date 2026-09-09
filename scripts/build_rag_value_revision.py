#!/usr/bin/env python3
"""Write or verify the exact core-53 authoring revision, without runtime I/O."""

from __future__ import annotations

import argparse
from pathlib import Path

from eve_relation_rag.experiments.rag_value_ablation.question_revision import (
    ACTIVE_PACKAGE,
    HISTORICAL_PACKAGE,
    revision_package_files,
    verify_revision_package,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / ACTIVE_PACKAGE)
    args = parser.parse_args()
    try:
        source = ROOT / HISTORICAL_PACKAGE
        if not args.verify:
            files = revision_package_files(source)
            if args.output.exists() or args.output.is_symlink():
                raise ValueError("output already exists")
            target = args.output.parent.resolve(strict=True) / args.output.name
            target.mkdir(exist_ok=False)
            for name, raw in files.items():
                with (target / name).open("xb") as stream:
                    stream.write(raw)
        verify_revision_package(source, args.output)
    except (OSError, ValueError):
        print("Revision failed validation or output already exists; no source was overwritten.")
        return 2
    print("single-source-v2: 53 pending questions; exactly 3 wording changes; authoring only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
