"""Shared FastAPI dependencies: wiring and authorization."""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import get_db_session
from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    PasswordChangeRequiredError,
)
from app.core.secret_box import SecretBox
from app.core.security import TokenAudience, TokenScope
from app.domain.access import AccessScope
from app.domain.enums import UserRole
from app.models import User
from app.repositories.enrollment_token import EnrollmentTokenRepository
from app.repositories.hierarchy import (
    ClientRepository,
    EquipmentRepository,
    FleetRepository,
    GatewayRepository,
    SiteRepository,
    VariableRepository,
)
from app.repositories.platform_setting import PlatformSettingRepository
from app.repositories.service_account import ServiceAccountRepository
from app.repositories.tariff import TariffRepository
from app.repositories.user import UserRepository
from app.schemas.common import Pagination
from app.services.auth import AuthService, ResolvedToken
from app.services.auth_monitor import MonitorAccessService, MonitorAuthService
from app.services.enrollment import EnrollmentService
from app.services.fleet import FleetService
from app.services.gateway_config import (
    GatewayConfigService,
    GatewayCredentialService,
    GatewayTokenService,
)
from app.services.hierarchy import (
    ClientService,
    EquipmentService,
    GatewayService,
    SiteService,
    VariableService,
)
from app.services.platform_settings import PlatformSettingService
from app.services.service_accounts import ServiceAccountService, ServiceTokenService
from app.services.tariff import TariffService
from app.services.user import UserService

# auto_error=False so a missing header raises our own AuthenticationError,
# with the same response shape as every other error in the API.
_bearer = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


def get_app_settings(request: Request) -> Settings:
    """Return the settings the running app was built with."""
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def get_user_repository(session: SessionDep) -> UserRepository:
    return UserRepository(session)


UserRepositoryDep = Annotated[UserRepository, Depends(get_user_repository)]


def get_auth_service(users: UserRepositoryDep, settings: SettingsDep) -> AuthService:
    return AuthService(users, settings)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


# The only routes a password-change token reaches. Anything else is refused
# with `password_change_required`, so the mandatory change is enforced by the
# API and not merely suggested to the user interface.
ALLOWED_WHILE_RESTRICTED = frozenset(
    {
        "/api/v1/auth/me",
        "/api/v1/auth/password",
        "/api/v1/auth-monitor/me",
        "/api/v1/auth-monitor/password",
    }
)


async def _resolve_bearer(
    auth: AuthService,
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> User:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Missing bearer token")
    user, scope = await auth.resolve_access_token(credentials.credentials)
    if (
        scope is TokenScope.PASSWORD_CHANGE
        and request.url.path not in ALLOWED_WHILE_RESTRICTED
    ):
        raise PasswordChangeRequiredError
    return user


async def get_current_user(
    request: Request,
    users: UserRepositoryDep,
    settings: SettingsDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer)
    ] = None,
) -> User:
    """Return the caller behind a token from either surface.

    The administrative data is read through these same routes by both webs, so
    both audiences are honoured here. What each caller may reach is decided by
    its role: a `cliente` is confined to its own company either way. The
    authentication surfaces themselves stay exclusive — see
    :func:`get_current_crm_user` and :func:`get_current_monitor_user`.
    """
    auth = AuthService(
        users,
        settings,
        accepted_audiences=(TokenAudience.CRM, TokenAudience.MONITOR),
    )
    return await _resolve_bearer(auth, request, credentials)


CurrentUserDep = Annotated[User, Depends(get_current_user)]


async def get_current_crm_user(
    request: Request,
    auth: AuthServiceDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer)
    ] = None,
) -> User:
    """Return the caller, refusing tokens minted for the monitoring web."""
    return await _resolve_bearer(auth, request, credentials)


CurrentCrmUserDep = Annotated[User, Depends(get_current_crm_user)]


