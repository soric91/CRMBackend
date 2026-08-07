"""Authentication for the monitoring web.

A surface of its own, apart from the CRM's `/auth`: another public, another
audience, another set of rules. Only the `cliente` role gets in, and an account
whose password was issued by the CRM cannot go anywhere until it replaces it.
"""

from fastapi import APIRouter, Request

from app.api.deps import CurrentMonitorUserDep, MonitorAuthServiceDep
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
async def me(user: CurrentMonitorUserDep) -> MonitorIdentity:
    """Return the caller's identity. Reachable even with a restricted token."""
    return MonitorIdentity(
        user_id=user.id,
        email=user.email,
        client_id=user.client_id,  # pyright: ignore[reportArgumentType]
        must_change_password=user.must_change_password,
    )


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
