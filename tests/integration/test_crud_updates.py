"""Updating and deleting deeper in the hierarchy."""

import uuid
from collections.abc import Awaitable, Callable

import pytest
from fastapi import status
from httpx import AsyncClient

from app.models import User
from tests.conftest import auth_header

type Login = Callable[[str], Awaitable[str]]


@pytest.fixture
async def admin_headers(admin_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(admin_user.email))


@pytest.fixture
async def tree(client: AsyncClient, admin_headers: dict[str, str]) -> dict[str, str]:
    """A full Client -> Variable chain, created through the API."""
    created = await client.post(
        "/api/v1/clients", json={"nombre_empresa": "Empresa"}, headers=admin_headers
    )
    client_id = created.json()["id"]

    site = await client.post(
        f"/api/v1/clients/{client_id}/sites",
        json={"nombre": "Planta"},
        headers=admin_headers,
    )
    site_id = site.json()["id"]

    gateway = await client.post(
        f"/api/v1/sites/{site_id}/gateways",
        json={"numero_serie": "GW-1"},
        headers=admin_headers,
    )
    gateway_id = gateway.json()["id"]

    equipment = await client.post(
        f"/api/v1/gateways/{gateway_id}/equipment",
        json={
            "tipo": "analizador",
            "modbus_id": 1,
            "nombre_dispositivo": "Modbus_1",
            "device_type": "CT_Meter",
        },
        headers=admin_headers,
    )
    equipment_id = equipment.json()["id"]

    variable = await client.post(
        f"/api/v1/equipment/{equipment_id}/variables",
        json={"nombre": "PhV_phsA", "registro_modbus": 100},
        headers=admin_headers,
    )
    return {
        "client": client_id,
        "site": site_id,
        "gateway": gateway_id,
        "equipment": equipment_id,
        "variable": variable.json()["id"],
    }


