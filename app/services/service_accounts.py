"""Machine-to-machine credentials: issuing them, and turning them into tokens.

Two services, deliberately apart. One is reached by an administrator through
the panel and can create, rotate and revoke; the other is reached by the
consumer itself and can only exchange a credential it already holds. Nothing
on the second path can widen what the first one granted.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import Settings
from app.core.exceptions import (
    AlreadyExistsError,
    AuthenticationError,
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.security import (
    TokenAudience,
    TokenType,
    create_token,
    decode_token,
    hash_password,
    verify_password,
    waste_time_like_a_real_verification,
)
from app.domain.access import AccessScope
from app.domain.enums import ServicePermission, UserRole
from app.domain.passwords import generate_service_identifier, generate_service_secret
from app.models import ServiceAccount
from app.repositories.hierarchy import ClientRepository
from app.repositories.service_account import ServiceAccountRepository

logger = get_logger(__name__)


def _require_admin(scope: AccessScope) -> None:
    if not scope.can_manage_services:
        raise AuthorizationError(
            f"Role '{scope.principal}' cannot manage service credentials"
        )


class ServiceAccountService:
    """The panel's side: who exists, what they may read, and rotation."""

    def __init__(
        self, accounts: ServiceAccountRepository, clients: ClientRepository
    ) -> None:
        self._accounts = accounts
        self._clients = clients

    async def list(
        self, scope: AccessScope, *, limit: int, offset: int
    ) -> tuple[list[ServiceAccount], int]:
        _require_admin(scope)
        return await self._accounts.list_page(limit=limit, offset=offset)

    async def get(self, scope: AccessScope, account_id: uuid.UUID) -> ServiceAccount:
        _require_admin(scope)
        account = await self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(f"Service account {account_id} not found")
        return account

    async def _validate_client(self, client_id: uuid.UUID | None) -> None:
        if client_id is not None and await self._clients.get(client_id) is None:
            raise ValidationError(f"Client {client_id} does not exist")

    @staticmethod
    def _validate_expiry(expira_en: datetime | None) -> None:
        """A credential that is already expired can never mint a token."""
        if expira_en is not None and expira_en <= datetime.now(UTC):
            raise ValidationError("expira_en is already in the past")

    async def create(
        self, scope: AccessScope, data: dict[str, Any]
    ) -> tuple[ServiceAccount, str]:
        """Create an account and return it with its one-off secret."""
        _require_admin(scope)
        if await self._accounts.name_taken(data["nombre"]):
            raise AlreadyExistsError(
                f"A service account named '{data['nombre']}' already exists"
            )
        await self._validate_client(data.get("client_id"))
        self._validate_expiry(data.get("expira_en"))

        secret = generate_service_secret()
        account = await self._accounts.add(
            ServiceAccount(
                nombre=data["nombre"],
                descripcion=data.get("descripcion"),
                credencial_id=generate_service_identifier(),
                secret_hash=hash_password(secret),
                secret_emitido_en=datetime.now(UTC),
                permisos=[ServicePermission(item).value for item in data["permisos"]],
                client_id=data.get("client_id"),
                expira_en=data.get("expira_en"),
            )
        )
        logger.info(
            "service account created",
            extra={
                "service_id": str(account.id),
                "service_name": account.nombre,
                "permisos": account.permisos,
            },
        )
        return account, secret

    async def update(
        self, scope: AccessScope, account_id: uuid.UUID, changes: dict[str, Any]
    ) -> ServiceAccount:
        _require_admin(scope)
        account = await self.get(scope, account_id)

        new_name = changes.get("nombre")
        if new_name is not None and await self._accounts.name_taken(
            new_name, excluding=account_id
        ):
            raise AlreadyExistsError(
                f"A service account named '{new_name}' already exists"
            )
        if "expira_en" in changes:
            self._validate_expiry(changes["expira_en"])
        if "permisos" in changes:
            changes = {
                **changes,
                "permisos": [
                    ServicePermission(item).value for item in changes["permisos"]
                ],
            }

        updated = await self._accounts.update(account, changes)
        logger.info(
            "service account updated",
            extra={"service_id": str(account_id), "fields": sorted(changes)},
        )
        return updated

    async def rotate_secret(
        self, scope: AccessScope, account_id: uuid.UUID
    ) -> tuple[ServiceAccount, str]:
        """Replace the secret, invalidating the previous one immediately.

        The consumer stops being able to obtain new tokens until the new
        secret is deployed to it. That is the point: it is how a leaked
        credential is taken back.
        """
        _require_admin(scope)
        account = await self.get(scope, account_id)
        secret = generate_service_secret()
        updated = await self._accounts.update(
            account,
            {
                "secret_hash": hash_password(secret),
                "secret_emitido_en": datetime.now(UTC),
            },
        )
        logger.info("service secret rotated", extra={"service_id": str(account_id)})
        return updated, secret

    async def delete(self, scope: AccessScope, account_id: uuid.UUID) -> None:
        """Remove the account. Tokens already minted die with their expiry."""
        _require_admin(scope)
        account = await self.get(scope, account_id)
        await self._accounts.delete(account)
        logger.info("service account deleted", extra={"service_id": str(account_id)})


