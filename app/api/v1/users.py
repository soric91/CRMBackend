"""User account endpoints. Administrators only."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import PaginationDep, ScopeDep, UserServiceDep
from app.domain.enums import UserRole
from app.schemas.auth import UserRead
from app.schemas.common import Page
from app.schemas.user import PasswordSet, UserCreate, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=Page[UserRead])
async def list_users(
    scope: ScopeDep,
    service: UserServiceDep,
    pagination: PaginationDep,
    client_id: Annotated[uuid.UUID | None, Query()] = None,
    role: Annotated[UserRole | None, Query()] = None,
) -> Page[UserRead]:
    """List accounts, optionally narrowed to one client or one role."""
    items, total = await service.list(
        scope,
        limit=pagination.limit,
        offset=pagination.offset,
        client_id=client_id,
        role=role,
    )
    return Page[UserRead](
        items=[UserRead.model_validate(item) for item in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate, scope: ScopeDep, service: UserServiceDep
) -> UserRead:
    """Create an account.

    This is how a client gets access to its consumption page: an account with
    role `cliente` bound to that client.
    """
    user = await service.create(scope, payload.model_dump())
    return UserRead.model_validate(user)


@router.get("/{user_id}", response_model=UserRead)
async def get_user(
    user_id: uuid.UUID, scope: ScopeDep, service: UserServiceDep
) -> UserRead:
    return UserRead.model_validate(await service.get(scope, user_id))


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    scope: ScopeDep,
    service: UserServiceDep,
) -> UserRead:
    """Change a role, rebind a client, or activate and deactivate."""
    user = await service.update(scope, user_id, payload.model_dump(exclude_unset=True))
    return UserRead.model_validate(user)


@router.post("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def set_user_password(
    user_id: uuid.UUID,
    payload: PasswordSet,
    scope: ScopeDep,
    service: UserServiceDep,
) -> None:
    """Set someone else's password, for when they have lost access."""
    await service.set_password(scope, user_id, payload.new_password)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID, scope: ScopeDep, service: UserServiceDep
) -> None:
    await service.delete(scope, user_id)
