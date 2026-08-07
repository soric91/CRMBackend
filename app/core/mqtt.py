"""The CRM's MQTT bridge.

Two directions, both deliberately thin:

* **Outbound** — when a gateway has a configuration waiting, the CRM publishes a
  small notice on that device's topic. The notice carries no configuration: the
  gateway still fetches it over HTTP with its own credential. Keeping the
  payload out of MQTT means the contract lives in one place and the broker
  never becomes something that has to be trusted with it.
* **Inbound** — gateways publish their presence, which refreshes
  `ultima_conexion` without waiting for the next HTTP call.

Losing a message costs nothing. The gateway polls anyway, so the bridge makes
the system faster, never correct: if the broker is down, everything still works
a little later.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any, Protocol

import aiomqtt

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Delivered at least once, and kept by the broker so a gateway that was offline
# gets the notice the moment it reconnects — the case a plain push loses.
QOS_AT_LEAST_ONCE = 1

# How long to wait before reconnecting after the broker drops us.
RECONNECT_SECONDS = 5


class InboundMessage(Protocol):
    """The two things the bridge needs from a message.

    Structural rather than the concrete client type: the dispatch logic has no
    business depending on the transport library, and it makes the rules
    testable without a broker.
    """

    @property
    def topic(self) -> object: ...

    @property
    def payload(self) -> bytes | bytearray | str | None: ...


class MqttBridge:
    """Publishes notices to gateways and listens for what they report.

    A single connection owned by the application, started and stopped with it.
    Publishing never raises into a request: a notice that does not arrive is a
    lost optimisation, not a lost write.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: aiomqtt.Client | None = None
        self._listener: asyncio.Task[None] | None = None
        self._on_status: Callable[[uuid.UUID, dict[str, Any]], Awaitable[None]] | None
        self._on_status = None

    # --- topics ---------------------------------------------------------

    def config_topic(self, gateway_uuid: uuid.UUID) -> str:
        """Where the CRM tells one gateway that it has work to do."""
        return f"{self._settings.mqtt_topic_prefix}/{gateway_uuid}/config"

    @property
    def status_pattern(self) -> str:
        """Where every gateway reports in."""
        return f"{self._settings.mqtt_topic_prefix}/+/status"

    @staticmethod
    def _uuid_from_topic(topic: str) -> uuid.UUID | None:
        parts = topic.split("/")
        if len(parts) < 2:
            return None
        try:
            return uuid.UUID(parts[-2])
        except ValueError:
            return None

    # --- lifecycle ------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._settings.mqtt_enabled

    def on_status(
        self, handler: Callable[[uuid.UUID, dict[str, Any]], Awaitable[None]]
    ) -> None:
        """Register what to do when a gateway reports in."""
        self._on_status = handler

    async def start(self) -> None:
        """Connect and begin listening, without blocking application start.

        A broker that is unreachable at boot must not stop the API: the task
        keeps retrying while every HTTP route serves normally.
        """
        if not self.enabled:
            logger.info("mqtt bridge disabled")
            return
        self._listener = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._listener is not None:
            self._listener.cancel()
            with suppress(asyncio.CancelledError):
                await self._listener
            self._listener = None
        self._client = None

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[aiomqtt.Client]:
        async with aiomqtt.Client(
            hostname=self._settings.mqtt_host,
            port=self._settings.mqtt_port,
            username=self._settings.mqtt_user,
            password=(
                self._settings.mqtt_password.get_secret_value()
                if self._settings.mqtt_password
                else None
            ),
            identifier=f"crm-backend-{uuid.uuid4().hex[:8]}",
            tls_params=(aiomqtt.TLSParameters() if self._settings.mqtt_tls else None),
        ) as client:
            yield client

    async def _run(self) -> None:
        """Hold the connection open, reconnecting for as long as the app lives."""
        while True:
            try:
                async with self._connection() as client:
                    self._client = client
                    await client.subscribe(self.status_pattern, qos=QOS_AT_LEAST_ONCE)
                    logger.info(
                        "mqtt bridge connected",
                        extra={
                            "host": self._settings.mqtt_host,
                            "subscribed": self.status_pattern,
                        },
                    )
                    async for message in client.messages:
                        await self._handle(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._client = None
                logger.warning("mqtt bridge disconnected", extra={"error": str(exc)})
                await asyncio.sleep(RECONNECT_SECONDS)

    async def _handle(self, message: InboundMessage) -> None:
        """Dispatch one inbound message, never letting it kill the listener."""
        gateway_uuid = self._uuid_from_topic(str(message.topic))
        if gateway_uuid is None or self._on_status is None:
            return
        try:
            payload = json.loads(message.payload or b"{}")
            await self._on_status(
                gateway_uuid, payload if isinstance(payload, dict) else {}
            )
        except Exception as exc:
            logger.warning(
                "mqtt message ignored",
                extra={"topic": str(message.topic), "error": str(exc)},
            )

    # --- publishing -----------------------------------------------------

    async def notify_config_changed(
        self, gateway_uuid: uuid.UUID, config_version: str
    ) -> bool:
        """Tell one gateway that a configuration is waiting for it.

        Retained, so a device that was powered off receives the notice as soon
        as it reconnects. Returns whether it went out; callers ignore that,
        because the gateway's own polling covers a failure.
        """
        if not self.enabled or self._client is None:
            return False

        payload = json.dumps(
            {
                "event": "config_changed",
                "gateway_uuid": str(gateway_uuid),
                "config_version": config_version,
            }
        )
        try:
            await self._client.publish(
                self.config_topic(gateway_uuid),
                payload=payload,
                qos=QOS_AT_LEAST_ONCE,
                retain=True,
            )
        except Exception as exc:
            logger.warning(
                "mqtt notice not delivered",
                extra={"gateway_uuid": str(gateway_uuid), "error": str(exc)},
            )
            return False

        logger.info("mqtt notice published", extra={"gateway_uuid": str(gateway_uuid)})
        return True


# One bridge per process, owned by the application's lifespan.
_bridge: MqttBridge | None = None


def get_bridge() -> MqttBridge | None:
    """Return the running bridge, or None when MQTT is not configured."""
    return _bridge


def set_bridge(bridge: MqttBridge | None) -> None:
    global _bridge
    _bridge = bridge
