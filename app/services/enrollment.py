"""Enrolar un gateway: emitir el permiso de un solo uso y canjearlo."""

import uuid
from datetime import UTC, datetime, timedelta

from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    BusinessRuleError,
    NotFoundError,
)
from app.core.logging import get_logger
from app.core.security import hash_lookup_token
from app.domain.access import AccessScope
from app.domain.enums import SettingOrigin
from app.domain.passwords import generate_enrollment_token
from app.models import EnrollmentToken, Gateway
from app.repositories.enrollment_token import EnrollmentTokenRepository
from app.repositories.hierarchy import GatewayRepository
from app.schemas.enrollment import EnvEntry, ReleaseRef
from app.services.gateway_config import GatewayCredentialService
from app.services.platform_settings import PlatformSettingService

logger = get_logger(__name__)


def _vencido(fila: EnrollmentToken, ahora: datetime) -> bool:
    """Si ya pasó su vencimiento.

    La fecha guardada puede volver sin zona horaria según el motor. Tratarla
    como UTC es correcto —se escribió en UTC— y evita que la comparación
    reviente en un backend y funcione en el otro.
    """
    expira = fila.expira_en
    if expira.tzinfo is None:
        expira = expira.replace(tzinfo=UTC)
    return expira <= ahora

# Ocho horas: una jornada. Lo que protege a este token es que se gasta al
# usarlo, no que dure poco — uno de un solo uso válido una jornada es más
# seguro que uno reutilizable de quince minutos, y no arruina un viaje a sitio
# porque el técnico llegó más tarde de lo previsto.
VIGENCIA = timedelta(hours=8)


