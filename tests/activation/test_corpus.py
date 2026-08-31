from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

import eve_relation_rag.activation.corpus as corpus_activation
from eve_relation_rag.activation.contracts import (
    StudyFormalMappingManifest,
    seal_manifest_payload,
)
from eve_relation_rag.domain.keys import stable_key
from eve_relation_rag.hybrid.contracts import (
    HybridReleaseBindingManifest,
    canonical_self_sha256,
)
from eve_relation_rag.literature.anchors import (
    AnchorManifestEntry,
    CorpusAnchorManifest,
)
from eve_relation_rag.literature.contracts import (
    ANCHOR_POLICY_KEY,
    CHUNKING_POLICY_KEY,
    CORPUS_MANIFEST_VERSION,
    EMBEDDING_MODEL_KEY,
    FTS_POLICY_KEY,
    PARSER_POLICY_KEY,
    RETRIEVAL_POLICY_KEY,
    CorpusDocumentSpec,
    CorpusManifest,
    DocumentAnchor,
    DocumentKeyPreimage,
    KeywordAnchor,
)
from eve_relation_rag.literature.hashing import (
    anchor_key,
    canonical_json_sha256,
    canonical_manifest_sha256,
    document_key,
)


def test_v0_corpus_and_structured_anchors_reconstruct_exact_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_corpus, root = _base_corpus(tmp_path)
    base_anchors = _base_anchors(base_corpus)
    monkeypatch.setattr(
        corpus_activation,
        "BASE_CORPUS_MANIFEST_SHA256",
        base_corpus.manifest_sha256,
    )
    monkeypatch.setattr(
        corpus_activation,
        "BASE_ANCHOR_MANIFEST_SHA256",
        base_anchors.anchor_manifest_sha256,
    )

    v0_corpus = corpus_activation.build_v0_corpus_manifest(base_corpus)
    anchors = corpus_activation.build_v0_anchor_manifest(
        base_anchor_manifest=base_anchors,
        v0_corpus_manifest=v0_corpus,
        taxonomy_mapping_manifest=_mapping_manifest(),
        corpus_root=root,
    )

    assert v0_corpus.corpus_release_key == corpus_activation.V0_CORPUS_RELEASE_KEY
    assert v0_corpus.documents == base_corpus.documents
    assert anchors.anchor_count == 30
    structured = tuple(
        row
        for row in anchors.anchors
        if row.curation_method == corpus_activation.V0_STRUCTURED_ANCHOR_CURATION_METHOD
    )
    assert len(structured) == 8
    assert {
        (row.anchor.snapshot_key, row.anchor.term_key)
        for row in structured
        if row.anchor.anchor_type == "lineage"
    } == {
        (
            corpus_activation.STUDY_SNAPSHOT_KEY,
            corpus_activation.STUDY_ORTHOPOLINTOVIRALES_TERM_KEY,
        ),
        (
            corpus_activation.FORMAL_MSL41_SNAPSHOT_KEY,
            corpus_activation.FORMAL_AMPHINTOVIRALES_TERM_KEY,
        ),
    }
    assert all(
        row.source_locator["bridge"]["relation"] == "renamed_to" for row in structured
    )

    (root / "PMC4028283.xml").write_text("<article/>", encoding="utf-8")
    with pytest.raises(corpus_activation.CorpusActivationError, match="checksum drifted"):
        corpus_activation.validate_v0_structured_anchor_evidence(
            manifest=anchors,
            corpus_manifest=v0_corpus,
            taxonomy_mapping_manifest=_mapping_manifest(),
            corpus_root=root,
        )


def test_lineage_bridge_rejects_old_formal_name_and_non_rename_relation() -> None:
    payload = _bridge_payload()
    bridge = corpus_activation.LineageAnchorBridge.model_validate(payload)
    assert bridge.formal_canonical_name == "Amphintovirales"

    with pytest.raises(ValidationError):
        corpus_activation.LineageAnchorBridge.model_validate(
            payload | {"formal_canonical_name": "Orthopolintovirales"}
        )
    with pytest.raises(ValidationError):
        corpus_activation.LineageAnchorBridge.model_validate(
            payload | {"relation": "curated_equivalent_to"}
        )


