"""Trusted composition primitives for Milestone 4 hybrid retrieval."""

from eve_relation_rag.retrieval.hybrid.anchors import (
    StructuredAnchorResolution,
    StructuredAnchorResolutionError,
    StructuredAnchorResolver,
    StructuredAnchorTarget,
    extract_structured_anchor_targets,
)

__all__ = [
    "StructuredAnchorResolution",
    "StructuredAnchorResolutionError",
    "StructuredAnchorResolver",
    "StructuredAnchorTarget",
    "extract_structured_anchor_targets",
]
