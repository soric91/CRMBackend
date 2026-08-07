"""Health and readiness endpoints against the running ASGI app."""

from collections.abc import AsyncGenerator

from fastapi import FastAPI, status
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import get_db_session
from app.main import create_app


class TestHealth:
    async def test_liveness_returns_ok(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/health")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "ok"}

    async def test_liveness_does_not_require_the_database(
        self, settings: Settings
    ) -> None:
        """No database dependency override: the probe must still answer."""
        transport = ASGITransport(app=create_app(settings))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/health")

        assert response.status_code == status.HTTP_200_OK


class TestReadiness:
    async def test_ready_when_the_database_answers(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/ready")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "ready", "database": "up"}

    async def test_degraded_when_the_database_fails(self, app: FastAPI) -> None:
        class _BrokenSession:
            async def execute(self, *_: object, **__: object) -> None:
                raise ConnectionError("connection refused")

            async def rollback(self) -> None:
                return None

        async def _override() -> AsyncGenerator[AsyncSession]:
            yield _BrokenSession()  # pyright: ignore[reportReturnType]

        app.dependency_overrides[get_db_session] = _override
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/ready")

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json() == {"status": "degraded", "database": "down"}

    async def test_failure_details_are_not_leaked(self, app: FastAPI) -> None:
        class _BrokenSession:
            async def execute(self, *_: object, **__: object) -> None:
                raise ConnectionError("password authentication failed for user")

            async def rollback(self) -> None:
                return None

        async def _override() -> AsyncGenerator[AsyncSession]:
            yield _BrokenSession()  # pyright: ignore[reportReturnType]

        app.dependency_overrides[get_db_session] = _override
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/ready")

        assert "password" not in response.text


class TestRouting:
    async def test_unknown_route_returns_404(self, client: AsyncClient) -> None:
        assert (await client.get("/api/v1/nope")).status_code == (
            status.HTTP_404_NOT_FOUND
        )

    async def test_endpoints_live_under_the_versioned_prefix(
        self, client: AsyncClient
    ) -> None:
        assert (await client.get("/health")).status_code == status.HTTP_404_NOT_FOUND
