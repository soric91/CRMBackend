"""Monthly tariff use cases.

Tariffs belong to the platform, not to a client, so the access rules differ
from the rest of the hierarchy: a `cliente` is refused with 403 rather than the
404 used to hide another company's rows, and only an `admin` writes.
"""

import uuid
from typing import Any

from app.core.exceptions import (
    AlreadyExistsError,
    AuthorizationError,
    NotFoundError,
)
from app.domain.access import AccessScope
from app.domain.months import month_label
from app.models import Tariff
from app.repositories.tariff import TariffRepository


class TariffService:
    """Reads and maintains the price of energy per month."""

    def __init__(self, tariffs: TariffRepository) -> None:
        self._tariffs = tariffs

    @staticmethod
    def _require_read(scope: AccessScope) -> None:
        if not scope.can_read_tariffs:
            raise AuthorizationError(
                f"Role '{scope.principal}' cannot read platform tariffs"
            )

    @staticmethod
    def _require_manage(scope: AccessScope) -> None:
        if not scope.can_manage_tariffs:
            raise AuthorizationError(
                f"Role '{scope.principal}' cannot change platform tariffs"
            )

    async def list(
        self, scope: AccessScope, *, limit: int, offset: int
    ) -> tuple[list[Tariff], int]:
        self._require_read(scope)
        return await self._tariffs.list_page(limit=limit, offset=offset)

    async def get(self, scope: AccessScope, tariff_id: uuid.UUID) -> Tariff:
        self._require_read(scope)
        tariff = await self._tariffs.get(tariff_id)
        if tariff is None:
            raise NotFoundError(f"Tariff {tariff_id} not found")
        return tariff

    async def create(self, scope: AccessScope, data: dict[str, Any]) -> Tariff:
        self._require_manage(scope)
        if await self._tariffs.month_taken(data["mes"]):
            raise AlreadyExistsError(
                f"A tariff for {month_label(data['mes'])} already exists"
            )
        return await self._tariffs.add(Tariff(**data))

    async def update(
        self, scope: AccessScope, tariff_id: uuid.UUID, changes: dict[str, Any]
    ) -> Tariff:
        """Change the prices of a period.

        The month itself is not updatable; the request schema does not carry
        it, so a body that includes one leaves the row where it is.
        """
        self._require_manage(scope)
        tariff = await self.get(scope, tariff_id)
        return await self._tariffs.update(tariff, changes)

    async def delete(self, scope: AccessScope, tariff_id: uuid.UUID) -> None:
        """Remove a period. The way to fix a tariff filed under the wrong month."""
        self._require_manage(scope)
        await self._tariffs.delete(await self.get(scope, tariff_id))