def get_monitor_auth_service(
    users: UserRepositoryDep, settings: SettingsDep, session: SessionDep
) -> MonitorAuthService:
    """The monitoring web's auth, minting tokens for its own audience."""
    return MonitorAuthService(
        AuthService(users, settings, audience=TokenAudience.MONITOR),
        users,
        ClientRepository(session),
    )


MonitorAuthServiceDep = Annotated[MonitorAuthService, Depends(get_monitor_auth_service)]


async def get_current_monitor_user(
    request: Request,
    users: UserRepositoryDep,
    settings: SettingsDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer)
    ] = None,
) -> User:
    """Return the client behind a monitoring token, or fail with 401."""
    auth = AuthService(users, settings, audience=TokenAudience.MONITOR)
    return await _resolve_bearer(auth, request, credentials)


CurrentMonitorUserDep = Annotated[User, Depends(get_current_monitor_user)]


async def get_current_monitor_session(
    request: Request,
    users: UserRepositoryDep,
    settings: SettingsDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer)
    ] = None,
) -> ResolvedToken:
    """El usuario detrás del token y la empresa que ese token abre.

    Distinto de :func:`get_current_monitor_user`, que solo devuelve la cuenta:
    en una suplantación la empresa no sale de la cuenta —un administrador no
    pertenece a ninguna— sino del token, y hay endpoints que necesitan saber
    cuál es y que hubo suplantación.
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Missing bearer token")
    auth = AuthService(users, settings, audience=TokenAudience.MONITOR)
    resolved = await auth.resolve_access(credentials.credentials)
    if (
        resolved.scope is TokenScope.PASSWORD_CHANGE
        and request.url.path not in ALLOWED_WHILE_RESTRICTED
    ):
        raise PasswordChangeRequiredError
    return resolved


CurrentMonitorSessionDep = Annotated[
    ResolvedToken, Depends(get_current_monitor_session)
]


def get_monitor_access_service(session: SessionDep) -> MonitorAccessService:
    return MonitorAccessService(UserRepository(session), ClientRepository(session))


MonitorAccessServiceDep = Annotated[
    MonitorAccessService, Depends(get_monitor_access_service)
]


def require_roles(
    *allowed: UserRole,
) -> Callable[[User], Coroutine[Any, Any, User]]:
    """Build a dependency that admits only the listed roles.

    Used as ``Depends(require_roles(UserRole.ADMIN))``. Listing the roles that
    may pass, rather than the ones that may not, means a role added later is
    denied by default instead of silently allowed.
    """
    permitted = frozenset(allowed)

    async def _guard(user: CurrentUserDep) -> User:
        if user.role not in permitted:
            raise AuthorizationError(
                f"Role '{user.role.value}' cannot perform this action"
            )
        return user

    return _guard


def get_access_scope(user: CurrentUserDep) -> AccessScope:
    """Reduce the authenticated user to what the services need to know."""
    return AccessScope(role=user.role, client_id=user.client_id, user_id=user.id)


ScopeDep = Annotated[AccessScope, Depends(get_access_scope)]


def get_client_service(session: SessionDep) -> ClientService:
    return ClientService(ClientRepository(session))


ClientServiceDep = Annotated[ClientService, Depends(get_client_service)]


def get_site_service(session: SessionDep, clients: ClientServiceDep) -> SiteService:
    return SiteService(SiteRepository(session), clients)


SiteServiceDep = Annotated[SiteService, Depends(get_site_service)]


def get_gateway_service(session: SessionDep, sites: SiteServiceDep) -> GatewayService:
    return GatewayService(GatewayRepository(session), sites)


GatewayServiceDep = Annotated[GatewayService, Depends(get_gateway_service)]


def get_equipment_service(
    session: SessionDep, gateways: GatewayServiceDep
) -> EquipmentService:
    return EquipmentService(EquipmentRepository(session), gateways)


EquipmentServiceDep = Annotated[EquipmentService, Depends(get_equipment_service)]


def get_variable_service(
    session: SessionDep, equipment: EquipmentServiceDep
) -> VariableService:
    return VariableService(VariableRepository(session), equipment)


VariableServiceDep = Annotated[VariableService, Depends(get_variable_service)]


def get_user_service(session: SessionDep) -> UserService:
    return UserService(UserRepository(session), ClientRepository(session))


UserServiceDep = Annotated[UserService, Depends(get_user_service)]


def get_tariff_service(session: SessionDep) -> TariffService:
    return TariffService(TariffRepository(session))


TariffServiceDep = Annotated[TariffService, Depends(get_tariff_service)]


def get_gateway_credential_service(session: SessionDep) -> GatewayCredentialService:
    return GatewayCredentialService(GatewayRepository(session))


GatewayCredentialServiceDep = Annotated[
    GatewayCredentialService, Depends(get_gateway_credential_service)
]


def get_gateway_token_service(
    session: SessionDep, settings: SettingsDep
) -> GatewayTokenService:
    return GatewayTokenService(session, settings)


GatewayTokenServiceDep = Annotated[
    GatewayTokenService, Depends(get_gateway_token_service)
]


def get_gateway_config_service(session: SessionDep) -> GatewayConfigService:
    return GatewayConfigService(session)


GatewayConfigServiceDep = Annotated[
    GatewayConfigService, Depends(get_gateway_config_service)
]


def get_fleet_service(session: SessionDep) -> FleetService:
    return FleetService(FleetRepository(session))


FleetServiceDep = Annotated[FleetService, Depends(get_fleet_service)]


def get_enrollment_service(
    session: SessionDep, settings: SettingsDep
) -> EnrollmentService:
    return EnrollmentService(
        EnrollmentTokenRepository(session),
        GatewayRepository(session),
        PlatformSettingService(PlatformSettingRepository(session), SecretBox(settings)),
        GatewayCredentialService(GatewayRepository(session)),
    )


EnrollmentServiceDep = Annotated[EnrollmentService, Depends(get_enrollment_service)]


def get_platform_setting_service(
    session: SessionDep, settings: SettingsDep
) -> PlatformSettingService:
    return PlatformSettingService(
        PlatformSettingRepository(session), SecretBox(settings)
    )


PlatformSettingServiceDep = Annotated[
    PlatformSettingService, Depends(get_platform_setting_service)
]


def get_service_account_service(session: SessionDep) -> ServiceAccountService:
    return ServiceAccountService(
        ServiceAccountRepository(session), ClientRepository(session)
    )


ServiceAccountServiceDep = Annotated[
    ServiceAccountService, Depends(get_service_account_service)
]


def get_service_token_service(
    session: SessionDep, settings: SettingsDep
) -> ServiceTokenService:
    return ServiceTokenService(ServiceAccountRepository(session), settings)


ServiceTokenServiceDep = Annotated[
    ServiceTokenService, Depends(get_service_token_service)
]


async def get_machine_or_user_scope(
    request: Request,
    users: UserRepositoryDep,
    settings: SettingsDep,
    tokens: ServiceTokenServiceDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer)
    ] = None,
) -> AccessScope:
    """Resolve a caller that may be a person **or** another system.

    Wired by hand into the few read endpoints that machines consume, never
    applied globally. That is the containment: a route reaches service tokens
    only by asking for this dependency by name, so an endpoint added later is
    closed to them by default rather than open until somebody notices.

    A service token is tried only after the human audiences have refused it,
    so the ordinary path is unchanged for ordinary callers.
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Missing bearer token")

    try:
        user = await get_current_user(request, users, settings, credentials)
    except AuthenticationError:
        return await tokens.scope_from_token(credentials.credentials)
    return get_access_scope(user)


MachineOrUserScopeDep = Annotated[AccessScope, Depends(get_machine_or_user_scope)]

PaginationDep = Annotated[Pagination, Depends()]
