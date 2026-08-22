"""Gateway endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps import (
    EnrollmentServiceDep,
    EquipmentServiceDep,
    FirmwareAdminServiceDep,
    FirmwareUpdateServiceDep,
    FleetServiceDep,
    GatewayConfigServiceDep,
    GatewayCredentialServiceDep,
    GatewayServiceDep,
    PaginationDep,
    ScopeDep,
)
from app.core.exceptions import AuthenticationError
from app.core.mqtt import get_bridge
from app.domain.enums import GatewayStatus
from app.models import Gateway
from app.schemas.common import Page
from app.schemas.enrollment import EnrollmentResponse, EnrollmentTokenIssued
from app.schemas.equipment import EquipmentCreate, EquipmentRead
from app.schemas.firmware import FirmwareUpdateStatus
from app.schemas.gateway import GatewayRead, GatewayUpdate
from app.schemas.gateway_config import (
    GatewayConfigStatus,
    GatewayCredentialCreated,
    GatewayCredentialRead,
)

# El bearer del enrolamiento, aparte del que usan las personas: son dos
# credenciales distintas y no deben poder usarse una en lugar de la otra.
_enrollment_bearer = HTTPBearer(
    auto_error=False, description="Token de enrolamiento, de un solo uso"
)

router = APIRouter(prefix="/gateways", tags=["gateways"])


@router.get("", response_model=Page[GatewayRead])
async def list_gateways(
    scope: ScopeDep,
    service: FleetServiceDep,
    pagination: PaginationDep,
    client_id: Annotated[uuid.UUID | None, Query()] = None,
    site_id: Annotated[uuid.UUID | None, Query()] = None,
    estado: Annotated[GatewayStatus | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[GatewayRead]:
    """List gateways across the whole fleet.

    `estado` filters on reachability, which is derived from the last contact:
    this is what answers "which of my gateways are down right now".
    """
    items, total = await service.list_gateways(
        scope,
        limit=pagination.limit,
        offset=pagination.offset,
        client_id=client_id,
        site_id=site_id,
        online=None if estado is None else estado is GatewayStatus.ONLINE,
        search=search,
    )
    return Page[GatewayRead](
        items=[GatewayRead.model_validate(item) for item in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/{gateway_id}", response_model=GatewayRead)
async def get_gateway(
    gateway_id: uuid.UUID, scope: ScopeDep, service: GatewayServiceDep
) -> GatewayRead:
    return GatewayRead.model_validate(await service.get(scope, gateway_id))


@router.patch("/{gateway_id}", response_model=GatewayRead)
async def update_gateway(
    gateway_id: uuid.UUID,
    payload: GatewayUpdate,
    scope: ScopeDep,
    service: GatewayServiceDep,
    config: GatewayConfigServiceDep,
) -> GatewayRead:
    changes = payload.model_dump(exclude_unset=True)
    gateway = await service.update(scope, gateway_id, changes)

    # Turning the download on is the moment somebody says "go fetch it", so it
    # is where the notice belongs. It is best effort: if the broker is down the
    # gateway finds out on its next poll instead.
    if changes.get("config_habilitada") is True:
        bridge = get_bridge()
        if bridge is not None:
            version, _ = await config.status_for_crm(gateway)
            await bridge.notify_config_changed(gateway.uuid, version)

    return GatewayRead.model_validate(gateway)


@router.delete("/{gateway_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_gateway(
    gateway_id: uuid.UUID, scope: ScopeDep, service: GatewayServiceDep
) -> None:
    await service.delete(scope, gateway_id)


@router.get("/{gateway_id}/equipment", response_model=Page[EquipmentRead])
async def list_gateway_equipment(
    gateway_id: uuid.UUID,
    scope: ScopeDep,
    service: EquipmentServiceDep,
    pagination: PaginationDep,
) -> Page[EquipmentRead]:
    items, total = await service.list_for_gateway(
        scope, gateway_id, limit=pagination.limit, offset=pagination.offset
    )
    return Page[EquipmentRead](
        items=[EquipmentRead.model_validate(item) for item in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.post(
    "/{gateway_id}/equipment",
    response_model=EquipmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_gateway_equipment(
    gateway_id: uuid.UUID,
    payload: EquipmentCreate,
    scope: ScopeDep,
    service: EquipmentServiceDep,
) -> EquipmentRead:
    equipment = await service.create(scope, gateway_id, payload.model_dump())
    return EquipmentRead.model_validate(equipment)


@router.get("/{gateway_id}/credential", response_model=GatewayCredentialRead)
async def get_gateway_credential(
    gateway_id: uuid.UUID, scope: ScopeDep, service: GatewayCredentialServiceDep
) -> GatewayCredentialRead:
    """Whether the gateway has a credential, and when it was issued."""
    return _as_credential(await service.get(scope, gateway_id))


@router.post(
    "/{gateway_id}/credential",
    response_model=GatewayCredentialCreated,
    status_code=status.HTTP_201_CREATED,
)
async def issue_gateway_credential(
    gateway_id: uuid.UUID, scope: ScopeDep, service: GatewayCredentialServiceDep
) -> GatewayCredentialCreated:
    """Issue a credential, shown only here.

    Regenerating replaces the previous one at once, so the gateway stops
    working until the new secret is loaded into it.
    """
    gateway, credential = await service.issue(scope, gateway_id)
    return GatewayCredentialCreated(
        **_as_credential(gateway).model_dump(), credential=credential
    )


@router.post(
    "/enroll",
    response_model=EnrollmentResponse,
    # Sin `ScopeDep` a propósito: quien llama no es un usuario ni una cuenta
    # de servicio, es un equipo en una sede con un token de un solo uso. Si
    # colgara de la cadena de autenticación normal, ese token pasaría a valer
    # en otras rutas — que es exactamente lo que no debe pasar.
    #
    # Va antes que `/{gateway_id}/…` porque FastAPI resuelve por orden de
    # declaración: al revés, "enroll" se leería como un gateway_id y fallaría
    # con un 422 de UUID inválido.
)
async def enroll_gateway(
    request: Request,
    service: EnrollmentServiceDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_enrollment_bearer)
    ] = None,
) -> EnrollmentResponse:
    """Canjear un token de enrolamiento por la configuración del equipo.

    Devuelve el `.env` completo, incluidas las variables que el equipo llena
    solo — con el valor vacío y su `origen`, que es la instrucción para el
    instalador.

    Gastar el token y rotar la credencial ocurren juntos. Si la respuesta no
    llega, hay que emitir otro token: permitir reintentar el canje sería tener
    un token reutilizable.
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Falta el token de enrolamiento")

    desde = request.client.host if request.client else "desconocido"
    gateway, env, release = await service.exchange(credentials.credentials, desde)
    return EnrollmentResponse(gateway_uuid=str(gateway.uuid), env=env, release=release)


