"""Variable: a single magnitude read from a piece of equipment."""

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.domain.enums import ModbusDataType, ModbusRegisterType, RegisterNotation
from app.domain.measurements import Fase, Magnitud, buscar
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.types import enum_column

if TYPE_CHECKING:
    from app.domain.measurements import Medicion
    from app.models.equipment import Equipment


class Variable(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One reading exposed by an equipment, e.g. ``voltaje_l1``."""

    __tablename__ = "variables"
    __table_args__ = (
        UniqueConstraint(
            "equipment_id", "nombre", name="uq_variables_equipment_id_nombre"
        ),
        CheckConstraint("registro_modbus >= 0", name="registro_modbus_non_negative"),
        CheckConstraint("escala <> 0", name="escala_not_zero"),
    )

    equipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False, index=True
    )
    nombre: Mapped[str] = mapped_column(String(80), nullable=False)
    # Always the numeric address, whatever base it was read in.
    registro_modbus: Mapped[int] = mapped_column(Integer, nullable=False)
    # Which base the operator read it in. Decides how it is written back to
    # the firmware, whose map files use `0x2006` for a hex address.
    notacion_registro: Mapped[RegisterNotation] = mapped_column(
        enum_column(RegisterNotation, "register_notation"),
        nullable=False,
        default=RegisterNotation.DECIMAL,
        server_default=text("'decimal'"),
    )
    # The address space this register lives in. Per variable, not per
    # equipment: one analyser commonly exposes its electrical measurements as
    # holding or input registers and its relay states as coils.
    tipo_registro: Mapped[ModbusRegisterType] = mapped_column(
        enum_column(ModbusRegisterType, "modbus_register_type"),
        nullable=False,
        default=ModbusRegisterType.HOLDING,
        server_default=text("'holding'"),
    )
    tipo_dato: Mapped[ModbusDataType] = mapped_column(
        enum_column(ModbusDataType, "modbus_data_type"),
        nullable=False,
        default=ModbusDataType.UINT16,
    )
    # Multiplier applied to the raw register value. Numeric, not float: a 0.1
    # scale on a float column silently drifts on repeated readings.
    escala: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=Decimal(1)
    )
    equipment: Mapped["Equipment"] = relationship(
        back_populates="variables", lazy="raise"
    )

    @property
    def medicion(self) -> "Medicion | None":
        """La entrada del catálogo que este nombre representa.

        `None` solo para filas anteriores al catálogo. Una variable creada
        hoy siempre resuelve: el nombre se valida contra la lista al escribir.
        """
        return buscar(self.nombre)

    @property
    def magnitud(self) -> Magnitud | None:
        """Qué se está midiendo. Derivado del nombre, nunca guardado.

        Guardarlo sería una segunda verdad que nada mantiene al día — el
        mismo error que ya corregimos con el `estado` del gateway.
        """
        medicion = self.medicion
        return medicion.magnitud if medicion else None

    @property
    def fase(self) -> Fase | None:
        medicion = self.medicion
        return medicion.fase if medicion else None

    @property
    def unidad(self) -> str | None:
        """La unidad física. Se deduce de qué se mide, no se escribe.

        Por eso no existe el caso de `kw` contra `kW`: nadie la teclea.
        """
        medicion = self.medicion
        return medicion.unidad if medicion else None

    @property
    def etiqueta(self) -> str | None:
        """Cómo se muestra: "Tensión fase C".

        El `nombre` es el identificador que viaja por MQTT y queda en
        InfluxDB; no está pensado para leerse. Que la etiqueta salga del
        catálogo y no de una tabla del panel evita que cada proyecto invente
        su propia traducción de `PhV_phsC`.
        """
        medicion = self.medicion
        return medicion.etiqueta if medicion else None

    @property
    def acumulativa(self) -> bool:
        """Si es un contador monótono. Decide si admite promedios."""
        medicion = self.medicion
        return medicion.acumulativa if medicion else False

    def __repr__(self) -> str:
        return f"<Variable {self.nombre!r} reg={self.registro_modbus}>"
