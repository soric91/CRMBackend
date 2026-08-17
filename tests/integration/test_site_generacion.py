"""F0.6 — la sede declara si tiene generación propia.

ApiEMS lee este campo para decidir cómo interpretar el medidor de frontera:
con generación solo ve el balance neto (varios indicadores solo valen sin
sol); sin generación, todo lo que pasa por el medidor es consumo.

`tiene_generacion` es NULLABLE y NULL significa "detéctalo", no "no tiene":
por eso ninguna sede nace en `False`.
"""

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


async def _create_client(client: AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post(
        "/api/v1/clients", json={"nombre_empresa": "Solar SAS"}, headers=headers
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    client_id: str = response.json()["id"]
    return client_id


class TestSedeConGeneracion:
    async def test_sin_declarar_queda_en_null_para_que_se_detecte(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        client_id = await _create_client(client, admin_headers)

        response = await client.post(
            f"/api/v1/clients/{client_id}/sites",
            json={"nombre": "Planta", "timezone": "America/Bogota"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_201_CREATED, response.text
        body = response.json()
        # NULL, no False: nadie lo dijo todavía y el modo se infiere de la
        # energía exportada.
        assert body["tiene_generacion"] is None
        assert body["capacidad_kwp"] is None

    async def test_declarar_generacion_y_capacidad(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        client_id = await _create_client(client, admin_headers)

        response = await client.post(
            f"/api/v1/clients/{client_id}/sites",
            json={"nombre": "Casa", "tiene_generacion": True, "capacidad_kwp": "5.50"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_201_CREATED, response.text
        site_id = response.json()["id"]

        leida = await client.get(f"/api/v1/sites/{site_id}", headers=admin_headers)
        assert leida.json()["tiene_generacion"] is True
        assert float(leida.json()["capacidad_kwp"]) == 5.5

    async def test_marcar_una_sede_existente_como_solo_consumo(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        client_id = await _create_client(client, admin_headers)
        creada = await client.post(
            f"/api/v1/clients/{client_id}/sites",
            json={"nombre": "Bodega"},
            headers=admin_headers,
        )
        site_id = creada.json()["id"]

        response = await client.patch(
            f"/api/v1/sites/{site_id}",
            json={"tiene_generacion": False},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK, response.text
        assert response.json()["tiene_generacion"] is False

    async def test_capacidad_no_positiva_rechazada(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        client_id = await _create_client(client, admin_headers)

        response = await client.post(
            f"/api/v1/clients/{client_id}/sites",
            json={"nombre": "Techo", "tiene_generacion": True, "capacidad_kwp": "0"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, (
            response.text
        )

    async def test_el_arbol_de_flota_lo_expone(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """Es el camino por el que ApiEMS se entera: el documento de flota."""
        client_id = await _create_client(client, admin_headers)
        await client.post(
            f"/api/v1/clients/{client_id}/sites",
            json={"nombre": "Planta", "tiene_generacion": True},
            headers=admin_headers,
        )

        response = await client.get(
            "/api/v1/fleet",
            params={"nivel": "sitios", "client_id": client_id},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK, response.text
        sede = response.json()["items"][0]["sites"][0]
        assert sede["tiene_generacion"] is True
