"""Acceso a la configuración compartida de los gateways."""

from sqlalchemy import select

from app.models import PlatformSetting
from app.repositories.base import BaseRepository


class PlatformSettingRepository(BaseRepository[PlatformSetting]):
    model = PlatformSetting

    async def get_by_clave(self, clave: str) -> PlatformSetting | None:
        result = await self._session.execute(
            select(PlatformSetting).where(PlatformSetting.clave == clave)
        )
        return result.scalar_one_or_none()

    async def list_ordered(self) -> list[PlatformSetting]:
        """Todas, por nombre.

        Sin paginar a propósito: son las variables de un archivo `.env`, del
        orden de treinta. Paginarlas obligaría al panel a pedir páginas para
        mostrar una lista que entra en una pantalla.
        """
        result = await self._session.execute(
            select(PlatformSetting).order_by(PlatformSetting.clave)
        )
        return list(result.scalars().all())
