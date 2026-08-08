"""Password hashing and JSON Web Tokens.

Both the login endpoint and the bootstrap script call the functions here, so a
password hashed by one is always verifiable by the other.

Hashing uses the ``bcrypt`` library directly rather than ``passlib``: passlib's
last release is from 2020 and it crashes against bcrypt 4.1+ while reading a
private attribute that no longer exists.
"""

import hashlib
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import bcrypt
import jwt

from app.core.config import Settings
from app.core.exceptions import AuthenticationError
from app.core.jwks import compute_key_id

# bcrypt hashes at most 72 bytes and silently ignores the rest, which would
# make two different long passwords interchangeable.
MAX_PASSWORD_BYTES = 72


class TokenType(StrEnum):
    """What a token may be used for."""

    ACCESS = "access"
    REFRESH = "refresh"


class TokenAudience(StrEnum):
    """Which surface a token belongs to.

    The CRM and the monitoring web are separate authentication surfaces with
    different audiences and rules. Without this claim both logins would mint
    interchangeable tokens and the separation would exist only in the URL.
    """

    CRM = "crm"
    MONITOR = "monitor"
    # The firmware. Its token is minted by exchanging the gateway credential
    # and is useless on the two human-facing surfaces.
    GATEWAY = "gateway"
    # Another system, not a person: `ApiEMS` reading tariffs and the fleet.
    # Reaches only the endpoints that opt into it, and only for reading.
    SERVICE = "service"


class TokenScope(StrEnum):
    """How much of the API a token reaches.

    A password set by somebody else is not yet a credential the account owner
    chose, so the token it produces is deliberately crippled: it opens the two
    routes needed to replace it and nothing else. Returning a flag for the UI
    to honour would not be an obligation — anyone holding the token could just
    call the API directly.
    """

    FULL = "full"
    PASSWORD_CHANGE = "password_change"  # noqa: S105 - a scope name, not a secret


class PasswordTooLongError(ValueError):
    """Raised when a password exceeds what bcrypt can actually hash."""


def hash_password(password: str) -> str:
    """Return a salted bcrypt hash of ``password``."""
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise PasswordTooLongError(
            f"Password exceeds {MAX_PASSWORD_BYTES} bytes, which bcrypt truncates"
        )
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Return whether ``password`` matches ``password_hash``.

    Never raises on malformed input: a corrupted hash in the database must read
    as a failed login, not as a 500.
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# A hash of a value nobody can log in with. Verifying against it lets the login
# path spend the same time on an unknown email as on a known one, so response
# timing does not reveal which addresses exist.
_DUMMY_HASH = hash_password(uuid.uuid4().hex)


def hash_lookup_token(token: str) -> str:
    """Return the sha256 of a token, for storing and looking it up.

    Distinto de :func:`hash_password`, y a propósito. Bcrypt existe para hacer
    lento el ataque por diccionario contra un secreto que una persona eligió;
    esto son 32 bytes al azar, donde un diccionario no sirve.

    Y bcrypt no se puede indexar: cada verificación es contra un hash
    conocido. Acá la petición llega con el token y hay que **encontrar** su
    fila, así que el hash tiene que ser determinista y buscable. Es el mismo
    razonamiento por el que `ServiceAccount.credencial_id` es público e
    indexado y solo su secreto va con bcrypt.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def waste_time_like_a_real_verification() -> None:
    """Burn one bcrypt verification to keep login timing uniform."""
    verify_password("does-not-matter", _DUMMY_HASH)


def create_token(
    settings: Settings,
    *,
    subject: str,
    token_type: TokenType,
    expires_in: timedelta,
    audience: TokenAudience = TokenAudience.CRM,
    claims: dict[str, Any] | None = None,
) -> str:
    """Return a signed JWT for ``subject``."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        **(claims or {}),
        "sub": subject,
        "type": token_type.value,
        "aud": audience.value,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_in).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    # `kid` names which published key verifies this token. Without it a
    # consumer holding two keys during a rotation has to try both, and the
    # older one keeps working for longer than intended.
    headers = (
        {"kid": compute_key_id(settings.jwt_verification_key)}
        if settings.uses_asymmetric_jwt
        else None
    )
    return jwt.encode(
        payload,
        settings.jwt_signing_key,
        algorithm=settings.jwt_algorithm,
        headers=headers,
    )


def create_access_token(
    settings: Settings,
    *,
    subject: str,
    audience: TokenAudience = TokenAudience.CRM,
    claims: dict[str, Any] | None = None,
) -> str:
    """Return a short-lived token carrying the caller's identity and role."""
    return create_token(
        settings,
        subject=subject,
        token_type=TokenType.ACCESS,
        expires_in=timedelta(minutes=settings.access_token_expire_minutes),
        audience=audience,
        claims=claims,
    )


def create_refresh_token(
    settings: Settings,
    *,
    subject: str,
    audience: TokenAudience = TokenAudience.CRM,
    claims: dict[str, Any] | None = None,
) -> str:
    """Return a long-lived token whose only power is minting access tokens.

    It deliberately carries neither role nor scope: both are re-read from the
    database when it is exchanged, so a demotion — or a still-pending password
    change — takes effect without waiting for the refresh token to expire.
    Refreshing can therefore never be a way out of the mandatory change.

    ``claims`` es la excepción a esa regla, y existe para una sola cosa: la
    empresa que un administrador eligió mirar. Esa no es un dato de la cuenta
    que se pueda releer —es una elección— así que si no viaja acá, refrescar
    devuelve al administrador a la pantalla de proyectos cada vez. El permiso
    para usarla se sigue verificando contra la base al canjear el token.
    """
    return create_token(
        settings,
        claims=claims,
        subject=subject,
        token_type=TokenType.REFRESH,
        expires_in=timedelta(days=settings.refresh_token_expire_days),
        audience=audience,
    )


def decode_token(
    settings: Settings,
    token: str,
    *,
    expected_type: TokenType,
    expected_audience: TokenAudience | Sequence[TokenAudience] = TokenAudience.CRM,
) -> dict[str, Any]:
    """Validate a JWT and return its claims.

    Raises :class:`AuthenticationError` for anything wrong: bad signature,
    expiry, a tampered algorithm, a refresh token used as an access token, or
    a token minted for the other surface.
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_verification_key,
            # Exactly one algorithm, never a list. Two reasons: a token could
            # otherwise name `none` and skip verification entirely, and under
            # RS* it could name HS256 so that the *public* key — which anyone
            # can fetch — is treated as the shared secret. That second one is
            # the algorithm-confusion attack, and pinning is the whole defence.
            algorithms=[settings.jwt_algorithm],
            audience=(
                [expected_audience.value]
                if isinstance(expected_audience, TokenAudience)
                else [item.value for item in expected_audience]
            ),
            options={"require": ["exp", "sub", "type", "aud"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid token") from exc

    if payload.get("type") != expected_type.value:
        raise AuthenticationError(f"Expected a {expected_type.value} token")
    return payload
