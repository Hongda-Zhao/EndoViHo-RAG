"""Derive trusted structured targets and resolve exact curated corpus anchors.

The structured response supplies targets, never anchor identities.  Anchor keys remain
curator-authored M3 data and are accepted only after their complete persisted preimage and
typed-anchor checksum have been reconstructed and verified inside one corpus capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

from pydantic import TypeAdapter
from sqlalchemy import Engine, and_, or_, select
from sqlalchemy.orm import Session

from eve_relation_rag.db.models import (
    CorpusDocumentMembership,
    Document,
    DocumentAnchor,
)
from eve_relation_rag.literature.capability import CorpusCapability
from eve_relation_rag.literature.contracts import RetrievalAnchor
from eve_relation_rag.literature.hashing import anchor_key, canonical_json_sha256
from eve_relation_rag.planning.query_plans import (
    AssemblyFilter,
    FilteredScope,
    LocusFilter,
    SourceLineageFilter,
    ViralLineageFilter,
)
from eve_relation_rag.retrieval.structured.results import (
    AggregateData,
    AssemblyDetailData,
    AssemblyPageData,
    AssemblySummary,
    LineageRef,
    LocusDetailData,
    LocusPageData,
    LocusSummary,
    QuerySuccess,
    SourceTaxonPageData,
)

type StructuredAnchorTargetType = Literal["locus", "assembly", "lineage", "method"]
type StructuredAnchorDiagnostic = Literal["structured_anchor_unmatched"]
type StructuredAnchorResolutionErrorCode = Literal[
    "anchor_integrity_error", "anchor_limit_exceeded"
]

_MAX_ANCHORS = 64
_TARGET_TYPE_ORDER: dict[StructuredAnchorTargetType, int] = {
    "locus": 0,
    "assembly": 1,
    "lineage": 2,
    "method": 3,
}
_ANCHOR_ADAPTER: TypeAdapter[RetrievalAnchor] = TypeAdapter(RetrievalAnchor)


class StructuredAnchorResolutionError(RuntimeError):
    """Stable fail-closed refusal raised before literature retrieval is invoked."""

    def __init__(
        self,
        code: StructuredAnchorResolutionErrorCode,
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class StructuredAnchorTarget:
    """One exact target derived only from a validated structured response."""

    target_type: StructuredAnchorTargetType
    locus_key: str | None = None
    assembly_key: str | None = None
    snapshot_key: str | None = None
    term_key: str | None = None
    method_definition_key: str | None = None

    def __post_init__(self) -> None:
        expected = {
            "locus": (self.locus_key is not None, False, False, False),
            "assembly": (False, self.assembly_key is not None, False, False),
            "lineage": (
                False,
                False,
                self.snapshot_key is not None and self.term_key is not None,
                False,
            ),
            "method": (False, False, False, self.method_definition_key is not None),
        }[self.target_type]
        observed = (
            self.locus_key is not None,
            self.assembly_key is not None,
            self.snapshot_key is not None or self.term_key is not None,
            self.method_definition_key is not None,
        )
        if observed != expected:
            raise ValueError("structured anchor target fields do not match target_type")

    def sort_key(self) -> tuple[int, str, str]:
        """Return the frozen type-first, lexical-within-type target ordering."""

        first = cast(
            str,
            self.locus_key or self.assembly_key or self.snapshot_key or self.method_definition_key,
        )
        second = self.term_key or ""
        return (_TARGET_TYPE_ORDER[self.target_type], first, second)

    def anchor_target_payload(self) -> dict[str, str]:
        """Return the M3 typed-anchor target portion, excluding ``anchor_key``."""

        if self.target_type == "locus":
            return {"anchor_type": "locus", "locus_key": cast(str, self.locus_key)}
        if self.target_type == "assembly":
            return {
                "anchor_type": "assembly",
                "assembly_key": cast(str, self.assembly_key),
            }
        if self.target_type == "lineage":
            return {
                "anchor_type": "lineage",
                "snapshot_key": cast(str, self.snapshot_key),
                "term_key": cast(str, self.term_key),
            }
        return {
            "anchor_type": "method",
            "method_definition_key": cast(str, self.method_definition_key),
        }


@dataclass(frozen=True, slots=True)
class StructuredAnchorResolution:
    """Canonical resolved anchors plus deterministic unmatched-target diagnostics."""

    targets: tuple[StructuredAnchorTarget, ...]
    anchors: tuple[RetrievalAnchor, ...]
    unmatched_targets: tuple[StructuredAnchorTarget, ...]
    diagnostics: tuple[StructuredAnchorDiagnostic, ...]


def extract_structured_anchor_targets(
    query_success: QuerySuccess,
) -> tuple[StructuredAnchorTarget, ...]:
    """Round-trip validate and extract the four approved structured target classes."""

    success = _round_trip_query_success(query_success)
    targets: set[StructuredAnchorTarget] = set()

    scope = success.query_plan.scope
    if isinstance(scope, FilteredScope):
        for query_filter in scope.filters:
            if isinstance(query_filter, LocusFilter):
                targets.add(
                    StructuredAnchorTarget(
                        target_type="locus",
                        locus_key=query_filter.locus_key,
                    )
                )
            elif isinstance(query_filter, AssemblyFilter):
                targets.add(
                    StructuredAnchorTarget(
                        target_type="assembly",
                        assembly_key=query_filter.assembly_key,
                    )
                )
            elif isinstance(query_filter, (SourceLineageFilter, ViralLineageFilter)):
                targets.add(
                    StructuredAnchorTarget(
                        target_type="lineage",
                        snapshot_key=query_filter.snapshot_key,
                        term_key=query_filter.term_key,
                    )
                )

    data = success.structured_result.data
    if isinstance(data, AssemblyDetailData):
        _add_assembly_targets(targets, data.assembly)
    elif isinstance(data, LocusDetailData):
        _add_locus_targets(targets, data.locus)
        for assertion in data.public_assertions:
            targets.add(
                StructuredAnchorTarget(
                    target_type="method",
                    method_definition_key=assertion.method_definition_key,
                )
            )
            if assertion.lineage is not None:
                _add_lineage_target(targets, assertion.lineage)
    elif isinstance(data, LocusPageData):
        for locus in data.items:
            _add_locus_targets(targets, locus)
    elif isinstance(data, AssemblyPageData):
        for assembly in data.items:
            _add_assembly_targets(targets, assembly)
    elif isinstance(data, SourceTaxonPageData):
        for source_taxon in data.items:
            _add_lineage_target(targets, source_taxon.lineage)
    elif not isinstance(data, AggregateData):  # pragma: no cover - closed Pydantic union
        raise StructuredAnchorResolutionError(
            "anchor_integrity_error",
            "validated structured result has an unsupported data variant",
        )

    ordered = tuple(sorted(targets, key=StructuredAnchorTarget.sort_key))
    if len(ordered) > _MAX_ANCHORS:
        raise StructuredAnchorResolutionError(
            "anchor_limit_exceeded",
            "structured result contains more than 64 distinct anchor targets",
        )
    return ordered


class StructuredAnchorResolver:
    """Resolve trusted targets to actual, checksum-valid M3 anchors in one corpus."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def resolve(
        self,
        query_success: QuerySuccess,
        corpus: CorpusCapability,
    ) -> StructuredAnchorResolution:
        """Return exact curated anchors; unmatched targets remain explicit diagnostics."""

        targets = extract_structured_anchor_targets(query_success)
        if not targets:
            return StructuredAnchorResolution(
                targets=(),
                anchors=(),
                unmatched_targets=(),
                diagnostics=("structured_anchor_unmatched",),
            )

        try:
            predicates = tuple(_target_predicate(target) for target in targets)
            membership_manifest_row = CorpusDocumentMembership.manifest_row.label(
                "membership_manifest_row"
            )
            statement = (
                select(DocumentAnchor, Document.document_key, membership_manifest_row)
                .join(Document, Document.id == DocumentAnchor.document_id)
                .join(
                    CorpusDocumentMembership,
                    and_(
                        CorpusDocumentMembership.release_id == DocumentAnchor.release_id,
                        CorpusDocumentMembership.document_id == DocumentAnchor.document_id,
                    ),
                )
                .where(
                    DocumentAnchor.release_id == corpus.release_id,
                    or_(*predicates),
                )
                .order_by(DocumentAnchor.anchor_key)
            )
            with self._engine.connect().execution_options(postgresql_readonly=True) as connection:
                with Session(bind=connection, expire_on_commit=False) as session, session.begin():
                    rows = tuple(session.execute(statement))

            anchors: list[RetrievalAnchor] = []
            matched_targets: set[StructuredAnchorTarget] = set()
            seen_anchor_keys: set[str] = set()
            requested_targets = set(targets)
            for result in rows:
                row = result.DocumentAnchor
                if row.anchor_key in seen_anchor_keys:
                    raise StructuredAnchorResolutionError(
                        "anchor_integrity_error",
                        "corpus anchor query returned a duplicate anchor identity",
                    )
                anchor = _validated_stored_anchor(
                    row,
                    document_key=result.document_key,
                    membership_manifest_row=result.membership_manifest_row,
                )
                resolved_target = _target_from_anchor(anchor)
                if resolved_target not in requested_targets:
                    raise StructuredAnchorResolutionError(
                        "anchor_integrity_error",
                        "corpus anchor resolved outside the requested exact targets",
                    )
                seen_anchor_keys.add(row.anchor_key)
                anchors.append(anchor)
                matched_targets.add(resolved_target)

            if len(anchors) > _MAX_ANCHORS:
                raise StructuredAnchorResolutionError(
                    "anchor_limit_exceeded",
                    "structured targets resolve to more than 64 curated anchors",
                )
            canonical_anchors = tuple(sorted(anchors, key=lambda anchor: anchor.anchor_key))
            unmatched = tuple(target for target in targets if target not in matched_targets)
            diagnostics: tuple[StructuredAnchorDiagnostic, ...] = (
                ("structured_anchor_unmatched",) if unmatched else ()
            )
            return StructuredAnchorResolution(
                targets=targets,
                anchors=canonical_anchors,
                unmatched_targets=unmatched,
                diagnostics=diagnostics,
            )
        except StructuredAnchorResolutionError:
            raise
        except Exception as exc:
            raise StructuredAnchorResolutionError(
                "anchor_integrity_error",
                "structured anchor resolution failed closed",
            ) from exc


