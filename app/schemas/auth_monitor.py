"""Request and response models for the monitoring web's authentication."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr

from app.schemas.auth import TokenPair


class MonitorTokenPair(TokenPair):
    """Everything the monitoring web needs to start, in one response."""

    # The company whose data the web is about to request.
    client_id: uuid.UUID
    # True: the web goes straight to the change-password screen, and the token
    # it holds reaches nothing else until that is done.
    must_change_password: bool


class MonitorIdentity(BaseModel):
    """Who the caller is, from the monitoring web's point of view."""

    user_id: uuid.UUID
    email: EmailStr
    client_id: uuid.UUID
    must_change_password: bool


class MonitorAccessRead(BaseModel):
    """State of a client's access. Never carries the password."""

    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    email: EmailStr
    is_active: bool
    must_change_password: bool
    created_at: datetime


class MonitorAccessCreated(MonitorAccessRead):
    """The only response that carries the password, and only this once.

    It is never stored in plaintext and never logged, so losing it means
    generating a new one through the reset endpoint.
    """

    temporary_password: str
