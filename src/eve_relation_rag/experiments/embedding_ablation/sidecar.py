"""Exact, dimension-safe sidecar vector indexes outside production PostgreSQL."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import struct
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from pydantic import Field, TypeAdapter, model_validator

from eve_relation_rag.experiments.embedding_ablation.contracts import (
    ModelRepresentationContract,
)
from eve_relation_rag.experiments.embedding_ablation.providers import (
    ProviderOutputError,
    validate_embedding_batch,
    validate_embedding_vector,
)
from eve_relation_rag.literature.contracts import ChunkKey, Sha256, StableToken, StrictFrozenSchema
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256

_KEYS_FILE = "chunk_keys.json"
_VECTORS_FILE = "vectors.f32le"
_MANIFEST_FILE = "sidecar_manifest.json"
_EXPECTED_FILES = frozenset({_KEYS_FILE, _VECTORS_FILE, _MANIFEST_FILE})
_CHUNK_KEYS_ADAPTER = TypeAdapter(tuple[ChunkKey, ...])


class SidecarIndexError(RuntimeError):
    """Raised when a sidecar index is unsafe, inconsistent, or incomplete."""


class SidecarIndexManifest(StrictFrozenSchema):
    """Self-checksummed identity of exact vector and ordered-key files."""

    sidecar_schema_version: str = Field(pattern=r"^embedding-ablation-sidecar-v1$")
    model_key: StableToken
    artifact_manifest_sha256: Sha256
    representation: ModelRepresentationContract
    representation_sha256: Sha256
    row_count: int = Field(ge=1)
    dimension: int = Field(ge=1)
    ordered_chunk_keys_sha256: Sha256
    keys_file_sha256: Sha256
    keys_file_size: int = Field(ge=1)
    vectors_file_sha256: Sha256
    vectors_file_size: int = Field(ge=1)
    sidecar_manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_internal_hashes(self) -> Self:
        if self.representation.task_kind != "embedding":
            raise ValueError("sidecar representation must be an embedding contract")
        if self.representation.dimension != self.dimension:
            raise ValueError("sidecar dimension does not match representation")
        if self.representation_sha256 != canonical_json_sha256(self.representation):
            raise ValueError("sidecar representation checksum does not match")
        if self.vectors_file_size != self.row_count * self.dimension * 4:
            raise ValueError("sidecar vector byte size does not match rows and dimension")
        payload = self.model_dump(mode="python")
        del payload["sidecar_manifest_sha256"]
        if self.sidecar_manifest_sha256 != canonical_json_sha256(payload):
            raise ValueError("sidecar manifest checksum does not match")
        return self


class VectorHit(StrictFrozenSchema):
    """One exact dense-ranking hit."""

    chunk_key: ChunkKey
    rank: int = Field(ge=1)
    score: float


@dataclass(frozen=True, slots=True)
class ExactVectorIndex:
    """In-memory exact scorer whose rows are positionally bound to chunk keys."""

    model_key: str
    artifact_manifest_sha256: str
    representation: ModelRepresentationContract
    chunk_keys: tuple[str, ...]
    vectors: tuple[tuple[float, ...], ...]

    @classmethod
    def build(
        cls,
        *,
        model_key: str,
        artifact_manifest_sha256: str,
        representation: ModelRepresentationContract,
        chunk_keys: Sequence[str],
        vectors: Sequence[Sequence[float]],
    ) -> Self:
        """Validate every row and construct an exact index without persistence."""

        keys = tuple(chunk_keys)
        try:
            validated_keys = _CHUNK_KEYS_ADAPTER.validate_python(keys, strict=True)
        except Exception as exc:
            raise SidecarIndexError("sidecar chunk keys are invalid") from exc
        if not validated_keys:
            raise SidecarIndexError("sidecar requires at least one chunk")
        if validated_keys != tuple(sorted(validated_keys)):
            raise SidecarIndexError("sidecar chunk keys must be in canonical sorted order")
        if len(validated_keys) != len(set(validated_keys)):
            raise SidecarIndexError("sidecar chunk keys must be unique")
        try:
            validated_vectors = validate_embedding_batch(
                vectors,
                expected_count=len(validated_keys),
                representation=representation,
            )
        except ProviderOutputError as exc:
            raise SidecarIndexError(str(exc)) from exc
        if not model_key or any(character.isspace() for character in model_key):
            raise SidecarIndexError("sidecar model key is invalid")
        if len(artifact_manifest_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in artifact_manifest_sha256
        ):
            raise SidecarIndexError("sidecar artifact manifest checksum is invalid")
        return cls(
            model_key=model_key,
            artifact_manifest_sha256=artifact_manifest_sha256,
            representation=representation,
            chunk_keys=validated_keys,
            vectors=validated_vectors,
        )

    def rank(
        self,
        query_vector: Sequence[float],
        *,
        allowed_chunk_keys: Iterable[str] | None = None,
        limit: int = 100,
    ) -> tuple[VectorHit, ...]:
        """Rank exact cosine or dot-product scores with a chunk-key tie break."""

        if limit < 1:
            raise SidecarIndexError("dense ranking limit must be positive")
        try:
            query = validate_embedding_vector(
                query_vector,
                representation=self.representation,
            )
        except ProviderOutputError as exc:
            raise SidecarIndexError(str(exc)) from exc
        allowed = None if allowed_chunk_keys is None else frozenset(allowed_chunk_keys)
        known = frozenset(self.chunk_keys)
        if allowed is not None and not allowed <= known:
            raise SidecarIndexError("dense ranking filter contains an unknown chunk key")

        scored: list[tuple[str, float]] = []
        for chunk_key, vector in zip(self.chunk_keys, self.vectors, strict=True):
            if allowed is not None and chunk_key not in allowed:
                continue
            dot_product = math.fsum(left * right for left, right in zip(query, vector, strict=True))
            if self.representation.similarity == "cosine":
                if self.representation.normalization == "l2":
                    score = dot_product
                else:
                    query_norm = math.sqrt(math.fsum(value * value for value in query))
                    vector_norm = math.sqrt(math.fsum(value * value for value in vector))
                    if query_norm == 0.0 or vector_norm == 0.0:
                        raise SidecarIndexError("cosine similarity cannot score a zero vector")
                    score = dot_product / (query_norm * vector_norm)
            elif self.representation.similarity == "dot_product":
                score = dot_product
            else:
                raise SidecarIndexError("sidecar embedding similarity is not scoreable")
            if not math.isfinite(score):
                raise SidecarIndexError("dense scorer produced a non-finite score")
            scored.append((chunk_key, score))

        ordered = sorted(scored, key=lambda item: (-item[1], item[0]))[:limit]
        return tuple(
            VectorHit(chunk_key=chunk_key, rank=rank, score=score)
            for rank, (chunk_key, score) in enumerate(ordered, start=1)
        )

    @property
    def vector_bytes(self) -> bytes:
        dimension = self.representation.dimension
        if dimension is None:
            raise SidecarIndexError("sidecar representation has no dimension")
        flattened = (value for vector in self.vectors for value in vector)
        return struct.pack(f"<{len(self.vectors) * dimension}f", *flattened)


def write_sidecar_index(target_directory: Path, index: ExactVectorIndex) -> SidecarIndexManifest:
    """Atomically create a new sidecar directory and refuse every overwrite."""

    if target_directory.is_symlink() or target_directory.exists():
        raise SidecarIndexError("sidecar target already exists or is a symbolic link")
    try:
        parent = target_directory.parent.resolve(strict=True)
    except OSError as exc:
        raise SidecarIndexError("sidecar parent directory does not exist") from exc
    if not parent.is_dir():
        raise SidecarIndexError("sidecar parent is not a directory")

    keys_bytes = canonical_json_bytes(list(index.chunk_keys))
    vector_bytes = index.vector_bytes
    payload: dict[str, object] = {
        "sidecar_schema_version": "embedding-ablation-sidecar-v1",
        "model_key": index.model_key,
        "artifact_manifest_sha256": index.artifact_manifest_sha256,
        "representation": index.representation,
        "representation_sha256": canonical_json_sha256(index.representation),
        "row_count": len(index.chunk_keys),
        "dimension": index.representation.dimension,
        "ordered_chunk_keys_sha256": canonical_json_sha256(index.chunk_keys),
        "keys_file_sha256": hashlib.sha256(keys_bytes).hexdigest(),
        "keys_file_size": len(keys_bytes),
        "vectors_file_sha256": hashlib.sha256(vector_bytes).hexdigest(),
        "vectors_file_size": len(vector_bytes),
    }
    manifest = SidecarIndexManifest.model_validate(
        {
            **payload,
            "sidecar_manifest_sha256": canonical_json_sha256(payload),
        }
    )
    manifest_bytes = canonical_json_bytes(manifest)

    temporary = Path(tempfile.mkdtemp(prefix=f".{target_directory.name}.", dir=parent))
    try:
        (temporary / _KEYS_FILE).write_bytes(keys_bytes)
        (temporary / _VECTORS_FILE).write_bytes(vector_bytes)
        (temporary / _MANIFEST_FILE).write_bytes(manifest_bytes)
        os.rename(temporary, target_directory)
    except Exception as exc:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise SidecarIndexError("failed to create sidecar index atomically") from exc
    return manifest


def load_sidecar_index(
    directory: Path,
    *,
    expected_model_key: str,
    expected_artifact_manifest_sha256: str,
    expected_dimension: int,
) -> tuple[ExactVectorIndex, SidecarIndexManifest]:
    """Load an exact sidecar only after all file, identity, and checksum checks pass."""

    if directory.is_symlink():
        raise SidecarIndexError("sidecar directory must not be a symbolic link")
    try:
        root = directory.resolve(strict=True)
    except OSError as exc:
        raise SidecarIndexError("sidecar directory does not exist") from exc
    if not root.is_dir():
        raise SidecarIndexError("sidecar path must be a directory")
    actual_files: set[str] = set()
    for path in root.iterdir():
        if path.is_symlink() or not path.is_file():
            raise SidecarIndexError("sidecar contains a non-regular entry")
        actual_files.add(path.name)
    if actual_files != _EXPECTED_FILES:
        raise SidecarIndexError("sidecar file set is incomplete or contains extras")

    try:
        manifest = SidecarIndexManifest.model_validate_json((root / _MANIFEST_FILE).read_bytes())
    except Exception as exc:
        raise SidecarIndexError("sidecar manifest is invalid") from exc
    if (
        manifest.model_key != expected_model_key
        or manifest.artifact_manifest_sha256 != expected_artifact_manifest_sha256
        or manifest.dimension != expected_dimension
    ):
        raise SidecarIndexError("sidecar identity does not match the requested system")

    keys_bytes = _read_and_verify(
        root / _KEYS_FILE,
        expected_size=manifest.keys_file_size,
        expected_sha256=manifest.keys_file_sha256,
    )
    vector_bytes = _read_and_verify(
        root / _VECTORS_FILE,
        expected_size=manifest.vectors_file_size,
        expected_sha256=manifest.vectors_file_sha256,
    )
    try:
        decoded_keys = json.loads(keys_bytes)
        if not isinstance(decoded_keys, list):
            raise TypeError("sidecar chunk keys must be a JSON array")
        keys = _CHUNK_KEYS_ADAPTER.validate_python(tuple(decoded_keys), strict=True)
    except Exception as exc:
        raise SidecarIndexError("sidecar chunk-key file is invalid") from exc
    if canonical_json_sha256(keys) != manifest.ordered_chunk_keys_sha256:
        raise SidecarIndexError("sidecar ordered chunk keys do not match the manifest")
    try:
        flat_values = tuple(value[0] for value in struct.iter_unpack("<f", vector_bytes))
    except struct.error as exc:
        raise SidecarIndexError("sidecar vector bytes are malformed") from exc
    vectors = tuple(
        tuple(flat_values[offset : offset + manifest.dimension])
        for offset in range(0, len(flat_values), manifest.dimension)
    )
    index = ExactVectorIndex.build(
        model_key=manifest.model_key,
        artifact_manifest_sha256=manifest.artifact_manifest_sha256,
        representation=manifest.representation,
        chunk_keys=keys,
        vectors=vectors,
    )
    return index, manifest


def sidecar_size_bytes(directory: Path) -> int:
    """Return the actual size of the three verified sidecar files."""

    return sum((directory / name).stat().st_size for name in _EXPECTED_FILES)


def _read_and_verify(path: Path, *, expected_size: int, expected_sha256: str) -> bytes:
    try:
        value = path.read_bytes()
    except OSError as exc:
        raise SidecarIndexError("sidecar file cannot be read") from exc
    if len(value) != expected_size or hashlib.sha256(value).hexdigest() != expected_sha256:
        raise SidecarIndexError("sidecar file checksum or size mismatch")
    return value
