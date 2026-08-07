"""Listings across the whole fleet, not just down one branch."""

from collections.abc import Awaitable, Callable

import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from tests.conftest import auth_header

type Login = Callable[..., Awaitable[str]]


@pytest.fixture
async def admin_headers(admin_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(admin_user.email))


@pytest.fixture
async def fleet(client: AsyncClient, admin_headers: dict[str, str]) -> dict[str, str]:
    """Two clients, each with a site, a gateway and a device."""
    ids: dict[str, str] = {}
    for index, nombre in enumerate(("Empresa Norte", "Empresa Sur")):
        created = await client.post(
            "/api/v1/clients",
            json={"nombre_empresa": nombre},
            headers=admin_headers,
        )
        client_id = created.json()["id"]
        site = await client.post(
            f"/api/v1/clients/{client_id}/sites",
            json={"nombre": f"Planta {index}"},
            headers=admin_headers,
        )
        gateway = await client.post(
            f"/api/v1/sites/{site.json()['id']}/gateways",
            json={"numero_serie": f"GW-{index}"},
            headers=admin_headers,
        )
        equipment = await client.post(
            f"/api/v1/gateways/{gateway.json()['id']}/equipment",
            json={
                "tipo": "analizador",
                "modbus_id": 11,
                "nombre_dispositivo": f"Modbus_{index}",
                "device_type": "CT_Meter",
                "marca": "chint",
            },
            headers=admin_headers,
        )
        ids[f"client_{index}"] = client_id
        ids[f"site_{index}"] = site.json()["id"]
        ids[f"gateway_{index}"] = gateway.json()["id"]
        ids[f"gateway_uuid_{index}"] = gateway.json()["uuid"]
        ids[f"equipment_{index}"] = equipment.json()["id"]
    return ids


class TestListingTheWholeFleet:
    async def test_gateways_of_every_client_come_back_at_once(
        self, client: AsyncClient, admin_headers: dict[str, str], fleet: dict[str, str]
    ) -> None:
        """The question the panel opens with, in one request."""
        response = await client.get("/api/v1/gateways", headers=admin_headers)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["total"] == 2

    async def test_sites_of_every_client_come_back_at_once(
        self, client: AsyncClient, admin_headers: dict[str, str], fleet: dict[str, str]
    ) -> None:
        response = await client.get("/api/v1/sites", headers=admin_headers)

        assert response.json()["total"] == 2

    async def test_equipment_of_every_gateway_comes_back_at_once(
        self, client: AsyncClient, admin_headers: dict[str, str], fleet: dict[str, str]
    ) -> None:
        response = await client.get("/api/v1/equipment", headers=admin_headers)

        assert response.json()["total"] == 2


class TestFilters:
    async def test_gateways_can_be_narrowed_to_one_client(
        self, client: AsyncClient, admin_headers: dict[str, str], fleet: dict[str, str]
    ) -> None:
        response = await client.get(
            f"/api/v1/gateways?client_id={fleet['client_0']}", headers=admin_headers
        )

        assert response.json()["total"] == 1
        assert response.json()["items"][0]["numero_serie"] == "GW-0"

    async def test_sites_can_be_narrowed_to_one_client(
        self, client: AsyncClient, admin_headers: dict[str, str], fleet: dict[str, str]
    ) -> None:
        response = await client.get(
            f"/api/v1/sites?client_id={fleet['client_1']}", headers=admin_headers
        )

        assert response.json()["total"] == 1
        assert response.json()["items"][0]["nombre"] == "Planta 1"

    async def test_gateways_can_be_narrowed_to_one_site(
        self, client: AsyncClient, admin_headers: dict[str, str], fleet: dict[str, str]
    ) -> None:
        response = await client.get(
            f"/api/v1/gateways?site_id={fleet['site_1']}", headers=admin_headers
        )

        assert response.json()["total"] == 1

    async def test_equipment_can_be_narrowed_to_one_gateway(
        self, client: AsyncClient, admin_headers: dict[str, str], fleet: dict[str, str]
    ) -> None:
        response = await client.get(
            f"/api/v1/equipment?gateway_id={fleet['gateway_0']}", headers=admin_headers
        )

        assert response.json()["total"] == 1

    async def test_sites_can_be_searched_by_name(
        self, client: AsyncClient, admin_headers: dict[str, str], fleet: dict[str, str]
    ) -> None:
        response = await client.get(
            "/api/v1/sites?search=Planta 1", headers=admin_headers
        )

        assert response.json()["total"] == 1

    async def test_gateways_can_be_searched_by_serial(
        self, client: AsyncClient, admin_headers: dict[str, str], fleet: dict[str, str]
    ) -> None:
        response = await client.get(
            "/api/v1/gateways?search=GW-1", headers=admin_headers
        )

        assert response.json()["total"] == 1

    async def test_equipment_can_be_searched_by_brand(
        self, client: AsyncClient, admin_headers: dict[str, str], fleet: dict[str, str]
    ) -> None:
        response = await client.get(
            "/api/v1/equipment?search=chint", headers=admin_headers
        )

        assert response.json()["total"] == 2

    async def test_a_search_that_matches_nothing_returns_an_empty_page(
        self, client: AsyncClient, admin_headers: dict[str, str], fleet: dict[str, str]
    ) -> None:
        response = await client.get(
            "/api/v1/gateways?search=no-existe", headers=admin_headers
        )

        assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


