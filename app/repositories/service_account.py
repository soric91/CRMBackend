"""Data access for machine-to-machine credentials."""

import uuid

from sqlalchemy import select

from app.models import ServiceAccount
from app.repositories.base import BaseRepository


class ServiceAccountRepository(BaseRepository[ServiceAccount]):
    model = ServiceAccount

    async def list_page(
        self, *, limit: int, offset: int
    ) -> tuple[list[ServiceAccount], int]:
        return await self.list_where(
            limit=limit, offset=offset, order_by=ServiceAccount.nombre
        )

    async def by_credential_id(self, credencial_id: str) -> ServiceAccount | None:
        """Look the account up by the public half of its credential.

        Indexed, and the reason the credential has a public half at all: the
        alternative is a bcrypt comparison against every row on every token
        request, which gets slower precisely as the platform grows.
        """
        result = await self._session.execute(
            select(ServiceAccount).where(ServiceAccount.credencial_id == credencial_id)
        )
        return result.scalar_one_or_none()

    async def name_taken(
        self, nombre: str, *, excluding: uuid.UUID | None = None
    ) -> bool:
        conditions = [ServiceAccount.nombre == nombre]
        if excluding is not None:
            conditions.append(ServiceAccount.id != excluding)
        return await self.exists_where(*conditions)