@router.post(
    "/{gateway_id}/enrollment-token",
    response_model=EnrollmentTokenIssued,
    status_code=status.HTTP_201_CREATED,
)
async def issue_enrollment_token(
    gateway_id: uuid.UUID, scope: ScopeDep, service: EnrollmentServiceDep
) -> EnrollmentTokenIssued:
    """Emitir el permiso con el que este gateway se configura solo.

    Devuelve el comando entero, listo para pasarle al técnico. Se ve una sola
    vez: de acá en más solo queda su hash.

    Emitir uno nuevo vence los anteriores de este gateway — si no, cada
    intento fallido dejaría un token vivo dando vueltas.

    Distinto de `/credential`: aquel entrega el secreto para configurar el
    equipo a mano; este hace que el equipo se configure solo, y **rota la
    credencial durante el canje**. Usar los dos seguidos invalida el primero.
    """
    token, expira_en, comando = await service.issue(scope, gateway_id)
    return EnrollmentTokenIssued(
        token=token, expira_en=expira_en, comando=comando
    )


@router.delete("/{gateway_id}/credential", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_gateway_credential(
    gateway_id: uuid.UUID, scope: ScopeDep, service: GatewayCredentialServiceDep
) -> None:
    """Drop the credential. The gateway can no longer obtain a token."""
    await service.revoke(scope, gateway_id)


def _as_credential(gateway: Gateway) -> GatewayCredentialRead:
    return GatewayCredentialRead(
        gateway_id=gateway.id,
        uuid=gateway.uuid,
        numero_serie=gateway.numero_serie,
        tiene_credencial=gateway.credential_hash is not None,
        credential_emitida_en=gateway.credential_emitida_en,
        config_habilitada=gateway.config_habilitada,
    )


@router.get("/{gateway_id}/config-status", response_model=GatewayConfigStatus)
async def get_gateway_config_status(
    gateway_id: uuid.UUID,
    scope: ScopeDep,
    credentials: GatewayCredentialServiceDep,
    service: GatewayConfigServiceDep,
) -> GatewayConfigStatus:
    """Whether the device is running what the CRM currently has for it.

    Applying a configuration turns the download switch off, so an edit made
    afterwards sits undelivered until somebody turns it back on. This is what
    makes that visible instead of silent.
    """
    gateway = await credentials.get(scope, gateway_id)
    current, drifted = await service.status_for_crm(gateway)
    return GatewayConfigStatus(
        gateway_id=gateway.id,
        uuid=gateway.uuid,
        config_habilitada=gateway.config_habilitada,
        config_version_actual=current,
        config_version_aplicada=gateway.config_version_aplicada,
        config_aplicada_en=gateway.config_aplicada_en,
        ultima_conexion=gateway.ultima_conexion,
        desactualizada=drifted,
    )


@router.get("/{gateway_id}/firmware", response_model=FirmwareUpdateStatus)
async def get_gateway_firmware_status(
    gateway_id: uuid.UUID,
    scope: ScopeDep,
    credentials: GatewayCredentialServiceDep,
    firmware: FirmwareUpdateServiceDep,
) -> FirmwareUpdateStatus:
    """En qué anda la actualización de este equipo.

    Lo reporta el equipo, así que puede quedarse quieto: un `descargando` de
    hace dos horas es un equipo que se quedó en el camino, y eso se ve acá.
    """
    gateway = await credentials.get(scope, gateway_id)
    release = await firmware.objetivo(gateway)
    return firmware.status(gateway, release.version if release else None)


@router.delete("/{gateway_id}/firmware", response_model=FirmwareUpdateStatus)
async def cancel_gateway_firmware_update(
    gateway_id: uuid.UUID,
    scope: ScopeDep,
    credentials: GatewayCredentialServiceDep,
    admin: FirmwareAdminServiceDep,
    firmware: FirmwareUpdateServiceDep,
) -> FirmwareUpdateStatus:
    """Sacarle la actualización que tenía pedida.

    No se puede una vez que empezó a aplicarla: el paquete ya está en el disco
    y el equipo se está reiniciando con él.
    """
    gateway = await credentials.get(scope, gateway_id)
    actualizado = await admin.cancel(scope, gateway)
    return firmware.status(actualizado, None)