class TestFilteringByReachability:
    async def test_everything_starts_offline(
        self, client: AsyncClient, admin_headers: dict[str, str], fleet: dict[str, str]
    ) -> None:
        response = await client.get(
            "/api/v1/gateways?estado=offline", headers=admin_headers
        )

        assert response.json()["total"] == 2

    async def test_a_gateway_that_reported_in_shows_as_online(
        self, client: AsyncClient, admin_headers: dict[str, str], fleet: dict[str, str]
    ) -> None:
        """What makes the fleet view answer "which ones are down"."""
        credential = await client.post(
            f"/api/v1/gateways/{fleet['gateway_0']}/credential", headers=admin_headers
        )
        token = await client.post(
            "/api/v1/gateway/token",
            json={
                "gateway_uuid": fleet["gateway_uuid_0"],
                "credential": credential.json()["credential"],
            },
        )
        await client.post(
            f"/api/v1/gateway/{fleet['gateway_uuid_0']}/heartbeat",
            json={},
            headers=auth_header(token.json()["access_token"]),
        )

        online = await client.get(
            "/api/v1/gateways?estado=online", headers=admin_headers
        )
        offline = await client.get(
            "/api/v1/gateways?estado=offline", headers=admin_headers
        )

        assert online.json()["total"] == 1
        assert online.json()["items"][0]["numero_serie"] == "GW-0"
        assert offline.json()["total"] == 1


class TestIsolationHolds:
    @pytest.fixture
    async def cliente_headers(
        self,
        db_session: AsyncSession,
        fleet: dict[str, str],
        authenticate_monitor: Login,
    ) -> dict[str, str]:
        import uuid as uuid_module

        from app.domain.enums import UserRole
        from tests.conftest import TEST_PASSWORD_HASH

        user = User(
            email="cliente@example.com",
            password_hash=TEST_PASSWORD_HASH,
            role=UserRole.CLIENTE,
            client_id=uuid_module.UUID(fleet["client_0"]),
        )
        db_session.add(user)
        await db_session.flush()
        return auth_header(await authenticate_monitor(user.email))

    @pytest.mark.parametrize(
        "path", ["/api/v1/gateways", "/api/v1/sites", "/api/v1/equipment"]
    )
    async def test_a_client_only_sees_its_own(
        self, client: AsyncClient, cliente_headers: dict[str, str], path: str
    ) -> None:
        """The fleet listings honour the same confinement as everything else."""
        response = await client.get(path, headers=cliente_headers)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["total"] == 1

    async def test_asking_for_another_client_returns_nothing(
        self,
        client: AsyncClient,
        cliente_headers: dict[str, str],
        fleet: dict[str, str],
    ) -> None:
        """The filter cannot widen what the caller may see."""
        response = await client.get(
            f"/api/v1/gateways?client_id={fleet['client_1']}", headers=cliente_headers
        )

        assert response.json()["total"] == 0

    @pytest.mark.parametrize(
        "path", ["/api/v1/gateways", "/api/v1/sites", "/api/v1/equipment"]
    )
    async def test_an_anonymous_caller_gets_401(
        self, client: AsyncClient, path: str
    ) -> None:
        response = await client.get(path)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestPagination:
    async def test_the_page_size_is_respected(
        self, client: AsyncClient, admin_headers: dict[str, str], fleet: dict[str, str]
    ) -> None:
        response = await client.get("/api/v1/gateways?limit=1", headers=admin_headers)

        assert len(response.json()["items"]) == 1
        assert response.json()["total"] == 2

    @pytest.mark.parametrize("query", ["limit=0", "limit=201", "offset=-1"])
    async def test_out_of_range_pagination_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], query: str
    ) -> None:
        response = await client.get(f"/api/v1/gateways?{query}", headers=admin_headers)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
