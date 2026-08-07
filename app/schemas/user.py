"""Request models for managing user accounts.

``UserRead`` lives in ``app.schemas.auth``: it is the same shape the login flow
already returns, and duplicating it would let the two drift apart.
"""

import uuid

from pydantic import BaseModel, EmailStr, Field

from app.core.security import MAX_PASSWORD_BYTES
from app.domain.enums import UserRole

MIN_PASSWORD_LENGTH = 8


class UserCreate(BaseModel):
    """Payload for creating an account."""

    email: EmailStr
    # Upper bound is bcrypt's: anything longer would be silently truncated.
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_BYTES)
    role: UserRole
    # Mandatory for the `cliente` role, forbidden for the others. The service
    # enforces it, so the rule holds no matter which endpoint is used.
    client_id: uuid.UUID | None = None


class UserUpdate(BaseModel):
    """Partial update. The password is changed through its own endpoint."""

    role: UserRole | None = None
    client_id: uuid.UUID | None = None
    is_active: bool | None = None


class PasswordSet(BaseModel):
    """An administrator setting someone else's password."""

    new_password: str = Field(
        min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_BYTES
    )


class PasswordChange(BaseModel):
    """A user changing their own password."""

    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_BYTES)
    new_password: str = Field(
        min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_BYTES
    )
