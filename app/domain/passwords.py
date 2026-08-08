"""Generation of the initial password of an account.

Random, never derived from the client's data. A rule built from the company
name, email or phone is the same rule for every client: whoever works it out
from one account opens all of them, and the password stays valid until that
person logs in for the first time — possibly months.
"""

import secrets

# No 0/O/1/l/I: this gets read off a screen and dictated over the phone.
_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# 16 characters of this alphabet is about 95 bits of entropy: out of reach of
# online guessing even without the rate limiter in front of it.
TEMPORARY_PASSWORD_LENGTH = 16


def generate_temporary_password() -> str:
    """Return a one-off password, shown once and never recoverable."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(TEMPORARY_PASSWORD_LENGTH))


# The gateway's credential is machine-to-machine: nobody types it, so it is
# longer and uses the full URL-safe alphabet.
GATEWAY_CREDENTIAL_BYTES = 32


def generate_gateway_credential() -> str:
    """Return the long-lived secret a gateway authenticates with.

    Shown once, when issued. Only its hash is stored, so a lost credential is
    reissued rather than recovered — the same rule as every other secret here.
    """
    return secrets.token_urlsafe(GATEWAY_CREDENTIAL_BYTES)


# A service credential is two halves. The identifier is public and travels in
# every token request; the secret is the part that proves the caller holds it.
SERVICE_IDENTIFIER_BYTES = 12
SERVICE_SECRET_BYTES = 32

# Prefixes so a leaked string is recognisable on sight — in a log, a commit or
# a screenshot — and can be revoked without first working out what it opens.
SERVICE_IDENTIFIER_PREFIX = "svc_"
SERVICE_SECRET_PREFIX = "svcsec_"  # noqa: S105 - a prefix, not a secret


def generate_service_identifier() -> str:
    """Return the public half of a service credential."""
    return SERVICE_IDENTIFIER_PREFIX + secrets.token_urlsafe(SERVICE_IDENTIFIER_BYTES)


def generate_service_secret() -> str:
    """Return the private half, shown once and stored only as a hash."""
    return SERVICE_SECRET_PREFIX + secrets.token_urlsafe(SERVICE_SECRET_BYTES)


# El token de enrolamiento vive horas y se gasta al usarse, pero mientras vive
# entrega la configuración entera de un equipo. Misma longitud que la
# credencial: nadie lo teclea, así que no hay razón para acortarlo.
ENROLLMENT_TOKEN_BYTES = 32


def generate_enrollment_token() -> str:
    """Return the one-off token that exchanges for a gateway's configuration.

    Opaco a propósito: no lleva adentro a qué gateway pertenece. Un token
    filtrado no dice ni de qué instalación es, y la relación vive en una fila
    que se puede revocar.
    """
    return secrets.token_urlsafe(ENROLLMENT_TOKEN_BYTES)
