"""EnrollmentToken: el papelito con el que un equipo se configura solo."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class EnrollmentToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Un permiso de un solo uso para que un gateway reciba su configuración.

    Es un puntero, no un contenedor: el token que se entrega no lleva adentro
    a qué equipo pertenece. Un token filtrado no dice ni de qué instalación
    es, y la relación vive acá, donde se puede revocar.

    Existe porque el instalador que corre en la sede no sabe nada — ni el uuid
    del equipo ni dónde está el broker. Presenta esto y el CRM le contesta
    quién es y cómo configurarse.
    """

    __tablename__ = "enrollment_tokens"

    # sha256, no bcrypt: la petición llega con el token y hay que **encontrar**
    # esta fila. Ver `hash_lookup_token` para el razonamiento completo.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    # A qué equipo enrola. Se borra con él: un token que apunta a un gateway
    # que ya no existe no puede llevar a nada bueno.
    gateway_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("gateways.id", ondelete="CASCADE"), index=True
    )

    expira_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Cuándo se canjeó. `None` mientras siga sin usar — que es lo que lo hace
    # de un solo uso: no se borra al gastarse, se marca, así queda registro de
    # que ese equipo se enroló y cuándo.
    usado_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    # Desde dónde se canjeó. Si un token aparece usado desde una dirección que
    # no es la sede, eso es lo único que lo va a delatar.
    usado_desde: Mapped[str | None] = mapped_column(String(45), default=None)

    # Quién lo emitió. Sin clave foránea a propósito: es un registro de
    # auditoría, y tiene que sobrevivir a que esa cuenta se borre. Con
    # `CASCADE` la fila desaparecería junto con el usuario —borrando la
    # evidencia justo cuando más importa— y con `RESTRICT` no se podría dar de
    # baja a nadie que alguna vez haya emitido un token.
    emitido_por: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
