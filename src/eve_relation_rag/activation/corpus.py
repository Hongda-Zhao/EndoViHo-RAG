"""Build checksum-frozen V0 corpus, structured-anchor, and hybrid-binding candidates.

This module is deliberately candidate-only.  It never imports a corpus, mutates a
published release, publishes a binding, or assigns a human review decision.  The V0
structured anchors are admitted only when their JATS locator resolves against the
checksum-pinned source bytes and the historical-to-current taxonomy bridge is the
explicit ICTV ``renamed_to`` mapping approved for the activation packet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final, Literal, Self

from pydantic import BaseModel, Field, model_validator

from eve_relation_rag.activation.contracts import StudyFormalMappingManifest
from eve_relation_rag.hybrid.contracts import (
    BINDING_MANIFEST_VERSION,
    HybridReleaseBinding,
    HybridReleaseBindingManifest,
    canonical_self_sha256,
)
from eve_relation_rag.literature.anchors import (
    AnchorManifestEntry,
    CorpusAnchorManifest,
)
from eve_relation_rag.literature.contracts import (
    ANCHOR_POLICY_KEY,
    CanonicalLocator,
    CorpusManifest,
    JatsLocator,
    LineageAnchor,
    NonEmptyText,
    Sha256,
    StableToken,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.hashing import (
    anchor_key,
    canonical_json_sha256,
    canonical_manifest_sha256,
)
from eve_relation_rag.literature.parsing import parse_document, resolve_locator

BASE_CORPUS_RELEASE_KEY: Final = "corpus:endoviho-rag:v0:20260828:001"
BASE_CORPUS_MANIFEST_SHA256: Final = (
    "1497ea3383bea64d2bc4f17d2376dceb537b4f6c6f57ccb6eaf667b6589732f0"
)
BASE_ANCHOR_MANIFEST_SHA256: Final = (
    "75a523bc6408f13b07ba283e6539734ec3b694f3dab59994a464d40d98b01fca"
)
V0_CORPUS_RELEASE_KEY: Final = "corpus:endoviho-rag:v0:20260829:001"
V0_CORPUS_MANIFEST_SHA256: Final = (
    "a96fe244fa82ddbba0c24f7cee16753a5f1194b91c37af9cf27380c6368be929"
)
V0_STRUCTURED_RELEASE_KEY: Final = "release:endoviho-rag:v0:20260826:001"
V0_STRUCTURED_ANCHOR_CURATION_METHOD: Final = "curation:v0-activation-packet-a-v1"

STUDY_SNAPSHOT_KEY: Final = (
    "lineage-snapshot:study-viral:sha256:"
    "b3e002edc491b74adaabd22519b4eca7ee1b75a56b16309203314520b0a281e1"
)
STUDY_ORTHOPOLINTOVIRALES_TERM_KEY: Final = (
    "study-viral-major-taxon:orthopolintovirales"
)
FORMAL_MSL41_SNAPSHOT_KEY: Final = (
    "lineage-snapshot:ictv-msl41:sha256:"
    "7c5f784708f36a8d717df8a24ba84d45ca27d9b13e805b520284c203fcfe1374"
)
FORMAL_AMPHINTOVIRALES_TERM_KEY: Final = (
    "lineage-term:ictv-msl41:sha256:"
    "352b600f9fea40f27ba62cf424b81e2f9360210190822bb6732aee52c28bc200"
)
STUDY_FORMAL_MAPPING_KEY: Final = (
    "study-formal-mapping:sha256:"
    "33d8ef6c4867c8436da80511f96294b4c22ab8e3fb3ce2cb03f08eb62ccd4869"
)


class CorpusActivationError(ValueError):
    """Raised when candidate evidence is incomplete, inconsistent, or unsupported."""


class LineageAnchorBridge(StrictFrozenSchema):
    """Exact study-to-formal identity used to interpret one literature anchor."""

    bridge_schema_version: Literal["v0-lineage-anchor-bridge-v1"]
    target_role: Literal["study_viral_lineage", "formal_viral_taxonomy"]
    literature_label: NonEmptyText
    study_snapshot_key: StableToken
    study_term_key: StableToken
    study_canonical_name: Literal["Orthopolintovirales"]
    formal_snapshot_key: StableToken
    formal_term_key: StableToken
    formal_canonical_name: Literal["Amphintovirales"]
    relation: Literal["renamed_to"]
    curation_method_key: Literal["curation:ictv-proposal-2024.010D"]
    mapping_key: StableToken
    mapping_manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_exact_v0_endpoints(self) -> Self:
        if (
            self.study_snapshot_key != STUDY_SNAPSHOT_KEY
            or self.study_term_key != STUDY_ORTHOPOLINTOVIRALES_TERM_KEY
            or self.formal_snapshot_key != FORMAL_MSL41_SNAPSHOT_KEY
            or self.formal_term_key != FORMAL_AMPHINTOVIRALES_TERM_KEY
            or self.mapping_key != STUDY_FORMAL_MAPPING_KEY
        ):
            raise ValueError("lineage anchor bridge does not use the frozen V0 endpoints")
        return self


class StructuredAnchorSourceLocator(StrictFrozenSchema):
    """Resolvable source evidence and taxonomy bridge covered by an anchor key."""

    locator_schema_version: Literal["v0-structured-anchor-source-locator-v1"]
    source_artifact_sha256: Sha256
    canonical_locator: CanonicalLocator
    locator_text: NonEmptyText
    resolved_text_sha256: Sha256
    evidence_quote: NonEmptyText = Field(max_length=500)
    evidence_quote_sha256: Sha256
    bridge: LineageAnchorBridge

    @model_validator(mode="after")
    def validate_quote_digest(self) -> Self:
        observed = hashlib.sha256(self.evidence_quote.encode("utf-8")).hexdigest()
        if self.evidence_quote_sha256 != observed:
            raise ValueError("evidence_quote_sha256 does not match evidence_quote")
        return self


class _EvidenceSpec(StrictFrozenSchema):
    pmcid: str
    locator: JatsLocator
    evidence_quote: NonEmptyText = Field(max_length=500)
    literature_label: NonEmptyText


_EVIDENCE_SPECS: Final = (
    _EvidenceSpec(
        pmcid="PMC4028283",
        locator=JatsLocator(
            locator_type="jats_xml",
            section_path=("Abstract",),
            element_type="abstract",
            element_ordinal=2,
            xml_element_path="/article/front[1]/article-meta[1]/abstract[1]/sec[2]/p[1]",
            line_start=None,
            line_end=None,
            token_start=None,
            token_end=None,
        ),
        evidence_quote="We propose the name ‘Polintoviruses’ to denote these putative viruses",
        literature_label="Polintoviruses",
    ),
    _EvidenceSpec(
        pmcid="PMC4642659",
        locator=JatsLocator(
            locator_type="jats_xml",
            section_path=("Abstract",),
            element_type="abstract",
            element_ordinal=2,
            xml_element_path="/article/front[1]/article-meta[1]/abstract[1]/sec[2]/p[1]",
            line_start=None,
            line_end=None,
            token_start=None,
            token_end=None,
        ),
        evidence_quote=(
            "a large group of Polinton-like viruses (PLV) that resemble Polintons "
            "(polintoviruses) and virophages"
        ),
        literature_label="Polintons (polintoviruses)",
    ),
    _EvidenceSpec(
        pmcid="PMC7805220",
        locator=JatsLocator(
            locator_type="jats_xml",
            section_path=("Introduction",),
            element_type="paragraph",
            element_ordinal=1,
            xml_element_path="/article/body[1]/sec[1]/p[1]",
            line_start=None,
            line_end=None,
            token_start=None,
            token_end=None,
        ),
        evidence_quote="they would be reclassified as Polintoviruses",
        literature_label="Polintoviruses",
    ),
    _EvidenceSpec(
        pmcid="PMC8097293",
        locator=JatsLocator(
            locator_type="jats_xml",
            section_path=("Abstract",),
            element_type="abstract",
            element_ordinal=1,
            xml_element_path="/article/front[1]/article-meta[1]/abstract[1]/p[1]",
            line_start=None,
            line_end=None,
            token_start=None,
            token_end=None,
        ),
        evidence_quote=(
            "Mavericks form an ancient lineage of aquatic dsDNA viruses which are probably "
            "still functional in some vertebrate lineages"
        ),
        literature_label="Mavericks",
    ),
)


def build_v0_corpus_manifest(base_manifest: CorpusManifest) -> CorpusManifest:
    """Clone the approved 11-document source set under the new immutable V0 key."""

    base = _revalidate_corpus(base_manifest)
    if (
        base.corpus_release_key != BASE_CORPUS_RELEASE_KEY
        or base.manifest_sha256 != BASE_CORPUS_MANIFEST_SHA256
        or base.document_count != 11
    ):
        raise CorpusActivationError("the approved M3 corpus v2 baseline is required")
    payload = base.model_dump(mode="python")
    payload.update(
        {
            "corpus_release_key": V0_CORPUS_RELEASE_KEY,
            "release_title": "EndoViHo-RAG V0 activation corpus with exact lineage anchors",
            "purpose": (
                "The approved 11-document EVE and Polinton pilot rebuilt as a distinct "
                "immutable corpus with checksum-bound study and formal viral-lineage anchors "
                "for the V0 hybrid activation benchmark."
            ),
            "manifest_sha256": "0" * 64,
        }
    )
    payload["manifest_sha256"] = canonical_manifest_sha256(payload)
    return CorpusManifest.model_validate(payload)


def build_v0_anchor_manifest(
    *,
    base_anchor_manifest: CorpusAnchorManifest,
    v0_corpus_manifest: CorpusManifest,
    taxonomy_mapping_manifest: StudyFormalMappingManifest,
    corpus_root: str | Path,
) -> CorpusAnchorManifest:
    """Reuse the approved anchors and add eight exact, bridge-bound lineage anchors."""

    base = _revalidate_anchor_manifest(base_anchor_manifest)
    corpus = _revalidate_corpus(v0_corpus_manifest)
    mapping = _revalidate_mapping(taxonomy_mapping_manifest)
    _validate_baselines(base, corpus)
    _require_order_rename(mapping)

    root = Path(corpus_root)
    documents_by_pmcid = {document.pmcid: document for document in corpus.documents}
    structured_entries: list[AnchorManifestEntry] = []
    for spec in _EVIDENCE_SPECS:
        document = documents_by_pmcid.get(spec.pmcid)
        if document is None:
            raise CorpusActivationError(f"approved document is absent: {spec.pmcid}")
        raw = _verified_document_bytes(root, document.relative_path, document.source_sha256)
        resolved = resolve_locator(document.document_format, raw, spec.locator)
        if spec.evidence_quote not in resolved:
            raise CorpusActivationError(
                f"exact evidence quote is absent at the frozen locator: {spec.pmcid}"
            )
        parsed = parse_document(document.document_format, raw)
        matching_blocks = tuple(block for block in parsed.blocks if block.locator == spec.locator)
        if len(matching_blocks) != 1:
            raise CorpusActivationError("structured anchor locator is not unique")
        locator_text = matching_blocks[0].locator_text
        target_pairs: tuple[
            tuple[
                Literal["study_viral_lineage", "formal_viral_taxonomy"],
                str,
                str,
            ],
            ...,
        ] = (
            (
                "study_viral_lineage",
                STUDY_SNAPSHOT_KEY,
                STUDY_ORTHOPOLINTOVIRALES_TERM_KEY,
            ),
            (
                "formal_viral_taxonomy",
                FORMAL_MSL41_SNAPSHOT_KEY,
                FORMAL_AMPHINTOVIRALES_TERM_KEY,
            ),
        )
        for target_role, snapshot_key, term_key in target_pairs:
            bridge = LineageAnchorBridge(
                bridge_schema_version="v0-lineage-anchor-bridge-v1",
                target_role=target_role,
                literature_label=spec.literature_label,
                study_snapshot_key=STUDY_SNAPSHOT_KEY,
                study_term_key=STUDY_ORTHOPOLINTOVIRALES_TERM_KEY,
                study_canonical_name="Orthopolintovirales",
                formal_snapshot_key=FORMAL_MSL41_SNAPSHOT_KEY,
                formal_term_key=FORMAL_AMPHINTOVIRALES_TERM_KEY,
                formal_canonical_name="Amphintovirales",
                relation="renamed_to",
                curation_method_key="curation:ictv-proposal-2024.010D",
                mapping_key=STUDY_FORMAL_MAPPING_KEY,
                mapping_manifest_sha256=mapping.manifest_sha256,
            )
            source_locator = StructuredAnchorSourceLocator(
                locator_schema_version="v0-structured-anchor-source-locator-v1",
                source_artifact_sha256=document.source_sha256,
                canonical_locator=spec.locator,
                locator_text=locator_text,
                resolved_text_sha256=hashlib.sha256(resolved.encode("utf-8")).hexdigest(),
                evidence_quote=spec.evidence_quote,
                evidence_quote_sha256=hashlib.sha256(
                    spec.evidence_quote.encode("utf-8")
                ).hexdigest(),
                bridge=bridge,
            )
            structured_entries.append(
                _lineage_anchor_entry(
                    manifest_row=document.manifest_row,
                    document_key=document.expected_document_key,
                    snapshot_key=snapshot_key,
                    term_key=term_key,
                    source_locator=source_locator,
                )
            )

    entries = tuple(
        sorted((*base.anchors, *structured_entries), key=lambda entry: entry.anchor.anchor_key)
    )
    payload: dict[str, object] = {
        "anchor_manifest_schema_version": "corpus-anchor-manifest-v1",
        "corpus_release_key": corpus.corpus_release_key,
        "corpus_manifest_sha256": corpus.manifest_sha256,
        "anchor_policy_key": ANCHOR_POLICY_KEY,
        "anchor_count": len(entries),
        "anchor_manifest_sha256": "0" * 64,
        "anchors": entries,
    }
    hash_payload = dict(payload)
    del hash_payload["anchor_manifest_sha256"]
    payload["anchor_manifest_sha256"] = canonical_json_sha256(hash_payload)
    manifest = CorpusAnchorManifest.model_validate(payload)
    validate_v0_structured_anchor_evidence(
        manifest=manifest,
        corpus_manifest=corpus,
        taxonomy_mapping_manifest=mapping,
        corpus_root=root,
    )
    return manifest


def validate_v0_structured_anchor_evidence(
    *,
    manifest: CorpusAnchorManifest,
    corpus_manifest: CorpusManifest,
    taxonomy_mapping_manifest: StudyFormalMappingManifest,
    corpus_root: str | Path,
) -> None:
    """Reconstruct every V0 structured anchor from its frozen source bytes."""

    anchors = _revalidate_anchor_manifest(manifest)
    corpus = _revalidate_corpus(corpus_manifest)
    mapping = _revalidate_mapping(taxonomy_mapping_manifest)
    _require_order_rename(mapping)
    if (
        anchors.corpus_release_key != corpus.corpus_release_key
        or anchors.corpus_manifest_sha256 != corpus.manifest_sha256
    ):
        raise CorpusActivationError("anchor manifest is not bound to the V0 corpus")
    documents_by_row = {document.manifest_row: document for document in corpus.documents}
    structured = tuple(
        entry
        for entry in anchors.anchors
        if entry.curation_method == V0_STRUCTURED_ANCHOR_CURATION_METHOD
    )
    if len(structured) != 8:
        raise CorpusActivationError("V0 requires exactly eight structured lineage anchors")
    target_counts = {"study_viral_lineage": 0, "formal_viral_taxonomy": 0}
    expected_specs = {spec.pmcid: spec for spec in _EVIDENCE_SPECS}
    observed_pairs: set[tuple[str, str]] = set()
    root = Path(corpus_root)
    for entry in structured:
        if entry.anchor.anchor_type != "lineage":
            raise CorpusActivationError("unsupported V0 structured anchor type")
        source = StructuredAnchorSourceLocator.model_validate_json(
            json.dumps(entry.source_locator, ensure_ascii=False, sort_keys=True),
            strict=True,
        )
        document = documents_by_row.get(entry.manifest_row)
        if document is None or document.expected_document_key != entry.document_key:
            raise CorpusActivationError("structured anchor document identity is inconsistent")
        if document.pmcid is None or document.pmcid not in expected_specs:
            raise CorpusActivationError("structured anchor uses an unapproved evidence document")
        spec = expected_specs[document.pmcid]
        if (
            source.canonical_locator != spec.locator
            or source.evidence_quote != spec.evidence_quote
            or source.bridge.literature_label != spec.literature_label
        ):
            raise CorpusActivationError("structured anchor evidence locator drifted from policy")
        if source.source_artifact_sha256 != document.source_sha256:
            raise CorpusActivationError("structured anchor source checksum is inconsistent")
        if source.bridge.mapping_manifest_sha256 != mapping.manifest_sha256:
            raise CorpusActivationError("structured anchor taxonomy bridge checksum drifted")
        raw = _verified_document_bytes(root, document.relative_path, document.source_sha256)
        resolved = resolve_locator(document.document_format, raw, source.canonical_locator)
        if hashlib.sha256(resolved.encode("utf-8")).hexdigest() != source.resolved_text_sha256:
            raise CorpusActivationError("structured anchor resolved-text checksum drifted")
        if source.evidence_quote not in resolved:
            raise CorpusActivationError("structured anchor evidence quote no longer resolves")
        target_counts[source.bridge.target_role] += 1
        observed_pairs.add((document.pmcid, source.bridge.target_role))
        expected_target = (
            (STUDY_SNAPSHOT_KEY, STUDY_ORTHOPOLINTOVIRALES_TERM_KEY)
            if source.bridge.target_role == "study_viral_lineage"
            else (FORMAL_MSL41_SNAPSHOT_KEY, FORMAL_AMPHINTOVIRALES_TERM_KEY)
        )
        if (entry.anchor.snapshot_key, entry.anchor.term_key) != expected_target:
            raise CorpusActivationError("structured anchor target does not match its bridge role")
    if target_counts != {"study_viral_lineage": 4, "formal_viral_taxonomy": 4}:
        raise CorpusActivationError("lineage anchor target coverage is incomplete")
    expected_pairs = {
        (spec.pmcid, role)
        for spec in _EVIDENCE_SPECS
        for role in ("study_viral_lineage", "formal_viral_taxonomy")
    }
    if observed_pairs != expected_pairs:
        raise CorpusActivationError("lineage anchor evidence-target pairs are incomplete")


def build_v0_hybrid_binding_manifest(
    *,
    release_manifest_sha256: str,
    corpus_manifest_sha256: str,
) -> HybridReleaseBindingManifest:
    """Build the one-pair V0 allowlist; the caller must supply final manifest digests."""

    if corpus_manifest_sha256 != V0_CORPUS_MANIFEST_SHA256:
        raise CorpusActivationError("hybrid binding requires the exact V0 corpus manifest")

    payload: dict[str, object] = {
        "binding_schema_version": BINDING_MANIFEST_VERSION,
        "bindings": (
            HybridReleaseBinding(
                release_key=V0_STRUCTURED_RELEASE_KEY,
                release_manifest_sha256=release_manifest_sha256,
                corpus_release_key=V0_CORPUS_RELEASE_KEY,
                corpus_manifest_sha256=corpus_manifest_sha256,
            ),
        ),
        "manifest_sha256": "0" * 64,
    }
    payload["manifest_sha256"] = canonical_self_sha256(payload, "manifest_sha256")
    return HybridReleaseBindingManifest.model_validate(payload)


def write_candidate_manifest(path: str | Path, manifest: BaseModel) -> Path:
    """Write one new candidate JSON file without replacing any existing artifact."""

    output = Path(path)
    if output.exists():
        raise CorpusActivationError("candidate manifest already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return output


def main(argv: Sequence[str] | None = None) -> int:
    """Build one candidate artifact without importing or publishing it."""

    arguments = _parser().parse_args(argv)
    try:
        manifest: CorpusManifest | CorpusAnchorManifest | HybridReleaseBindingManifest
        if arguments.command == "corpus":
            manifest = build_v0_corpus_manifest(
                load_manifest(arguments.base_manifest, CorpusManifest)
            )
        elif arguments.command == "anchors":
            manifest = build_v0_anchor_manifest(
                base_anchor_manifest=load_manifest(
                    arguments.base_anchor_manifest,
                    CorpusAnchorManifest,
                ),
                v0_corpus_manifest=load_manifest(
                    arguments.corpus_manifest,
                    CorpusManifest,
                ),
                taxonomy_mapping_manifest=load_manifest(
                    arguments.taxonomy_mapping_manifest,
                    StudyFormalMappingManifest,
                ),
                corpus_root=arguments.corpus_root,
            )
        else:
            manifest = build_v0_hybrid_binding_manifest(
                release_manifest_sha256=arguments.release_manifest_sha256,
                corpus_manifest_sha256=arguments.corpus_manifest_sha256,
            )
        output = write_candidate_manifest(arguments.output, manifest)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "schema_version": "v0-corpus-activation-driver-result-v1",
                    "status": "error",
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    digest = (
        manifest.anchor_manifest_sha256
        if isinstance(manifest, CorpusAnchorManifest)
        else manifest.manifest_sha256
    )
    print(
        json.dumps(
            {
                "schema_version": "v0-corpus-activation-driver-result-v1",
                "status": "ok",
                "candidate_only": True,
                "database_writes": False,
                "output": str(output),
                "semantic_manifest_sha256": digest,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build candidate-only V0 corpus activation manifests"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    corpus = subparsers.add_parser("corpus")
    corpus.add_argument("--base-manifest", type=Path, required=True)
    corpus.add_argument("--output", type=Path, required=True)

    anchors = subparsers.add_parser("anchors")
    anchors.add_argument("--base-anchor-manifest", type=Path, required=True)
    anchors.add_argument("--corpus-manifest", type=Path, required=True)
    anchors.add_argument("--taxonomy-mapping-manifest", type=Path, required=True)
    anchors.add_argument("--corpus-root", type=Path, required=True)
    anchors.add_argument("--output", type=Path, required=True)

    binding = subparsers.add_parser("binding")
    binding.add_argument("--release-manifest-sha256", required=True)
    binding.add_argument("--corpus-manifest-sha256", required=True)
    binding.add_argument("--output", type=Path, required=True)
    return parser


def _lineage_anchor_entry(
    *,
    manifest_row: int,
    document_key: str,
    snapshot_key: str,
    term_key: str,
    source_locator: StructuredAnchorSourceLocator,
) -> AnchorManifestEntry:
    target = {
        "anchor_type": "lineage",
        "snapshot_key": snapshot_key,
        "term_key": term_key,
    }
    locator_payload = source_locator.model_dump(mode="json")
    key = anchor_key(
        {
            "anchor_schema_version": "document-anchor-v1",
            "curation_method": V0_STRUCTURED_ANCHOR_CURATION_METHOD,
            "document_key": document_key,
            "manifest_row": manifest_row,
            "source_locator": locator_payload,
            "target": target,
        }
    )
    anchor = LineageAnchor(
        anchor_key=key,
        anchor_type="lineage",
        snapshot_key=snapshot_key,
        term_key=term_key,
    )
    return AnchorManifestEntry(
        manifest_row=manifest_row,
        document_key=document_key,
        anchor=anchor,
        curation_method=V0_STRUCTURED_ANCHOR_CURATION_METHOD,
        source_locator=locator_payload,
        expected_anchor_sha256=canonical_json_sha256(anchor),
    )


def _require_order_rename(mapping: StudyFormalMappingManifest) -> None:
    if (
        mapping.study_snapshot_key != STUDY_SNAPSHOT_KEY
        or mapping.formal_snapshot_key != FORMAL_MSL41_SNAPSHOT_KEY
    ):
        raise CorpusActivationError("taxonomy mapping uses unexpected V0 snapshots")
    matches = tuple(
        row
        for row in mapping.mappings
        if row.study_term_key == STUDY_ORTHOPOLINTOVIRALES_TERM_KEY
        and row.formal_term_key == FORMAL_AMPHINTOVIRALES_TERM_KEY
        and row.relation == "renamed_to"
        and row.curation_method_key == "curation:ictv-proposal-2024.010D"
        and row.mapping_key == STUDY_FORMAL_MAPPING_KEY
    )
    if len(matches) != 1:
        raise CorpusActivationError(
            "one explicit Orthopolintovirales renamed_to Amphintovirales mapping is required"
        )


def _validate_baselines(
    anchors: CorpusAnchorManifest,
    corpus: CorpusManifest,
) -> None:
    if (
        anchors.corpus_release_key != BASE_CORPUS_RELEASE_KEY
        or anchors.corpus_manifest_sha256 != BASE_CORPUS_MANIFEST_SHA256
        or anchors.anchor_manifest_sha256 != BASE_ANCHOR_MANIFEST_SHA256
        or anchors.anchor_count != 22
    ):
        raise CorpusActivationError("the approved M3 anchor v2 baseline is required")
    if corpus.corpus_release_key != V0_CORPUS_RELEASE_KEY or corpus.document_count != 11:
        raise CorpusActivationError("the exact V0 corpus candidate is required")


def _verified_document_bytes(root: Path, relative_path: str, expected_sha256: str) -> bytes:
    path = root / relative_path
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CorpusActivationError(f"cannot read approved corpus source: {relative_path}") from exc
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise CorpusActivationError(f"approved corpus source checksum drifted: {relative_path}")
    return raw


def _revalidate_corpus(value: CorpusManifest) -> CorpusManifest:
    return CorpusManifest.model_validate_json(value.model_dump_json(), strict=True)


def _revalidate_anchor_manifest(value: CorpusAnchorManifest) -> CorpusAnchorManifest:
    return CorpusAnchorManifest.model_validate_json(value.model_dump_json(), strict=True)


def _revalidate_mapping(value: StudyFormalMappingManifest) -> StudyFormalMappingManifest:
    return StudyFormalMappingManifest.model_validate_json(value.model_dump_json(), strict=True)


def load_manifest[ModelT: BaseModel](path: str | Path, model: type[ModelT]) -> ModelT:
    """Load one strict candidate input without weakening model validation."""

    try:
        return model.model_validate_json(Path(path).read_text(encoding="utf-8"), strict=True)
    except (OSError, UnicodeError, ValueError) as exc:
        raise CorpusActivationError(f"cannot load candidate manifest: {path}") from exc


__all__ = [
    "BASE_ANCHOR_MANIFEST_SHA256",
    "BASE_CORPUS_MANIFEST_SHA256",
    "BASE_CORPUS_RELEASE_KEY",
    "FORMAL_AMPHINTOVIRALES_TERM_KEY",
    "FORMAL_MSL41_SNAPSHOT_KEY",
    "STUDY_ORTHOPOLINTOVIRALES_TERM_KEY",
    "STUDY_FORMAL_MAPPING_KEY",
    "STUDY_SNAPSHOT_KEY",
    "V0_CORPUS_RELEASE_KEY",
    "V0_CORPUS_MANIFEST_SHA256",
    "V0_STRUCTURED_RELEASE_KEY",
    "CorpusActivationError",
    "LineageAnchorBridge",
    "StructuredAnchorSourceLocator",
    "build_v0_anchor_manifest",
    "build_v0_corpus_manifest",
    "build_v0_hybrid_binding_manifest",
    "load_manifest",
    "validate_v0_structured_anchor_evidence",
    "write_candidate_manifest",
]


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess integration
    raise SystemExit(main())
