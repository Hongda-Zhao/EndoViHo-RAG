from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]


def test_build_backend_is_exactly_pinned() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["build-system"]["requires"] == ["hatchling==1.32.0"]
    assert pyproject["build-system"]["build-backend"] == "hatchling.build"


def test_citation_metadata_is_valid_and_does_not_claim_a_release() -> None:
    citation = yaml.safe_load((ROOT / "CITATION.cff").read_text(encoding="utf-8"))

    assert citation["cff-version"] == "1.2.0"
    assert citation["title"] == "EndoViHo-RAG"
    assert citation["type"] == "software"
    assert citation["version"] == "V0"
    assert citation["license"] == "MIT"
    assert citation["authors"] == [{"family-names": "Zhao", "given-names": "Hongda"}]
    assert citation["repository-code"] == "https://github.com/Hongda-Zhao/EndoViHo-RAG"
    assert "date-released" not in citation
    assert "doi" not in citation


def test_license_and_changelog_keep_third_party_and_release_boundaries() -> None:
    data_license = (ROOT / "DATA_LICENSE").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    for marker in (
        "CC-BY-NC-ND-4.0",
        "CC-BY-4.0",
        "CC0-1.0",
        "NCBI-MOLECULAR-DATA-USAGE-POLICY",
        "BAAI/bge-small-en-v1.5",
    ):
        assert marker in data_license
    assert "V0 — Unreleased" in changelog
    assert "v0.1.0" not in changelog.casefold()
