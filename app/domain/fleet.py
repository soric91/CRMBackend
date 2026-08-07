"""How deep a fleet document goes.

The whole tree — client, site, gateway, device, register — is one answer to
one question, but not every caller wants all of it. A panel drawing a meter
selector stops at the devices; a consumer that has to interpret readings needs
the registers. Naming the levels here keeps the repository, the service and
the API talking about the same four steps.
"""

from enum import StrEnum


class FleetLevel(StrEnum):
    """The last layer a fleet document includes."""

    SITIOS = "sitios"
    GATEWAYS = "gateways"
    EQUIPOS = "equipos"
    VARIABLES = "variables"


# Depth order. The chain is fixed by the schema itself: there is no gateway
# without a site, and no register without a device.
LEVEL_ORDER: tuple[FleetLevel, ...] = (
    FleetLevel.SITIOS,
    FleetLevel.GATEWAYS,
    FleetLevel.EQUIPOS,
    FleetLevel.VARIABLES,
)


def reaches(level: FleetLevel, wanted: FleetLevel) -> bool:
    """Whether a document built to ``level`` contains the ``wanted`` layer."""
    return LEVEL_ORDER.index(level) >= LEVEL_ORDER.index(wanted)
