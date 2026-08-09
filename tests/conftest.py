"""Shared test fixtures.

Tests never touch the real Supabase database: settings are built from
throwaway in-memory values, and the database dependency is overridden.
"""

import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.database import Base, get_db_session
from app.core.security import hash_password
from app.domain.enums import UserRole
from app.main import create_app
from app.models import Client, User

TEST_DB_URL = "postgresql://test_user:test_pass@localhost:5432/test_db"
TEST_JWT_SECRET = "unit-test-secret-key-not-used-anywhere-real-0000"
# Clave de cifrado de la configuración de plataforma. Generada para los
# tests y sin uso en ningún lado real.
TEST_SETTINGS_KEY = "iyfEs5t-40puTXN5i2FJ0BoCoBUylchZlywPps09ikM="


@pytest.fixture
def settings() -> Settings:
    """Settings built from explicit test values, ignoring any local ``.env``."""
    return Settings(
        database_dsn=TEST_DB_URL,  # pyright: ignore[reportArgumentType]
        jwt_secret_key=TEST_JWT_SECRET,
        settings_encryption_key=TEST_SETTINGS_KEY,
        environment="local",
        _env_file=None,  # pyright: ignore[reportCallIssue]
    )


@pytest.fixture(scope="session")
def rsa_keypair(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """One RSA key pair for the whole run — generating them is slow."""
    directory = tmp_path_factory.mktemp("jwt-keys")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    private_path = directory / "private.pem"
    private_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_path = directory / "public.pem"
    public_path.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_path, public_path


@pytest.fixture
def rs256_settings(rsa_keypair: tuple[Path, Path]) -> Settings:
    """Settings signing with a key pair rather than a shared secret."""
    private_path, public_path = rsa_keypair
    return Settings(
        database_dsn=TEST_DB_URL,  # pyright: ignore[reportArgumentType]
        jwt_algorithm="RS256",
        jwt_private_key_path=str(private_path),
        jwt_public_key_path=str(public_path),
        settings_encryption_key=TEST_SETTINGS_KEY,
        environment="local",
        _env_file=None,  # pyright: ignore[reportCallIssue]
    )


@pytest.fixture
def rs256_app(rs256_settings: Settings, db_session: AsyncSession) -> Iterator[FastAPI]:
    """The application as it runs once the migration to RS256 is done."""
    application = create_app(rs256_settings)

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    application.dependency_overrides[get_db_session] = _override
    yield application
    application.dependency_overrides.clear()


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    """A SQLite-backed session with the real schema created on it."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _enforce_foreign_keys(dbapi_connection: Any, _: Any) -> None:
        """SQLite ignores foreign keys unless asked; PostgreSQL never does."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def app(settings: Settings, db_session: AsyncSession) -> Iterator[FastAPI]:
    """App instance whose database dependency yields the test session."""
    application = create_app(settings)

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    application.dependency_overrides[get_db_session] = _override
    yield application
    application.dependency_overrides.clear()


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient]:
    """HTTP client talking to the app in-process (no network, no server)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


TEST_PASSWORD = "una-clave-de-prueba"
# Hashed once per session: bcrypt is deliberately slow, and every user fixture
# would otherwise pay for it again.
TEST_PASSWORD_HASH = hash_password(TEST_PASSWORD)


@pytest.fixture
def make_password_hash() -> Callable[[str], str]:
    """The same hashing the login endpoint verifies against."""
    return hash_password


@pytest.fixture
async def admin_user(db_session: AsyncSession) -> User:
    return await _persist_user(db_session, "admin@example.com", UserRole.ADMIN)


@pytest.fixture
async def tecnico_user(db_session: AsyncSession) -> User:
    return await _persist_user(db_session, "tecnico@example.com", UserRole.TECNICO)


@pytest.fixture
async def cliente_user(db_session: AsyncSession) -> User:
    client = Client(nombre_empresa="Industrias Andinas")
    db_session.add(client)
    await db_session.flush()
    return await _persist_user(
        db_session, "cliente@example.com", UserRole.CLIENTE, client_id=client.id
    )


async def _persist_user(
    session: AsyncSession,
    email: str,
    role: UserRole,
    *,
    client_id: uuid.UUID | None = None,
    is_active: bool = True,
) -> User:
    user = User(
        email=email,
        password_hash=TEST_PASSWORD_HASH,
        role=role,
        client_id=client_id,
        is_active=is_active,
    )
    session.add(user)
    await session.flush()
    return user


@pytest.fixture
def authenticate(client: AsyncClient) -> Callable[[str], Awaitable[str]]:
    """Log in and return the access token."""

    async def _login(email: str, password: str = TEST_PASSWORD) -> str:
        response = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        response.raise_for_status()
        token: str = response.json()["access_token"]
        return token

    return _login


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def app_openapi(app: FastAPI) -> dict[str, Any]:
    """The app's OpenAPI document, for tests that assert on the route surface."""
    schema: dict[str, Any] = app.openapi()
    return schema


@pytest.fixture
def authenticate_monitor(client: AsyncClient) -> Callable[..., Awaitable[str]]:
    """Log a client in through the monitoring web and return its access token."""

    async def _login(email: str, password: str = TEST_PASSWORD) -> str:
        response = await client.post(
            "/api/v1/auth-monitor/login", json={"email": email, "password": password}
        )
        response.raise_for_status()
        token: str = response.json()["access_token"]
        return token

    return _login
