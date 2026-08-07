"""Role-based authorization: `require_roles`.

Phase 3 has no business routes to protect yet, so the guard is mounted on
throwaway routes here. When phase 4 adds real CRUD, these keep proving the
mechanism itself works.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI, status
from httpx import ASGITransport, AsyncClient

from app.api.deps import CurrentUserDep, require_roles
from app.domain.enums import UserRole
from app.models import User
from tests.conftest import auth_header

AdminOnly = Annotated[User, Depends(require_roles(UserRole.ADMIN))]
Staff = Annotated[User, Depends(require_roles(UserRole.ADMIN, UserRole.TECNICO))]


@pytest.fixture
def guarded_app(app: FastAPI) -> FastAPI:
    """The real app plus routes exercising each guard."""
    router = APIRouter(prefix="/api/v1/_test", tags=["test"])

    @router.get("/admin-only")
    async def _admin_only(_: AdminOnly) -> dict[str, bool]:
        return {"ok": True}

    @router.get("/staff")
    async def _staff(_: Staff) -> dict[str, bool]:
        return {"ok": True}

    @router.get("/any-authenticated")
    async def _any(user: CurrentUserDep) -> dict[str, str]:
        return {"role": user.role.value}

    app.include_router(router)
    return app


@pytest.fixture
async def guarded_client(guarded_app: FastAPI) -> AsyncClient:
    transport = ASGITransport(app=guarded_app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestSingleRoleGuard:
    async def test_an_admin_passes(
        self,
        guarded_client: AsyncClient,
        admin_user: User,
        authenticate: Callable[[str], Awaitable[str]],
    ) -> None:
        token = await authenticate(admin_user.email)

        response = await guarded_client.get(
            "/api/v1/_test/admin-only", headers=auth_header(token)
        )

        assert response.status_code == status.HTTP_200_OK

    async def test_a_tecnico_is_forbidden(
        self,
        guarded_client: AsyncClient,
        tecnico_user: User,
        authenticate: Callable[[str], Awaitable[str]],
    ) -> None:
        token = await authenticate(tecnico_user.email)

        response = await guarded_client.get(
            "/api/v1/_test/admin-only", headers=auth_header(token)
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["error"]["code"] == "not_authorized"

    async def test_a_cliente_is_forbidden(
        self,
        guarded_client: AsyncClient,
        cliente_user: User,
        authenticate_monitor: Callable[..., Awaitable[str]],
    ) -> None:
        """A client only ever holds a monitoring token; it still fails here."""
        token = await authenticate_monitor(cliente_user.email)

        response = await guarded_client.get(
            "/api/v1/_test/admin-only", headers=auth_header(token)
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_an_anonymous_caller_gets_401_not_403(
        self, guarded_client: AsyncClient
    ) -> None:
        """Unauthenticated is a different problem from unauthorized."""
        response = await guarded_client.get("/api/v1/_test/admin-only")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestMultiRoleGuard:
    async def test_every_listed_role_passes(
        self,
        guarded_client: AsyncClient,
        admin_user: User,
        tecnico_user: User,
        authenticate: Callable[[str], Awaitable[str]],
    ) -> None:
        for user in (admin_user, tecnico_user):
            token = await authenticate(user.email)
            response = await guarded_client.get(
                "/api/v1/_test/staff", headers=auth_header(token)
            )
            assert response.status_code == status.HTTP_200_OK, user.email

    async def test_an_unlisted_role_is_forbidden(
        self,
        guarded_client: AsyncClient,
        cliente_user: User,
        authenticate_monitor: Callable[..., Awaitable[str]],
    ) -> None:
        token = await authenticate_monitor(cliente_user.email)

        response = await guarded_client.get(
            "/api/v1/_test/staff", headers=auth_header(token)
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestAuthenticatedOnly:
    async def test_any_role_reaches_an_unguarded_authenticated_route(
        self,
        guarded_client: AsyncClient,
        cliente_user: User,
        authenticate_monitor: Callable[..., Awaitable[str]],
    ) -> None:
        token = await authenticate_monitor(cliente_user.email)

        response = await guarded_client.get(
            "/api/v1/_test/any-authenticated", headers=auth_header(token)
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["role"] == "cliente"


class TestDenyByDefault:
    def test_the_guard_lists_who_may_pass_not_who_may_not(self) -> None:
        """A role added later must be denied until listed, never allowed."""
        guard = require_roles(UserRole.ADMIN)
        permitted = guard.__closure__[0].cell_contents  # type: ignore[union-attr,index]

        assert permitted == frozenset({UserRole.ADMIN})
        assert UserRole.SOLO_LECTURA not in permitted
