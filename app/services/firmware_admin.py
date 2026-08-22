"""Publicar versiones y pedírselas a los equipos.

Dos actos separados, y la separación es el punto: se publica una versión, se
prueba en un equipo, y recién entonces se le pide a la flota. Juntarlos haría
que publicar fuera desplegar, que es como se rompen cien sedes a la vez.

Nada de esto reinicia nada por su cuenta. Deja escrito qué versión tiene que
tener cada equipo y desde cuándo; el equipo lo busca solo (ver
`app.services.firmware`).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AlreadyExistsError,
    AuthorizationError,
    BusinessRuleError,
    NotFoundError,
)
from app.core.logging import get_logger
from app.core.mqtt import get_bridge
from app.domain.access import AccessScope
from app.domain.enums import FirmwareUpdateState
from app.domain.firmware_update import (
    es_descenso,
    misma_version,
    proxima_ventana,
    transicion_valida,
)
from app.models import FirmwareRelease, Gateway, Site
from app.repositories.firmware_release import FirmwareReleaseRepository
from app.schemas.firmware import (
    FirmwareReleaseCreate,
    FirmwareReleaseRead,
    RolloutCreate,
    RolloutOmitido,
    RolloutProgramado,
    RolloutResult,
)
from app.services.firmware import FirmwareUpdateService

logger = get_logger(__name__)


def _require_admin(scope: AccessScope, accion: str) -> None:
    """Mismo permiso que las credenciales de servicio, y por lo mismo.

    Quien pueda hacer esto decide qué software corre en cada equipo instalado.
    Un `tecnico` mantiene dispositivos; elegir el firmware de la flota no es
    parte de ese trabajo.
    """
    if not scope.can_manage_services:
        raise AuthorizationError(f"Role '{scope.principal}' cannot {accion}")


class FirmwareAdminService:
    """El catálogo de versiones y los despliegues, desde el panel."""

    def __init__(
        self,
        session: AsyncSession,
        releases: FirmwareReleaseRepository,
        updates: FirmwareUpdateService,
    ) -> None:
        self._session = session
        self._releases = releases
        self._updates = updates

    # --- el catálogo ------------------------------------------------------

    async def list_releases(self, scope: AccessScope) -> list[FirmwareReleaseRead]:
        """Las versiones publicadas, la más nueva primero."""
        if not scope.is_staff:
            raise AuthorizationError(
                f"Role '{scope.principal}' cannot read firmware releases"
            )
        return [
            FirmwareReleaseRead.model_validate(release).model_copy(
                update={
                    "gateways_apuntando": await self._releases.gateways_apuntando(
                        release.id
                    )
                }
            )
            for release in await self._releases.list_ordered()
        ]

    async def publish(
        self, scope: AccessScope, payload: FirmwareReleaseCreate
    ) -> FirmwareReleaseRead:
        """Agregar una versión al catálogo, sin desplegarla en ningún equipo."""
        _require_admin(scope, "publish firmware releases")

        if await self._releases.get_by_version(payload.version) is not None:
            raise AlreadyExistsError(
                f"La versión '{payload.version}' ya está publicada"
            )

        release = await self._releases.add(
            FirmwareRelease(
                version=payload.version,
                canal=payload.canal,
                sha256=payload.sha256,
                tamano_bytes=payload.tamano_bytes,
                notas=payload.notas,
                publicado_por=scope.user_id,
            )
        )
        logger.warning(
            "firmware release published",
            extra={
                "version": release.version,
                "canal": release.canal.value,
                "principal": scope.principal,
            },
        )
        return FirmwareReleaseRead.model_validate(release)

    async def retire(
        self, scope: AccessScope, release_id: uuid.UUID
    ) -> FirmwareReleaseRead:
        """Dejar de ofrecer una versión, sin borrarla.

        Los equipos que ya la instalaron siguen apuntando a esta fila, y los
        que iban hacia ella dejan de recibirla en la próxima consulta. Borrar
        la fila haría desaparecer la única explicación de por qué una sede
        quedó como quedó.
        """
        _require_admin(scope, "retire firmware releases")
        release = await self._require_release(release_id)

        if release.disponible:
            release = await self._releases.update(
                release, {"retirado_en": datetime.now(UTC)}
            )
            logger.warning(
                "firmware release retired",
                extra={"version": release.version, "principal": scope.principal},
            )

        apuntando = await self._releases.gateways_apuntando(release.id)
        return FirmwareReleaseRead.model_validate(release).model_copy(
            update={"gateways_apuntando": apuntando}
        )

    # --- pedirle una versión a un grupo de equipos ------------------------

    async def rollout(
        self, scope: AccessScope, payload: RolloutCreate
    ) -> RolloutResult:
        """Dejar pedida una versión en cada equipo del destino elegido.

        Se salta —informándolo— todo equipo al que pedírsela no tendría
        sentido: el que ya la tiene, el que se está reiniciando ahora mismo, y
        el que no tiene credencial para bajarla. Un despliegue que dice a
        quién no llegó es mejor que uno que dice "listo".
        """
        _require_admin(scope, "deploy firmware")
        release = await self._require_release(payload.release_id)
        if not release.disponible:
            raise BusinessRuleError(
                f"La versión '{release.version}' está retirada: no se puede "
                "desplegar"
            )

        gateways = await self._destino(scope, payload)
        if not gateways:
            raise NotFoundError("No hay gateways en el destino elegido")

        config = await self._updates.config()
        ahora = datetime.now(UTC)
        programados: list[RolloutProgramado] = []
        omitidos: list[RolloutOmitido] = []

        for gateway, timezone in gateways:
            motivo = self._por_que_no(gateway, release)
            if motivo is not None:
                omitidos.append(
                    RolloutOmitido(
                        gateway_id=gateway.id,
                        numero_serie=gateway.numero_serie,
                        motivo=motivo,
                    )
                )
                continue

            desde = (
                ahora
                if payload.ahora
                else proxima_ventana(
                    ahora, config.hora, config.ventana_minutos, timezone
                )
            )
            anterior = gateway.firmware_version

            gateway.firmware_objetivo_id = release.id
            gateway.firmware_estado = FirmwareUpdateState.PROGRAMADA
            gateway.firmware_aplicar_desde = desde
            # Los intentos son de este despliegue, no del anterior: un equipo
            # que gastó tres intentos con otra versión tiene que poder
            # intentar ésta.
            gateway.firmware_intentos = 0
            gateway.firmware_error = None
            gateway.firmware_reportado_en = None

            programados.append(
                RolloutProgramado(
                    gateway_id=gateway.id,
                    numero_serie=gateway.numero_serie,
                    version_anterior=anterior,
                    aplicar_desde=desde,
                    descenso=es_descenso(anterior, release.version),
                )
            )

        await self._session.flush()
        await self._avisar(programados, release.version)

        logger.warning(
            "firmware rollout scheduled",
            extra={
                "version": release.version,
                "programados": len(programados),
                "omitidos": len(omitidos),
                "principal": scope.principal,
            },
        )
        return RolloutResult(
            version=release.version,
            flota_activa=config.activo,
            programados=programados,
            omitidos=omitidos,
        )

    async def cancel(self, scope: AccessScope, gateway: Gateway) -> Gateway:
        """Sacarle a un equipo la actualización que tenía pedida.

        No se puede una vez que empezó a aplicarla: para entonces el paquete
        ya está en el disco y el equipo se está reiniciando con él, así que
        "cancelada" sería una palabra que no describe nada.
        """
        _require_admin(scope, "cancel firmware updates")

        if gateway.firmware_estado is FirmwareUpdateState.SIN_PENDIENTE:
            return gateway
        if not transicion_valida(
            gateway.firmware_estado, FirmwareUpdateState.SIN_PENDIENTE
        ):
            raise BusinessRuleError(
                "El equipo ya está aplicando la actualización: no se puede "
                "cancelar"
            )

        gateway.firmware_estado = FirmwareUpdateState.SIN_PENDIENTE
        gateway.firmware_objetivo_id = None
        gateway.firmware_aplicar_desde = None
        gateway.firmware_intentos = 0
        gateway.firmware_error = None
        await self._session.flush()
        await self._session.refresh(gateway)

        logger.warning(
            "firmware update cancelled from the panel",
            extra={"gateway_id": str(gateway.id), "principal": scope.principal},
        )
        return gateway

    # --- piezas -----------------------------------------------------------

    @staticmethod
    def _por_que_no(gateway: Gateway, release: FirmwareRelease) -> str | None:
        """El motivo para no pedirle esta versión a este equipo, si lo hay."""
        if misma_version(gateway.firmware_version, release.version):
            return f"Ya está corriendo {release.version}"
        if gateway.credential_hash is None:
            return "No tiene credencial: no podría bajar el paquete"
        if not transicion_valida(
            gateway.firmware_estado, FirmwareUpdateState.PROGRAMADA
        ):
            return "Está aplicando otra actualización ahora mismo"
        return None

    async def _destino(
        self, scope: AccessScope, payload: RolloutCreate
    ) -> list[tuple[Gateway, str]]:
        """Los equipos elegidos, con la zona horaria de su sede.

        Se traen juntos porque la ventana se calcula en la hora local de cada
        planta: pedirlas de a una sería una consulta por equipo, y un
        despliegue a una empresa entera son decenas.
        """
        statement = select(Gateway, Site.timezone).join(
            Site, Gateway.site_id == Site.id
        )

        if payload.gateway_ids is not None:
            statement = statement.where(Gateway.id.in_(payload.gateway_ids))
        elif payload.site_id is not None:
            statement = statement.where(Gateway.site_id == payload.site_id)
        else:
            statement = statement.where(Site.client_id == payload.client_id)

        # El recorte de siempre: un equipo de otra empresa no aparece, así que
        # tampoco se le puede desplegar nada por número.
        visible = scope.visible_client_id
        if visible is not None:
            statement = statement.where(Site.client_id == visible)

        result = await self._session.execute(statement.order_by(Gateway.numero_serie))
        return [(gateway, timezone) for gateway, timezone in result.all()]

    async def _avisar(
        self, programados: list[RolloutProgramado], version: str
    ) -> None:
        """Le toca el hombro a cada equipo por MQTT.

        Es una optimización: el equipo pregunta solo cada tanto, y esto le
        ahorra la espera. Si el broker no está, no pasa nada — por eso no se
        mira el resultado.
        """
        bridge = get_bridge()
        if bridge is None:
            return
        for item in programados:
            gateway = await self._session.get(Gateway, item.gateway_id)
            if gateway is not None:
                await bridge.notify_firmware_update(
                    gateway.uuid, version, item.aplicar_desde
                )

    async def _require_release(self, release_id: uuid.UUID) -> FirmwareRelease:
        release = await self._releases.get(release_id)
        if release is None:
            raise NotFoundError(f"Firmware release {release_id} not found")
        return release
