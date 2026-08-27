"""Stable API/CLI status mappings for structured query responses."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from eve_relation_rag.retrieval.structured.results import (
    ErrorCode,
    ErrorResponse,
    FieldError,
    StructuredError,
)

_CURSOR_CODES: frozenset[ErrorCode] = frozenset({"cursor_invalid", "cursor_plan_mismatch"})
_NOT_FOUND_CODES: frozenset[ErrorCode] = frozenset(
    {"release_not_found", "entity_unresolved", "entity_not_in_release"}
)
_CONFLICT_CODES: frozenset[ErrorCode] = frozenset(
    {
        "entity_ambiguous",
        "release_not_published",
        "release_dependencies_incomplete",
        "release_manifest_invalid",
    }
)
_SERVER_CODES: frozenset[ErrorCode] = frozenset(
    {
        "query_plan_version_unsupported",
        "compiler_constraint_unmapped",
        "result_integrity_error",
        "structured_query_failed",
    }
)


def http_status_for(response: ErrorResponse) -> int:
    """Map one stable error code to the Draft B HTTP status."""

    code = response.error.code
    if code in _CURSOR_CODES:
        return 400
    if code in _NOT_FOUND_CODES:
        return 404
    if code in _CONFLICT_CODES:
        return 409
    if code in _SERVER_CODES:
        return 500
    return 422


def cli_exit_code_for(response: ErrorResponse) -> int:
    """Map one stable error response to the Draft B CLI exit status."""

    code = response.error.code
    if code in {"entity_unresolved", "entity_ambiguous", "entity_not_in_release"}:
        return 3
    if code in {
        "release_not_found",
        "release_not_published",
        "release_dependencies_incomplete",
        "release_manifest_invalid",
    }:
        return 4
    if code in _SERVER_CODES:
        return 5
    return 2


def request_validation_response(
    errors: Sequence[Mapping[str, Any]],
) -> ErrorResponse:
    """Convert framework/Pydantic validation details to the project envelope."""

    findings: list[FieldError] = []
    classified: list[ErrorCode] = []
    for error in errors:
        raw_location = error.get("loc", ())
        location = tuple(str(part) for part in raw_location if part not in {"body"})
        field = ".".join(location) or "request"
        validation_type = str(error.get("type", "invalid"))
        message = str(error.get("msg", "Invalid request value."))
        findings.append(
            FieldError(
                field=field,
                code=validation_type.replace(".", "_"),
                message=message,
            )
        )
        if field == "release_key":
            classified.append(
                "release_required" if validation_type == "missing" else "release_key_invalid"
            )
        elif field == "page.cursor":
            classified.append("cursor_invalid")
        elif field == "page.limit":
            classified.append("limit_invalid")
        else:
            classified.append("request_schema_invalid")

    unique_codes = set(classified)
    code: ErrorCode = (
        next(iter(unique_codes)) if len(unique_codes) == 1 else "request_schema_invalid"
    )
    return ErrorResponse(
        error=StructuredError(
            code=code,
            message="The structured query request is invalid.",
            field_errors=tuple(
                sorted(findings, key=lambda item: (item.field, item.code, item.message))
            ),
        )
    )


def internal_error_response() -> ErrorResponse:
    """Return a safe failure without leaking exception or database details."""

    return ErrorResponse(
        error=StructuredError(
            code="structured_query_failed",
            message="The structured query could not be completed.",
        )
    )
