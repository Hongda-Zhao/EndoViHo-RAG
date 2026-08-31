"""Internal release capability used by structured retrieval.

The capability is deliberately not a request schema.  Production issuance is
owned by :mod:`eve_relation_rag.retrieval.structured.gate`; repository tests use
structural protocol doubles declared under ``tests/``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Literal, Protocol

type LineageRole = Literal[
    "assembly_source_taxonomy",
    "formal_viral_taxonomy",
    "study_viral_lineage",
]


@dataclass(frozen=True, slots=True)
class SourceDependencyBinding:
    """One exact source snapshot bound to a release role."""

    role: str
    source_snapshot_id: int
    snapshot_key: str
    verified_manifest_sha256: str


@dataclass(frozen=True, slots=True)
class LineageDependencyBinding:
    """One exact, role-qualified lineage snapshot dependency."""

    role: LineageRole
    snapshot_id: int
    snapshot_key: str
    domain: Literal["host", "viral"]
    scheme_kind: Literal["formal_taxonomy", "study_defined"]
    authority_namespace: str
    version: str
    snapshot_sha256: str


class ReleaseCapability(Protocol):
    """Structural boundary accepted by the compiler and repository.

    This protocol has no constructor and is not exported from the package root.
    It exists so repository tests can supply a tests-only capability without
    weakening the production release gate.
    """

    release_id: int
    dataset_key: Literal["dataset:endoviho-rag"]
    release_key: str
    status: Literal["published", "validation_candidate"]
    schema_version: str
    published_at: datetime
    manifest_sha256: str
    validation_receipt_key: str
    validation_receipt_sha256: str
    candidate_validation_input_sha256: str | None
    candidate_capability_sha256: str | None
    source_dependencies: Mapping[str, SourceDependencyBinding]
    lineage_dependencies: Mapping[LineageRole, LineageDependencyBinding]
    complete_lineage_closure_roles: frozenset[LineageRole]


_GATE_ISSUER = object()


@dataclass(frozen=True, slots=True)
class _QueryableRelease:
    """Gate-issued production implementation of :class:`ReleaseCapability`."""

    release_id: int
    dataset_key: Literal["dataset:endoviho-rag"]
    release_key: str
    status: Literal["published", "validation_candidate"]
    schema_version: str
    published_at: datetime
    manifest_sha256: str
    validation_receipt_key: str
    validation_receipt_sha256: str
    candidate_validation_input_sha256: str | None
    candidate_capability_sha256: str | None
    source_dependencies: Mapping[str, SourceDependencyBinding]
    lineage_dependencies: Mapping[LineageRole, LineageDependencyBinding]
    complete_lineage_closure_roles: frozenset[LineageRole]
    _issuer: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _GATE_ISSUER:
            raise TypeError("QueryableRelease may only be issued by PublishedReleaseGate")
        if self.status == "validation_candidate":
            if (
                self.candidate_validation_input_sha256 is None
                or self.candidate_capability_sha256 is None
                or self.validation_receipt_key != "validation-candidate:no-receipt"
                or self.validation_receipt_sha256 != "0" * 64
            ):
                raise TypeError("validation candidate capability identity is incomplete")
        elif (
            self.candidate_validation_input_sha256 is not None
            or self.candidate_capability_sha256 is not None
        ):
            raise TypeError("published capability must not claim candidate-only identity")
        object.__setattr__(
            self,
            "source_dependencies",
            MappingProxyType(dict(self.source_dependencies)),
        )
        object.__setattr__(
            self,
            "lineage_dependencies",
            MappingProxyType(dict(self.lineage_dependencies)),
        )


def _issue_queryable_release(
    *,
    release_id: int,
    dataset_key: Literal["dataset:endoviho-rag"],
    release_key: str,
    status: Literal["published", "validation_candidate"],
    schema_version: str,
    published_at: datetime,
    manifest_sha256: str,
    validation_receipt_key: str,
    validation_receipt_sha256: str,
    candidate_validation_input_sha256: str | None = None,
    candidate_capability_sha256: str | None = None,
    source_dependencies: Mapping[str, SourceDependencyBinding],
    lineage_dependencies: Mapping[LineageRole, LineageDependencyBinding],
    complete_lineage_closure_roles: frozenset[LineageRole],
) -> _QueryableRelease:
    """Issue a production capability after gate verification.

    The leading underscore and unexported concrete type are intentional.  No
    request model, API, or CLI code may call this factory.
    """

    return _QueryableRelease(
        release_id=release_id,
        dataset_key=dataset_key,
        release_key=release_key,
        status=status,
        schema_version=schema_version,
        published_at=published_at,
        manifest_sha256=manifest_sha256,
        validation_receipt_key=validation_receipt_key,
        validation_receipt_sha256=validation_receipt_sha256,
        candidate_validation_input_sha256=candidate_validation_input_sha256,
        candidate_capability_sha256=candidate_capability_sha256,
        source_dependencies=source_dependencies,
        lineage_dependencies=lineage_dependencies,
        complete_lineage_closure_roles=complete_lineage_closure_roles,
        _issuer=_GATE_ISSUER,
    )
