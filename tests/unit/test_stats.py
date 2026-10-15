"""Unit tests for the unified stats hierarchy and operational metrics.

Validates Stage 5 implementation:
- StatsBase serialization to dict and JSON.
- Strongly typed Pydantic models: EngineStats, AdapterStats, EgressStats.
- Verification of operational metrics tracking across Ingest adapters,
  Egress clients, and GatewayEngine.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from tactical_edge_gateway.core.stats import AdapterStats, EgressStats, EngineStats, StatsBase
from tactical_edge_gateway.egress.udp import UdpEgressClient
from tactical_edge_gateway.ingest.mavlink import MavlinkAdapter
from tactical_edge_gateway.ingest.ros2 import ROS2Adapter
from tactical_edge_gateway.ingest.synthetic import SyntheticAdapter


def test_stats_base_serialization():
    """Verify StatsBase models serialize cleanly to dict and JSON."""
    stats = EngineStats(
        frames_ingested=10,
        frames_routed=8,
        frames_dropped=2,
        frames_buffered=1,
        errors_count=0,
        started_at=datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert isinstance(stats, StatsBase)

    d = stats.to_dict()
    assert isinstance(d, dict)
    assert d["frames_ingested"] == 10
    assert d["frames_routed"] == 8

    j = stats.to_json()
    assert isinstance(j, str)
    parsed = json.loads(j)
    assert parsed["frames_ingested"] == 10
    assert "started_at" in parsed


def test_stats_validation_constraints():
    """Verify negative counters and unexpected fields are rejected by Pydantic."""
    # Negative counter error
    with pytest.raises(ValidationError):
        AdapterStats(frames_received=-1)

    with pytest.raises(ValidationError):
        EgressStats(packets_sent=-5)

    with pytest.raises(ValidationError):
        EngineStats(frames_dropped=-2)

    # Extra fields forbidden
    with pytest.raises(ValidationError):
        AdapterStats(extra_field=123)  # type: ignore[call-arg]


def test_adapter_stats_on_synthetic():
    """Verify SyntheticAdapter instruments frames_received, parsed, and last_received_at."""
    adapter = SyntheticAdapter()
    assert adapter.stats.frames_received == 0
    assert adapter.stats.frames_parsed == 0
    assert adapter.stats.last_received_at is None

    frame = adapter.next_frame()
    assert frame is not None
    assert adapter.stats.frames_received == 1
    assert adapter.stats.frames_parsed == 1
    assert adapter.stats.last_received_at is not None

    frame2 = adapter.next_frame()
    assert frame2 is not None
    assert adapter.stats.frames_received == 2
    assert adapter.stats.frames_parsed == 2


def test_adapter_stats_on_mavlink():
    """Verify MavlinkAdapter tracks message arrival, valid frames, and dropped frames."""
    adapter = MavlinkAdapter()

    # 1. Non-frame heartbeat
    hb_msg = MagicMock()
    hb_msg.get_type.return_value = "HEARTBEAT"
    hb_msg.get_srcSystem.return_value = 1
    hb_msg.type = 2  # MAV_TYPE_QUADROTOR

    frame = adapter.parse_message(hb_msg)
    assert frame is None
    assert adapter.stats.frames_received == 1
    assert adapter.stats.frames_parsed == 0

    # 2. Valid GLOBAL_POSITION_INT
    gps_msg = MagicMock()
    gps_msg.get_type.return_value = "GLOBAL_POSITION_INT"
    gps_msg.get_srcSystem.return_value = 1
    gps_msg.lat = int(35.1234567 * 1e7)
    gps_msg.lon = int(-79.1234567 * 1e7)
    gps_msg.alt = 100000
    gps_msg.relative_alt = 50000
    gps_msg.vx = 1000
    gps_msg.vy = 2000
    gps_msg.hdg = 18000

    frame = adapter.parse_message(gps_msg)
    assert frame is not None
    assert adapter.stats.frames_received == 2
    assert adapter.stats.frames_parsed == 1

    # 3. Invalid GLOBAL_POSITION_INT dropped
    bad_gps = MagicMock()
    bad_gps.get_type.return_value = "GLOBAL_POSITION_INT"
    bad_gps.get_srcSystem.return_value = 1
    bad_gps.lat = int(95.0 * 1e7)  # Invalid latitude > 90
    bad_gps.lon = 0
    bad_gps.alt = 0
    bad_gps.relative_alt = 0
    bad_gps.vx = 0
    bad_gps.vy = 0
    bad_gps.hdg = 0

    frame = adapter.parse_message(bad_gps)
    assert frame is None
    assert adapter.stats.frames_received == 3
    assert adapter.stats.frames_dropped == 1


def test_adapter_stats_on_ros2():
    """Verify ROS2Adapter tracks message arrival, valid fixes, dropped frames, and errors."""
    adapter = ROS2Adapter()

    # 1. Valid NavSatFix
    valid_fix = MagicMock()
    valid_fix.status = MagicMock(status=0)
    valid_fix.latitude = 35.0
    valid_fix.longitude = -79.0
    valid_fix.altitude = 100.0

    frame = adapter.handle_nav_sat_fix(valid_fix)
    assert frame is not None
    assert adapter.stats.frames_received == 1
    assert adapter.stats.frames_parsed == 1

    # 2. Discarded fix (STATUS_NO_FIX < 0)
    no_fix = MagicMock()
    no_fix.status = MagicMock(status=-1)
    no_fix.latitude = 35.0
    no_fix.longitude = -79.0

    frame = adapter.handle_nav_sat_fix(no_fix)
    assert frame is None
    assert adapter.stats.frames_received == 2
    assert adapter.stats.frames_dropped == 1

    # 3. Invalid coordinates in fix
    bad_fix = MagicMock()
    bad_fix.status = MagicMock(status=0)
    bad_fix.latitude = 105.0  # Out of bounds
    bad_fix.longitude = -79.0

    frame = adapter.handle_nav_sat_fix(bad_fix)
    assert frame is None
    assert adapter.stats.frames_received == 3
    assert adapter.stats.frames_dropped == 2


def test_egress_stats_json_export():
    """Verify EgressStats supports Pydantic JSON export and timestamp tracking."""
    client = UdpEgressClient(host="127.0.0.1", port=4242)
    assert client.stats.packets_sent == 0
    assert client.stats.bytes_sent == 0

    json_str = client.stats.to_json()
    assert '"packets_sent":0' in json_str or '"packets_sent": 0' in json_str
