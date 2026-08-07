"""Settings behaviour: env parsing, validation and derived values."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.config import Settings, get_settings
from tests.conftest import TEST_JWT_SECRET

DSN_WITHOUT_PASSWORD = (
    "postgresql://postgres.abc@aws-0-eu-west-1.pooler.supabase.com:5432/postgres"
)
DSN_WITH_PASSWORD = "postgresql://user:pass@db.example.com:5432/postgres"


def _build(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "supabase_db_url": DSN_WITH_PASSWORD,
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
            supabase_db_url="postgres://user:pass@db.example.com:5432/postgres"
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
            _build(supabase_db_url="mysql://user:pass@localhost:3306/db")

    def test_missing_url_is_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            Settings(  # pyright: ignore[reportCallIssue]
                jwt_secret_key=TEST_JWT_SECRET,
                _env_file=None,  # pyright: ignore[reportCallIssue]
            )

    def test_supabase_project_url_is_rejected_with_a_useful_message(self) -> None:
        with pytest.raises(PydanticValidationError, match="Session pooler"):
            _build(supabase_db_url="https://abcdef.supabase.co")


class TestDatabasePassword:
    def test_separate_password_is_merged_into_the_url(self) -> None:
        settings = _build(
            supabase_db_url=DSN_WITHOUT_PASSWORD, supabase_db_password="s3cret"
        )
        assert settings.database_url.password == "s3cret"

    def test_separate_password_overrides_one_inlined_in_the_dsn(self) -> None:
        settings = _build(supabase_db_password="from-variable")
        assert settings.database_url.password == "from-variable"

    def test_password_inlined_in_the_dsn_is_used_when_no_variable_is_set(self) -> None:
        assert _build().database_url.password == "pass"

    @pytest.mark.parametrize("password", ["p@ss/word", "100%sure", "a:b#c", "sla/sh"])
    def test_special_characters_survive_a_round_trip(self, password: str) -> None:
        settings = _build(
            supabase_db_url=DSN_WITHOUT_PASSWORD, supabase_db_password=password
        )
        assert settings.database_url.password == password

    def test_special_characters_are_escaped_in_the_rendered_string(self) -> None:
        settings = _build(
            supabase_db_url=DSN_WITHOUT_PASSWORD, supabase_db_password="p@ss/word"
        )
        rendered = settings.async_database_url
        assert "p%40ss%2Fword" in rendered
        # A raw '@' would split the DSN at the wrong place.
        assert "p@ss" not in rendered

    def test_no_password_anywhere_is_rejected(self) -> None:
        with pytest.raises(PydanticValidationError, match="SUPABASE_DB_PASSWORD"):
            _build(supabase_db_url=DSN_WITHOUT_PASSWORD)


class TestSecretHandling:
    def test_password_is_masked_in_the_safe_url(self) -> None:
        settings = _build(
            supabase_db_url=DSN_WITHOUT_PASSWORD, supabase_db_password="s3cret"
        )
        assert "s3cret" not in settings.safe_database_url
        assert settings.safe_database_url.startswith("postgresql+asyncpg://")

    def test_password_is_not_leaked_by_repr(self) -> None:
        settings = _build(
            supabase_db_url=DSN_WITHOUT_PASSWORD, supabase_db_password="s3cret"
        )
        assert "s3cret" not in repr(settings)

    def test_jwt_secret_is_not_leaked_by_repr(self) -> None:
        assert TEST_JWT_SECRET not in repr(_build())

    def test_jwt_secret_is_readable_on_demand(self) -> None:
        assert _build().jwt_signing_key == TEST_JWT_SECRET

    def test_async_database_url_still_carries_the_password(self) -> None:
        """Alembic and the engine need the real value."""
        settings = _build(
            supabase_db_url=DSN_WITHOUT_PASSWORD, supabase_db_password="s3cret"
        )
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
        monkeypatch.setenv("SUPABASE_DB_URL", DSN_WITHOUT_PASSWORD)
        monkeypatch.setenv("SUPABASE_DB_PASSWORD", "from-env")
        monkeypatch.setenv("JWT_SECRET_KEY", TEST_JWT_SECRET)
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")

        settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

        assert settings.log_level == "DEBUG"
        assert settings.database_url.host == "aws-0-eu-west-1.pooler.supabase.com"
        assert settings.database_url.password == "from-env"

    def test_get_settings_is_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SUPABASE_DB_URL", DSN_WITH_PASSWORD)
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
