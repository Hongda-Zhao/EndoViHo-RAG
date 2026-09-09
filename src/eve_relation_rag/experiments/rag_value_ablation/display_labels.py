"""Apply approved display aliases without merging roles, versions, scopes or source records."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Self

from pydantic import Field, model_validator

from eve_relation_rag.experiments.rag_value_ablation.associations import (
    ExactAssociation,
    SourceReportedAssociation,
    ViralLineageAffinity,
)
from eve_relation_rag.experiments.rag_value_ablation.contracts import HumanApproval
from eve_relation_rag.literature.contracts import (
    DocumentKey,
    NonEmptyText,
    Sha256,
    StableToken,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.hashing import canonical_json_sha256


class DisplayLabelMapping(StrictFrozenSchema):
    mapping_key: StableToken
    original_lineage: ViralLineageAffinity
    display_label: NonEmptyText
    documentation_document_keys: tuple[DocumentKey, ...] = Field(min_length=1)
    documentation_locator: NonEmptyText


class ApprovedDisplayLabels(StrictFrozenSchema):
    """Human-approved exact aliases; a label string alone never establishes identity."""

    corpus_release_key: StableToken
    corpus_manifest_sha256: Sha256
    approval: HumanApproval
    mappings: tuple[DisplayLabelMapping, ...]
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_mappings(self) -> Self:
        keys = tuple(row.mapping_key for row in self.mappings)
        identities = tuple(canonical_json_sha256(row.original_lineage) for row in self.mappings)
        if keys != tuple(sorted(set(keys))) or len(identities) != len(set(identities)):
            raise ValueError("display aliases must be unique, sorted and unambiguous")
        if self.manifest_sha256 != canonical_json_sha256(
            self.model_dump(mode="json", exclude={"manifest_sha256"})
        ):
            raise ValueError("display mapping manifest checksum differs")
        return self


class DisplayedAssociation(StrictFrozenSchema):
    original: ExactAssociation | SourceReportedAssociation
    original_label: NonEmptyText
    display_label: NonEmptyText
    mapping_key: StableToken | None
    mapping_manifest_sha256: Sha256 | None


def apply_display_labels(
    associations: Sequence[ExactAssociation | SourceReportedAssociation],
    *,
    manifest: ApprovedDisplayLabels | None = None,
    approved_manifest_sha256: str | None = None,
    corpus_release_key: str,
    corpus_manifest_sha256: str,
    permitted_document_keys: frozenset[str],
) -> tuple[DisplayedAssociation, ...]:
    """Return one record per input, including unmapped entries; originals remain immutable."""
    if (manifest is None) != (approved_manifest_sha256 is None):
        raise ValueError("display aliases require both an approved manifest and its pinned hash")
    by_identity = {}
    if manifest is not None:
        manifest = ApprovedDisplayLabels.model_validate_json(manifest.model_dump_json())
        if (
            manifest.manifest_sha256 != approved_manifest_sha256
            or manifest.corpus_release_key != corpus_release_key
            or manifest.corpus_manifest_sha256 != corpus_manifest_sha256
            or any(
                not set(row.documentation_document_keys) <= permitted_document_keys
                for row in manifest.mappings
            )
        ):
            raise ValueError("display aliases differ from the approved corpus or documentation")
        by_identity = {
            canonical_json_sha256(row.original_lineage): row for row in manifest.mappings
        }
    output = []
    for association in associations:
        if type(association) not in (ExactAssociation, SourceReportedAssociation):
            raise ValueError("display aliases require exact typed source associations")
        # Validate detached copies so model_copy cannot smuggle a changed role/version.
        original = type(association).model_validate_json(association.model_dump_json())
        affinity = original.viral_lineage_affinity
        source_label = (
            original.viral_lineage_affinity.canonical_name
            if isinstance(original, ExactAssociation)
            else original.viral_lineage_affinity_text
        )
        mapping = by_identity.get(canonical_json_sha256(affinity)) if affinity is not None else None
        # Source-reported wording must itself match the documented alias; resolving
        # a taxon key does not authorize silently relabeling a different source phrase.
        if mapping is not None and mapping.original_lineage.canonical_name != source_label:
            mapping = None
        output.append(
            DisplayedAssociation(
                original=original,
                original_label=source_label,
                display_label=source_label if mapping is None else mapping.display_label,
                mapping_key=None if mapping is None else mapping.mapping_key,
                mapping_manifest_sha256=None if mapping is None else approved_manifest_sha256,
            )
        )
    return tuple(output)
