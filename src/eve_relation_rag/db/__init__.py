"""Database metadata and Milestone 1 truth-layer models."""

# Importing models registers every table on Base.metadata for Alembic.
from eve_relation_rag.db import models as models
from eve_relation_rag.db.base import Base

__all__ = ["Base", "models"]
