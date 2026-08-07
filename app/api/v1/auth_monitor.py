"""Authentication for the monitoring web.

A surface of its own, apart from the CRM's `/auth`: another public, another
audience, another set of rules. Only the `cliente` role gets in, and an account
whose password was issued by the CRM cannot go anywhere until it replaces it.
"""

import uuid

from fastapi import APIRouter, Request

from app.api.deps import (
    CurrentMonitorSessionDep,
    CurrentMonitorUserDep,
    MonitorAuthServiceDep,
)
from app.core.exceptions import AuthenticationError
from app.core.logging import get_logger
from app.core.rate_limit import monitor_login_limiter
from app.schemas.auth import LoginRequest, RefreshRequest
from app.schemas.auth_monitor import MonitorIdentity, MonitorTokenPair
from app.schemas.user import PasswordChange

logger = get_logger(__name__)
router = APIRouter(prefix="/auth-monitor", tags=["auth-monitor"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/login", response_model=MonitorTokenPair)
async def login(
    payload: LoginRequest, request: Request, auth: MonitorAuthServiceDep
) -> MonitorTokenPair:
    """Exchange credentials for tokens plus the client whose data to show.

    Rate limited per address and per source IP: unlike the CRM this is reachable
    by anyone with the link, and the password the client chooses after the first
    login may be weak.
    """
    keys = (f"ip:{_client_ip(request)}", f"email:{payload.email.lower()}")
    if not all(monitor_login_limiter.hit(key) for key in keys):
        logger.warning("monitor login throttled", extra={"ip": _client_ip(request)})
        raise AuthenticationError("Too many attempts; try again later")

    pair = await auth.login(payload.email, payload.password)
    for key in keys:
        monitor_login_limiter.reset(key)
    return pair


@router.post("/refresh", response_model=MonitorTokenPair)
async def refresh(
    payload: RefreshRequest, auth: MonitorAuthServiceDep
) -> MonitorTokenPair:
    """Exchange a refresh token. A pending password change survives it."""
    return await auth.refresh(payload.refresh_token)


@router.get("/me", response_model=MonitorIdentity)
async def me(session: CurrentMonitorSessionDep) -> MonitorIdentity:
    """Return the caller's identity. Reachable even with a restricted token.

    `client_id` sale del token y no de la cuenta: es la empresa que este token
    abre, que en una suplantación no es la del usuario y en un administrador
    que todavía no eligió no es ninguna.
    """
    return MonitorIdentity(
        user_id=session.user.id,
        email=session.user.email,
        client_id=session.client_id,
        role=session.user.role,
        impersonated=session.impersonated,
        must_change_password=session.user.must_change_password,
    )


@router.post("/impersonate/{client_id}", response_model=MonitorTokenPair)
async def impersonate(
    client_id: uuid.UUID,
    session: CurrentMonitorSessionDep,
    auth: MonitorAuthServiceDep,
) -> MonitorTokenPair:
    """Cambiar a los datos de una empresa. Solo administradores.

    Devuelve un par de tokens nuevo en vez de guardar la elección en el
    servidor: así el estado vive en el token de quien llama, todo lo que lee
    datos sigue viendo una sola empresa, y volver atrás es descartar el token.

    Se puede entrar a una empresa con `puede_ver_consumo` apagado: esa marca
    decide lo que ve el cliente, no lo que ve quien lo administra.
    """
    return await auth.impersonate(session.user, client_id)


@router.post("/password", response_model=MonitorTokenPair)
async def change_password(
    payload: PasswordChange,
    user: CurrentMonitorUserDep,
    auth: MonitorAuthServiceDep,
) -> MonitorTokenPair:
    """Replace the caller's password and return a token that works.

    Returning 204 would leave the client holding a restricted token and no way
    to use it, so the new unrestricted pair comes back here.
    """
    return await auth.change_own_password(
        user, payload.current_password, payload.new_password
    )
