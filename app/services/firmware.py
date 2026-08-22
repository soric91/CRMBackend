"""La actualización remota, del lado que habla con el equipo.

El CRM no empuja software: deja una orden escrita y el equipo la busca. Eso
hace que un gateway que estuvo apagado tres días se actualice igual al
volver, y que un broker caído no sea un despliegue perdido.

Lo que el servidor sí hace es no ofrecer nunca una orden que no se pueda
cumplir: sin versión disponible, sin ventana, sin intentos o con el
interruptor de la flota apagado, la respuesta es "nada que hacer".
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthorizationError, BusinessRuleError, NotFoundError
from app.core.logging import get_logger
from app.domain.enums import FirmwareUpdateState
from app.domain.firmware_update import (
    MAX_INTENTOS,
    como_utc,
    dentro_de_ventana,
    misma_version,
    normalizar_version,
    paquete_url,
    parse_bandera,
    parse_hora,
    parse_ventana_minutos,
    proxima_ventana,
    puede_reintentar,
    transicion_valida,
    validar_transicion,
)
from app.models import FirmwareRelease, Gateway, Site
from app.schemas.firmware import FirmwareUpdateOrder, FirmwareUpdateStatus
from app.services.platform_settings import PlatformSettingService

logger = get_logger(__name__)

# Los estados en los que todavía hay algo que el equipo tenga que hacer.
EN_CURSO = frozenset(
    {
        FirmwareUpdateState.PROGRAMADA,
        FirmwareUpdateState.DESCARGANDO,
        FirmwareUpdateState.APLICANDO,
        FirmwareUpdateState.FALLIDA,
    }
)


@dataclass(frozen=True)
class VentanaConfig:
    """Lo que la flota comparte sobre cuándo y cómo actualizarse."""

    activo: bool
    hora: time
    ventana_minutos: int
    rollback_auto: bool
    base_url: str


class FirmwareUpdateService:
    """Sirve la orden de actualización y anota lo que el equipo contesta."""

    def __init__(
        self, session: AsyncSession, settings: PlatformSettingService
    ) -> None:
        self._session = session
        self._settings = settings

    # --- la configuración de la flota ------------------------------------

    async def config(self) -> VentanaConfig:
        """La ventana y el interruptor, leídos de `platform_settings`.

        Falla cerrado: una fila borrada, una hora ilegible o una URL vacía
        dejan la actualización **apagada**. La alternativa sería inventar un
        valor por omisión y reiniciar equipos a una hora que nadie eligió.
        """
        activo = parse_bandera(await self._valor("FIRMWARE_UPDATE_ACTIVO"))
        base_url = (await self._valor("GATEWAY_RELEASE_BASE_URL")).strip()
        rollback = parse_bandera(await self._valor("FIRMWARE_ROLLBACK_AUTO"))

        try:
            hora = parse_hora(await self._valor("FIRMWARE_UPDATE_HORA"))
            ventana = parse_ventana_minutos(
                await self._valor("FIRMWARE_UPDATE_VENTANA_MINUTOS")
            )
        except ValueError as error:
            logger.warning(
                "firmware update window is unusable", extra={"error": str(error)}
            )
            return VentanaConfig(False, time(3, 0), 120, rollback, base_url)

        return VentanaConfig(
            activo=activo and bool(base_url),
            hora=hora,
            ventana_minutos=ventana,
            rollback_auto=rollback,
            base_url=base_url,
        )

    async def _valor(self, clave: str) -> str:
        """El valor de una clave, o vacío si la fila no está.

        Una fila que falta y una vacía significan lo mismo acá: nadie
        configuró esto todavía.
        """
        try:
            return await self._settings.valor_publico(clave)
        except NotFoundError:
            return ""

    # --- lo que el equipo pregunta ---------------------------------------

    async def pendiente(self, gateway: Gateway) -> FirmwareUpdateOrder | None:
        """La orden que este equipo tiene que ejecutar, si hay alguna.

        Devuelve `None` cuando no hay nada que hacer. Levanta 403 cuando las
        actualizaciones están apagadas para toda la flota: es el mismo
        vocabulario que ya usa la descarga de configuración, y el firmware lo
        trata como "estoy al día", no como error.
        """
        config = await self.config()
        if not config.activo:
            raise AuthorizationError(
                "Las actualizaciones de firmware están desactivadas"
            )

        release = await self._objetivo(gateway)
        if release is None:
            return None

        if not release.disponible:
            # Retirada mientras el equipo iba hacia ella. Se cancela salvo que
            # ya esté reiniciándose con el paquete: para entonces cancelar
            # sería escribir en la pantalla algo que no va a pasar.
            await self._cancelar(gateway, f"La versión {release.version} fue retirada")
            return None

        if gateway.firmware_estado not in EN_CURSO:
            return None

        if misma_version(gateway.firmware_version, release.version):
            # Ya está corriendo lo que se le pidió: el acuse se perdió, o
            # alguien la instaló a mano. Se cierra si el recorrido lo permite.
            await self._dar_por_aplicada(gateway)
            return None

        if not puede_reintentar(gateway.firmware_intentos):
            return None

        desde = await self._vigente(gateway, config)
        return FirmwareUpdateOrder(
            version=release.version,
            url=paquete_url(config.base_url, release.version),
            sha256=release.sha256,
            tamano_bytes=release.tamano_bytes,
            aplicar_desde=desde,
            ventana_minutos=config.ventana_minutos,
            rollback_auto=config.rollback_auto,
            intentos_restantes=MAX_INTENTOS - gateway.firmware_intentos,
            notas=release.notas,
        )

    async def _vigente(self, gateway: Gateway, config: VentanaConfig) -> datetime:
        """La ventana que el equipo tiene por delante, recalculada si venció.

        Un gateway que estuvo apagado una semana vuelve con una hora que ya
        pasó. Sin esto se quedaría esperando para siempre una ventana que no
        va a volver.
        """
        ahora = datetime.now(UTC)
        desde = (
            como_utc(gateway.firmware_aplicar_desde)
            if gateway.firmware_aplicar_desde is not None
            else None
        )
        vigente = desde is not None and (
            ahora < desde
            or dentro_de_ventana(ahora, desde, config.ventana_minutos)
        )
        if vigente and desde is not None:
            if gateway.firmware_estado is FirmwareUpdateState.FALLIDA:
                await self._rearmar(gateway)
            return desde

        nueva = proxima_ventana(
            ahora, config.hora, config.ventana_minutos, await self._timezone(gateway)
        )
        gateway.firmware_aplicar_desde = nueva
        if gateway.firmware_estado is FirmwareUpdateState.FALLIDA:
            gateway.firmware_estado = FirmwareUpdateState.PROGRAMADA
        await self._session.flush()
        logger.info(
            "firmware window recomputed",
            extra={"gateway_id": str(gateway.id), "aplicar_desde": nueva.isoformat()},
        )
        return nueva

    async def _rearmar(self, gateway: Gateway) -> None:
        """Vuelve a dejar programado un intento que había fallado."""
        gateway.firmware_estado = FirmwareUpdateState.PROGRAMADA
        await self._session.flush()

    # --- lo que el equipo cuenta ------------------------------------------

    async def acuse(
        self,
        gateway: Gateway,
        estado: FirmwareUpdateState,
        version: str,
        error: str | None,
    ) -> Gateway:
        """Anota lo que el equipo reporta sobre su actualización.

        Se acepta aunque el interruptor de la flota se haya apagado en el
        medio: anotar lo que de verdad pasó siempre está bien, y un equipo que
        ya se reinició con la versión nueva no puede quedar registrado como si
        no lo hubiera hecho.
        """
        release = await self._objetivo(gateway)
        if release is None:
            raise BusinessRuleError(
                "Este gateway no tiene ninguna actualización pedida"
            )

        if not misma_version(version, release.version):
            raise BusinessRuleError(
                f"El acuse habla de '{version}' y la actualización pedida es "
                f"'{release.version}'"
            )

        try:
            validar_transicion(gateway.firmware_estado, estado)
        except ValueError as invalida:
            raise BusinessRuleError(str(invalida)) from invalida

        ahora = datetime.now(UTC)
        anterior = gateway.firmware_estado

        if estado is FirmwareUpdateState.DESCARGANDO and anterior is not estado:
            # Un intento arranca cuando empieza la descarga. Contarlo en el
            # acuse repetido haría que una red intermitente gastara los tres
            # intentos sin que el equipo llegara a probar una sola vez.
            gateway.firmware_intentos += 1

        if estado is FirmwareUpdateState.APLICADA:
            gateway.firmware_version_anterior = gateway.firmware_version
            gateway.firmware_version = normalizar_version(release.version)
            gateway.firmware_error = None
        elif estado is FirmwareUpdateState.FALLIDA:
            gateway.firmware_error = error or "El equipo no dijo por qué"

        gateway.firmware_estado = estado
        gateway.firmware_reportado_en = ahora
        gateway.ultima_conexion = ahora
        await self._session.flush()
        await self._session.refresh(gateway)

        logger.info(
            "firmware update reported",
            extra={
                "gateway_id": str(gateway.id),
                "estado": estado.value,
                "version": release.version,
                "intentos": gateway.firmware_intentos,
            },
        )
        return gateway

    def status(
        self, gateway: Gateway, version_objetivo: str | None
    ) -> FirmwareUpdateStatus:
        """Cómo quedó el equipo, para contestarle el acuse."""
        return FirmwareUpdateStatus(
            gateway_uuid=gateway.uuid,
            estado=gateway.firmware_estado,
            version_objetivo=version_objetivo,
            version_actual=gateway.firmware_version,
            aplicar_desde=gateway.firmware_aplicar_desde,
            intentos=gateway.firmware_intentos,
            intentos_restantes=max(MAX_INTENTOS - gateway.firmware_intentos, 0),
            error=gateway.firmware_error,
            reportado_en=gateway.firmware_reportado_en,
        )

    # --- piezas compartidas ----------------------------------------------

    async def objetivo(self, gateway: Gateway) -> FirmwareRelease | None:
        """La versión a la que este equipo tiene que llegar, si hay una."""
        return await self._objetivo(gateway)

    async def _objetivo(self, gateway: Gateway) -> FirmwareRelease | None:
        if gateway.firmware_objetivo_id is None:
            return None
        return await self._session.get(FirmwareRelease, gateway.firmware_objetivo_id)

    async def _timezone(self, gateway: Gateway) -> str:
        """La zona horaria de la sede donde está instalado el equipo.

        Se consulta y no se navega por la relación: `Gateway.site` es
        `lazy="raise"` a propósito, para que ninguna ruta cargue el árbol
        entero sin querer.
        """
        result = await self._session.execute(
            select(Site.timezone).where(Site.id == gateway.site_id)
        )
        return result.scalar_one()

    async def _cancelar(self, gateway: Gateway, motivo: str) -> None:
        """Deja al equipo sin nada que hacer, si el recorrido lo permite."""
        if not transicion_valida(
            gateway.firmware_estado, FirmwareUpdateState.SIN_PENDIENTE
        ):
            return
        gateway.firmware_estado = FirmwareUpdateState.SIN_PENDIENTE
        gateway.firmware_objetivo_id = None
        gateway.firmware_aplicar_desde = None
        gateway.firmware_error = motivo
        await self._session.flush()
        logger.warning(
            "firmware update cancelled",
            extra={"gateway_id": str(gateway.id), "motivo": motivo},
        )

    async def _dar_por_aplicada(self, gateway: Gateway) -> None:
        if not transicion_valida(
            gateway.firmware_estado, FirmwareUpdateState.APLICADA
        ):
            return
        gateway.firmware_estado = FirmwareUpdateState.APLICADA
        gateway.firmware_reportado_en = datetime.now(UTC)
        gateway.firmware_error = None
        await self._session.flush()
        logger.info(
            "firmware update closed without an ack",
            extra={"gateway_id": str(gateway.id)},
        )
