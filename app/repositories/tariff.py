"""Data access for monthly tariffs."""

import uuid
from datetime import date

from app.models import Tariff
from app.repositories.base import BaseRepository


class TariffRepository(BaseRepository[Tariff]):
    model = Tariff

    async def list_page(self, *, limit: int, offset: int) -> tuple[list[Tariff], int]:
        """Most recent month first: that is the period usually being consulted."""
        return await self.list_where(
            limit=limit, offset=offset, order_by=Tariff.mes.desc()
        )

    async def month_taken(
        self, mes: date, *, excluding: uuid.UUID | None = None
    ) -> bool:
        conditions = [Tariff.mes == mes]
        if excluding is not None:
            conditions.append(Tariff.id != excluding)
        return await self.exists_where(*conditions)
