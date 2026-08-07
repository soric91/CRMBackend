"""Registering RTU and TCP devices through the API."""

import uuid
from collections.abc import Awaitable, Callable
from itertools import count

import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Gateway, User
from tests.conftest import auth_header

type Login = Callable[..., Awaitable[str]]

# Device names title a config section and are unique per gateway.
_names = count(1)


@pytest.fixture
async def admin_headers(admin_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(admin_user.email))


@pytest.fixture
async def gateway_id(client: AsyncClient, admin_headers: dict[str, str]) -> str:
    created = await client.post(
        "/api/v1/clients", json={"nombre_empresa": "Empresa"}, headers=admin_headers
    )
    site = await client.post(
        f"/api/v1/clients/{created.json()['id']}/sites",
        json={"nombre": "Planta"},
        headers=admin_headers,
    )
    gateway = await client.post(
        f"/api/v1/sites/{site.json()['id']}/gateways",
        json={"numero_serie": "GW-1"},
        headers=admin_headers,
    )
    gid: str = gateway.json()["id"]
    return gid


async def _create(
    client: AsyncClient, headers: dict[str, str], gateway_id: str, **payload: object
) -> tuple[int, dict[str, object]]:
    response = await client.post(
        f"/api/v1/gateways/{gateway_id}/equipment",
        json={
            "tipo": "analizador",
            "nombre_dispositivo": f"Modbus_{next(_names)}",
            "device_type": "CT_Meter",
            **payload,
        },
        headers=headers,
    )
    return response.status_code, response.json()


