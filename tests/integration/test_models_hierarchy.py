"""The Client -> Site -> Gateway -> Equipment -> Variable chain."""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import (
    ClientStatus,
    GatewayStatus,
    ModbusRegisterType,
    ModbusTransport,
    SerialParity,
)
from app.models import Client, Equipment, Gateway, Site, Variable
from tests.factories import (
    make_client,
    make_equipment,
    make_gateway,
    make_site,
    make_variable,
)


async def _full_chain(session: AsyncSession) -> tuple[Client, Site, Gateway, Equipment]:
    client = make_client()
    session.add(client)
    await session.flush()
    site = make_site(client)
    session.add(site)
    await session.flush()
    gateway = make_gateway(site)
    session.add(gateway)
    await session.flush()
    equipment = make_equipment(gateway)
    session.add(equipment)
    await session.flush()
    return client, site, gateway, equipment


async def _count(session: AsyncSession, model: type) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


class TestDefaults:
    async def test_client_starts_as_prospecto_with_an_id_and_dates(
        self, db_session: AsyncSession
    ) -> None:
        client = make_client()
        db_session.add(client)
        await db_session.flush()

        assert isinstance(client.id, uuid.UUID)
        assert client.estado is ClientStatus.PROSPECTO
        assert client.fecha_alta is not None
        assert client.created_at is not None

    async def test_gateway_starts_offline_with_its_own_uuid(
        self, db_session: AsyncSession
    ) -> None:
        _, _, gateway, _ = await _full_chain(db_session)

        assert gateway.estado is GatewayStatus.OFFLINE
        assert isinstance(gateway.uuid, uuid.UUID)
        assert gateway.uuid != gateway.id

    async def test_equipment_gets_sane_modbus_defaults(
        self, db_session: AsyncSession
    ) -> None:
        _, _, _, equipment = await _full_chain(db_session)

        assert equipment.baudrate == 9600
        assert equipment.bits == 8
        assert equipment.stop_bits == 1
        assert equipment.paridad is SerialParity.NONE
        assert equipment.transporte is ModbusTransport.RTU
        assert equipment.modbusconnect is True
        assert equipment.blockreading is True

    async def test_variable_defaults_to_scale_one_every_minute(
        self, db_session: AsyncSession
    ) -> None:
        _, _, _, equipment = await _full_chain(db_session)
        db_session.add(make_variable(equipment, nombre="A_phsA"))
        await db_session.flush()

        stored = (
            await db_session.execute(
                select(Variable).where(Variable.nombre == "A_phsA")
            )
        ).scalar_one()
        assert stored.escala == 1
        assert stored.tipo_registro is ModbusRegisterType.HOLDING


