"""Shared data-access behaviour."""

import uuid
from typing import Any

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base


class BaseRepository[ModelT: Base]:
    """CRUD common to every table keyed by a UUID ``id``.

    Subclasses set :attr:`model` and add the queries specific to their entity.
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, entity_id: uuid.UUID) -> ModelT | None:
        return await self._session.get(self.model, entity_id)

    async def add(self, entity: ModelT) -> ModelT:
        self._session.add(entity)
        # Flush, not commit: the request-scoped session commits once, at the
        # end, so a later failure rolls the whole request back together.
        await self._session.flush()
        # Read the row back so the response carries what the database stored,
        # not what Python sent. Numeric columns are the visible case: a price
        # given as Decimal("0") would serialise as "0" on create and "0.0000"
        # on every later read — the same value in two shapes, for a client that
        # handles decimals as strings.
        await self._session.refresh(entity)
        return entity

    async def delete(self, entity: ModelT) -> None:
        await self._session.delete(entity)
        await self._session.flush()

    async def update(self, entity: ModelT, changes: dict[str, Any]) -> ModelT:
        """Apply ``changes`` to ``entity``.

        Callers pass the already-filtered set of fields, so a field left out of
        a PATCH body keeps its stored value rather than being set to null.
        """
        for field, value in changes.items():
            setattr(entity, field, value)
        await self._session.flush()
        # `updated_at` is computed by the database, so the flush leaves it
        # expired. Refreshing here does that IO explicitly; otherwise the first
        # read of the attribute — typically Pydantic serialising the response —
        # would try to emit SQL from sync code and raise MissingGreenlet.
        await self._session.refresh(entity)
        return entity

    async def list_where(
        self,
        *conditions: ColumnElement[bool],
        limit: int,
        offset: int,
        order_by: Any | None = None,
    ) -> tuple[list[ModelT], int]:
        """Return one page of rows and the total number matching."""
        statement: Select[tuple[ModelT]] = select(self.model)
        counter = select(func.count()).select_from(self.model)
        for condition in conditions:
            statement = statement.where(condition)
            counter = counter.where(condition)

        if order_by is not None:
            statement = statement.order_by(order_by)

        rows = (
            (await self._session.execute(statement.limit(limit).offset(offset)))
            .scalars()
            .all()
        )
        total = (await self._session.execute(counter)).scalar_one()
        return list(rows), total

    async def exists_where(self, *conditions: ColumnElement[bool]) -> bool:
        statement = select(func.count()).select_from(self.model)
        for condition in conditions:
            statement = statement.where(condition)
        return (await self._session.execute(statement)).scalar_one() > 0
