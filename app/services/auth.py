"""Authentication use cases."""

import uuid
from collections.abc import Sequence

from app.core.config import Settings
from app.core.exceptions import AuthenticationError
from app.core.logging import get_logger
from app.core.security import (
    TokenAudience,
    TokenScope,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
    waste_time_like_a_real_verification,
)
from app.domain.enums import UserRole
from app.models import User
from app.repositories.user import UserRepository
from app.schemas.auth import TokenPair

logger = get_logger(__name__)


class AuthService:
    """Turns credentials into tokens, and tokens back into a user."""

    def __init__(
        self,
        users: UserRepository,
        settings: Settings,
        audience: TokenAudience = TokenAudience.CRM,
        accepted_audiences: Sequence[TokenAudience] | None = None,
    ) -> None:
        self._users = users
        self._settings = settings
        # Which surface this instance mints tokens for.
        self._audience = audience
        # Which surfaces it accepts on the way in. They differ on the data
        # endpoints, which both webs read through the same routes, while each
        # authentication surface only honours its own tokens.
        self._accepted = tuple(accepted_audiences or (audience,))

    async def authenticate(self, email: str, password: str) -> User:
        """Return the user behind valid credentials.

        Every failure raises the same error with the same message: telling a
        caller whether the address exists, or whether the account is disabled,
        hands an attacker a list of valid targets.
        """
        user = await self._users.get_by_email(email)
        if user is None:
            # Spend the time a real bcrypt check would have taken, so response
            # timing does not reveal that the address is unknown.
            waste_time_like_a_real_verification()
            logger.info("login failed", extra={"reason": "unknown_email"})
            raise AuthenticationError("Incorrect email or password")

        if not verify_password(password, user.password_hash):
            logger.info(
                "login failed",
                extra={"reason": "bad_password", "user_id": str(user.id)},
            )
            raise AuthenticationError("Incorrect email or password")

        if not user.is_active:
            logger.info(
                "login failed",
                extra={"reason": "inactive", "user_id": str(user.id)},
            )
            raise AuthenticationError("Incorrect email or password")

        logger.info("login succeeded", extra={"user_id": str(user.id)})
        return user

    def issue_tokens(self, user: User) -> TokenPair:
        """Return a fresh access/refresh pair for ``user``.

        The scope is read from the account every time, never carried over from
        an older token, so a pending password change survives a refresh.
        """
        subject = str(user.id)
        scope = (
            TokenScope.PASSWORD_CHANGE if user.must_change_password else TokenScope.FULL
        )
        claims = {
            "role": user.role.value,
            "client_id": str(user.client_id) if user.client_id else None,
            "scope": scope.value,
        }
        return TokenPair(
            access_token=create_access_token(
                self._settings,
                subject=subject,
                audience=self._audience,
                claims=claims,
            ),
            refresh_token=create_refresh_token(
                self._settings, subject=subject, audience=self._audience
            ),
            expires_in=self._settings.access_token_expire_minutes * 60,
        )

    async def login(self, email: str, password: str) -> TokenPair:
        """Log a member of the administration in to the CRM.

        The `cliente` role is refused with the same generic failure as a wrong
        password: clients belong to the monitoring web, and saying that the
        account exists but is of another kind would leak which addresses are
        registered.
        """
        user = await self.authenticate(email, password)
        if user.role is UserRole.CLIENTE:
            logger.info(
                "crm login refused",
                extra={"reason": "client_role", "user_id": str(user.id)},
            )
            raise AuthenticationError("Incorrect email or password")
        return self.issue_tokens(user)

    async def refresh(self, refresh_token: str) -> TokenPair:
        """Exchange a refresh token for a new pair.

        The user is re-read from the database instead of trusting the token's
        claims, so a deactivated or demoted account stops working immediately
        rather than when the refresh token happens to expire.
        """
        return self.issue_tokens(await self.user_from_refresh_token(refresh_token))

    async def user_from_refresh_token(self, refresh_token: str) -> User:
        """Return the account a refresh token stands for, re-read from the base."""
        payload = decode_token(
            self._settings,
            refresh_token,
            expected_type=TokenType.REFRESH,
            expected_audience=self._accepted,
        )
        return await self._load_active_user(payload["sub"])

    async def resolve_access_token(self, token: str) -> tuple[User, TokenScope]:
        """Return the user an access token stands for, and what it may reach."""
        payload = decode_token(
            self._settings,
            token,
            expected_type=TokenType.ACCESS,
            expected_audience=self._accepted,
        )
        user = await self._load_active_user(payload["sub"])
        try:
            scope = TokenScope(payload.get("scope", TokenScope.FULL.value))
        except ValueError as exc:
            raise AuthenticationError("Invalid token") from exc
        return user, scope

    async def _load_active_user(self, subject: str) -> User:
        try:
            user_id = uuid.UUID(subject)
        except ValueError as exc:
            raise AuthenticationError("Invalid token") from exc

        user = await self._users.get_by_id(user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("Invalid token")
        return user
