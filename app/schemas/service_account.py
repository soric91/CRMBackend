"""Request and response models for machine-to-machine credentials.

The secret appears in exactly two places: the response to creating an account
and the response to rotating one. Nowhere else — not in the listing, not in
the detail view, not in an error. That is the whole reason those two responses
have their own model.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import ServicePermission


class ServiceAccountCreate(BaseModel):
    """Payload for issuing a credential to another system."""

    nombre: str = Field(min_length=1, max_length=120)
    descripcion: str | None = Field(default=None, max_length=300)
    # At least one: an account that may read nothing is not a useful
    # credential, it is a secret with no purpose that still has to be rotated.
    permisos: list[ServicePermission] = Field(min_length=1)
    # Left out, the credential reaches the whole platform. Set, it is pinned to
    # one client exactly as a `cliente` login is.
    client_id: uuid.UUID | None = None
    expira_en: datetime | None = None


class ServiceAccountUpdate(BaseModel):
    """Partial update. The secret is never touched here — rotate it instead."""

    nombre: str | None = Field(default=None, min_length=1, max_length=120)
    descripcion: str | None = Field(default=None, max_length=300)
    permisos: list[ServicePermission] | None = Field(default=None, min_length=1)
    activo: bool | None = None
    expira_en: datetime | None = None


class ServiceAccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    nombre: str
    descripcion: str | None
    # The public half. Safe to show: on its own it opens nothing.
    credencial_id: str
    permisos: list[ServicePermission]
    client_id: uuid.UUID | None
    activo: bool
    expira_en: datetime | None
    secret_emitido_en: datetime
    ultimo_uso_en: datetime | None
    created_at: datetime
    updated_at: datetime


class ServiceAccountCreated(ServiceAccountRead):
    """The one response that carries the secret.

    Returned on creation and on rotation. It is not stored in a recoverable
    form, so a secret lost here is replaced, never looked up.
    """

    client_secret: str


class ServiceTokenRequest(BaseModel):
    """What another system sends to exchange its credential for a token."""

    client_id: str = Field(min_length=1, max_length=60)
    client_secret: str = Field(min_length=1, max_length=200)


class ServiceTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - the scheme name, not a secret
    expires_in: int = Field(description="Life of the token, in seconds")
    permisos: list[ServicePermission]
    # The client this token is confined to, or null for the whole platform.
    # Echoed back so the consumer can tell which one it got without decoding
    # the token itself.
    scope_client_id: uuid.UUID | None
