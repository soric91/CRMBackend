"""`GET /fleet/summary` — cuánto tiene instalado cada cliente.

El riesgo de esta consulta no es que falle: es que dé números plausibles y
equivocados. Contar varios niveles con joins encadenados multiplica las filas
entre sí —2 sedes por 3 gateways son 6 filas— y cada conteo sale inflado por
el tamaño de los otros. Nada revienta y los números parecen razonables, así
que estos tests usan cantidades distintas por nivel a propósito: con 1 de
cada cosa, un conteo multiplicado se ve idéntico a uno correcto.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from fastapi import status
from httpx import AsyncClient

from app.models import User
from tests.conftest import auth_header

Login = Callable[..., Awaitable[str]]

SUMMARY = "/api/v1/fleet/summary"


@pytest.fixture
async def admin_headers(admin_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(admin_user.email))


async def _crear_cliente(
    client: AsyncClient,
    headers: dict[str, str],
    empresa: str,
    *,
    contacto: str | None = None,
) -> str:
    cuerpo: dict[str, str] = {"nombre_empresa": empresa}
    if contacto is not None:
        cuerpo["contacto_email"] = contacto
    created = await client.post("/api/v1/clients", json=cuerpo, headers=headers)
    assert created.status_code == status.HTTP_201_CREATED, created.text
    return str(created.json()["id"])


async def _crear_sede(
    client: AsyncClient, headers: dict[str, str], client_id: str, nombre: str
) -> str:
    site = await client.post(
        f"/api/v1/clients/{client_id}/sites",
        json={"nombre": nombre},
        headers=headers,
    )
    return str(site.json()["id"])


async def _crear_gateway(
    client: AsyncClient, headers: dict[str, str], site_id: str, serie: str
) -> str:
    gateway = await client.post(
        f"/api/v1/sites/{site_id}/gateways",
        json={"numero_serie": serie},
        headers=headers,
    )
    return str(gateway.json()["id"])


async def _crear_equipo(
    client: AsyncClient,
    headers: dict[str, str],
    gateway_id: str,
    nombre: str,
    modbus_id: int,
) -> str:
    equipment = await client.post(
        f"/api/v1/gateways/{gateway_id}/equipment",
        json={
            "tipo": "analizador",
            "modbus_id": modbus_id,
            "nombre_dispositivo": nombre,
            "device_type": "CT_Meter",
            "marca": "chint",
        },
        headers=headers,
    )
    assert equipment.status_code == status.HTTP_201_CREATED, equipment.text
    return str(equipment.json()["id"])


async def _crear_variable(
    client: AsyncClient, headers: dict[str, str], equipment_id: str, nombre: str
) -> None:
    response = await client.post(
        f"/api/v1/equipment/{equipment_id}/variables",
        json={
            "nombre": nombre,
            "registro_modbus": "2006",
            "notacion_registro": "hex",
            "tipo_dato": "float32",
        },
        headers=headers,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text


@pytest.fixture
async def desigual(
    client: AsyncClient, admin_headers: dict[str, str]
) -> dict[str, Any]:
    """Una empresa con cantidades distintas en cada nivel.

    2 sedes, 3 gateways, 4 equipos, 5 variables. Ningún número es múltiplo
    accidental de otro, así que un conteo inflado por un join no puede
    coincidir con el correcto por casualidad.
    """
    client_id = await _crear_cliente(client, admin_headers, "Empresa Desigual")
    sede_a = await _crear_sede(client, admin_headers, client_id, "Planta A")
    sede_b = await _crear_sede(client, admin_headers, client_id, "Planta B")

    gw1 = await _crear_gateway(client, admin_headers, sede_a, "GW-1")
    gw2 = await _crear_gateway(client, admin_headers, sede_a, "GW-2")
    gw3 = await _crear_gateway(client, admin_headers, sede_b, "GW-3")

    eq1 = await _crear_equipo(client, admin_headers, gw1, "M1", 11)
    eq2 = await _crear_equipo(client, admin_headers, gw1, "M2", 12)
    eq3 = await _crear_equipo(client, admin_headers, gw2, "M3", 13)
    eq4 = await _crear_equipo(client, admin_headers, gw3, "M4", 14)

    for nombre in ("PhV_phsA", "PhV_phsB"):
        await _crear_variable(client, admin_headers, eq1, nombre)
    await _crear_variable(client, admin_headers, eq2, "A_phsA")
    await _crear_variable(client, admin_headers, eq3, "TotW")
    await _crear_variable(client, admin_headers, eq4, "TotPF")

    return {"client_id": client_id, "gateways": [gw1, gw2, gw3]}


async def _resumen(
    client: AsyncClient, headers: dict[str, str], nombre: str
) -> dict[str, Any]:
    response = await client.get(SUMMARY, headers=headers)
    assert response.status_code == status.HTTP_200_OK, response.text
    fila = next(
        item for item in response.json()["items"] if item["nombre_empresa"] == nombre
    )
    return dict(fila)


class TestTheCountsAreNotMultipliedByEachOther:
    async def test_each_level_is_counted_on_its_own(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        desigual: dict[str, Any],
    ) -> None:
        fila = await _resumen(client, admin_headers, "Empresa Desigual")

        assert fila["sedes"] == 2
        assert fila["gateways"] == 3
        assert fila["equipos"] == 4
        assert fila["variables"] == 5

    async def test_an_empty_client_counts_zero_and_still_appears(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """Una empresa recién dada de alta tiene que verse en la lista: es
        justamente la que hay que terminar de configurar. Un `JOIN` interno la
        habría hecho desaparecer."""
        await _crear_cliente(client, admin_headers, "Empresa Vacia")

        fila = await _resumen(client, admin_headers, "Empresa Vacia")

        assert fila["sedes"] == 0
        assert fila["gateways"] == 0
        assert fila["equipos"] == 0
        assert fila["variables"] == 0


class TestWhatIsReachable:
    async def test_a_gateway_that_never_reported_is_not_online(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        desigual: dict[str, Any],
    ) -> None:
        """Los tres se acaban de crear y ninguno se conectó nunca."""
        fila = await _resumen(client, admin_headers, "Empresa Desigual")

        assert fila["gateways_en_linea"] == 0
        assert fila["ultima_conexion"] is None


class TestItIsScopedLikeTheTree:
    async def test_a_client_only_sees_itself(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        desigual: dict[str, Any],
        cliente_user: User,
        authenticate_monitor: Login,
    ) -> None:
        """Saber cuántos gateways tiene otra empresa ya es información de otra
        empresa, aunque sea un número y no una fila.

        `desigual` existe justamente para que haya algo que filtrar: sin otra
        empresa cargada, este test pasaría con cualquier implementación.
        """
        token = await authenticate_monitor(cliente_user.email)

        response = await client.get(SUMMARY, headers=auth_header(token))

        assert response.status_code == status.HTTP_200_OK, response.text
        nombres = [item["nombre_empresa"] for item in response.json()["items"]]
        assert nombres == ["Industrias Andinas"]

    async def test_without_a_token_it_is_rejected(self, client: AsyncClient) -> None:
        assert (await client.get(SUMMARY)).status_code == status.HTTP_401_UNAUTHORIZED
