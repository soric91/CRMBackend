"""The gateway's own surface: its credential, its token, its configuration."""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    BusinessRuleError,
    NotFoundError,
)
from app.core.logging import get_logger
from app.core.security import (
    TokenAudience,
    TokenType,
    create_token,
    decode_token,
    hash_password,
    verify_password,
    waste_time_like_a_real_verification,
)
from app.domain.access import AccessScope
from app.domain.enums import ModbusTransport
from app.domain.firmware import (
    FIRMWARE_PROTOCOL,
    as_firmware_address,
    as_firmware_data_type,
)
from app.domain.passwords import generate_gateway_credential
from app.models import Equipment, Gateway
from app.repositories.hierarchy import GatewayRepository
from app.schemas.gateway_config import (
    DeviceConfig,
    GatewayConfigResponse,
    LogConfig,
    MainModbusConfig,
    VariableMapEntry,
)

logger = get_logger(__name__)


class GatewayCredentialService:
    """Issues and revokes the secret a gateway authenticates with. CRM only."""

    def __init__(self, gateways: GatewayRepository) -> None:
        self._gateways = gateways

    @staticmethod
    def _require_write(scope: AccessScope) -> None:
        if not scope.can_write:
            raise AuthorizationError(
                f"Role '{scope.principal}' cannot manage gateway credentials"
            )

    async def _gateway(self, scope: AccessScope, gateway_id: uuid.UUID) -> Gateway:
        gateway = await self._gateways.get(gateway_id)
        if gateway is None:
            raise NotFoundError(f"Gateway {gateway_id} not found")
        owner = await self._gateways.owning_client_id(gateway_id)
        if owner is None or not scope.may_read_client(owner):
            raise NotFoundError(f"Gateway {gateway_id} not found")
        return gateway

    async def get(self, scope: AccessScope, gateway_id: uuid.UUID) -> Gateway:
        return await self._gateway(scope, gateway_id)

    async def issue(
        self, scope: AccessScope, gateway_id: uuid.UUID
    ) -> tuple[Gateway, str]:
        """Issue a credential, replacing any previous one.

        Regenerating revokes the old secret immediately, so the gateway stops
        working until the new one is loaded into it. That is the point: it is
        how a leaked credential is taken back.
        """
        self._require_write(scope)
        gateway = await self._gateway(scope, gateway_id)
        return await self.rotate(gateway)

    async def rotate(self, gateway: Gateway) -> tuple[Gateway, str]:
        """Reemplazar la credencial de un gateway ya autorizado.

        Sin `AccessScope`: quien llama ya verificó su propio permiso. Lo usa
        :meth:`issue` después de comprobarlo, y el enrolamiento después de
        validar el token de un solo uso — que no es un usuario y no tiene
        alcance que verificar.

        La mecánica vive acá una sola vez a propósito. Duplicarla dejaría dos
        formas de emitir una credencial, y el día que una cambie —otro
        algoritmo de hash, un campo más que registrar— la otra queda vieja sin
        que nada avise.
        """
        credential = generate_gateway_credential()
        updated = await self._gateways.update(
            gateway,
            {
                "credential_hash": hash_password(credential),
                "credential_emitida_en": datetime.now(UTC),
            },
        )
        logger.info("gateway credential issued", extra={"gateway_id": str(gateway.id)})
        return updated, credential

    async def revoke(self, scope: AccessScope, gateway_id: uuid.UUID) -> None:
        """Drop the credential. The gateway can no longer obtain a token."""
        self._require_write(scope)
        gateway = await self._gateway(scope, gateway_id)
        await self._gateways.update(
            gateway, {"credential_hash": None, "credential_emitida_en": None}
        )
        logger.info("gateway credential revoked", extra={"gateway_id": str(gateway_id)})


