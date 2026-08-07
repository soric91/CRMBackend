"""Engine construction and session dependency semantics."""

from typing import cast

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import QueuePool

from app.core.config import Settings
from app.core.database import (
    build_connect_args,
    create_engine,
    dispose_engine,
    get_db_session,
    get_engine,
    get_session_factory,
)


class TestEngineConfiguration:
    async def test_engine_uses_the_asyncpg_driver(self, settings: Settings) -> None:
        engine = create_engine(settings)
        try:
            assert engine.url.drivername == "postgresql+asyncpg"
        finally:
            await engine.dispose()

    async def test_pool_settings_come_from_configuration(
        self, settings: Settings
    ) -> None:
        engine = create_engine(settings)
        try:
            pool = cast(QueuePool, engine.pool)
            assert pool.size() == settings.db_pool_size
        finally:
            await engine.dispose()

    async def test_pool_size_override_is_honoured(self, settings: Settings) -> None:
        engine = create_engine(settings.model_copy(update={"db_pool_size": 11}))
        try:
            pool = cast(QueuePool, engine.pool)
            assert pool.size() == 11
        finally:
            await engine.dispose()


class TestModuleLevelSingletons:
    async def test_engine_and_factory_are_reused(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        monkeypatch.setattr("app.core.database.get_settings", lambda: settings)
        await dispose_engine()
        try:
            assert get_engine() is get_engine()
            assert get_session_factory() is get_session_factory()
        finally:
            await dispose_engine()

    async def test_dispose_resets_the_singletons(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        monkeypatch.setattr("app.core.database.get_settings", lambda: settings)
        await dispose_engine()
        first = get_engine()
        await dispose_engine()
        second = get_engine()
        try:
            assert first is not second
        finally:
            await dispose_engine()


class TestSessionDependency:
    async def test_session_can_execute_statements(
        self, db_session: AsyncSession
    ) -> None:
        result = await db_session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1

    async def test_rollback_discards_pending_work(
        self, db_session: AsyncSession
    ) -> None:
        await db_session.execute(text("CREATE TABLE probe (id INTEGER PRIMARY KEY)"))
        await db_session.commit()
        await db_session.execute(text("INSERT INTO probe (id) VALUES (1)"))
        await db_session.rollback()

        result = await db_session.execute(text("SELECT COUNT(*) FROM probe"))
        assert result.scalar_one() == 0


class TestGetDbSessionDependency:
    async def test_commits_when_the_caller_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _RecordingSession()
        monkeypatch.setattr(
            "app.core.database.get_session_factory", lambda: lambda: session
        )

        generator = get_db_session()
        await anext(generator)
        with pytest.raises(StopAsyncIteration):
            await anext(generator)

        assert session.calls == ["commit", "close"]

    async def test_rolls_back_and_reraises_when_the_caller_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _RecordingSession()
        monkeypatch.setattr(
            "app.core.database.get_session_factory", lambda: lambda: session
        )

        generator = get_db_session()
        await anext(generator)
        with pytest.raises(RuntimeError, match="handler blew up"):
            await generator.athrow(RuntimeError("handler blew up"))

        assert session.calls == ["rollback", "close"]


class _RecordingSession:
    """Minimal async-context-manager stand-in recording lifecycle calls."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __aenter__(self) -> "_RecordingSession":
        return self

    async def __aexit__(self, *_: object) -> None:
        self.calls.append("close")

    async def commit(self) -> None:
        self.calls.append("commit")

    async def rollback(self) -> None:
        self.calls.append("rollback")


class TestConnectArgs:
    def test_ssl_mode_reaches_the_driver(self, settings: Settings) -> None:
        args = build_connect_args(
            settings.model_copy(update={"db_ssl_mode": "verify-full"})
        )
        assert args["ssl"] == "verify-full"

    def test_tls_is_on_by_default(self, settings: Settings) -> None:
        assert build_connect_args(settings)["ssl"] == "require"

    def test_prepared_statement_caches_are_disabled_for_pgbouncer(
        self, settings: Settings
    ) -> None:
        args = build_connect_args(settings)
        assert args["statement_cache_size"] == 0
        assert args["prepared_statement_cache_size"] == 0

    def test_application_name_identifies_the_service(self, settings: Settings) -> None:
        args = build_connect_args(settings)
        assert args["server_settings"]["application_name"] == settings.app_name