def _round_trip_query_success(query_success: QuerySuccess) -> QuerySuccess:
    try:
        return QuerySuccess.model_validate_json(query_success.model_dump_json())
    except Exception as exc:
        raise StructuredAnchorResolutionError(
            "anchor_integrity_error",
            "structured query success failed round-trip validation",
        ) from exc


def _add_lineage_target(
    targets: set[StructuredAnchorTarget],
    lineage: LineageRef,
) -> None:
    targets.add(
        StructuredAnchorTarget(
            target_type="lineage",
            snapshot_key=lineage.snapshot_key,
            term_key=lineage.term_key,
        )
    )


def _add_assembly_targets(
    targets: set[StructuredAnchorTarget],
    assembly: AssemblySummary,
) -> None:
    targets.add(
        StructuredAnchorTarget(
            target_type="assembly",
            assembly_key=assembly.assembly_key,
        )
    )
    _add_lineage_target(targets, assembly.source_taxon)


def _add_locus_targets(
    targets: set[StructuredAnchorTarget],
    locus: LocusSummary,
) -> None:
    targets.add(StructuredAnchorTarget(target_type="locus", locus_key=locus.locus_key))
    targets.add(
        StructuredAnchorTarget(
            target_type="assembly",
            assembly_key=locus.assembly_key,
        )
    )
    _add_lineage_target(targets, locus.source_taxon)
    for lineage in locus.viral_lineages:
        _add_lineage_target(targets, lineage)


