"""Use cases for the client hierarchy.

Every method takes the caller's :class:`AccessScope`. Two rules are enforced
here rather than in the routers, so no endpoint can forget them:

* a `cliente` login only ever reaches its own company's rows;
* only staff roles write anything.

Reads of something the caller may not see raise `NotFoundError`, not
`AuthorizationError`: a 403 would confirm the row exists.
"""

import uuid
from typing import Any

from app.core.exceptions import (
    AlreadyExistsError,
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from app.domain.access import AccessScope
from app.domain.modbus import (
    RTU_FIELDS,
    TCP_FIELDS,
    TransportFieldError,
    describe_endpoint,
    normalize_transport_fields,
)
from app.models import Client, Equipment, Gateway, Site, Variable
from app.repositories.hierarchy import (
    ClientRepository,
    EquipmentRepository,
    GatewayRepository,
    SiteRepository,
    VariableRepository,
)


def _require_write(scope: AccessScope) -> None:
    if not scope.can_write:
        raise AuthorizationError(
            f"Role '{scope.principal}' cannot modify administrative data"
        )


class ClientService:
    """Clients: the tenant boundary itself."""

    def __init__(self, clients: ClientRepository) -> None:
        self._clients = clients

    async def list(
        self, scope: AccessScope, *, limit: int, offset: int
    ) -> tuple[list[Client], int]:
        return await self._clients.list_page(
            limit=limit, offset=offset, only_client_id=scope.visible_client_id
        )

    async def get(self, scope: AccessScope, client_id: uuid.UUID) -> Client:
        client = await self._clients.get(client_id)
        if client is None or not scope.may_read_client(client.id):
            raise NotFoundError(f"Client {client_id} not found")
        return client

    async def create(self, scope: AccessScope, data: dict[str, Any]) -> Client:
        _require_write(scope)
        if await self._clients.name_taken(data["nombre_empresa"]):
            raise AlreadyExistsError(
                f"A client named '{data['nombre_empresa']}' already exists"
            )
        return await self._clients.add(Client(**data))

    async def update(
        self, scope: AccessScope, client_id: uuid.UUID, changes: dict[str, Any]
    ) -> Client:
        _require_write(scope)
        client = await self.get(scope, client_id)
        new_name = changes.get("nombre_empresa")
        if new_name is not None and await self._clients.name_taken(
            new_name, excluding=client_id
        ):
            raise AlreadyExistsError(f"A client named '{new_name}' already exists")
        return await self._clients.update(client, changes)


class SiteService:
    """Sites, always reached through their client."""

    def __init__(self, sites: SiteRepository, clients: ClientService) -> None:
        self._sites = sites
        self._clients = clients

    async def list_for_client(
        self, scope: AccessScope, client_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Site], int]:
        # Resolving the client first turns "not yours" into the same 404 as
        # "does not exist".
        await self._clients.get(scope, client_id)
        return await self._sites.list_for_client(client_id, limit=limit, offset=offset)

    async def get(self, scope: AccessScope, site_id: uuid.UUID) -> Site:
        site = await self._sites.get(site_id)
        if site is None or not scope.may_read_client(site.client_id):
            raise NotFoundError(f"Site {site_id} not found")
        return site

    async def create(
        self, scope: AccessScope, client_id: uuid.UUID, data: dict[str, Any]
    ) -> Site:
        _require_write(scope)
        await self._clients.get(scope, client_id)
        if await self._sites.name_taken_in_client(client_id, data["nombre"]):
            raise AlreadyExistsError(
                f"Client already has a site named '{data['nombre']}'"
            )
        return await self._sites.add(Site(client_id=client_id, **data))

    async def update(
        self, scope: AccessScope, site_id: uuid.UUID, changes: dict[str, Any]
    ) -> Site:
        _require_write(scope)
        site = await self.get(scope, site_id)
        new_name = changes.get("nombre")
        if new_name is not None and await self._sites.name_taken_in_client(
            site.client_id, new_name, excluding=site_id
        ):
            raise AlreadyExistsError(f"Client already has a site named '{new_name}'")
        return await self._sites.update(site, changes)

    async def delete(self, scope: AccessScope, site_id: uuid.UUID) -> None:
        _require_write(scope)
        await self._sites.delete(await self.get(scope, site_id))


