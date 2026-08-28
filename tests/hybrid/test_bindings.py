from __future__ import annotations

from pathlib import Path

import pytest

from eve_relation_rag.hybrid.bindings import (
    ApprovedHybridBindingRegistry,
    ConfiguredHybridBindingRegistry,
    HybridBindingRefusal,
    UnavailableHybridBindingRegistry,
)
from eve_relation_rag.hybrid.contracts import (
    BINDING_MANIFEST_VERSION,
    HybridReleaseBinding,
    HybridReleaseBindingManifest,
    canonical_self_sha256,
)

RELEASE_KEY = "release:endoviho-rag:v0:20991231:999"
CORPUS_KEY = "corpus:endoviho-rag:v0:20991231:999"


def _manifest() -> HybridReleaseBindingManifest:
    payload: dict[str, object] = {
        "binding_schema_version": BINDING_MANIFEST_VERSION,
        "bindings": (
            HybridReleaseBinding(
                release_key=RELEASE_KEY,
                release_manifest_sha256="a" * 64,
                corpus_release_key=CORPUS_KEY,
                corpus_manifest_sha256="b" * 64,
            ),
        ),
        "manifest_sha256": "0" * 64,
    }
    payload["manifest_sha256"] = canonical_self_sha256(payload, "manifest_sha256")
    return HybridReleaseBindingManifest.model_validate(payload)


def test_approved_registry_loads_only_the_checksum_pinned_manifest(tmp_path: Path) -> None:
    manifest = _manifest()
    path = tmp_path / "bindings.json"
    path.write_text(manifest.model_dump_json(), encoding="utf-8")

    registry = ApprovedHybridBindingRegistry.from_file(
        path,
        approved_manifest_sha256=manifest.manifest_sha256,
    )

    assert registry.authorize(RELEASE_KEY, CORPUS_KEY) == manifest.bindings[0]

    with pytest.raises(HybridBindingRefusal) as mismatch:
        ApprovedHybridBindingRegistry.from_file(
            path,
            approved_manifest_sha256="f" * 64,
        )
    assert mismatch.value.code == "hybrid_binding_unavailable"


def test_registry_refuses_unapproved_pair_without_fallback() -> None:
    registry = ApprovedHybridBindingRegistry(_manifest())

    with pytest.raises(HybridBindingRefusal) as refusal:
        registry.authorize(
            "release:endoviho-rag:v0:20991230:001",
            CORPUS_KEY,
        )

    assert refusal.value.code == "hybrid_binding_unavailable"
    assert "approved" in str(refusal.value)


def test_unavailable_registry_is_a_stable_fail_closed_default() -> None:
    with pytest.raises(HybridBindingRefusal) as refusal:
        UnavailableHybridBindingRegistry().authorize(RELEASE_KEY, CORPUS_KEY)

    assert refusal.value.code == "hybrid_binding_unavailable"
    assert "configured" in str(refusal.value)


def test_configured_registry_defers_file_io_until_hybrid_authorization(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "not-created.json"
    registry = ConfiguredHybridBindingRegistry(
        missing,
        approved_manifest_sha256="a" * 64,
    )

    assert not missing.exists()
    with pytest.raises(HybridBindingRefusal):
        registry.authorize(RELEASE_KEY, CORPUS_KEY)
