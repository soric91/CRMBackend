"""Authentication endpoints.

There is deliberately no registration endpoint: the first administrator is
created by `app/scripts/create_admin.py`, and every account after that by an
authenticated admin. A public sign-up route would let anyone mint an admin.
"""

from fastapi import APIRouter, status

from app.api.deps import (
    AuthServiceDep,
    CurrentCrmUserDep,
    ScopeDep,
    UserServiceDep,
)
from app.schemas.auth import LoginRequest, RefreshRequest, TokenPair, UserRead
from app.schemas.user import PasswordChange

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenPair)
async def login(payload: LoginRequest, auth: AuthServiceDep) -> TokenPair:
    """Exchange credentials for an access/refresh pair."""
    return await auth.login(payload.email, payload.password)


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest, auth: AuthServiceDep) -> TokenPair:
    """Exchange a refresh token for a new pair."""
    return await auth.refresh(payload.refresh_token)


@router.get("/me", response_model=UserRead, status_code=status.HTTP_200_OK)
async def me(user: CurrentCrmUserDep) -> UserRead:
    """Return the caller's own account."""
    return UserRead.model_validate(user)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_own_password(
    payload: PasswordChange, scope: ScopeDep, service: UserServiceDep
) -> None:
    """Replace the caller's own password.

    Any role may do this, including `cliente`: the first password is handed
    over by an administrator, so the user needs a way to change it.
    """
    await service.change_own_password(
        scope, payload.current_password, payload.new_password
    )
