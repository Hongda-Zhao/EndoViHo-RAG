"""Deterministic, release-scoped entity resolution for structured planning.

The resolver deliberately has no database session and cannot choose a release at call
time.  A production adapter must be constructed *after* the published-release gate.
Assemblies and loci come only from that release's public memberships; lineage terms come
from its pinned snapshots, while only public-connected terms may be suggestions.  The
in-memory catalog implementation is the reference implementation and test seam.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eve_relation_rag.domain.keys import is_release_key, is_versioned_assembly_accession
from eve_relation_rag.retrieval.structured.results import EntitySuggestion, ResolvedEntity

type EntityKind = Literal["assembly", "locus", "source_lineage", "viral_lineage"]
type LineageRole = Literal[
    "assembly_source_taxonomy",
    "formal_viral_taxonomy",
    "study_viral_lineage",
]
type SchemeKind = Literal["formal_taxonomy", "study_defined"]
type ResolverErrorCode = Literal[
    "assembly_accession_version_required",
    "entity_unresolved",
    "entity_ambiguous",
    "entity_not_in_release",
    "lineage_snapshot_mismatch",
    "lineage_role_ambiguous",
]

_RELEASE_PREFIX = "release:endoviho-rag:v0:"
_ASSEMBLY_PREFIX = "assembly:ncbi:"
_VERSIONLESS_ASSEMBLY_RE = re.compile(r"^(?:GCA|GCF)_[0-9]+$")
_LOCUS_KEY_RE = re.compile(r"^locus:eve:v1:sha256:[0-9a-f]{64}$")


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def normalize_english_name(value: str) -> str:
    """Apply the complete and intentionally small Draft B name normalization."""

    return " ".join(unicodedata.normalize("NFKC", value).strip().split()).casefold()


class AssemblyResolverRecord(_FrozenModel):
    """One assembly known to be represented by a public locus in one release."""

    accession_version: str = Field(min_length=1, max_length=255)
    canonical_name: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_accession(self) -> Self:
        if not is_versioned_assembly_accession(self.accession_version):
            raise ValueError("assembly resolver records require an exact accession.version")
        return self


class LocusResolverRecord(_FrozenModel):
    """One locus selected by ReleaseLocusMembership in one release."""

    locus_key: str = Field(min_length=1, max_length=255)
    canonical_name: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_key(self) -> Self:
        if _LOCUS_KEY_RE.fullmatch(self.locus_key) is None:
            raise ValueError("locus resolver records require an exact V1 locus key")
        return self


class LineageResolverRecord(_FrozenModel):
    """One release-pinned lineage term reachable from public memberships."""

    entity_kind: Literal["source_lineage", "viral_lineage"]
    term_key: str = Field(min_length=1, max_length=255, pattern=r"^\S+$")
    canonical_name: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    snapshot_key: str = Field(min_length=1, max_length=255, pattern=r"^\S+$")
    authority_namespace: str = Field(min_length=1, max_length=255, pattern=r"^\S+$")
    snapshot_version: str = Field(min_length=1)
    scheme_kind: SchemeKind
    role: LineageRole
    suggestible: bool = True

    @model_validator(mode="after")
    def validate_namespace(self) -> Self:
        if not self.canonical_name.strip():
            raise ValueError("canonical_name must contain non-whitespace text")
        if any(not alias.strip() for alias in self.aliases):
            raise ValueError("aliases must contain non-whitespace text")
        normalized_aliases = tuple(normalize_english_name(alias) for alias in self.aliases)
        if len(normalized_aliases) != len(set(normalized_aliases)):
            raise ValueError("a lineage record cannot repeat one normalized alias")
        if self.entity_kind == "source_lineage":
            if self.role != "assembly_source_taxonomy":
                raise ValueError("source lineage records require assembly_source_taxonomy")
        elif self.role == "assembly_source_taxonomy":
            raise ValueError("viral lineage records require a viral lineage role")
        expected_scheme = (
            "study_defined" if self.role == "study_viral_lineage" else "formal_taxonomy"
        )
        if self.scheme_kind != expected_scheme:
            raise ValueError("lineage role and scheme_kind are inconsistent")
        return self


class LineageReference(_FrozenModel):
    """One role-qualified lineage mention extracted by the controlled parser."""

    original_input: str = Field(min_length=1)
    entity_kind: Literal["source_lineage", "viral_lineage"]
    role: LineageRole
    term_key: str | None = Field(default=None, min_length=1, max_length=255, pattern=r"^\S+$")
    snapshot_key: str | None = Field(default=None, min_length=1, max_length=255, pattern=r"^\S+$")
    name: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_reference_shape(self) -> Self:
        exact = self.term_key is not None or self.snapshot_key is not None
        if exact:
            if self.term_key is None or self.snapshot_key is None or self.name is not None:
                raise ValueError("exact lineage references require both term and snapshot keys")
        elif self.name is None or not self.name.strip():
            raise ValueError("a lineage reference requires an exact key pair or a name")
        if self.entity_kind == "source_lineage" and self.role != "assembly_source_taxonomy":
            raise ValueError("source lineage references require assembly_source_taxonomy")
        if self.entity_kind == "viral_lineage" and self.role == "assembly_source_taxonomy":
            raise ValueError("viral lineage references require a viral role")
        return self


class ResolutionFailure(Exception):
    """A stable fail-closed resolver outcome with public-safe suggestions only."""

    def __init__(
        self,
        code: ResolverErrorCode,
        message: str,
        *,
        suggestions: tuple[EntitySuggestion, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.suggestions = suggestions


@runtime_checkable
class ReleaseScopedEntityResolver(Protocol):
    """Adapter boundary for metadata resolution inside one gate-qualified release.

    Implementations must expose only release-pinned metadata for ``release_key``.  Exact
    lineage resolution covers all terms in a pinned snapshot so a valid zero-result query
    remains possible; fuzzy/prefix suggestions are limited to public-connected terms.  An
    implementation must not consult candidate loci, quarantine rows, a bare assembly
    allowlist, or assertions outside ReleaseAssertionMembership.
    """

    @property
    def release_key(self) -> str: ...

    def resolve_assembly(self, original_input: str) -> ResolvedEntity: ...

    def resolve_locus(self, original_input: str) -> ResolvedEntity: ...

    def resolve_lineage(self, reference: LineageReference) -> ResolvedEntity: ...

    def suggest(
        self,
        entity_kind: EntityKind,
        original_input: str,
        *,
        role: LineageRole | None = None,
    ) -> tuple[EntitySuggestion, ...]: ...


def _score_suggestion(query: str, candidate: str) -> tuple[int, float] | None:
    if not query or not candidate:
        return None
    ratio = difflib.SequenceMatcher(a=query, b=candidate, autojunk=False).ratio()
    if candidate.startswith(query) or query.startswith(candidate):
        return (2, ratio)
    if query in candidate or candidate in query:
        return (1, ratio)
    if ratio >= 0.6:
        return (0, ratio)
    return None


class CatalogReleaseResolver:
    """Deterministic reference resolver over an already public, one-release catalog."""

    def __init__(
        self,
        *,
        release_key: str,
        assemblies: tuple[AssemblyResolverRecord, ...] = (),
        loci: tuple[LocusResolverRecord, ...] = (),
        lineages: tuple[LineageResolverRecord, ...] = (),
    ) -> None:
        if not release_key.startswith(_RELEASE_PREFIX) or not is_release_key(release_key):
            raise ValueError("resolver release_key must use the exact EndoViHo V0 grammar")
        assembly_keys = tuple(item.accession_version for item in assemblies)
        locus_keys = tuple(item.locus_key for item in loci)
        lineage_keys = tuple((item.role, item.snapshot_key, item.term_key) for item in lineages)
        if len(assembly_keys) != len(set(assembly_keys)):
            raise ValueError("resolver assembly records must be unique")
        if len(locus_keys) != len(set(locus_keys)):
            raise ValueError("resolver locus records must be unique")
        if len(lineage_keys) != len(set(lineage_keys)):
            raise ValueError("resolver lineage records must be unique")
        snapshots_by_role: dict[LineageRole, set[str]] = {}
        for item in lineages:
            snapshots_by_role.setdefault(item.role, set()).add(item.snapshot_key)
        if any(len(keys) != 1 for keys in snapshots_by_role.values()):
            raise ValueError("a release resolver may pin only one snapshot per lineage role")

        self._release_key = release_key
        self._assemblies = tuple(sorted(assemblies, key=lambda item: item.accession_version))
        self._loci = tuple(sorted(loci, key=lambda item: item.locus_key))
        self._lineages = tuple(
            sorted(lineages, key=lambda item: (item.role, item.snapshot_key, item.term_key))
        )
        self._snapshots_by_role = {
            role: next(iter(snapshot_keys)) for role, snapshot_keys in snapshots_by_role.items()
        }

    @property
    def release_key(self) -> str:
        return self._release_key

    def resolve_assembly(self, original_input: str) -> ResolvedEntity:
        is_stable_key = original_input.startswith(_ASSEMBLY_PREFIX)
        accession = original_input.removeprefix(_ASSEMBLY_PREFIX)
        if _VERSIONLESS_ASSEMBLY_RE.fullmatch(accession):
            raise ResolutionFailure(
                "assembly_accession_version_required",
                "Assembly accessions require an explicit version.",
                suggestions=self.suggest("assembly", original_input),
            )
        if not is_versioned_assembly_accession(accession):
            raise ResolutionFailure(
                "entity_unresolved",
                "The assembly identifier is not recognized.",
                suggestions=self.suggest("assembly", original_input),
            )
        record = next(
            (item for item in self._assemblies if item.accession_version == accession),
            None,
        )
        if record is None:
            raise ResolutionFailure(
                "entity_not_in_release",
                "The assembly is not represented by a public locus in this release.",
                suggestions=self.suggest("assembly", original_input),
            )
        return ResolvedEntity(
            original_input=original_input,
            entity_kind="assembly",
            match_mode="exact_stable_key" if is_stable_key else "exact_identifier",
            stable_key=f"{_ASSEMBLY_PREFIX}{accession}",
            canonical_name=record.canonical_name,
        )

    def resolve_locus(self, original_input: str) -> ResolvedEntity:
        if _LOCUS_KEY_RE.fullmatch(original_input) is None:
            raise ResolutionFailure(
                "entity_unresolved",
                "The locus key is not recognized.",
                suggestions=self.suggest("locus", original_input),
            )
        record = next((item for item in self._loci if item.locus_key == original_input), None)
        if record is None:
            raise ResolutionFailure(
                "entity_not_in_release",
                "The locus is not a public member of this release.",
                suggestions=self.suggest("locus", original_input),
            )
        return ResolvedEntity(
            original_input=original_input,
            entity_kind="locus",
            match_mode="exact_stable_key",
            stable_key=record.locus_key,
            canonical_name=record.canonical_name,
        )

    def resolve_lineage(self, reference: LineageReference) -> ResolvedEntity:
        candidates = tuple(item for item in self._lineages if item.role == reference.role)
        if reference.term_key is not None:
            expected_snapshot = self._snapshots_by_role.get(reference.role)
            if expected_snapshot is None or reference.snapshot_key != expected_snapshot:
                raise ResolutionFailure(
                    "lineage_snapshot_mismatch",
                    "The lineage snapshot is not the one pinned to this release role.",
                )
            exact = tuple(
                item
                for item in candidates
                if item.snapshot_key == reference.snapshot_key
                and item.term_key == reference.term_key
            )
            if not exact:
                raise ResolutionFailure(
                    "entity_unresolved",
                    "The lineage term is not present in the pinned public namespace.",
                    suggestions=self.suggest(
                        reference.entity_kind,
                        reference.term_key,
                        role=reference.role,
                    ),
                )
            return self._resolved_lineage(reference.original_input, exact[0], "exact_stable_key")

        assert reference.name is not None
        normalized = normalize_english_name(reference.name)
        canonical = tuple(
            item for item in candidates if normalize_english_name(item.canonical_name) == normalized
        )
        if canonical:
            return self._one_lineage_or_ambiguous(
                reference.original_input,
                canonical,
                "exact_canonical_name",
            )
        aliases = tuple(
            item
            for item in candidates
            if normalized in {normalize_english_name(alias) for alias in item.aliases}
        )
        if aliases:
            return self._one_lineage_or_ambiguous(
                reference.original_input,
                aliases,
                "exact_curated_alias",
            )
        raise ResolutionFailure(
            "entity_unresolved",
            "The lineage name or curated alias is not resolved in this release.",
            suggestions=self.suggest(
                reference.entity_kind,
                reference.name,
                role=reference.role,
            ),
        )

    def _one_lineage_or_ambiguous(
        self,
        original_input: str,
        records: tuple[LineageResolverRecord, ...],
        match_mode: Literal["exact_canonical_name", "exact_curated_alias"],
    ) -> ResolvedEntity:
        unique = {item.term_key: item for item in records}
        if len(unique) != 1:
            suggestions = tuple(
                sorted(
                    (
                        self._lineage_suggestion(item)
                        for item in unique.values()
                        if item.suggestible
                    ),
                    key=lambda item: (item.entity_kind, item.stable_key),
                )[:5]
            )
            raise ResolutionFailure(
                "entity_ambiguous",
                "The lineage mention matches more than one public term.",
                suggestions=suggestions,
            )
        return self._resolved_lineage(original_input, next(iter(unique.values())), match_mode)

    @staticmethod
    def _resolved_lineage(
        original_input: str,
        record: LineageResolverRecord,
        match_mode: Literal[
            "exact_stable_key",
            "exact_canonical_name",
            "exact_curated_alias",
        ],
    ) -> ResolvedEntity:
        return ResolvedEntity(
            original_input=original_input,
            entity_kind=record.entity_kind,
            match_mode=match_mode,
            stable_key=record.term_key,
            canonical_name=record.canonical_name,
            snapshot_key=record.snapshot_key,
            authority_namespace=record.authority_namespace,
            snapshot_version=record.snapshot_version,
            scheme_kind=record.scheme_kind,
            role=record.role,
        )

    @staticmethod
    def _lineage_suggestion(record: LineageResolverRecord) -> EntitySuggestion:
        return EntitySuggestion(
            entity_kind=record.entity_kind,
            stable_key=record.term_key,
            canonical_name=record.canonical_name,
            snapshot_key=record.snapshot_key,
            role=record.role,
        )

    def suggest(
        self,
        entity_kind: EntityKind,
        original_input: str,
        *,
        role: LineageRole | None = None,
    ) -> tuple[EntitySuggestion, ...]:
        query = normalize_english_name(original_input)
        ranked: list[tuple[int, float, EntitySuggestion]] = []
        if entity_kind == "assembly":
            query = query.removeprefix(_ASSEMBLY_PREFIX)
            for assembly_record in self._assemblies:
                candidate_values = (
                    assembly_record.accession_version,
                    assembly_record.canonical_name or "",
                )
                scores = tuple(
                    score
                    for value in candidate_values
                    if (score := _score_suggestion(query, normalize_english_name(value)))
                    is not None
                )
                if scores:
                    ranked.append(
                        (
                            *max(scores),
                            EntitySuggestion(
                                entity_kind="assembly",
                                stable_key=(
                                    f"{_ASSEMBLY_PREFIX}{assembly_record.accession_version}"
                                ),
                                canonical_name=assembly_record.canonical_name,
                            ),
                        )
                    )
        elif entity_kind == "locus":
            for locus_record in self._loci:
                score = _score_suggestion(
                    query,
                    normalize_english_name(locus_record.locus_key),
                )
                if score is not None:
                    ranked.append(
                        (
                            *score,
                            EntitySuggestion(
                                entity_kind="locus",
                                stable_key=locus_record.locus_key,
                                canonical_name=locus_record.canonical_name,
                            ),
                        )
                    )
        else:
            if role is None:
                return ()
            for lineage_record in self._lineages:
                if (
                    lineage_record.entity_kind != entity_kind
                    or lineage_record.role != role
                    or not lineage_record.suggestible
                ):
                    continue
                scores = tuple(
                    score
                    for value in (
                        lineage_record.canonical_name,
                        *lineage_record.aliases,
                        lineage_record.term_key,
                    )
                    if (score := _score_suggestion(query, normalize_english_name(value)))
                    is not None
                )
                if scores:
                    ranked.append((*max(scores), self._lineage_suggestion(lineage_record)))

        ranked.sort(key=lambda item: (-item[0], -item[1], item[2].stable_key))
        selected: dict[tuple[str, str], EntitySuggestion] = {}
        for _, _, suggestion in ranked:
            selected.setdefault((suggestion.entity_kind, suggestion.stable_key), suggestion)
            if len(selected) == 5:
                break
        return tuple(
            sorted(selected.values(), key=lambda item: (item.entity_kind, item.stable_key))
        )


__all__ = [
    "AssemblyResolverRecord",
    "CatalogReleaseResolver",
    "EntityKind",
    "LineageReference",
    "LineageResolverRecord",
    "LineageRole",
    "LocusResolverRecord",
    "ReleaseScopedEntityResolver",
    "ResolutionFailure",
    "normalize_english_name",
]