def _target_predicate(target: StructuredAnchorTarget) -> Any:
    if target.target_type == "locus":
        return and_(
            DocumentAnchor.anchor_type == "locus",
            DocumentAnchor.locus_key == target.locus_key,
        )
    if target.target_type == "assembly":
        return and_(
            DocumentAnchor.anchor_type == "assembly",
            DocumentAnchor.assembly_key == target.assembly_key,
        )
    if target.target_type == "lineage":
        return and_(
            DocumentAnchor.anchor_type == "lineage",
            DocumentAnchor.lineage_snapshot_key == target.snapshot_key,
            DocumentAnchor.lineage_term_key == target.term_key,
        )
    return and_(
        DocumentAnchor.anchor_type == "method",
        DocumentAnchor.method_definition_key == target.method_definition_key,
    )


def _validated_stored_anchor(
    row: DocumentAnchor,
    *,
    document_key: str,
    membership_manifest_row: int,
) -> RetrievalAnchor:
    if row.manifest_row != membership_manifest_row:
        raise StructuredAnchorResolutionError(
            "anchor_integrity_error",
            "stored anchor manifest row does not match corpus membership",
        )
    if not _stored_target_shape_is_exact(row):
        raise StructuredAnchorResolutionError(
            "anchor_integrity_error",
            "stored anchor target fields do not match anchor_type",
        )
    payload: dict[str, Any] = {
        "anchor_key": row.anchor_key,
        "anchor_type": row.anchor_type,
    }
    if row.anchor_type == "locus":
        payload["locus_key"] = row.locus_key
    elif row.anchor_type == "assembly":
        payload["assembly_key"] = row.assembly_key
    elif row.anchor_type == "lineage":
        payload["snapshot_key"] = row.lineage_snapshot_key
        payload["term_key"] = row.lineage_term_key
    elif row.anchor_type == "method":
        payload["method_definition_key"] = row.method_definition_key
    else:
        raise StructuredAnchorResolutionError(
            "anchor_integrity_error",
            "stored anchor is not an approved structured anchor type",
        )
    try:
        anchor = _ANCHOR_ADAPTER.validate_python(payload)
        target = anchor.model_dump(mode="python")
        del target["anchor_key"]
        preimage = {
            "anchor_schema_version": "document-anchor-v1",
            "curation_method": row.curation_method,
            "document_key": document_key,
            "manifest_row": row.manifest_row,
            "source_locator": row.source_locator,
            "target": target,
        }
        if row.anchor_key != anchor_key(preimage):
            raise StructuredAnchorResolutionError(
                "anchor_integrity_error",
                "stored anchor key does not match its full curated preimage",
            )
        if row.anchor_sha256 != canonical_json_sha256(anchor):
            raise StructuredAnchorResolutionError(
                "anchor_integrity_error",
                "stored anchor checksum does not match its typed contract",
            )
        return anchor
    except StructuredAnchorResolutionError:
        raise
    except Exception as exc:
        raise StructuredAnchorResolutionError(
            "anchor_integrity_error",
            "stored anchor cannot be reconstructed as a typed M3 anchor",
        ) from exc


