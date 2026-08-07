"""The administrative CRUD endpoints, end to end."""

from collections.abc import Awaitable, Callable

import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from tests.conftest import auth_header

type Login = Callable[[str], Awaitable[str]]


@pytest.fixture
async def admin_headers(admin_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(admin_user.email))


@pytest.fixture
async def tecnico_headers(tecnico_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(tecnico_user.email))


async def _create_client(
    client: AsyncClient, headers: dict[str, str], nombre: str = "Industrias Andinas"
) -> str:
    response = await client.post(
        "/api/v1/clients", json={"nombre_empresa": nombre}, headers=headers
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    client_id: str = response.json()["id"]
    return client_id


async def _create_site(
    client: AsyncClient, headers: dict[str, str], client_id: str, nombre: str = "Planta"
) -> str:
    response = await client.post(
        f"/api/v1/clients/{client_id}/sites",
        json={"nombre": nombre, "timezone": "America/Bogota"},
        headers=headers,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    site_id: str = response.json()["id"]
    return site_id


async def _create_gateway(
    client: AsyncClient, headers: dict[str, str], site_id: str, serie: str = "GW-0001"
) -> dict[str, str]:
    response = await client.post(
        f"/api/v1/sites/{site_id}/gateways",
        json={"numero_serie": serie},
        headers=headers,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    body: dict[str, str] = response.json()
    return body


async def _create_equipment(
    client: AsyncClient, headers: dict[str, str], gateway_id: str, modbus_id: int = 1
) -> str:
    response = await client.post(
        f"/api/v1/gateways/{gateway_id}/equipment",
        json={
            "tipo": "analizador",
            "modbus_id": modbus_id,
            "nombre_dispositivo": f"Modbus_{modbus_id}",
            "device_type": "CT_Meter",
        },
        headers=headers,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    equipment_id: str = response.json()["id"]
    return equipment_id


class TestClientCrud:
    async def test_create_then_read_back(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        client_id = await _create_client(client, admin_headers)

        response = await client.get(
            f"/api/v1/clients/{client_id}", headers=admin_headers
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["nombre_empresa"] == "Industrias Andinas"
        assert response.json()["estado"] == "prospecto"

    async def test_listing_is_paginated(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        for index in range(3):
            await _create_client(client, admin_headers, f"Empresa {index}")

        response = await client.get(
            "/api/v1/clients?limit=2&offset=0", headers=admin_headers
        )

        body = response.json()
        assert len(body["items"]) == 2
        assert body["total"] == 3
        assert body["limit"] == 2

    async def test_a_duplicate_name_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        await _create_client(client, admin_headers)

        response = await client.post(
            "/api/v1/clients",
            json={"nombre_empresa": "Industrias Andinas"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json()["error"]["code"] == "already_exists"

    async def test_patch_only_touches_the_fields_sent(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        client_id = await _create_client(client, admin_headers)
        await client.patch(
            f"/api/v1/clients/{client_id}",
            json={"contacto_email": "ana@example.com"},
            headers=admin_headers,
        )

        response = await client.patch(
            f"/api/v1/clients/{client_id}",
            json={"estado": "activo"},
            headers=admin_headers,
        )

        body = response.json()
        assert body["estado"] == "activo"
        assert body["contacto_email"] == "ana@example.com"

    async def test_an_unknown_id_is_404(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        import uuid

        response = await client.get(
            f"/api/v1/clients/{uuid.uuid4()}", headers=admin_headers
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_a_malformed_id_is_422(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.get("/api/v1/clients/not-a-uuid", headers=admin_headers)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_an_empty_name_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/clients", json={"nombre_empresa": ""}, headers=admin_headers
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestHierarchyCreation:
    async def test_the_whole_chain_can_be_built(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        client_id = await _create_client(client, admin_headers)
        site_id = await _create_site(client, admin_headers, client_id)
        gateway = await _create_gateway(client, admin_headers, site_id)
        equipment_id = await _create_equipment(client, admin_headers, gateway["id"])

        response = await client.post(
            f"/api/v1/equipment/{equipment_id}/variables",
            json={
                "nombre": "voltaje_l1",
                "registro_modbus": 100,
                "tipo_dato": "float32",
                "unidad": "V",
            },
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["equipment_id"] == equipment_id

    async def test_a_site_under_an_unknown_client_is_404(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        import uuid

        response = await client.post(
            f"/api/v1/clients/{uuid.uuid4()}/sites",
            json={"nombre": "Planta"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_a_gateway_under_an_unknown_site_is_404(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        import uuid

        response = await client.post(
            f"/api/v1/sites/{uuid.uuid4()}/gateways",
            json={"numero_serie": "GW-X"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_two_sites_of_one_client_cannot_share_a_name(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        client_id = await _create_client(client, admin_headers)
        await _create_site(client, admin_headers, client_id)

        response = await client.post(
            f"/api/v1/clients/{client_id}/sites",
            json={"nombre": "Planta"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    async def test_a_serial_is_unique_across_the_platform(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        first = await _create_client(client, admin_headers, "Empresa A")
        second = await _create_client(client, admin_headers, "Empresa B")
        site_a = await _create_site(client, admin_headers, first)
        site_b = await _create_site(client, admin_headers, second)
        await _create_gateway(client, admin_headers, site_a, "GW-DUP")

        response = await client.post(
            f"/api/v1/sites/{site_b}/gateways",
            json={"numero_serie": "GW-DUP"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    async def test_the_same_modbus_id_on_one_port_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        client_id = await _create_client(client, admin_headers)
        site_id = await _create_site(client, admin_headers, client_id)
        gateway = await _create_gateway(client, admin_headers, site_id)
        await _create_equipment(client, admin_headers, gateway["id"], modbus_id=5)

        response = await client.post(
            f"/api/v1/gateways/{gateway['id']}/equipment",
            json={
                "tipo": "medidor",
                "modbus_id": 5,
                "nombre_dispositivo": "Modbus_otro",
                "device_type": "CT_Meter",
            },
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    async def test_the_same_modbus_id_on_another_port_is_allowed(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        client_id = await _create_client(client, admin_headers)
        site_id = await _create_site(client, admin_headers, client_id)
        gateway = await _create_gateway(client, admin_headers, site_id)
        await _create_equipment(client, admin_headers, gateway["id"], modbus_id=5)

        response = await client.post(
            f"/api/v1/gateways/{gateway['id']}/equipment",
            json={
                "tipo": "medidor",
                "modbus_id": 5,
                "puerto": "/dev/ttymxc2",
                "nombre_dispositivo": "Modbus_otro_puerto",
                "device_type": "CT_Meter",
            },
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_201_CREATED

    @pytest.mark.parametrize("modbus_id", [0, 248, -1])
    async def test_a_modbus_id_outside_the_rtu_range_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], modbus_id: int
    ) -> None:
        client_id = await _create_client(client, admin_headers)
        site_id = await _create_site(client, admin_headers, client_id)
        gateway = await _create_gateway(client, admin_headers, site_id)

        response = await client.post(
            f"/api/v1/gateways/{gateway['id']}/equipment",
            json={
                "tipo": "medidor",
                "modbus_id": modbus_id,
                "nombre_dispositivo": "Modbus_rango",
                "device_type": "CT_Meter",
            },
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_a_zero_scale_variable_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """A zero multiplier would silently turn every reading into zero."""
        client_id = await _create_client(client, admin_headers)
        site_id = await _create_site(client, admin_headers, client_id)
        gateway = await _create_gateway(client, admin_headers, site_id)
        equipment_id = await _create_equipment(client, admin_headers, gateway["id"])

        response = await client.post(
            f"/api/v1/equipment/{equipment_id}/variables",
            json={"nombre": "v", "registro_modbus": 1, "escala": 0},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestCascadeDelete:
    async def test_deleting_a_site_takes_its_subtree(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        from sqlalchemy import func, select

        from app.models import Equipment, Gateway, Variable

        client_id = await _create_client(client, admin_headers)
        site_id = await _create_site(client, admin_headers, client_id)
        gateway = await _create_gateway(client, admin_headers, site_id)
        equipment_id = await _create_equipment(client, admin_headers, gateway["id"])
        await client.post(
            f"/api/v1/equipment/{equipment_id}/variables",
            json={"nombre": "v", "registro_modbus": 1},
            headers=admin_headers,
        )

        response = await client.delete(
            f"/api/v1/sites/{site_id}", headers=admin_headers
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        for model in (Gateway, Equipment, Variable):
            count = (
                await db_session.execute(select(func.count()).select_from(model))
            ).scalar_one()
            assert count == 0, model.__name__


class TestConsumptionPageFlag:
    """Whether a client's users may open their energy consumption page."""

    async def test_it_is_off_for_a_new_client(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """A half-configured client must not see partial readings."""
        client_id = await _create_client(client, admin_headers)

        response = await client.get(
            f"/api/v1/clients/{client_id}", headers=admin_headers
        )

        assert response.json()["puede_ver_consumo"] is False

    async def test_it_can_be_set_at_creation(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/clients",
            json={"nombre_empresa": "Habilitada", "puede_ver_consumo": True},
            headers=admin_headers,
        )

        assert response.json()["puede_ver_consumo"] is True

    async def test_staff_can_turn_it_on_and_off(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        client_id = await _create_client(client, admin_headers)

        enabled = await client.patch(
            f"/api/v1/clients/{client_id}",
            json={"puede_ver_consumo": True},
            headers=admin_headers,
        )
        disabled = await client.patch(
            f"/api/v1/clients/{client_id}",
            json={"puede_ver_consumo": False},
            headers=admin_headers,
        )

        assert enabled.json()["puede_ver_consumo"] is True
        assert disabled.json()["puede_ver_consumo"] is False

    async def test_toggling_it_leaves_other_fields_alone(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        client_id = await _create_client(client, admin_headers)

        response = await client.patch(
            f"/api/v1/clients/{client_id}",
            json={"puede_ver_consumo": True},
            headers=admin_headers,
        )

        assert response.json()["nombre_empresa"] == "Industrias Andinas"
        assert response.json()["estado"] == "prospecto"
