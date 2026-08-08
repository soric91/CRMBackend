"""Structural guarantees the whole schema must keep.

These catch a regression in a later phase that a per-model test would miss:
a new table with no cascade rule, a forgotten import, a stray naming style.
"""

import pytest
from sqlalchemy import Table

from app.core.database import Base
from app.models import __all__ as exported_models

EXPECTED_TABLES = {
    "alerts_config",
    "clients",
    "enrollment_tokens",
    "equipment",
    "gateways",
    "platform_settings",
    "service_accounts",
    "sites",
    "tariffs",
    "users",
    "variables",
}


def _tables() -> list[Table]:
    return list(Base.metadata.tables.values())


class TestMetadata:
    def test_every_expected_table_is_registered(self) -> None:
        assert set(Base.metadata.tables) == EXPECTED_TABLES

    def test_every_model_is_exported_for_alembic(self) -> None:
        """A model missing from __init__ is invisible to autogenerate."""
        assert len(exported_models) == len(EXPECTED_TABLES)

    @pytest.mark.parametrize("table", _tables(), ids=lambda t: t.name)
    def test_each_table_has_a_uuid_primary_key(self, table: Table) -> None:
        primary_key = list(table.primary_key.columns)
        assert len(primary_key) == 1
        assert primary_key[0].name == "id"
        assert primary_key[0].type.python_type.__name__ == "UUID"

    @pytest.mark.parametrize("table", _tables(), ids=lambda t: t.name)
    def test_each_table_is_timestamped(self, table: Table) -> None:
        assert "created_at" in table.columns
        assert "updated_at" in table.columns

    @pytest.mark.parametrize("table", _tables(), ids=lambda t: t.name)
    def test_constraint_names_follow_the_convention(self, table: Table) -> None:
        """Unnamed constraints cannot be dropped by a later migration."""
        for constraint in table.constraints:
            assert constraint.name is not None
            assert str(constraint.name).startswith(("pk_", "fk_", "uq_", "ck_"))


class TestReferentialIntegrity:
    @pytest.mark.parametrize("table", _tables(), ids=lambda t: t.name)
    def test_every_foreign_key_states_a_delete_policy(self, table: Table) -> None:
        """Leaving ondelete unset makes PostgreSQL block deletes silently."""
        for foreign_key in table.foreign_keys:
            assert foreign_key.ondelete in {"CASCADE", "RESTRICT"}

    def test_the_hierarchy_cascades_from_the_client_down(self) -> None:
        chain = [
            ("sites", "clients"),
            ("gateways", "sites"),
            ("equipment", "gateways"),
            ("variables", "equipment"),
            ("alerts_config", "gateways"),
        ]
        for child, parent in chain:
            keys = [
                fk
                for fk in Base.metadata.tables[child].foreign_keys
                if fk.column.table.name == parent
            ]
            assert keys, f"{child} has no foreign key to {parent}"
            assert all(fk.ondelete == "CASCADE" for fk in keys)

    def test_logins_are_never_cascade_deleted_with_their_client(self) -> None:
        keys = list(Base.metadata.tables["users"].foreign_keys)
        assert [fk.ondelete for fk in keys] == ["RESTRICT"]


class TestSecrets:
    def test_gateways_store_no_credential(self) -> None:
        """v1 has no firmware config endpoint, so a gateway needs no secret."""
        columns = set(Base.metadata.tables["gateways"].columns.keys())
        assert not any("token" in name for name in columns)

    def test_no_column_looks_like_a_plaintext_password(self) -> None:
        for table in _tables():
            for column in table.columns:
                assert column.name not in {"password", "token", "secret"}


class TestRepresentations:
    """__repr__ shows up in logs and debuggers; it must never raise."""

    def test_each_model_renders_its_identifying_field(self) -> None:
        from datetime import date
        from decimal import Decimal

        from app.domain.enums import AlertType, EquipmentType, UserRole
        from app.models import (
            AlertConfig,
            Client,
            Equipment,
            Gateway,
            Site,
            Tariff,
            User,
            Variable,
        )

        cases = [
            (Client(nombre_empresa="Acme"), "Acme"),
            (Site(nombre="Planta Norte"), "Planta Norte"),
            (Gateway(numero_serie="GW-1"), "GW-1"),
            (Equipment(tipo=EquipmentType.MEDIDOR, modbus_id=3), "id=3"),
            (Variable(nombre="PhV_phsA", registro_modbus=100), "PhV_phsA"),
            (
                Tariff(mes=date(2026, 6, 1), valor_importado=Decimal("780")),
                "junio 2026",
            ),
            (User(email="a@b.com", role=UserRole.ADMIN), "a@b.com"),
            (AlertConfig(tipo=AlertType.DESCONEXION), "desconexion"),
        ]
        for instance, expected in cases:
            assert expected in repr(instance)

    def test_a_global_alert_rule_says_so(self) -> None:
        from app.domain.enums import AlertType
        from app.models import AlertConfig

        assert "global" in repr(AlertConfig(tipo=AlertType.DESCONEXION))
