"""The MQTT bridge, exercised without a broker.

Everything here runs against a stand-in client: the point is the bridge's own
rules — topic shapes, what happens when the broker is absent, and the promise
that publishing never raises into a request.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.core.config import Settings
from app.core.mqtt import MqttBridge, get_bridge, set_bridge

GATEWAY = uuid.UUID("4f50cc89-8030-4654-a2cc-4a1ec34ab37a")


def _settings(base: Settings, **overrides: object) -> Settings:
    return base.model_copy(update={"mqtt_enabled": True, **overrides})


class _RecordingClient:
    """Stands in for the broker connection, remembering what was published."""

    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def publish(self, topic: str, payload: str, qos: int, retain: bool) -> None:
        self.published.append(
            {"topic": topic, "payload": payload, "qos": qos, "retain": retain}
        )


class _FailingClient:
    async def publish(self, *_: object, **__: object) -> None:
        raise ConnectionError("broker went away")


class TestTopics:
    def test_each_gateway_gets_its_own(self, settings: Settings) -> None:
        bridge = MqttBridge(_settings(settings))

        assert bridge.config_topic(GATEWAY) == f"crm/gateways/{GATEWAY}/config"

    def test_the_prefix_is_configurable(self, settings: Settings) -> None:
        bridge = MqttBridge(_settings(settings, mqtt_topic_prefix="otro/arbol"))

        assert bridge.config_topic(GATEWAY).startswith("otro/arbol/")

    def test_presence_is_listened_for_across_every_gateway(
        self, settings: Settings
    ) -> None:
        bridge = MqttBridge(_settings(settings))

        assert bridge.status_pattern == "crm/gateways/+/status"

    def test_the_prefix_is_separate_from_the_telemetry_tree(
        self, settings: Settings
    ) -> None:
        """Readings already flow on their own topics; this must not collide."""
        bridge = MqttBridge(_settings(settings))

        assert not bridge.status_pattern.startswith("gatewayems/")


class TestExtractingTheGateway:
    def test_the_uuid_is_read_from_the_topic(self, settings: Settings) -> None:
        bridge = MqttBridge(_settings(settings))

        assert bridge._uuid_from_topic(f"crm/gateways/{GATEWAY}/status") == GATEWAY

    @pytest.mark.parametrize(
        "topic", ["crm/gateways/no-es-uuid/status", "corto", "", "a/b"]
    )
    def test_nonsense_is_ignored(self, settings: Settings, topic: str) -> None:
        bridge = MqttBridge(_settings(settings))

        assert bridge._uuid_from_topic(topic) is None


class TestPublishing:
    async def test_the_notice_carries_no_configuration(
        self, settings: Settings
    ) -> None:
        """Only a hint: the payload stays on the channel that owns it."""
        bridge = MqttBridge(_settings(settings))
        client = _RecordingClient()
        bridge._client = client  # type: ignore[assignment]

        await bridge.notify_config_changed(GATEWAY, "abc123")

        payload = json.loads(client.published[0]["payload"])
        assert payload == {
            "event": "config_changed",
            "gateway_uuid": str(GATEWAY),
            "config_version": "abc123",
        }
        assert "devices" not in payload

    async def test_it_is_retained_so_a_sleeping_device_still_learns(
        self, settings: Settings
    ) -> None:
        bridge = MqttBridge(_settings(settings))
        client = _RecordingClient()
        bridge._client = client  # type: ignore[assignment]

        await bridge.notify_config_changed(GATEWAY, "abc123")

        assert client.published[0]["retain"] is True
        assert client.published[0]["qos"] == 1

    async def test_it_goes_to_that_gateway_alone(self, settings: Settings) -> None:
        bridge = MqttBridge(_settings(settings))
        client = _RecordingClient()
        bridge._client = client  # type: ignore[assignment]

        await bridge.notify_config_changed(GATEWAY, "abc123")

        assert client.published[0]["topic"] == f"crm/gateways/{GATEWAY}/config"

    async def test_a_broker_that_refuses_does_not_raise(
        self, settings: Settings
    ) -> None:
        """A lost notice is a lost optimisation; the poll still covers it."""
        bridge = MqttBridge(_settings(settings))
        bridge._client = _FailingClient()  # type: ignore[assignment]

        assert await bridge.notify_config_changed(GATEWAY, "abc123") is False

    async def test_nothing_is_published_while_disconnected(
        self, settings: Settings
    ) -> None:
        bridge = MqttBridge(_settings(settings))

        assert await bridge.notify_config_changed(GATEWAY, "abc123") is False


class TestTheFirmwareNotice:
    async def test_it_goes_to_its_own_topic(self, settings: Settings) -> None:
        """Separado del de configuración: un equipo puede querer escuchar uno
        y no el otro, y mezclarlos obligaría a leer el cuerpo para saber cuál
        llegó."""
        bridge = MqttBridge(_settings(settings))
        client = _RecordingClient()
        bridge._client = client  # type: ignore[assignment]

        await bridge.notify_firmware_update(
            GATEWAY, "v1.4.0", datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
        )

        assert client.published[0]["topic"] == f"crm/gateways/{GATEWAY}/firmware"

    async def test_it_carries_no_package_and_no_checksum(
        self, settings: Settings
    ) -> None:
        """El equipo igual pregunta por HTTP con su credencial: el broker
        nunca decide qué software se instala."""
        bridge = MqttBridge(_settings(settings))
        client = _RecordingClient()
        bridge._client = client  # type: ignore[assignment]

        await bridge.notify_firmware_update(
            GATEWAY, "v1.4.0", datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
        )

        payload = json.loads(client.published[0]["payload"])
        assert payload == {
            "event": "firmware_update",
            "gateway_uuid": str(GATEWAY),
            "version": "v1.4.0",
            "aplicar_desde": "2026-08-21T08:00:00+00:00",
        }
        assert "sha256" not in payload
        assert "url" not in payload

    async def test_it_is_retained_for_a_device_that_was_off(
        self, settings: Settings
    ) -> None:
        bridge = MqttBridge(_settings(settings))
        client = _RecordingClient()
        bridge._client = client  # type: ignore[assignment]

        await bridge.notify_firmware_update(
            GATEWAY, "v1.4.0", datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
        )

        assert client.published[0]["retain"] is True
        assert client.published[0]["qos"] == 1

    async def test_a_broker_that_refuses_does_not_raise(
        self, settings: Settings
    ) -> None:
        bridge = MqttBridge(_settings(settings))
        bridge._client = _FailingClient()  # type: ignore[assignment]

        result = await bridge.notify_firmware_update(
            GATEWAY, "v1.4.0", datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
        )

        assert result is False

    async def test_nothing_is_published_while_disconnected(
        self, settings: Settings
    ) -> None:
        bridge = MqttBridge(_settings(settings))

        result = await bridge.notify_firmware_update(
            GATEWAY, "v1.4.0", datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
        )

        assert result is False


class TestDisabled:
    async def test_it_publishes_nothing(self, settings: Settings) -> None:
        """The API has to run with no broker at all."""
        bridge = MqttBridge(settings.model_copy(update={"mqtt_enabled": False}))
        client = _RecordingClient()
        bridge._client = client  # type: ignore[assignment]

        result = await bridge.notify_config_changed(GATEWAY, "abc123")

        assert result is False
        assert client.published == []

    async def test_starting_it_connects_to_nothing(self, settings: Settings) -> None:
        bridge = MqttBridge(settings.model_copy(update={"mqtt_enabled": False}))

        await bridge.start()
        await bridge.stop()

        assert bridge.enabled is False


class TestTheProcessWideBridge:
    def test_it_is_absent_until_the_application_sets_it(self) -> None:
        set_bridge(None)

        assert get_bridge() is None

    def test_it_can_be_set_and_cleared(self, settings: Settings) -> None:
        bridge = MqttBridge(_settings(settings))
        set_bridge(bridge)

        assert get_bridge() is bridge

        set_bridge(None)
        assert get_bridge() is None


class _Message:
    """Stands in for an inbound broker message."""

    def __init__(self, topic: str, payload: bytes | None) -> None:
        self.topic = topic
        self.payload = payload


class TestHandlingWhatGatewaysSend:
    async def test_a_report_reaches_the_handler(self, settings: Settings) -> None:
        bridge = MqttBridge(_settings(settings))
        seen: list[tuple[uuid.UUID, dict[str, Any]]] = []

        async def _record(gateway: uuid.UUID, payload: dict[str, Any]) -> None:
            seen.append((gateway, payload))

        bridge.on_status(_record)
        await bridge._handle(
            _Message(f"crm/gateways/{GATEWAY}/status", b'{"firmware_version": "2.4.1"}')
        )

        assert seen == [(GATEWAY, {"firmware_version": "2.4.1"})]

    async def test_an_empty_payload_is_still_a_report(self, settings: Settings) -> None:
        """The contact itself is the point; the body is optional."""
        bridge = MqttBridge(_settings(settings))
        seen: list[uuid.UUID] = []

        async def _record(gateway: uuid.UUID, _: dict[str, Any]) -> None:
            seen.append(gateway)

        bridge.on_status(_record)
        await bridge._handle(_Message(f"crm/gateways/{GATEWAY}/status", None))

        assert seen == [GATEWAY]

    async def test_a_topic_without_a_uuid_is_dropped(self, settings: Settings) -> None:
        bridge = MqttBridge(_settings(settings))
        seen: list[uuid.UUID] = []

        async def _record(gateway: uuid.UUID, _: dict[str, Any]) -> None:
            seen.append(gateway)

        bridge.on_status(_record)
        await bridge._handle(_Message("crm/gateways/basura/status", b"{}"))

        assert seen == []

    async def test_malformed_json_does_not_kill_the_listener(
        self, settings: Settings
    ) -> None:
        """One bad message must not take the bridge down with it."""
        bridge = MqttBridge(_settings(settings))
        seen: list[uuid.UUID] = []

        async def _record(gateway: uuid.UUID, _: dict[str, Any]) -> None:
            seen.append(gateway)

        bridge.on_status(_record)
        await bridge._handle(_Message(f"crm/gateways/{GATEWAY}/status", b"no-json"))

        assert seen == []

    async def test_a_handler_that_raises_is_contained(self, settings: Settings) -> None:
        bridge = MqttBridge(_settings(settings))

        async def _explode(_: uuid.UUID, __: dict[str, Any]) -> None:
            raise RuntimeError("something broke downstream")

        bridge.on_status(_explode)

        # Must not propagate: the listener has to survive.
        await bridge._handle(_Message(f"crm/gateways/{GATEWAY}/status", b"{}"))

    async def test_a_json_scalar_is_treated_as_an_empty_body(
        self, settings: Settings
    ) -> None:
        bridge = MqttBridge(_settings(settings))
        seen: list[dict[str, Any]] = []

        async def _record(_: uuid.UUID, payload: dict[str, Any]) -> None:
            seen.append(payload)

        bridge.on_status(_record)
        await bridge._handle(_Message(f"crm/gateways/{GATEWAY}/status", b'"hola"'))

        assert seen == [{}]

    async def test_nothing_happens_without_a_handler(self, settings: Settings) -> None:
        bridge = MqttBridge(_settings(settings))

        await bridge._handle(_Message(f"crm/gateways/{GATEWAY}/status", b"{}"))