class ServiceTokenService:
    """The consumer's side: a credential in, a short-lived token out."""

    def __init__(self, accounts: ServiceAccountRepository, settings: Settings) -> None:
        self._accounts = accounts
        self._settings = settings

    @property
    def token_lifetime_seconds(self) -> int:
        return self._settings.service_token_expire_minutes * 60

    async def issue_token(
        self, credencial_id: str, secret: str
    ) -> tuple[str, ServiceAccount]:
        """Return a token for a consumer that proved it holds the secret.

        Every failure answers identically — unknown identifier, wrong secret,
        deactivated, expired — and an unknown identifier still pays for a
        bcrypt verification, so response timing does not reveal which
        credentials exist.
        """
        account = await self._accounts.by_credential_id(credencial_id)
        if account is None:
            waste_time_like_a_real_verification()
            logger.info("service token refused", extra={"reason": "unknown"})
            raise AuthenticationError("Invalid service credential")

        if not verify_password(secret, account.secret_hash):
            logger.info(
                "service token refused",
                extra={"reason": "bad_secret", "service_id": str(account.id)},
            )
            raise AuthenticationError("Invalid service credential")

        if not self._usable(account):
            logger.info(
                "service token refused",
                extra={"reason": "revoked_or_expired", "service_id": str(account.id)},
            )
            raise AuthenticationError("Invalid service credential")

        await self._accounts.update(account, {"ultimo_uso_en": datetime.now(UTC)})
        logger.info(
            "service token issued",
            extra={"service_id": str(account.id), "permisos": account.permisos},
        )
        return self._mint(account), account

    @staticmethod
    def _usable(account: ServiceAccount) -> bool:
        if not account.activo:
            return False
        if account.expira_en is None:
            return True
        # SQLite hands back naive datetimes; treat them as the UTC they were
        # written as rather than comparing a naive against an aware value.
        deadline = account.expira_en
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        return deadline > datetime.now(UTC)

    def _mint(self, account: ServiceAccount) -> str:
        return create_token(
            self._settings,
            subject=str(account.id),
            token_type=TokenType.ACCESS,
            audience=TokenAudience.SERVICE,
            expires_in=timedelta(minutes=self._settings.service_token_expire_minutes),
            claims={
                "permisos": list(account.permisos),
                "client_id": (str(account.client_id) if account.client_id else None),
            },
        )

    async def scope_from_token(self, token: str) -> AccessScope:
        """Return what a service token stands for, re-read from the database.

        The permissions come from the row, not from the token: an account
        narrowed or revoked after a token was minted must lose that reach
        immediately, and the token alone is not proof it is still trusted.
        """
        payload = decode_token(
            self._settings,
            token,
            expected_type=TokenType.ACCESS,
            expected_audience=TokenAudience.SERVICE,
        )
        try:
            account_id = uuid.UUID(payload["sub"])
        except ValueError as exc:
            raise AuthenticationError("Invalid token") from exc

        account = await self._accounts.get(account_id)
        if account is None or not self._usable(account):
            raise AuthenticationError("Invalid token")
        return scope_for(account)


def scope_for(account: ServiceAccount) -> AccessScope:
    """Reduce a service account to what the services below need to know."""
    return AccessScope(
        # A floor, never a decision: every capability checks `is_service`
        # first. The least-privileged human role is used so that a rule added
        # later without thinking about services still denies rather than
        # allows.
        role=UserRole.SOLO_LECTURA,
        client_id=account.client_id,
        service_id=account.id,
        permissions=frozenset(ServicePermission(item) for item in account.permisos),
    )
