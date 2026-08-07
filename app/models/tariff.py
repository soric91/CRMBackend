"""Tariff: the monthly energy prices ApiEMS reads to turn kWh into money."""

from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, Numeric, column, extract
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.domain.months import month_label
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Tariff(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The energy prices in force for one month, platform-wide.

    One row per month, never overwritten in place by a different period: a cost
    computed last year has to stay reproducible.
    """

    __tablename__ = "tariffs"
    __table_args__ = (
        # Stored as the first day of the month so periods sort and filter as
        # dates. The API renders it as a month name.
        # Built with `extract` rather than raw SQL: `EXTRACT(DAY FROM ...)` is
        # PostgreSQL-only syntax, and the tests run this DDL on SQLite.
        CheckConstraint(extract("day", column("mes")) == 1, name="mes_is_first_day"),
        CheckConstraint("valor_importado >= 0", name="valor_importado_non_negative"),
        CheckConstraint("valor_excedente >= 0", name="valor_excedente_non_negative"),
    )

    mes: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    # Numeric, never float: these multiply consumption to produce money.
    valor_importado: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    # Price paid for the surplus left after netting the imported energy.
    valor_excedente: Mapped[Decimal] = mapped_column(
        Numeric(14, 4), nullable=False, default=Decimal(0)
    )

    @property
    def periodo(self) -> str:
        """The month as shown to users, e.g. ``enero 2026``."""
        return month_label(self.mes)

    def __repr__(self) -> str:
        return f"<Tariff {self.periodo} importado={self.valor_importado}>"
