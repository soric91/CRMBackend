"""Acceso a los tokens de enrolamiento."""

import uuid
from datetime import datetime

from sqlalchemy import select, update

from app.models import EnrollmentToken
from app.repositories.base import BaseRepository


class EnrollmentTokenRepository(BaseRepository[EnrollmentToken]):
    model = EnrollmentToken

    async def get_by_hash(self, token_hash: str) -> EnrollmentToken | None:
        """La fila de un token, o `None`.

        Busca por hash y no por token: lo que se guarda es el sha256, así que
        un volcado de la base no alcanza para enrolar un equipo ajeno.
        """
        result = await self._session.execute(
            select(EnrollmentToken).where(EnrollmentToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def expire_pending(self, gateway_id: uuid.UUID, *, ahora: datetime) -> None:
        """Vencer los tokens sin usar de un gateway.

        Se llama al emitir uno nuevo. Sin esto, cada intento fallido deja un
        token vivo dando vueltas —en un chat, en un papel— y todos siguen
        sirviendo hasta que expiran solos.
        """
        await self._session.execute(
            update(EnrollmentToken)
            .where(
                EnrollmentToken.gateway_id == gateway_id,
                EnrollmentToken.usado_en.is_(None),
                EnrollmentToken.expira_en > ahora,
            )
            .values(expira_en=ahora)
        )
