"""Application factory."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import well_known
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.database import dispose_engine
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.mqtt import MqttBridge, set_bridge
from app.services.mqtt_events import record_gateway_presence

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings: Settings = app.state.settings
    logger.info(
        "application starting",
        extra={"environment": settings.environment, "app": settings.app_name},
    )

    # The bridge listens for gateway presence for as long as the app runs. It
    # connects in the background, so an unreachable broker delays nothing.
    bridge = MqttBridge(settings)
    bridge.on_status(record_gateway_presence)
    set_bridge(bridge)
    await bridge.start()

    yield

    await bridge.stop()
    set_bridge(None)
    await dispose_engine()
    logger.info("application stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.is_production)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    # No version prefix: `/.well-known/` is a fixed location by convention, and
    # a consumer that cached it must not have to move when `v2` appears.
    app.include_router(well_known.router)
    return app
