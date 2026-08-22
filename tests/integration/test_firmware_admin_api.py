"""Publicar versiones y desplegarlas, desde el panel.

Lo que se prueba acá es lo que separa publicar de desplegar, y lo que un
despliegue tiene que contar: a quién no llegó y por qué. Un rollout que
contesta "listo" sobre una flota donde la mitad quedó afuera es peor que uno
que falla.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import status
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import FirmwareUpdateState
from app.models import Gateway, User
from tests.conftest import auth_header

type Login = Callable[..., Awaitable[str]]

SHA = "a" * 64
VERSION = "v1.4.0"
RUTA = "/api/v1/firmware"


@pytest.fixture
async def admin_headers(admin_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(admin_user.email))


@pytest.fixture
async def tecnico_headers(tecnico_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(tecnico_user.email))


@pytest.fixture
async def flota(client: AsyncClient, admin_headers: dict[str, str]) -> dict[str, str]:
    """Una empresa, dos sedes, un gateway con credencial en cada una."""
    for clave, valor in (
        ("FIRMWARE_UPDATE_ACTIVO", "true"),
        ("FIRMWARE_UPDATE_HORA", "03:00"),
        ("FIRMWARE_UPDATE_VENTANA_MINUTOS", "120"),
        ("GATEWAY_RELEASE_BASE_URL", "https://ems.example/rel"),
    ):
        await client.post(
            "/api/v1/platform-settings",
            json={"clave": clave, "valor": valor},
            headers=admin_headers,
        )

    empresa = await client.post(
        "/api/v1/clients", json={"nombre_empresa": "Empresa"}, headers=admin_headers
    )
    creado: dict[str, str] = {"client_id": empresa.json()["id"]}

    for indice, nombre in enumerate(("Planta Norte", "Planta Sur"), start=1):
        sede = await client.post(
            f"/api/v1/clients/{creado['client_id']}/sites",
            json={"nombre": nombre, "timezone": "America/Bogota"},
            headers=admin_headers,
        )
        gateway = await client.post(
            f"/api/v1/sites/{sede.json()['id']}/gateways",
            json={"numero_serie": f"GW-{indice}", "log_level": "INFO"},
            headers=admin_headers,
        )
        await client.post(
            f"/api/v1/gateways/{gateway.json()['id']}/credential",
            headers=admin_headers,
        )
        creado[f"site_{indice}"] = sede.json()["id"]
        creado[f"gateway_{indice}"] = gateway.json()["id"]
    return creado


async def _publicar(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    version: str = VERSION,
    **campos: object,
) -> dict[str, object]:
    response = await client.post(
        f"{RUTA}/releases",
        json={"version": version, "sha256": SHA, **campos},
        headers=headers,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    cuerpo: dict[str, object] = response.json()
    return cuerpo


async def _desplegar(
    client: AsyncClient, headers: dict[str, str], **destino: object
) -> Response:
    return await client.post(f"{RUTA}/rollouts", json=destino, headers=headers)


async def _gateway(session: AsyncSession, gateway_id: str) -> Gateway:
    result = await session.execute(
        select(Gateway).where(Gateway.id == uuid.UUID(gateway_id))
    )
    gateway = result.scalar_one()
    await session.refresh(gateway)
    return gateway


class TestPublicarUnaVersion:
    async def test_publicar_no_despliega_nada(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        flota: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """Se publica, se prueba en un equipo, y recién entonces se despliega."""
        await _publicar(client, admin_headers)

        gateway = await _gateway(db_session, flota["gateway_1"])
        assert gateway.firmware_estado is FirmwareUpdateState.SIN_PENDIENTE
        assert gateway.firmware_objetivo_id is None

    async def test_nace_en_beta(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """Una versión recién subida no puede quedar ofrecida a la flota
        entera por omisión."""
        cuerpo = await _publicar(client, admin_headers)

        assert cuerpo["canal"] == "beta"

    async def test_queda_registrado_quien_la_publico(
        self, client: AsyncClient, admin_headers: dict[str, str], admin_user: User
    ) -> None:
        cuerpo = await _publicar(client, admin_headers)

        assert cuerpo["publicado_por"] == str(admin_user.id)

    async def test_el_checksum_se_guarda_en_minusculas(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """Copiado de un `sha256sum` de Windows viene en mayúsculas, y es el
        mismo valor: normalizarlo evita un fallo de verificación absurdo."""
        cuerpo = await _publicar(client, admin_headers, sha256="A" * 64)

        assert cuerpo["sha256"] == "a" * 64

    async def test_la_misma_version_no_se_publica_dos_veces(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        await _publicar(client, admin_headers)

        response = await client.post(
            f"{RUTA}/releases",
            json={"version": VERSION, "sha256": "b" * 64},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    @pytest.mark.parametrize("version", ["latest", "1.4", "v1.4.0-rc1", ""])
    async def test_una_version_que_no_se_puede_comparar_no_entra(
        self, client: AsyncClient, admin_headers: dict[str, str], version: str
    ) -> None:
        """No habría forma de saber si un equipo ya la tiene."""
        response = await client.post(
            f"{RUTA}/releases",
            json={"version": version, "sha256": SHA},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    @pytest.mark.parametrize("sha256", ["a" * 63, "z" * 64, "", "no-es-un-hash"])
    async def test_un_checksum_que_no_lo_es_no_entra(
        self, client: AsyncClient, admin_headers: dict[str, str], sha256: str
    ) -> None:
        """Un valor mal pegado se descubriría después de bajar el paquete
        entero, por 4G, en una sede remota."""
        response = await client.post(
            f"{RUTA}/releases",
            json={"version": VERSION, "sha256": sha256},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_un_tecnico_no_elige_el_firmware_de_la_flota(
        self, client: AsyncClient, tecnico_headers: dict[str, str]
    ) -> None:
        """Mantiene dispositivos; decidir qué software corren no es ese trabajo."""
        response = await client.post(
            f"{RUTA}/releases",
            json={"version": VERSION, "sha256": SHA},
            headers=tecnico_headers,
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_un_cliente_no_ve_el_catalogo(
        self, client: AsyncClient, cliente_user: User, authenticate_monitor: Login
    ) -> None:
        headers = auth_header(await authenticate_monitor(cliente_user.email))

        response = await client.get(f"{RUTA}/releases", headers=headers)

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestElCatalogo:
    async def test_la_mas_nueva_primero(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        await _publicar(client, admin_headers, version="v1.3.0")
        await _publicar(client, admin_headers, version="v1.4.0")

        response = await client.get(f"{RUTA}/releases", headers=admin_headers)

        assert [item["version"] for item in response.json()] == ["v1.4.0", "v1.3.0"]

    async def test_dice_cuantos_equipos_la_tienen_pedida(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        flota: dict[str, str],
    ) -> None:
        """Retirar una a la que van tres equipos los deja sin nada que
        instalar, y eso hay que verlo antes."""
        release = await _publicar(client, admin_headers)
        await _desplegar(
            client,
            admin_headers,
            release_id=release["id"],
            client_id=flota["client_id"],
        )

        response = await client.get(f"{RUTA}/releases", headers=admin_headers)

        assert response.json()[0]["gateways_apuntando"] == 2

    async def test_un_tecnico_puede_mirarlo(
        self, client: AsyncClient, tecnico_headers: dict[str, str]
    ) -> None:
        """Ver qué versiones hay no cambia nada de la flota."""
        response = await client.get(f"{RUTA}/releases", headers=tecnico_headers)

        assert response.status_code == status.HTTP_200_OK


class TestRetirarUnaVersion:
    async def test_retirar_no_borra(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        release = await _publicar(client, admin_headers)

        response = await client.post(
            f"{RUTA}/releases/{release['id']}/retire", headers=admin_headers
        )

        assert response.json()["retirado_en"] is not None
        listado = await client.get(f"{RUTA}/releases", headers=admin_headers)
        assert len(listado.json()) == 1

    async def test_retirarla_dos_veces_no_cambia_la_fecha(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        release = await _publicar(client, admin_headers)
        ruta = f"{RUTA}/releases/{release['id']}/retire"

        primera = await client.post(ruta, headers=admin_headers)
        segunda = await client.post(ruta, headers=admin_headers)

        assert primera.json()["retirado_en"] == segunda.json()["retirado_en"]

    async def test_una_version_retirada_no_se_despliega(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        flota: dict[str, str],
    ) -> None:
        release = await _publicar(client, admin_headers)
        await client.post(
            f"{RUTA}/releases/{release['id']}/retire", headers=admin_headers
        )

        response = await _desplegar(
            client,
            admin_headers,
            release_id=release["id"],
            gateway_ids=[flota["gateway_1"]],
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    async def test_un_tecnico_no_retira(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        tecnico_headers: dict[str, str],
    ) -> None:
        release = await _publicar(client, admin_headers)

        response = await client.post(
            f"{RUTA}/releases/{release['id']}/retire", headers=tecnico_headers
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_una_version_que_no_existe(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            f"{RUTA}/releases/{uuid.uuid4()}/retire", headers=admin_headers
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestDesplegar:
    async def test_deja_la_actualizacion_pedida_en_la_proxima_ventana(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        flota: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        release = await _publicar(client, admin_headers)

        response = await _desplegar(
            client,
            admin_headers,
            release_id=release["id"],
            gateway_ids=[flota["gateway_1"]],
        )

        assert response.status_code == status.HTTP_201_CREATED, response.text
        cuerpo = response.json()
        assert cuerpo["version"] == VERSION
        assert cuerpo["flota_activa"] is True
        assert len(cuerpo["programados"]) == 1
        desde = datetime.fromisoformat(cuerpo["programados"][0]["aplicar_desde"])
        assert desde > datetime.now(UTC)

        gateway = await _gateway(db_session, flota["gateway_1"])
        assert gateway.firmware_estado is FirmwareUpdateState.PROGRAMADA

    async def test_ahora_no_espera_a_la_ventana(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        flota: dict[str, str],
    ) -> None:
        """Para una sede parada y un técnico esperando al lado del equipo."""
        release = await _publicar(client, admin_headers)

        response = await _desplegar(
            client,
            admin_headers,
            release_id=release["id"],
            gateway_ids=[flota["gateway_1"]],
            ahora=True,
        )

        programado = response.json()["programados"][0]
        desde = datetime.fromisoformat(programado["aplicar_desde"])
        assert desde - datetime.now(UTC) < timedelta(minutes=1)

    async def test_una_sede_entera(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        flota: dict[str, str],
    ) -> None:
        release = await _publicar(client, admin_headers)

        response = await _desplegar(
            client,
            admin_headers,
            release_id=release["id"],
            site_id=flota["site_1"],
        )

        assert [item["numero_serie"] for item in response.json()["programados"]] == [
            "GW-1"
        ]

    async def test_una_empresa_entera(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        flota: dict[str, str],
    ) -> None:
        release = await _publicar(client, admin_headers)

        response = await _desplegar(
            client,
            admin_headers,
            release_id=release["id"],
            client_id=flota["client_id"],
        )

        assert len(response.json()["programados"]) == 2

    async def test_avisa_cuando_es_volver_atras(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        flota: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """No lo impide —volver atrás es cómo se sale de una versión mala—
        pero no puede pasar sin que se vea."""
        gateway = await _gateway(db_session, flota["gateway_1"])
        gateway.firmware_version = "1.9.0"
        await db_session.flush()
        release = await _publicar(client, admin_headers)

        response = await _desplegar(
            client,
            admin_headers,
            release_id=release["id"],
            gateway_ids=[flota["gateway_1"]],
        )

        programado = response.json()["programados"][0]
        assert programado["descenso"] is True
        assert programado["version_anterior"] == "1.9.0"

    async def test_borra_los_intentos_del_despliegue_anterior(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        flota: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """Un equipo que gastó tres intentos con otra versión tiene que poder
        intentar ésta."""
        vieja = await _publicar(client, admin_headers, version="v1.3.0")
        await _desplegar(
            client,
            admin_headers,
            release_id=vieja["id"],
            gateway_ids=[flota["gateway_1"]],
        )
        gateway = await _gateway(db_session, flota["gateway_1"])
        gateway.firmware_intentos = 3
        gateway.firmware_estado = FirmwareUpdateState.FALLIDA
        gateway.firmware_error = "sha256 no coincide"
        await db_session.flush()

        nueva = await _publicar(client, admin_headers, version="v1.4.0")
        await _desplegar(
            client,
            admin_headers,
            release_id=nueva["id"],
            gateway_ids=[flota["gateway_1"]],
        )

        actualizado = await _gateway(db_session, flota["gateway_1"])
        assert actualizado.firmware_intentos == 0
        assert actualizado.firmware_error is None
        assert actualizado.firmware_estado is FirmwareUpdateState.PROGRAMADA

    async def test_con_la_flota_apagada_lo_dice(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        flota: dict[str, str],
    ) -> None:
        """La orden queda escrita, pero nadie va a bajarla hasta que se
        encienda. Callarlo dejaría un despliegue que parece hecho."""
        await client.patch(
            "/api/v1/platform-settings/FIRMWARE_UPDATE_ACTIVO",
            json={"valor": "false"},
            headers=admin_headers,
        )
        release = await _publicar(client, admin_headers)

        response = await _desplegar(
            client,
            admin_headers,
            release_id=release["id"],
            gateway_ids=[flota["gateway_1"]],
        )

        assert response.json()["flota_activa"] is False
        assert len(response.json()["programados"]) == 1


class TestAQuienNoSeLePidio:
    async def test_al_que_ya_la_tiene(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        flota: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        gateway = await _gateway(db_session, flota["gateway_1"])
        gateway.firmware_version = "1.4.0"
        await db_session.flush()
        release = await _publicar(client, admin_headers)

        response = await _desplegar(
            client,
            admin_headers,
            release_id=release["id"],
            gateway_ids=[flota["gateway_1"]],
        )

        assert response.json()["programados"] == []
        assert "Ya está corriendo" in response.json()["omitidos"][0]["motivo"]

    async def test_al_que_no_tiene_credencial(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        flota: dict[str, str],
    ) -> None:
        """Sin credencial no puede pedir el paquete: pedírselo sería anotar un
        despliegue que nunca va a ocurrir."""
        await client.delete(
            f"/api/v1/gateways/{flota['gateway_1']}/credential", headers=admin_headers
        )
        release = await _publicar(client, admin_headers)

        response = await _desplegar(
            client,
            admin_headers,
            release_id=release["id"],
            gateway_ids=[flota["gateway_1"]],
        )

        assert "credencial" in response.json()["omitidos"][0]["motivo"]

    async def test_al_que_se_esta_reiniciando(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        flota: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        vieja = await _publicar(client, admin_headers, version="v1.3.0")
        await _desplegar(
            client,
            admin_headers,
            release_id=vieja["id"],
            gateway_ids=[flota["gateway_1"]],
        )
        gateway = await _gateway(db_session, flota["gateway_1"])
        gateway.firmware_estado = FirmwareUpdateState.APLICANDO
        await db_session.flush()

        nueva = await _publicar(client, admin_headers, version="v1.4.0")
        response = await _desplegar(
            client,
            admin_headers,
            release_id=nueva["id"],
            gateway_ids=[flota["gateway_1"]],
        )

        assert response.json()["programados"] == []
        assert "aplicando" in response.json()["omitidos"][0]["motivo"]

    async def test_los_omitidos_no_esconden_a_los_programados(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        flota: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """Una sede con un equipo listo y otro no tiene que decir las dos
        cosas, no la más cómoda."""
        gateway = await _gateway(db_session, flota["gateway_2"])
        gateway.firmware_version = "1.4.0"
        await db_session.flush()
        release = await _publicar(client, admin_headers)

        response = await _desplegar(
            client,
            admin_headers,
            release_id=release["id"],
            client_id=flota["client_id"],
        )

        cuerpo = response.json()
        assert [item["numero_serie"] for item in cuerpo["programados"]] == ["GW-1"]
        assert [item["numero_serie"] for item in cuerpo["omitidos"]] == ["GW-2"]


class TestDestinosQueNoSonDestinos:
    async def test_sin_destino(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        release = await _publicar(client, admin_headers)

        response = await _desplegar(client, admin_headers, release_id=release["id"])

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_dos_destinos_a_la_vez(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        flota: dict[str, str],
    ) -> None:
        """Aceptar dos obligaría a inventar cuál gana, y el error se vería
        recién cuando media flota se reiniciara."""
        release = await _publicar(client, admin_headers)

        response = await _desplegar(
            client,
            admin_headers,
            release_id=release["id"],
            site_id=flota["site_1"],
            client_id=flota["client_id"],
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_una_lista_de_gateways_vacia(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        release = await _publicar(client, admin_headers)

        response = await _desplegar(
            client, admin_headers, release_id=release["id"], gateway_ids=[]
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_una_sede_sin_equipos(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        release = await _publicar(client, admin_headers)

        response = await _desplegar(
            client, admin_headers, release_id=release["id"], site_id=str(uuid.uuid4())
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_una_version_que_no_existe(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        flota: dict[str, str],
    ) -> None:
        response = await _desplegar(
            client,
            admin_headers,
            release_id=str(uuid.uuid4()),
            gateway_ids=[flota["gateway_1"]],
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_un_tecnico_no_despliega(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        tecnico_headers: dict[str, str],
        flota: dict[str, str],
    ) -> None:
        release = await _publicar(client, admin_headers)

        response = await _desplegar(
            client,
            tecnico_headers,
            release_id=release["id"],
            gateway_ids=[flota["gateway_1"]],
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestElEstadoDeUnEquipo:
    async def test_un_gateway_sin_nada_pedido(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        flota: dict[str, str],
    ) -> None:
        response = await client.get(
            f"/api/v1/gateways/{flota['gateway_1']}/firmware", headers=admin_headers
        )

        assert response.json()["estado"] == "sin_pendiente"
        assert response.json()["version_objetivo"] is None
        assert response.json()["intentos_restantes"] == 3

    async def test_despues_de_desplegar_dice_a_donde_va(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        flota: dict[str, str],
    ) -> None:
        release = await _publicar(client, admin_headers)
        await _desplegar(
            client,
            admin_headers,
            release_id=release["id"],
            gateway_ids=[flota["gateway_1"]],
        )

        response = await client.get(
            f"/api/v1/gateways/{flota['gateway_1']}/firmware", headers=admin_headers
        )

        assert response.json()["estado"] == "programada"
        assert response.json()["version_objetivo"] == VERSION

    async def test_un_cliente_no_ve_los_equipos_de_otra_empresa(
        self,
        client: AsyncClient,
        cliente_user: User,
        authenticate_monitor: Login,
        flota: dict[str, str],
    ) -> None:
        headers = auth_header(await authenticate_monitor(cliente_user.email))

        response = await client.get(
            f"/api/v1/gateways/{flota['gateway_1']}/firmware", headers=headers
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestCancelar:
    async def test_le_saca_la_actualizacion_pedida(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        flota: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        release = await _publicar(client, admin_headers)
        await _desplegar(
            client,
            admin_headers,
            release_id=release["id"],
            gateway_ids=[flota["gateway_1"]],
        )

        response = await client.delete(
            f"/api/v1/gateways/{flota['gateway_1']}/firmware", headers=admin_headers
        )

        assert response.json()["estado"] == "sin_pendiente"
        gateway = await _gateway(db_session, flota["gateway_1"])
        assert gateway.firmware_objetivo_id is None
        assert gateway.firmware_aplicar_desde is None

    async def test_cancelar_lo_que_no_existe_no_es_un_error(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        flota: dict[str, str],
    ) -> None:
        response = await client.delete(
            f"/api/v1/gateways/{flota['gateway_1']}/firmware", headers=admin_headers
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["estado"] == "sin_pendiente"

    async def test_no_se_cancela_un_equipo_que_ya_se_esta_reiniciando(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        flota: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """El paquete ya está en el disco: "cancelada" no describiría nada."""
        release = await _publicar(client, admin_headers)
        await _desplegar(
            client,
            admin_headers,
            release_id=release["id"],
            gateway_ids=[flota["gateway_1"]],
        )
        gateway = await _gateway(db_session, flota["gateway_1"])
        gateway.firmware_estado = FirmwareUpdateState.APLICANDO
        await db_session.flush()

        response = await client.delete(
            f"/api/v1/gateways/{flota['gateway_1']}/firmware", headers=admin_headers
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    async def test_un_tecnico_no_cancela(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        tecnico_headers: dict[str, str],
        flota: dict[str, str],
    ) -> None:
        release = await _publicar(client, admin_headers)
        await _desplegar(
            client,
            admin_headers,
            release_id=release["id"],
            gateway_ids=[flota["gateway_1"]],
        )

        response = await client.delete(
            f"/api/v1/gateways/{flota['gateway_1']}/firmware", headers=tecnico_headers
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
