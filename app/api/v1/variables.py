"""Variable endpoints."""

import uuid

from fastapi import APIRouter, status

from app.api.deps import ScopeDep, VariableServiceDep
from app.schemas.variable import VariableRead, VariableUpdate

router = APIRouter(prefix="/variables", tags=["variables"])


@router.get("/{variable_id}", response_model=VariableRead)
async def get_variable(
    variable_id: uuid.UUID, scope: ScopeDep, service: VariableServiceDep
) -> VariableRead:
    return VariableRead.model_validate(await service.get(scope, variable_id))


@router.patch("/{variable_id}", response_model=VariableRead)
async def update_variable(
    variable_id: uuid.UUID,
    payload: VariableUpdate,
    scope: ScopeDep,
    service: VariableServiceDep,
) -> VariableRead:
    variable = await service.update(
        scope, variable_id, payload.model_dump(exclude_unset=True)
    )
    return VariableRead.model_validate(variable)


@router.delete("/{variable_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_variable(
    variable_id: uuid.UUID, scope: ScopeDep, service: VariableServiceDep
) -> None:
    await service.delete(scope, variable_id)