class TestSiteUpdate:
    async def test_a_field_can_be_changed(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        response = await client.patch(
            f"/api/v1/sites/{tree['site']}",
            json={"timezone": "America/Lima"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["timezone"] == "America/Lima"
        assert response.json()["nombre"] == "Planta"

    async def test_renaming_onto_a_sibling_name_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        second = await client.post(
            f"/api/v1/clients/{tree['client']}/sites",
            json={"nombre": "Planta Sur"},
            headers=admin_headers,
        )

        response = await client.patch(
            f"/api/v1/sites/{second.json()['id']}",
            json={"nombre": "Planta"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    async def test_renaming_to_its_own_name_is_fine(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        """The uniqueness check must exclude the row being edited."""
        response = await client.patch(
            f"/api/v1/sites/{tree['site']}",
            json={"nombre": "Planta"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK

    async def test_out_of_range_coordinates_are_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        response = await client.patch(
            f"/api/v1/sites/{tree['site']}",
            json={"latitud": 95},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestGatewayUpdate:
    async def test_the_ip_can_be_recorded(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        response = await client.patch(
            f"/api/v1/gateways/{tree['gateway']}",
            json={"ip_actual": "192.168.1.20"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["ip_actual"] == "192.168.1.20"

    async def test_the_status_cannot_be_set_by_hand(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        """It is observed from the last contact, so there is nothing to set."""
        response = await client.patch(
            f"/api/v1/gateways/{tree['gateway']}",
            json={"estado": "online"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        # A gateway that has never reported in is offline, whatever was sent.
        assert response.json()["estado"] == "offline"

    async def test_taking_another_gateways_serial_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        second = await client.post(
            f"/api/v1/sites/{tree['site']}/gateways",
            json={"numero_serie": "GW-2"},
            headers=admin_headers,
        )

        response = await client.patch(
            f"/api/v1/gateways/{second.json()['id']}",
            json={"numero_serie": "GW-1"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    async def test_keeping_its_own_serial_is_fine(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        response = await client.patch(
            f"/api/v1/gateways/{tree['gateway']}",
            json={"numero_serie": "GW-1", "firmware_version": "2.1.0"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["firmware_version"] == "2.1.0"

    async def test_deleting_it_removes_its_equipment(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        response = await client.delete(
            f"/api/v1/gateways/{tree['gateway']}", headers=admin_headers
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        gone = await client.get(
            f"/api/v1/equipment/{tree['equipment']}", headers=admin_headers
        )
        assert gone.status_code == status.HTTP_404_NOT_FOUND


class TestEquipmentUpdate:
    async def test_serial_parameters_can_be_changed(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        response = await client.patch(
            f"/api/v1/equipment/{tree['equipment']}",
            json={"baudrate": 19200, "paridad": "E"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["baudrate"] == 19200
        assert response.json()["paridad"] == "E"

    async def test_moving_onto_a_taken_address_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        second = await client.post(
            f"/api/v1/gateways/{tree['gateway']}/equipment",
            json={
                "tipo": "medidor",
                "modbus_id": 2,
                "nombre_dispositivo": "Modbus_2",
                "device_type": "CT_Meter",
            },
            headers=admin_headers,
        )

        response = await client.patch(
            f"/api/v1/equipment/{second.json()['id']}",
            json={"modbus_id": 1},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    async def test_moving_to_a_free_port_frees_the_address(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        second = await client.post(
            f"/api/v1/gateways/{tree['gateway']}/equipment",
            json={
                "tipo": "medidor",
                "modbus_id": 2,
                "nombre_dispositivo": "Modbus_2",
                "device_type": "CT_Meter",
            },
            headers=admin_headers,
        )

        response = await client.patch(
            f"/api/v1/equipment/{second.json()['id']}",
            json={"modbus_id": 1, "puerto": "/dev/ttymxc2"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK

    async def test_keeping_its_own_address_is_fine(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        response = await client.patch(
            f"/api/v1/equipment/{tree['equipment']}",
            json={"marca": "Schneider"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK

    async def test_deleting_it_removes_its_variables(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        response = await client.delete(
            f"/api/v1/equipment/{tree['equipment']}", headers=admin_headers
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        gone = await client.get(
            f"/api/v1/variables/{tree['variable']}", headers=admin_headers
        )
        assert gone.status_code == status.HTTP_404_NOT_FOUND


class TestVariableUpdate:
    async def test_the_scale_can_be_changed(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        response = await client.patch(
            f"/api/v1/variables/{tree['variable']}",
            json={"escala": "0.1"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["escala"] == "0.100000"

    async def test_the_unit_cannot_be_set_by_hand(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        """Se deduce de qué se mide, y por eso no admite grafías rivales.

        Una unidad tecleada acepta tantas escrituras como personas la
        escriban — `kw`, `kW`, `KW`— y después nada las compara bien.
        """
        response = await client.patch(
            f"/api/v1/variables/{tree['variable']}",
            json={"unidad": "kV"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        # Ignorado, no aplicado: sigue siendo la del catálogo.
        assert response.json()["unidad"] == "V"

    async def test_the_name_must_come_from_the_catalogue(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        """Texto libre es lo que produjo `Voltaje A` contra `VOLTAGE_A`."""
        response = await client.patch(
            f"/api/v1/variables/{tree['variable']}",
            json={"nombre": "Voltaje C"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_a_zero_scale_is_still_rejected_on_update(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        response = await client.patch(
            f"/api/v1/variables/{tree['variable']}",
            json={"escala": 0},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_renaming_onto_a_sibling_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        second = await client.post(
            f"/api/v1/equipment/{tree['equipment']}/variables",
            json={"nombre": "A_phsA", "registro_modbus": 200},
            headers=admin_headers,
        )

        response = await client.patch(
            f"/api/v1/variables/{second.json()['id']}",
            json={"nombre": "PhV_phsA"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    async def test_it_can_be_deleted(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        response = await client.delete(
            f"/api/v1/variables/{tree['variable']}", headers=admin_headers
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT

    async def test_a_zero_read_interval_is_rejected_on_the_gateway(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        """The cadence belongs to the gateway: one loop walks every device."""
        response = await client.patch(
            f"/api/v1/gateways/{tree['gateway']}",
            json={"intervalo_lectura_segundos": 0},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestMissingTargets:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/sites/{}",
            "/api/v1/gateways/{}",
            "/api/v1/equipment/{}",
            "/api/v1/variables/{}",
        ],
    )
    async def test_patching_something_that_does_not_exist_is_404(
        self, client: AsyncClient, admin_headers: dict[str, str], path: str
    ) -> None:
        response = await client.patch(
            path.format(uuid.uuid4()), json={}, headers=admin_headers
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/sites/{}",
            "/api/v1/gateways/{}",
            "/api/v1/equipment/{}",
            "/api/v1/variables/{}",
        ],
    )
    async def test_deleting_something_that_does_not_exist_is_404(
        self, client: AsyncClient, admin_headers: dict[str, str], path: str
    ) -> None:
        response = await client.delete(path.format(uuid.uuid4()), headers=admin_headers)

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestRegisterNotation:
    """How an address is typed decides what register it points at."""

    async def test_hex_digits_are_stored_as_the_real_address(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        response = await client.post(
            f"/api/v1/equipment/{tree['equipment']}/variables",
            json={
                "nombre": "PhV_phsB",
                "registro_modbus": "2006",
                "notacion_registro": "hex",
            },
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["registro_modbus"] == 8198
        assert response.json()["registro_display"] == "0x2006"

    async def test_the_prefix_is_accepted(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        response = await client.post(
            f"/api/v1/equipment/{tree['equipment']}/variables",
            json={
                "nombre": "PhV_phsC",
                "registro_modbus": "0x2008",
                "notacion_registro": "hex",
            },
            headers=admin_headers,
        )

        assert response.json()["registro_modbus"] == 8200

    async def test_decimal_is_the_default_and_stays_literal(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        response = await client.post(
            f"/api/v1/equipment/{tree['equipment']}/variables",
            json={"nombre": "A_phsA", "registro_modbus": 2000},
            headers=admin_headers,
        )

        assert response.json()["notacion_registro"] == "decimal"
        assert response.json()["registro_modbus"] == 2000
        assert response.json()["registro_display"] == "2000"

    async def test_the_same_digits_mean_different_registers(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        """The reason the notation exists at all."""
        as_hex = await client.post(
            f"/api/v1/equipment/{tree['equipment']}/variables",
            json={
                "nombre": "A_phsB",
                "registro_modbus": "2000",
                "notacion_registro": "hex",
            },
            headers=admin_headers,
        )
        as_decimal = await client.post(
            f"/api/v1/equipment/{tree['equipment']}/variables",
            json={"nombre": "A_phsC", "registro_modbus": "2000"},
            headers=admin_headers,
        )

        assert as_hex.json()["registro_modbus"] == 8192
        assert as_decimal.json()["registro_modbus"] == 2000

    async def test_a_prefixed_value_declared_decimal_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        response = await client.post(
            f"/api/v1/equipment/{tree['equipment']}/variables",
            json={
                "nombre": "TotW",
                "registro_modbus": "0x2006",
                "notacion_registro": "decimal",
            },
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_the_notation_can_be_corrected_afterwards(
        self, client: AsyncClient, admin_headers: dict[str, str], tree: dict[str, str]
    ) -> None:
        """How a row loaded in the wrong base gets fixed from the panel."""
        created = await client.post(
            f"/api/v1/equipment/{tree['equipment']}/variables",
            json={"nombre": "TotPF", "registro_modbus": 2000},
            headers=admin_headers,
        )

        response = await client.patch(
            f"/api/v1/variables/{created.json()['id']}",
            json={"registro_modbus": "2006", "notacion_registro": "hex"},
            headers=admin_headers,
        )

        assert response.json()["registro_modbus"] == 8198
        assert response.json()["registro_display"] == "0x2006"