class GatewayService:
    """Gateways installed at a site."""

    def __init__(self, gateways: GatewayRepository, sites: SiteService) -> None:
        self._gateways = gateways
        self._sites = sites

    async def list_for_site(
        self, scope: AccessScope, site_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Gateway], int]:
        await self._sites.get(scope, site_id)
        return await self._gateways.list_for_site(site_id, limit=limit, offset=offset)

    async def get(self, scope: AccessScope, gateway_id: uuid.UUID) -> Gateway:
        gateway = await self._gateways.get(gateway_id)
        if gateway is None:
            raise NotFoundError(f"Gateway {gateway_id} not found")
        owner = await self._gateways.owning_client_id(gateway_id)
        if owner is None or not scope.may_read_client(owner):
            raise NotFoundError(f"Gateway {gateway_id} not found")
        return gateway

    async def create(
        self, scope: AccessScope, site_id: uuid.UUID, data: dict[str, Any]
    ) -> Gateway:
        _require_write(scope)
        await self._sites.get(scope, site_id)
        if await self._gateways.serial_taken(data["numero_serie"]):
            raise AlreadyExistsError(
                f"A gateway with serial '{data['numero_serie']}' already exists"
            )
        return await self._gateways.add(Gateway(site_id=site_id, **data))

    async def update(
        self, scope: AccessScope, gateway_id: uuid.UUID, changes: dict[str, Any]
    ) -> Gateway:
        _require_write(scope)
        gateway = await self.get(scope, gateway_id)
        new_serial = changes.get("numero_serie")
        if new_serial is not None and await self._gateways.serial_taken(
            new_serial, excluding=gateway_id
        ):
            raise AlreadyExistsError(
                f"A gateway with serial '{new_serial}' already exists"
            )
        return await self._gateways.update(gateway, changes)

    async def delete(self, scope: AccessScope, gateway_id: uuid.UUID) -> None:
        _require_write(scope)
        await self._gateways.delete(await self.get(scope, gateway_id))


