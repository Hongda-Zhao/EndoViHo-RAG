from __future__ import annotations

import stat
from pathlib import Path

import pytest

from eve_relation_rag.activation.release_state import V0ActivationArtifacts
from scripts import build_v0_activation_state as builder


def test_builder_artifact_options_follow_the_typed_schema() -> None:
    parser = builder._parser()
    artifact_destinations = {
        action.dest
        for action in parser._actions
        if action.dest in V0ActivationArtifacts.model_fields and action.required
    }

    assert artifact_destinations == set(V0ActivationArtifacts.model_fields)


def test_builder_output_is_exclusive_and_private(tmp_path: Path) -> None:
    output = tmp_path / "v0_activation_state.json"

    builder._write_new(output, "{}\n")

    assert output.read_bytes() == b"{}\n"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    with pytest.raises(RuntimeError, match="new regular file"):
        builder._write_new(output, "changed\n")
