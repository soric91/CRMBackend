"""What the CRM does with what gateways report over MQTT.

Presence arriving this way is a **hint**, not proof: every gateway shares one
broker credential today, so nothing stops one from publishing on another's
topic. The consequence is bounded — a device could be shown as reachable when
it is not — and the authenticated signal remains `POST /gateway/{uuid}/heartbeat`.
Per-device broker credentials with topic ACLs would close the gap.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, select, update

from app.core.database import get_session_factory
from app.core.logging import get_logger
from app.models import Gateway

logger = get_logger(__name__)


async def record_gateway_presence(
    gateway_uuid: uuid.UUID, payload: dict[str, Any]
) -> None:
    """Refresh what the CRM knows about a device that just spoke up.

    Runs in the listener task, so it opens its own session: it is outside any
    request and must not borrow one.
    """
    changes: dict[str, Any] = {"ultima_conexion": datetime.now(UTC)}
    for field in ("firmware_version", "ip_actual"):
        value = payload.get(field)
        if isinstance(value, str) and value:
            changes[field] = value

    async with get_session_factory()() as session:
        result = await session.execute(
            update(Gateway).where(Gateway.uuid == gateway_uuid).values(**changes)
        )
        if cast("CursorResult[Any]", result).rowcount == 0:
            # An unknown uuid on the bus is worth noticing: either a device was
            # deleted from the CRM and never reconfigured, or something is
            # publishing where it should not.
            logger.warning(
                "presence from an unknown gateway",
                extra={"gateway_uuid": str(gateway_uuid)},
            )
            return
        await session.commit()

    logger.info("gateway presence recorded", extra={"gateway_uuid": str(gateway_uuid)})


async def gateway_uuid_for(gateway_id: uuid.UUID) -> uuid.UUID | None:
    """Return the uuid the firmware knows itself by, given the CRM's id."""
    async with get_session_factory()() as session:
        result = await session.execute(
            select(Gateway.uuid).where(Gateway.id == gateway_id)
        )
        return result.scalar_one_or_none()
