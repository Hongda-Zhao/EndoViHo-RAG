"""HTTP-only demo adapter for the V0 routed RAG API."""

from eve_relation_rag.demo.client import (
    DemoApiResult,
    DemoClientConfig,
    DemoClientError,
    submit_query,
)
from eve_relation_rag.demo.examples import DemoExample, load_demo_examples

__all__ = [
    "DemoApiResult",
    "DemoClientConfig",
    "DemoClientError",
    "DemoExample",
    "load_demo_examples",
    "submit_query",
]
