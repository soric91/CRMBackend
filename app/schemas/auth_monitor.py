"""Request and response models for the monitoring web's authentication."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr

from app.domain.enums import UserRole
from app.schemas.auth import TokenPair


class MonitorTokenPair(TokenPair):
    """Everything the monitoring web needs to start, in one response."""

    # The company whose data the web is about to request.
    #
    # `None` para un administrador que todavía no eligió a quién mirar: su
    # cuenta no pertenece a ninguna empresa. Con ese token el panel muestra la
    # pantalla de proyectos y nada más — no hay datos que pedir sin empresa.
    client_id: uuid.UUID | None
    # Qué es quien entró. El panel decide con esto si arranca en el tablero o
    # en la lista de proyectos, en vez de deducirlo de que falte `client_id`.
    role: UserRole
    # True: the web goes straight to the change-password screen, and the token
    # it holds reaches nothing else until that is done.
    must_change_password: bool


class MonitorIdentity(BaseModel):
    """Who the caller is, from the monitoring web's point of view."""

    user_id: uuid.UUID
    email: EmailStr
    # La empresa que este token abre: la propia, la que un administrador está
    # mirando, o `None` mientras todavía no eligió ninguna.
    client_id: uuid.UUID | None
    role: UserRole
    # El panel muestra un aviso permanente mientras esto sea verdadero. Sin
    # él, un administrador lee los números de una empresa creyendo que son de
    # otra y decide sobre eso.
    impersonated: bool
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
