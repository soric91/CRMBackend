"""Reachability, derived from the last time a gateway said anything."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.enums import GatewayStatus
from app.domain.gateway_status import OFFLINE_AFTER, derive_status

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


class TestDerivation:
    def test_a_device_that_never_reported_is_offline(self) -> None:
        """Not "unknown": for deciding what to go fix, they are the same."""
        assert derive_status(None, now=NOW) is GatewayStatus.OFFLINE

    def test_a_recent_contact_is_online(self) -> None:
        assert (
            derive_status(NOW - timedelta(seconds=30), now=NOW) is GatewayStatus.ONLINE
        )

    def test_silence_past_the_threshold_is_offline(self) -> None:
        assert (
            derive_status(NOW - OFFLINE_AFTER - timedelta(seconds=1), now=NOW)
            is GatewayStatus.OFFLINE
        )

    def test_exactly_at_the_threshold_still_counts_as_online(self) -> None:
        assert derive_status(NOW - OFFLINE_AFTER, now=NOW) is GatewayStatus.ONLINE

    def test_a_naive_timestamp_is_read_as_utc(self) -> None:
        """SQLite hands rows back without a timezone; comparing must not crash."""
        naive = (NOW - timedelta(seconds=30)).replace(tzinfo=None)

        assert derive_status(naive, now=NOW) is GatewayStatus.ONLINE

    @pytest.mark.parametrize("seconds", [0, 1, 60, 299])
    def test_anything_inside_the_window_is_online(self, seconds: int) -> None:
        assert (
            derive_status(NOW - timedelta(seconds=seconds), now=NOW)
            is GatewayStatus.ONLINE
        )

    def test_a_future_timestamp_does_not_read_as_offline(self) -> None:
        """Clock skew on the device must not make it disappear from the panel."""
        assert (
            derive_status(NOW + timedelta(minutes=1), now=NOW) is GatewayStatus.ONLINE
        )
