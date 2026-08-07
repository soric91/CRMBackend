"""The /auth endpoints end to end."""

from collections.abc import Awaitable, Callable

import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import (
    TokenType,
    create_access_token,
    create_refresh_token,
    create_token,
)
from app.models import User
from tests.conftest import TEST_PASSWORD, auth_header


class TestLogin:
    async def test_valid_credentials_return_a_token_pair(
        self, client: AsyncClient, admin_user: User
    ) -> None:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": admin_user.email, "password": TEST_PASSWORD},
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["access_token"]
        assert body["refresh_token"]
        assert body["token_type"] == "bearer"
        assert body["expires_in"] > 0

    async def test_the_email_is_matched_case_insensitively(
        self, client: AsyncClient, admin_user: User
    ) -> None:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "ADMIN@EXAMPLE.COM", "password": TEST_PASSWORD},
        )

        assert response.status_code == status.HTTP_200_OK

    async def test_a_wrong_password_is_rejected(
        self, client: AsyncClient, admin_user: User
    ) -> None:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": admin_user.email, "password": "no-es-la-clave"},
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_an_unknown_email_is_rejected(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "nadie@example.com", "password": TEST_PASSWORD},
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_unknown_email_and_wrong_password_are_indistinguishable(
        self, client: AsyncClient, admin_user: User
    ) -> None:
        """Different messages would hand an attacker a list of valid accounts."""
        unknown = await client.post(
            "/api/v1/auth/login",
            json={"email": "nadie@example.com", "password": "x" * 12},
        )
        wrong = await client.post(
            "/api/v1/auth/login",
            json={"email": admin_user.email, "password": "x" * 12},
        )

        assert unknown.status_code == wrong.status_code
        assert unknown.json() == wrong.json()

    async def test_an_inactive_account_cannot_log_in(
        self, client: AsyncClient, db_session: AsyncSession, admin_user: User
    ) -> None:
        admin_user.is_active = False
        await db_session.flush()

        response = await client.post(
            "/api/v1/auth/login",
            json={"email": admin_user.email, "password": TEST_PASSWORD},
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_the_response_never_leaks_the_hash(
        self, client: AsyncClient, admin_user: User
    ) -> None:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": admin_user.email, "password": TEST_PASSWORD},
        )

        assert "password" not in response.text
        assert "$2b$" not in response.text

    @pytest.mark.parametrize(
        "payload",
        [
            {"email": "not-an-email", "password": "x" * 12},
            {"email": "a@b.com", "password": "short"},
            {"email": "a@b.com"},
            {"password": "x" * 12},
            {},
        ],
    )
    async def test_malformed_payloads_are_rejected_before_any_lookup(
        self, client: AsyncClient, payload: dict[str, str]
    ) -> None:
        response = await client.post("/api/v1/auth/login", json=payload)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestProtectedRoute:
    async def test_a_valid_token_identifies_the_caller(
        self,
        client: AsyncClient,
        admin_user: User,
        authenticate: Callable[[str], Awaitable[str]],
    ) -> None:
        token = await authenticate(admin_user.email)

        response = await client.get("/api/v1/auth/me", headers=auth_header(token))

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["email"] == admin_user.email
        assert body["role"] == "admin"
        assert "password_hash" not in body

    async def test_a_missing_header_is_rejected(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/auth/me")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_a_garbage_token_is_rejected(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/auth/me", headers=auth_header("garbage"))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_an_expired_token_is_rejected(
        self, client: AsyncClient, settings: Settings, admin_user: User
    ) -> None:
        from datetime import timedelta

        expired = create_token(
            settings,
            subject=str(admin_user.id),
            token_type=TokenType.ACCESS,
            expires_in=timedelta(seconds=-1),
        )

        response = await client.get("/api/v1/auth/me", headers=auth_header(expired))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_a_refresh_token_is_not_accepted_as_a_bearer(
        self, client: AsyncClient, settings: Settings, admin_user: User
    ) -> None:
        refresh = create_refresh_token(settings, subject=str(admin_user.id))

        response = await client.get("/api/v1/auth/me", headers=auth_header(refresh))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_a_token_for_a_deleted_user_is_rejected(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        """The subject is re-read from the database, never trusted blindly."""
        import uuid

        orphan = create_access_token(
            settings, subject=str(uuid.uuid4()), claims={"role": "admin"}
        )

        response = await client.get("/api/v1/auth/me", headers=auth_header(orphan))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_deactivating_an_account_invalidates_its_live_token(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        admin_user: User,
        authenticate: Callable[[str], Awaitable[str]],
    ) -> None:
        token = await authenticate(admin_user.email)
        admin_user.is_active = False
        await db_session.flush()

        response = await client.get("/api/v1/auth/me", headers=auth_header(token))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestRefresh:
    async def test_a_refresh_token_yields_a_new_pair(
        self, client: AsyncClient, admin_user: User
    ) -> None:
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": admin_user.email, "password": TEST_PASSWORD},
        )
        refresh_token = login.json()["refresh_token"]

        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["access_token"]
        assert response.json()["refresh_token"]

    async def test_an_access_token_cannot_be_used_to_refresh(
        self, client: AsyncClient, settings: Settings, admin_user: User
    ) -> None:
        access = create_access_token(settings, subject=str(admin_user.id))

        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": access}
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_a_refresh_token_of_a_deactivated_account_is_rejected(
        self, client: AsyncClient, db_session: AsyncSession, admin_user: User
    ) -> None:
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": admin_user.email, "password": TEST_PASSWORD},
        )
        refresh_token = login.json()["refresh_token"]
        admin_user.is_active = False
        await db_session.flush()

        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_a_role_change_takes_effect_on_the_next_refresh(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        admin_user: User,
        settings: Settings,
    ) -> None:
        """Privileges come from the database, not from the old token's claims."""
        from app.core.security import decode_token
        from app.domain.enums import UserRole

        login = await client.post(
            "/api/v1/auth/login",
            json={"email": admin_user.email, "password": TEST_PASSWORD},
        )
        refresh_token = login.json()["refresh_token"]

        admin_user.role = UserRole.SOLO_LECTURA
        await db_session.flush()

        refreshed = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
        )
        payload = decode_token(
            settings, refreshed.json()["access_token"], expected_type=TokenType.ACCESS
        )

        assert payload["role"] == "solo_lectura"


class TestNoPublicRegistration:
    """Accounts are never created anonymously.

    `/users` exists since phase 5, but it is admin-only. What must stay true is
    that no route mints an account without an authenticated administrator.
    """

    async def test_there_is_no_anonymous_registration_route(
        self, app_openapi: dict[str, object]
    ) -> None:
        paths = set(app_openapi["paths"])  # type: ignore[arg-type]
        forbidden = {"/api/v1/auth/register", "/api/v1/auth/signup"}

        assert not (paths & forbidden)

    async def test_creating_a_user_requires_a_token(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/users",
            json={
                "email": "intruso@example.com",
                "password": "una-clave-larga",
                "role": "admin",
            },
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_a_non_admin_cannot_mint_an_admin(
        self, client: AsyncClient, cliente_user: User, settings: Settings
    ) -> None:
        token = create_access_token(settings, subject=str(cliente_user.id))

        response = await client.post(
            "/api/v1/users",
            json={
                "email": "intruso@example.com",
                "password": "una-clave-larga",
                "role": "admin",
            },
            headers=auth_header(token),
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
