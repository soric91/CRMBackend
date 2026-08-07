"""Site endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import (
    FleetServiceDep,
    GatewayServiceDep,
    PaginationDep,
    ScopeDep,
    SiteServiceDep,
)
from app.schemas.common import Page
from app.schemas.gateway import GatewayCreate, GatewayRead
from app.schemas.site import SiteRead, SiteUpdate

router = APIRouter(prefix="/sites", tags=["sites"])


@router.get("", response_model=Page[SiteRead])
async def list_sites(
    scope: ScopeDep,
    service: FleetServiceDep,
    pagination: PaginationDep,
    client_id: Annotated[uuid.UUID | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[SiteRead]:
    """List sites across every client the caller may see."""
    items, total = await service.list_sites(
        scope,
        limit=pagination.limit,
        offset=pagination.offset,
        client_id=client_id,
        search=search,
    )
    return Page[SiteRead](
        items=[SiteRead.model_validate(item) for item in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/{site_id}", response_model=SiteRead)
async def get_site(
    site_id: uuid.UUID, scope: ScopeDep, service: SiteServiceDep
) -> SiteRead:
    return SiteRead.model_validate(await service.get(scope, site_id))


@router.patch("/{site_id}", response_model=SiteRead)
async def update_site(
    site_id: uuid.UUID,
    payload: SiteUpdate,
    scope: ScopeDep,
    service: SiteServiceDep,
) -> SiteRead:
    site = await service.update(scope, site_id, payload.model_dump(exclude_unset=True))
    return SiteRead.model_validate(site)


@router.delete("/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_site(
    site_id: uuid.UUID, scope: ScopeDep, service: SiteServiceDep
) -> None:
    """Delete a site and, by cascade, its gateways, equipment and variables."""
    await service.delete(scope, site_id)


@router.get("/{site_id}/gateways", response_model=Page[GatewayRead])
async def list_site_gateways(
    site_id: uuid.UUID,
    scope: ScopeDep,
    service: GatewayServiceDep,
    pagination: PaginationDep,
) -> Page[GatewayRead]:
    items, total = await service.list_for_site(
        scope, site_id, limit=pagination.limit, offset=pagination.offset
    )
    return Page[GatewayRead](
        items=[GatewayRead.model_validate(item) for item in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.post(
    "/{site_id}/gateways",
    response_model=GatewayRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_site_gateway(
    site_id: uuid.UUID,
    payload: GatewayCreate,
    scope: ScopeDep,
    service: GatewayServiceDep,
) -> GatewayRead:
    """Register a gateway under a site."""
    gateway = await service.create(scope, site_id, payload.model_dump())
    return GatewayRead.model_validate(gateway)
