"""Tenant isolation and write permissions on the CRUD endpoints.

A `cliente` login must never reach another company's rows, and only staff may
change anything. These are the tests that should fail loudly if a later phase
adds an endpoint that forgets to ask the service.
"""

import uuid
from collections.abc import Awaitable, Callable

import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import UserRole
from app.models import Client, Gateway, Site, User, Variable
from tests.conftest import TEST_PASSWORD_HASH, auth_header
from tests.factories import make_equipment

type Login = Callable[[str], Awaitable[str]]


class Fixtures:
    """Ids of a two-client world, so a test can name what it is reaching for."""

    def __init__(
        self,
        own_client: uuid.UUID,
        other_client: uuid.UUID,
        own: dict[str, uuid.UUID],
        other: dict[str, uuid.UUID],
    ) -> None:
        self.own_client = own_client
        self.other_client = other_client
        self.own = own
        self.other = other


async def _build_tree(
    session: AsyncSession, nombre: str, serie: str
) -> tuple[Client, dict[str, uuid.UUID]]:
    client = Client(nombre_empresa=nombre)
    session.add(client)
    await session.flush()
    site = Site(client_id=client.id, nombre="Planta", timezone="America/Bogota")
    session.add(site)
    await session.flush()
    gateway = Gateway(site_id=site.id, numero_serie=serie)
    session.add(gateway)
    await session.flush()
    equipment = make_equipment(gateway)
    session.add(equipment)
    await session.flush()
    variable = Variable(
        equipment_id=equipment.id, nombre="voltaje_l1", registro_modbus=100
    )
    session.add(variable)
    await session.flush()
    return client, {
        "site": site.id,
        "gateway": gateway.id,
        "equipment": equipment.id,
        "variable": variable.id,
    }


@pytest.fixture
async def world(db_session: AsyncSession) -> Fixtures:
    mine, own = await _build_tree(db_session, "Empresa Propia", "GW-MINE")
    theirs, other = await _build_tree(db_session, "Empresa Ajena", "GW-THEIRS")
    return Fixtures(mine.id, theirs.id, own, other)


