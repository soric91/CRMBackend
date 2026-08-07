"""Listing children at every level of the hierarchy."""

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
async def populated(
    client: AsyncClient, admin_headers: dict[str, str]
) -> dict[str, str]:
    """One client with two sites; the first site holds the rest of the chain."""
    created = await client.post(
        "/api/v1/clients", json={"nombre_empresa": "Empresa"}, headers=admin_headers
    )
    client_id = created.json()["id"]

    site_ids = []
    for nombre in ("Planta Norte", "Planta Sur"):
        site = await client.post(
            f"/api/v1/clients/{client_id}/sites",
            json={"nombre": nombre},
            headers=admin_headers,
        )
        site_ids.append(site.json()["id"])

    gateway_ids = []
    for serie in ("GW-1", "GW-2"):
        gateway = await client.post(
            f"/api/v1/sites/{site_ids[0]}/gateways",
            json={"numero_serie": serie},
            headers=admin_headers,
        )
        gateway_ids.append(gateway.json()["id"])

    equipment_ids = []
    for modbus_id in (1, 2):
        equipment = await client.post(
            f"/api/v1/gateways/{gateway_ids[0]}/equipment",
            json={
                "tipo": "analizador",
                "modbus_id": modbus_id,
                "nombre_dispositivo": f"Modbus_{modbus_id}",
                "device_type": "CT_Meter",
            },
            headers=admin_headers,
        )
        equipment_ids.append(equipment.json()["id"])

    for nombre, registro in (("voltaje_l1", 100), ("corriente_l1", 200)):
        await client.post(
            f"/api/v1/equipment/{equipment_ids[0]}/variables",
            json={"nombre": nombre, "registro_modbus": registro},
            headers=admin_headers,
        )

    return {
        "client": client_id,
        "site": site_ids[0],
        "empty_site": site_ids[1],
        "gateway": gateway_ids[0],
        "empty_gateway": gateway_ids[1],
        "equipment": equipment_ids[0],
        "empty_equipment": equipment_ids[1],
    }


class TestListingChildren:
    @pytest.mark.parametrize(
        ("path", "key"),
        [
            ("/api/v1/clients/{}/sites", "client"),
            ("/api/v1/sites/{}/gateways", "site"),
            ("/api/v1/gateways/{}/equipment", "gateway"),
            ("/api/v1/equipment/{}/variables", "equipment"),
        ],
    )
    async def test_each_level_returns_its_two_children(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        populated: dict[str, str],
        path: str,
        key: str,
    ) -> None:
        response = await client.get(path.format(populated[key]), headers=admin_headers)

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2

    @pytest.mark.parametrize(
        ("path", "key"),
        [
            ("/api/v1/sites/{}/gateways", "empty_site"),
            ("/api/v1/gateways/{}/equipment", "empty_gateway"),
            ("/api/v1/equipment/{}/variables", "empty_equipment"),
        ],
    )
    async def test_a_childless_parent_returns_an_empty_page(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        populated: dict[str, str],
        path: str,
        key: str,
    ) -> None:
        response = await client.get(path.format(populated[key]), headers=admin_headers)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "items": [],
            "total": 0,
            "limit": 50,
            "offset": 0,
        }

    async def test_children_of_one_parent_do_not_leak_into_another(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        populated: dict[str, str],
    ) -> None:
        response = await client.get(
            f"/api/v1/sites/{populated['empty_site']}/gateways", headers=admin_headers
        )

        assert response.json()["total"] == 0

    async def test_sites_come_back_ordered_by_name(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        populated: dict[str, str],
    ) -> None:
        response = await client.get(
            f"/api/v1/clients/{populated['client']}/sites", headers=admin_headers
        )

        names = [item["nombre"] for item in response.json()["items"]]
        assert names == sorted(names)


class TestPagination:
    async def test_offset_skips_rows(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        populated: dict[str, str],
    ) -> None:
        first = await client.get(
            f"/api/v1/clients/{populated['client']}/sites?limit=1&offset=0",
            headers=admin_headers,
        )
        second = await client.get(
            f"/api/v1/clients/{populated['client']}/sites?limit=1&offset=1",
            headers=admin_headers,
        )

        assert first.json()["items"][0]["id"] != second.json()["items"][0]["id"]

    async def test_the_total_ignores_the_page_size(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        populated: dict[str, str],
    ) -> None:
        response = await client.get(
            f"/api/v1/clients/{populated['client']}/sites?limit=1",
            headers=admin_headers,
        )

        body = response.json()
        assert len(body["items"]) == 1
        assert body["total"] == 2

    async def test_an_offset_past_the_end_returns_nothing(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        populated: dict[str, str],
    ) -> None:
        response = await client.get(
            f"/api/v1/clients/{populated['client']}/sites?offset=99",
            headers=admin_headers,
        )

        assert response.json()["items"] == []
        assert response.json()["total"] == 2

    @pytest.mark.parametrize("query", ["limit=0", "limit=201", "limit=-1", "offset=-1"])
    async def test_out_of_range_pagination_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], query: str
    ) -> None:
        response = await client.get(f"/api/v1/clients?{query}", headers=admin_headers)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestDuplicateNames:
    async def test_a_duplicate_variable_name_is_rejected_at_creation(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        populated: dict[str, str],
    ) -> None:
        response = await client.post(
            f"/api/v1/equipment/{populated['equipment']}/variables",
            json={"nombre": "voltaje_l1", "registro_modbus": 999},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    async def test_the_same_variable_name_on_another_equipment_is_fine(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        populated: dict[str, str],
    ) -> None:
        response = await client.post(
            f"/api/v1/equipment/{populated['empty_equipment']}/variables",
            json={"nombre": "voltaje_l1", "registro_modbus": 100},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_201_CREATED

    async def test_renaming_a_client_onto_another_name_is_rejected(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        populated: dict[str, str],
    ) -> None:
        other = await client.post(
            "/api/v1/clients", json={"nombre_empresa": "Otra"}, headers=admin_headers
        )

        response = await client.patch(
            f"/api/v1/clients/{other.json()['id']}",
            json={"nombre_empresa": "Empresa"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    async def test_renaming_a_client_to_its_own_name_is_fine(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        populated: dict[str, str],
    ) -> None:
        response = await client.patch(
            f"/api/v1/clients/{populated['client']}",
            json={"nombre_empresa": "Empresa"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK
