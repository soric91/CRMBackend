"""La superficie que el firmware usa para actualizarse.

Dos rutas: qué tengo que instalar, y qué pasó cuando lo intenté. Lo que se
prueba acá es que el CRM nunca ofrezca una orden que no se pueda cumplir —sin
versión, sin ventana, sin intentos o con la flota apagada— y que un acuse que
llega tarde o de otra versión no se anote como progreso.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import FirmwareUpdateState
from app.models import FirmwareRelease, Gateway, User
from tests.conftest import auth_header
from tests.factories import make_firmware_release

type Login = Callable[..., Awaitable[str]]

BASE_URL = "https://ems.example/rel"
VERSION = "v1.4.0"


@pytest.fixture
async def admin_headers(admin_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(admin_user.email))


@pytest.fixture
async def installation(
    client: AsyncClient, admin_headers: dict[str, str]
) -> dict[str, str]:
    """Una empresa con una sede y un gateway, como en el campo."""
    empresa = await client.post(
        "/api/v1/clients", json={"nombre_empresa": "Empresa"}, headers=admin_headers
    )
    sede = await client.post(
        f"/api/v1/clients/{empresa.json()['id']}/sites",
        json={"nombre": "Planta", "timezone": "America/Bogota"},
        headers=admin_headers,
    )
    gateway = await client.post(
        f"/api/v1/sites/{sede.json()['id']}/gateways",
        json={"numero_serie": "GW-1", "log_level": "INFO"},
        headers=admin_headers,
    )
    return {
        "gateway_id": gateway.json()["id"],
        "gateway_uuid": gateway.json()["uuid"],
    }


@pytest.fixture
async def gateway_headers(
    client: AsyncClient, admin_headers: dict[str, str], installation: dict[str, str]
) -> dict[str, str]:
    """El token corto con el que habla el equipo."""
    credential = await client.post(
        f"/api/v1/gateways/{installation['gateway_id']}/credential",
        headers=admin_headers,
    )
    token = await client.post(
        "/api/v1/gateway/token",
        json={
            "gateway_uuid": installation["gateway_uuid"],
            "credential": credential.json()["credential"],
        },
    )
    return auth_header(token.json()["access_token"])


async def _ajuste(
    client: AsyncClient, headers: dict[str, str], clave: str, valor: str
) -> None:
    response = await client.post(
        "/api/v1/platform-settings",
        json={"clave": clave, "valor": valor},
        headers=headers,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text


@pytest.fixture
async def flota_activa(client: AsyncClient, admin_headers: dict[str, str]) -> None:
    """Las filas que la migración siembra, con el interruptor encendido."""
    for clave, valor in (
        ("FIRMWARE_UPDATE_ACTIVO", "true"),
        ("FIRMWARE_UPDATE_HORA", "03:00"),
        ("FIRMWARE_UPDATE_VENTANA_MINUTOS", "120"),
        ("FIRMWARE_ROLLBACK_AUTO", "true"),
        ("GATEWAY_RELEASE_BASE_URL", BASE_URL),
    ):
        await _ajuste(client, admin_headers, clave, valor)


async def _gateway(session: AsyncSession, gateway_uuid: str) -> Gateway:
    result = await session.execute(
        select(Gateway).where(Gateway.uuid == uuid.UUID(gateway_uuid))
    )
    gateway = result.scalar_one()
    await session.refresh(gateway)
    return gateway


async def _programar(
    session: AsyncSession,
    gateway_uuid: str,
    *,
    version: str = VERSION,
    aplicar_desde: datetime | None = None,
    estado: FirmwareUpdateState = FirmwareUpdateState.PROGRAMADA,
    retirada: bool = False,
    **campos: object,
) -> FirmwareRelease:
    """Deja una actualización pedida, como la dejará el panel en F3."""
    release = make_firmware_release(version=version, notas="Arregla el watchdog")
    if retirada:
        release.retirado_en = datetime.now(UTC)
    session.add(release)
    await session.flush()

    gateway = await _gateway(session, gateway_uuid)
    gateway.firmware_objetivo_id = release.id
    gateway.firmware_estado = estado
    gateway.firmware_aplicar_desde = aplicar_desde or datetime.now(UTC)
    for campo, valor in campos.items():
        setattr(gateway, campo, valor)
    await session.flush()
    return release


class TestElInterruptorDeLaFlota:
    async def test_sin_configurar_nada_no_se_actualiza_nadie(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
    ) -> None:
        """Falla cerrado: una base recién migrada no reinicia equipos."""
        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware",
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_apagado_explicitamente_tampoco(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        for clave, valor in (
            ("FIRMWARE_UPDATE_ACTIVO", "false"),
            ("FIRMWARE_UPDATE_HORA", "03:00"),
            ("FIRMWARE_UPDATE_VENTANA_MINUTOS", "120"),
            ("GATEWAY_RELEASE_BASE_URL", BASE_URL),
        ):
            await _ajuste(client, admin_headers, clave, valor)
        await _programar(db_session, installation["gateway_uuid"])

        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware",
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_sin_saber_de_donde_bajar_el_paquete_no_se_ofrece_nada(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """Encendido pero sin `GATEWAY_RELEASE_BASE_URL`: la orden llevaría una
        dirección a ninguna parte."""
        for clave, valor in (
            ("FIRMWARE_UPDATE_ACTIVO", "true"),
            ("FIRMWARE_UPDATE_HORA", "03:00"),
            ("FIRMWARE_UPDATE_VENTANA_MINUTOS", "120"),
            ("GATEWAY_RELEASE_BASE_URL", ""),
        ):
            await _ajuste(client, admin_headers, clave, valor)
        await _programar(db_session, installation["gateway_uuid"])

        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware",
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_una_hora_ilegible_apaga_la_actualizacion(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """Antes que inventar una hora por omisión, no actualizar: un valor
        inventado reinicia equipos a una hora que nadie eligió."""
        for clave, valor in (
            ("FIRMWARE_UPDATE_ACTIVO", "true"),
            ("FIRMWARE_UPDATE_HORA", "a las tres"),
            ("FIRMWARE_UPDATE_VENTANA_MINUTOS", "120"),
            ("GATEWAY_RELEASE_BASE_URL", BASE_URL),
        ):
            await _ajuste(client, admin_headers, clave, valor)
        await _programar(db_session, installation["gateway_uuid"])

        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware",
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.usefixtures("flota_activa")
class TestQueTengoQueInstalar:
    async def test_sin_nada_pedido_no_hay_contenido(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
    ) -> None:
        """El caso común, en cada consulta: 204 y nada más."""
        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware",
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not response.content

    async def test_la_orden_lleva_el_paquete_y_como_verificarlo(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        release = await _programar(db_session, installation["gateway_uuid"])

        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware",
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        cuerpo = response.json()
        assert cuerpo["version"] == VERSION
        assert cuerpo["url"] == f"{BASE_URL}/gatewayEMS-{VERSION}.tar.gz"
        assert cuerpo["sha256"] == release.sha256
        assert cuerpo["ventana_minutos"] == 120
        assert cuerpo["rollback_auto"] is True
        assert cuerpo["intentos_restantes"] == 3
        assert cuerpo["notas"] == "Arregla el watchdog"

    async def test_una_ventana_vencida_se_recalcula_hacia_adelante(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """Un equipo que estuvo apagado una semana no puede quedarse esperando
        para siempre una ventana que ya no vuelve."""
        vencida = datetime.now(UTC) - timedelta(days=7)
        await _programar(
            db_session, installation["gateway_uuid"], aplicar_desde=vencida
        )

        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware",
            headers=gateway_headers,
        )

        desde = datetime.fromisoformat(response.json()["aplicar_desde"])
        assert desde > datetime.now(UTC)

    async def test_una_ventana_que_todavia_no_llego_se_respeta(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        futura = datetime.now(UTC) + timedelta(hours=5)
        await _programar(
            db_session, installation["gateway_uuid"], aplicar_desde=futura
        )

        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware",
            headers=gateway_headers,
        )

        assert datetime.fromisoformat(response.json()["aplicar_desde"]) == futura

    async def test_una_version_retirada_deja_de_ofrecerse(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """Y el equipo queda sin nada pedido, con el motivo escrito."""
        await _programar(db_session, installation["gateway_uuid"], retirada=True)

        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware",
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        gateway = await _gateway(db_session, installation["gateway_uuid"])
        assert gateway.firmware_estado is FirmwareUpdateState.SIN_PENDIENTE
        assert gateway.firmware_objetivo_id is None
        assert "retirada" in (gateway.firmware_error or "")

    async def test_gastados_los_intentos_se_deja_de_insistir(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """Tres noches fallando es un problema que otro reintento no arregla."""
        await _programar(
            db_session,
            installation["gateway_uuid"],
            estado=FirmwareUpdateState.FALLIDA,
            firmware_intentos=3,
        )

        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware",
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT

    async def test_un_intento_fallido_se_vuelve_a_ofrecer(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        await _programar(
            db_session,
            installation["gateway_uuid"],
            estado=FirmwareUpdateState.FALLIDA,
            firmware_intentos=1,
        )

        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware",
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["intentos_restantes"] == 2
        gateway = await _gateway(db_session, installation["gateway_uuid"])
        assert gateway.firmware_estado is FirmwareUpdateState.PROGRAMADA

    async def test_un_equipo_que_ya_corre_la_version_pedida_se_da_por_cerrado(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """El acuse se perdió, o alguien la instaló a mano. Sin esto el equipo
        quedaría 'aplicando' para siempre."""
        await _programar(
            db_session,
            installation["gateway_uuid"],
            estado=FirmwareUpdateState.APLICANDO,
            firmware_version="1.4.0",
        )

        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware",
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        gateway = await _gateway(db_session, installation["gateway_uuid"])
        assert gateway.firmware_estado is FirmwareUpdateState.APLICADA

    async def test_una_actualizacion_ya_aplicada_no_se_vuelve_a_ofrecer(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        await _programar(
            db_session,
            installation["gateway_uuid"],
            estado=FirmwareUpdateState.APLICADA,
        )

        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware",
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT

    async def test_un_fallo_viejo_se_reprograma_a_la_proxima_ventana(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """Falló anoche: se vuelve a intentar en la ventana que viene, no en
        el momento en que el equipo pregunta."""
        await _programar(
            db_session,
            installation["gateway_uuid"],
            estado=FirmwareUpdateState.FALLIDA,
            aplicar_desde=datetime.now(UTC) - timedelta(days=1),
            firmware_intentos=1,
        )

        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware",
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        assert datetime.fromisoformat(response.json()["aplicar_desde"]) > datetime.now(
            UTC
        )
        gateway = await _gateway(db_session, installation["gateway_uuid"])
        assert gateway.firmware_estado is FirmwareUpdateState.PROGRAMADA

    async def test_retirar_la_version_no_frena_a_un_equipo_que_ya_reinicia(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """En `aplicando` el paquete ya está en el disco y el equipo se está
        reiniciando: cancelar sería escribir algo que no va a pasar."""
        await _programar(
            db_session,
            installation["gateway_uuid"],
            estado=FirmwareUpdateState.APLICANDO,
            retirada=True,
        )

        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware",
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        gateway = await _gateway(db_session, installation["gateway_uuid"])
        assert gateway.firmware_estado is FirmwareUpdateState.APLICANDO

    async def test_una_version_que_ya_corre_sin_haber_empezado_no_se_cierra_sola(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """Programada y el equipo ya la tiene: no se marca como aplicada,
        porque nunca la aplicó. Queda a la vista en el panel."""
        await _programar(
            db_session,
            installation["gateway_uuid"],
            estado=FirmwareUpdateState.PROGRAMADA,
            firmware_version="1.4.0",
        )

        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware",
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        gateway = await _gateway(db_session, installation["gateway_uuid"])
        assert gateway.firmware_estado is FirmwareUpdateState.PROGRAMADA

    async def test_un_gateway_no_pregunta_por_otro(
        self,
        client: AsyncClient,
        gateway_headers: dict[str, str],
    ) -> None:
        """404 y no 403: confirmar que otro uuid existe sería contar la flota."""
        response = await client.get(
            f"/api/v1/gateway/{uuid.uuid4()}/firmware", headers=gateway_headers
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_sin_token_no_se_contesta(
        self, client: AsyncClient, installation: dict[str, str]
    ) -> None:
        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware"
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.usefixtures("flota_activa")
class TestElAcuseDelEquipo:
    async def test_empezar_a_descargar_gasta_un_intento(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        await _programar(db_session, installation["gateway_uuid"])

        response = await client.post(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware/ack",
            json={"estado": "descargando", "version": VERSION},
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["estado"] == "descargando"
        assert response.json()["intentos"] == 1
        assert response.json()["intentos_restantes"] == 2

    async def test_repetir_el_acuse_no_gasta_otro_intento(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """La red se corta y el equipo reintenta el acuse. Contarlo dos veces
        gastaría los tres intentos sin haber probado una sola vez."""
        await _programar(db_session, installation["gateway_uuid"])
        ruta = f"/api/v1/gateway/{installation['gateway_uuid']}/firmware/ack"
        cuerpo = {"estado": "descargando", "version": VERSION}

        await client.post(ruta, json=cuerpo, headers=gateway_headers)
        segundo = await client.post(ruta, json=cuerpo, headers=gateway_headers)

        assert segundo.status_code == status.HTTP_200_OK
        assert segundo.json()["intentos"] == 1

    async def test_el_recorrido_completo_deja_la_version_nueva_registrada(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        gateway = await _gateway(db_session, installation["gateway_uuid"])
        gateway.firmware_version = "1.3.0"
        await _programar(db_session, installation["gateway_uuid"])
        ruta = f"/api/v1/gateway/{installation['gateway_uuid']}/firmware/ack"

        for estado in ("descargando", "aplicando", "aplicada"):
            respuesta = await client.post(
                ruta,
                json={"estado": estado, "version": VERSION},
                headers=gateway_headers,
            )
            assert respuesta.status_code == status.HTTP_200_OK, respuesta.text

        assert respuesta.json()["version_actual"] == "1.4.0"
        actualizado = await _gateway(db_session, installation["gateway_uuid"])
        assert actualizado.firmware_version_anterior == "1.3.0"
        assert actualizado.firmware_error is None

    async def test_un_fallo_queda_escrito_con_su_motivo(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        await _programar(
            db_session,
            installation["gateway_uuid"],
            estado=FirmwareUpdateState.DESCARGANDO,
        )

        response = await client.post(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware/ack",
            json={
                "estado": "fallida",
                "version": VERSION,
                "error": "sha256 no coincide",
            },
            headers=gateway_headers,
        )

        assert response.json()["estado"] == "fallida"
        assert response.json()["error"] == "sha256 no coincide"

    async def test_un_fallo_sin_motivo_igual_dice_algo(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        await _programar(
            db_session,
            installation["gateway_uuid"],
            estado=FirmwareUpdateState.DESCARGANDO,
        )

        response = await client.post(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware/ack",
            json={"estado": "fallida", "version": VERSION},
            headers=gateway_headers,
        )

        assert response.json()["error"]

    async def test_un_acuse_de_otra_version_no_se_anota(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """Un acuse que llegó tarde, de una versión que ya se cambió, no puede
        contarse como progreso de la nueva."""
        await _programar(db_session, installation["gateway_uuid"])

        response = await client.post(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware/ack",
            json={"estado": "aplicada", "version": "v1.0.0"},
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    async def test_la_v_del_tag_no_hace_fallar_el_acuse(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """El equipo reporta `1.4.0` y el tag es `v1.4.0`: son la misma."""
        await _programar(db_session, installation["gateway_uuid"])

        response = await client.post(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware/ack",
            json={"estado": "descargando", "version": "1.4.0"},
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_200_OK

    async def test_no_se_puede_saltear_el_recorrido(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """Nadie aplica sin haber descargado."""
        await _programar(db_session, installation["gateway_uuid"])

        response = await client.post(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware/ack",
            json={"estado": "aplicada", "version": VERSION},
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    async def test_un_acuse_viejo_no_hace_retroceder_el_estado(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        await _programar(
            db_session,
            installation["gateway_uuid"],
            estado=FirmwareUpdateState.APLICANDO,
        )

        response = await client.post(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware/ack",
            json={"estado": "descargando", "version": VERSION},
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize("estado", ["sin_pendiente", "programada"])
    async def test_el_equipo_no_decide_lo_que_decide_el_crm(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
        estado: str,
    ) -> None:
        """Un equipo que pudiera declararse 'sin pendiente' estaría cancelando
        su propia actualización."""
        await _programar(db_session, installation["gateway_uuid"])

        response = await client.post(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware/ack",
            json={"estado": estado, "version": VERSION},
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_acusar_sin_nada_pedido_es_un_error(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
    ) -> None:
        response = await client.post(
            f"/api/v1/gateway/{installation['gateway_uuid']}/firmware/ack",
            json={"estado": "descargando", "version": VERSION},
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    async def test_un_gateway_no_acusa_por_otro(
        self, client: AsyncClient, gateway_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            f"/api/v1/gateway/{uuid.uuid4()}/firmware/ack",
            json={"estado": "descargando", "version": VERSION},
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestElHeartbeatAvisa:
    @pytest.mark.usefixtures("flota_activa")
    async def test_dice_que_hay_una_actualizacion_esperando(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """Así un equipo que solo hace heartbeat se entera igual, y lo sabe
        aunque el broker esté caído."""
        await _programar(db_session, installation["gateway_uuid"])

        response = await client.post(
            f"/api/v1/gateway/{installation['gateway_uuid']}/heartbeat",
            json={"firmware_version": "1.3.0"},
            headers=gateway_headers,
        )

        cuerpo = response.json()
        assert cuerpo["firmware_pendiente"] is True
        assert cuerpo["firmware_version_objetivo"] == VERSION
        assert cuerpo["firmware_aplicar_desde"] is not None

    @pytest.mark.usefixtures("flota_activa")
    async def test_sin_nada_pedido_lo_dice_igual(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
    ) -> None:
        response = await client.post(
            f"/api/v1/gateway/{installation['gateway_uuid']}/heartbeat",
            json={},
            headers=gateway_headers,
        )

        assert response.json()["firmware_pendiente"] is False

    async def test_con_la_flota_apagada_el_heartbeat_sigue_funcionando(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """El 403 de la actualización no puede volver invisible a un equipo."""
        await _programar(db_session, installation["gateway_uuid"])

        response = await client.post(
            f"/api/v1/gateway/{installation['gateway_uuid']}/heartbeat",
            json={},
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["firmware_pendiente"] is False
