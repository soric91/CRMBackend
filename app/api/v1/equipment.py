"""Equipment endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import (
    EquipmentServiceDep,
    FleetServiceDep,
    PaginationDep,
    ScopeDep,
    VariableServiceDep,
)
from app.schemas.common import Page
from app.schemas.equipment import EquipmentRead, EquipmentUpdate
from app.schemas.variable import VariableCreate, VariableRead

router = APIRouter(prefix="/equipment", tags=["equipment"])


@router.get("", response_model=Page[EquipmentRead])
async def list_equipment(
    scope: ScopeDep,
    service: FleetServiceDep,
    pagination: PaginationDep,
    gateway_id: Annotated[uuid.UUID | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[EquipmentRead]:
    """List Modbus devices across every gateway the caller may see."""
    items, total = await service.list_equipment(
        scope,
        limit=pagination.limit,
        offset=pagination.offset,
        gateway_id=gateway_id,
        search=search,
    )
    return Page[EquipmentRead](
        items=[EquipmentRead.model_validate(item) for item in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/{equipment_id}", response_model=EquipmentRead)
async def get_equipment(
    equipment_id: uuid.UUID, scope: ScopeDep, service: EquipmentServiceDep
) -> EquipmentRead:
    return EquipmentRead.model_validate(await service.get(scope, equipment_id))


@router.patch("/{equipment_id}", response_model=EquipmentRead)
async def update_equipment(
    equipment_id: uuid.UUID,
    payload: EquipmentUpdate,
    scope: ScopeDep,
    service: EquipmentServiceDep,
) -> EquipmentRead:
    equipment = await service.update(
        scope, equipment_id, payload.model_dump(exclude_unset=True)
    )
    return EquipmentRead.model_validate(equipment)


@router.delete("/{equipment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_equipment(
    equipment_id: uuid.UUID, scope: ScopeDep, service: EquipmentServiceDep
) -> None:
    await service.delete(scope, equipment_id)


@router.get("/{equipment_id}/variables", response_model=Page[VariableRead])
async def list_equipment_variables(
    equipment_id: uuid.UUID,
    scope: ScopeDep,
    service: VariableServiceDep,
    pagination: PaginationDep,
) -> Page[VariableRead]:
    items, total = await service.list_for_equipment(
        scope, equipment_id, limit=pagination.limit, offset=pagination.offset
    )
    return Page[VariableRead](
        items=[VariableRead.model_validate(item) for item in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.post(
    "/{equipment_id}/variables",
    response_model=VariableRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_equipment_variable(
    equipment_id: uuid.UUID,
    payload: VariableCreate,
    scope: ScopeDep,
    service: VariableServiceDep,
) -> VariableRead:
    variable = await service.create(scope, equipment_id, payload.model_dump())
    return VariableRead.model_validate(variable)