class TestUniqueness:
    async def test_gateway_serial_is_unique_platform_wide(
        self, db_session: AsyncSession
    ) -> None:
        _, site, _, _ = await _full_chain(db_session)
        db_session.add(make_gateway(site, numero_serie="GW-0001"))

        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_two_sites_of_one_client_cannot_share_a_name(
        self, db_session: AsyncSession
    ) -> None:
        client, _, _, _ = await _full_chain(db_session)
        db_session.add(make_site(client, nombre="Planta Norte"))

        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_different_clients_may_reuse_a_site_name(
        self, db_session: AsyncSession
    ) -> None:
        await _full_chain(db_session)
        other = make_client(nombre_empresa="Otra Empresa")
        db_session.add(other)
        await db_session.flush()
        db_session.add(make_site(other, nombre="Planta Norte"))

        await db_session.flush()  # must not raise

    async def test_same_modbus_id_on_one_port_is_rejected(
        self, db_session: AsyncSession
    ) -> None:
        _, _, gateway, _ = await _full_chain(db_session)
        db_session.add(make_equipment(gateway, modbus_id=1, puerto="/dev/ttymxc1"))

        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_same_modbus_id_on_another_port_is_allowed(
        self, db_session: AsyncSession
    ) -> None:
        _, _, gateway, _ = await _full_chain(db_session)
        db_session.add(make_equipment(gateway, modbus_id=1, puerto="/dev/ttymxc2"))

        await db_session.flush()  # must not raise

    async def test_a_variable_name_is_unique_per_equipment(
        self, db_session: AsyncSession
    ) -> None:
        _, _, _, equipment = await _full_chain(db_session)
        db_session.add(make_variable(equipment))
        await db_session.flush()
        db_session.add(make_variable(equipment, registro_modbus=200))

        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestCheckConstraints:
    @pytest.mark.parametrize("modbus_id", [0, 248, -1])
    async def test_modbus_id_outside_the_rtu_range_is_rejected(
        self, db_session: AsyncSession, modbus_id: int
    ) -> None:
        _, _, gateway, _ = await _full_chain(db_session)
        db_session.add(make_equipment(gateway, modbus_id=modbus_id, puerto="/dev/x"))

        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_a_valid_modbus_id_is_accepted(
        self, db_session: AsyncSession
    ) -> None:
        _, _, gateway, _ = await _full_chain(db_session)
        db_session.add(make_equipment(gateway, modbus_id=247, puerto="/dev/x"))

        await db_session.flush()  # must not raise

    @pytest.mark.parametrize(("field", "value"), [("bits", 9), ("stop_bits", 3)])
    async def test_invalid_serial_framing_is_rejected(
        self, db_session: AsyncSession, field: str, value: int
    ) -> None:
        _, _, gateway, _ = await _full_chain(db_session)
        db_session.add(
            make_equipment(gateway, modbus_id=5, puerto="/dev/x", **{field: value})
        )

        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_zero_read_frequency_is_rejected(
        self, db_session: AsyncSession
    ) -> None:
        """The cadence lives on the gateway: one poll walks the whole bus."""
        _, _, gateway, _ = await _full_chain(db_session)
        gateway.intervalo_lectura_segundos = 0

        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_a_zero_scale_is_rejected(self, db_session: AsyncSession) -> None:
        _, _, _, equipment = await _full_chain(db_session)
        db_session.add(make_variable(equipment, escala=0))

        with pytest.raises(IntegrityError):
            await db_session.flush()

    @pytest.mark.parametrize(
        ("latitud", "longitud"), [(91, 0), (-91, 0), (0, 181), (0, -181)]
    )
    async def test_coordinates_outside_the_globe_are_rejected(
        self, db_session: AsyncSession, latitud: int, longitud: int
    ) -> None:
        client, *_ = await _full_chain(db_session)
        db_session.add(
            make_site(client, nombre="A_phsB", latitud=latitud, longitud=longitud)
        )

        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_valid_coordinates_are_accepted(
        self, db_session: AsyncSession
    ) -> None:
        client, _, _, _ = await _full_chain(db_session)
        db_session.add(
            make_site(
                client, nombre="A_phsB", latitud="4.710989", longitud="-74.072092"
            )
        )

        await db_session.flush()  # must not raise


class TestForeignKeys:
    async def test_a_site_cannot_point_at_a_missing_client(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add(Site(client_id=uuid.uuid4(), nombre="A_phsC"))

        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_a_gateway_cannot_point_at_a_missing_site(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add(Gateway(site_id=uuid.uuid4(), numero_serie="GW-9999"))

        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestCascades:
    async def test_deleting_a_client_removes_the_whole_chain(
        self, db_session: AsyncSession
    ) -> None:
        client, _, _, equipment = await _full_chain(db_session)
        db_session.add(make_variable(equipment))
        await db_session.commit()

        await db_session.delete(client)
        await db_session.commit()

        for model in (Client, Site, Gateway, Equipment, Variable):
            assert await _count(db_session, model) == 0

    async def test_deleting_a_gateway_keeps_its_client_and_site(
        self, db_session: AsyncSession
    ) -> None:
        _, _, gateway, equipment = await _full_chain(db_session)
        db_session.add(make_variable(equipment))
        await db_session.commit()

        await db_session.delete(gateway)
        await db_session.commit()

        assert await _count(db_session, Client) == 1
        assert await _count(db_session, Site) == 1
        assert await _count(db_session, Gateway) == 0
        assert await _count(db_session, Equipment) == 0
        assert await _count(db_session, Variable) == 0


class TestLazyLoading:
    async def test_relationships_refuse_to_load_implicitly(
        self, db_session: AsyncSession
    ) -> None:
        """A silent lazy load under asyncio fails as MissingGreenlet later on."""
        await _full_chain(db_session)
        await db_session.commit()
        db_session.expire_all()

        reloaded = (await db_session.execute(select(Client))).scalar_one()
        with pytest.raises(Exception, match=r"(?i)lazy load|not available"):
            _ = reloaded.sites
