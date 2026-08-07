"""Fleet-wide listings.

The hierarchy is navigated downwards — client, site, gateway, device — which
answers "what does this client have". It cannot answer "what is broken", the
question a technician actually opens the panel with. These use cases read
across every client the caller may see.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime

from app.core.exceptions import AuthorizationError
from app.domain.access import AccessScope
from app.domain.firmware import as_firmware_address
from app.domain.fleet import FleetLevel, reaches
from app.domain.gateway_status import OFFLINE_AFTER
from app.models import Client, Equipment, Gateway, Site, Variable
from app.repositories.hierarchy import FleetRepository
from app.schemas.common import Page
from app.schemas.fleet import (
    ClientFleet,
    EquipmentFleet,
    GatewayFleet,
    SiteFleet,
    VariableFleet,
)


class FleetService:
    """Lists sites, gateways and devices across the whole platform."""

    def __init__(self, fleet: FleetRepository) -> None:
        self._fleet = fleet

    async def list_sites(
        self,
        scope: AccessScope,
        *,
        limit: int,
        offset: int,
        client_id: uuid.UUID | None = None,
        search: str | None = None,
    ) -> tuple[list[Site], int]:
        return await self._fleet.list_sites(
            limit=limit,
            offset=offset,
            only_client_id=scope.visible_client_id,
            client_id=client_id,
            search=search,
        )

    async def list_gateways(
        self,
        scope: AccessScope,
        *,
        limit: int,
        offset: int,
        client_id: uuid.UUID | None = None,
        site_id: uuid.UUID | None = None,
        online: bool | None = None,
        search: str | None = None,
    ) -> tuple[list[Gateway], int]:
        """List gateways, optionally only the reachable or the silent ones.

        The cut-off is computed here rather than stored, so the answer is
        true at the moment of asking instead of the moment somebody last
        edited a row.
        """
        return await self._fleet.list_gateways(
            limit=limit,
            offset=offset,
            only_client_id=scope.visible_client_id,
            client_id=client_id,
            site_id=site_id,
            seen_since=datetime.now(UTC) - OFFLINE_AFTER,
            online=online,
            search=search,
        )

    async def list_equipment(
        self,
        scope: AccessScope,
        *,
        limit: int,
        offset: int,
        gateway_id: uuid.UUID | None = None,
        search: str | None = None,
    ) -> tuple[list[Equipment], int]:
        return await self._fleet.list_equipment(
            limit=limit,
            offset=offset,
            only_client_id=scope.visible_client_id,
            gateway_id=gateway_id,
            search=search,
        )

    async def client_trees(
        self,
        scope: AccessScope,
        *,
        limit: int,
        offset: int,
        level: FleetLevel,
        client_id: uuid.UUID | None = None,
        search: str | None = None,
    ) -> tuple[list[ClientFleet], int]:
        """Return whole installations, nested, for everything the caller sees.

        The projection happens here rather than in the router because how far
        down the tree goes decides which relationships were loaded at all: the
        unloaded ones must be reported as absent, not walked into.
        """
        if not scope.can_read_fleet:
            raise AuthorizationError(f"Role '{scope.principal}' cannot read the fleet")
        clients, total = await self._fleet.list_client_trees(
            limit=limit,
            offset=offset,
            level=level,
            only_client_id=scope.visible_client_id,
            client_id=client_id,
            search=search,
        )
        return [_as_client(client, level) for client in clients], total


def _as_variable(variable: Variable) -> VariableFleet:
    return VariableFleet(
        id=variable.id,
        nombre=variable.nombre,
        registro_modbus=variable.registro_modbus,
        notacion_registro=variable.notacion_registro,
        registro_display=as_firmware_address(
            variable.registro_modbus, variable.notacion_registro
        ),
        tipo_registro=variable.tipo_registro,
        tipo_dato=variable.tipo_dato,
        escala=variable.escala,
        unidad=variable.unidad,
        magnitud=variable.magnitud,
        fase=variable.fase,
        acumulativa=variable.acumulativa,
    )


def _as_equipment(equipment: Equipment, level: FleetLevel) -> EquipmentFleet:
    return EquipmentFleet(
        id=equipment.id,
        nombre_dispositivo=equipment.nombre_dispositivo,
        device_type=equipment.device_type,
        tipo=equipment.tipo,
        marca=equipment.marca,
        modelo=equipment.modelo,
        modbus_id=equipment.modbus_id,
        transporte=equipment.transporte,
        variables=(
            [_as_variable(item) for item in equipment.variables]
            if reaches(level, FleetLevel.VARIABLES)
            else None
        ),
    )


def _as_gateway(gateway: Gateway, level: FleetLevel) -> GatewayFleet:
    return GatewayFleet(
        id=gateway.id,
        uuid=gateway.uuid,
        numero_serie=gateway.numero_serie,
        firmware_version=gateway.firmware_version,
        estado=gateway.estado,
        ultima_conexion=gateway.ultima_conexion,
        ip_actual=gateway.ip_actual,
        intervalo_lectura_segundos=gateway.intervalo_lectura_segundos,
        hora_inicio=gateway.hora_inicio,
        hora_fin=gateway.hora_fin,
        equipment=(
            [_as_equipment(item, level) for item in gateway.equipment]
            if reaches(level, FleetLevel.EQUIPOS)
            else None
        ),
    )


def _as_site(site: Site, level: FleetLevel) -> SiteFleet:
    return SiteFleet(
        id=site.id,
        nombre=site.nombre,
        direccion=site.direccion,
        timezone=site.timezone,
        latitud=site.latitud,
        longitud=site.longitud,
        gateways=(
            [_as_gateway(item, level) for item in site.gateways]
            if reaches(level, FleetLevel.GATEWAYS)
            else None
        ),
    )


def _as_client(client: Client, level: FleetLevel) -> ClientFleet:
    return ClientFleet(
        id=client.id,
        nombre_empresa=client.nombre_empresa,
        estado=client.estado,
        puede_ver_consumo=client.puede_ver_consumo,
        sites=[_as_site(item, level) for item in client.sites],
    )


def compute_fleet_version(page: Page[ClientFleet]) -> str:
    """Return a stable fingerprint of a fleet document.

    The whole page is fingerprinted, `total` included: a client created beyond
    the current window changes nothing about the rows on it, and answering 304
    would leave the caller with a pager that quietly disagrees with reality.

    Unlike the gateway configuration there is no timestamp to exclude —
    everything here is content. `estado` is derived from the last contact, so
    the fingerprint does change when a gateway goes quiet. That is a real
    change to the answer, and whoever polls this wants to hear about it.
    """
    canonical = json.dumps(
        page.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
