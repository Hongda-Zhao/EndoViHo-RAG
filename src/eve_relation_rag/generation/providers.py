"""Dependency-free LLM provider boundary for Milestone 4."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from eve_relation_rag.hybrid.contracts import ProviderIdentity


@runtime_checkable
class LLMProvider(Protocol):
    """A provider receives exactly one canonical ContextPack JSON value."""

    @property
    def identity(self) -> ProviderIdentity:
        """Return the complete runtime identity before any generation call."""
        ...

    def generate(self, context_json: str) -> str:
        """Return one UTF-8 JSON draft without tools, streaming, or retries.

        Implementations must enforce ``identity.timeout_seconds`` around their own I/O and
        raise on expiry.  The composer deliberately performs one call and never retries.
        """
        ...


class LLMProviderError(RuntimeError):
    """Sanitized provider-boundary failure."""


class LLMProviderUnavailable(LLMProviderError):
    """No approved provider is available for production generation."""


class LLMProviderFailure(LLMProviderError):
    """An invoked provider failed without exposing its internal exception."""


__all__ = [
    "LLMProvider",
    "LLMProviderError",
    "LLMProviderFailure",
    "LLMProviderUnavailable",
]
