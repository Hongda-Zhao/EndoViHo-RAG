"""Bounded, single-request HTTP client used by the Streamlit demo."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import TypeAdapter, ValidationError

from eve_relation_rag.hybrid.contracts import RagErrorResponse, RagQueryRequest, RagResponse
from eve_relation_rag.hybrid.rendering import revalidate_rag_response
from eve_relation_rag.hybrid.transport import rag_http_status_for
from eve_relation_rag.planning.router import DeterministicRouter

_RAG_RESPONSE_ADAPTER: TypeAdapter[RagResponse] = TypeAdapter(RagResponse)
_DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
_API_ENVIRONMENT_KEY = "EVE_RAG_DEMO_API_BASE_URL"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_TIMEOUT_SECONDS = 20.0


class DemoClientError(RuntimeError):
    """A deliberately sanitized error safe to display in the demo."""


@dataclass(frozen=True, slots=True)
class DemoClientConfig:
    """Server-owned demo transport configuration; none is browser-authored."""

    api_base_url: str
    timeout_seconds: float = _TIMEOUT_SECONDS
    max_response_bytes: int = _MAX_RESPONSE_BYTES

    @classmethod
    def from_environment(cls) -> DemoClientConfig:
        """Load and validate the fixed API origin used by the server process."""

        return cls(api_base_url=_normalize_api_base_url(os.getenv(_API_ENVIRONMENT_KEY)))

    def __post_init__(self) -> None:
        normalized = _normalize_api_base_url(self.api_base_url)
        object.__setattr__(self, "api_base_url", normalized)
        if not 0 < self.timeout_seconds <= 60:
            raise ValueError("demo timeout must be between 0 and 60 seconds")
        if not 1024 <= self.max_response_bytes <= 2 * 1024 * 1024:
            raise ValueError("demo response limit must be between 1 KiB and 2 MiB")


@dataclass(frozen=True, slots=True)
class DemoApiResult:
    """One validated canonical API envelope and its transport status."""

    status_code: int
    response: RagResponse


def submit_query(
    request: RagQueryRequest,
    *,
    config: DemoClientConfig | None = None,
    transport: httpx.BaseTransport | None = None,
) -> DemoApiResult:
    """POST exactly one validated request, without redirects, retries, or fallback routes."""

    resolved = config or DemoClientConfig.from_environment()
    try:
        body = request.model_dump_json(exclude_none=True)
        trusted_request = RagQueryRequest.model_validate_json(body)
        if trusted_request.model_dump_json(exclude_none=True) != body:
            raise ValueError("demo request changed during strict validation")
    except (TypeError, ValidationError, ValueError) as exc:
        raise DemoClientError("The demo request does not match the V0 contract.") from exc
    try:
        with httpx.Client(
            base_url=resolved.api_base_url,
            timeout=httpx.Timeout(resolved.timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            with client.stream(
                "POST",
                "/v0/query",
                content=body,
                headers={
                    "accept": "application/json",
                    "accept-encoding": "identity",
                    "content-type": "application/json",
                },
            ) as http_response:
                raw = _read_bounded(http_response, resolved.max_response_bytes)
                status_code = http_response.status_code
    except httpx.HTTPError as exc:
        raise DemoClientError(
            "The local API could not be reached. Check the API container and try again."
        ) from exc

    try:
        parsed = _RAG_RESPONSE_ADAPTER.validate_json(raw)
        response = revalidate_rag_response(parsed)
    except (TypeError, ValidationError, ValueError) as exc:
        raise DemoClientError(
            "The API returned a response that does not match the V0 contract."
        ) from exc

    expected_status = (
        rag_http_status_for(response) if isinstance(response, RagErrorResponse) else 200
    )
    if status_code != expected_status:
        raise DemoClientError("The API response status does not match the V0 contract.")
    if not _response_belongs_to_request(response, trusted_request):
        raise DemoClientError("The API response does not belong to the submitted request.")

    return DemoApiResult(status_code=status_code, response=response)


def _response_belongs_to_request(response: RagResponse, request: RagQueryRequest) -> bool:
    if not isinstance(response, RagErrorResponse):
        return response.original_request == request
    selectors_match = (
        response.requested_release_key == request.release_key
        and response.requested_corpus_release_key == request.corpus_release_key
    )
    if response.code == "internal_error" and response.route is None:
        return selectors_match or (
            response.requested_release_key is None
            and response.requested_corpus_release_key is None
        )
    if response.code == "request_schema_invalid" or not selectors_match:
        return False
    decision = DeterministicRouter().route(request)
    if response.route != decision.route:
        return False
    return decision.route != "unsupported" or response.code == decision.refusal_code


def _read_bounded(response: httpx.Response, maximum: int) -> bytes:
    content_encoding = response.headers.get("content-encoding")
    if content_encoding is not None and content_encoding.casefold() != "identity":
        raise DemoClientError("The API returned an unsupported response encoding.")
    length_header = response.headers.get("content-length")
    if length_header is not None:
        try:
            declared_length = int(length_header)
        except ValueError as exc:
            raise DemoClientError("The API returned an invalid response length.") from exc
        if declared_length < 0 or declared_length > maximum:
            raise DemoClientError("The API response exceeded the demo safety limit.")

    payload = bytearray()
    for chunk in response.iter_bytes():
        payload.extend(chunk)
        if len(payload) > maximum:
            raise DemoClientError("The API response exceeded the demo safety limit.")
    if not payload:
        raise DemoClientError("The API returned an empty response.")
    return bytes(payload)


def _normalize_api_base_url(value: str | None) -> str:
    raw = (value or _DEFAULT_API_BASE_URL).strip()
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise ValueError(
            "demo API base URL must be one credential-free HTTP(S) origin"
        ) from exc
    if (
        parts.scheme not in {"http", "https"}
        or parts.hostname is None
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or parts.path not in {"", "/"}
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("demo API base URL must be one credential-free HTTP(S) origin")
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


__all__ = ["DemoApiResult", "DemoClientConfig", "DemoClientError", "submit_query"]
