from __future__ import annotations

import hashlib
import re

import pytest

from eve_relation_rag.domain.keys import (
    LocusIdentity,
    StableKeyError,
    canonical_json,
    canonical_json_sha256,
    is_release_key,
    locus_key,
    stable_key,
)


def test_canonical_json_is_order_independent_and_compact() -> None:
    left = {"z": None, "a": ["海", True, 7], "nested": {"b": 2, "a": 1}}
    right = {"nested": {"a": 1, "b": 2}, "a": ("海", True, 7), "z": None}

    expected = '{"a":["海",true,7],"nested":{"a":1,"b":2},"z":null}'
    assert canonical_json(left) == expected
    assert canonical_json(right) == expected
    assert canonical_json_sha256(left) == hashlib.sha256(expected.encode()).hexdigest()


@pytest.mark.parametrize("value", [{"value": 1.5}, {1: "non-string-key"}, {"value": {1, 2}}])
def test_canonical_json_rejects_ambiguous_values(value: object) -> None:
    with pytest.raises(StableKeyError):
        canonical_json(value)


def test_generic_stable_key_uses_full_lowercase_sha256() -> None:
    key = stable_key("call:zhao2026-v4", {"row": 39158, "token": "vr1"})

    assert re.fullmatch(r"call:zhao2026-v4:sha256:[0-9a-f]{64}", key)
    assert key == stable_key("call:zhao2026-v4", {"token": "vr1", "row": 39158})


def test_locus_key_preimage_is_coordinate_free_and_deterministic() -> None:
    identity = LocusIdentity(
        source_snapshot_key="study-defined:10.1101/2025.04.19.649669:v4:data-s1",
        assembly_accession_version="GCA_945859735.2",
        contig_accession_version="CAMAOU020000182.1",
        native_vr_token="vr3",
        identity_policy_version="zhao-v4-contig-source-occurrence-v1",
    )

    payload = identity.canonical_payload()
    assert payload == {
        "assembly_accession_version": "GCA_945859735.2",
        "contig_accession_version": "CAMAOU020000182.1",
        "identity_policy_version": "zhao-v4-contig-source-occurrence-v1",
        "native_vr_token": "vr3",
        "source_snapshot_key": "study-defined:10.1101/2025.04.19.649669:v4:data-s1",
    }
    assert "start0" not in payload
    assert "end0" not in payload
    assert identity.key() == locus_key(
        source_snapshot_key=identity.source_snapshot_key,
        assembly_accession_version=identity.assembly_accession_version,
        contig_accession_version=identity.contig_accession_version,
        native_vr_token=identity.native_vr_token,
        identity_policy_version=identity.identity_policy_version,
    )
    assert identity.key() == (
        "locus:eve:v1:sha256:"
        "60e27773c6490d6a95eb0c2a2c9ca3531c65465764554b650064d42372a046ad"
    )
    assert re.fullmatch(r"locus:eve:v1:sha256:[0-9a-f]{64}", identity.key())


def test_native_vr_token_prevents_same_contig_collision() -> None:
    common = {
        "source_snapshot_key": "study-defined:10.1101/2025.04.19.649669:v4:data-s1",
        "assembly_accession_version": "GCA_945859735.2",
        "contig_accession_version": "CAMAOU020000182.1",
        "identity_policy_version": "zhao-v4-contig-source-occurrence-v1",
    }

    assert locus_key(native_vr_token="vr3", **common) != locus_key(native_vr_token="vr7", **common)


@pytest.mark.parametrize(
    ("assembly", "contig"),
    [
        ("GCA_945859735", "CAMAOU020000182.1"),
        ("GCA_945859735.0", "CAMAOU020000182.1"),
        ("GCA_945859735.2", "CAMAOU020000182"),
        ("GCA_945859735.2", "CAMAOU020000182.0"),
    ],
)
def test_locus_key_rejects_unversioned_or_invalid_accessions(assembly: str, contig: str) -> None:
    with pytest.raises(StableKeyError):
        locus_key(
            source_snapshot_key="snapshot:zhao-v4:data-s1",
            assembly_accession_version=assembly,
            contig_accession_version=contig,
            native_vr_token="vr3",
            identity_policy_version="policy-v1",
        )


def test_stable_key_rejects_unversioned_namespace() -> None:
    with pytest.raises(StableKeyError):
        stable_key("Locus EVE", {"value": "x"})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("release:endoviho-rag:v0:20260826:001", True),
        ("release:endoviho-rag:v0:20260230:001", False),
        ("release:endoviho-rag:latest:20260826:001", False),
        ("release:endoviho-rag:v0:20260826:1", False),
    ],
)
def test_release_key_uses_immutable_versioned_grammar(value: str, expected: bool) -> None:
    assert is_release_key(value) is expected
