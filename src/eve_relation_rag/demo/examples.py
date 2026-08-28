"""Checksum-stable, strictly validated example requests for the V0 demo."""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eve_relation_rag.hybrid.contracts import RagQueryRequest


class DemoExample(BaseModel):
    """One immutable real-state example; it never injects a test capability."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    example_key: str = Field(pattern=r"^[a-z][a-z0-9-]{2,39}$")
    title: str = Field(min_length=1, max_length=80)
    family: Literal["structured", "literature", "hybrid", "unsupported"]
    purpose: str = Field(min_length=1, max_length=300)
    current_outcome: str = Field(min_length=1, max_length=400)
    activation_blocker: str = Field(min_length=1, max_length=500)
    request: RagQueryRequest


class _DemoExampleCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    example_schema_version: Literal["demo-examples-v1"]
    examples: tuple[DemoExample, ...] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def validate_catalog(self) -> Self:
        keys = tuple(example.example_key for example in self.examples)
        if len(keys) != len(set(keys)):
            raise ValueError("demo example keys must be unique")
        families = tuple(example.family for example in self.examples)
        required = {"structured", "literature", "hybrid", "unsupported"}
        if set(families) != required:
            raise ValueError("demo examples must cover each V0 route family exactly once")
        return self


@lru_cache
def load_demo_examples() -> tuple[DemoExample, ...]:
    """Load the package-owned example catalog through the strict request contract."""

    path = files("eve_relation_rag.demo").joinpath("examples.json")
    catalog = _DemoExampleCatalog.model_validate_json(path.read_text(encoding="utf-8"))
    return catalog.examples


__all__ = ["DemoExample", "load_demo_examples"]
