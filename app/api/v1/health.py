"""Health and readiness endpoints."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    status: Literal["ready", "degraded"]
    database: Literal["up", "down"]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe: the process is up. Touches no external service."""
    return HealthResponse(status="ok")


@router.get("/ready", response_model=ReadinessResponse)
async def ready(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ReadinessResponse:
    """Readiness probe: verifies the database answers a trivial query."""
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("readiness check failed", extra={"error": str(exc)})
        await session.rollback()
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status="degraded", database="down")
    return ReadinessResponse(status="ready", database="up")