def test_v0_binding_is_one_exact_pair_and_self_checksummed() -> None:
    manifest = corpus_activation.build_v0_hybrid_binding_manifest(
        release_manifest_sha256="a" * 64,
        corpus_manifest_sha256=corpus_activation.V0_CORPUS_MANIFEST_SHA256,
    )

    assert len(manifest.bindings) == 1
    binding = manifest.bindings[0]
    assert binding.release_key == corpus_activation.V0_STRUCTURED_RELEASE_KEY
    assert binding.corpus_release_key == corpus_activation.V0_CORPUS_RELEASE_KEY
    assert manifest.manifest_sha256 == canonical_self_sha256(manifest, "manifest_sha256")
    with pytest.raises(corpus_activation.CorpusActivationError, match="exact V0 corpus"):
        corpus_activation.build_v0_hybrid_binding_manifest(
            release_manifest_sha256="a" * 64,
            corpus_manifest_sha256="b" * 64,
        )


def test_candidate_driver_writes_binding_without_database_mutation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "binding.json"

    assert (
        corpus_activation.main(
            (
                "binding",
                "--release-manifest-sha256",
                "a" * 64,
                "--corpus-manifest-sha256",
                corpus_activation.V0_CORPUS_MANIFEST_SHA256,
                "--output",
                str(output),
            )
        )
        == 0
    )

    manifest = corpus_activation.load_manifest(
        output,
        HybridReleaseBindingManifest,
    )
    assert manifest.bindings[0].release_manifest_sha256 == "a" * 64
    assert (
        manifest.bindings[0].corpus_manifest_sha256
        == corpus_activation.V0_CORPUS_MANIFEST_SHA256
    )
    assert '"database_writes": false' in capsys.readouterr().out


