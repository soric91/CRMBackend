"""Qué dice Postgres y qué muestra el panel, lado a lado.

Correr con::

    uv run python -m app.scripts.comparar_datos
    uv run python -m app.scripts.comparar_datos --empresa "ems sas"

Existe porque "los datos de la base no concuerdan con los del CRM" no es una
sola pregunta sino tres, y las tres se ven igual desde el editor de tablas:

* **Campos derivados.** `gateways` no tiene columna `estado`: el panel lo
  calcula desde `ultima_conexion` contra un umbral. Buscarlo en la base no
  encuentra nada, y eso se lee como un dato perdido.
* **Zona horaria.** Todo se guarda en UTC y se muestra en la zona de la sede.
  Cinco horas de diferencia se leen como dos valores distintos.
* **Campos que se cargan a mano.** `clients.estado` no lo actualiza nadie: un
  cliente que está midiendo puede seguir figurando como `prospecto`. Ahí la
  base y la realidad no concuerdan de verdad.

La salida marca cuál es cuál, así que la discusión se termina mirando una
columna en vez de discutiendo.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import dispose_engine, get_session_factory
from app.domain.gateway_status import OFFLINE_AFTER
from app.models import Client, Site

GUARDADO = "guardado"
DERIVADO = "derivado"
A_MANO = "a mano"


def _fila(campo: str, valor: object, origen: str, nota: str = "") -> str:
    return f"    {campo:<22} {valor!s:<34} [{origen}] {nota}"


async def comparar(filtro: str | None) -> None:
    # El mismo umbral que usa el servicio de flota, importado y no copiado:
    # una segunda constante acá diría "online" mientras el panel dice "offline".
    umbral = OFFLINE_AFTER
    ahora = datetime.now(UTC)
    factory = get_session_factory()

    async with factory() as session:
        consulta = (
            select(Client)
            .options(selectinload(Client.sites).selectinload(Site.gateways))
            .order_by(Client.nombre_empresa)
        )
        if filtro:
            consulta = consulta.where(Client.nombre_empresa.ilike(f"%{filtro}%"))
        clientes = (await session.execute(consulta)).scalars().all()

    if not clientes:
        print("\n  Ninguna empresa coincide.\n")
        return

    print(f"\n  Ahora: {ahora:%Y-%m-%d %H:%M} UTC")
    print(
        f"  Un gateway se considera caído tras "
        f"{umbral.total_seconds():.0f} s sin reportar."
    )
    print(
        f"  [{GUARDADO}] está en una columna · [{DERIVADO}] se calcula · "
        f"[{A_MANO}] lo carga una persona\n"
    )

    for cliente in clientes:
        print(f"  {cliente.nombre_empresa}")
        print(_fila("estado", cliente.estado.value, A_MANO, "nadie lo actualiza solo"))
        print(_fila("puede_ver_consumo", cliente.puede_ver_consumo, GUARDADO))

        for sede in cliente.sites:
            zona = ZoneInfo(sede.timezone)
            print(f"\n    sede: {sede.nombre}  (zona {sede.timezone})")

            for gateway in sede.gateways:
                ultima = gateway.ultima_conexion
                en_linea = ultima is not None and (ahora - ultima) <= umbral
                print(f"\n      gateway: {gateway.numero_serie}")
                print(_fila("ultima_conexion", ultima, GUARDADO, "en UTC"))
                print(
                    _fila(
                        "  ...en la sede",
                        ultima.astimezone(zona) if ultima else None,
                        DERIVADO,
                        "es lo que muestra el panel",
                    )
                )
                print(
                    _fila(
                        "estado",
                        "online" if en_linea else "offline",
                        DERIVADO,
                        "no existe como columna",
                    )
                )
                print(_fila("config_habilitada", gateway.config_habilitada, GUARDADO))

        print()


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Postgres contra lo que muestra el panel."
    )
    parser.add_argument("--empresa", default=None, help="Filtrar por nombre.")
    args = parser.parse_args()

    try:
        await comparar(args.empresa)
    finally:
        await dispose_engine()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
