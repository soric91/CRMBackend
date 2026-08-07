"""Monthly tariff endpoints."""

import uuid

from fastapi import APIRouter, status

from app.api.deps import (
    MachineOrUserScopeDep,
    PaginationDep,
    ScopeDep,
    TariffServiceDep,
)
from app.schemas.common import Page
from app.schemas.tariff import TariffCreate, TariffRead, TariffUpdate

router = APIRouter(prefix="/tariffs", tags=["tariffs"])


@router.get("", response_model=Page[TariffRead])
async def list_tariffs(
    scope: MachineOrUserScopeDep,
    service: TariffServiceDep,
    pagination: PaginationDep,
) -> Page[TariffRead]:
    """List the monthly prices, most recent first.

    One of the two routes another system may reach, and then only with the
    `tariffs:read` permission. Writing stays closed to machines entirely.
    """
    items, total = await service.list(
        scope, limit=pagination.limit, offset=pagination.offset
    )
    return Page[TariffRead](
        items=[TariffRead.model_validate(item) for item in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.post("", response_model=TariffRead, status_code=status.HTTP_201_CREATED)
async def create_tariff(
    payload: TariffCreate, scope: ScopeDep, service: TariffServiceDep
) -> TariffRead:
    """Register the prices of a month. Any day given is snapped to the first."""
    tariff = await service.create(scope, payload.model_dump())
    return TariffRead.model_validate(tariff)


@router.get("/{tariff_id}", response_model=TariffRead)
async def get_tariff(
    tariff_id: uuid.UUID, scope: ScopeDep, service: TariffServiceDep
) -> TariffRead:
    return TariffRead.model_validate(await service.get(scope, tariff_id))


@router.patch("/{tariff_id}", response_model=TariffRead)
async def update_tariff(
    tariff_id: uuid.UUID,
    payload: TariffUpdate,
    scope: ScopeDep,
    service: TariffServiceDep,
) -> TariffRead:
    """Change the prices of a period. The period itself cannot be moved."""
    tariff = await service.update(
        scope, tariff_id, payload.model_dump(exclude_unset=True)
    )
    return TariffRead.model_validate(tariff)


@router.delete("/{tariff_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tariff(
    tariff_id: uuid.UUID, scope: ScopeDep, service: TariffServiceDep
) -> None:
    await service.delete(scope, tariff_id)
