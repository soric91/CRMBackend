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
from app.domain.enums import FirmwareUpdateState, GatewayLogLevel, GatewayStatus
from app.domain.gateway_status import derive_status
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.types import enum_column

if TYPE_CHECKING:
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
        # An update in flight without a target is a device downloading nothing.
        CheckConstraint(
            "firmware_estado = 'sin_pendiente' OR firmware_objetivo_id IS NOT NULL",
            name="firmware_target_present",
        ),
        CheckConstraint("firmware_intentos >= 0", name="firmware_intentos_no_negative"),
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

    # --- the firmware update this device has been asked to install ---
    # The release it should end up running, or NULL when nothing is pending.
    # RESTRICT rather than SET NULL: a release cannot be deleted while a
    # device is on its way to it, and the row is what says which package that
    # device is downloading right now.
    firmware_objetivo_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("firmware_releases.id", ondelete="RESTRICT"), index=True
    )
    # How far along it is. Reported by the device, never inferred here: a
    # restart happens in the middle, and the CRM cannot watch that stretch.
    firmware_estado: Mapped[FirmwareUpdateState] = mapped_column(
        enum_column(FirmwareUpdateState, "firmware_update_state"),
        nullable=False,
        default=FirmwareUpdateState.SIN_PENDIENTE,
        server_default=text(f"'{FirmwareUpdateState.SIN_PENDIENTE.value}'"),
    )
    # The instant the device is allowed to start. Computed from the fleet-wide
    # hour in the site's own timezone, so it is comparable without knowing
    # where the site is.
    firmware_aplicar_desde: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # What it was running before. Without it, a rollback has to be typed from
    # memory on the night the new version turned out to be wrong.
    firmware_version_anterior: Mapped[str | None] = mapped_column(String(40))
    # Why it failed, in the device's own words. Truncated rather than a text
    # column: this is a line for the panel, not a log.
    firmware_error: Mapped[str | None] = mapped_column(String(300))
    # Attempts spent on the current target. Capped so a device that cannot
    # apply an update does not reboot itself every night forever.
    firmware_intentos: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # When the device last said anything about the update. Distinguishes "no
    # news yet" from "it went quiet halfway through".
    firmware_reportado_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    site: Mapped["Site"] = relationship(back_populates="gateways", lazy="raise")
    equipment: Mapped[list["Equipment"]] = relationship(
        back_populates="gateway",
        cascade="all, delete-orphan",
        lazy="raise",
        order_by="Equipment.nombre_dispositivo",
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
