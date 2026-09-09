"""Pinned projection inputs fail before any database construction."""

from __future__ import annotations

from argparse import Namespace

import pytest

from scripts.run_rag_value_experiment import load_projection_options
from tests.experiments.test_rag_value_association_projection import _bindings


def test_projection_cli_loads_exact_binding_and_rejects_wrong_hash(tmp_path):
    binding = _bindings()
    path = tmp_path / "binding.json"
    path.write_text(binding.model_dump_json())
    args = Namespace(
        mode="s4-rehearsal", projection_bindings=path,
        projection_bindings_sha256=binding.binding_sha256,
    )
    options = load_projection_options(args)
    assert options["projection_bindings"] == binding
    assert options["approved_projection_binding_sha256"] == binding.binding_sha256
    args.projection_bindings_sha256 = "0" * 64
    with pytest.raises(ValueError, match="approved manifest"):
        load_projection_options(args)


def test_projection_cli_rejects_missing_hash_and_symlink(tmp_path):
    args = Namespace(mode="s4-rehearsal", projection_bindings=tmp_path / "absent.json")
    with pytest.raises(ValueError, match="exact approved"):
        load_projection_options(args)
    path = tmp_path / "binding.json"
    binding = _bindings()
    path.write_text(binding.model_dump_json())
    link = tmp_path / "link.json"
    link.symlink_to(path)
    args.projection_bindings = link
    args.projection_bindings_sha256 = binding.binding_sha256
    with pytest.raises(ValueError, match="structured rehearsal"):
        load_projection_options(args)
