"""AuthService behaviour not reachable through the HTTP layer."""

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AuthenticationError
from app.core.security import TokenType, create_token, decode_token
from app.models import User
from app.repositories.user import UserRepository
from app.services.auth import AuthService
from tests.conftest import TEST_PASSWORD


@pytest.fixture
def auth_service(db_session: AsyncSession, settings: Settings) -> AuthService:
    return AuthService(UserRepository(db_session), settings)


class TestAuthenticate:
    async def test_valid_credentials_return_the_user(
        self, auth_service: AuthService, admin_user: User
    ) -> None:
        found = await auth_service.authenticate(admin_user.email, TEST_PASSWORD)

        assert found.id == admin_user.id

    async def test_every_failure_uses_the_same_message(
        self, auth_service: AuthService, admin_user: User, db_session: AsyncSession
    ) -> None:
        messages = set()

        for email, password in [
            ("nadie@example.com", TEST_PASSWORD),
            (admin_user.email, "clave-incorrecta"),
        ]:
            with pytest.raises(AuthenticationError) as caught:
                await auth_service.authenticate(email, password)
            messages.add(caught.value.message)

        admin_user.is_active = False
        await db_session.flush()
        with pytest.raises(AuthenticationError) as caught:
            await auth_service.authenticate(admin_user.email, TEST_PASSWORD)
        messages.add(caught.value.message)

        assert messages == {"Incorrect email or password"}


class TestTokenSubjects:
    async def test_a_subject_that_is_not_a_uuid_is_rejected(
        self, auth_service: AuthService, settings: Settings
    ) -> None:
        """A hand-crafted token could carry anything in `sub`."""
        token = create_token(
            settings,
            subject="not-a-uuid",
            token_type=TokenType.ACCESS,
            expires_in=timedelta(minutes=5),
        )

        with pytest.raises(AuthenticationError, match="Invalid token"):
            await auth_service.resolve_access_token(token)

    async def test_a_refresh_with_a_non_uuid_subject_is_rejected(
        self, auth_service: AuthService, settings: Settings
    ) -> None:
        token = create_token(
            settings,
            subject="not-a-uuid",
            token_type=TokenType.REFRESH,
            expires_in=timedelta(days=1),
        )

        with pytest.raises(AuthenticationError):
            await auth_service.refresh(token)


class TestIssuedClaims:
    async def test_an_internal_user_carries_no_client(
        self, auth_service: AuthService, admin_user: User, settings: Settings
    ) -> None:
        pair = auth_service.issue_tokens(admin_user)
        payload = decode_token(
            settings, pair.access_token, expected_type=TokenType.ACCESS
        )

        assert payload["role"] == "admin"
        assert payload["client_id"] is None

    async def test_a_client_user_carries_its_client_id(
        self, auth_service: AuthService, cliente_user: User, settings: Settings
    ) -> None:
        pair = auth_service.issue_tokens(cliente_user)
        payload = decode_token(
            settings, pair.access_token, expected_type=TokenType.ACCESS
        )

        assert payload["role"] == "cliente"
        assert payload["client_id"] == str(cliente_user.client_id)


class TestChangeOwnPasswordEdges:
    """Paths the HTTP layer cannot reach, since it always supplies an identity."""

    async def test_a_scope_without_an_identity_is_rejected(
        self, db_session: AsyncSession, settings: Settings
    ) -> None:
        from app.domain.access import AccessScope
        from app.domain.enums import UserRole
        from app.repositories.hierarchy import ClientRepository
        from app.repositories.user import UserRepository
        from app.services.user import UserService

        service = UserService(UserRepository(db_session), ClientRepository(db_session))

        with pytest.raises(AuthenticationError):
            await service.change_own_password(
                AccessScope(role=UserRole.ADMIN), "old", "una-clave-nueva"
            )

    async def test_a_scope_pointing_at_a_deleted_user_is_rejected(
        self, db_session: AsyncSession
    ) -> None:
        import uuid

        from app.domain.access import AccessScope
        from app.domain.enums import UserRole
        from app.repositories.hierarchy import ClientRepository
        from app.repositories.user import UserRepository
        from app.services.user import UserService

        service = UserService(UserRepository(db_session), ClientRepository(db_session))

        with pytest.raises(AuthenticationError):
            await service.change_own_password(
                AccessScope(role=UserRole.ADMIN, user_id=uuid.uuid4()),
                "old",
                "una-clave-nueva",
            )
