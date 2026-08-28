"""Dependency-free provider interfaces approved for Milestone 3."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from eve_relation_rag.literature.contracts import EMBEDDING_MODEL_KEY


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Structural boundary for document and query embedding providers.

    The protocol deliberately performs no import or initialization of a model runtime.
    Later stages validate dimensions, finiteness, normalization, and truncation before
    accepting any returned vector.
    """

    @property
    def model_key(self) -> str:
        """Return the exact model-and-processing policy key."""

        ...

    @property
    def dimension(self) -> int:
        """Return the provider's declared output dimension."""

        ...

    @property
    def artifact_manifest_sha256(self) -> str:
        """Return the SHA-256 of the exact verified model artifact manifest."""

        ...

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Embed passage text without a query instruction."""

        ...

    def embed_query(self, text: str) -> Sequence[float]:
        """Embed one query using the provider's exact query policy."""

        ...


class DeterministicFakeEmbeddingProvider:
    """Offline 384-dimensional provider for deterministic tests only.

    This provider deliberately shares the pinned model identity so the production persistence
    path can be tested without loading model weights. It is not permitted for a trusted pilot
    benchmark or validation receipt.
    """

    @property
    def model_key(self) -> str:
        return EMBEDDING_MODEL_KEY

    @property
    def dimension(self) -> int:
        return 384

    @property
    def artifact_manifest_sha256(self) -> str:
        """Return the synthetic artifact identity reserved for deterministic tests."""

        return "f" * 64

    def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector(text) for text in texts)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> tuple[float, ...]:
        seed = text.encode("utf-8")
        components: list[float] = []
        counter = 0
        while len(components) < 384:
            digest = hashlib.sha256(seed + counter.to_bytes(4, "little")).digest()
            components.extend((value - 127.5) / 127.5 for value in digest)
            counter += 1
        components = components[:384]
        norm = math.sqrt(math.fsum(value * value for value in components))
        return tuple(value / norm for value in components)