class TestRtuDevice:
    async def test_it_gets_the_serial_defaults(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        code, body = await _create(client, admin_headers, gateway_id, modbus_id=1)

        assert code == status.HTTP_201_CREATED
        assert body["transporte"] == "rtu"
        assert body["puerto"] == "/dev/ttymxc1"
        assert body["baudrate"] == 9600
        assert body["paridad"] == "N"
        assert body["host"] is None
        assert body["puerto_tcp"] is None

    async def test_rtu_is_the_default_transport(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        """Everything installed so far is serial; omitting it must not break."""
        _, body = await _create(client, admin_headers, gateway_id, modbus_id=1)

        assert body["transporte"] == "rtu"

    async def test_network_fields_are_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        code, _ = await _create(
            client, admin_headers, gateway_id, modbus_id=1, host="10.0.0.5"
        )

        assert code == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestTcpDevice:
    async def test_it_is_registered_with_host_and_port(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        code, body = await _create(
            client,
            admin_headers,
            gateway_id,
            modbus_id=11,
            transporte="tcp",
            host="10.0.0.5",
        )

        assert code == status.HTTP_201_CREATED
        assert body["host"] == "10.0.0.5"
        assert body["puerto_tcp"] == 502
        assert body["puerto"] is None
        assert body["baudrate"] is None

    async def test_a_missing_host_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        code, _ = await _create(
            client, admin_headers, gateway_id, modbus_id=11, transporte="tcp"
        )

        assert code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_serial_fields_are_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        code, _ = await _create(
            client,
            admin_headers,
            gateway_id,
            modbus_id=11,
            transporte="tcp",
            host="10.0.0.5",
            baudrate=9600,
        )

        assert code == status.HTTP_422_UNPROCESSABLE_CONTENT

    @pytest.mark.parametrize("puerto_tcp", [0, 65536, -1])
    async def test_a_port_outside_the_tcp_range_is_rejected(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        gateway_id: str,
        puerto_tcp: int,
    ) -> None:
        code, _ = await _create(
            client,
            admin_headers,
            gateway_id,
            modbus_id=11,
            transporte="tcp",
            host="10.0.0.5",
            puerto_tcp=puerto_tcp,
        )

        assert code == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestUniquenessPerTransport:
    async def test_rtu_and_tcp_may_share_a_unit_id(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        """They live on different buses; the id only collides within one."""
        await _create(client, admin_headers, gateway_id, modbus_id=11)

        code, _ = await _create(
            client,
            admin_headers,
            gateway_id,
            modbus_id=11,
            transporte="tcp",
            host="10.0.0.5",
        )

        assert code == status.HTTP_201_CREATED

    async def test_two_tcp_devices_at_the_same_endpoint_collide(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        await _create(
            client,
            admin_headers,
            gateway_id,
            modbus_id=11,
            transporte="tcp",
            host="10.0.0.5",
        )

        code, body = await _create(
            client,
            admin_headers,
            gateway_id,
            modbus_id=11,
            transporte="tcp",
            host="10.0.0.5",
        )

        assert code == status.HTTP_409_CONFLICT
        error = body["error"]
        assert isinstance(error, dict)
        assert "10.0.0.5:502" in str(error["message"])

    async def test_the_same_unit_id_on_another_host_is_allowed(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        await _create(
            client,
            admin_headers,
            gateway_id,
            modbus_id=11,
            transporte="tcp",
            host="10.0.0.5",
        )

        code, _ = await _create(
            client,
            admin_headers,
            gateway_id,
            modbus_id=11,
            transporte="tcp",
            host="10.0.0.6",
        )

        assert code == status.HTTP_201_CREATED

    async def test_the_same_unit_id_on_another_tcp_port_is_allowed(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        await _create(
            client,
            admin_headers,
            gateway_id,
            modbus_id=11,
            transporte="tcp",
            host="10.0.0.5",
        )

        code, _ = await _create(
            client,
            admin_headers,
            gateway_id,
            modbus_id=11,
            transporte="tcp",
            host="10.0.0.5",
            puerto_tcp=5020,
        )

        assert code == status.HTTP_201_CREATED

    async def test_the_conflict_message_names_the_serial_port(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        await _create(
            client, admin_headers, gateway_id, modbus_id=11, puerto="/dev/ttyRS485"
        )

        _, body = await _create(
            client, admin_headers, gateway_id, modbus_id=11, puerto="/dev/ttyRS485"
        )

        error = body["error"]
        assert isinstance(error, dict)
        assert "/dev/ttyRS485" in str(error["message"])


class TestSwitchingTransport:
    async def test_moving_to_tcp_clears_the_serial_fields(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        """A stale port must not survive on a device that now speaks TCP."""
        _, created = await _create(client, admin_headers, gateway_id, modbus_id=11)

        response = await client.patch(
            f"/api/v1/equipment/{created['id']}",
            json={"transporte": "tcp", "host": "10.0.0.5"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["host"] == "10.0.0.5"
        assert body["puerto_tcp"] == 502
        assert body["puerto"] is None
        assert body["baudrate"] is None
        assert body["paridad"] is None

    async def test_moving_to_tcp_without_a_host_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        _, created = await _create(client, admin_headers, gateway_id, modbus_id=11)

        response = await client.patch(
            f"/api/v1/equipment/{created['id']}",
            json={"transporte": "tcp"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_moving_back_to_rtu_restores_the_serial_defaults(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        _, created = await _create(
            client,
            admin_headers,
            gateway_id,
            modbus_id=11,
            transporte="tcp",
            host="10.0.0.5",
        )

        response = await client.patch(
            f"/api/v1/equipment/{created['id']}",
            json={"transporte": "rtu"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["puerto"] == "/dev/ttymxc1"
        assert body["baudrate"] == 9600
        assert body["host"] is None
        assert body["puerto_tcp"] is None

    async def test_patching_an_unrelated_field_leaves_the_transport_alone(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        _, created = await _create(
            client,
            admin_headers,
            gateway_id,
            modbus_id=11,
            transporte="tcp",
            host="10.0.0.5",
        )

        response = await client.patch(
            f"/api/v1/equipment/{created['id']}",
            json={"marca": "Chint"},
            headers=admin_headers,
        )

        body = response.json()
        assert body["transporte"] == "tcp"
        assert body["host"] == "10.0.0.5"
        assert body["marca"] == "Chint"


class TestPollRateOnTheGateway:
    async def test_equipment_no_longer_carries_a_cadence(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        """One poll walks the whole bus, so the cadence belongs to the gateway."""
        _, body = await _create(client, admin_headers, gateway_id, modbus_id=1)

        assert "frecuencia_lectura_segundos" not in body

    async def test_the_gateway_carries_it(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        response = await client.get(
            f"/api/v1/gateways/{gateway_id}", headers=admin_headers
        )

        assert response.json()["intervalo_lectura_segundos"] == 60
        assert response.json()["hora_inicio"] == 0
        assert response.json()["hora_fin"] == 23

    async def test_it_can_be_changed_on_the_gateway(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        response = await client.patch(
            f"/api/v1/gateways/{gateway_id}",
            json={"intervalo_lectura_segundos": 5, "hora_inicio": 6, "hora_fin": 20},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["intervalo_lectura_segundos"] == 5
        assert response.json()["hora_fin"] == 20

    async def test_a_zero_interval_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        response = await client.patch(
            f"/api/v1/gateways/{gateway_id}",
            json={"intervalo_lectura_segundos": 0},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_variables_no_longer_carry_a_cadence(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        _, equipment = await _create(client, admin_headers, gateway_id, modbus_id=1)

        response = await client.post(
            f"/api/v1/equipment/{equipment['id']}/variables",
            json={"nombre": "voltaje_l1", "registro_modbus": 8198},
            headers=admin_headers,
        )

        assert "frecuencia_lectura_segundos" not in response.json()


class TestRegisterTypeOnVariable:
    async def test_it_defaults_to_holding(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        _, equipment = await _create(client, admin_headers, gateway_id, modbus_id=1)

        response = await client.post(
            f"/api/v1/equipment/{equipment['id']}/variables",
            json={"nombre": "voltaje_l1", "registro_modbus": 2000},
            headers=admin_headers,
        )

        assert response.json()["tipo_registro"] == "holding"

    async def test_one_device_can_mix_address_spaces(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        """The reason it moved: measurements and relay states differ."""
        _, equipment = await _create(client, admin_headers, gateway_id, modbus_id=1)

        for nombre, registro, tipo in (
            ("voltaje_l1", 2000, "holding"),
            ("rele_1", 10, "coil"),
        ):
            response = await client.post(
                f"/api/v1/equipment/{equipment['id']}/variables",
                json={
                    "nombre": nombre,
                    "registro_modbus": registro,
                    "tipo_registro": tipo,
                },
                headers=admin_headers,
            )
            assert response.status_code == status.HTTP_201_CREATED, nombre
            assert response.json()["tipo_registro"] == tipo

    async def test_equipment_no_longer_exposes_it(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        _, body = await _create(client, admin_headers, gateway_id, modbus_id=1)

        assert "tipo_registro" not in body
        assert "direccion_inicial" not in body


class TestGatewayIpAtCreation:
    async def test_the_ip_can_be_set_in_one_call(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        """It is known at install time when the device has a fixed address."""
        detail = await client.get(
            f"/api/v1/gateways/{gateway_id}", headers=admin_headers
        )
        site_id = detail.json()["site_id"]

        response = await client.post(
            f"/api/v1/sites/{site_id}/gateways",
            json={"numero_serie": "GW-2", "ip_actual": "192.168.1.50"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["ip_actual"] == "192.168.1.50"

    async def test_it_stays_null_when_omitted(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        response = await client.get(
            f"/api/v1/gateways/{gateway_id}", headers=admin_headers
        )

        assert response.json()["ip_actual"] is None

    async def test_the_state_is_still_not_settable_by_the_operator(
        self, client: AsyncClient, admin_headers: dict[str, str], gateway_id: str
    ) -> None:
        """The device reports its own connectivity."""
        detail = await client.get(
            f"/api/v1/gateways/{gateway_id}", headers=admin_headers
        )

        response = await client.post(
            f"/api/v1/sites/{detail.json()['site_id']}/gateways",
            json={"numero_serie": "GW-3", "estado": "online"},
            headers=admin_headers,
        )

        assert response.json()["estado"] == "offline"


class TestEndpointProperty:
    """`Equipment.endpoint` shows up in conflict messages and logs."""

    async def test_a_serial_device_reads_as_its_port(
        self, db_session: AsyncSession
    ) -> None:
        from tests.factories import make_equipment

        equipment = make_equipment(Gateway(id=uuid.uuid4()), puerto="/dev/ttyRS485")

        assert equipment.endpoint == "/dev/ttyRS485"

    async def test_a_network_device_reads_as_host_and_port(
        self, db_session: AsyncSession
    ) -> None:
        from tests.factories import make_tcp_equipment

        equipment = make_tcp_equipment(Gateway(id=uuid.uuid4()), host="10.0.0.9")

        assert equipment.endpoint == "10.0.0.9:502"
