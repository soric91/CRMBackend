"""Request and response models for authentication."""

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.security import MAX_PASSWORD_BYTES
from app.domain.enums import UserRole


class LoginRequest(BaseModel):
    """Credentials presented at ``/auth/login``."""

    email: EmailStr
    # The upper bound is bcrypt's: anything longer would be silently truncated,
    # making two different passwords equivalent.
    password: str = Field(min_length=8, max_length=MAX_PASSWORD_BYTES)


class RefreshRequest(BaseModel):
    """A refresh token being exchanged for a new pair."""

    refresh_token: str


class TokenPair(BaseModel):
    """What a successful login returns."""

    access_token: str
    refresh_token: str
    # The OAuth 2.0 scheme name, not a secret.
    token_type: str = "bearer"  # noqa: S105
    expires_in: int = Field(description="Access token lifetime in seconds")


class UserRead(BaseModel):
    """A user as exposed by the API. Never carries the password hash."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    role: UserRole
    client_id: uuid.UUID | None
    is_active: bool
