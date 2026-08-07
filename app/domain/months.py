"""Spanish month names.

Tariffs are stored as a date so the year is never lost and periods sort and
filter natively, but they are shown as a month name.
"""

from datetime import date

MONTH_NAMES_ES: tuple[str, ...] = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def month_name(value: date) -> str:
    """Return the Spanish month name, e.g. ``enero``."""
    return MONTH_NAMES_ES[value.month - 1]


def month_label(value: date) -> str:
    """Return month and year, e.g. ``enero 2026``."""
    return f"{month_name(value)} {value.year}"


def parse_month_name(name: str) -> int:
    """Return the month number for a Spanish month name.

    Case and surrounding blanks are ignored.
    """
    try:
        return MONTH_NAMES_ES.index(name.strip().lower()) + 1
    except ValueError:
        raise ValueError(f"'{name}' is not a Spanish month name") from None


def first_day(year: int, month_name_or_number: str | int) -> date:
    """Return the first day of a month, the canonical form stored in tariffs."""
    month = (
        parse_month_name(month_name_or_number)
        if isinstance(month_name_or_number, str)
        else month_name_or_number
    )
    return date(year, month, 1)
