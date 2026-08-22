"""The firmware's own endpoints.

Reached by the gateway itself and by nobody else: it exchanges its long-lived
credential for a short-lived token, reports in, fetches its configuration, and
asks whether a firmware update is waiting for it. Tokens carry the `gateway`
audience, so they are useless on the CRM and on the monitoring web.

Every route answers the same way when the uuid in the path is not the one
inside the token: **404**, never 403. Confirming that another gateway exists
would let one credential enumerate the fleet.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps import (
    FirmwareUpdateServiceDep,
    GatewayConfigServiceDep,
    GatewayTokenServiceDep,
)
from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    NotFoundError,
)
from app.models import Gateway
from app.schemas.firmware import (
    FirmwareUpdateAck,
    FirmwareUpdateOrder,
    FirmwareUpdateStatus,
)
from app.schemas.gateway_config import (
    GatewayConfigAck,
    GatewayConfigResponse,
    GatewayConfigStatus,
    GatewayHeartbeat,
    GatewayHeartbeatAck,
    GatewayTokenRequest,
    GatewayTokenResponse,
)
from app.services.gateway_config import compute_config_version

_bearer = HTTPBearer(auto_error=False)
router = APIRouter(prefix="/gateway", tags=["gateway"])


async def get_current_gateway(
    tokens: GatewayTokenServiceDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer)
    ] = None,
) -> Gateway:
    """Return the gateway behind a firmware token, or fail with 401."""
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Missing bearer token")
    return await tokens.gateway_from_token(credentials.credentials)


CurrentGatewayDep = Annotated[Gateway, Depends(get_current_gateway)]


@router.post("/token", response_model=GatewayTokenResponse)
async def issue_gateway_token(
    payload: GatewayTokenRequest, tokens: GatewayTokenServiceDep
) -> GatewayTokenResponse:
    """Exchange the gateway's credential for a short-lived token.

    The firmware calls this on boot and again whenever its token expires, so
    nobody has to carry a fresh token to the device by hand.
    """
    token = await tokens.issue_token(payload.gateway_uuid, payload.credential)
    return GatewayTokenResponse(
        access_token=token,
        expires_in=tokens.token_lifetime_seconds,
    )


@router.post("/{gateway_uuid}/heartbeat", response_model=GatewayHeartbeatAck)
async def gateway_heartbeat(
    gateway_uuid: uuid.UUID,
    payload: GatewayHeartbeat,
    gateway: CurrentGatewayDep,
    service: GatewayConfigServiceDep,
    firmware: FirmwareUpdateServiceDep,
) -> GatewayHeartbeatAck:
    """Report that the device is alive.

    Works whether or not the configuration download is enabled: a provisioned
    gateway has to keep being visible, and `estado` in the panel is derived
    from this timestamp. The response says whether a download is waiting, so a
    device that only heartbeats still learns it has work to do.
    """
    if gateway.uuid != gateway_uuid:
        raise NotFoundError(f"Gateway {gateway_uuid} not found")

    updated = await service.heartbeat(
        gateway, payload.firmware_version, payload.ip_actual
    )
    current, _ = await service.status_for_crm(updated)
    # El aviso de firmware nunca hace fallar el heartbeat: con las
    # actualizaciones apagadas la respuesta es la de siempre, sin novedad.
    try:
        orden = await firmware.pendiente(updated)
    except AuthorizationError:
        orden = None
    return GatewayHeartbeatAck(
        gateway_uuid=updated.uuid,
        ultima_conexion=updated.ultima_conexion,  # pyright: ignore[reportArgumentType]
        config_habilitada=updated.config_habilitada,
        config_version_actual=current,
        firmware_pendiente=orden is not None,
        firmware_version_objetivo=orden.version if orden else None,
        firmware_aplicar_desde=orden.aplicar_desde if orden else None,
    )


@router.get("/{gateway_uuid}/config", response_model=GatewayConfigResponse)
async def get_gateway_config(
    gateway_uuid: uuid.UUID,
    gateway: CurrentGatewayDep,
    service: GatewayConfigServiceDep,
    response: Response,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> GatewayConfigResponse | Response:
    """Return the devices and registers this gateway has to read.

    The uuid in the path has to be the one inside the token: a gateway can only
    ever fetch its own configuration, and asking for somebody else's answers
    404 rather than admitting that it exists.

    Answers **304** when the caller already holds this version, so polling
    costs nothing and the gateway only reapplies on a real change.
    """
    config = await service.build(gateway, gateway_uuid)
    version = compute_config_version(config)
    await service.mark_seen(gateway)

    etag = f'"{version}"'
    if if_none_match is not None and if_none_match.strip() in (etag, version):
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag}
        )

    response.headers["ETag"] = etag
    return config.model_copy(update={"config_version": version})


@router.post("/{gateway_uuid}/config/ack", response_model=GatewayConfigStatus)
async def acknowledge_gateway_config(
    gateway_uuid: uuid.UUID,
    payload: GatewayConfigAck,
    gateway: CurrentGatewayDep,
    service: GatewayConfigServiceDep,
) -> GatewayConfigStatus:
    """Report that the configuration was written and is in use.

    Records the version and turns `config_habilitada` off, so the same document
    is not handed out again. Editing the configuration afterwards therefore
    needs the switch turned back on — the panel shows when that is pending.
    """
    if gateway.uuid != gateway_uuid:
        raise NotFoundError(f"Gateway {gateway_uuid} not found")

    updated = await service.acknowledge(gateway, payload.config_version)
    current, drifted = await service.status_for_crm(updated)
    return GatewayConfigStatus(
        gateway_id=updated.id,
        uuid=updated.uuid,
        config_habilitada=updated.config_habilitada,
        config_version_actual=current,
        config_version_aplicada=updated.config_version_aplicada,
        config_aplicada_en=updated.config_aplicada_en,
        ultima_conexion=updated.ultima_conexion,
        desactualizada=drifted,
    )


@router.get("/{gateway_uuid}/firmware", response_model=FirmwareUpdateOrder)
async def get_firmware_update(
    gateway_uuid: uuid.UUID,
    gateway: CurrentGatewayDep,
    firmware: FirmwareUpdateServiceDep,
) -> FirmwareUpdateOrder | Response:
    """Return the update this gateway has to install, if any.

    Three answers, and the firmware treats none of them as a failure:

    * **200** — an order, with the package, its checksum and the window it may
      be applied in. The device checks its own clock against that window.
    * **204** — nothing pending. The common case, on every poll.
    * **403** — updates are switched off for the whole fleet. Same vocabulary
      as the configuration download, so the firmware already knows it means
      "you are up to date", not "something broke".
    """
    if gateway.uuid != gateway_uuid:
        raise NotFoundError(f"Gateway {gateway_uuid} not found")

    orden = await firmware.pendiente(gateway)
    if orden is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return orden


@router.post("/{gateway_uuid}/firmware/ack", response_model=FirmwareUpdateStatus)
async def acknowledge_firmware_update(
    gateway_uuid: uuid.UUID,
    payload: FirmwareUpdateAck,
    gateway: CurrentGatewayDep,
    firmware: FirmwareUpdateServiceDep,
) -> FirmwareUpdateStatus:
    """Record what the device reports about the update it was given.

    The acknowledgement names the version it is talking about: between the
    download and the reboot there is a stretch the CRM cannot observe, and an
    ack that arrived late for a target that has since changed must not be
    written down as progress on the new one.
    """
    if gateway.uuid != gateway_uuid:
        raise NotFoundError(f"Gateway {gateway_uuid} not found")

    updated = await firmware.acuse(
        gateway, payload.estado, payload.version, payload.error
    )
    release = await firmware.objetivo(updated)
    return firmware.status(updated, release.version if release else None)
