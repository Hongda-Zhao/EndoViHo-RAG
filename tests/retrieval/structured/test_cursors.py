from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from eve_relation_rag.retrieval.structured.cursors import (
    CURSOR_VERSION,
    CursorContext,
    CursorInvalidError,
    CursorPayload,
    CursorPlanMismatchError,
    decode_cursor,
    encode_cursor,
)

RELEASE = "release:endoviho-rag:v0:20260827:001"
OTHER_RELEASE = "release:endoviho-rag:v0:20260827:002"
MANIFEST = "a" * 64
PLAN_HASH = "b" * 64
LOCUS = f"locus:eve:v1:sha256:{'c' * 64}"
TEST_SECRET = b"x" * 32
OTHER_TEST_SECRET = b"y" * 32


def _context(
    *,
    release_key: str = RELEASE,
    release_manifest_sha256: str = MANIFEST,
    plan_sha256: str = PLAN_HASH,
) -> CursorContext:
    return CursorContext(
        release_key=release_key,
        release_manifest_sha256=release_manifest_sha256,
        plan_sha256=plan_sha256,
        intent="list_loci",
        canonical_sort_key="locus_key",
    )


def test_cursor_round_trip_is_deterministic_versioned_and_unpadded() -> None:
    context = _context()
    token = encode_cursor(context, last_sort_values=(LOCUS,), secret=TEST_SECRET)

    assert token == encode_cursor(context, last_sort_values=(LOCUS,), secret=TEST_SECRET)
    assert re.fullmatch(r"[A-Za-z0-9_-]+", token)
    assert "=" not in token

    payload = decode_cursor(token, expected_context=context, secret=TEST_SECRET)
    assert payload == CursorPayload(
        cursor_version=CURSOR_VERSION,
        release_key=RELEASE,
        release_manifest_sha256=MANIFEST,
        plan_sha256=PLAN_HASH,
        intent="list_loci",
        canonical_sort_key="locus_key",
        last_sort_values=(LOCUS,),
    )


def test_tampered_cursor_and_wrong_secret_fail_as_invalid() -> None:
    context = _context()
    token = encode_cursor(context, last_sort_values=(LOCUS,), secret=TEST_SECRET)
    index = len(token) // 2
    replacement = "A" if token[index] != "A" else "B"
    tampered = token[:index] + replacement + token[index + 1 :]

    with pytest.raises(CursorInvalidError) as tampered_error:
        decode_cursor(tampered, expected_context=context, secret=TEST_SECRET)
    assert tampered_error.value.code == "cursor_invalid"

    with pytest.raises(CursorInvalidError):
        decode_cursor(token, expected_context=context, secret=OTHER_TEST_SECRET)


@pytest.mark.parametrize("token", ["", "abc=", "abc+", "_" * 4097, "a"])
def test_malformed_cursor_fails_closed(token: str) -> None:
    with pytest.raises(CursorInvalidError, match="malformed or unauthenticated"):
        decode_cursor(token, expected_context=_context(), secret=TEST_SECRET)


@pytest.mark.parametrize(
    "other_context",
    [
        _context(release_key=OTHER_RELEASE),
        _context(release_manifest_sha256="d" * 64),
        _context(plan_sha256="e" * 64),
        CursorContext(
            release_key=RELEASE,
            release_manifest_sha256=MANIFEST,
            plan_sha256=PLAN_HASH,
            intent="list_assemblies",
            canonical_sort_key="assembly_accession_version+assembly_key",
        ),
    ],
)
def test_valid_cursor_reused_across_context_fails_as_plan_mismatch(
    other_context: CursorContext,
) -> None:
    token = encode_cursor(_context(), last_sort_values=(LOCUS,), secret=TEST_SECRET)

    with pytest.raises(CursorPlanMismatchError) as caught:
        decode_cursor(token, expected_context=other_context, secret=TEST_SECRET)
    assert caught.value.code == "cursor_plan_mismatch"


def test_cursor_payload_enforces_fixed_sort_shape_and_context() -> None:
    with pytest.raises(ValidationError, match="canonical_sort_key"):
        CursorContext(
            release_key=RELEASE,
            release_manifest_sha256=MANIFEST,
            plan_sha256=PLAN_HASH,
            intent="list_loci",
            canonical_sort_key="snapshot_key+term_key",
        )

    with pytest.raises(ValidationError, match="one exact locus_key"):
        CursorPayload(
            release_key=RELEASE,
            release_manifest_sha256=MANIFEST,
            plan_sha256=PLAN_HASH,
            intent="list_loci",
            canonical_sort_key="locus_key",
            last_sort_values=("not-a-locus",),
        )

    assembly_context = CursorContext(
        release_key=RELEASE,
        release_manifest_sha256=MANIFEST,
        plan_sha256=PLAN_HASH,
        intent="list_assemblies",
        canonical_sort_key="assembly_accession_version+assembly_key",
    )
    with pytest.raises(ValidationError, match="same assembly"):
        encode_cursor(
            assembly_context,
            last_sort_values=("GCA_1.1", "assembly:ncbi:GCA_2.1"),
            secret=TEST_SECRET,
        )


def test_hmac_key_material_must_be_explicit_bytes_with_256_bits() -> None:
    with pytest.raises(ValueError, match="at least 32 bytes"):
        encode_cursor(_context(), last_sort_values=(LOCUS,), secret=b"short")
    with pytest.raises(TypeError, match="must be bytes"):
        encode_cursor(
            _context(),
            last_sort_values=(LOCUS,),
            secret="x" * 32,  # type: ignore[arg-type]
        )
