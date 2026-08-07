"""Aggregate router for API v1."""

from fastapi import APIRouter

from app.api.v1 import (
    auth,
    auth_monitor,
    clients,
    equipment,
    fleet,
    gateway_config,
    gateways,
    health,
    service_accounts,
    sites,
    tariffs,
    users,
    variables,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(auth_monitor.router)
api_router.include_router(clients.router)
api_router.include_router(sites.router)
api_router.include_router(gateways.router)
api_router.include_router(gateway_config.router)
api_router.include_router(equipment.router)
api_router.include_router(variables.router)
api_router.include_router(users.router)
api_router.include_router(tariffs.router)
api_router.include_router(fleet.router)
api_router.include_router(service_accounts.router)
api_router.include_router(service_accounts.token_router)
