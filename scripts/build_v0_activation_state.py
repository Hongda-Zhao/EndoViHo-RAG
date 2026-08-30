"""Build the middle evidence-bound stage of the V0 activation-state manifest.

The evidence files must already be tracked at ``activation_evidence_commit``.  The
clean rebuild must name a strict ancestor runtime commit.  The resulting state file
is written afterwards and is committed by the operator in a strict descendant
publication commit, avoiding both commit/self-hash fixed points.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from eve_relation_rag.activation.release_state import (
    ACTIVATION_STATE_PATH,
    V0ActivationArtifacts,
    build_v0_activation_state_manifest,
    validate_v0_activation_state,
)
from eve_relation_rag.hybrid.contracts import canonical_model_json


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--activation-evidence-commit",
        help="Committed Git identity containing every activation evidence file; defaults to HEAD.",
    )
    parser.add_argument("--release-key", required=True)
    parser.add_argument("--corpus-release-key", required=True)
    parser.add_argument("--output", type=Path, default=ACTIVATION_STATE_PATH)
    artifacts = parser.add_argument_group("committed typed activation evidence")
    for field_name in V0ActivationArtifacts.model_fields:
        artifacts.add_argument(
            f"--{field_name.replace('_', '-')}",
            dest=field_name,
            type=Path,
            required=True,
        )
    return parser


def _git_head(root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError("the activation evidence Git commit is unavailable")
    return completed.stdout.strip()


def _artifact_paths(arguments: argparse.Namespace) -> dict[str, Path]:
    return {
        field_name: getattr(arguments, field_name)
        for field_name in V0ActivationArtifacts.model_fields
    }


def _write_new(path: Path, payload: str) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise RuntimeError("activation state output parent is unavailable or unsafe")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        observed = os.fstat(descriptor)
        if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
            raise OSError
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise RuntimeError("activation state output must be a new regular file") from exc


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        root = arguments.root.resolve(strict=True)
        evidence_commit = arguments.activation_evidence_commit or _git_head(root)
        output_relative = arguments.output
        if output_relative.is_absolute() or ".." in output_relative.parts:
            raise RuntimeError("activation state output must be repository-relative")
        output_parent = root
        for part in output_relative.parts[:-1]:
            output_parent /= part
            if output_parent.is_symlink() or not output_parent.is_dir():
                raise RuntimeError("activation state output parent is unavailable or unsafe")
        manifest = build_v0_activation_state_manifest(
            root,
            activation_evidence_commit=evidence_commit,
            release_key=arguments.release_key,
            corpus_release_key=arguments.corpus_release_key,
            artifact_paths=_artifact_paths(arguments),
        )
        _write_new(root / output_relative, canonical_model_json(manifest) + "\n")
        validated = validate_v0_activation_state(
            root,
            state_path=output_relative,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "error": {"message": str(exc), "type": type(exc).__name__},
                    "status": "error",
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "activation_evidence_commit": validated.activation_evidence_commit,
                "artifact_count": len(V0ActivationArtifacts.model_fields),
                "state_sha256": validated.state_sha256,
                "status": "candidate",
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
