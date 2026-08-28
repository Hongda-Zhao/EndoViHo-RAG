"""Verified local-only adapter for the pinned BGE embedding model."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from eve_relation_rag.literature.chunking import TokenSpan
from eve_relation_rag.literature.contracts import (
    EMBEDDING_MODEL_KEY,
    EMBEDDING_QUERY_PREFIX,
    EMBEDDING_REPOSITORY_ID,
    EMBEDDING_REVISION,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_MANIFEST_KEYS = {
    "dimension",
    "files",
    "l2_normalized",
    "license_key",
    "manifest_schema_version",
    "max_sequence_tokens",
    "model_key",
    "passage_prefix",
    "pooling",
    "query_prefix",
    "repository_id",
    "revision",
    "similarity",
}
_ARTIFACT_MANIFEST_IDENTITY: dict[str, object] = {
    "dimension": 384,
    "l2_normalized": True,
    "license_key": "MIT",
    "manifest_schema_version": "embedding-artifact-manifest-v1",
    "max_sequence_tokens": 512,
    "model_key": EMBEDDING_MODEL_KEY,
    "passage_prefix": "",
    "pooling": "cls",
    "query_prefix": EMBEDDING_QUERY_PREFIX,
    "repository_id": EMBEDDING_REPOSITORY_ID,
    "revision": EMBEDDING_REVISION,
    "similarity": "cosine",
}


class LocalBgeConfigurationError(RuntimeError):
    """Raised before model use when local provenance or runtime requirements fail."""


class LocalBgeProvider:
    """Sentence Transformers adapter that forbids repository/network model resolution."""

    def __init__(
        self,
        model_directory: Path,
        *,
        artifact_manifest_path: Path | None = None,
        approved_artifact_manifest_sha256: str | None = None,
    ) -> None:
        if model_directory.is_symlink():
            raise LocalBgeConfigurationError("model directory must not be a symbolic link")
        try:
            resolved = model_directory.resolve(strict=True)
        except OSError as exc:
            raise LocalBgeConfigurationError(
                "verified local model directory does not exist"
            ) from exc
        if not resolved.is_dir():
            raise LocalBgeConfigurationError("model path must be a directory")
        if artifact_manifest_path is None or approved_artifact_manifest_sha256 is None:
            raise LocalBgeConfigurationError(
                "an approved local model artifact manifest is required"
            )
        self._artifact_manifest_sha256 = verify_model_artifact_manifest(
            resolved,
            artifact_manifest_path,
            approved_artifact_manifest_sha256,
        )

        try:
            sentence_transformers = importlib.import_module("sentence_transformers")
            sentence_transformer = sentence_transformers.SentenceTransformer
        except (AttributeError, ImportError) as exc:
            raise LocalBgeConfigurationError(
                "install the local-embeddings optional dependency"
            ) from exc
        try:
            self._model: Any = sentence_transformer(
                str(resolved),
                local_files_only=True,
                trust_remote_code=False,
            )
        except Exception as exc:
            raise LocalBgeConfigurationError("failed to load the verified local model") from exc
        dimension_getter = getattr(self._model, "get_embedding_dimension", None)
        observed_dimension = (
            dimension_getter()
            if callable(dimension_getter)
            else self._model.get_sentence_embedding_dimension()
        )
        if observed_dimension != 384:
            raise LocalBgeConfigurationError("local model embedding dimension is not 384")
        if self._model.max_seq_length != 512:
            raise LocalBgeConfigurationError("local model max sequence length is not 512")

    @property
    def model_key(self) -> str:
        return EMBEDDING_MODEL_KEY

    @property
    def dimension(self) -> int:
        return 384

    @property
    def artifact_manifest_sha256(self) -> str:
        """Return the checksum whose manifest and listed files were verified at startup."""

        return self._artifact_manifest_sha256

    def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return self._encode(tuple(texts))

    def embed_query(self, text: str) -> tuple[float, ...]:
        query = f"{EMBEDDING_QUERY_PREFIX}{text}"
        tokenized = self._model.tokenizer(query, add_special_tokens=True, truncation=False)
        if len(tokenized["input_ids"]) > 512:
            raise LocalBgeConfigurationError("query plus approved prefix exceeds 512 tokens")
        return self._encode((query,))[0]

    def token_spans(self, text: str) -> tuple[TokenSpan, ...]:
        encoded = self._model.tokenizer(
            text,
            add_special_tokens=False,
            return_offsets_mapping=True,
            truncation=False,
        )
        offsets = encoded["offset_mapping"]
        return tuple(
            TokenSpan(token_index=index, char_start=int(start), char_end=int(end))
            for index, (start, end) in enumerate(offsets)
            if int(end) > int(start)
        )

    def _encode(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        try:
            encoded = self._model.encode(
                list(texts),
                batch_size=min(500, max(1, len(texts))),
                convert_to_numpy=True,
                normalize_embeddings=True,
                precision="float32",
                show_progress_bar=False,
            )
        except Exception as exc:
            raise LocalBgeConfigurationError("local model encoding failed") from exc
        return tuple(tuple(float(value) for value in vector) for vector in encoded)


def verify_model_artifact_manifest(
    model_directory: Path,
    manifest_path: Path,
    approved_manifest_sha256: str,
) -> str:
    """Verify a checksum-pinned JSON list of every local model file."""

    if manifest_path.is_symlink():
        raise LocalBgeConfigurationError("model artifact manifest must not be a symbolic link")
    try:
        raw = manifest_path.read_bytes()
    except OSError as exc:
        raise LocalBgeConfigurationError("model artifact manifest is unavailable") from exc
    if not _SHA256_RE.fullmatch(approved_manifest_sha256):
        raise LocalBgeConfigurationError("approved model artifact checksum is invalid")
    observed_manifest_sha256 = hashlib.sha256(raw).hexdigest()
    if observed_manifest_sha256 != approved_manifest_sha256:
        raise LocalBgeConfigurationError("model artifact manifest SHA-256 is not approved")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LocalBgeConfigurationError("model artifact manifest is malformed") from exc
    if not isinstance(payload, dict) or set(payload) != _ARTIFACT_MANIFEST_KEYS:
        raise LocalBgeConfigurationError("model artifact manifest schema is not exact")
    for field, expected in _ARTIFACT_MANIFEST_IDENTITY.items():
        if payload[field] != expected or type(payload[field]) is not type(expected):
            raise LocalBgeConfigurationError(
                f"model artifact manifest has an invalid {field}"
            )
    files = payload["files"]
    if not isinstance(files, list) or not files:
        raise LocalBgeConfigurationError("model artifact manifest contains no files")

    root = model_directory.resolve(strict=True)
    listed_paths: set[str] = set()
    for item in files:
        if not isinstance(item, dict) or set(item) != {"byte_size", "relative_path", "sha256"}:
            raise LocalBgeConfigurationError("model artifact manifest file row is malformed")
        relative_path = item.get("relative_path")
        byte_size = item.get("byte_size")
        sha256 = item.get("sha256")
        relative = PurePosixPath(relative_path) if isinstance(relative_path, str) else None
        if (
            not isinstance(relative_path, str)
            or relative is None
            or not relative_path
            or relative.is_absolute()
            or relative.as_posix() != relative_path
            or any(part in {"", ".", ".."} for part in relative.parts)
            or type(byte_size) is not int
            or byte_size < 0
            or not isinstance(sha256, str)
            or not _SHA256_RE.fullmatch(sha256)
            or relative_path in listed_paths
        ):
            raise LocalBgeConfigurationError("model artifact manifest file identity is invalid")
        candidate = root / relative_path
        if candidate.is_symlink():
            raise LocalBgeConfigurationError("model artifact must not be a symbolic link")
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise LocalBgeConfigurationError("listed model artifact is missing") from exc
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise LocalBgeConfigurationError("model artifact escapes the verified directory")
        artifact = resolved.read_bytes()
        if len(artifact) != byte_size or hashlib.sha256(artifact).hexdigest() != sha256:
            raise LocalBgeConfigurationError("model artifact checksum or size mismatch")
        listed_paths.add(relative_path)

    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.resolve() != manifest_path.resolve()
    }
    if actual_paths != listed_paths:
        raise LocalBgeConfigurationError("model artifact manifest does not enumerate every file")
    return observed_manifest_sha256
