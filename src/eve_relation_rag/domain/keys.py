"""Deterministic public keys for Milestone 1 truth objects.

Keys are hashes of deliberately small, versioned identity payloads.  This
module does not normalize scientific identifiers: callers must supply the
exact, already validated accession.version and source-native token.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

type JsonScalar = None | bool | int | str
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

_NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9-]*(?::[a-z0-9][a-z0-9.-]*)*$")
_ASSEMBLY_ACCESSION_VERSION_RE = re.compile(r"^(?:GCA|GCF)_[0-9]+\.[1-9][0-9]*$")
_CONTIG_ACCESSION_VERSION_RE = re.compile(r"^[A-Z][A-Z0-9_]*[0-9]\.[1-9][0-9]*$")
_RELEASE_KEY_RE = re.compile(
    r"^release:[a-z][a-z0-9-]*:v(?:0|[1-9][0-9]*):(?P<date>[0-9]{8}):[0-9]{3}$"
)

LOCUS_KEY_NAMESPACE = "locus:eve:v1"


class StableKeyError(ValueError):
    """Raised when a value cannot participate in a deterministic key."""


def _canonical_value(value: object, *, path: str = "$") -> JsonValue:
    """Return a JSON-compatible value while rejecting ambiguous inputs."""

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        raise StableKeyError(f"floating-point values are not allowed at {path}")
    if isinstance(value, Mapping):
        canonical: dict[str, JsonValue] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise StableKeyError(f"JSON object keys must be strings at {path}")
            canonical[key] = _canonical_value(child, path=f"{path}.{key}")
        return canonical
    if isinstance(value, (list, tuple)):
        return [
            _canonical_value(child, path=f"{path}[{index}]") for index, child in enumerate(value)
        ]
    raise StableKeyError(f"unsupported canonical JSON value at {path}: {type(value).__name__}")


def canonical_json(value: object) -> str:
    """Serialize *value* as deterministic UTF-8 JSON.

    Mapping keys are sorted, insignificant whitespace is removed, non-ASCII
    text is preserved, and floating-point/non-JSON values are rejected.  The
    stable-key contracts intentionally use only strings and integers, which
    avoids cross-runtime floating-point canonicalization ambiguity.
    """

    canonical = _canonical_value(value)
    return json.dumps(
        canonical,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_json_sha256(value: object) -> str:
    """Return the lowercase SHA-256 hex digest of canonical JSON bytes."""

    encoded = canonical_json(value).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_key(namespace: str, payload: object) -> str:
    """Build ``<namespace>:sha256:<64-lowercase-hex>`` from *payload*."""

    if not _NAMESPACE_RE.fullmatch(namespace):
        raise StableKeyError(f"invalid stable-key namespace: {namespace!r}")
    return f"{namespace}:sha256:{canonical_json_sha256(payload)}"


def is_versioned_assembly_accession(value: str) -> bool:
    """Return whether *value* is an exact NCBI assembly accession.version."""

    return _ASSEMBLY_ACCESSION_VERSION_RE.fullmatch(value) is not None


def is_versioned_contig_accession(value: str) -> bool:
    """Return whether *value* is an exact INSDC-style sequence accession.version."""

    return _CONTIG_ACCESSION_VERSION_RE.fullmatch(value) is not None


def is_release_key(value: str) -> bool:
    """Return whether *value* follows the approved immutable release-key grammar."""

    match = _RELEASE_KEY_RE.fullmatch(value)
    if match is None:
        return False
    date_token = match.group("date")
    try:
        date.fromisoformat(f"{date_token[:4]}-{date_token[4:6]}-{date_token[6:]}")
    except ValueError:
        return False
    return True


def _require_token(name: str, value: str) -> str:
    if not value or value != value.strip():
        raise StableKeyError(f"{name} must be a non-empty exact token without outer whitespace")
    return value


@dataclass(frozen=True, slots=True)
class LocusIdentity:
    """The approved, coordinate-free EVELocus identity preimage."""

    source_snapshot_key: str
    assembly_accession_version: str
    contig_accession_version: str
    native_vr_token: str
    identity_policy_version: str

    def canonical_payload(self) -> dict[str, JsonValue]:
        """Return the complete versioned payload used to derive the locus key."""

        source_snapshot_key = _require_token("source_snapshot_key", self.source_snapshot_key)
        assembly_accession = _require_token(
            "assembly_accession_version", self.assembly_accession_version
        )
        contig_accession = _require_token("contig_accession_version", self.contig_accession_version)
        native_vr_token = _require_token("native_vr_token", self.native_vr_token)
        identity_policy_version = _require_token(
            "identity_policy_version", self.identity_policy_version
        )

        if not is_versioned_assembly_accession(assembly_accession):
            raise StableKeyError(
                "assembly_accession_version must be an exact GCA_/GCF_ accession.version"
            )
        if not is_versioned_contig_accession(contig_accession):
            raise StableKeyError(
                "contig_accession_version must be an exact INSDC accession.version"
            )

        return {
            "assembly_accession_version": assembly_accession,
            "contig_accession_version": contig_accession,
            "identity_policy_version": identity_policy_version,
            "native_vr_token": native_vr_token,
            "source_snapshot_key": source_snapshot_key,
        }

    def key(self) -> str:
        """Return this identity's deterministic public locus key."""

        return stable_key(LOCUS_KEY_NAMESPACE, self.canonical_payload())


def locus_key(
    *,
    source_snapshot_key: str,
    assembly_accession_version: str,
    contig_accession_version: str,
    native_vr_token: str,
    identity_policy_version: str,
) -> str:
    """Build an EVELocus key from the approved coordinate-free preimage."""

    return LocusIdentity(
        source_snapshot_key=source_snapshot_key,
        assembly_accession_version=assembly_accession_version,
        contig_accession_version=contig_accession_version,
        native_vr_token=native_vr_token,
        identity_policy_version=identity_policy_version,
    ).key()
