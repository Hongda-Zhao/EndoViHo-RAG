from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from eve_relation_rag.config import get_settings


class HealthResponse(BaseModel):
    """Stable response returned by the liveness endpoint."""

    status: Literal["ok"] = "ok"
    service: str
    version: str


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Milestone 0 service scaffold. No scientific query routes are available.",
)


@app.get("/health", response_model=HealthResponse, tags=["operations"])
def health() -> HealthResponse:
    """Report process liveness without querying scientific data."""
    return HealthResponse(
        service=settings.app_name,
        version=settings.app_version,
    )
