"""Shared column types."""

from enum import StrEnum

from sqlalchemy import Enum


def enum_column(enum_class: type[StrEnum], constraint_name: str) -> Enum:
    """Map a ``StrEnum`` to VARCHAR plus a CHECK constraint.

    A native PostgreSQL ``ENUM`` type would be stricter, but adding a value to
    one is a migration that Alembic cannot autogenerate, and the type outlives
    the table that used it. VARCHAR + CHECK enforces the same values while
    staying trivial to alter.

    Values are stored as written in the enum (``activo``), not as the member
    name (``ACTIVO``).
    """
    return Enum(
        enum_class,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        name=constraint_name,
        values_callable=lambda members: [member.value for member in members],
    )
