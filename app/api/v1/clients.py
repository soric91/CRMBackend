"""Client endpoints.

Routers stay thin on purpose: parse, delegate, serialise. Every authorization
and uniqueness rule lives in the service, so no endpoint can skip one.
"""

import uuid
from typing import Any

from fastapi import APIRouter, status

from app.api.deps import (
    ClientServiceDep,
    MonitorAccessServiceDep,
    PaginationDep,
    ScopeDep,
    SiteServiceDep,
)
from app.models import User
from app.schemas.auth_monitor import MonitorAccessCreated, MonitorAccessRead
from app.schemas.client import ClientCreate, ClientRead, ClientUpdate
from app.schemas.common import Page
from app.schemas.site import SiteCreate, SiteRead

router = APIRouter(prefix="/clients", tags=["clients"])


@router.get("", response_model=Page[ClientRead])
async def list_clients(
    scope: ScopeDep, service: ClientServiceDep, pagination: PaginationDep
) -> Page[ClientRead]:
    """List clients. A `cliente` login only ever sees its own."""
    items, total = await service.list(
        scope, limit=pagination.limit, offset=pagination.offset
    )
    return Page[ClientRead](
        items=[ClientRead.model_validate(item) for item in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.post("", response_model=ClientRead, status_code=status.HTTP_201_CREATED)
async def create_client(
    payload: ClientCreate, scope: ScopeDep, service: ClientServiceDep
) -> ClientRead:
    client = await service.create(scope, payload.model_dump(exclude_none=True))
    return ClientRead.model_validate(client)


@router.get("/{client_id}", response_model=ClientRead)
async def get_client(
    client_id: uuid.UUID, scope: ScopeDep, service: ClientServiceDep
) -> ClientRead:
    return ClientRead.model_validate(await service.get(scope, client_id))


@router.patch("/{client_id}", response_model=ClientRead)
async def update_client(
    client_id: uuid.UUID,
    payload: ClientUpdate,
    scope: ScopeDep,
    service: ClientServiceDep,
) -> ClientRead:
    """Partial update: fields absent from the body keep their stored value."""
    client = await service.update(
        scope, client_id, payload.model_dump(exclude_unset=True)
    )
    return ClientRead.model_validate(client)


@router.get("/{client_id}/sites", response_model=Page[SiteRead])
async def list_client_sites(
    client_id: uuid.UUID,
    scope: ScopeDep,
    service: SiteServiceDep,
    pagination: PaginationDep,
) -> Page[SiteRead]:
    items, total = await service.list_for_client(
        scope, client_id, limit=pagination.limit, offset=pagination.offset
    )
    return Page[SiteRead](
        items=[SiteRead.model_validate(item) for item in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.post(
    "/{client_id}/sites", response_model=SiteRead, status_code=status.HTTP_201_CREATED
)
async def create_client_site(
    client_id: uuid.UUID,
    payload: SiteCreate,
    scope: ScopeDep,
    service: SiteServiceDep,
) -> SiteRead:
    site = await service.create(scope, client_id, payload.model_dump())
    return SiteRead.model_validate(site)


@router.get("/{client_id}/monitor-access", response_model=MonitorAccessRead)
async def get_monitor_access(
    client_id: uuid.UUID, scope: ScopeDep, service: MonitorAccessServiceDep
) -> MonitorAccessRead:
    """State of the client's access to the monitoring web."""
    access = await service.get(scope, client_id)
    return MonitorAccessRead.model_validate(_as_access(access))


@router.post(
    "/{client_id}/monitor-access",
    response_model=MonitorAccessCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_monitor_access(
    client_id: uuid.UUID, scope: ScopeDep, service: MonitorAccessServiceDep
) -> MonitorAccessCreated:
    """Grant access, returning a one-off password shown only here.

    The address is the client's `contacto_email`. A previously revoked access
    is reactivated with a fresh password instead of being duplicated.
    """
    access, password = await service.create(scope, client_id)
    return MonitorAccessCreated(**_as_access(access), temporary_password=password)


@router.post("/{client_id}/monitor-access/reset", response_model=MonitorAccessCreated)
async def reset_monitor_access(
    client_id: uuid.UUID, scope: ScopeDep, service: MonitorAccessServiceDep
) -> MonitorAccessCreated:
    """Issue a new one-off password. Does not change whether the access is on."""
    access, password = await service.reset_password(scope, client_id)
    return MonitorAccessCreated(**_as_access(access), temporary_password=password)


@router.delete("/{client_id}/monitor-access", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_monitor_access(
    client_id: uuid.UUID, scope: ScopeDep, service: MonitorAccessServiceDep
) -> None:
    """Disable the access. The row stays, so the trace of who entered stays."""
    await service.revoke(scope, client_id)


def _as_access(user: User) -> dict[str, Any]:
    """Project a user row onto the monitoring-access view."""
    return {
        "user_id": user.id,
        "email": user.email,
        "is_active": user.is_active,
        "must_change_password": user.must_change_password,
        "created_at": user.created_at,
    }
