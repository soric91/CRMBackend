"""The aggregate view: a client and everything installed under it.

One request instead of the four the per-parent listings need — clients, then
sites, then gateways, then devices, then registers. Meant for whoever has to
know the shape of an installation before it can do anything with it: a panel
drawing a meter selector, or another service resolving a gateway uuid back to
the registers behind a reading.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response, status

from app.api.deps import FleetServiceDep, MachineOrUserScopeDep, PaginationDep
from app.domain.fleet import FleetLevel
from app.schemas.common import Page
from app.schemas.fleet import ClientFleet
from app.schemas.fleet_summary import ClientSummary
from app.services.fleet import compute_fleet_version

router = APIRouter(prefix="/fleet", tags=["fleet"])


@router.get("", response_model=Page[ClientFleet])
async def get_fleet(
    scope: MachineOrUserScopeDep,
    service: FleetServiceDep,
    pagination: PaginationDep,
    response: Response,
    client_id: Annotated[uuid.UUID | None, Query()] = None,
    nivel: Annotated[FleetLevel, Query()] = FleetLevel.VARIABLES,
    search: Annotated[str | None, Query(max_length=100)] = None,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Page[ClientFleet] | Response:
    """Return whole installations, nested, for every client the caller sees.

    A `cliente` login is already pinned to its own company, so for it this is
    simply "everything of mine" — there is no separate route for that. Passing
    `client_id` narrows the result; it can never widen it.

    `nivel` decides how deep the tree goes, and a collection below it comes
    back as `null` rather than empty, so "not asked for" stays distinct from
    "there are none".

    Answers **304** when the caller already holds this version, which is what
    makes polling it cheap.
    """
    items, total = await service.client_trees(
        scope,
        limit=pagination.limit,
        offset=pagination.offset,
        level=nivel,
        client_id=client_id,
        search=search,
    )
    page = Page[ClientFleet](
        items=items, total=total, limit=pagination.limit, offset=pagination.offset
    )

    etag = f'"{compute_fleet_version(page)}"'
    if if_none_match is not None and if_none_match.strip() in (etag, etag.strip('"')):
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag}
        )

    response.headers["ETag"] = etag
    return page


@router.get("/summary", response_model=Page[ClientSummary])
async def get_fleet_summary(
    scope: MachineOrUserScopeDep,
    service: FleetServiceDep,
    pagination: PaginationDep,
    search: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[ClientSummary]:
    """Cuánto tiene instalado cada cliente, sin el inventario.

    Para pintar una lista de clientes con "3 gateways, 2 en línea" sin traer
    el árbol de cada uno. Los conteos se hacen en la base; la alternativa era
    pedir `/fleet?nivel=variables` y contar del lado del que pregunta, lo que
    transfiere cada registro Modbus de cada equipo para mostrar un número.

    Mismo recorte por cliente que el árbol: un `cliente` se ve solo a sí mismo.
    """
    items, total = await service.summarise_clients(
        scope,
        limit=pagination.limit,
        offset=pagination.offset,
        search=search,
    )
    return Page[ClientSummary](
        items=items, total=total, limit=pagination.limit, offset=pagination.offset
    )
