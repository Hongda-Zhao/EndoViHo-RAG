"""Release-scoped resolver catalog projected from public memberships only."""

from __future__ import annotations

from collections import defaultdict
from threading import Lock
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy import Engine, Select, and_, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from eve_relation_rag.db.models import (
    AssemblyTaxonAssignment,
    EVELocus,
    GenomeAssembly,
    LineageAlias,
    LineageSnapshot,
    LineageTerm,
    ReleaseAssertionMembership,
    ReleaseLocusMembership,
    ScientificAssertion,
)
from eve_relation_rag.planning.resolver import (
    AssemblyResolverRecord,
    CatalogReleaseResolver,
    LineageResolverRecord,
    LineageRole,
    LocusResolverRecord,
    ReleaseScopedEntityResolver,
    SchemeKind,
)
from eve_relation_rag.retrieval.structured.capability import ReleaseCapability
from eve_relation_rag.retrieval.structured.errors import RetrievalRefusal


class SqlAlchemyReleaseResolverFactory:
    """Build a resolver only after the release gate has issued a capability.

    Assemblies and loci are rooted in ``ReleaseLocusMembership``. Exact lineage
    resolution covers every term in each release-pinned snapshot so a legitimate
    zero-result filter remains expressible, while suggestions are restricted to
    terms connected to public locus/assertion memberships. Candidate, quarantine,
    and bare release-allowlist rows are unreachable from suggestions. Resolver
    metadata lookup is not a public fact query under the Draft B contract.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._catalogs: dict[tuple[str, str], ReleaseScopedEntityResolver] = {}
        self._cache_lock = Lock()

    def create(self, release: ReleaseCapability) -> ReleaseScopedEntityResolver:
        """Return one immutable resolver catalog for ``release``."""

        cache_key = (release.release_key, release.manifest_sha256)
        with self._cache_lock:
            cached = self._catalogs.get(cache_key)
        if cached is not None:
            return cached

        try:
            with self._engine.connect().execution_options(
                isolation_level="REPEATABLE READ",
                postgresql_readonly=True,
            ) as connection:
                with Session(bind=connection) as session, session.begin():
                    assemblies = self._assemblies(session, release)
                    loci = self._loci(session, release)
                    lineage_rows = self._lineage_rows(session, release)
                    suggestible_keys = self._suggestible_lineage_keys(session, release)
                    aliases = self._aliases(session, lineage_rows)
        except RetrievalRefusal:
            raise
        except SQLAlchemyError as exc:
            raise RetrievalRefusal(
                "structured_query_failed",
                "release-scoped entity metadata lookup failed",
            ) from exc

        try:
            lineages = tuple(
                LineageResolverRecord(
                    entity_kind=(
                        "source_lineage"
                        if row["role"] == "assembly_source_taxonomy"
                        else "viral_lineage"
                    ),
                    term_key=cast(str, row["term_key"]),
                    canonical_name=cast(str, row["canonical_name"]),
                    aliases=aliases[(cast(int, row["snapshot_id"]), cast(int, row["term_id"]))],
                    snapshot_key=cast(str, row["snapshot_key"]),
                    authority_namespace=cast(str, row["authority_namespace"]),
                    snapshot_version=cast(str, row["snapshot_version"]),
                    scheme_kind=cast(SchemeKind, row["scheme_kind"]),
                    role=cast(LineageRole, row["role"]),
                    suggestible=(
                        (
                            cast(LineageRole, row["role"]),
                            cast(int, row["snapshot_id"]),
                            cast(int, row["term_id"]),
                        )
                        in suggestible_keys
                    ),
                )
                for row in lineage_rows
            )
            resolver = CatalogReleaseResolver(
                release_key=release.release_key,
                assemblies=assemblies,
                loci=loci,
                lineages=lineages,
            )
        except (ValidationError, ValueError) as exc:
            raise RetrievalRefusal(
                "result_integrity_error",
                "public resolver metadata violates the structured query contract",
            ) from exc

        with self._cache_lock:
            return self._catalogs.setdefault(cache_key, resolver)

    @staticmethod
    def _public_loci(release: ReleaseCapability) -> Select[Any]:
        return (
            select(
                EVELocus.id.label("locus_id"),
                EVELocus.locus_key,
                EVELocus.assembly_id,
            )
            .select_from(ReleaseLocusMembership)
            .join(
                EVELocus,
                and_(
                    EVELocus.release_id == ReleaseLocusMembership.release_id,
                    EVELocus.id == ReleaseLocusMembership.locus_id,
                ),
            )
            .where(ReleaseLocusMembership.release_id == release.release_id)
        )

    def _assemblies(
        self,
        session: Session,
        release: ReleaseCapability,
    ) -> tuple[AssemblyResolverRecord, ...]:
        public_loci = self._public_loci(release).subquery("resolver_public_loci")
        rows = session.execute(
            select(
                GenomeAssembly.accession_version,
                GenomeAssembly.source_organism_name,
            )
            .select_from(public_loci)
            .join(GenomeAssembly, GenomeAssembly.id == public_loci.c.assembly_id)
            .distinct()
            .order_by(GenomeAssembly.accession_version.asc())
        )
        return tuple(
            AssemblyResolverRecord(
                accession_version=row.accession_version,
                canonical_name=row.source_organism_name,
            )
            for row in rows
        )

    def _loci(
        self,
        session: Session,
        release: ReleaseCapability,
    ) -> tuple[LocusResolverRecord, ...]:
        rows = session.execute(
            self._public_loci(release)
            .with_only_columns(EVELocus.locus_key)
            .distinct()
            .order_by(EVELocus.locus_key.asc())
        )
        return tuple(LocusResolverRecord(locus_key=row.locus_key) for row in rows)

    def _lineage_rows(
        self,
        session: Session,
        release: ReleaseCapability,
    ) -> tuple[dict[str, object], ...]:
        rows: list[dict[str, object]] = []
        for role in sorted(release.lineage_dependencies):
            binding = release.lineage_dependencies[role]
            namespace_statement = (
                select(
                    LineageTerm.id.label("term_id"),
                    LineageTerm.snapshot_id,
                    LineageTerm.term_key,
                    LineageTerm.canonical_name,
                    LineageSnapshot.snapshot_key,
                    LineageSnapshot.authority_namespace,
                    LineageSnapshot.version.label("snapshot_version"),
                    LineageSnapshot.scheme_kind,
                )
                .select_from(LineageTerm)
                .join(LineageSnapshot, LineageSnapshot.id == LineageTerm.snapshot_id)
                .where(LineageTerm.snapshot_id == binding.snapshot_id)
                .order_by(LineageTerm.term_key.asc())
            )
            for namespace_row in session.execute(namespace_statement):
                projected = dict(namespace_row._mapping)
                projected["role"] = role
                rows.append(projected)

        unique = {
            (
                cast(str, row["role"]),
                cast(int, row["snapshot_id"]),
                cast(int, row["term_id"]),
            ): row
            for row in rows
        }
        return tuple(unique[key] for key in sorted(unique))

    def _suggestible_lineage_keys(
        self,
        session: Session,
        release: ReleaseCapability,
    ) -> frozenset[tuple[LineageRole, int, int]]:
        keys: set[tuple[LineageRole, int, int]] = set()
        source_binding = release.lineage_dependencies.get("assembly_source_taxonomy")
        if source_binding is not None:
            public_assemblies = (
                self._public_loci(release)
                .with_only_columns(EVELocus.assembly_id)
                .distinct()
                .subquery("resolver_public_assemblies")
            )
            source_statement = (
                select(
                    AssemblyTaxonAssignment.snapshot_id,
                    AssemblyTaxonAssignment.term_id,
                )
                .select_from(public_assemblies)
                .join(
                    AssemblyTaxonAssignment,
                    and_(
                        AssemblyTaxonAssignment.release_id == release.release_id,
                        AssemblyTaxonAssignment.assembly_id == public_assemblies.c.assembly_id,
                        AssemblyTaxonAssignment.snapshot_id == source_binding.snapshot_id,
                        AssemblyTaxonAssignment.snapshot_role == "assembly_source_taxonomy",
                    ),
                )
                .distinct()
            )
            keys.update(
                (
                    "assembly_source_taxonomy",
                    int(row.snapshot_id),
                    int(row.term_id),
                )
                for row in session.execute(source_statement)
            )

        viral_bindings = tuple(
            binding
            for role, binding in release.lineage_dependencies.items()
            if role in {"formal_viral_taxonomy", "study_viral_lineage"}
        )
        if viral_bindings:
            viral_statement = (
                select(
                    ScientificAssertion.lineage_snapshot_role,
                    ScientificAssertion.lineage_snapshot_id,
                    ScientificAssertion.lineage_term_id,
                )
                .select_from(ReleaseAssertionMembership)
                .join(
                    ScientificAssertion,
                    and_(
                        ScientificAssertion.release_id == ReleaseAssertionMembership.release_id,
                        ScientificAssertion.id == ReleaseAssertionMembership.assertion_id,
                    ),
                )
                .where(
                    ReleaseAssertionMembership.release_id == release.release_id,
                    ScientificAssertion.assertion_type == "viral_major_taxon",
                    or_(
                        *(
                            and_(
                                ScientificAssertion.lineage_snapshot_role == binding.role,
                                ScientificAssertion.lineage_snapshot_id == binding.snapshot_id,
                            )
                            for binding in viral_bindings
                        )
                    ),
                )
                .distinct()
            )
            for row in session.execute(viral_statement):
                if (
                    row.lineage_snapshot_role is not None
                    and row.lineage_snapshot_id is not None
                    and row.lineage_term_id is not None
                ):
                    keys.add(
                        (
                            cast(LineageRole, row.lineage_snapshot_role),
                            int(row.lineage_snapshot_id),
                            int(row.lineage_term_id),
                        )
                    )
        return frozenset(keys)

    @staticmethod
    def _aliases(
        session: Session,
        lineage_rows: tuple[dict[str, object], ...],
    ) -> defaultdict[tuple[int, int], tuple[str, ...]]:
        grouped: defaultdict[tuple[int, int], list[str]] = defaultdict(list)
        snapshot_ids = tuple(sorted({cast(int, row["snapshot_id"]) for row in lineage_rows}))
        if snapshot_ids:
            statement = (
                select(
                    LineageAlias.snapshot_id,
                    LineageAlias.term_id,
                    LineageAlias.alias,
                )
                .where(
                    LineageAlias.snapshot_id.in_(snapshot_ids),
                    LineageAlias.locale == "en",
                )
                .order_by(
                    LineageAlias.snapshot_id.asc(),
                    LineageAlias.term_id.asc(),
                    LineageAlias.normalized_alias.asc(),
                )
            )
            for row in session.execute(statement):
                grouped[(int(row.snapshot_id), int(row.term_id))].append(row.alias)

        frozen: defaultdict[tuple[int, int], tuple[str, ...]] = defaultdict(tuple)
        frozen.update({key: tuple(values) for key, values in grouped.items()})
        return frozen
