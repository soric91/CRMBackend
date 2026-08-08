"""La configuración compartida de la flota.

Esta tabla es la única del sistema que guarda secretos **recuperables**: la
contraseña del broker tiene que poder mostrarse y servirse a un gateway, y un
hash no se deshace. Todo lo demás —`credential_hash`, las cuentas de
servicio— se guarda hasheado y se muestra una sola vez.

Entonces lo que se prueba acá es lo que compensa esa diferencia: que el valor
no viaje en un listado, que en la base no esté en claro, y que nadie que no
sea administrador lo pueda tocar.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from tests.conftest import TEST_PASSWORD, auth_header

Login = Callable[..., Awaitable[str]]

RUTA = "/api/v1/platform-settings"
SECRETO = "una-clave-de-broker-larga"


@pytest.fixture
async def admin_headers(admin_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(admin_user.email))


async def _crear(
    client: AsyncClient,
    headers: dict[str, str],
    clave: str,
    valor: str = "",
    *,
    es_secreto: bool = False,
) -> dict[str, Any]:
    response = await client.post(
        RUTA,
        json={"clave": clave, "valor": valor, "es_secreto": es_secreto},
        headers=headers,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    body: dict[str, Any] = response.json()
    return body


class TestSecretsDoNotTravelInListings:
    async def test_a_secret_is_masked_in_the_list(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """Ver un secreto tiene que ser un acto deliberado, no algo que pase
        por abrir la pantalla."""
        await _crear(client, admin_headers, "MQTT_PASSWORD", SECRETO, es_secreto=True)

        listado = (await client.get(RUTA, headers=admin_headers)).json()
        fila = next(item for item in listado if item["clave"] == "MQTT_PASSWORD")

        assert fila["valor"] is None
        assert fila["tiene_valor"] is True

    async def test_an_empty_secret_is_told_apart_from_a_masked_one(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """Un secreto sin cargar es un gateway que no va a poder conectar. Si
        se viera igual que uno tapado, la carga pendiente parecería hecha."""
        await _crear(client, admin_headers, "MQTT_PASSWORD", "", es_secreto=True)

        listado = (await client.get(RUTA, headers=admin_headers)).json()
        fila = next(item for item in listado if item["clave"] == "MQTT_PASSWORD")

        assert fila["valor"] is None
        assert fila["tiene_valor"] is False

    async def test_a_value_that_is_not_secret_travels(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """Tapar `MQTT_PORT=8883` no protege nada y hace la pantalla inútil."""
        await _crear(client, admin_headers, "MQTT_PORT", "8883")

        listado = (await client.get(RUTA, headers=admin_headers)).json()
        fila = next(item for item in listado if item["clave"] == "MQTT_PORT")

        assert fila["valor"] == "8883"

    async def test_revealing_returns_the_real_value(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        await _crear(client, admin_headers, "MQTT_PASSWORD", SECRETO, es_secreto=True)

        response = await client.get(
            f"{RUTA}/MQTT_PASSWORD/reveal", headers=admin_headers
        )

        assert response.status_code == status.HTTP_200_OK, response.text
        assert response.json()["valor"] == SECRETO


class TestSecretsAreNotStoredInClear:
    async def test_the_row_does_not_contain_the_plaintext(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """Lo que separa esto de guardar contraseñas en claro.

        Se lee la fila cruda, sin pasar por el servicio: si el texto original
        apareciera ahí, un volcado de la base bastaría para leerlo.
        """
        await _crear(client, admin_headers, "MQTT_PASSWORD", SECRETO, es_secreto=True)

        guardado = await db_session.scalar(
            text("SELECT valor FROM platform_settings WHERE clave = 'MQTT_PASSWORD'")
        )

        assert guardado is not None
        assert SECRETO not in guardado

    async def test_marking_an_existing_value_as_secret_encrypts_it(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """Marcar como secreta una fila que quedó en claro no la protege de
        nada si el valor no se reescribe."""
        await _crear(client, admin_headers, "MQTT_PASSWORD", SECRETO)

        await client.patch(
            f"{RUTA}/MQTT_PASSWORD",
            json={"es_secreto": True},
            headers=admin_headers,
        )

        guardado = await db_session.scalar(
            text("SELECT valor FROM platform_settings WHERE clave = 'MQTT_PASSWORD'")
        )
        assert guardado is not None
        assert SECRETO not in guardado
        revelado = await client.get(
            f"{RUTA}/MQTT_PASSWORD/reveal", headers=admin_headers
        )
        assert revelado.json()["valor"] == SECRETO


class TestAddingVariables:
    async def test_a_new_variable_can_be_added(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        creada = await _crear(client, admin_headers, "MODBUS_TIMEOUT_MS", "3000")

        assert creada["clave"] == "MODBUS_TIMEOUT_MS"
        assert creada["valor"] == "3000"

    @pytest.mark.parametrize(
        "clave", ["mqtt_host", "MQTT HOST", "MQTT-HOST", "2FAST", "MQTT.HOST"]
    )
    async def test_a_name_a_shell_cannot_assign_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], clave: str
    ) -> None:
        """El valor termina en un archivo que un shell interpreta."""
        response = await client.post(
            RUTA, json={"clave": clave, "valor": "x"}, headers=admin_headers
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_a_system_variable_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """`PATH` tiene la forma correcta y aun así no se puede usar:
        cambiarlo afecta al proceso, no a la aplicación."""
        response = await client.post(
            RUTA, json={"clave": "PATH", "valor": "un-valor"}, headers=admin_headers
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_a_repeated_name_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        await _crear(client, admin_headers, "MODBUS_TIMEOUT_MS", "3000")

        response = await client.post(
            RUTA,
            json={"clave": "MODBUS_TIMEOUT_MS", "valor": "5000"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    async def test_a_variable_can_be_deleted(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        await _crear(client, admin_headers, "MODBUS_TIMEOUT_MS", "3000")

        borrado = await client.delete(
            f"{RUTA}/MODBUS_TIMEOUT_MS", headers=admin_headers
        )

        assert borrado.status_code == status.HTTP_204_NO_CONTENT
        listado = (await client.get(RUTA, headers=admin_headers)).json()
        assert all(item["clave"] != "MODBUS_TIMEOUT_MS" for item in listado)


class TestOnlyAdministrators:
    async def test_a_client_cannot_read_them(
        self,
        client: AsyncClient,
        cliente_user: User,
        authenticate_monitor: Login,
    ) -> None:
        """Acá está la contraseña del broker de toda la flota."""
        token = await authenticate_monitor(cliente_user.email)

        response = await client.get(RUTA, headers=auth_header(token))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_a_tecnico_cannot_edit_them(
        self, client: AsyncClient, tecnico_user: User, authenticate: Login
    ) -> None:
        """Quien pueda editarlas apunta toda la flota a otro broker."""
        token = await authenticate(tecnico_user.email, TEST_PASSWORD)

        response = await client.post(
            RUTA, json={"clave": "MQTT_HOST", "valor": "el-mio"},
            headers=auth_header(token),
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_without_a_token_it_is_rejected(self, client: AsyncClient) -> None:
        assert (await client.get(RUTA)).status_code == status.HTTP_401_UNAUTHORIZED


class TestVariablesTheCrmDoesNotOwn:
    """Las que existen acá solo para que su nombre viaje.

    Un equipo que pide su configuración tiene que recibir el archivo completo,
    incluidas las líneas que él mismo llena. Si el CRM le mandara solo las que
    conoce, el firmware tendría que mantener una segunda lista de qué más hace
    falta — y esa lista se desactualiza la primera vez que alguien agrega una
    variable desde el panel.
    """

    async def _sembrar(
        self,
        db_session: AsyncSession,
        clave: str,
        origen: str,
        *,
        es_secreto: bool = False,
    ) -> None:
        await db_session.execute(
            text(
                "INSERT INTO platform_settings "
                "(id, clave, valor, es_secreto, origen, descripcion, "
                " created_at, updated_at) "
                "VALUES (:id, :clave, '', :es_secreto, :origen, '', "
                " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "id": str(uuid.uuid4()),
                "clave": clave,
                "origen": origen,
                "es_secreto": es_secreto,
            },
        )
        await db_session.flush()

    async def test_the_origin_travels_so_the_panel_can_tell_them_apart(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        await self._sembrar(
            db_session, "INFLUXDB_TOKEN", "equipo", es_secreto=True
        )

        listado = (await client.get(RUTA, headers=admin_headers)).json()
        fila = next(item for item in listado if item["clave"] == "INFLUXDB_TOKEN")

        assert fila["origen"] == "equipo"
        assert fila["valor"] is None
        assert fila["tiene_valor"] is False

    async def test_a_value_the_device_generates_cannot_be_edited(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """Guardar un valor que nadie va a leer es la peor configuración: la
        que parece aplicada."""
        await self._sembrar(db_session, "INFLUXDB_TOKEN", "equipo")

        response = await client.patch(
            f"{RUTA}/INFLUXDB_TOKEN", json={"valor": "inventado"}, headers=admin_headers
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    async def test_an_identity_value_cannot_be_edited_either(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """`GATEWAY_UUID` sale de la ficha del gateway. Si se pudiera escribir
        acá habría dos lugares afirmando cuál es, y ninguna forma de saber
        cuál manda cuando discrepen."""
        await self._sembrar(db_session, "GATEWAY_UUID", "identidad")

        response = await client.patch(
            f"{RUTA}/GATEWAY_UUID", json={"valor": "otro-uuid"}, headers=admin_headers
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    async def test_neither_can_it_be_deleted(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """Borrarla sacaría su nombre de la configuración que recibe el
        equipo, que es justamente para lo que está."""
        await self._sembrar(db_session, "GATEWAY_UUID", "identidad")

        response = await client.delete(f"{RUTA}/GATEWAY_UUID", headers=admin_headers)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    async def test_a_platform_value_is_still_editable(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """El guardia no aplica a las normales."""
        await _crear(client, admin_headers, "MQTT_HOST", "el-viejo")

        response = await client.patch(
            f"{RUTA}/MQTT_HOST", json={"valor": "el-nuevo"}, headers=admin_headers
        )

        assert response.status_code == status.HTTP_200_OK, response.text
        assert response.json()["valor"] == "el-nuevo"
