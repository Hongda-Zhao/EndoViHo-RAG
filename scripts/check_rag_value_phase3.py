#!/usr/bin/env python3
"""Print the public-workspace Phase 3 readiness report without runtime I/O."""

from __future__ import annotations

import subprocess
from pathlib import Path

from eve_relation_rag.experiments.rag_value_ablation.workspace_readiness import (
    audit_public_phase3_workspace,
    render_phase3_workspace_audit,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    audit = audit_public_phase3_workspace(
        REPOSITORY_ROOT,
        source_tree_clean=_source_tree_clean(REPOSITORY_ROOT),
    )
    print(render_phase3_workspace_audit(audit), end="")
    return 0 if audit.ready else 2


def _source_tree_clean(repository_root: Path) -> bool:
    completed = subprocess.run(
        ("git", "status", "--porcelain", "--untracked-files=all"),
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=False,
    )
    return not completed.stdout


if __name__ == "__main__":
    raise SystemExit(main())
