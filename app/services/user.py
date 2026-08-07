"""Account management use cases.

Four invariants are enforced here, so no endpoint can bypass one:

* only an `admin` touches accounts — a `tecnico` who could create users would
  be able to mint an admin and promote itself;
* the `cliente` role always carries a client, and the other roles never do;
* the platform never runs out of usable administrators;
* nobody locks themselves out.
"""

import uuid
from typing import Any

from app.core.exceptions import (
    AlreadyExistsError,
    AuthenticationError,
    AuthorizationError,
    BusinessRuleError,
    NotFoundError,
)
from app.core.security import hash_password, verify_password
from app.domain.access import AccessScope
from app.domain.enums import UserRole
from app.models import User
from app.repositories.hierarchy import ClientRepository
from app.repositories.user import UserRepository, normalize_email


class UserService:
    """Creates, lists and modifies panel accounts."""

    def __init__(self, users: UserRepository, clients: ClientRepository) -> None:
        self._users = users
        self._clients = clients

    # --- guards ---------------------------------------------------------

    @staticmethod
    def _require_admin(scope: AccessScope) -> None:
        if not scope.can_manage_users:
            raise AuthorizationError(
                f"Role '{scope.principal}' cannot manage user accounts"
            )

    async def _validate_client_binding(
        self, role: UserRole, client_id: uuid.UUID | None
    ) -> None:
        """A `cliente` needs a client; staff and read-only accounts must not."""
        if role is UserRole.CLIENTE:
            if client_id is None:
                raise BusinessRuleError("Role 'cliente' requires a client_id")
            if await self._clients.get(client_id) is None:
                raise NotFoundError(f"Client {client_id} not found")
        elif client_id is not None:
            raise BusinessRuleError(
                f"Role '{role.value}' must not be bound to a client"
            )

    async def _refuse_to_remove_the_last_admin(self, user: User) -> None:
        """Stop a change that would leave nobody able to administer anything."""
        if user.role is not UserRole.ADMIN or not user.is_active:
            return
        if await self._users.count_active_admins(excluding=user.id) == 0:
            raise BusinessRuleError(
                "This is the last active administrator; promote another one first"
            )

    # --- use cases ------------------------------------------------------

    async def list(
        self,
        scope: AccessScope,
        *,
        limit: int,
        offset: int,
        client_id: uuid.UUID | None = None,
        role: UserRole | None = None,
    ) -> tuple[list[User], int]:
        self._require_admin(scope)
        return await self._users.list_page(
            limit=limit, offset=offset, client_id=client_id, role=role
        )

    async def get(self, scope: AccessScope, user_id: uuid.UUID) -> User:
        self._require_admin(scope)
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError(f"User {user_id} not found")
        return user

    async def create(self, scope: AccessScope, data: dict[str, Any]) -> User:
        self._require_admin(scope)

        email = normalize_email(data["email"])
        if await self._users.exists_with_email(email):
            raise AlreadyExistsError(f"A user with email '{email}' already exists")

        role: UserRole = data["role"]
        client_id: uuid.UUID | None = data.get("client_id")
        await self._validate_client_binding(role, client_id)

        return await self._users.add(
            User(
                email=email,
                password_hash=hash_password(data["password"]),
                role=role,
                client_id=client_id,
                is_active=True,
                # Somebody else chose this password, so the account has to
                # replace it before the token it gets reaches anything.
                must_change_password=True,
            )
        )

    async def update(
        self, scope: AccessScope, user_id: uuid.UUID, changes: dict[str, Any]
    ) -> User:
        self._require_admin(scope)
        user = await self.get(scope, user_id)

        # Role and client are validated together against the state the row
        # will end up in: changing one without the other would leave the pair
        # inconsistent. Promoting a `cliente` to staff drops its client.
        role: UserRole = changes.get("role", user.role)
        if "client_id" in changes:
            client_id = changes["client_id"]
        elif role is UserRole.CLIENTE:
            client_id = user.client_id
        else:
            client_id = None
        await self._validate_client_binding(role, client_id)
        changes = {**changes, "role": role, "client_id": client_id}

        losing_admin = role is not UserRole.ADMIN or changes.get("is_active") is False
        if losing_admin:
            await self._refuse_to_remove_the_last_admin(user)
        if scope.is_self(user_id):
            self._refuse_self_lockout(user, changes)

        return await self._users.update(user, changes)

    @staticmethod
    def _refuse_self_lockout(user: User, changes: dict[str, Any]) -> None:
        """An admin editing itself cannot drop its own access."""
        if changes.get("is_active") is False:
            raise BusinessRuleError("You cannot deactivate your own account")
        if changes.get("role", user.role) is not user.role:
            raise BusinessRuleError("You cannot change your own role")

    async def set_password(
        self, scope: AccessScope, user_id: uuid.UUID, new_password: str
    ) -> User:
        """Replace someone's password. Used when a user has lost access."""
        self._require_admin(scope)
        user = await self.get(scope, user_id)
        return await self._users.update(
            user,
            {
                "password_hash": hash_password(new_password),
                "must_change_password": True,
            },
        )

    async def change_own_password(
        self, scope: AccessScope, current_password: str, new_password: str
    ) -> None:
        """Let the caller replace their own password.

        The current one is required: a stolen access token alone must not be
        enough to take an account over permanently.
        """
        if scope.user_id is None:
            raise AuthenticationError("Invalid token")
        user = await self._users.get_by_id(scope.user_id)
        if user is None:
            raise AuthenticationError("Invalid token")
        if not verify_password(current_password, user.password_hash):
            raise AuthenticationError("Current password is incorrect")
        # Chosen by the account owner, so the mandatory change is satisfied.
        await self._users.update(
            user,
            {
                "password_hash": hash_password(new_password),
                "must_change_password": False,
            },
        )

    async def delete(self, scope: AccessScope, user_id: uuid.UUID) -> None:
        self._require_admin(scope)
        user = await self.get(scope, user_id)
        if scope.is_self(user_id):
            raise BusinessRuleError("You cannot delete your own account")
        await self._refuse_to_remove_the_last_admin(user)
        await self._users.delete(user)
