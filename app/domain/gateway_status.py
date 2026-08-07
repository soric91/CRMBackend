"""Whether a gateway is reachable, derived from when it was last heard from.

`estado` used to be a column somebody typed. Nothing updated it, so the panel
showed whatever was set at installation forever. Connectivity is not a fact you
record, it is a fact you observe: a device is online if it contacted us
recently, and offline the moment it stops.
"""

from datetime import UTC, datetime, timedelta

from app.domain.enums import GatewayStatus

# How long a gateway may stay silent before it counts as offline. The firmware
# has to check in at least this often — its polling interval is a different
# setting and can be much longer, so the heartbeat is what keeps this honest.
OFFLINE_AFTER = timedelta(minutes=5)


def derive_status(
    ultima_conexion: datetime | None, *, now: datetime | None = None
) -> GatewayStatus:
    """Return whether a gateway counts as reachable right now.

    A device that has never reported in is offline, not unknown: for the
    purpose of "what do I have to go fix", the two are the same.
    """
    if ultima_conexion is None:
        return GatewayStatus.OFFLINE

    moment = now or datetime.now(UTC)
    seen = ultima_conexion
    # Rows read back from SQLite come without a timezone; treat them as UTC
    # rather than crashing on the comparison.
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=UTC)

    return (
        GatewayStatus.ONLINE
        if moment - seen <= OFFLINE_AFTER
        else GatewayStatus.OFFLINE
    )
