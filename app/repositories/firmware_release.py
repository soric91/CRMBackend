"""Acceso al catálogo de versiones del firmware."""

import uuid
from datetime import datetime

from sqlalchemy import func, select

from app.domain.firmware_update import VersionInvalidaError, parse_version
from app.models import FirmwareRelease, Gateway
from app.repositories.base import BaseRepository


def _orden(release: FirmwareRelease) -> tuple[datetime, tuple[int, int, int]]:
    """Cuándo se publicó y, a igualdad, qué número de versión es.

    Una versión ilegible se va al fondo en vez de romper el listado: el
    esquema de entrada no deja publicar ninguna así, pero una fila puesta a
    mano no puede dejar al panel sin catálogo.
    """
    try:
        numeros = parse_version(release.version)
    except VersionInvalidaError:
        numeros = (0, 0, 0)
    return release.created_at, numeros


class FirmwareReleaseRepository(BaseRepository[FirmwareRelease]):
    model = FirmwareRelease

    async def get_by_version(self, version: str) -> FirmwareRelease | None:
        result = await self._session.execute(
            select(FirmwareRelease).where(FirmwareRelease.version == version)
        )
        return result.scalar_one_or_none()

    async def list_ordered(self) -> list[FirmwareRelease]:
        """Todas, la más nueva primero.

        Sin paginar: son las versiones publicadas de un firmware, no un
        historial de eventos. Paginarlas obligaría al panel a pedir páginas
        para llenar un desplegable.

        El desempate por número de versión no es cosmético: `created_at` lo
        pone la base con `now()`, que en PostgreSQL es la hora de la
        transacción, así que dos versiones publicadas juntas quedan con el
        mismo instante y el orden del desplegable pasaría a ser el que
        devuelva el motor.
        """
        result = await self._session.execute(
            select(FirmwareRelease).order_by(FirmwareRelease.created_at.desc())
        )
        releases = list(result.scalars().all())
        releases.sort(key=_orden, reverse=True)
        return releases

    async def gateways_apuntando(self, release_id: uuid.UUID) -> int:
        """Cuántos equipos tienen pedida esta versión.

        Lo mira el panel antes de retirarla: retirar una a la que van tres
        equipos los deja sin nada que instalar, y eso hay que verlo antes y
        no después.
        """
        result = await self._session.execute(
            select(func.count())
            .select_from(Gateway)
            .where(Gateway.firmware_objetivo_id == release_id)
        )
        return result.scalar_one()
