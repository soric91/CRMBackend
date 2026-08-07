"""Where a service token reaches, and — mostly — where it does not.

The containment is by construction: an endpoint accepts machine tokens only
by asking for that dependency by name. These tests are what turns that from a
claim into something that fails loudly if somebody widens it by accident.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from fastapi import status
from httpx import AsyncClient

from app.models import User
from tests.conftest import auth_header

type Login = Callable[..., Awaitable[str]]

ACCOUNTS = "/api/v1/service-accounts"
TOKEN = "/api/v1/service/token"


@pytest.fixture
async def admin_headers(admin_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(admin_user.email))


@pytest.fixture
async def installation(
    client: AsyncClient, admin_headers: dict[str, str]
) -> dict[str, str]:
    """One client with a site, a gateway and a device under it."""
    created = await client.post(
        "/api/v1/clients",
        json={"nombre_empresa": "Empresa Norte"},
        headers=admin_headers,
    )
    client_id = created.json()["id"]
    site = await client.post(
        f"/api/v1/clients/{client_id}/sites",
        json={"nombre": "Planta Norte"},
        headers=admin_headers,
    )
    gateway = await client.post(
        f"/api/v1/sites/{site.json()['id']}/gateways",
        json={"numero_serie": "GW-NORTE"},
        headers=admin_headers,
    )
    return {
        "client_id": client_id,
        "site_id": site.json()["id"],
        "gateway_id": gateway.json()["id"],
    }


async def _token_for(
    client: AsyncClient,
    admin_headers: dict[str, str],
    *permissions: str,
    client_id: str | None = None,
) -> str:
    body: dict[str, Any] = {
        "nombre": f"Consumidor {uuid.uuid4().hex[:6]}",
        "permisos": list(permissions),
    }
    if client_id is not None:
        body["client_id"] = client_id

    created = (await client.post(ACCOUNTS, json=body, headers=admin_headers)).json()
    response = await client.post(
        TOKEN,
        json={
            "client_id": created["credencial_id"],
            "client_secret": created["client_secret"],
        },
    )
    token: str = response.json()["access_token"]
    return token


class TestWhatItReaches:
    async def test_it_reads_tariffs_with_that_permission(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        token = await _token_for(client, admin_headers, "tariffs:read")

        response = await client.get("/api/v1/tariffs", headers=auth_header(token))

        assert response.status_code == status.HTTP_200_OK

    async def test_it_reads_the_fleet_with_that_permission(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        token = await _token_for(client, admin_headers, "fleet:read")

        response = await client.get("/api/v1/fleet", headers=auth_header(token))

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["total"] == 1

    async def test_the_fleet_still_answers_304(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        """Cheap polling is the reason a machine reads this at all."""
        token = await _token_for(client, admin_headers, "fleet:read")
        headers = auth_header(token)

        etag = (await client.get("/api/v1/fleet", headers=headers)).headers["ETag"]
        response = await client.get(
            "/api/v1/fleet", headers={**headers, "If-None-Match": etag}
        )

        assert response.status_code == status.HTTP_304_NOT_MODIFIED


class TestWhatItDoesNotReach:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/clients",
            "/api/v1/sites",
            "/api/v1/gateways",
            "/api/v1/equipment",
            "/api/v1/users",
            "/api/v1/service-accounts",
            "/api/v1/auth/me",
        ],
    )
    async def test_every_other_endpoint_refuses_it(
        self, client: AsyncClient, admin_headers: dict[str, str], path: str
    ) -> None:
        """Not 403 but 401: those routes never learn what a service token is.

        This is the containment. An endpoint added tomorrow is closed to
        machines until somebody deliberately opens it.
        """
        token = await _token_for(client, admin_headers, "tariffs:read", "fleet:read")

        response = await client.get(path, headers=auth_header(token))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_it_cannot_write_a_tariff(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """The route it can read from is a GET; the POST beside it is not."""
        token = await _token_for(client, admin_headers, "tariffs:read")

        response = await client.post(
            "/api/v1/tariffs",
            json={
                "mes": "2026-03-01",
                "valor_importado": "800.0000",
                "valor_excedente": "300.0000",
            },
            headers=auth_header(token),
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_it_cannot_read_one_tariff_by_id(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """Only the listing was opened. The detail route was not."""
        created = await client.post(
            "/api/v1/tariffs",
            json={
                "mes": "2026-04-01",
                "valor_importado": "800.0000",
                "valor_excedente": "300.0000",
            },
            headers=admin_headers,
        )
        token = await _token_for(client, admin_headers, "tariffs:read")

        response = await client.get(
            f"/api/v1/tariffs/{created.json()['id']}", headers=auth_header(token)
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_it_cannot_mint_another_credential(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """Otherwise the permission list would be a suggestion."""
        token = await _token_for(client, admin_headers, "fleet:read")

        response = await client.post(
            ACCOUNTS,
            json={"nombre": "Escalada", "permisos": ["fleet:read"]},
            headers=auth_header(token),
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestPermissionsAreSeparate:
    async def test_tariffs_only_does_not_open_the_fleet(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """A consumer that needs prices has no business listing the devices."""
        token = await _token_for(client, admin_headers, "tariffs:read")

        response = await client.get("/api/v1/fleet", headers=auth_header(token))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_fleet_only_does_not_open_tariffs(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        token = await _token_for(client, admin_headers, "fleet:read")

        response = await client.get("/api/v1/tariffs", headers=auth_header(token))

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestConfinementToOneClient:
    async def test_a_pinned_credential_sees_only_its_client(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        await client.post(
            "/api/v1/clients",
            json={"nombre_empresa": "Empresa Ajena"},
            headers=admin_headers,
        )
        token = await _token_for(
            client, admin_headers, "fleet:read", client_id=installation["client_id"]
        )

        response = await client.get("/api/v1/fleet", headers=auth_header(token))

        payload = response.json()
        assert payload["total"] == 1
        assert payload["items"][0]["nombre_empresa"] == "Empresa Norte"

    async def test_asking_for_another_client_returns_nothing(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        """The same rule as a confined person: the filter cannot widen."""
        other = await client.post(
            "/api/v1/clients",
            json={"nombre_empresa": "Empresa Ajena"},
            headers=admin_headers,
        )
        token = await _token_for(
            client, admin_headers, "fleet:read", client_id=installation["client_id"]
        )

        response = await client.get(
            "/api/v1/fleet",
            params={"client_id": other.json()["id"]},
            headers=auth_header(token),
        )

        assert response.json()["total"] == 0

    async def test_an_unpinned_credential_sees_the_platform(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        await client.post(
            "/api/v1/clients",
            json={"nombre_empresa": "Empresa Ajena"},
            headers=admin_headers,
        )
        token = await _token_for(client, admin_headers, "fleet:read")

        response = await client.get("/api/v1/fleet", headers=auth_header(token))

        assert response.json()["total"] == 2


class TestPeopleStillWork:
    """The dependency was widened; the ordinary path must be unchanged."""

    async def test_an_admin_still_reads_tariffs(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.get("/api/v1/tariffs", headers=admin_headers)

        assert response.status_code == status.HTTP_200_OK

    async def test_a_client_is_still_refused_tariffs(
        self, client: AsyncClient, cliente_user: User, authenticate_monitor: Login
    ) -> None:
        headers = auth_header(await authenticate_monitor(cliente_user.email))

        response = await client.get("/api/v1/tariffs", headers=headers)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_a_garbage_token_is_still_a_401(self, client: AsyncClient) -> None:
        """The fall-through to the service path must not swallow the error."""
        response = await client.get(
            "/api/v1/fleet", headers=auth_header("no-es-un-token")
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_a_missing_header_is_still_a_401(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/fleet")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
