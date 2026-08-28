"""Checksum-frozen curated anchor manifests and atomic candidate import."""

from __future__ import annotations

from typing import Any, Self

from pydantic import Field, model_validator
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, aliased

from eve_relation_rag.db.models import (
    CorpusDocumentMembership,
    CorpusRelease,
    Document,
    DocumentAnchor,
    LiteraturePolicy,
)
from eve_relation_rag.literature.contracts import (
    CorpusReleaseKey,
    DocumentKey,
    RetrievalAnchor,
    Sha256,
    StableToken,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.hashing import anchor_key, canonical_json_sha256


class AnchorManifestEntry(StrictFrozenSchema):
    """One exact curated anchor attached to a manifest document row."""

    manifest_row: int = Field(ge=1)
    document_key: DocumentKey
    anchor: RetrievalAnchor
    curation_method: StableToken
    source_locator: dict[str, Any]
    expected_anchor_sha256: Sha256

    @model_validator(mode="after")
    def validate_anchor_identity(self) -> Self:
        expected_sha256 = canonical_json_sha256(self.anchor)
        if self.expected_anchor_sha256 != expected_sha256:
            raise ValueError("expected_anchor_sha256 does not match the typed anchor")
        target = self.anchor.model_dump(mode="python")
        del target["anchor_key"]
        expected_key = anchor_key(
            {
                "anchor_schema_version": "document-anchor-v1",
                "curation_method": self.curation_method,
                "document_key": self.document_key,
                "manifest_row": self.manifest_row,
                "source_locator": self.source_locator,
                "target": target,
            }
        )
        if self.anchor.anchor_key != expected_key:
            raise ValueError("anchor_key does not match the curated anchor preimage")
        return self


class CorpusAnchorManifest(StrictFrozenSchema):
    """Self-checksummed anchor package bound to one approved corpus manifest."""

    anchor_manifest_schema_version: str
    corpus_release_key: CorpusReleaseKey
    corpus_manifest_sha256: Sha256
    anchor_policy_key: StableToken
    anchor_count: int = Field(ge=1)
    anchor_manifest_sha256: Sha256
    anchors: tuple[AnchorManifestEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        if self.anchor_manifest_schema_version != "corpus-anchor-manifest-v1":
            raise ValueError("unsupported anchor manifest schema version")
        if self.anchor_count != len(self.anchors):
            raise ValueError("anchor_count does not match anchors")
        keys = tuple(entry.anchor.anchor_key for entry in self.anchors)
        if len(keys) != len(set(keys)):
            raise ValueError("anchor manifest contains duplicate anchor keys")
        if keys != tuple(sorted(keys)):
            raise ValueError("anchor manifest entries must be in canonical anchor-key order")
        payload = self.model_dump(mode="python")
        del payload["anchor_manifest_sha256"]
        if self.anchor_manifest_sha256 != canonical_json_sha256(payload):
            raise ValueError("anchor_manifest_sha256 does not match manifest")
        return self


class AnchorImportError(RuntimeError):
    """Raised when curated anchors cannot be inserted or exactly replayed."""


class AnchorImportReport(StrictFrozenSchema):
    """Stable summary of candidate anchor staging."""

    corpus_release_key: str
    anchor_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    anchor_count: int = Field(ge=1)
    inserted_count: int = Field(ge=0)
    reused_count: int = Field(ge=0)
    anchors_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def import_candidate_anchors(
    engine: Engine,
    *,
    manifest: CorpusAnchorManifest,
    approved_anchor_manifest_sha256: str,
) -> AnchorImportReport:
    """Atomically insert or exactly reuse every curated anchor manifest row."""

    if manifest.anchor_manifest_sha256 != approved_anchor_manifest_sha256:
        raise AnchorImportError("approved anchor manifest checksum does not match")
    inserted = 0
    reused = 0
    anchor_policy = aliased(LiteraturePolicy)
    try:
        with Session(engine) as session, session.begin():
            release_row = session.execute(
                select(CorpusRelease, anchor_policy.policy_key.label("anchor_policy_key"))
                .join(anchor_policy, anchor_policy.id == CorpusRelease.anchor_policy_id)
                .where(CorpusRelease.corpus_release_key == manifest.corpus_release_key)
                .with_for_update()
            ).one_or_none()
            if release_row is None or release_row.CorpusRelease.status not in {
                "candidate",
                "validated",
            }:
                raise AnchorImportError("anchors may only be staged for candidate/validated corpus")
            release = release_row.CorpusRelease
            if (
                release.manifest_sha256 != manifest.corpus_manifest_sha256
                or release_row.anchor_policy_key != manifest.anchor_policy_key
            ):
                raise AnchorImportError("anchor manifest does not match corpus policy identity")

            memberships = session.execute(
                select(
                    CorpusDocumentMembership.manifest_row,
                    Document.id,
                    Document.document_key,
                )
                .join(Document, Document.id == CorpusDocumentMembership.document_id)
                .where(CorpusDocumentMembership.release_id == release.id)
            ).all()
            by_row = {row.manifest_row: row for row in memberships}
            for entry in manifest.anchors:
                membership = by_row.get(entry.manifest_row)
                if membership is None or membership.document_key != entry.document_key:
                    raise AnchorImportError("anchor document identity does not match manifest row")
                values = _anchor_values(entry)
                existing = session.scalar(
                    select(DocumentAnchor).where(
                        DocumentAnchor.release_id == release.id,
                        DocumentAnchor.anchor_key == entry.anchor.anchor_key
                    )
                )
                expected = {
                    "release_id": release.id,
                    "document_id": membership.id,
                    **values,
                }
                if existing is not None:
                    if any(getattr(existing, key) != value for key, value in expected.items()):
                        raise AnchorImportError("existing anchor differs from approved manifest")
                    reused += 1
                    continue
                session.add(
                    DocumentAnchor(
                        anchor_key=entry.anchor.anchor_key,
                        release_id=release.id,
                        document_id=membership.id,
                        **values,
                    )
                )
                inserted += 1
            session.flush()
    except AnchorImportError:
        raise
    except Exception as exc:
        raise AnchorImportError("anchor import transaction failed") from exc

    anchors_sha256 = canonical_json_sha256(
        tuple(
            sorted(
                (entry.anchor.anchor_key, entry.expected_anchor_sha256)
                for entry in manifest.anchors
            )
        )
    )
    return AnchorImportReport(
        corpus_release_key=manifest.corpus_release_key,
        anchor_manifest_sha256=manifest.anchor_manifest_sha256,
        anchor_count=manifest.anchor_count,
        inserted_count=inserted,
        reused_count=reused,
        anchors_sha256=anchors_sha256,
    )


def _anchor_values(entry: AnchorManifestEntry) -> dict[str, Any]:
    anchor = entry.anchor
    values: dict[str, Any] = {
        "anchor_type": anchor.anchor_type,
        "locus_key": None,
        "assembly_key": None,
        "lineage_snapshot_key": None,
        "lineage_term_key": None,
        "method_definition_key": None,
        "target_document_key": None,
        "doi": None,
        "pmid": None,
        "pmcid": None,
        "keyword_phrase": None,
        "manifest_row": entry.manifest_row,
        "curation_method": entry.curation_method,
        "source_locator": entry.source_locator,
        "anchor_sha256": entry.expected_anchor_sha256,
    }
    payload = anchor.model_dump(mode="python")
    if anchor.anchor_type == "locus":
        values["locus_key"] = payload["locus_key"]
    elif anchor.anchor_type == "assembly":
        values["assembly_key"] = payload["assembly_key"]
    elif anchor.anchor_type == "lineage":
        values["lineage_snapshot_key"] = payload["snapshot_key"]
        values["lineage_term_key"] = payload["term_key"]
    elif anchor.anchor_type == "method":
        values["method_definition_key"] = payload["method_definition_key"]
    elif anchor.anchor_type == "document":
        values["target_document_key"] = payload["document_key"]
        values["doi"] = payload["doi"]
        values["pmid"] = payload["pmid"]
        values["pmcid"] = payload["pmcid"]
    elif anchor.anchor_type == "keyword":
        values["keyword_phrase"] = payload["phrase"]
    return values