def _base_corpus(tmp_path: Path) -> tuple[CorpusManifest, Path]:
    root = tmp_path / "corpus"
    root.mkdir()
    evidence_xml = {
        "PMC4028283": (
            "<article><front><article-meta><title-group><article-title>Doc 1</article-title>"
            "</title-group><abstract><sec><p>Background.</p></sec><sec><p>"
            "We propose the name ‘Polintoviruses’ to denote these putative viruses."
            "</p></sec></abstract></article-meta></front></article>"
        ),
        "PMC4642659": (
            "<article><front><article-meta><title-group><article-title>Doc 2</article-title>"
            "</title-group><abstract><sec><p>Background.</p></sec><sec><p>We found "
            "a large group of Polinton-like viruses (PLV) that resemble Polintons "
            "(polintoviruses) and virophages.</p></sec></abstract></article-meta></front>"
            "</article>"
        ),
        "PMC7805220": (
            "<article><front><article-meta><title-group><article-title>Doc 3</article-title>"
            "</title-group></article-meta></front><body><sec><title>Introduction</title><p>"
            "The elements encode capsids; they would be reclassified as Polintoviruses."
            "</p></sec></body></article>"
        ),
        "PMC8097293": (
            "<article><front><article-meta><title-group><article-title>Doc 4</article-title>"
            "</title-group><abstract><p>Mavericks form an ancient lineage of aquatic dsDNA "
            "viruses which are probably still functional in some vertebrate lineages.</p>"
            "</abstract></article-meta></front></article>"
        ),
    }
    pmcids = (
        "PMC4028283",
        "PMC4642659",
        "PMC7805220",
        "PMC8097293",
        "PMC5000001",
        "PMC5000002",
        "PMC5000003",
        "PMC5000004",
        "PMC5000005",
        "PMC5000006",
        "PMC5000007",
    )
    documents: list[CorpusDocumentSpec] = []
    for row, pmcid in enumerate(pmcids, start=1):
        title = f"Doc {row}"
        raw = evidence_xml.get(
            pmcid,
            (
                "<article><front><article-meta><title-group><article-title>"
                f"{title}</article-title></title-group></article-meta></front></article>"
            ),
        ).encode()
        path = root / f"{pmcid}.xml"
        path.write_bytes(raw)
        source_sha256 = hashlib.sha256(raw).hexdigest()
        version = f"synthetic exact source {row}"
        doi = f"10.1234/synthetic.{row}"
        pmid = str(100000 + row)
        preimage = DocumentKeyPreimage(
            key_schema_version="document-key-v1",
            source_artifact_sha256=source_sha256,
            media_type="application/xml",
            document_version=version,
            doi=doi,
            pmid=pmid,
            pmcid=pmcid,
            canonical_title=title,
        )
        documents.append(
            CorpusDocumentSpec(
                manifest_row=row,
                relative_path=f"{pmcid}.xml",
                byte_size=len(raw),
                source_sha256=source_sha256,
                document_format="jats_xml",
                media_type="application/xml",
                source_uri=f"https://example.test/{pmcid}",
                retrieved_at="2026-08-27T00:00:00Z",
                title=title,
                authors=("Test Author",),
                document_version=version,
                doi=doi,
                pmid=pmid,
                pmcid=pmcid,
                declared_license="CC-BY-4.0",
                license_evidence_uri=f"https://example.test/{pmcid}/license",
                license_review_status="approved",
                retrieval_text_allowed=True,
                expected_document_key=document_key(preimage),
            )
        )
    payload: dict[str, object] = {
        "manifest_schema_version": CORPUS_MANIFEST_VERSION,
        "corpus_release_key": corpus_activation.BASE_CORPUS_RELEASE_KEY,
        "release_title": "Synthetic baseline",
        "purpose": "Synthetic unit-test baseline.",
        "document_count": len(documents),
        "expected_chunk_count_min": 11,
        "expected_chunk_count_max": 100,
        "parser_policy_key": PARSER_POLICY_KEY,
        "chunking_policy_key": CHUNKING_POLICY_KEY,
        "embedding_model_key": EMBEDDING_MODEL_KEY,
        "fts_policy_key": FTS_POLICY_KEY,
        "retrieval_policy_key": RETRIEVAL_POLICY_KEY,
        "anchor_policy_key": ANCHOR_POLICY_KEY,
        "manifest_sha256": "0" * 64,
        "documents": tuple(documents),
    }
    payload["manifest_sha256"] = canonical_manifest_sha256(payload)
    return CorpusManifest.model_validate(payload), root


def _base_anchors(corpus: CorpusManifest) -> CorpusAnchorManifest:
    entries: list[AnchorManifestEntry] = []
    for document in corpus.documents:
        for kind in ("keyword", "document"):
            target: dict[str, object]
            if kind == "keyword":
                target = {"anchor_type": "keyword", "phrase": f"topic {document.manifest_row}"}
            else:
                target = {
                    "anchor_type": "document",
                    "document_key": None,
                    "doi": None,
                    "pmid": None,
                    "pmcid": document.pmcid,
                }
            locator = {"fixture": "approved-m3-anchor", "row": document.manifest_row}
            key = anchor_key(
                {
                    "anchor_schema_version": "document-anchor-v1",
                    "curation_method": "curation:synthetic-approved-v1",
                    "document_key": document.expected_document_key,
                    "manifest_row": document.manifest_row,
                    "source_locator": locator,
                    "target": target,
                }
            )
            anchor = (
                KeywordAnchor(
                    anchor_key=key,
                    anchor_type="keyword",
                    phrase=f"topic {document.manifest_row}",
                )
                if kind == "keyword"
                else DocumentAnchor(
                    anchor_key=key,
                    anchor_type="document",
                    document_key=None,
                    doi=None,
                    pmid=None,
                    pmcid=document.pmcid,
                )
            )
            entries.append(
                AnchorManifestEntry(
                    manifest_row=document.manifest_row,
                    document_key=document.expected_document_key,
                    anchor=anchor,
                    curation_method="curation:synthetic-approved-v1",
                    source_locator=locator,
                    expected_anchor_sha256=canonical_json_sha256(anchor),
                )
            )
    ordered = tuple(sorted(entries, key=lambda row: row.anchor.anchor_key))
    payload: dict[str, object] = {
        "anchor_manifest_schema_version": "corpus-anchor-manifest-v1",
        "corpus_release_key": corpus.corpus_release_key,
        "corpus_manifest_sha256": corpus.manifest_sha256,
        "anchor_policy_key": ANCHOR_POLICY_KEY,
        "anchor_count": len(ordered),
        "anchor_manifest_sha256": "0" * 64,
        "anchors": ordered,
    }
    hash_payload = dict(payload)
    del hash_payload["anchor_manifest_sha256"]
    payload["anchor_manifest_sha256"] = canonical_json_sha256(hash_payload)
    return CorpusAnchorManifest.model_validate(payload)