@pytest.fixture
async def cliente_of_own(db_session: AsyncSession, world: Fixtures) -> User:
    user = User(
        email="cliente@example.com",
        password_hash=TEST_PASSWORD_HASH,
        role=UserRole.CLIENTE,
        client_id=world.own_client,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.fixture
async def readonly_user(db_session: AsyncSession) -> User:
    user = User(
        email="lectura@example.com",
        password_hash=TEST_PASSWORD_HASH,
        role=UserRole.SOLO_LECTURA,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.fixture
async def cliente_headers(
    cliente_of_own: User, authenticate_monitor: Login
) -> dict[str, str]:
    """A client only ever holds a monitoring token; the CRM refuses it a login."""
    return auth_header(await authenticate_monitor(cliente_of_own.email))


@pytest.fixture
async def readonly_headers(readonly_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(readonly_user.email))


@pytest.fixture
async def admin_headers(admin_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(admin_user.email))


class TestClientSeesOnlyItsOwn:
    async def test_the_client_list_is_narrowed_to_one_row(
        self, client: AsyncClient, world: Fixtures, cliente_headers: dict[str, str]
    ) -> None:
        response = await client.get("/api/v1/clients", headers=cliente_headers)

        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == str(world.own_client)

    async def test_its_own_client_is_readable(
        self, client: AsyncClient, world: Fixtures, cliente_headers: dict[str, str]
    ) -> None:
        response = await client.get(
            f"/api/v1/clients/{world.own_client}", headers=cliente_headers
        )

        assert response.status_code == status.HTTP_200_OK

    async def test_another_client_reads_as_404_not_403(
        self, client: AsyncClient, world: Fixtures, cliente_headers: dict[str, str]
    ) -> None:
        """403 would confirm the row exists; 404 says nothing either way."""
        response = await client.get(
            f"/api/v1/clients/{world.other_client}", headers=cliente_headers
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize(
        ("path", "key"),
        [
            ("/api/v1/sites/{}", "site"),
            ("/api/v1/gateways/{}", "gateway"),
            ("/api/v1/equipment/{}", "equipment"),
            ("/api/v1/variables/{}", "variable"),
        ],
    )
    async def test_its_own_subtree_is_readable(
        self,
        client: AsyncClient,
        world: Fixtures,
        cliente_headers: dict[str, str],
        path: str,
        key: str,
    ) -> None:
        response = await client.get(
            path.format(world.own[key]), headers=cliente_headers
        )

        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize(
        ("path", "key"),
        [
            ("/api/v1/sites/{}", "site"),
            ("/api/v1/gateways/{}", "gateway"),
            ("/api/v1/equipment/{}", "equipment"),
            ("/api/v1/variables/{}", "variable"),
        ],
    )
    async def test_another_clients_subtree_is_invisible(
        self,
        client: AsyncClient,
        world: Fixtures,
        cliente_headers: dict[str, str],
        path: str,
        key: str,
    ) -> None:
        response = await client.get(
            path.format(world.other[key]), headers=cliente_headers
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize(
        ("path", "key"),
        [
            ("/api/v1/clients/{}/sites", "client"),
            ("/api/v1/sites/{}/gateways", "site"),
            ("/api/v1/gateways/{}/equipment", "gateway"),
            ("/api/v1/equipment/{}/variables", "equipment"),
        ],
    )
    async def test_listing_another_clients_children_is_invisible(
        self,
        client: AsyncClient,
        world: Fixtures,
        cliente_headers: dict[str, str],
        path: str,
        key: str,
    ) -> None:
        target = world.other_client if key == "client" else world.other[key]

        response = await client.get(path.format(target), headers=cliente_headers)

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestClientCannotWrite:
    async def test_it_cannot_create_a_client(
        self, client: AsyncClient, cliente_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/clients",
            json={"nombre_empresa": "Nueva"},
            headers=cliente_headers,
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_it_cannot_edit_even_its_own_client(
        self, client: AsyncClient, world: Fixtures, cliente_headers: dict[str, str]
    ) -> None:
        """Clients read their installation; staff maintains it."""
        response = await client.patch(
            f"/api/v1/clients/{world.own_client}",
            json={"nombre_empresa": "Renombrada"},
            headers=cliente_headers,
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_it_cannot_add_a_site_to_its_own_client(
        self, client: AsyncClient, world: Fixtures, cliente_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            f"/api/v1/clients/{world.own_client}/sites",
            json={"nombre": "Nueva Planta"},
            headers=cliente_headers,
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_it_cannot_delete_its_own_gateway(
        self, client: AsyncClient, world: Fixtures, cliente_headers: dict[str, str]
    ) -> None:
        response = await client.delete(
            f"/api/v1/gateways/{world.own['gateway']}", headers=cliente_headers
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestReadOnlyRole:
    async def test_it_sees_every_client(
        self, client: AsyncClient, world: Fixtures, readonly_headers: dict[str, str]
    ) -> None:
        response = await client.get("/api/v1/clients", headers=readonly_headers)

        assert response.json()["total"] == 2

    async def test_it_reads_any_subtree(
        self, client: AsyncClient, world: Fixtures, readonly_headers: dict[str, str]
    ) -> None:
        response = await client.get(
            f"/api/v1/gateways/{world.other['gateway']}", headers=readonly_headers
        )

        assert response.status_code == status.HTTP_200_OK

    async def test_it_writes_nothing(
        self, client: AsyncClient, readonly_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/clients",
            json={"nombre_empresa": "Nueva"},
            headers=readonly_headers,
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_it_cannot_patch(
        self, client: AsyncClient, world: Fixtures, readonly_headers: dict[str, str]
    ) -> None:
        response = await client.patch(
            f"/api/v1/sites/{world.own['site']}",
            json={"nombre": "Otra"},
            headers=readonly_headers,
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestStaffReachesEverything:
    async def test_an_admin_sees_both_clients(
        self, client: AsyncClient, world: Fixtures, admin_headers: dict[str, str]
    ) -> None:
        response = await client.get("/api/v1/clients", headers=admin_headers)

        assert response.json()["total"] == 2

    async def test_an_admin_edits_any_client(
        self, client: AsyncClient, world: Fixtures, admin_headers: dict[str, str]
    ) -> None:
        response = await client.patch(
            f"/api/v1/clients/{world.other_client}",
            json={"estado": "activo"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK


class TestAnonymous:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/clients",
            "/api/v1/sites/{}",
            "/api/v1/gateways/{}",
            "/api/v1/equipment/{}",
            "/api/v1/variables/{}",
        ],
    )
    async def test_no_crud_route_answers_without_a_token(
        self, client: AsyncClient, world: Fixtures, path: str
    ) -> None:
        response = await client.get(path.format(uuid.uuid4()))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
