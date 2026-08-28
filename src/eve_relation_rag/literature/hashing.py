"""Canonical Unicode-aware hashing for Milestone 3 literature identities."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

from pydantic import BaseModel

from eve_relation_rag.literature.contracts import (
    RETRIEVAL_POLICY_KEY,
    DocumentKeyPreimage,
    LiteratureRetrievalRequest,
    RetrievalAnchor,
)

type CanonicalScalar = None | bool | int | float | str
type CanonicalValue = CanonicalScalar | list[CanonicalValue] | dict[str, CanonicalValue]

_NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9-]*(?::[a-z0-9][a-z0-9.-]*)*$")


class CanonicalHashError(ValueError):
    """Raised when a value has no unambiguous approved canonical JSON form."""


def _canonical_value(value: object, *, path: str = "$") -> CanonicalValue:
    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump(mode="python"), path=path)
    if isinstance(value, Enum):
        return _canonical_value(value.value, path=path)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalHashError(f"non-finite number at {path}")
        return value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        canonical: dict[str, CanonicalValue] = {}
        source_keys: dict[str, str] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise CanonicalHashError(f"JSON object keys must be strings at {path}")
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in canonical:
                first = source_keys[normalized_key]
                raise CanonicalHashError(
                    f"object keys {first!r} and {key!r} collide after Unicode NFC at {path}"
                )
            source_keys[normalized_key] = key
            canonical[normalized_key] = _canonical_value(child, path=f"{path}.{normalized_key}")
        return canonical
    if isinstance(value, (list, tuple)):
        return [
            _canonical_value(child, path=f"{path}[{index}]") for index, child in enumerate(value)
        ]
    raise CanonicalHashError(f"unsupported canonical JSON value at {path}: {type(value).__name__}")


def canonical_json_bytes(value: object) -> bytes:
    """Return sorted, compact, Unicode-NFC canonical UTF-8 JSON bytes."""

    canonical = _canonical_value(value)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return encoded.encode("utf-8")


def canonical_json_sha256(value: object) -> str:
    """Return the lowercase SHA-256 of approved canonical JSON bytes."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _stable_key(namespace: str, preimage: object) -> str:
    if _NAMESPACE_RE.fullmatch(namespace) is None:
        raise CanonicalHashError(f"invalid stable-key namespace: {namespace!r}")
    return f"{namespace}:sha256:{canonical_json_sha256(preimage)}"


def document_key(preimage: DocumentKeyPreimage) -> str:
    """Build an approved immutable document key."""

    return _stable_key("document", preimage)


def chunk_key(preimage: object) -> str:
    """Build an approved corpus-scoped chunk key."""

    return _stable_key("chunk", preimage)


def anchor_key(preimage: object) -> str:
    """Build an approved curated-anchor key."""

    return _stable_key("anchor", preimage)


def corpus_import_run_key(preimage: object) -> str:
    """Build an approved immutable corpus-import run key."""

    return _stable_key("corpus-import", preimage)


def corpus_receipt_key(preimage: object) -> str:
    """Build an approved immutable corpus-validation receipt key."""

    return _stable_key("corpus-receipt", preimage)


def canonical_manifest_sha256(manifest: BaseModel | Mapping[str, object]) -> str:
    """Hash a manifest payload while excluding its non-self-referential digest field."""

    if isinstance(manifest, BaseModel):
        payload: dict[str, Any] = manifest.model_dump(mode="python")
    else:
        payload = dict(manifest)
    if "manifest_sha256" not in payload:
        raise CanonicalHashError("manifest payload is missing manifest_sha256")
    del payload["manifest_sha256"]
    return canonical_json_sha256(payload)


def canonical_query_sha256(
    request: LiteratureRetrievalRequest,
    system_anchors: Sequence[RetrievalAnchor],
) -> str:
    """Hash every approved retrieval input with anchors in canonical key order."""

    anchors = tuple(sorted(system_anchors, key=lambda anchor: anchor.anchor_key))
    keys = tuple(anchor.anchor_key for anchor in anchors)
    if len(keys) != len(set(keys)):
        raise CanonicalHashError("query contains a duplicate system anchor")
    payload = {
        "corpus_release_key": request.corpus_release_key,
        "question": request.question,
        "retrieval_policy_key": RETRIEVAL_POLICY_KEY,
        "system_anchors": [anchor.model_dump(mode="python") for anchor in anchors],
        "top_k": request.top_k,
    }
    return canonical_json_sha256(payload)
