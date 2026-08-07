"""The monitoring web's authentication, and the access the CRM hands out.

A separate surface from the CRM's `/auth`: different audience, different rules,
different public. It shares the security primitives and the identity store —
`users` already holds an email, a hash, a role bound to a client and an active
flag — but none of the CRM's rules.
"""

import uuid

from app.core.exceptions import (
    AlreadyExistsError,
    AuthenticationError,
    AuthorizationError,
    BusinessRuleError,
    NotFoundError,
)
from app.core.logging import get_logger
from app.core.security import hash_password, verify_password
from app.domain.access import AccessScope
from app.domain.enums import UserRole
from app.domain.passwords import generate_temporary_password
from app.models import Client, User
from app.repositories.hierarchy import ClientRepository
from app.repositories.user import UserRepository, normalize_email
from app.schemas.auth_monitor import MonitorTokenPair
from app.services.auth import AuthService

logger = get_logger(__name__)


class MonitorAuthService:
    """Logs a client in to the monitoring web."""

    def __init__(self, auth: AuthService, users: UserRepository) -> None:
        self._auth = auth
        self._users = users

    @staticmethod
    def _require_client_role(user: User) -> None:
        """Refuse anyone who is not a client, without saying so.

        An administrator gets the same generic failure as a wrong password:
        confirming that the account exists but belongs elsewhere hands an
        attacker a map of the internal staff.
        """
        if user.role is not UserRole.CLIENTE:
            logger.info(
                "monitor login refused",
                extra={"reason": "not_a_client", "user_id": str(user.id)},
            )
            raise AuthenticationError("Incorrect email or password")

    def _pair_for(self, user: User) -> MonitorTokenPair:
        pair = self._auth.issue_tokens(user)
        if user.client_id is None:  # pragma: no cover - the database forbids it
            raise AuthenticationError("Incorrect email or password")
        return MonitorTokenPair(
            **pair.model_dump(),
            client_id=user.client_id,
            must_change_password=user.must_change_password,
        )

    async def login(self, email: str, password: str) -> MonitorTokenPair:
        """Verify credentials and return the tokens plus the client to show.

        `AuthService.authenticate` is reused as the primitive: it already
        normalises the address, spends a real bcrypt when the address is
        unknown, and treats a disabled account as a bad credential.
        """
        user = await self._auth.authenticate(email, password)
        self._require_client_role(user)
        return self._pair_for(user)

    async def refresh(self, refresh_token: str) -> MonitorTokenPair:
        """Exchange a refresh token, keeping whatever scope the account has now.

        The scope is recomputed from the stored flag, so refreshing is not a
        way around the mandatory password change.
        """
        user = await self._auth.user_from_refresh_token(refresh_token)
        self._require_client_role(user)
        return self._pair_for(user)

    async def change_own_password(
        self, user: User, current_password: str, new_password: str
    ) -> MonitorTokenPair:
        """Replace the caller's password and hand back a usable token pair.

        A 204 would leave the client holding a token that still reaches
        nothing, so the new, unrestricted pair comes back in the response.
        """
        if not verify_password(current_password, user.password_hash):
            raise AuthenticationError("Current password is incorrect")

        updated = await self._users.update(
            user,
            {
                "password_hash": hash_password(new_password),
                "must_change_password": False,
            },
        )
        return self._pair_for(updated)


class MonitorAccessService:
    """Creates, resets and revokes a client's access to the monitoring web.

    Used only by the CRM, under the usual write rules.
    """

    def __init__(self, users: UserRepository, clients: ClientRepository) -> None:
        self._users = users
        self._clients = clients

    @staticmethod
    def _require_write(scope: AccessScope) -> None:
        if not scope.can_write:
            raise AuthorizationError(
                f"Role '{scope.principal}' cannot manage monitoring access"
            )

    async def _client(self, scope: AccessScope, client_id: uuid.UUID) -> Client:
        client = await self._clients.get(client_id)
        if client is None or not scope.may_read_client(client.id):
            raise NotFoundError(f"Client {client_id} not found")
        return client

    async def _existing(self, client_id: uuid.UUID) -> User | None:
        users, _ = await self._users.list_page(
            limit=1, offset=0, client_id=client_id, role=UserRole.CLIENTE
        )
        return users[0] if users else None

    async def get(self, scope: AccessScope, client_id: uuid.UUID) -> User:
        await self._client(scope, client_id)
        access = await self._existing(client_id)
        if access is None:
            raise NotFoundError(f"Client {client_id} has no monitoring access")
        return access

    async def create(
        self, scope: AccessScope, client_id: uuid.UUID
    ) -> tuple[User, str]:
        """Issue an access with a one-off random password.

        Deliberately not tied to `puede_ver_consumo`: being able to log in and
        what there is to see inside are different questions. The CRM chains
        them; the backend does not couple them.
        """
        self._require_write(scope)
        client = await self._client(scope, client_id)

        if not client.contacto_email:
            raise BusinessRuleError("El cliente no tiene contacto_email cargado")
        email = normalize_email(client.contacto_email)

        password = generate_temporary_password()
        existing = await self._existing(client_id)
        if existing is not None:
            if existing.is_active:
                raise AlreadyExistsError(
                    "This client already has monitoring access; use reset to "
                    "issue a new password"
                )
            # Revoking disables the row rather than deleting it, so granting
            # access again reactivates that same row with a fresh password.
            user = await self._users.update(
                existing,
                {
                    "password_hash": hash_password(password),
                    "must_change_password": True,
                    "is_active": True,
                },
            )
            logger.info(
                "monitoring access reactivated", extra={"client_id": str(client_id)}
            )
            return user, password

        if await self._users.get_by_email(email) is not None:
            raise AlreadyExistsError(
                f"The address '{email}' already belongs to another account"
            )

        user = await self._users.add(
            User(
                email=email,
                password_hash=hash_password(password),
                role=UserRole.CLIENTE,
                client_id=client_id,
                is_active=True,
                must_change_password=True,
            )
        )
        logger.info("monitoring access created", extra={"client_id": str(client_id)})
        return user, password

    async def reset_password(
        self, scope: AccessScope, client_id: uuid.UUID
    ) -> tuple[User, str]:
        """Issue a new one-off password. Does not touch ``is_active``."""
        self._require_write(scope)
        access = await self.get(scope, client_id)

        password = generate_temporary_password()
        updated = await self._users.update(
            access,
            {
                "password_hash": hash_password(password),
                "must_change_password": True,
            },
        )
        logger.info("monitoring access reset", extra={"client_id": str(client_id)})
        return updated, password

    async def revoke(self, scope: AccessScope, client_id: uuid.UUID) -> None:
        """Disable the access without deleting the row.

        Deleting would lose the trace of who had entered, and the foreign key
        is RESTRICT precisely so accesses are not destroyed by accident. A
        later create reactivates the row with a fresh password.
        """
        self._require_write(scope)
        access = await self.get(scope, client_id)
        await self._users.update(access, {"is_active": False})
        logger.info("monitoring access revoked", extra={"client_id": str(client_id)})
