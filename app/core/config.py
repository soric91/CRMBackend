"""Application settings loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    Field,
    PostgresDsn,
    PrivateAttr,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import URL, make_url

# Below this a shared secret is guessable offline given a single token.
MIN_JWT_SECRET_LENGTH = 32


def _read_pem(path: str | None, variable: str, algorithm: str) -> str:
    """Return the PEM at ``path``, failing with a message that names the fix."""
    if not path:
        raise ValueError(f"{algorithm} needs a key pair: set {variable}.")
    try:
        pem = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"{variable}: cannot read '{path}' ({exc.strerror}).") from exc
    if "-----BEGIN" not in pem:
        raise ValueError(f"{variable}: '{path}' is not a PEM file.")
    return pem


class Settings(BaseSettings):
    """Runtime configuration.

    Every value comes from the environment (or a local ``.env`` file).
    No secret is ever hardcoded here; see ``.env.example`` for the contract.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Loaded once by the validator below, so the PEM files are read at
    # start-up rather than on every token.
    _private_key_pem: str = PrivateAttr(default="")
    _public_key_pem: str = PrivateAttr(default="")

    # --- Application ---
    app_name: str = "CRM Backend"
    api_v1_prefix: str = "/api/v1"
    environment: Literal["local", "development", "staging", "production"] = "local"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    # NoDecode: the value is a plain comma-separated string, not JSON.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Database (Supabase / PostgreSQL) ---
    # The DSN may carry no password: keeping the secret in its own variable
    # avoids URL-escaping mistakes and makes rotation a one-line change.
    supabase_db_url: PostgresDsn
    supabase_db_password: SecretStr | None = None
    # Supabase requires TLS. `verify-full` is the right value once the client
    # trusts Supabase's CA; `require` encrypts without verifying the certificate.
    db_ssl_mode: Literal[
        "disable", "allow", "prefer", "require", "verify-ca", "verify-full"
    ] = "require"
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800

    # --- MQTT (notices to gateways, presence from them) ---
    # Off by default: the API has to run without a broker, and the bridge only
    # makes things faster, never correct.
    mqtt_enabled: bool = False
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_user: str | None = None
    mqtt_password: SecretStr | None = None
    mqtt_tls: bool = False
    # A namespace of its own, apart from the telemetry topics the gateways
    # already publish readings on.
    mqtt_topic_prefix: str = "crm/gateways"

    # --- Security (JWT) ---
    # HS* signs and verifies with the same secret, so anybody able to check a
    # token is also able to mint one. RS* splits that: the private key signs
    # here and never leaves, the public key only verifies. That is what lets
    # `ApiEMS` trust a token this API issued without being able to forge one,
    # and it is why the monitoring stack runs on RS256.
    jwt_algorithm: Literal["HS256", "HS384", "HS512", "RS256", "RS384", "RS512"] = (
        "HS256"
    )
    # Used by HS* only.
    jwt_secret_key: SecretStr | None = None
    # Used by RS* only. Paths rather than inline PEM: a PEM is multi-line, and
    # Docker's --env-file passes quotes through literally instead of stripping
    # them — the same trap that already bit us with the database password.
    jwt_private_key_path: str | None = None
    jwt_public_key_path: str | None = None
    # Cifra los valores de configuración de plataforma que hay que poder leer
    # de vuelta —la contraseña de MQTT, el token de InfluxDB del servidor—.
    # Vive en el entorno y no en la base a propósito: así un volcado de la
    # base, solo, no alcanza para leerlos. Sin esta clave la API se niega a
    # guardar un valor secreto en vez de guardarlo en claro.
    #
    # Generar una con:
    #   uv run python -c "from app.core.secret_box import generate_key; \
    #                     print(generate_key())"
    settings_encryption_key: SecretStr | None = None
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    # The firmware renews this itself with its credential, so a short life
    # costs nothing operationally and bounds the damage of a leaked token.
    gateway_token_expire_hours: int = 24

    # Life of the token another system gets in exchange for its credential.
    # Short on purpose: revoking a service account cannot reach a token that
    # was already minted, so the window in which a revocation is not yet felt
    # is exactly this number.
    service_token_expire_minutes: int = 60

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Allow a comma-separated string in ``.env`` for CORS origins."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("supabase_db_url", mode="before")
    @classmethod
    def _reject_http_project_url(cls, value: object) -> object:
        """Reject the Supabase Project URL, a common copy-paste mistake.

        ``https://<ref>.supabase.co`` is the PostgREST endpoint, not a
        PostgreSQL DSN. ``PostgresDsn`` already rejects it, but the default
        message does not explain where the right value lives.
        """
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            raise ValueError(
                "SUPABASE_DB_URL must be a PostgreSQL DSN (postgresql://...), "
                "not the Supabase Project URL. Copy it from "
                "Dashboard > Connect > Connection String > Session pooler."
            )
        return value

    @model_validator(mode="after")
    def _require_the_keys_of_the_chosen_algorithm(self) -> Self:
        """Load and check whatever the configured algorithm actually needs.

        Done here rather than on first use: a missing or unreadable key is a
        deployment mistake, and it should stop the process at start-up instead
        of turning every login into a 500.
        """
        if self.uses_asymmetric_jwt:
            self._private_key_pem = _read_pem(
                self.jwt_private_key_path, "JWT_PRIVATE_KEY_PATH", self.jwt_algorithm
            )
            self._public_key_pem = _read_pem(
                self.jwt_public_key_path, "JWT_PUBLIC_KEY_PATH", self.jwt_algorithm
            )
            return self

        if self.jwt_secret_key is None:
            raise ValueError(
                f"{self.jwt_algorithm} signs with a shared secret: set JWT_SECRET_KEY."
            )
        if len(self.jwt_secret_key.get_secret_value()) < MIN_JWT_SECRET_LENGTH:
            raise ValueError(
                f"JWT_SECRET_KEY must be at least {MIN_JWT_SECRET_LENGTH} characters."
            )
        return self

    @property
    def uses_asymmetric_jwt(self) -> bool:
        """Whether tokens are signed with a key pair rather than a secret."""
        return self.jwt_algorithm.startswith("RS")

    @property
    def jwt_signing_key(self) -> str:
        """The key that mints tokens. Never leaves this process."""
        if self.uses_asymmetric_jwt:
            return self._private_key_pem
        # Checked by the validator above, so this cannot be None here.
        return self.jwt_secret_key.get_secret_value()  # pyright: ignore[reportOptionalMemberAccess]

    @property
    def jwt_verification_key(self) -> str:
        """The key that checks a signature.

        Identical to the signing key under HS*, which is exactly the property
        that makes HS* unusable across services.
        """
        if self.uses_asymmetric_jwt:
            return self._public_key_pem
        return self.jwt_secret_key.get_secret_value()  # pyright: ignore[reportOptionalMemberAccess]

    @property
    def jwt_public_key_pem(self) -> str | None:
        """The public key, for publishing. None while signing with a secret."""
        return self._public_key_pem if self.uses_asymmetric_jwt else None

    @model_validator(mode="after")
    def _require_a_password(self) -> Self:
        """Fail fast when neither the DSN nor its own variable carries one."""
        if self.database_url.password:
            return self
        raise ValueError(
            "No database password: set SUPABASE_DB_PASSWORD, or embed the "
            "password in SUPABASE_DB_URL."
        )

    @property
    def database_url(self) -> URL:
        """Return the connection URL forced to the ``asyncpg`` driver.

        Supabase hands out ``postgresql://`` (or ``postgres://``) URLs; the
        async engine needs an explicit async driver. When
        ``SUPABASE_DB_PASSWORD`` is set it wins over any password inlined in
        the DSN, and SQLAlchemy escapes it for us.
        """
        url = make_url(str(self.supabase_db_url)).set(drivername="postgresql+asyncpg")
        if self.supabase_db_password is not None:
            url = url.set(password=self.supabase_db_password.get_secret_value())
        return url

    @property
    def async_database_url(self) -> str:
        """The connection URL as a string, password included.

        Only for consumers that cannot take a SQLAlchemy ``URL`` object.
        Never log this; use :attr:`safe_database_url` instead.
        """
        return self.database_url.render_as_string(hide_password=False)

    @property
    def safe_database_url(self) -> str:
        """The connection URL with the password masked, safe to log."""
        return self.database_url.render_as_string(hide_password=True)

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance (one env read per process)."""
    return Settings()  # pyright: ignore[reportCallIssue]
