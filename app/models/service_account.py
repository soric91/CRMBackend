"""ServiceAccount: another system's credential, not a person's login."""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.client import Client


class ServiceAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A machine-to-machine consumer of this API.

    Exists so `ApiEMS` stops logging in as a borrowed person. A person's
    password opens the panel, carries a role that can write, and belongs to
    somebody who will eventually leave; none of that is true of a credential
    that lives in another service's environment file.

    The secret is split in two on purpose. `credencial_id` is public, indexed
    and travels in the request; `secret_hash` never leaves the database. Both
    halves in one opaque string would force a bcrypt comparison against every
    row on every token request.
    """

    __tablename__ = "service_accounts"

    nombre: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    descripcion: Mapped[str | None] = mapped_column(String(300))

    # Public half of the credential. Indexed because it is the lookup key on
    # the token endpoint, which is the one route this table is read by often.
    credencial_id: Mapped[str] = mapped_column(
        String(60), nullable=False, unique=True, index=True
    )
    # Only the hash, as everywhere else: a database dump must not be enough to
    # impersonate the consumer. Shown once, when issued.
    secret_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    secret_emitido_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # What it may read. Stored as JSON rather than a second table: this is a
    # short closed list read whole on every token request, never joined or
    # filtered on.
    permisos: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    # Null means the whole platform. Set, it pins the credential to one client
    # exactly as a `cliente` login is pinned — a consumer serving one company
    # should not be able to enumerate the others.
    client_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )

    # Turning this off stops new tokens at once. Tokens already minted stay
    # valid until they expire, which is why they are short-lived.
    activo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    # Optional deadline. A credential with no expiry is one nobody ever
    # revisits; setting it forces a rotation to be a decision rather than an
    # omission.
    expira_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Refreshed when a token is issued, not on every request: writing a row on
    # every read would turn a cached consumer into constant database traffic.
    ultimo_uso_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    client: Mapped["Client"] = relationship(lazy="raise")

    def __repr__(self) -> str:
        estado = "activo" if self.activo else "revocado"
        return f"<ServiceAccount {self.nombre!r} ({estado})>"
