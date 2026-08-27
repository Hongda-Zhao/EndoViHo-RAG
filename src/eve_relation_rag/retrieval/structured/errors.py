"""Stable internal refusals at the structured retrieval boundary."""

from __future__ import annotations

from dataclasses import dataclass

from eve_relation_rag.retrieval.structured.results import ErrorCode


@dataclass(frozen=True, slots=True)
class RetrievalRefusal(Exception):
    """Expected fail-closed refusal that can be rendered as StructuredError."""

    code: ErrorCode
    message: str
    fact_retrieval_executed: bool = False

    def __str__(self) -> str:
        return self.message
