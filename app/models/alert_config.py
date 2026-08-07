"""AlertConfig: the rules; firing them lives in ApiEMS/EMS, not here."""

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.domain.enums import AlertType, NotificationChannel
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.types import enum_column

if TYPE_CHECKING:
    from app.models.gateway import Gateway


class AlertConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One alert rule, either global or bound to a single gateway."""

    __tablename__ = "alerts_config"

    # NULL means the rule applies to every gateway.
    gateway_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("gateways.id", ondelete="CASCADE"), index=True
    )
    tipo: Mapped[AlertType] = mapped_column(
        enum_column(AlertType, "alert_type"), nullable=False, index=True
    )
    # NULL for rules that need no threshold, such as `desconexion`.
    umbral: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    canal_notif: Mapped[NotificationChannel] = mapped_column(
        enum_column(NotificationChannel, "notification_channel"),
        nullable=False,
        default=NotificationChannel.EMAIL,
    )
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    gateway: Mapped["Gateway | None"] = relationship(
        back_populates="alerts_config", lazy="raise"
    )

    def __repr__(self) -> str:
        scope = self.gateway_id or "global"
        return f"<AlertConfig {self.tipo} {scope}>"
