"""Authenticated, release-bound forward keyset cursors.

The codec is deliberately independent of HTTP, databases, and release lookup.  A
caller must first obtain the exact published-release manifest digest and the
canonical plan hash, then pass that context explicitly.  Decoding never grants
publication authority and never performs fact retrieval.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import unicodedata
from typing import Annotated, Final, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator

from eve_relation_rag.domain.keys import canonical_json, is_release_key

CURSOR_VERSION: Final = "endoviho-keyset-cursor-v1"
MINIMUM_HMAC_SECRET_BYTES: Final = 32

type ListIntent = Literal["list_loci", "list_assemblies", "list_source_taxa"]
type CanonicalSortKey = Literal[
    "locus_key",
    "assembly_accession_version+assembly_key",
    "snapshot_key+term_key",
]
type CursorErrorCode = Literal["cursor_invalid", "cursor_plan_mismatch"]

_TOKEN_RE: Final = re.compile(r"^[A-Za-z0-9_-]{1,4096}$")
_LOCUS_KEY_RE: Final = re.compile(r"^locus:eve:v1:sha256:[0-9a-f]{64}$")
_ASSEMBLY_ACCESSION_RE: Final = re.compile(r"^(?:GCA|GCF)_[0-9]+\.[1-9][0-9]*$")
_RELEASE_PREFIX: Final = "release:endoviho-rag:v0:"
_SIGNATURE_SIZE: Final = hashlib.sha256().digest_size

SORT_KEY_BY_INTENT: Final[dict[ListIntent, CanonicalSortKey]] = {
    "list_loci": "locus_key",
    "list_assemblies": "assembly_accession_version+assembly_key",
    "list_source_taxa": "snapshot_key+term_key",
}


def _validate_stable_token(value: str) -> str:
    if any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in value
    ):
        raise ValueError("cursor sort values must not contain whitespace or control characters")
    return value


type Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
type StableSortValue = Annotated[
    str,
    Field(min_length=1, max_length=255),
    AfterValidator(_validate_stable_token),
]


class _CursorModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CursorContext(_CursorModel):
    """All request/release semantics to which a cursor is bound."""

    release_key: str = Field(min_length=1, max_length=255)
    release_manifest_sha256: Sha256
    plan_sha256: Sha256
    intent: ListIntent
    canonical_sort_key: CanonicalSortKey

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        if not self.release_key.startswith(_RELEASE_PREFIX) or not is_release_key(self.release_key):
            raise ValueError("release_key does not follow the approved immutable grammar")
        if self.canonical_sort_key != SORT_KEY_BY_INTENT[self.intent]:
            raise ValueError("intent and canonical_sort_key are inconsistent")
        return self


class CursorPayload(CursorContext):
    """The complete canonical JSON object covered by the cursor HMAC."""

    cursor_version: Literal["endoviho-keyset-cursor-v1"] = CURSOR_VERSION
    last_sort_values: tuple[StableSortValue, ...] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def validate_last_sort_values(self) -> Self:
        values = self.last_sort_values
        if self.intent == "list_loci":
            if len(values) != 1 or _LOCUS_KEY_RE.fullmatch(values[0]) is None:
                raise ValueError("list_loci cursors require one exact locus_key")
        elif self.intent == "list_assemblies":
            if len(values) != 2 or _ASSEMBLY_ACCESSION_RE.fullmatch(values[0]) is None:
                raise ValueError(
                    "list_assemblies cursors require accession.version and assembly_key"
                )
            if values[1] != f"assembly:ncbi:{values[0]}":
                raise ValueError("assembly cursor values do not identify the same assembly")
        elif len(values) != 2:
            raise ValueError("list_source_taxa cursors require snapshot_key and term_key")
        return self

    def context(self) -> CursorContext:
        """Return the signed request/release portion without the page position."""

        return CursorContext(
            release_key=self.release_key,
            release_manifest_sha256=self.release_manifest_sha256,
            plan_sha256=self.plan_sha256,
            intent=self.intent,
            canonical_sort_key=self.canonical_sort_key,
        )


class CursorError(ValueError):
    """Safe cursor refusal suitable for translation to a StructuredError code."""

    code: CursorErrorCode

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class CursorInvalidError(CursorError):
    """The cursor is malformed, unauthenticated, or uses an unsupported version."""

    code: Literal["cursor_invalid"] = "cursor_invalid"


class CursorPlanMismatchError(CursorError):
    """The valid signed cursor belongs to different request or release semantics."""

    code: Literal["cursor_plan_mismatch"] = "cursor_plan_mismatch"


def validate_cursor_secret(secret: bytes) -> bytes:
    """Validate runtime-only HMAC key material and return it unchanged."""

    if type(secret) is not bytes:
        raise TypeError("cursor HMAC secret must be bytes")
    if len(secret) < MINIMUM_HMAC_SECRET_BYTES:
        raise ValueError(
            f"cursor HMAC secret must contain at least {MINIMUM_HMAC_SECRET_BYTES} bytes"
        )
    return secret


def _payload_bytes(payload: CursorPayload) -> bytes:
    return canonical_json(payload.model_dump(mode="json")).encode("utf-8")


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(token: str) -> bytes:
    if type(token) is not str or _TOKEN_RE.fullmatch(token) is None:
        raise CursorInvalidError("The cursor is malformed or unauthenticated.")
    padding = "=" * (-len(token) % 4)
    try:
        raw = base64.b64decode(token + padding, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise CursorInvalidError("The cursor is malformed or unauthenticated.") from exc
    if _base64url_encode(raw) != token:
        raise CursorInvalidError("The cursor is malformed or unauthenticated.")
    return raw


def encode_cursor(
    context: CursorContext,
    *,
    last_sort_values: tuple[str, ...],
    secret: bytes,
) -> str:
    """Return one deterministic unpadded base64url HMAC-SHA-256 cursor."""

    secret = validate_cursor_secret(secret)
    payload = CursorPayload(
        cursor_version=CURSOR_VERSION,
        release_key=context.release_key,
        release_manifest_sha256=context.release_manifest_sha256,
        plan_sha256=context.plan_sha256,
        intent=context.intent,
        canonical_sort_key=context.canonical_sort_key,
        last_sort_values=last_sort_values,
    )
    encoded_payload = _payload_bytes(payload)
    signature = hmac.new(secret, encoded_payload, hashlib.sha256).digest()
    return _base64url_encode(encoded_payload + b"." + signature)


def decode_cursor(
    token: str,
    *,
    expected_context: CursorContext,
    secret: bytes,
) -> CursorPayload:
    """Authenticate and context-check one cursor, failing closed on any drift."""

    secret = validate_cursor_secret(secret)
    raw = _base64url_decode(token)
    if len(raw) <= _SIGNATURE_SIZE + 1 or raw[-(_SIGNATURE_SIZE + 1)] != ord("."):
        raise CursorInvalidError("The cursor is malformed or unauthenticated.")

    encoded_payload = raw[: -(_SIGNATURE_SIZE + 1)]
    signature = raw[-_SIGNATURE_SIZE:]
    expected_signature = hmac.new(secret, encoded_payload, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected_signature):
        raise CursorInvalidError("The cursor is malformed or unauthenticated.")

    try:
        # JSON arrays are the canonical wire representation of immutable Python
        # tuples.  Pydantic's JSON validator preserves strict scalar validation
        # while performing only that transport conversion.
        payload = CursorPayload.model_validate_json(encoded_payload)
    except ValidationError as exc:
        raise CursorInvalidError("The cursor is malformed or unauthenticated.") from exc

    if _payload_bytes(payload) != encoded_payload:
        raise CursorInvalidError("The cursor is malformed or unauthenticated.")
    if payload.context() != expected_context:
        raise CursorPlanMismatchError(
            "The cursor does not match the requested release and query plan."
        )
    return payload


__all__ = [
    "CURSOR_VERSION",
    "MINIMUM_HMAC_SECRET_BYTES",
    "SORT_KEY_BY_INTENT",
    "CanonicalSortKey",
    "CursorContext",
    "CursorError",
    "CursorErrorCode",
    "CursorInvalidError",
    "CursorPayload",
    "CursorPlanMismatchError",
    "ListIntent",
    "decode_cursor",
    "encode_cursor",
    "validate_cursor_secret",
]
