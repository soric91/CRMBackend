"""Emitir el permiso con el que un gateway se configura solo.

Un token vivo entrega la configuración entera de un equipo: la contraseña del
broker, la credencial del gateway, las URLs. Así que lo que se prueba acá es
sobre todo lo que **no** se puede hacer con él.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_lookup_token
from app.models import User
from tests.conftest import TEST_PASSWORD, auth_header

Login = Callable[..., Awaitable[str]]

INSTALADOR = "https://ems.example/install.sh"


def _utc(valor: object) -> datetime:
    """La fecha como UTC, venga como texto o como datetime.

    SQLite las devuelve en texto y sin zona; PostgreSQL, como `datetime` con
    zona. El test tiene que valer en los dos.
    """
    fecha = valor if isinstance(valor, datetime) else datetime.fromisoformat(str(valor))
    return fecha if fecha.tzinfo else fecha.replace(tzinfo=UTC)


@pytest.fixture
async def admin_headers(admin_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(admin_user.email))


@pytest.fixture
async def instalador(client: AsyncClient, admin_headers: dict[str, str]) -> None:
    """La URL del instalador, que la semilla real trae por migración."""
    response = await client.post(
        "/api/v1/platform-settings",
        json={"clave": "GATEWAY_INSTALLER_URL", "valor": INSTALADOR},
        headers=admin_headers,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text


@pytest.fixture
async def gateway_id(client: AsyncClient, admin_headers: dict[str, str]) -> str:
    creado = await client.post(
        "/api/v1/clients",
        json={"nombre_empresa": "Textiles del Sur"},
        headers=admin_headers,
    )
    site = await client.post(
        f"/api/v1/clients/{creado.json()['id']}/sites",
        json={"nombre": "Planta Norte"},
        headers=admin_headers,
    )
    gateway = await client.post(
        f"/api/v1/sites/{site.json()['id']}/gateways",
        json={"numero_serie": "GW-0042"},
        headers=admin_headers,
    )
    assert gateway.status_code == status.HTTP_201_CREATED, gateway.text
    return str(gateway.json()["id"])


async def _emitir(
    client: AsyncClient, headers: dict[str, str], gateway_id: str
) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/gateways/{gateway_id}/enrollment-token", headers=headers
    )
    body: dict[str, Any] = response.json()
    body["_status"] = response.status_code
    return body


class TestWhatItGivesBack:
    async def test_it_returns_the_whole_command(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        gateway_id: str,
        instalador: None,
    ) -> None:
        """Se entrega armado y no solo el token: rearmarlo a mano en un chat
        es una oportunidad de equivocarse — un espacio de más, la URL vieja."""
        emitido = await _emitir(client, admin_headers, gateway_id)

        assert emitido["_status"] == status.HTTP_201_CREATED, emitido
        assert emitido["comando"] == (
            f"curl -fsSL {INSTALADOR} | sudo EMS_TOKEN={emitido['token']} bash"
        )

    async def test_the_token_is_not_in_the_url(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        gateway_id: str,
        instalador: None,
    ) -> None:
        """Una query string queda escrita en el log de acceso del servidor y
        en el del proxy. Es el mismo error que ya corregimos en el WebSocket."""
        emitido = await _emitir(client, admin_headers, gateway_id)

        antes_del_pipe = emitido["comando"].split("|")[0]
        assert emitido["token"] not in antes_del_pipe

    async def test_the_url_comes_from_the_settings(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        gateway_id: str,
        instalador: None,
    ) -> None:
        """El día que cambie el dominio, el comando cambia solo."""
        await client.patch(
            "/api/v1/platform-settings/GATEWAY_INSTALLER_URL",
            json={"valor": "https://otro.example/install.sh"},
            headers=admin_headers,
        )

        emitido = await _emitir(client, admin_headers, gateway_id)

        assert "https://otro.example/install.sh" in emitido["comando"]


class TestTheTokenIsNotRecoverable:
    async def test_only_the_hash_is_stored(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        gateway_id: str,
        instalador: None,
        db_session: AsyncSession,
    ) -> None:
        """Un volcado de la base no debería alcanzar para enrolar un equipo."""
        emitido = await _emitir(client, admin_headers, gateway_id)

        guardado = await db_session.scalar(
            text("SELECT token_hash FROM enrollment_tokens LIMIT 1")
        )

        assert guardado is not None
        assert emitido["token"] not in guardado
        assert guardado == hash_lookup_token(emitido["token"])


class TestIssuingAgainRevokesThePrevious:
    async def test_the_earlier_one_is_expired(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        gateway_id: str,
        instalador: None,
        db_session: AsyncSession,
    ) -> None:
        """Sin esto, cada intento fallido deja un token vivo dando vueltas en
        un chat o un papel, y todos siguen sirviendo hasta que expiran."""
        primero = await _emitir(client, admin_headers, gateway_id)
        segundo = await _emitir(client, admin_headers, gateway_id)

        # Se compara contra el reloj, no entre los dos tokens: el segundo se
        # emite después, así que su vencimiento siempre es mayor que el del
        # primero — con invalidación y sin ella. Una aserción entre ambos pasa
        # aunque no se invalide nada.
        filas = (
            await db_session.execute(
                text("SELECT token_hash, expira_en FROM enrollment_tokens")
            )
        ).all()
        por_hash = {fila[0]: _utc(fila[1]) for fila in filas}
        ahora = datetime.now(UTC)

        assert por_hash[hash_lookup_token(primero["token"])] <= ahora
        assert por_hash[hash_lookup_token(segundo["token"])] > ahora


class TestWhoCanIssueIt:
    async def test_a_tecnico_can(
        self,
        client: AsyncClient,
        tecnico_user: User,
        authenticate: Login,
        gateway_id: str,
        instalador: None,
    ) -> None:
        """Quien instala equipos es quien los enrola. El permiso es el mismo
        que ya tiene para emitir la credencial de un gateway."""
        token = await authenticate(tecnico_user.email, TEST_PASSWORD)

        emitido = await _emitir(client, auth_header(token), gateway_id)

        assert emitido["_status"] == status.HTTP_201_CREATED, emitido

    async def test_a_client_cannot(
        self,
        client: AsyncClient,
        cliente_user: User,
        authenticate_monitor: Login,
        gateway_id: str,
        instalador: None,
    ) -> None:
        """403 y no 404, y está bien.

        El rechazo es por rol —un `cliente` nunca escribe nada— y ocurre antes
        de mirar el gateway, así que no revela si existe. El 404 es para el
        caso contrario: alguien que sí puede escribir pero no debería ver ese
        equipo, donde confirmar su existencia ya sería contar algo ajeno.
        """
        token = await authenticate_monitor(cliente_user.email)

        emitido = await _emitir(client, auth_header(token), gateway_id)

        assert emitido["_status"] == status.HTTP_403_FORBIDDEN

    async def test_without_a_token_it_is_rejected(
        self, client: AsyncClient, gateway_id: str
    ) -> None:
        response = await client.post(
            f"/api/v1/gateways/{gateway_id}/enrollment-token"
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


ENROLL = "/api/v1/gateways/enroll"


@pytest.fixture
async def release(client: AsyncClient, admin_headers: dict[str, str]) -> None:
    """La versión elegida, que la semilla real trae vacía."""
    for clave, valor in (
        ("GATEWAY_RELEASE_BASE_URL", "https://ems.example/rel"),
        ("GATEWAY_RELEASE_VERSION", "v2.4.1"),
        ("GATEWAY_RELEASE_SHA256", "9f2ab7"),
    ):
        await client.post(
            "/api/v1/platform-settings",
            json={"clave": clave, "valor": valor},
            headers=admin_headers,
        )


async def _canjear(client: AsyncClient, token: str) -> dict[str, Any]:
    response = await client.post(ENROLL, headers=auth_header(token))
    body: dict[str, Any] = response.json()
    body["_status"] = response.status_code
    return body


class TestExchanging:
    async def test_it_returns_the_whole_env(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        gateway_id: str,
        instalador: None,
        release: None,
    ) -> None:
        """Incluida la URL del instalador y todo lo demás: el equipo recibe el
        archivo completo, no un recorte que el firmware tenga que completar."""
        emitido = await _emitir(client, admin_headers, gateway_id)

        canjeado = await _canjear(client, str(emitido["token"]))

        assert canjeado["_status"] == status.HTTP_200_OK, canjeado
        claves = {entrada["clave"] for entrada in canjeado["env"]}
        assert "GATEWAY_UUID" in claves
        assert "GATEWAY_CREDENTIAL" in claves

    async def test_the_device_secrets_travel_empty(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        gateway_id: str,
        instalador: None,
        release: None,
        db_session: AsyncSession,
    ) -> None:
        """Solo el nombre. Si el CRM mandara un valor, el equipo escribiría un
        secreto que otro conoce."""
        await client.post(
            "/api/v1/platform-settings",
            json={"clave": "INFLUXDB_TOKEN", "valor": "", "es_secreto": True},
            headers=admin_headers,
        )
        await db_session.execute(
            text(
                "UPDATE platform_settings "
                "SET origen='equipo', valor='no-deberia-viajar' "
                "WHERE clave='INFLUXDB_TOKEN'"
            )
        )
        await db_session.flush()
        emitido = await _emitir(client, admin_headers, gateway_id)

        canjeado = await _canjear(client, str(emitido["token"]))

        entrada = next(
            e for e in canjeado["env"] if e["clave"] == "INFLUXDB_TOKEN"
        )
        assert entrada["origen"] == "equipo"
        assert entrada["valor"] == ""
        assert "no-deberia-viajar" not in str(canjeado)

    async def test_the_client_id_is_generated_by_the_device(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        gateway_id: str,
        instalador: None,
        release: None,
        db_session: AsyncSession,
    ) -> None:
        """Lo genera el equipo, como los secretos de su InfluxDB local.

        El CRM podría derivarlo del uuid —y así los logs del broker dirían qué
        equipo es cada conexión— pero eso movería al CRM una decisión que hoy
        vive en el dispositivo. Si el CRM mandara un valor, el equipo lo
        escribiría en vez de generar el suyo.
        """
        await client.post(
            "/api/v1/platform-settings",
            json={"clave": "MQTT_CLIENT_ID", "valor": ""},
            headers=admin_headers,
        )
        await db_session.execute(
            text(
                "UPDATE platform_settings SET origen='equipo' "
                "WHERE clave='MQTT_CLIENT_ID'"
            )
        )
        await db_session.flush()
        emitido = await _emitir(client, admin_headers, gateway_id)

        canjeado = await _canjear(client, str(emitido["token"]))

        entrada = next(
            e for e in canjeado["env"] if e["clave"] == "MQTT_CLIENT_ID"
        )
        assert entrada["origen"] == "equipo"
        assert entrada["valor"] == ""

    async def test_the_credential_it_hands_over_actually_works(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        gateway_id: str,
        instalador: None,
        release: None,
    ) -> None:
        """La prueba de que el canje sirve para algo.

        Se verifica usándola contra el endpoint real del firmware, no mirando
        que la fila de la base cambió: una credencial guardada con otro
        formato de hash también «cambia» y no serviría para nada.
        """
        emitido = await _emitir(client, admin_headers, gateway_id)
        canjeado = await _canjear(client, str(emitido["token"]))
        por_clave = {e["clave"]: e["valor"] for e in canjeado["env"]}

        token = await client.post(
            "/api/v1/gateway/token",
            json={
                "gateway_uuid": por_clave["GATEWAY_UUID"],
                "credential": por_clave["GATEWAY_CREDENTIAL"],
            },
        )

        assert token.status_code == status.HTTP_200_OK, token.text

    async def test_the_previous_credential_stops_working(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        gateway_id: str,
        instalador: None,
        release: None,
    ) -> None:
        """Enrolar rota, y rotar revoca. Si la vieja siguiera sirviendo, un
        equipo robado seguiría publicando después de reemplazarlo."""
        vieja = await client.post(
            f"/api/v1/gateways/{gateway_id}/credential", headers=admin_headers
        )
        emitido = await _emitir(client, admin_headers, gateway_id)
        canjeado = await _canjear(client, str(emitido["token"]))

        token = await client.post(
            "/api/v1/gateway/token",
            json={
                "gateway_uuid": canjeado["gateway_uuid"],
                "credential": vieja.json()["credential"],
            },
        )

        assert token.status_code == status.HTTP_401_UNAUTHORIZED


class TestATokenIsSpent:
    async def test_it_cannot_be_used_twice(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        gateway_id: str,
        instalador: None,
        release: None,
    ) -> None:
        """Es toda la garantía del diseño. Sin esto, un token reenviado por
        chat sirve para enrolar cualquier cantidad de equipos."""
        emitido = await _emitir(client, admin_headers, gateway_id)
        await _canjear(client, str(emitido["token"]))

        segundo = await _canjear(client, str(emitido["token"]))

        assert segundo["_status"] == status.HTTP_401_UNAUTHORIZED

    async def test_an_invented_token_is_rejected(self, client: AsyncClient) -> None:
        assert (await _canjear(client, "no-existe"))[
            "_status"
        ] == status.HTTP_401_UNAUTHORIZED

    async def test_an_expired_token_is_rejected(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        gateway_id: str,
        instalador: None,
        release: None,
        db_session: AsyncSession,
    ) -> None:
        emitido = await _emitir(client, admin_headers, gateway_id)
        await db_session.execute(
            text("UPDATE enrollment_tokens SET expira_en = :e"),
            {"e": datetime.now(UTC) - timedelta(minutes=1)},
        )
        await db_session.flush()

        canjeado = await _canjear(client, str(emitido["token"]))

        assert canjeado["_status"] == status.HTTP_401_UNAUTHORIZED

    async def test_every_rejection_says_the_same(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        gateway_id: str,
        instalador: None,
        release: None,
    ) -> None:
        """Distinguir «no existe» de «ya se usó» le diría a quien prueba
        tokens al azar cuándo acertó uno."""
        emitido = await _emitir(client, admin_headers, gateway_id)
        await _canjear(client, str(emitido["token"]))

        usado = await _canjear(client, str(emitido["token"]))
        inventado = await _canjear(client, "no-existe")

        assert usado.get("detail") == inventado.get("detail")


class TestItIsOutsideTheNormalAuth:
    async def test_a_session_token_does_not_work_here(
        self,
        client: AsyncClient,
        admin_user: User,
        authenticate: Login,
        gateway_id: str,
        instalador: None,
        release: None,
    ) -> None:
        """El riesgo al revés del que importa, pero vale fijarlo: el token de
        un operador no es un token de enrolamiento."""
        sesion = await authenticate(admin_user.email)

        canjeado = await _canjear(client, sesion)

        assert canjeado["_status"] == status.HTTP_401_UNAUTHORIZED

    async def test_an_enrollment_token_opens_nothing_else(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        gateway_id: str,
        instalador: None,
        release: None,
    ) -> None:
        """Lo que de verdad importa: que este token no se cuele en el resto de
        la API. Si el endpoint colgara de la cadena de autenticación normal,
        pasaría a valer en todas las rutas."""
        emitido = await _emitir(client, admin_headers, gateway_id)
        cabecera = auth_header(str(emitido["token"]))

        respuestas = [
            await client.get("/api/v1/clients", headers=cabecera),
            await client.get("/api/v1/platform-settings", headers=cabecera),
            await client.get(f"/api/v1/gateways/{gateway_id}", headers=cabecera),
        ]

        assert all(r.status_code == status.HTTP_401_UNAUTHORIZED for r in respuestas)


class TestWithoutAChosenVersion:
    async def test_it_refuses_instead_of_installing_anything(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        gateway_id: str,
        instalador: None,
    ) -> None:
        """La semilla deja la versión vacía a propósito. Enrolar sin elegirla
        instalaría lo que hubiera, y el equipo quedaría con algo que nadie
        decidió."""
        for clave in ("GATEWAY_RELEASE_VERSION", "GATEWAY_RELEASE_SHA256"):
            await client.post(
                "/api/v1/platform-settings",
                json={"clave": clave, "valor": ""},
                headers=admin_headers,
            )
        await client.post(
            "/api/v1/platform-settings",
            json={"clave": "GATEWAY_RELEASE_BASE_URL", "valor": "https://x/rel"},
            headers=admin_headers,
        )
        emitido = await _emitir(client, admin_headers, gateway_id)

        canjeado = await _canjear(client, str(emitido["token"]))

        assert canjeado["_status"] == status.HTTP_400_BAD_REQUEST

    async def test_it_does_not_burn_the_token_first(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        gateway_id: str,
        instalador: None,
    ) -> None:
        """Fallar por configuración no debe costar el token.

        Canjear gasta el token y rota la credencial. Si la versión se
        comprobara después, una configuración incompleta dejaría el token
        quemado y al equipo con una credencial que nadie recibió — y el
        técnico en sitio sin nada que hacer salvo pedir otro token.
        """
        for clave in ("GATEWAY_RELEASE_VERSION", "GATEWAY_RELEASE_SHA256"):
            await client.post(
                "/api/v1/platform-settings",
                json={"clave": clave, "valor": ""},
                headers=admin_headers,
            )
        await client.post(
            "/api/v1/platform-settings",
            json={"clave": "GATEWAY_RELEASE_BASE_URL", "valor": "https://x/rel"},
            headers=admin_headers,
        )
        emitido = await _emitir(client, admin_headers, gateway_id)
        assert (await _canjear(client, str(emitido["token"])))[
            "_status"
        ] == status.HTTP_400_BAD_REQUEST

        # Se carga la versión que faltaba y se reintenta con el MISMO token.
        for clave, valor in (
            ("GATEWAY_RELEASE_VERSION", "v2.4.1"),
            ("GATEWAY_RELEASE_SHA256", "9f2ab7"),
        ):
            await client.patch(
                f"/api/v1/platform-settings/{clave}",
                json={"valor": valor},
                headers=admin_headers,
            )

        segundo = await _canjear(client, str(emitido["token"]))

        assert segundo["_status"] == status.HTTP_200_OK, segundo
