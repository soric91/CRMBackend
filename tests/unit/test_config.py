"""Settings behaviour: env parsing, validation and derived values."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.config import Settings, get_settings
from tests.conftest import TEST_JWT_SECRET

DSN_WITHOUT_PASSWORD = "postgresql://usuario@db.example.com:5432/postgres"
DSN_WITH_PASSWORD = "postgresql://user:pass@db.example.com:5432/postgres"


def _build(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_dsn": DSN_WITH_PASSWORD,
        "jwt_secret_key": TEST_JWT_SECRET,
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)  # pyright: ignore[reportCallIssue, reportArgumentType]


class TestDatabaseUrl:
    def test_postgresql_scheme_is_rewritten_to_asyncpg(self) -> None:
        assert _build().database_url.drivername == "postgresql+asyncpg"

    def test_postgres_alias_scheme_is_also_rewritten(self) -> None:
        settings = _build(
            database_dsn="postgres://user:pass@db.example.com:5432/postgres"
        )
        assert settings.database_url.drivername == "postgresql+asyncpg"

    def test_host_port_and_database_survive_the_rewrite(self) -> None:
        url = _build().database_url
        assert url.host == "db.example.com"
        assert url.port == 5432
        assert url.database == "postgres"
        assert url.username == "user"

    def test_non_postgres_url_is_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            _build(database_dsn="mysql://user:pass@localhost:3306/db")

    def test_missing_url_is_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            Settings(  # pyright: ignore[reportCallIssue]
                jwt_secret_key=TEST_JWT_SECRET,
                _env_file=None,  # pyright: ignore[reportCallIssue]
            )

    def test_an_http_endpoint_is_rejected_with_a_useful_message(self) -> None:
        """El error de copiar y pegar más común: los servicios administrados
        muestran una URL HTTP —su API REST— al lado de la cadena de conexión."""
        with pytest.raises(PydanticValidationError, match="PostgreSQL DSN"):
            _build(database_dsn="https://abcdef.example.com")


class TestDatabasePassword:
    """La contraseña viaja dentro del DSN y no en una variable aparte.

    Tenerla separada existía por el flujo de Supabase, cuyo panel entrega la
    URL con `[YOUR-PASSWORD]` de marcador. Fuera de ahí es un segundo lugar
    donde buscarla, y la aplicación tenía que fusionar dos fuentes que podían
    contradecirse.
    """

    def test_the_password_comes_from_the_dsn(self) -> None:
        assert _build().database_url.password == "pass"

    @pytest.mark.parametrize("password", ["p@ss/word", "100%sure", "a:b#c", "sla/sh"])
    def test_a_percent_encoded_password_survives_the_rewrite(
        self, password: str
    ) -> None:
        """Con caracteres reservados hay que codificarlos en el `.env`; lo que
        se prueba acá es que reescribir el driver no los estropea."""
        from urllib.parse import quote

        settings = _build(
            database_dsn=f"postgresql://user:{quote(password, safe='')}@h:5432/db"
        )
        assert settings.database_url.password == password

    def test_a_dsn_without_password_is_rejected(self) -> None:
        """Conectarse sin contraseña solo funciona con `trust` en el servidor,
        que nadie debería tener abierto. Mejor fallar al arrancar."""
        with pytest.raises(PydanticValidationError, match="must include the password"):
            _build(database_dsn=DSN_WITHOUT_PASSWORD)


class TestSecretHandling:
    def test_password_is_masked_in_the_safe_url(self) -> None:
        settings = _build(database_dsn="postgresql://user:s3cret@h:5432/db")
        assert "s3cret" not in settings.safe_database_url
        assert settings.safe_database_url.startswith("postgresql+asyncpg://")

    def test_password_is_not_leaked_by_repr(self) -> None:
        """La contraseña ahora vive dentro del DSN, así que lo que no debe
        aparecer en un `repr` es la URL entera."""
        settings = _build(database_dsn="postgresql://user:s3cret@h:5432/db")
        assert "s3cret" not in repr(settings)

    def test_jwt_secret_is_not_leaked_by_repr(self) -> None:
        assert TEST_JWT_SECRET not in repr(_build())

    def test_jwt_secret_is_readable_on_demand(self) -> None:
        assert _build().jwt_signing_key == TEST_JWT_SECRET

    def test_async_database_url_still_carries_the_password(self) -> None:
        """Alembic and the engine need the real value."""
        settings = _build(database_dsn="postgresql://user:s3cret@h:5432/db")
        assert "s3cret" in settings.async_database_url


class TestJwtSecret:
    def test_short_secret_is_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            _build(jwt_secret_key="too-short")

    def test_secret_of_exactly_32_chars_is_accepted(self) -> None:
        settings = _build(jwt_secret_key="x" * 32)
        assert len(settings.jwt_signing_key) == 32


class TestCorsOrigins:
    def test_comma_separated_string_is_split(self) -> None:
        settings = _build(cors_origins="https://a.example.com, https://b.example.com")
        assert settings.cors_origins == [
            "https://a.example.com",
            "https://b.example.com",
        ]

    def test_empty_string_yields_no_origins(self) -> None:
        assert _build(cors_origins="").cors_origins == []

    def test_list_is_passed_through(self) -> None:
        assert _build(cors_origins=["https://a.example.com"]).cors_origins == [
            "https://a.example.com"
        ]


class TestEnvironment:
    def test_local_is_the_default_and_not_production(self) -> None:
        settings = _build()
        assert settings.environment == "local"
        assert settings.is_production is False

    def test_production_flag(self) -> None:
        assert _build(environment="production").is_production is True

    def test_unknown_environment_is_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            _build(environment="staging-2")

    def test_unknown_log_level_is_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            _build(log_level="TRACE")


class TestSettingsFromEnvironment:
    def test_values_are_read_from_environment_variables(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:from-env@db.example.com:5432/x")
        monkeypatch.setenv("JWT_SECRET_KEY", TEST_JWT_SECRET)
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")

        settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

        assert settings.log_level == "DEBUG"
        assert settings.database_url.host == "db.example.com"
        assert settings.database_url.password == "from-env"

    def test_get_settings_is_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", DSN_WITH_PASSWORD)
        monkeypatch.setenv("JWT_SECRET_KEY", TEST_JWT_SECRET)
        get_settings.cache_clear()

        assert get_settings() is get_settings()
        get_settings.cache_clear()


class TestDatabaseTls:
    def test_tls_is_required_by_default(self) -> None:
        assert _build().db_ssl_mode == "require"

    def test_stricter_mode_is_accepted(self) -> None:
        assert _build(db_ssl_mode="verify-full").db_ssl_mode == "verify-full"

    def test_unknown_mode_is_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            _build(db_ssl_mode="sslv3")
