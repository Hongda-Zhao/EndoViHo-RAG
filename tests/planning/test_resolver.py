"""Release-scoped exact resolver tests for Milestone 2.2."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from eve_relation_rag.planning.resolver import (
    AssemblyResolverRecord,
    CatalogReleaseResolver,
    LineageReference,
    LineageResolverRecord,
    LocusResolverRecord,
    ResolutionFailure,
    normalize_english_name,
)

RELEASE = "release:endoviho-rag:v0:20260827:002"
ASSEMBLY = "GCA_029931535.1"
LOCUS = f"locus:eve:v1:sha256:{'a' * 64}"
SOURCE_SNAPSHOT = "lineage-snapshot:ncbi-taxonomy:test"
FORMAL_SNAPSHOT = "lineage-snapshot:ictv:test"
STUDY_SNAPSHOT = "lineage-snapshot:study:zhao-v4"
EXTENDED_SNAPSHOT = "lineage-snapshot:extended:asfa-like-v1"


def _source_term(
    *,
    term_key: str = "ncbi-taxonomy:taxid:6544",
    canonical_name: str = "Bivalvia",
    aliases: tuple[str, ...] = ("Pelecypoda",),
    suggestible: bool = True,
) -> LineageResolverRecord:
    return LineageResolverRecord(
        entity_kind="source_lineage",
        term_key=term_key,
        canonical_name=canonical_name,
        aliases=aliases,
        snapshot_key=SOURCE_SNAPSHOT,
        authority_namespace="ncbi-taxonomy",
        snapshot_version="test-v1",
        scheme_kind="formal_taxonomy",
        role="assembly_source_taxonomy",
        suggestible=suggestible,
    )


def _formal_viral_term() -> LineageResolverRecord:
    return LineageResolverRecord(
        entity_kind="viral_lineage",
        term_key="ictv:orthopolintovirales",
        canonical_name="Orthopolintovirales",
        snapshot_key=FORMAL_SNAPSHOT,
        authority_namespace="ictv",
        snapshot_version="test-v1",
        scheme_kind="formal_taxonomy",
        role="formal_viral_taxonomy",
    )


def _study_viral_term() -> LineageResolverRecord:
    return LineageResolverRecord(
        entity_kind="viral_lineage",
        term_key="study:orthopolintovirales",
        canonical_name="Orthopolintovirales",
        aliases=("polinton-like",),
        snapshot_key=STUDY_SNAPSHOT,
        authority_namespace="study-defined:zhao-v4",
        snapshot_version="v4",
        scheme_kind="study_defined",
        role="study_viral_lineage",
    )


def _extended_viral_term() -> LineageResolverRecord:
    return LineageResolverRecord(
        entity_kind="viral_lineage",
        term_key="extended:asfa-like",
        canonical_name="Asfa-like",
        aliases=("Asfarviridae-like",),
        snapshot_key=EXTENDED_SNAPSHOT,
        authority_namespace="curated-extended-viral-lineage",
        snapshot_version="test-v1",
        scheme_kind="study_defined",
        role="extended_viral_lineage",
    )


def _resolver(
    *, lineages: tuple[LineageResolverRecord, ...] | None = None
) -> CatalogReleaseResolver:
    return CatalogReleaseResolver(
        release_key=RELEASE,
        assemblies=(
            AssemblyResolverRecord(
                accession_version=ASSEMBLY,
                canonical_name="Margaritifera margaritifera",
            ),
        ),
        loci=(LocusResolverRecord(locus_key=LOCUS),),
        lineages=(
            (_source_term(), _formal_viral_term(), _study_viral_term(), _extended_viral_term())
            if lineages is None
            else lineages
        ),
    )


def test_name_normalization_is_only_nfkc_trim_collapse_and_casefold() -> None:
    assert normalize_english_name("  ＢＩＶＡＬＶＩＡ   test  ") == "bivalvia test"
    assert normalize_english_name("Müller") != normalize_english_name("Muller")
    assert normalize_english_name("virus-name") != normalize_english_name("virus name")


def test_exact_assembly_and_locus_resolution_is_release_scoped() -> None:
    resolver = _resolver()

    assembly = resolver.resolve_assembly(ASSEMBLY)
    stable_assembly = resolver.resolve_assembly(f"assembly:ncbi:{ASSEMBLY}")
    locus = resolver.resolve_locus(LOCUS)

    assert assembly.stable_key == f"assembly:ncbi:{ASSEMBLY}"
    assert assembly.match_mode == "exact_identifier"
    assert stable_assembly.match_mode == "exact_stable_key"
    assert locus.stable_key == LOCUS


def test_versionless_and_absent_exact_entities_fail_without_fallback() -> None:
    resolver = _resolver()

    with pytest.raises(ResolutionFailure) as versionless:
        resolver.resolve_assembly("GCA_029931535")
    with pytest.raises(ResolutionFailure) as absent_assembly:
        resolver.resolve_assembly("GCF_000001405.40")
    with pytest.raises(ResolutionFailure) as absent_locus:
        resolver.resolve_locus(f"locus:eve:v1:sha256:{'b' * 64}")

    assert versionless.value.code == "assembly_accession_version_required"
    assert versionless.value.suggestions[0].stable_key == f"assembly:ncbi:{ASSEMBLY}"
    assert absent_assembly.value.code == "entity_not_in_release"
    assert absent_locus.value.code == "entity_not_in_release"


def test_canonical_name_precedes_alias_and_alias_is_exactly_normalized() -> None:
    resolver = _resolver()
    canonical = resolver.resolve_lineage(
        LineageReference(
            original_input="  BIVALVIA  ",
            entity_kind="source_lineage",
            role="assembly_source_taxonomy",
            name="  BIVALVIA  ",
        )
    )
    alias = resolver.resolve_lineage(
        LineageReference(
            original_input="pelecypoda",
            entity_kind="source_lineage",
            role="assembly_source_taxonomy",
            name="pelecypoda",
        )
    )

    assert canonical.match_mode == "exact_canonical_name"
    assert alias.match_mode == "exact_curated_alias"
    assert canonical.stable_key == alias.stable_key == "ncbi-taxonomy:taxid:6544"


def test_snapshot_qualified_term_requires_the_release_pinned_snapshot() -> None:
    resolver = _resolver()
    resolved = resolver.resolve_lineage(
        LineageReference(
            original_input="term ncbi-taxonomy:taxid:6544 in snapshot " + SOURCE_SNAPSHOT,
            entity_kind="source_lineage",
            role="assembly_source_taxonomy",
            term_key="ncbi-taxonomy:taxid:6544",
            snapshot_key=SOURCE_SNAPSHOT,
        )
    )

    assert resolved.match_mode == "exact_stable_key"
    assert resolved.snapshot_key == SOURCE_SNAPSHOT

    with pytest.raises(ResolutionFailure) as mismatch:
        resolver.resolve_lineage(
            LineageReference(
                original_input="wrong snapshot",
                entity_kind="source_lineage",
                role="assembly_source_taxonomy",
                term_key="ncbi-taxonomy:taxid:6544",
                snapshot_key="lineage-snapshot:ncbi-taxonomy:other",
            )
        )
    assert mismatch.value.code == "lineage_snapshot_mismatch"


def test_formal_study_and_extended_namespaces_never_cross_resolve() -> None:
    resolver = _resolver()
    formal = resolver.resolve_lineage(
        LineageReference(
            original_input="Orthopolintovirales",
            entity_kind="viral_lineage",
            role="formal_viral_taxonomy",
            name="Orthopolintovirales",
        )
    )
    study = resolver.resolve_lineage(
        LineageReference(
            original_input="Orthopolintovirales",
            entity_kind="viral_lineage",
            role="study_viral_lineage",
            name="Orthopolintovirales",
        )
    )
    extended = resolver.resolve_lineage(
        LineageReference(
            original_input="asfa-like",
            entity_kind="viral_lineage",
            role="extended_viral_lineage",
            name="asfa-like",
        )
    )

    assert formal.stable_key == "ictv:orthopolintovirales"
    assert study.stable_key == "study:orthopolintovirales"
    assert extended.stable_key == "extended:asfa-like"
    assert len({formal.snapshot_key, study.snapshot_key, extended.snapshot_key}) == 3

    with pytest.raises(ResolutionFailure) as no_cross_resolution:
        resolver.resolve_lineage(
            LineageReference(
                original_input="asfa-like",
                entity_kind="viral_lineage",
                role="formal_viral_taxonomy",
                name="asfa-like",
            )
        )
    assert no_cross_resolution.value.code == "entity_unresolved"


def test_alias_collision_is_ambiguous_and_never_selects_first() -> None:
    collision = _source_term(
        term_key="ncbi-taxonomy:taxid:9999",
        canonical_name="Other bivalve class",
        aliases=("Pelecypoda",),
    )
    resolver = _resolver(lineages=(_source_term(), collision))

    with pytest.raises(ResolutionFailure) as ambiguous:
        resolver.resolve_lineage(
            LineageReference(
                original_input="Pelecypoda",
                entity_kind="source_lineage",
                role="assembly_source_taxonomy",
                name="Pelecypoda",
            )
        )

    assert ambiguous.value.code == "entity_ambiguous"
    assert tuple(item.stable_key for item in ambiguous.value.suggestions) == (
        "ncbi-taxonomy:taxid:6544",
        "ncbi-taxonomy:taxid:9999",
    )


def test_suggestions_are_deterministic_public_catalog_values_and_capped_at_five() -> None:
    terms = tuple(
        _source_term(
            term_key=f"ncbi-taxonomy:taxid:{index}",
            canonical_name=f"Bivalvia group {index}",
            aliases=(),
        )
        for index in range(10, 16)
    )
    resolver = _resolver(lineages=terms)

    suggestions = resolver.suggest(
        "source_lineage",
        "Bivalvia group",
        role="assembly_source_taxonomy",
    )

    assert len(suggestions) == 5
    assert suggestions == tuple(
        sorted(suggestions, key=lambda item: (item.entity_kind, item.stable_key))
    )
    assert all(item.snapshot_key == SOURCE_SNAPSHOT for item in suggestions)


def test_exact_lineage_covers_all_pinned_terms_but_suggestions_are_public_only() -> None:
    unmatched_public_fact_term = _source_term(
        term_key="ncbi-taxonomy:taxid:7777",
        canonical_name="Pinned taxon without matching public loci",
        aliases=("zero-match-taxon",),
        suggestible=False,
    )
    resolver = _resolver(lineages=(_source_term(), unmatched_public_fact_term))

    exact_name = resolver.resolve_lineage(
        LineageReference(
            original_input="zero-match-taxon",
            entity_kind="source_lineage",
            role="assembly_source_taxonomy",
            name="zero-match-taxon",
        )
    )
    exact_key = resolver.resolve_lineage(
        LineageReference(
            original_input="exact zero-match term",
            entity_kind="source_lineage",
            role="assembly_source_taxonomy",
            term_key="ncbi-taxonomy:taxid:7777",
            snapshot_key=SOURCE_SNAPSHOT,
        )
    )
    suggestions = resolver.suggest(
        "source_lineage",
        "Pinned taxon without matching public",
        role="assembly_source_taxonomy",
    )

    assert exact_name.stable_key == "ncbi-taxonomy:taxid:7777"
    assert exact_name.match_mode == "exact_curated_alias"
    assert exact_key.stable_key == "ncbi-taxonomy:taxid:7777"
    assert all(item.stable_key != "ncbi-taxonomy:taxid:7777" for item in suggestions)


def test_catalog_rejects_cross_snapshot_role_and_invalid_record_shapes() -> None:
    other_snapshot = _source_term().model_copy(
        update={"term_key": "ncbi-taxonomy:taxid:2", "snapshot_key": "snapshot:other"}
    )
    with pytest.raises(ValueError, match="one snapshot per lineage role"):
        _resolver(lineages=(_source_term(), other_snapshot))
    with pytest.raises(ValidationError):
        LineageResolverRecord(
            entity_kind="viral_lineage",
            term_key="study:test",
            canonical_name="Test",
            snapshot_key=STUDY_SNAPSHOT,
            authority_namespace="study",
            snapshot_version="v1",
            scheme_kind="formal_taxonomy",
            role="study_viral_lineage",
        )
    with pytest.raises(ValidationError):
        LineageResolverRecord(
            entity_kind="viral_lineage",
            term_key="extended:test",
            canonical_name="Test",
            snapshot_key=EXTENDED_SNAPSHOT,
            authority_namespace="extended",
            snapshot_version="v1",
            scheme_kind="formal_taxonomy",
            role="extended_viral_lineage",
        )
