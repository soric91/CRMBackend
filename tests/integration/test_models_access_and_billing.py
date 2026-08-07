"""Users, tariffs and alert rules."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import (
    AlertType,
    NotificationChannel,
    UserRole,
)
from app.models import AlertConfig, Client, Tariff, User
from tests.factories import (
    make_alert_config,
    make_client,
    make_gateway,
    make_site,
    make_tariff,
    make_user,
)


async def _client(session: AsyncSession) -> Client:
    client = make_client()
    session.add(client)
    await session.flush()
    return client


class TestUserRoles:
    async def test_an_internal_admin_needs_no_client(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add(make_user(role=UserRole.ADMIN))

        await db_session.flush()  # must not raise

    async def test_a_tecnico_needs_no_client(self, db_session: AsyncSession) -> None:
        db_session.add(make_user(role=UserRole.TECNICO, email="tec@example.com"))

        await db_session.flush()  # must not raise

    async def test_a_cliente_without_a_client_is_rejected(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add(
            make_user(
                role=UserRole.CLIENTE, email="cliente@example.com", client_id=None
            )
        )

        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_a_cliente_bound_to_a_client_is_accepted(
        self, db_session: AsyncSession
    ) -> None:
        client = await _client(db_session)
        db_session.add(
            make_user(
                role=UserRole.CLIENTE, email="cliente@example.com", client_id=client.id
            )
        )

        await db_session.flush()  # must not raise

    async def test_email_is_unique(self, db_session: AsyncSession) -> None:
        db_session.add(make_user())
        await db_session.flush()
        db_session.add(make_user(role=UserRole.TECNICO))

        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_an_unknown_role_is_rejected(self, db_session: AsyncSession) -> None:
        db_session.add(make_user(role="superadmin"))

        with pytest.raises((IntegrityError, StatementError, LookupError)):
            await db_session.flush()

    async def test_a_user_is_active_by_default(self, db_session: AsyncSession) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()

        assert user.is_active is True


class TestUserDeletionPolicy:
    async def test_deleting_a_client_with_logins_is_blocked(
        self, db_session: AsyncSession
    ) -> None:
        """RESTRICT: accounts must be dealt with explicitly, never vanish."""
        client = await _client(db_session)
        db_session.add(
            make_user(
                role=UserRole.CLIENTE, email="cliente@example.com", client_id=client.id
            )
        )
        await db_session.commit()

        await db_session.delete(client)
        with pytest.raises(IntegrityError):
            await db_session.commit()

    async def test_deleting_a_client_without_logins_works(
        self, db_session: AsyncSession
    ) -> None:
        client = await _client(db_session)
        await db_session.commit()

        await db_session.delete(client)
        await db_session.commit()

        count = (
            await db_session.execute(select(func.count()).select_from(Client))
        ).scalar_one()
        assert count == 0


class TestTariffs:
    async def test_money_keeps_its_decimal_scale(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add(make_tariff(valor_importado=Decimal("780.1234")))
        await db_session.commit()
        db_session.expire_all()

        stored = (await db_session.execute(select(Tariff))).scalar_one()
        assert isinstance(stored.valor_importado, Decimal)
        assert stored.valor_importado == Decimal("780.1234")
        assert stored.valor_excedente == Decimal("310.0000")

    async def test_the_month_is_unique_platform_wide(
        self, db_session: AsyncSession
    ) -> None:
        """One price per month: a second row for the same month is a mistake."""
        db_session.add(make_tariff(mes=date(2026, 5, 1)))
        await db_session.flush()
        db_session.add(
            make_tariff(mes=date(2026, 5, 1), valor_importado=Decimal("900"))
        )

        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_the_same_month_of_another_year_is_a_different_row(
        self, db_session: AsyncSession
    ) -> None:
        """Without the year, enero 2026 and enero 2027 would collide."""
        db_session.add(make_tariff(mes=date(2026, 1, 1)))
        db_session.add(make_tariff(mes=date(2027, 1, 1)))

        await db_session.flush()  # must not raise

    async def test_a_date_that_is_not_the_first_of_the_month_is_rejected(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add(make_tariff(mes=date(2026, 5, 17)))

        with pytest.raises(IntegrityError):
            await db_session.flush()

    @pytest.mark.parametrize("field", ["valor_importado", "valor_excedente"])
    async def test_a_negative_price_is_rejected(
        self, db_session: AsyncSession, field: str
    ) -> None:
        db_session.add(make_tariff(**{field: Decimal("-1")}))

        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_a_zero_surplus_price_is_allowed(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add(make_tariff(valor_excedente=Decimal("0")))

        await db_session.flush()  # must not raise

    async def test_successive_months_are_kept_as_separate_rows(
        self, db_session: AsyncSession
    ) -> None:
        """History must stay reproducible: a new price never overwrites the old."""
        for month in (1, 5, 6):
            db_session.add(make_tariff(mes=date(2026, month, 1)))
        await db_session.flush()

        count = (
            await db_session.execute(select(func.count()).select_from(Tariff))
        ).scalar_one()
        assert count == 3

    async def test_the_period_reads_as_a_spanish_month_and_year(
        self, db_session: AsyncSession
    ) -> None:
        tariff = make_tariff(mes=date(2026, 6, 1))
        db_session.add(tariff)
        await db_session.flush()

        assert tariff.periodo == "junio 2026"

    async def test_tariffs_do_not_belong_to_a_client(self) -> None:
        """A single platform-wide price: no tenant column, no foreign key."""
        assert "client_id" not in Tariff.__table__.columns
        assert not list(Tariff.__table__.foreign_keys)


class TestAlertConfig:
    async def test_a_global_rule_needs_no_gateway(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add(make_alert_config(gateway_id=None))
        await db_session.flush()

        stored = (await db_session.execute(select(AlertConfig))).scalar_one()
        assert stored.gateway_id is None
        assert stored.canal_notif is NotificationChannel.EMAIL
        assert stored.activo is True

    async def test_a_rule_can_be_scoped_to_one_gateway(
        self, db_session: AsyncSession
    ) -> None:
        client = await _client(db_session)
        site = make_site(client)
        db_session.add(site)
        await db_session.flush()
        gateway = make_gateway(site)
        db_session.add(gateway)
        await db_session.flush()
        db_session.add(
            make_alert_config(
                gateway_id=gateway.id,
                tipo=AlertType.VOLTAJE_FUERA_RANGO,
                umbral=Decimal("253.0"),
                canal_notif=NotificationChannel.TELEGRAM,
            )
        )

        await db_session.flush()  # must not raise

    async def test_a_rule_cannot_point_at_a_missing_gateway(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add(make_alert_config(gateway_id=uuid.uuid4()))

        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_an_unknown_channel_is_rejected(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add(make_alert_config(canal_notif="paloma_mensajera"))

        with pytest.raises((IntegrityError, StatementError, LookupError)):
            await db_session.flush()

    async def test_deleting_a_gateway_removes_its_rules(
        self, db_session: AsyncSession
    ) -> None:
        client = await _client(db_session)
        site = make_site(client)
        db_session.add(site)
        await db_session.flush()
        gateway = make_gateway(site)
        db_session.add(gateway)
        await db_session.flush()
        db_session.add(make_alert_config(gateway_id=gateway.id))
        await db_session.commit()

        await db_session.delete(gateway)
        await db_session.commit()

        count = (
            await db_session.execute(select(func.count()).select_from(AlertConfig))
        ).scalar_one()
        assert count == 0


class TestEnumStorage:
    async def test_enums_are_stored_by_value_not_by_member_name(
        self, db_session: AsyncSession
    ) -> None:
        """The firmware and ApiEMS read these strings; they must be lowercase."""
        db_session.add(make_user(role=UserRole.SOLO_LECTURA))
        await db_session.commit()

        raw = (await db_session.execute(select(User.__table__.c.role))).scalar_one()
        assert raw == "solo_lectura"
