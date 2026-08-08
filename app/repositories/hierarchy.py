"""Repositories for the Client -> Site -> Gateway -> Equipment -> Variable chain."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Row, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.interfaces import LoaderOption

from app.domain.enums import ModbusTransport
from app.domain.fleet import FleetLevel, reaches
from app.models import Client, Equipment, Gateway, Site, Variable
from app.repositories.base import BaseRepository


class ClientRepository(BaseRepository[Client]):
    model = Client

    async def list_page(
        self, *, limit: int, offset: int, only_client_id: uuid.UUID | None = None
    ) -> tuple[list[Client], int]:
        conditions = []
        if only_client_id is not None:
            conditions.append(Client.id == only_client_id)
        return await self.list_where(
            *conditions, limit=limit, offset=offset, order_by=Client.nombre_empresa
        )

    async def name_taken(
        self, nombre_empresa: str, *, excluding: uuid.UUID | None = None
    ) -> bool:
        conditions = [Client.nombre_empresa == nombre_empresa]
        if excluding is not None:
            conditions.append(Client.id != excluding)
        return await self.exists_where(*conditions)


class SiteRepository(BaseRepository[Site]):
    model = Site

    async def list_for_client(
        self, client_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Site], int]:
        return await self.list_where(
            Site.client_id == client_id,
            limit=limit,
            offset=offset,
            order_by=Site.nombre,
        )

    async def name_taken_in_client(
        self, client_id: uuid.UUID, nombre: str, *, excluding: uuid.UUID | None = None
    ) -> bool:
        conditions = [Site.client_id == client_id, Site.nombre == nombre]
        if excluding is not None:
            conditions.append(Site.id != excluding)
        return await self.exists_where(*conditions)


class GatewayRepository(BaseRepository[Gateway]):
    model = Gateway

    async def list_for_site(
        self, site_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Gateway], int]:
        return await self.list_where(
            Gateway.site_id == site_id,
            limit=limit,
            offset=offset,
            order_by=Gateway.numero_serie,
        )

    async def serial_taken(
        self, numero_serie: str, *, excluding: uuid.UUID | None = None
    ) -> bool:
        conditions = [Gateway.numero_serie == numero_serie]
        if excluding is not None:
            conditions.append(Gateway.id != excluding)
        return await self.exists_where(*conditions)

    async def owning_client_id(self, gateway_id: uuid.UUID) -> uuid.UUID | None:
        """Return the client a gateway ultimately belongs to.

        One join instead of walking the chain object by object, which under
        asyncio would mean several round trips.
        """
        result = await self._session.execute(
            select(Site.client_id)
            .join(Gateway, Gateway.site_id == Site.id)
            .where(Gateway.id == gateway_id)
        )
        return result.scalar_one_or_none()


class EquipmentRepository(BaseRepository[Equipment]):
    model = Equipment

    async def list_for_gateway(
        self, gateway_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Equipment], int]:
        return await self.list_where(
            Equipment.gateway_id == gateway_id,
            limit=limit,
            offset=offset,
            order_by=Equipment.modbus_id,
        )

    async def address_taken(
        self,
        gateway_id: uuid.UUID,
        transporte: ModbusTransport,
        modbus_id: int,
        *,
        puerto: str | None = None,
        host: str | None = None,
        puerto_tcp: int | None = None,
        excluding: uuid.UUID | None = None,
    ) -> bool:
        """Whether that unit id is already answering at the same endpoint.

        The tuple that identifies a device differs per transport: a serial port
        for RTU, a host and port for TCP. An RTU and a TCP device may therefore
        share a `modbus_id` on the same gateway without colliding.
        """
        conditions = [
            Equipment.gateway_id == gateway_id,
            Equipment.transporte == transporte,
            Equipment.modbus_id == modbus_id,
        ]
        if transporte is ModbusTransport.RTU:
            conditions.append(Equipment.puerto == puerto)
        else:
            conditions.append(Equipment.host == host)
            conditions.append(Equipment.puerto_tcp == puerto_tcp)
        if excluding is not None:
            conditions.append(Equipment.id != excluding)
        return await self.exists_where(*conditions)

    async def owning_client_id(self, equipment_id: uuid.UUID) -> uuid.UUID | None:
        result = await self._session.execute(
            select(Site.client_id)
            .join(Gateway, Gateway.site_id == Site.id)
            .join(Equipment, Equipment.gateway_id == Gateway.id)
            .where(Equipment.id == equipment_id)
        )
        return result.scalar_one_or_none()


class VariableRepository(BaseRepository[Variable]):
    model = Variable

    async def list_for_equipment(
        self, equipment_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Variable], int]:
        return await self.list_where(
            Variable.equipment_id == equipment_id,
            limit=limit,
            offset=offset,
            order_by=Variable.nombre,
        )

    async def name_taken_in_equipment(
        self,
        equipment_id: uuid.UUID,
        nombre: str,
        *,
        excluding: uuid.UUID | None = None,
    ) -> bool:
        conditions = [
            Variable.equipment_id == equipment_id,
            Variable.nombre == nombre,
        ]
        if excluding is not None:
            conditions.append(Variable.id != excluding)
        return await self.exists_where(*conditions)

    async def owning_client_id(self, variable_id: uuid.UUID) -> uuid.UUID | None:
        result = await self._session.execute(
            select(Site.client_id)
            .join(Gateway, Gateway.site_id == Site.id)
            .join(Equipment, Equipment.gateway_id == Gateway.id)
            .join(Variable, Variable.equipment_id == Equipment.id)
            .where(Variable.id == variable_id)
        )
        return result.scalar_one_or_none()


class FleetRepository:
    """Cross-cutting queries the navigation by parent cannot answer.

    Walking client -> site -> gateway from the panel to find the offline
    devices means one request per node. These read the whole fleet at once,
    still confined to what the caller may see.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _like(term: str) -> str:
        return f"%{term.strip()}%"

    async def _page(
        self, statement: Select[Any], counter: Select[Any], *, limit: int, offset: int
    ) -> tuple[list[Any], int]:
        rows = (
            (await self._session.execute(statement.limit(limit).offset(offset)))
            .scalars()
            .all()
        )
        total = (await self._session.execute(counter)).scalar_one()
        return list(rows), total

    async def list_sites(
        self,
        *,
        limit: int,
        offset: int,
        only_client_id: uuid.UUID | None = None,
        client_id: uuid.UUID | None = None,
        search: str | None = None,
    ) -> tuple[list[Site], int]:
        conditions = []
        if only_client_id is not None:
            conditions.append(Site.client_id == only_client_id)
        if client_id is not None:
            conditions.append(Site.client_id == client_id)
        if search:
            conditions.append(Site.nombre.ilike(self._like(search)))

        statement = select(Site).where(*conditions).order_by(Site.nombre)
        counter = select(func.count()).select_from(Site).where(*conditions)
        return await self._page(statement, counter, limit=limit, offset=offset)

    async def list_gateways(
        self,
        *,
        limit: int,
        offset: int,
        only_client_id: uuid.UUID | None = None,
        client_id: uuid.UUID | None = None,
        site_id: uuid.UUID | None = None,
        seen_since: datetime | None = None,
        online: bool | None = None,
        search: str | None = None,
    ) -> tuple[list[Gateway], int]:
        """List gateways across every site, optionally by reachability.

        Reachability is a property of `ultima_conexion`, so filtering by it is
        a comparison against the cut-off the caller passes in rather than a
        stored flag.
        """
        conditions = []
        # Both are applied, never one instead of the other: a filter narrows
        # what the caller may see, it can never widen it. A confined caller
        # asking for somebody else's client gets an empty page, not its own.
        for wanted in (only_client_id, client_id):
            if wanted is not None:
                conditions.append(
                    Gateway.site_id.in_(select(Site.id).where(Site.client_id == wanted))
                )
        if site_id is not None:
            conditions.append(Gateway.site_id == site_id)
        if search:
            conditions.append(Gateway.numero_serie.ilike(self._like(search)))
        if online is not None and seen_since is not None:
            conditions.append(
                Gateway.ultima_conexion >= seen_since
                if online
                else _sin_conexion(seen_since)
            )

        statement = select(Gateway).where(*conditions).order_by(Gateway.numero_serie)
        counter = select(func.count()).select_from(Gateway).where(*conditions)
        return await self._page(statement, counter, limit=limit, offset=offset)

    async def list_equipment(
        self,
        *,
        limit: int,
        offset: int,
        only_client_id: uuid.UUID | None = None,
        gateway_id: uuid.UUID | None = None,
        search: str | None = None,
    ) -> tuple[list[Equipment], int]:
        conditions = []
        if only_client_id is not None:
            conditions.append(
                Equipment.gateway_id.in_(
                    select(Gateway.id).where(
                        Gateway.site_id.in_(
                            select(Site.id).where(Site.client_id == only_client_id)
                        )
                    )
                )
            )
        if gateway_id is not None:
            conditions.append(Equipment.gateway_id == gateway_id)
        if search:
            conditions.append(
                or_(
                    Equipment.nombre_dispositivo.ilike(self._like(search)),
                    Equipment.marca.ilike(self._like(search)),
                    Equipment.modelo.ilike(self._like(search)),
                )
            )

        statement = (
            select(Equipment).where(*conditions).order_by(Equipment.nombre_dispositivo)
        )
        counter = select(func.count()).select_from(Equipment).where(*conditions)
        return await self._page(statement, counter, limit=limit, offset=offset)

    @staticmethod
    def _tree_loader(level: FleetLevel) -> LoaderOption:
        """Build the eager-loading chain down to ``level``.

        `selectinload` emits one extra SELECT per level rather than one per
        row, so the cost is four queries whatever the size of the fleet. The
        relationships are declared `lazy="raise"`, so anything not loaded here
        fails loudly instead of quietly issuing a query mid-serialisation.
        """
        loader = selectinload(Client.sites)
        if reaches(level, FleetLevel.GATEWAYS):
            loader = loader.selectinload(Site.gateways)
            if reaches(level, FleetLevel.EQUIPOS):
                loader = loader.selectinload(Gateway.equipment)
                if reaches(level, FleetLevel.VARIABLES):
                    loader = loader.selectinload(Equipment.variables)
        return loader

    async def list_client_trees(
        self,
        *,
        limit: int,
        offset: int,
        level: FleetLevel,
        only_client_id: uuid.UUID | None = None,
        client_id: uuid.UUID | None = None,
        search: str | None = None,
    ) -> tuple[list[Client], int]:
        """Read whole clients with their installations attached.

        Pagination is over clients, the root of the tree: a page of registers
        would be meaningless once nested, and a client is the unit a caller
        actually asks about.
        """
        conditions = []
        # Same rule as `list_gateways`: the confinement and the filter are both
        # applied. A caller pinned to one client that asks for another one gets
        # an empty page, never its own rows back under somebody else's name.
        for wanted in (only_client_id, client_id):
            if wanted is not None:
                conditions.append(Client.id == wanted)
        if search:
            conditions.append(Client.nombre_empresa.ilike(self._like(search)))

        statement = (
            select(Client)
            .where(*conditions)
            .options(self._tree_loader(level))
            .order_by(Client.nombre_empresa)
        )
        counter = select(func.count()).select_from(Client).where(*conditions)
        return await self._page(statement, counter, limit=limit, offset=offset)

    async def list_offline_gateways(
        self,
        *,
        limit: int,
        offset: int,
        seen_since: datetime,
        only_client_id: uuid.UUID | None = None,
    ) -> tuple[list[Row[Any]], int]:
        """Los gateways que dejaron de reportar, con dónde están.

        Devuelve los nombres de la sede y de la empresa, y no solo los ids,
        porque la pregunta que contesta es "a quién llamo". Una lista de
        números de serie obliga a resolver cada `site_id` a mano, que es
        exactamente el trabajo que esta vista existe para evitar.

        Los más viejos primero: un gateway callado hace una semana es un
        problema distinto de uno que se cayó recién.
        """
        conditions: list[ColumnElement[bool]] = [_sin_conexion(seen_since)]
        if only_client_id is not None:
            conditions.append(Site.client_id == only_client_id)

        desde = (
            select(Gateway)
            .join(Site, Gateway.site_id == Site.id)
            .join(Client, Site.client_id == Client.id)
            .where(*conditions)
        )
        total = await self._session.scalar(
            select(func.count()).select_from(desde.subquery())
        )

        stmt = (
            select(
                Gateway.id,
                Gateway.numero_serie,
                Gateway.uuid,
                Gateway.ultima_conexion,
                Site.id.label("site_id"),
                Site.nombre.label("sitio"),
                Client.id.label("client_id"),
                Client.nombre_empresa.label("empresa"),
            )
            .join(Site, Gateway.site_id == Site.id)
            .join(Client, Site.client_id == Client.id)
            .where(*conditions)
            # Nulls primero: el que nunca se conectó es el caso más viejo que
            # existe, y con un ORDER BY corriente quedaría al final o al
            # principio según el motor.
            .order_by(Gateway.ultima_conexion.asc().nulls_first())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.all()), total or 0

    async def summarise_clients(
        self,
        *,
        limit: int,
        offset: int,
        only_client_id: uuid.UUID | None = None,
        search: str | None = None,
        offline_before: datetime,
    ) -> tuple[list[Row[Any]], int]:
        """Cuánto tiene instalado cada cliente, contado en la base.

        Cada conteo va como subconsulta escalar y no como `JOIN` + `COUNT`
        agrupado. Con joins encadenados las filas se multiplican entre sí —un
        cliente con 2 sedes y 3 gateways devuelve 6 filas— y los conteos salen
        todos mal, cada uno inflado por el tamaño de los otros niveles. Es un
        error que no rompe nada visiblemente: los números quedan plausibles.
        """
        # Cada conteo es una subconsulta escalar correlacionada con `Client`
        # de la consulta externa. Es importante que ninguna se envuelva en
        # `.subquery()`: una tabla derivada en el FROM no puede referirse a
        # las columnas de la consulta que la contiene, así que la correlación
        # se pierde y el conteo pasa a ser el de TODOS los clientes. No falla
        # ni avisa —devuelve números plausibles— y además filtra información
        # de una empresa a otra.
        def _cuantos(desde: Select[Any]) -> Any:
            return desde.correlate(Client).scalar_subquery()

        sedes = _cuantos(
            select(func.count()).select_from(Site).where(Site.client_id == Client.id)
        )
        gateways = _cuantos(
            select(func.count())
            .select_from(Gateway)
            .join(Site, Gateway.site_id == Site.id)
            .where(Site.client_id == Client.id)
        )
        en_linea = _cuantos(
            select(func.count())
            .select_from(Gateway)
            .join(Site, Gateway.site_id == Site.id)
            .where(
                Site.client_id == Client.id,
                Gateway.ultima_conexion >= offline_before,
            )
        )
        equipos = _cuantos(
            select(func.count())
            .select_from(Equipment)
            .join(Gateway, Equipment.gateway_id == Gateway.id)
            .join(Site, Gateway.site_id == Site.id)
            .where(Site.client_id == Client.id)
        )
        variables = _cuantos(
            select(func.count())
            .select_from(Variable)
            .join(Equipment, Variable.equipment_id == Equipment.id)
            .join(Gateway, Equipment.gateway_id == Gateway.id)
            .join(Site, Gateway.site_id == Site.id)
            .where(Site.client_id == Client.id)
        )
        ultima = _cuantos(
            select(func.max(Gateway.ultima_conexion))
            .select_from(Gateway)
            .join(Site, Gateway.site_id == Site.id)
            .where(Site.client_id == Client.id)
        )

        conditions: list[ColumnElement[bool]] = []
        if only_client_id is not None:
            conditions.append(Client.id == only_client_id)
        if search:
            conditions.append(Client.nombre_empresa.ilike(self._like(search)))

        base = select(Client.id).where(*conditions)
        total = await self._session.scalar(
            select(func.count()).select_from(base.subquery())
        )

        stmt = (
            select(
                Client.id,
                Client.nombre_empresa,
                Client.estado,
                Client.puede_ver_consumo,
                sedes.label("sedes"),
                gateways.label("gateways"),
                en_linea.label("gateways_en_linea"),
                equipos.label("equipos"),
                variables.label("variables"),
                ultima.label("ultima_conexion"),
            )
            .where(*conditions)
            .order_by(Client.nombre_empresa)
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.all()), total or 0


def _sin_conexion(seen_since: datetime) -> ColumnElement[bool]:
    """Un gateway que no reportó desde el corte, o que nunca reportó.

    Una sola definición para los dos usos: el filtro `estado=offline` del
    listado y la vista de caídos. Con dos copias, un cambio en una dejaba a la
    otra contando distinto sobre los mismos datos.
    """
    return or_(
        Gateway.ultima_conexion.is_(None),
        Gateway.ultima_conexion < seen_since,
    )