class EquipmentService:
    """Modbus devices hanging off a gateway."""

    def __init__(
        self, equipment: EquipmentRepository, gateways: GatewayService
    ) -> None:
        self._equipment = equipment
        self._gateways = gateways

    async def list_for_gateway(
        self, scope: AccessScope, gateway_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Equipment], int]:
        await self._gateways.get(scope, gateway_id)
        return await self._equipment.list_for_gateway(
            gateway_id, limit=limit, offset=offset
        )

    async def get(self, scope: AccessScope, equipment_id: uuid.UUID) -> Equipment:
        equipment = await self._equipment.get(equipment_id)
        if equipment is None:
            raise NotFoundError(f"Equipment {equipment_id} not found")
        owner = await self._equipment.owning_client_id(equipment_id)
        if owner is None or not scope.may_read_client(owner):
            raise NotFoundError(f"Equipment {equipment_id} not found")
        return equipment

    async def _refuse_duplicate_address(
        self,
        gateway_id: uuid.UUID,
        state: dict[str, Any],
        *,
        excluding: uuid.UUID | None = None,
    ) -> None:
        if await self._equipment.address_taken(
            gateway_id,
            state["transporte"],
            state["modbus_id"],
            puerto=state.get("puerto"),
            host=state.get("host"),
            puerto_tcp=state.get("puerto_tcp"),
            excluding=excluding,
        ):
            raise AlreadyExistsError(
                f"Modbus id {state['modbus_id']} is already used "
                f"on {describe_endpoint(state)}"
            )

    async def create(
        self, scope: AccessScope, gateway_id: uuid.UUID, data: dict[str, Any]
    ) -> Equipment:
        _require_write(scope)
        await self._gateways.get(scope, gateway_id)
        await self._refuse_duplicate_address(gateway_id, data)
        return await self._equipment.add(Equipment(gateway_id=gateway_id, **data))

    async def update(
        self, scope: AccessScope, equipment_id: uuid.UUID, changes: dict[str, Any]
    ) -> Equipment:
        """Apply a patch, validating the row's resulting state.

        Transport coherence is checked here rather than in the schema: a patch
        that only flips `transporte` still has to leave a usable row, and the
        stored values are not visible from a request model.
        """
        _require_write(scope)
        equipment = await self.get(scope, equipment_id)

        current = {
            field: getattr(equipment, field)
            for field in (
                "transporte",
                "modbus_id",
                "puerto",
                "baudrate",
                "paridad",
                "bits",
                "stop_bits",
                "host",
                "puerto_tcp",
            )
        }
        merged = {**current, **changes}
        # Switching transport clears the fields of the old one, so a stale
        # serial port cannot survive on a device that now speaks TCP.
        if changes.get("transporte") not in (None, equipment.transporte):
            for field in RTU_FIELDS + TCP_FIELDS:
                if field not in changes:
                    merged[field] = None
        try:
            merged = normalize_transport_fields(merged["transporte"], merged)
        except TransportFieldError as exc:
            raise ValidationError(str(exc)) from exc

        await self._refuse_duplicate_address(
            equipment.gateway_id, merged, excluding=equipment_id
        )
        return await self._equipment.update(
            equipment,
            {
                **changes,
                **{
                    field: merged[field]
                    for field in (RTU_FIELDS + TCP_FIELDS + ("transporte",))
                },
            },
        )

    async def delete(self, scope: AccessScope, equipment_id: uuid.UUID) -> None:
        _require_write(scope)
        await self._equipment.delete(await self.get(scope, equipment_id))


class VariableService:
    """The individual readings an equipment exposes."""

    def __init__(
        self, variables: VariableRepository, equipment: EquipmentService
    ) -> None:
        self._variables = variables
        self._equipment = equipment

    async def list_for_equipment(
        self, scope: AccessScope, equipment_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Variable], int]:
        await self._equipment.get(scope, equipment_id)
        return await self._variables.list_for_equipment(
            equipment_id, limit=limit, offset=offset
        )

    async def get(self, scope: AccessScope, variable_id: uuid.UUID) -> Variable:
        variable = await self._variables.get(variable_id)
        if variable is None:
            raise NotFoundError(f"Variable {variable_id} not found")
        owner = await self._variables.owning_client_id(variable_id)
        if owner is None or not scope.may_read_client(owner):
            raise NotFoundError(f"Variable {variable_id} not found")
        return variable

    async def create(
        self, scope: AccessScope, equipment_id: uuid.UUID, data: dict[str, Any]
    ) -> Variable:
        _require_write(scope)
        await self._equipment.get(scope, equipment_id)
        if await self._variables.name_taken_in_equipment(equipment_id, data["nombre"]):
            raise AlreadyExistsError(
                f"Equipment already has a variable named '{data['nombre']}'"
            )
        return await self._variables.add(Variable(equipment_id=equipment_id, **data))

    async def update(
        self, scope: AccessScope, variable_id: uuid.UUID, changes: dict[str, Any]
    ) -> Variable:
        _require_write(scope)
        variable = await self.get(scope, variable_id)
        new_name = changes.get("nombre")
        if new_name is not None and await self._variables.name_taken_in_equipment(
            variable.equipment_id, new_name, excluding=variable_id
        ):
            raise AlreadyExistsError(
                f"Equipment already has a variable named '{new_name}'"
            )
        return await self._variables.update(variable, changes)

    async def delete(self, scope: AccessScope, variable_id: uuid.UUID) -> None:
        _require_write(scope)
        await self._variables.delete(await self.get(scope, variable_id))
