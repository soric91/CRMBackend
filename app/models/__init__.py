"""SQLAlchemy models.

Every model is imported here so Alembic autogenerate sees the full metadata.
"""

from app.models.alert_config import AlertConfig
from app.models.client import Client
from app.models.equipment import Equipment
from app.models.gateway import Gateway
from app.models.service_account import ServiceAccount
from app.models.site import Site
from app.models.tariff import Tariff
from app.models.user import User
from app.models.variable import Variable

__all__ = [
    "AlertConfig",
    "Client",
    "Equipment",
    "Gateway",
    "ServiceAccount",
    "Site",
    "Tariff",
    "User",
    "Variable",
]
