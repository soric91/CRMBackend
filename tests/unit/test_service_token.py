"""Two rules of the service token that the HTTP surface cannot reach.

An expiry in the past is refused when it is set, so the only way to observe a
credential going stale is to look at the check itself. And a token whose
subject is not an identifier at all never comes from this API — it comes from
somebody trying things.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings
from app.core.exceptions import AuthenticationError
from app.core.security import TokenAudience, TokenType, create_token
from app.models import ServiceAccount
from app.repositories.service_account import ServiceAccountRepository
from app.services.service_accounts import ServiceTokenService


def _account(**overrides: object) -> ServiceAccount:
    fields: dict[str, object] = {
        "nombre": "ApiEMS",
        "credencial_id": "svc_test",
        "secret_hash": "not-a-real-hash",
        "secret_emitido_en": datetime.now(UTC),
        "permisos": ["fleet:read"],
        "activo": True,
        **overrides,
    }
    return ServiceAccount(**fields)


class TestWhetherACredentialIsStillUsable:
    def test_an_active_one_with_no_deadline_is(self) -> None:
        assert ServiceTokenService._usable(_account()) is True

    def test_a_deactivated_one_is_not(self) -> None:
        assert ServiceTokenService._usable(_account(activo=False)) is False

    def test_a_future_deadline_is_fine(self) -> None:
        account = _account(expira_en=datetime.now(UTC) + timedelta(days=1))

        assert ServiceTokenService._usable(account) is True

    def test_a_past_deadline_is_not(self) -> None:
        account = _account(expira_en=datetime.now(UTC) - timedelta(seconds=1))

        assert ServiceTokenService._usable(account) is False

    def test_a_naive_deadline_is_read_as_utc(self) -> None:
        """SQLite hands datetimes back without a timezone.

        Comparing one of those against an aware value raises TypeError, which
        would surface as a 500 on the token endpoint rather than a refusal.
        """
        naive_future = (datetime.now(UTC) + timedelta(days=1)).replace(tzinfo=None)
        account = _account(expira_en=naive_future)

        assert ServiceTokenService._usable(account) is True

    def test_a_naive_deadline_in_the_past_still_expires(self) -> None:
        naive_past = (datetime.now(UTC) - timedelta(days=1)).replace(tzinfo=None)
        account = _account(expira_en=naive_past)

        assert ServiceTokenService._usable(account) is False

    def test_deactivation_beats_a_valid_deadline(self) -> None:
        account = _account(
            activo=False, expira_en=datetime.now(UTC) + timedelta(days=365)
        )

        assert ServiceTokenService._usable(account) is False


class TestAForgedSubject:
    async def test_a_token_whose_subject_is_not_an_id_is_refused(
        self, settings: Settings
    ) -> None:
        """Signed by us, so it decodes — and is still nonsense."""
        token = create_token(
            settings,
            subject="no-soy-un-uuid",
            token_type=TokenType.ACCESS,
            audience=TokenAudience.SERVICE,
            expires_in=timedelta(minutes=5),
            claims={"permisos": ["fleet:read"], "client_id": None},
        )
        service = ServiceTokenService(
            ServiceAccountRepository(None),  # pyright: ignore[reportArgumentType]
            settings,
        )

        with pytest.raises(AuthenticationError):
            await service.scope_from_token(token)

    async def test_a_token_from_another_surface_is_refused(
        self, settings: Settings
    ) -> None:
        """A CRM token is not a service token, however valid it is elsewhere."""
        token = create_token(
            settings,
            subject="00000000-0000-4000-8000-000000000000",
            token_type=TokenType.ACCESS,
            audience=TokenAudience.CRM,
            expires_in=timedelta(minutes=5),
        )
        service = ServiceTokenService(
            ServiceAccountRepository(None),  # pyright: ignore[reportArgumentType]
            settings,
        )

        with pytest.raises(AuthenticationError):
            await service.scope_from_token(token)


class TestTheLifetimeIsConfigured:
    def test_it_is_reported_in_seconds(self, settings: Settings) -> None:
        service = ServiceTokenService(
            ServiceAccountRepository(None),  # pyright: ignore[reportArgumentType]
            settings.model_copy(update={"service_token_expire_minutes": 15}),
        )

        assert service.token_lifetime_seconds == 900