class GatewayTokenService:
    """Exchanges a credential for a short-lived token, and reads it back."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    @property
    def token_lifetime_seconds(self) -> int:
        return self._settings.gateway_token_expire_hours * 3600

    async def _by_uuid(self, gateway_uuid: uuid.UUID) -> Gateway | None:
        result = await self._session.execute(
            select(Gateway).where(Gateway.uuid == gateway_uuid)
        )
        return result.scalar_one_or_none()

    async def issue_token(self, gateway_uuid: uuid.UUID, credential: str) -> str:
        """Return a token for a gateway that proved it holds the credential.

        Every failure — unknown gateway, no credential issued, wrong secret —
        answers the same way, and an unknown gateway still pays for a bcrypt
        verification so timing does not reveal which uuids exist.
        """
        gateway = await self._by_uuid(gateway_uuid)
        if gateway is None or gateway.credential_hash is None:
            waste_time_like_a_real_verification()
            logger.info("gateway token refused", extra={"reason": "unknown_or_unset"})
            raise AuthenticationError("Invalid gateway credential")

        if not verify_password(credential, gateway.credential_hash):
            logger.info(
                "gateway token refused",
                extra={"reason": "bad_credential", "gateway_id": str(gateway.id)},
            )
            raise AuthenticationError("Invalid gateway credential")

        logger.info("gateway token issued", extra={"gateway_id": str(gateway.id)})
        return create_token(
            self._settings,
            subject=str(gateway.uuid),
            token_type=TokenType.ACCESS,
            audience=TokenAudience.GATEWAY,
            expires_in=timedelta(hours=self._settings.gateway_token_expire_hours),
            claims={"gateway_id": str(gateway.id)},
        )

    async def gateway_from_token(self, token: str) -> Gateway:
        """Return the gateway a token stands for, re-read from the database."""
        payload = decode_token(
            self._settings,
            token,
            expected_type=TokenType.ACCESS,
            expected_audience=TokenAudience.GATEWAY,
        )
        try:
            gateway_uuid = uuid.UUID(payload["sub"])
        except ValueError as exc:
            raise AuthenticationError("Invalid token") from exc

        gateway = await self._by_uuid(gateway_uuid)
        # A credential that was revoked after the token was minted invalidates
        # it: the token alone is not proof that the gateway is still trusted.
        if gateway is None or gateway.credential_hash is None:
            raise AuthenticationError("Invalid token")
        return gateway


class GatewayConfigService:
    """Assembles the configuration a gateway downloads."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def build(
        self,
        gateway: Gateway,
        requested_uuid: uuid.UUID,
        *,
        enforce_switch: bool = True,
    ) -> GatewayConfigResponse:
        """Return the configuration of ``gateway``.

        The uuid in the path must be the one inside the token. A mismatch is a
        404, never a 403: this route is reachable by anyone holding a valid
        credential for some other gateway, and confirming that a uuid exists
        would let them enumerate the fleet.
        """
        if gateway.uuid != requested_uuid:
            logger.warning(
                "gateway config requested for another gateway",
                extra={"token_gateway": str(gateway.uuid)},
            )
            raise NotFoundError(f"Gateway {requested_uuid} not found")

        if enforce_switch and not gateway.config_habilitada:
            raise AuthorizationError(
                "Configuration download is not enabled for this gateway"
            )

        result = await self._session.execute(
            select(Equipment)
            .where(Equipment.gateway_id == gateway.id)
            .options(selectinload(Equipment.variables))
            .order_by(Equipment.modbus_id)
        )
        devices = [self._device(item) for item in result.scalars().all()]

        return GatewayConfigResponse(
            gateway_uuid=gateway.uuid,
            numero_serie=gateway.numero_serie,
            firmware_version=gateway.firmware_version,
            generated_at=datetime.now(UTC),
            log=LogConfig(loglevel=gateway.log_level),
            mainmodbus=MainModbusConfig(
                interval=gateway.intervalo_lectura_segundos,
                start_hour=gateway.hora_inicio,
                stop_hour=gateway.hora_fin,
            ),
            devices=devices,
        )

    async def mark_seen(self, gateway: Gateway) -> None:
        """Record that the gateway contacted us. Its truest liveness signal."""
        gateway.ultima_conexion = datetime.now(UTC)
        await self._session.flush()

    async def heartbeat(
        self, gateway: Gateway, firmware_version: str | None, ip_actual: str | None
    ) -> Gateway:
        """Record that the device is alive, and what it says about itself.

        Independent of `config_habilitada`: a gateway that already applied its
        configuration still has to be able to report in, or the panel would
        show every provisioned device as offline forever.
        """
        gateway.ultima_conexion = datetime.now(UTC)
        if firmware_version is not None:
            gateway.firmware_version = firmware_version
        if ip_actual is not None:
            gateway.ip_actual = ip_actual
        await self._session.flush()
        await self._session.refresh(gateway)
        return gateway

    async def acknowledge(self, gateway: Gateway, version: str) -> Gateway:
        """Record the configuration the gateway reports having applied.

        The version has to be the one currently being served: acknowledging a
        stale document would leave the CRM believing the device is up to date
        when it is running something else.

        Applying the configuration also turns `config_habilitada` off, so the
        gateway is not handed the same document again. The consequence is that
        a later edit needs the switch turned back on — which is why the CRM
        surfaces the drift between the applied version and the current one.
        """
        current = compute_config_version(await self.build(gateway, gateway.uuid))
        if version != current:
            raise BusinessRuleError(
                "The acknowledged version is not the one being served; "
                "fetch the configuration again"
            )

        now = datetime.now(UTC)
        gateway.config_version_aplicada = version
        gateway.config_aplicada_en = now
        gateway.ultima_conexion = now
        gateway.config_habilitada = False
        await self._session.flush()
        await self._session.refresh(gateway)
        logger.info(
            "gateway configuration applied",
            extra={"gateway_id": str(gateway.id), "config_version": version},
        )
        return gateway

    async def status_for_crm(self, gateway: Gateway) -> tuple[str, bool]:
        """Return the version being served and whether the device lags behind."""
        # Assembled with the switch ignored: the panel has to be able to see
        # what would be delivered even while the download is turned off.
        current = compute_config_version(
            await self.build(gateway, gateway.uuid, enforce_switch=False)
        )
        return current, gateway.config_version_aplicada != current

    @staticmethod
    def _device(equipment: Equipment) -> DeviceConfig:
        is_serial = equipment.transporte is ModbusTransport.RTU
        return DeviceConfig(
            name=equipment.nombre_dispositivo,
            identify_device=equipment.id,
            device_type=equipment.device_type,
            protocol=FIRMWARE_PROTOCOL[equipment.transporte],
            serialport=equipment.puerto if is_serial else None,
            baudrate=equipment.baudrate if is_serial else None,
            parity=equipment.paridad.value if is_serial and equipment.paridad else None,
            bytesize=equipment.bits if is_serial else None,
            stopbits=equipment.stop_bits if is_serial else None,
            host=None if is_serial else equipment.host,
            port=None if is_serial else equipment.puerto_tcp,
            device_id=equipment.modbus_id,
            modbus_function=equipment.modbus_function,
            modbusconnect=equipment.modbusconnect,
            modbusread=equipment.modbusread,
            blockreading=equipment.blockreading,
            map={
                variable.nombre: VariableMapEntry(
                    address=as_firmware_address(
                        variable.registro_modbus, variable.notacion_registro
                    ),
                    data_type=as_firmware_data_type(variable.tipo_dato),
                    # The firmware reads `gain` as a string, and normalising
                    # the Decimal keeps "1.000000" from reaching it as noise.
                    gain=str(variable.escala.normalize()),
                    unit=variable.unidad,
                    register_type=variable.tipo_registro.value,
                )
                for variable in sorted(
                    equipment.variables, key=lambda item: item.registro_modbus
                )
            },
        )


def compute_config_version(config: GatewayConfigResponse) -> str:
    """Return a stable fingerprint of a configuration document.

    `generated_at` is excluded on purpose: it changes on every request, and a
    version that changed every time would make the gateway reapply a
    configuration that is in fact identical.
    """
    payload = config.model_dump(mode="json", exclude={"generated_at"})
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
