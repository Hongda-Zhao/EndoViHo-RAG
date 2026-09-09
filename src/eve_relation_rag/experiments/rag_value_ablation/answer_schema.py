"""JSON Schema conditions shared with the model; never evidence or answer content."""

from __future__ import annotations

from typing import Any


def _present(name: str) -> dict[str, Any]:
    return {"required": [name], "properties": {name: {"not": {"type": "null"}}}}


def _paired(first: str, second: str) -> dict[str, Any]:
    return {
        "if": _present(first),
        "then": _present(second),
        "else": {"properties": {second: {"type": "null"}}},
    }


def claim_schema(schema: dict[str, Any]) -> None:
    schema["allOf"] = [{
        "if": {"properties": {"claim_type": {"const": "literature_fact"}}},
        "then": {
            "required": ["citation_ids"],
            "properties": {"citation_ids": {"minItems": 1}},
        },
    }]


def structured_facts_schema(schema: dict[str, Any]) -> None:
    schema["allOf"] = [
        _paired("exact_count", "metric_key"),
        _paired("release_key", "release_manifest_sha256"),
    ]
    schema["anyOf"] = [
        _present(name)
        for name in schema["properties"]
        if name not in {"metric_key", "release_manifest_sha256", "limitation_codes"}
    ] + [{
        "required": ["limitation_codes"],
        "properties": {"limitation_codes": {"minItems": 1}},
    }]
    for field in schema["properties"].values():
        for variant in field.get("anyOf", [field]):
            if variant.get("type") == "array":
                variant["uniqueItems"] = True


def answer_schema(schema: dict[str, Any]) -> None:
    schema["allOf"] = [
        {
            "if": {"properties": {"abstained": {"const": True}}},
            "then": {"properties": {
                "claims": {"maxItems": 0},
                "structured_facts": {"type": "null"},
                "cited_chunk_ids": {"maxItems": 0},
            }},
            "else": {
                "required": ["claims"],
                "properties": {"claims": {"minItems": 1}},
            },
        },
        {
            "if": {
                "required": ["claims"],
                "properties": {"claims": {"contains": {
                    "required": ["claim_type"],
                    "properties": {"claim_type": {"const": "structured_fact"}},
                }}},
            },
            "then": _present("structured_facts"),
            "else": {"properties": {"structured_facts": {"type": "null"}}},
        },
    ]
