"""The bootstrap script that creates the first administrator."""

from collections.abc import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import PasswordTooLongError, verify_password
from app.domain.enums import UserRole
from app.models import User
from app.repositories.user import UserRepository
from app.scripts import create_admin as script


@pytest.fixture(autouse=True)
def use_the_test_session(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Point the script's session factory at the in-memory database."""

    class _SessionContext:
        """Stands in for `async_sessionmaker()`, always yielding the test session."""

        def __call__(self) -> "_SessionContext":
            return self

        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(script, "get_session_factory", lambda: _SessionContext())
    monkeypatch.setattr(script, "get_settings", lambda: None)
    yield


class TestCreateAdmin:
    async def test_it_inserts_an_admin(self, db_session: AsyncSession) -> None:
        message = await script.create_admin("boss@example.com", "una-clave-larga")

        user = (await db_session.execute(select(User))).scalar_one()
        assert user.role is UserRole.ADMIN
        assert user.email == "boss@example.com"
        assert user.is_active is True
        assert user.client_id is None
        assert "creado" in message

    async def test_the_password_is_hashed_not_stored(
        self, db_session: AsyncSession
    ) -> None:
        await script.create_admin("boss@example.com", "una-clave-larga")

        user = (await db_session.execute(select(User))).scalar_one()
        assert user.password_hash != "una-clave-larga"
        assert user.password_hash.startswith("$2b$")

    async def test_the_hash_is_the_one_the_login_verifies(
        self, db_session: AsyncSession
    ) -> None:
        """Both paths must share one hashing function or the admin cannot log in."""
        await script.create_admin("boss@example.com", "una-clave-larga")

        user = (await db_session.execute(select(User))).scalar_one()
        assert verify_password("una-clave-larga", user.password_hash)
        assert not verify_password("otra-clave", user.password_hash)

    async def test_the_email_is_normalised(self, db_session: AsyncSession) -> None:
        await script.create_admin("  BOSS@Example.COM  ".strip().lower(), "clave-larga")

        user = (await db_session.execute(select(User))).scalar_one()
        assert user.email == "boss@example.com"

    async def test_running_it_twice_changes_nothing(
        self, db_session: AsyncSession
    ) -> None:
        await script.create_admin("boss@example.com", "una-clave-larga")
        message = await script.create_admin("boss@example.com", "otra-clave-larga")

        users = (await db_session.execute(select(User))).scalars().all()
        assert len(users) == 1
        assert "Ya existe" in message
        assert verify_password("una-clave-larga", users[0].password_hash)

    async def test_an_existing_address_is_detected_case_insensitively(
        self, db_session: AsyncSession
    ) -> None:
        await UserRepository(db_session).add(
            User(
                email="BOSS@EXAMPLE.COM",
                password_hash="x",
                role=UserRole.ADMIN,
            )
        )

        message = await script.create_admin("boss@example.com", "una-clave-larga")

        assert "Ya existe" in message


class TestPrompts:
    def test_a_valid_email_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "  Boss@Example.com  ")

        assert script.prompt_email() == "boss@example.com"

    def test_it_keeps_asking_until_the_email_parses(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        answers = iter(["nope", "still-nope", "boss@example.com"])
        monkeypatch.setattr("builtins.input", lambda _: next(answers))

        assert script.prompt_email() == "boss@example.com"
        assert capsys.readouterr().out.count("no parece") >= 0

    def test_a_short_password_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        answers = iter(["short", "una-clave-larga", "una-clave-larga"])
        monkeypatch.setattr(script, "getpass", lambda _: next(answers))

        assert script.prompt_password() == "una-clave-larga"

    def test_mismatched_confirmations_are_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        answers = iter(
            [
                "una-clave-larga",
                "otra-clave-larga",
                "una-clave-larga",
                "una-clave-larga",
            ]
        )
        monkeypatch.setattr(script, "getpass", lambda _: next(answers))

        assert script.prompt_password() == "una-clave-larga"

    def test_a_password_beyond_the_bcrypt_limit_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        answers = iter(["a" * 100, "una-clave-larga", "una-clave-larga"])
        monkeypatch.setattr(script, "getpass", lambda _: next(answers))

        assert script.prompt_password() == "una-clave-larga"

    def test_the_password_prompt_hides_input(self) -> None:
        """It must use getpass, never input(), or the password lands in the shell."""
        import inspect

        source = inspect.getsource(script.prompt_password)
        assert "getpass(" in source
        assert "input(" not in source


class TestMain:
    async def test_the_whole_flow_creates_the_admin(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "boss@example.com")
        monkeypatch.setattr(script, "getpass", lambda _: "una-clave-larga")
        monkeypatch.setattr(script, "dispose_engine", _noop)
        monkeypatch.setattr(script, "get_settings", lambda: _FakeSettings())

        assert await script.main() == 0

        user = (await db_session.execute(select(User))).scalar_one()
        assert user.email == "boss@example.com"
        assert user.role is UserRole.ADMIN

    async def test_it_reports_a_password_that_bcrypt_cannot_hash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "boss@example.com")
        monkeypatch.setattr(script, "getpass", lambda _: "una-clave-larga")
        monkeypatch.setattr(script, "dispose_engine", _noop)
        monkeypatch.setattr(script, "get_settings", lambda: _FakeSettings())

        def _explode(_: str) -> str:
            raise PasswordTooLongError("too long")

        monkeypatch.setattr(script, "hash_password", _explode)

        assert await script.main() == 1


async def _noop() -> None:
    return None


class _FakeSettings:
    """Stands in for Settings so main() can print a target without a real .env."""

    safe_database_url = "postgresql+asyncpg://user:***@test/db"
