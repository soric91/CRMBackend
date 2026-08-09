"""Site: a physical location belonging to a client."""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.client import Client
    from app.models.gateway import Gateway


class Site(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A client's physical location, where gateways are installed."""

    __tablename__ = "sites"
    __table_args__ = (
        UniqueConstraint("client_id", "nombre", name="uq_sites_client_id_nombre"),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    direccion: Mapped[str | None] = mapped_column(String(300))
    # IANA name, e.g. "America/Bogota". The firmware stamps its readings with it.
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="America/Bogota"
    )
    # Dirección y ciudad en vez de coordenadas: nadie tiene a mano la latitud
    # de una planta al darla de alta, así que el campo quedaba vacío y no servía
    # ni para ubicarla ni para dibujarla en un mapa. Con la dirección escrita al
    # menos se puede llegar.
    ciudad: Mapped[str | None] = mapped_column(String(120))
    responsable_nombre: Mapped[str | None] = mapped_column(String(150))

    client: Mapped["Client"] = relationship(back_populates="sites", lazy="raise")
    gateways: Mapped[list["Gateway"]] = relationship(
        back_populates="site",
        cascade="all, delete-orphan",
        lazy="raise",
        order_by="Gateway.numero_serie",
    )

    def __repr__(self) -> str:
        return f"<Site {self.nombre!r}>"
