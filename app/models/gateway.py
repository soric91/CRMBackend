"""Gateway: the iMX8MP device installed at a site."""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Uuid,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.domain.enums import GatewayLogLevel, GatewayStatus
from app.domain.gateway_status import derive_status
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.types import enum_column

if TYPE_CHECKING:
    from app.models.alert_config import AlertConfig
    from app.models.equipment import Equipment
    from app.models.site import Site


class Gateway(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A field device that reads Modbus equipment and publishes over MQTT."""

    __tablename__ = "gateways"
    __table_args__ = (
        CheckConstraint(
            "intervalo_lectura_segundos > 0", name="intervalo_lectura_positive"
        ),
        CheckConstraint("hora_inicio BETWEEN 0 AND 23", name="hora_inicio_range"),
        CheckConstraint("hora_fin BETWEEN 0 AND 23", name="hora_fin_range"),
        CheckConstraint("hora_fin >= hora_inicio", name="horas_ordered"),
    )

    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), nullable=False, index=True
    )
    numero_serie: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    # Identity the firmware sends when asking for its configuration. Separate
    # from the primary key so it can be reissued without rewriting foreign keys.
    uuid: Mapped[UUID] = mapped_column(Uuid, nullable=False, unique=True, default=uuid4)
    firmware_version: Mapped[str | None] = mapped_column(String(40))
    # Indexed: reachability is derived from it, so the fleet view filters here.
    ultima_conexion: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    # Plain string rather than INET: the column is informational, and INET has
    # no equivalent in the SQLite engine the tests run against.
    ip_actual: Mapped[str | None] = mapped_column(String(45))
    # --- credential the firmware authenticates with ---
    # Only the hash: a database dump must not be enough to impersonate a
    # gateway. The secret is shown once, when issued, and never again.
    credential_hash: Mapped[str | None] = mapped_column(String(255))
    credential_emitida_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # Off until somebody turns it on: a gateway half way through being set up
    # must not pull a configuration that is still incomplete.
    config_habilitada: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    # --- what the gateway reported back about its configuration ---
    # The version it acknowledged applying. Compared against the version the
    # CRM would serve now, it says whether pending edits have reached the
    # device.
    config_version_aplicada: Mapped[str | None] = mapped_column(String(64))
    config_aplicada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- what the firmware's config file needs, per gateway ---
    log_level: Mapped[GatewayLogLevel] = mapped_column(
        enum_column(GatewayLogLevel, "gateway_log_level"),
        nullable=False,
        default=GatewayLogLevel.INFO,
        server_default=text("'INFO'"),
    )
    # The polling cadence of the whole bus. One value per gateway, because the
    # firmware walks its devices in a single loop.
    intervalo_lectura_segundos: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60, server_default=text("60")
    )
    hora_inicio: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    hora_fin: Mapped[int] = mapped_column(
        Integer, nullable=False, default=23, server_default=text("23")
    )

    site: Mapped["Site"] = relationship(back_populates="gateways", lazy="raise")
    equipment: Mapped[list["Equipment"]] = relationship(
        back_populates="gateway",
        cascade="all, delete-orphan",
        lazy="raise",
        order_by="Equipment.nombre_dispositivo",
    )
    alerts_config: Mapped[list["AlertConfig"]] = relationship(
        back_populates="gateway", cascade="all, delete-orphan", lazy="raise"
    )

    @property
    def estado(self) -> GatewayStatus:
        """Reachability, observed rather than recorded.

        Derived from `ultima_conexion` so it cannot go stale: a stored flag
        that nothing updates is worse than no flag at all.
        """
        return derive_status(self.ultima_conexion)

    def __repr__(self) -> str:
        return f"<Gateway {self.numero_serie!r} ({self.estado})>"