def _stored_target_shape_is_exact(row: DocumentAnchor) -> bool:
    populated = {
        "locus": row.locus_key is not None,
        "assembly": row.assembly_key is not None,
        "lineage": row.lineage_snapshot_key is not None and row.lineage_term_key is not None,
        "method": row.method_definition_key is not None,
        "document": any(
            value is not None for value in (row.target_document_key, row.doi, row.pmid, row.pmcid)
        ),
        "keyword": row.keyword_phrase is not None,
    }
    if (row.lineage_snapshot_key is None) != (row.lineage_term_key is None):
        return False
    return populated.get(row.anchor_type, False) and sum(populated.values()) == 1


def _target_from_anchor(anchor: RetrievalAnchor) -> StructuredAnchorTarget:
    if anchor.anchor_type == "locus":
        return StructuredAnchorTarget(target_type="locus", locus_key=anchor.locus_key)
    if anchor.anchor_type == "assembly":
        return StructuredAnchorTarget(
            target_type="assembly",
            assembly_key=anchor.assembly_key,
        )
    if anchor.anchor_type == "lineage":
        return StructuredAnchorTarget(
            target_type="lineage",
            snapshot_key=anchor.snapshot_key,
            term_key=anchor.term_key,
        )
    if anchor.anchor_type == "method":
        return StructuredAnchorTarget(
            target_type="method",
            method_definition_key=anchor.method_definition_key,
        )
    raise StructuredAnchorResolutionError(
        "anchor_integrity_error",
        "resolved anchor is not an approved structured anchor type",
    )