class EnrollmentService:
    """El permiso con el que un equipo pide su configuración.

    Emitirlo es una acción del panel; canjearlo lo hace el instalador que
    corre en la sede, que no sabe nada más que el token.
    """

    def __init__(
        self,
        tokens: EnrollmentTokenRepository,
        gateways: GatewayRepository,
        settings: PlatformSettingService,
        credenciales: GatewayCredentialService,
    ) -> None:
        self._tokens = tokens
        self._gateways = gateways
        self._settings = settings
        self._credenciales = credenciales

    async def issue(
        self, scope: AccessScope, gateway_id: uuid.UUID
    ) -> tuple[str, datetime, str]:
        """Emitir un token para un gateway.

        Devuelve el token, su vencimiento y el comando listo para copiar. El
        token se ve una sola vez: de acá en más solo existe su hash.
        """
        if not scope.can_write:
            raise AuthorizationError(
                f"Role '{scope.principal}' cannot enrol gateways"
            )
        gateway = await self._require_gateway(scope, gateway_id)

        ahora = datetime.now(UTC)
        # Emitir uno nuevo vence los anteriores. Sin esto, cada intento
        # fallido deja un token vivo dando vueltas en un chat o un papel, y
        # todos siguen sirviendo hasta que expiran solos.
        await self._tokens.expire_pending(gateway.id, ahora=ahora)

        token = generate_enrollment_token()
        expira_en = ahora + VIGENCIA
        await self._tokens.add(
            EnrollmentToken(
                token_hash=hash_lookup_token(token),
                gateway_id=gateway.id,
                expira_en=expira_en,
                emitido_por=scope.user_id,
            )
        )

        logger.warning(
            "enrollment token issued",
            extra={
                "gateway_id": str(gateway.id),
                "numero_serie": gateway.numero_serie,
                "principal": scope.principal,
            },
        )
        return token, expira_en, await self._comando(token)

    async def exchange(
        self, token: str, desde: str
    ) -> tuple[Gateway, list[EnvEntry], ReleaseRef]:
        """Canjear un token por la configuración completa de su gateway.

        Quien llama no es un usuario ni una cuenta de servicio: es un equipo
        en una sede con un token de un solo uso. Por eso este método no recibe
        `AccessScope` — no hay alcance que verificar, hay un token que vale o
        no vale.

        Todo ocurre en la misma transacción: gastar el token, rotar la
        credencial y armar la respuesta. Si la respuesta no llega al equipo,
        el token ya se gastó y la credencial ya cambió, así que hay que emitir
        otro. Es incómodo a propósito — permitir reintentar el canje es tener
        un token reutilizable, que es justo lo que este diseño evita.
        """
        fila = await self._tokens.get_by_hash(hash_lookup_token(token))
        ahora = datetime.now(UTC)

        # Un solo motivo de rechazo hacia afuera: distinguir «no existe» de
        # «ya se usó» le diría a quien prueba tokens al azar cuándo acertó uno.
        if fila is None or fila.usado_en is not None or _vencido(fila, ahora):
            logger.warning("enrollment rejected", extra={"desde": desde})
            raise AuthenticationError("Token de enrolamiento inválido o vencido")

        gateway = await self._gateways.get(fila.gateway_id)
        if gateway is None:  # pragma: no cover - la FK lo impide
            raise NotFoundError("Gateway no encontrado")

        # La versión se resuelve acá, entre validar y consumir. Después de
        # consumir dejaría el token quemado y al equipo con una credencial que
        # nadie recibió; antes de validar haría que un token inválido se
        # quejara de la configuración en vez de decir que no sirve.
        release = await self.release()

        await self._tokens.update(fila, {"usado_en": ahora, "usado_desde": desde})
        _, credencial = await self._credenciales.rotate(gateway)

        logger.warning(
            "gateway enrolled",
            extra={
                "gateway_id": str(gateway.id),
                "numero_serie": gateway.numero_serie,
                "desde": desde,
            },
        )
        return gateway, await self._env(gateway, credencial), release

    async def release(self) -> ReleaseRef:
        """Qué versión instala un equipo nuevo.

        Sale de `platform_settings` y no del código porque es una decisión: se
        publica una versión, se prueba en un equipo, y recién entonces se
        cambia acá. Vacía significa que todavía no se eligió ninguna, y eso
        tiene que frenar el enrolamiento en vez de instalar cualquier cosa.
        """
        version = await self._sin_elegir("GATEWAY_RELEASE_VERSION")
        sha256 = await self._sin_elegir("GATEWAY_RELEASE_SHA256")
        base = await self._sin_elegir("GATEWAY_RELEASE_BASE_URL")

        if not version or not sha256:
            raise BusinessRuleError(
                "No hay una versión elegida para los equipos nuevos: cargar "
                "GATEWAY_RELEASE_VERSION y GATEWAY_RELEASE_SHA256 en el panel"
            )
        return ReleaseRef(
            version=version,
            url=f"{base}/gatewayEMS-{version}.tar.gz",
            sha256=sha256,
        )

    async def _sin_elegir(self, clave: str) -> str:
        """El valor, o vacío si la fila no existe.

        Una fila que falta y una vacía significan lo mismo acá: nadie eligió
        una versión. Dejar que la ausencia salga como 404 daría un error que
        no dice qué hacer, en vez del que nombra las claves que hay que
        cargar.
        """
        try:
            return await self._settings.valor_publico(clave)
        except NotFoundError:
            return ""

    async def _env(self, gateway: Gateway, credencial: str) -> list[EnvEntry]:
        """El archivo entero, listo para escribir.

        Incluye las variables que el equipo llena solo, con el valor vacío.
        Mandar únicamente las que el CRM conoce obligaría al firmware a
        mantener su propia lista de qué más hace falta — y esa lista se
        desactualiza la primera vez que alguien agrega una variable al panel.
        """
        # `MQTT_CLIENT_ID` no está acá a propósito: lo genera el equipo, con
        # `origen = equipo`, igual que los secretos de su InfluxDB local.
        # Derivarlo del uuid haría que los logs del broker dijeran qué equipo
        # es cada conexión, pero movería al CRM una decisión que hoy vive en
        # el dispositivo.
        identidad = {
            "GATEWAY_UUID": str(gateway.uuid),
            "GATEWAY_CREDENTIAL": credencial,
        }

        # La identidad se emite siempre, exista o no su fila en la tabla. Las
        # filas están para que estas variables se vean en el panel, no para
        # decidir si el equipo las recibe: un `.env` sin `GATEWAY_UUID` no
        # arranca, y eso no puede depender de que nadie haya borrado una fila.
        entradas: list[EnvEntry] = [
            EnvEntry(clave=clave, valor=valor, origen=SettingOrigin.IDENTIDAD)
            for clave, valor in identidad.items()
        ]

        for clave, valor, origen in await self._settings.para_un_gateway():
            if clave in identidad:
                continue
            entradas.append(EnvEntry(clave=clave, valor=valor, origen=origen))
        return entradas

    async def _comando(self, token: str) -> str:
        """La línea que se le pasa al técnico.

        Se arma acá y no en el panel porque la URL vive en
        `platform_settings`: el día que cambie el dominio, el comando cambia
        solo. Y entregarlo entero evita que alguien lo rearme a mano en un
        chat, con un espacio de más o la URL vieja.

        El token va en una variable de entorno y no dentro de la dirección:
        una query string queda escrita en el log de acceso del servidor y en
        el del proxy. Tampoco como argumento — los argumentos son visibles en
        `ps` para cualquier usuario del equipo.
        """
        url = await self._settings.valor_publico("GATEWAY_INSTALLER_URL")
        return f"curl -fsSL {url} | sudo EMS_TOKEN={token} bash"

    async def _require_gateway(
        self, scope: AccessScope, gateway_id: uuid.UUID
    ) -> Gateway:
        """El gateway, si quien pregunta puede verlo.

        Mismo recorte que el resto del árbol, con las mismas piezas: un
        gateway de otra empresa responde 404 y no 403, porque confirmar que
        existe ya sería contar algo de esa empresa.
        """
        gateway = await self._gateways.get(gateway_id)
        if gateway is None:
            raise NotFoundError(f"Gateway {gateway_id} not found")
        owner = await self._gateways.owning_client_id(gateway_id)
        if owner is None or not scope.may_read_client(owner):
            raise NotFoundError(f"Gateway {gateway_id} not found")
        return gateway
