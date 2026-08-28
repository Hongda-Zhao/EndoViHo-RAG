"""Stable fail-closed refusals at the literature retrieval boundary."""

from __future__ import annotations

from dataclasses import dataclass

from eve_relation_rag.literature.contracts import LiteratureErrorCode


@dataclass(slots=True)
class LiteratureRetrievalRefusal(Exception):
    """Expected refusal that can be rendered as a literature error envelope."""

    code: LiteratureErrorCode
    message: str
    retrieval_executed: bool = False

    def __str__(self) -> str:
        return self.message
