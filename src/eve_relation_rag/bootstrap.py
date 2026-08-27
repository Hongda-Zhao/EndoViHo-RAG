"""Production dependency composition shared by HTTP and CLI adapters."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine

from eve_relation_rag.application.structured import StructuredQueryApplication
from eve_relation_rag.config import get_settings
from eve_relation_rag.planning.sqlalchemy_resolver import SqlAlchemyReleaseResolverFactory
from eve_relation_rag.retrieval.structured.gate import PublishedReleaseGate
from eve_relation_rag.retrieval.structured.repository import StructuredRepository
from eve_relation_rag.retrieval.structured.service import StructuredRetrievalService


@lru_cache
def get_engine() -> Engine:
    """Return the process-local SQLAlchemy engine without opening a connection."""

    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_structured_query_application() -> StructuredQueryApplication:
    """Compose the one production question-first structured query application."""

    settings = get_settings()
    engine = get_engine()
    gate = PublishedReleaseGate(engine)
    secret_setting = settings.cursor_hmac_secret
    retrieval: StructuredRetrievalService | None = None
    if secret_setting is not None:
        secret = secret_setting.get_secret_value().encode("utf-8")
        if len(secret) >= 32:
            retrieval = StructuredRetrievalService(
                gate=gate,
                repository=StructuredRepository(engine),
                cursor_secret=secret,
            )
    return StructuredQueryApplication(
        gate=gate,
        resolver_factory=SqlAlchemyReleaseResolverFactory(engine),
        retrieval=retrieval,
    )
