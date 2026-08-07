"""Client: the top of the hierarchy and the tenant boundary."""

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, String, false, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.domain.enums import ClientStatus
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.types import enum_column

if TYPE_CHECKING:
    from app.models.site import Site
    from app.models.user import User


class Client(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A company that contracted the platform."""

    __tablename__ = "clients"

    nombre_empresa: Mapped[str] = mapped_column(String(200), nullable=False)
    contacto_nombre: Mapped[str | None] = mapped_column(String(150))
    contacto_email: Mapped[str | None] = mapped_column(String(255))
    contacto_telefono: Mapped[str | None] = mapped_column(String(40))
    plan_contratado: Mapped[str | None] = mapped_column(String(80))
    estado: Mapped[ClientStatus] = mapped_column(
        enum_column(ClientStatus, "client_status"),
        nullable=False,
        default=ClientStatus.PROSPECTO,
        index=True,
    )
    fecha_alta: Mapped[date] = mapped_column(
        Date, nullable=False, server_default=func.current_date()
    )
    # Whether this client's own users may open the energy consumption page.
    # Off until someone turns it on: a client that has not finished being set
    # up must not see half-populated readings.
    puede_ver_consumo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    # lazy="raise" everywhere: under asyncio an implicit lazy load raises a
    # confusing MissingGreenlet error, so relationships must be loaded on
    # purpose (selectinload / joinedload) by the repository.
    # order_by is not cosmetic: the aggregate fleet document is fingerprinted
    # into an ETag, and an unordered collection would change that fingerprint
    # every time Postgres felt like returning the rows differently.
    sites: Mapped[list["Site"]] = relationship(
        back_populates="client",
        cascade="all, delete-orphan",
        lazy="raise",
        order_by="Site.nombre",
    )
    users: Mapped[list["User"]] = relationship(back_populates="client", lazy="raise")

    def __repr__(self) -> str:
        return f"<Client {self.nombre_empresa!r} ({self.estado})>"
