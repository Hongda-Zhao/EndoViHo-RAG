"""Server-owned exact DatasetRelease-to-CorpusRelease binding registry."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from eve_relation_rag.hybrid.contracts import (
    HybridReleaseBinding,
    HybridReleaseBindingManifest,
)


@dataclass(frozen=True, slots=True)
class HybridBindingRefusal(Exception):
    """Sanitized fail-closed refusal for an absent or invalid approved binding."""

    code: str
    message: str

    def __str__(self) -> str:
        return self.message


class HybridBindingRegistry(Protocol):
    """Resolve only an explicitly approved exact release pair."""

    def authorize(
        self,
        release_key: str,
        corpus_release_key: str,
    ) -> HybridReleaseBinding: ...


class ApprovedHybridBindingRegistry:
    """Immutable in-memory index loaded from one checksum-pinned local manifest."""

    def __init__(self, manifest: HybridReleaseBindingManifest) -> None:
        self._manifest = HybridReleaseBindingManifest.model_validate_json(
            manifest.model_dump_json()
        )
        self._by_pair = {
            (binding.release_key, binding.corpus_release_key): binding
            for binding in self._manifest.bindings
        }

    @property
    def manifest_sha256(self) -> str:
        return self._manifest.manifest_sha256

    @classmethod
    def from_file(
        cls,
        path: Path,
        *,
        approved_manifest_sha256: str,
    ) -> ApprovedHybridBindingRegistry:
        """Load one strict manifest only when its self hash is independently approved."""

        try:
            manifest = HybridReleaseBindingManifest.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ValidationError) as exc:
            raise HybridBindingRefusal(
                "hybrid_binding_unavailable",
                "The approved hybrid release binding manifest is unavailable or invalid.",
            ) from exc
        if manifest.manifest_sha256 != approved_manifest_sha256:
            raise HybridBindingRefusal(
                "hybrid_binding_unavailable",
                "The hybrid release binding manifest does not match the approved checksum.",
            )
        return cls(manifest)

    def authorize(
        self,
        release_key: str,
        corpus_release_key: str,
    ) -> HybridReleaseBinding:
        try:
            return self._by_pair[(release_key, corpus_release_key)]
        except KeyError as exc:
            raise HybridBindingRefusal(
                "hybrid_binding_unavailable",
                "The exact dataset and corpus release pair is not approved.",
            ) from exc


class UnavailableHybridBindingRegistry:
    """Production-safe default while no real release-pair manifest is approved."""

    def authorize(
        self,
        release_key: str,
        corpus_release_key: str,
    ) -> HybridReleaseBinding:
        del release_key, corpus_release_key
        raise HybridBindingRefusal(
            "hybrid_binding_unavailable",
            "No approved hybrid release binding manifest is configured.",
        )


class ConfiguredHybridBindingRegistry:
    """Delay manifest file I/O until an actual hybrid route requests authorization."""

    def __init__(self, path: Path, *, approved_manifest_sha256: str) -> None:
        self._path = path
        self._approved_manifest_sha256 = approved_manifest_sha256

    @cached_property
    def _approved(self) -> ApprovedHybridBindingRegistry:
        return ApprovedHybridBindingRegistry.from_file(
            self._path,
            approved_manifest_sha256=self._approved_manifest_sha256,
        )

    def authorize(
        self,
        release_key: str,
        corpus_release_key: str,
    ) -> HybridReleaseBinding:
        return self._approved.authorize(release_key, corpus_release_key)


__all__ = [
    "ApprovedHybridBindingRegistry",
    "ConfiguredHybridBindingRegistry",
    "HybridBindingRefusal",
    "HybridBindingRegistry",
    "UnavailableHybridBindingRegistry",
]
