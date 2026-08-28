from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel

from eve_relation_rag.application import StructuredQueryApplication
from eve_relation_rag.application.rag import RagQueryApplication
from eve_relation_rag.bootstrap import (
    get_rag_query_application,
    get_structured_query_application,
)
from eve_relation_rag.config import get_settings
from eve_relation_rag.hybrid.contracts import (
    RagErrorResponse,
    RagQueryRequest,
    RagResponse,
)
from eve_relation_rag.hybrid.rendering import serialize_rag_response
from eve_relation_rag.hybrid.transport import (
    rag_http_status_for,
    rag_internal_error_response,
    rag_request_validation_response,
)
from eve_relation_rag.planning.parser import StructuredQueryRequest
from eve_relation_rag.retrieval.structured.rendering import serialize_structured_response
from eve_relation_rag.retrieval.structured.results import (
    ErrorResponse,
    PlanSuccess,
    QuerySuccess,
)
from eve_relation_rag.transport import (
    http_status_for,
    internal_error_response,
    request_validation_response,
)


class HealthResponse(BaseModel):
    """Stable response returned by the liveness endpoint."""

    status: Literal["ok"] = "ok"
    service: str
    version: str


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Milestone 2 deterministic structured-query surface over immutable published releases. "
        "The current pilot remains candidate-only and is rejected by the publication gate."
    ),
)


def _canonical_response(
    response: PlanSuccess | QuerySuccess | ErrorResponse,
    *,
    status_code: int = 200,
) -> Response:
    return Response(
        content=serialize_structured_response(response),
        status_code=status_code,
        media_type="application/json",
    )


def _canonical_rag_response(
    response: RagResponse,
    *,
    status_code: int = 200,
) -> Response:
    return Response(
        content=serialize_rag_response(response),
        status_code=status_code,
        media_type="application/json",
    )


@app.exception_handler(RequestValidationError)
async def structured_validation_error(
    request: Request,
    exc: RequestValidationError,
) -> Response:
    """Replace FastAPI validation payloads with the route-specific project envelope."""

    if request.url.path == "/v0/query":
        rag_response = rag_request_validation_response(exc.errors(), body=exc.body)
        return _canonical_rag_response(
            rag_response,
            status_code=rag_http_status_for(rag_response),
        )

    response = request_validation_response(exc.errors())
    return _canonical_response(response, status_code=http_status_for(response))


@app.exception_handler(Exception)
async def structured_internal_error(request: Request, _exc: Exception) -> Response:
    """Keep configuration, database, and programming details out of public responses."""

    if request.url.path == "/v0/query":
        rag_response = rag_internal_error_response()
        return _canonical_rag_response(
            rag_response,
            status_code=rag_http_status_for(rag_response),
        )

    response = internal_error_response()
    return _canonical_response(response, status_code=http_status_for(response))


@app.get("/health", response_model=HealthResponse, tags=["operations"])
def health() -> HealthResponse:
    """Report process liveness without querying scientific data."""
    return HealthResponse(
        service=settings.app_name,
        version=settings.app_version,
    )


@app.post(
    "/v0/structured/plan",
    response_model=PlanSuccess | ErrorResponse,
    tags=["structured-query"],
)
def structured_plan(
    payload: StructuredQueryRequest,
    application: Annotated[
        StructuredQueryApplication,
        Depends(get_structured_query_application),
    ],
) -> Response:
    """Interpret a controlled-English question without retrieving public facts."""

    try:
        response = application.plan(payload)
    except Exception:  # pragma: no cover - last-resort transport safety boundary.
        response = internal_error_response()
    status_code = http_status_for(response) if isinstance(response, ErrorResponse) else 200
    return _canonical_response(response, status_code=status_code)


@app.post(
    "/v0/structured/query",
    response_model=QuerySuccess | ErrorResponse,
    tags=["structured-query"],
)
def structured_query(
    payload: StructuredQueryRequest,
    application: Annotated[
        StructuredQueryApplication,
        Depends(get_structured_query_application),
    ],
) -> Response:
    """Interpret and execute one gate-authorized structured fact query."""

    try:
        response = application.query(payload)
    except Exception:  # pragma: no cover - last-resort transport safety boundary.
        response = internal_error_response()
    status_code = http_status_for(response) if isinstance(response, ErrorResponse) else 200
    return _canonical_response(response, status_code=status_code)


@app.post(
    "/v0/query",
    response_model=RagResponse,
    tags=["routed-rag"],
)
def rag_query(
    payload: RagQueryRequest,
    application: Annotated[
        RagQueryApplication,
        Depends(get_rag_query_application),
    ],
) -> Response:
    """Route one strict request through the approved M4 application service."""

    try:
        response = application.query(payload)
        status_code = (
            rag_http_status_for(response) if isinstance(response, RagErrorResponse) else 200
        )
        return _canonical_rag_response(response, status_code=status_code)
    except Exception:  # pragma: no cover - last-resort transport safety boundary.
        response = rag_internal_error_response()
        return _canonical_rag_response(
            response,
            status_code=rag_http_status_for(response),
        )
