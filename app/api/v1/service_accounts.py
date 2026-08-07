"""Machine-to-machine credentials, from both ends.

Two routers with nothing in common but the table behind them:

* `/service-accounts` is the panel's. Administrators only. It creates, lists,
  narrows, rotates and revokes.
* `/service/token` is the consumer's. It takes a credential and returns a
  short-lived token, and it can do nothing else — no listing, no reading back
  what it was granted beyond what the token itself says.

Keeping them apart is what stops a leaked credential from being usable to
discover what else exists.
"""

import uuid
from typing import Any

from fastapi import APIRouter, status

from app.api.deps import (
    PaginationDep,
    ScopeDep,
    ServiceAccountServiceDep,
    ServiceTokenServiceDep,
)
from app.domain.enums import ServicePermission
from app.models import ServiceAccount
from app.schemas.common import Page
from app.schemas.service_account import (
    ServiceAccountCreate,
    ServiceAccountCreated,
    ServiceAccountRead,
    ServiceAccountUpdate,
    ServiceTokenRequest,
    ServiceTokenResponse,
)

router = APIRouter(prefix="/service-accounts", tags=["service-accounts"])
token_router = APIRouter(prefix="/service", tags=["service"])


@router.get("", response_model=Page[ServiceAccountRead])
async def list_service_accounts(
    scope: ScopeDep, service: ServiceAccountServiceDep, pagination: PaginationDep
) -> Page[ServiceAccountRead]:
    """List the systems that hold a credential. Administrators only."""
    items, total = await service.list(
        scope, limit=pagination.limit, offset=pagination.offset
    )
    return Page[ServiceAccountRead](
        items=[ServiceAccountRead.model_validate(item) for item in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.post(
    "", response_model=ServiceAccountCreated, status_code=status.HTTP_201_CREATED
)
async def create_service_account(
    payload: ServiceAccountCreate,
    scope: ScopeDep,
    service: ServiceAccountServiceDep,
) -> ServiceAccountCreated:
    """Issue a credential. The secret is shown here and nowhere else.

    Grant the narrowest set of permissions the consumer actually needs: they
    can be widened later without rotating the secret, and starting wide is how
    a credential ends up with reach nobody remembers granting.
    """
    account, secret = await service.create(scope, payload.model_dump())
    return ServiceAccountCreated(**_as_read(account), client_secret=secret)


@router.get("/{account_id}", response_model=ServiceAccountRead)
async def get_service_account(
    account_id: uuid.UUID, scope: ScopeDep, service: ServiceAccountServiceDep
) -> ServiceAccountRead:
    return ServiceAccountRead.model_validate(await service.get(scope, account_id))


@router.patch("/{account_id}", response_model=ServiceAccountRead)
async def update_service_account(
    account_id: uuid.UUID,
    payload: ServiceAccountUpdate,
    scope: ScopeDep,
    service: ServiceAccountServiceDep,
) -> ServiceAccountRead:
    """Narrow, widen, deactivate or re-date a credential without rotating it."""
    account = await service.update(
        scope, account_id, payload.model_dump(exclude_unset=True)
    )
    return ServiceAccountRead.model_validate(account)


@router.post("/{account_id}/secret", response_model=ServiceAccountCreated)
async def rotate_service_secret(
    account_id: uuid.UUID, scope: ScopeDep, service: ServiceAccountServiceDep
) -> ServiceAccountCreated:
    """Issue a new secret, revoking the previous one at once.

    The consumer cannot obtain new tokens until the new secret reaches it.
    Tokens it already holds keep working until they expire.
    """
    account, secret = await service.rotate_secret(scope, account_id)
    return ServiceAccountCreated(**_as_read(account), client_secret=secret)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_service_account(
    account_id: uuid.UUID, scope: ScopeDep, service: ServiceAccountServiceDep
) -> None:
    """Remove it entirely. To keep the record, deactivate it instead."""
    await service.delete(scope, account_id)


def _as_read(account: ServiceAccount) -> dict[str, Any]:
    return ServiceAccountRead.model_validate(account).model_dump()


@token_router.post("/token", response_model=ServiceTokenResponse)
async def issue_service_token(
    payload: ServiceTokenRequest, tokens: ServiceTokenServiceDep
) -> ServiceTokenResponse:
    """Exchange a service credential for a short-lived token.

    No bearer header: the credential in the body *is* the authentication. The
    consumer calls this on start-up and again whenever its token expires.

    Every failure answers the same 401 — unknown identifier, wrong secret,
    deactivated account, expired credential. Telling them apart would let
    somebody holding a leaked identifier learn whether it is still live.
    """
    token, account = await tokens.issue_token(payload.client_id, payload.client_secret)
    return ServiceTokenResponse(
        access_token=token,
        expires_in=tokens.token_lifetime_seconds,
        permisos=[ServicePermission(item) for item in account.permisos],
        scope_client_id=account.client_id,
    )
