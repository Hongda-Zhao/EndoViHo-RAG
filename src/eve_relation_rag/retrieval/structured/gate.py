"""Fail-closed production authorization for immutable published releases."""

from __future__ import annotations

from typing import Final

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from eve_relation_rag.db.models import Dataset, DatasetRelease
from eve_relation_rag.domain.keys import is_release_key
from eve_relation_rag.retrieval.structured.capability import ReleaseCapability
from eve_relation_rag.retrieval.structured.errors import RetrievalRefusal

_DATASET_KEY: Final = "dataset:endoviho-rag"
_RELEASE_PREFIX: Final = "release:endoviho-rag:v0:"


class PublishedReleaseGate:
    """Authorize only exact published releases with a trusted receipt.

    Migration ``0005`` intentionally provides no validation-receipt store and
    prevents publication.  Consequently the current production gate has no
    success path: a candidate is ``release_not_published`` and any legacy row
    marked published is ``release_dependencies_incomplete``.  This is a
    scientific safety boundary, not an unfinished permissive fallback.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def authorize(self, release_key: str) -> ReleaseCapability:
        """Verify one exact stable release key before any public fact lookup."""

        if not release_key.startswith(_RELEASE_PREFIX) or not is_release_key(release_key):
            raise RetrievalRefusal(
                "release_key_invalid",
                "release_key must be an exact EndoViHo-RAG release key",
            )

        try:
            with self._engine.connect().execution_options(postgresql_readonly=True) as connection:
                with Session(bind=connection) as session, session.begin():
                    row = session.execute(
                        select(
                            Dataset.dataset_key,
                            DatasetRelease.id,
                            DatasetRelease.status,
                            DatasetRelease.schema_version,
                            DatasetRelease.manifest_sha256,
                            DatasetRelease.published_at,
                        )
                        .select_from(DatasetRelease)
                        .join(Dataset, Dataset.id == DatasetRelease.dataset_id)
                        .where(DatasetRelease.release_key == release_key)
                    ).one_or_none()
        except Exception as exc:
            raise RetrievalRefusal(
                "structured_query_failed",
                "release authorization failed",
                fact_retrieval_executed=False,
            ) from exc

        if row is None or row.dataset_key != _DATASET_KEY:
            raise RetrievalRefusal("release_not_found", "release was not found")
        if row.status != "published":
            raise RetrievalRefusal(
                "release_not_published",
                "release is not published and cannot be queried",
            )
        if row.manifest_sha256 is None or len(row.manifest_sha256) != 64:
            raise RetrievalRefusal(
                "release_manifest_invalid",
                "published release manifest is missing or invalid",
            )
        if row.published_at is None:
            raise RetrievalRefusal(
                "release_manifest_invalid",
                "published release timestamp is missing",
            )

        # There is deliberately no call to _issue_queryable_release here.  The
        # current schema has no immutable, release-bound receipt to verify.
        raise RetrievalRefusal(
            "release_dependencies_incomplete",
            "trusted validation receipt and dependency attestation are unavailable",
        )
