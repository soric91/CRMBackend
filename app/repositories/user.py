"""Data access for users."""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import UserRole
from app.models import User


def normalize_email(email: str) -> str:
    """Return the canonical stored form of an address.

    Addresses are compared case-insensitively, so they are stored lowercase and
    every lookup goes through here. Otherwise ``Ana@x.com`` and ``ana@x.com``
    would be two accounts.
    """
    return email.strip().lower()


class UserRepository:
    """Reads and writes rows of ``users``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(
            select(User).where(User.email == normalize_email(email))
        )
        return result.scalar_one_or_none()

    async def add(self, user: User) -> User:
        user.email = normalize_email(user.email)
        self._session.add(user)
        await self._session.flush()
        return user

    async def exists_with_email(self, email: str) -> bool:
        return await self.get_by_email(email) is not None

    async def list_page(
        self,
        *,
        limit: int,
        offset: int,
        client_id: uuid.UUID | None = None,
        role: UserRole | None = None,
    ) -> tuple[list[User], int]:
        statement = select(User)
        counter = select(func.count()).select_from(User)
        for condition in (
            User.client_id == client_id if client_id is not None else None,
            User.role == role if role is not None else None,
        ):
            if condition is not None:
                statement = statement.where(condition)
                counter = counter.where(condition)

        rows = (
            (
                await self._session.execute(
                    statement.order_by(User.email).limit(limit).offset(offset)
                )
            )
            .scalars()
            .all()
        )
        total = (await self._session.execute(counter)).scalar_one()
        return list(rows), total

    async def count_active_admins(self, *, excluding: uuid.UUID | None = None) -> int:
        """How many usable administrators remain.

        Used to refuse the change that would leave the platform with none.
        """
        statement = (
            select(func.count())
            .select_from(User)
            .where(User.role == UserRole.ADMIN, User.is_active.is_(True))
        )
        if excluding is not None:
            statement = statement.where(User.id != excluding)
        return (await self._session.execute(statement)).scalar_one()

    async def update(self, user: User, changes: dict[str, Any]) -> User:
        for field, value in changes.items():
            setattr(user, field, value)
        await self._session.flush()
        # `updated_at` is database-computed, so the flush expires it; refreshing
        # here keeps serialisation from emitting SQL outside the greenlet.
        await self._session.refresh(user)
        return user

    async def delete(self, user: User) -> None:
        await self._session.delete(user)
        await self._session.flush()
