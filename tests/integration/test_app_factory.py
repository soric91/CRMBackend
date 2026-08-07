"""Application factory wiring."""

from fastapi import status
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app


class TestDocumentation:
    def test_docs_are_exposed_outside_production(self, settings: Settings) -> None:
        app = create_app(settings)
        assert app.docs_url == "/docs"
        assert app.openapi_url == "/openapi.json"

    def test_docs_are_disabled_in_production(self, settings: Settings) -> None:
        app = create_app(settings.model_copy(update={"environment": "production"}))
        assert app.docs_url is None
        assert app.redoc_url is None
        assert app.openapi_url is None


class TestCors:
    async def test_no_cors_middleware_when_no_origins_configured(
        self, settings: Settings
    ) -> None:
        app = create_app(settings)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/health", headers={"Origin": "https://evil.example.com"}
            )

        assert "access-control-allow-origin" not in response.headers

    async def test_configured_origin_is_allowed(self, settings: Settings) -> None:
        app = create_app(
            settings.model_copy(update={"cors_origins": ["https://panel.example.com"]})
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/health", headers={"Origin": "https://panel.example.com"}
            )

        assert response.status_code == status.HTTP_200_OK
        assert (
            response.headers["access-control-allow-origin"]
            == "https://panel.example.com"
        )

    async def test_unlisted_origin_is_not_allowed(self, settings: Settings) -> None:
        app = create_app(
            settings.model_copy(update={"cors_origins": ["https://panel.example.com"]})
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/health", headers={"Origin": "https://evil.example.com"}
            )

        assert "access-control-allow-origin" not in response.headers


class TestState:
    def test_settings_are_attached_to_app_state(self, settings: Settings) -> None:
        assert create_app(settings).state.settings is settings

    def test_prefix_comes_from_settings(self, settings: Settings) -> None:
        app = create_app(settings.model_copy(update={"api_v1_prefix": "/api/v2"}))
        assert "/api/v2/health" in app.openapi()["paths"]
