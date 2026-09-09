"""Synthetic-only exact alias approvals; homonyms never merge biological evidence."""

from __future__ import annotations

import pytest

from eve_relation_rag.experiments.rag_value_ablation.association_projection import (
    project_exact_associations,
)
from eve_relation_rag.experiments.rag_value_ablation.display_labels import (
    ApprovedDisplayLabels,
    apply_display_labels,
)
from eve_relation_rag.hybrid.contracts import canonical_model_sha256
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256
from tests.experiments.test_rag_value_association_projection import _bindings, _success
from tests.experiments.test_rag_value_scoped_admission import _approval

DOCUMENT = "document:sha256:" + "d" * 64
CORPUS = "corpus:tests-only"


@pytest.fixture
def example():
    success, bindings = _success(), _bindings()
    original = project_exact_associations(
        success,
        bindings=bindings,
        expected_binding_sha256=bindings.binding_sha256,
        expected_source_sha256=canonical_model_sha256(success),
    ).associations[0]
    payload = {
        "corpus_release_key": CORPUS,
        "corpus_manifest_sha256": "e" * 64,
        "approval": _approval().model_dump(mode="json"),
        "mappings": [
            {
                "mapping_key": "mapping:tests-only",
                "original_lineage": original.viral_lineage_affinity,
                "display_label": "Synthetic display alias",
                "documentation_document_keys": [DOCUMENT],
                "documentation_locator": "Table 1",
            }
        ],
    }
    manifest = ApprovedDisplayLabels.model_validate_json(
        canonical_json_bytes(
            {
                **payload,
                "manifest_sha256": canonical_json_sha256(payload),
            }
        )
    )
    return original, manifest


def apply(records, manifest):
    return apply_display_labels(
        records,
        manifest=manifest,
        approved_manifest_sha256=None if manifest is None else manifest.manifest_sha256,
        corpus_release_key=CORPUS,
        corpus_manifest_sha256="e" * 64,
        permitted_document_keys=frozenset({DOCUMENT}),
    )


def test_alias_adds_display_value_and_retains_exact_original(example):
    original, manifest = example
    result = apply((original,), manifest)[0]
    assert result.display_label == "Synthetic display alias"
    assert result.original == original
    assert result.original_label == original.viral_lineage_affinity.canonical_name
    assert result.mapping_key == "mapping:tests-only"


@pytest.mark.parametrize(
    "field,value",
    [
        ("role", "formal_viral_taxonomy"),
        ("snapshot_key", "snapshot:another-version"),
        ("term_key", "term:different-object-same-name"),
        ("include_descendants", True),
    ],
)
def test_same_name_different_identity_role_version_or_scope_stays_unmapped(example, field, value):
    original, manifest = example
    changed = original.model_copy(
        update={
            "viral_lineage_affinity": original.viral_lineage_affinity.model_copy(
                update={field: value}
            ),
        }
    )
    results = apply((original, changed), manifest)
    assert len(results) == 2
    assert results[0].mapping_key is not None
    assert results[1].mapping_key is None
    assert results[1].original == changed


def test_no_approved_mapping_keeps_every_unmapped_source(example):
    original, _ = example
    result = apply((original,), None)[0]
    assert result.mapping_key is None
    assert result.display_label == original.viral_lineage_affinity.canonical_name


def test_mapping_cannot_be_borrowed_from_another_corpus(example):
    original, manifest = example
    with pytest.raises(ValueError, match="corpus or documentation"):
        apply_display_labels(
            (original,),
            manifest=manifest,
            approved_manifest_sha256=manifest.manifest_sha256,
            corpus_release_key="corpus:wrong",
            corpus_manifest_sha256="e" * 64,
            permitted_document_keys=frozenset({DOCUMENT}),
        )