def _mapping_manifest() -> StudyFormalMappingManifest:
    payload: dict[str, object] = {
        "manifest_schema_version": "study-formal-mapping-manifest-v1",
        "release_key": corpus_activation.V0_STRUCTURED_RELEASE_KEY,
        "study_snapshot_key": corpus_activation.STUDY_SNAPSHOT_KEY,
        "formal_snapshot_key": corpus_activation.FORMAL_MSL41_SNAPSHOT_KEY,
        "formal_snapshot_manifest_sha256": "a" * 64,
        "mappings": (
            {
                "mapping_key": corpus_activation.STUDY_FORMAL_MAPPING_KEY,
                "study_snapshot_key": corpus_activation.STUDY_SNAPSHOT_KEY,
                "study_term_key": corpus_activation.STUDY_ORTHOPOLINTOVIRALES_TERM_KEY,
                "formal_snapshot_key": corpus_activation.FORMAL_MSL41_SNAPSHOT_KEY,
                "formal_term_key": corpus_activation.FORMAL_AMPHINTOVIRALES_TERM_KEY,
                "relation": "renamed_to",
                "curation_method_key": "curation:ictv-proposal-2024.010D",
                "evidence_artifact_sha256": "b" * 64,
                "evidence_locator": "proposal 2024.010D exact rename table",
            },
        ),
    }
    return StudyFormalMappingManifest.model_validate(seal_manifest_payload(payload))


def _bridge_payload() -> dict[str, object]:
    return {
        "bridge_schema_version": "v0-lineage-anchor-bridge-v1",
        "target_role": "formal_viral_taxonomy",
        "literature_label": "Polintoviruses",
        "study_snapshot_key": corpus_activation.STUDY_SNAPSHOT_KEY,
        "study_term_key": corpus_activation.STUDY_ORTHOPOLINTOVIRALES_TERM_KEY,
        "study_canonical_name": "Orthopolintovirales",
        "formal_snapshot_key": corpus_activation.FORMAL_MSL41_SNAPSHOT_KEY,
        "formal_term_key": corpus_activation.FORMAL_AMPHINTOVIRALES_TERM_KEY,
        "formal_canonical_name": "Amphintovirales",
        "relation": "renamed_to",
        "curation_method_key": "curation:ictv-proposal-2024.010D",
        "mapping_key": stable_key(
            "study-formal-mapping",
            {
                "formal_snapshot_key": corpus_activation.FORMAL_MSL41_SNAPSHOT_KEY,
                "formal_term_key": corpus_activation.FORMAL_AMPHINTOVIRALES_TERM_KEY,
                "relation": "renamed_to",
                "study_snapshot_key": corpus_activation.STUDY_SNAPSHOT_KEY,
                "study_term_key": corpus_activation.STUDY_ORTHOPOLINTOVIRALES_TERM_KEY,
            },
        ),
        "mapping_manifest_sha256": _mapping_manifest().manifest_sha256,
    }
