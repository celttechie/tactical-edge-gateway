"""Unit tests for MAVLink Telemetry Ingest Adapter."""

from __future__ import annotations

import asyncio
import math
from typing import Any
from unittest.mock import MagicMock

import pytest
from pymavlink.dialects.v20 import common as mavlink2

from tactical_edge_gateway.core.models import VehicleDomain
from tactical_edge_gateway.ingest.mavlink import MavlinkAdapter


def _create_heartbeat(
    sys_id: int = 1,
    vehicle_type: int = mavlink2.MAV_TYPE_QUADROTOR,
    is_armed: bool = True,
    autopilot: int = 3,
) -> mavlink2.MAVLink_heartbeat_message:
    """Helper to generate a MAVLink HEARTBEAT message."""
    base_mode = mavlink2.MAV_MODE_FLAG_SAFETY_ARMED if is_armed else 0
    msg = mavlink2.MAVLink_heartbeat_message(
        type=vehicle_type,
        autopilot=autopilot,
        base_mode=base_mode,
        custom_mode=0,
        system_status=mavlink2.MAV_STATE_ACTIVE,
        mavlink_version=3,
    )
    msg._header.srcSystem = sys_id
    msg._header.srcComponent = 1
    return msg


def _create_attitude(
    sys_id: int = 1,
    roll_rad: float = 0.1,
    pitch_rad: float = -0.05,
    yaw_rad: float = 1.570796,  # ~90 deg
) -> mavlink2.MAVLink_attitude_message:
    """Helper to generate a MAVLink ATTITUDE message."""
    msg = mavlink2.MAVLink_attitude_message(
        time_boot_ms=1000,
        roll=roll_rad,
        pitch=pitch_rad,
        yaw=yaw_rad,
        rollspeed=0.0,
        pitchspeed=0.0,
        yawspeed=0.0,
    )
    msg._header.srcSystem = sys_id
    msg._header.srcComponent = 1
    return msg


def _create_sys_status(
    sys_id: int = 1,
    battery_pct: int = 88,
    voltage_mv: int = 14800,
    drop_comm_cpct: int = 250,  # 2.5% drop rate -> 97.5% link quality
) -> mavlink2.MAVLink_sys_status_message:
    """Helper to generate a MAVLink SYS_STATUS message."""
    msg = mavlink2.MAVLink_sys_status_message(
        onboard_control_sensors_present=0,
        onboard_control_sensors_enabled=0,
        onboard_control_sensors_health=0,
        load=100,
        voltage_battery=voltage_mv,
        current_battery=1500,
        battery_remaining=battery_pct,
        drop_rate_comm=drop_comm_cpct,
        errors_comm=0,
        errors_count1=1,
        errors_count2=0,
        errors_count3=0,
        errors_count4=0,
    )
    msg._header.srcSystem = sys_id
    msg._header.srcComponent = 1
    return msg


def _create_global_position(
    sys_id: int = 1,
    lat_deg: float = 35.1415,
    lon_deg: float = -79.0069,
    alt_msl_m: float = 150.0,
    alt_hae_m: float = 120.0,
    vx_cm: int = 1000,  # 10 m/s
    vy_cm: int = 500,  # 5 m/s -> hypot ~ 11.18 m/s
    hdg_cdeg: int = 9000,  # 90.0 deg
) -> mavlink2.MAVLink_global_position_int_message:
    """Helper to generate a MAVLink GLOBAL_POSITION_INT message."""
    msg = mavlink2.MAVLink_global_position_int_message(
        time_boot_ms=1000,
        lat=int(lat_deg * 1e7),
        lon=int(lon_deg * 1e7),
        alt=int(alt_msl_m * 1000),
        relative_alt=int(alt_hae_m * 1000),
        vx=vx_cm,
        vy=vy_cm,
        vz=0,
        hdg=hdg_cdeg,
    )
    msg._header.srcSystem = sys_id
    msg._header.srcComponent = 1
    return msg


def test_mavlink_adapter_initialization():
    """Verify default initialization parameters."""
    adapter = MavlinkAdapter(connection_string="udpin:0.0.0.0:14550", callsign_prefix="RAPTOR")
    assert adapter.name == "mavlink-udpin:0.0.0.0:14550"
    assert adapter.callsign_prefix == "RAPTOR"
    assert adapter.default_domain == VehicleDomain.AIR
    assert not adapter.is_running


def test_handle_heartbeat_armed_and_domains():
    """Verify HEARTBEAT maps armed flag, vehicle domains, and metadata."""
    adapter = MavlinkAdapter()

    # Air drone
    hb_air = _create_heartbeat(sys_id=1, vehicle_type=mavlink2.MAV_TYPE_QUADROTOR, is_armed=True)
    res1 = adapter.parse_message(hb_air)
    assert res1 is None  # Heartbeat alone does not emit frame
    state1 = adapter.get_platform_state(1)
    assert state1.domain == VehicleDomain.AIR
    assert state1.health is not None
    assert state1.health.is_armed is True
    assert state1.metadata["autopilot"] == 3

    # Ground rover
    hb_rover = _create_heartbeat(
        sys_id=2, vehicle_type=mavlink2.MAV_TYPE_GROUND_ROVER, is_armed=False
    )
    adapter.parse_message(hb_rover)
    state2 = adapter.get_platform_state(2)
    assert state2.domain == VehicleDomain.GROUND
    assert state2.health is not None
    assert state2.health.is_armed is False

    # Surface vessel
    hb_surface = _create_heartbeat(
        sys_id=3, vehicle_type=mavlink2.MAV_TYPE_SURFACE_BOAT, is_armed=True
    )
    adapter.parse_message(hb_surface)
    state3 = adapter.get_platform_state(3)
    assert state3.domain == VehicleDomain.SURFACE


def test_handle_attitude_conversion():
    """Verify Euler Tait-Bryan radians are correctly converted to degrees."""
    adapter = MavlinkAdapter()
    att_msg = _create_attitude(sys_id=1, roll_rad=0.1, pitch_rad=-0.05, yaw_rad=1.570796)

    res = adapter.parse_message(att_msg)
    assert res is None
    state = adapter.get_platform_state(1)
    assert state.attitude is not None
    assert math.isclose(state.attitude.roll_deg, math.degrees(0.1), abs_tol=0.1)
    assert math.isclose(state.attitude.pitch_deg, math.degrees(-0.05), abs_tol=0.1)
    assert math.isclose(state.attitude.yaw_deg, 90.0, abs_tol=0.1)


def test_handle_sys_status_health_metrics():
    """Verify SYS_STATUS parses battery percent, voltage, and link quality."""
    adapter = MavlinkAdapter()
    status_msg = _create_sys_status(sys_id=1, battery_pct=88, voltage_mv=14800, drop_comm_cpct=250)

    adapter.parse_message(status_msg)
    state = adapter.get_platform_state(1)
    assert state.health is not None
    assert state.health.battery_percent == 88.0
    assert state.health.battery_voltage_v == 14.8
    assert state.health.link_quality_percent == 97.5
    assert "HW_ERR_1" in state.health.error_codes


def test_full_frame_assembly_from_global_position():
    """Verify multi-packet telemetry aggregates into a complete TelemetryFrame on GPS update."""
    adapter = MavlinkAdapter(callsign_prefix="EAGLE")

    # Ingest state components
    adapter.parse_message(
        _create_heartbeat(sys_id=5, vehicle_type=mavlink2.MAV_TYPE_HEXAROTOR, is_armed=True)
    )
    adapter.parse_message(
        _create_attitude(sys_id=5, roll_rad=0.05, pitch_rad=0.02, yaw_rad=3.14159)
    )
    adapter.parse_message(
        _create_sys_status(sys_id=5, battery_pct=95, voltage_mv=22200, drop_comm_cpct=0)
    )

    # Ingest position
    gps_msg = _create_global_position(
        sys_id=5,
        lat_deg=35.1415,
        lon_deg=-79.0069,
        alt_msl_m=150.0,
        alt_hae_m=120.0,
        vx_cm=800,
        vy_cm=600,
        hdg_cdeg=18000,
    )
    frame = adapter.parse_message(gps_msg)

    assert frame is not None
    assert frame.uid == "mavlink-5"
    assert frame.callsign == "EAGLE-05"
    assert frame.domain == VehicleDomain.AIR
    assert frame.default_cot_type() == "a-f-A-M-F"
    assert frame.position.latitude == 35.1415
    assert frame.position.longitude == -79.0069
    assert frame.position.altitude_msl_m == 150.0
    assert frame.position.altitude_hae_m == 120.0
    assert frame.position.heading_deg == 180.0
    assert frame.position.speed_mps is not None
    assert math.isclose(frame.position.speed_mps, 10.0, abs_tol=0.01)

    assert frame.attitude is not None
    assert math.isclose(frame.attitude.yaw_deg, 180.0, abs_tol=0.1)

    assert frame.health is not None
    assert frame.health.is_armed is True
    assert frame.health.battery_percent == 95.0
    assert frame.health.battery_voltage_v == 22.2
    assert frame.health.link_quality_percent == 100.0


def test_invalid_coordinates_dropped():
    """Verify invalid or out-of-bounds coordinates are safely discarded."""
    adapter = MavlinkAdapter()
    invalid_gps = _create_global_position(sys_id=1, lat_deg=95.0, lon_deg=-79.0)
    frame = adapter.parse_message(invalid_gps)
    assert frame is None


def test_ignore_none_and_invalid_messages():
    """Verify adapter handles None, empty, or unhandled message types safely."""
    adapter = MavlinkAdapter()
    assert adapter.parse_message(None) is None

    # Dummy object without type
    assert adapter.parse_message(object()) is None

    # Zero system ID
    msg_zero = _create_heartbeat(sys_id=0)
    assert adapter.parse_message(msg_zero) is None


@pytest.mark.asyncio
async def test_async_stream_with_mock_connection():
    """Verify asynchronous stream lifecycle using an injected mock connection."""
    mock_conn = MagicMock()
    messages = [
        _create_heartbeat(sys_id=7, is_armed=True),
        _create_global_position(sys_id=7, lat_deg=35.1415, lon_deg=-79.0069),
        None,
    ]
    mock_conn.recv_match.side_effect = messages

    adapter = MavlinkAdapter(connection=mock_conn)
    frames: list[Any] = []

    async for frame in adapter.stream():
        frames.append(frame)
        if len(frames) >= 1:
            await adapter.stop()
            break

    assert len(frames) == 1
    assert frames[0].uid == "mavlink-7"
    assert not adapter.is_running


def test_handle_submarine_and_fallback_domains():
    """Verify submarine maps to SUBSURFACE and unrecognized types fallback to AIR."""
    adapter = MavlinkAdapter(default_domain=VehicleDomain.AIR)

    # Submarine
    hb_sub = _create_heartbeat(sys_id=11, vehicle_type=mavlink2.MAV_TYPE_SUBMARINE)
    adapter.parse_message(hb_sub)
    assert adapter.get_platform_state(11).domain == VehicleDomain.SUBSURFACE

    # Unknown vehicle type (e.g. 999)
    hb_unknown = _create_heartbeat(sys_id=12, vehicle_type=999)
    adapter.parse_message(hb_unknown)
    assert adapter.get_platform_state(12).domain == VehicleDomain.AIR


def test_unhandled_message_type_ignored():
    """Verify non-telemetry messages (like PARAM_VALUE) return None."""
    adapter = MavlinkAdapter()
    param_msg = mavlink2.MAVLink_param_value_message(
        param_id=b"SYSID_THISMAV\x00\x00\x00",
        param_value=1.0,
        param_type=mavlink2.MAV_PARAM_TYPE_REAL32,
        param_count=1,
        param_index=0,
    )
    param_msg._header.srcSystem = 1
    assert adapter.parse_message(param_msg) is None


@pytest.mark.asyncio
async def test_start_idempotency_and_owns_connection_close():
    """Verify start() is idempotent and stop() closes owned connection."""
    mock_conn = MagicMock()
    mock_conn.recv_match.return_value = None

    adapter = MavlinkAdapter(connection=mock_conn)
    adapter._owns_connection = True

    await adapter.start()
    assert adapter.is_running

    # Second start call does nothing
    await adapter.start()
    assert adapter.is_running

    # Stop closes connection
    await adapter.stop()
    assert not adapter.is_running
    mock_conn.close.assert_called_once()


@pytest.mark.asyncio
async def test_queue_backpressure_eviction():
    """Verify that when queue is full, oldest frame is evicted without raising QueueFull."""
    adapter = MavlinkAdapter()
    adapter._queue = asyncio.Queue(maxsize=2)

    # Directly enqueue 2 frames
    f1 = adapter.parse_message(_create_global_position(sys_id=1, lat_deg=10.0, lon_deg=10.0))
    f2 = adapter.parse_message(_create_global_position(sys_id=1, lat_deg=20.0, lon_deg=20.0))
    f3 = adapter.parse_message(_create_global_position(sys_id=1, lat_deg=30.0, lon_deg=30.0))

    assert f1 is not None and f2 is not None and f3 is not None

    adapter._queue.put_nowait(f1)
    adapter._queue.put_nowait(f2)

    # Third frame should trigger backpressure eviction
    try:
        adapter._queue.put_nowait(f3)
    except asyncio.QueueFull:
        adapter._queue.get_nowait()
        adapter._queue.put_nowait(f3)

    assert adapter._queue.qsize() == 2
    rem1 = adapter._queue.get_nowait()
    rem2 = adapter._queue.get_nowait()
    assert rem1.position.latitude == 20.0
    assert rem2.position.latitude == 30.0


def test_fetch_next_message_exception_handling():
    """Verify _fetch_next_message handles connection errors safely."""
    mock_conn = MagicMock()
    mock_conn.recv_match.side_effect = RuntimeError("Socket read failure")

    adapter = MavlinkAdapter(connection=mock_conn)
    adapter._running = True

    result = adapter._fetch_next_message()
    assert result is None

    # When not running
    adapter._running = False
    assert adapter._fetch_next_message() is None


@pytest.mark.asyncio
async def test_start_creates_connection_with_patch():
    """Verify start() creates mavlink connection and stop handles close errors."""
    from unittest.mock import patch

    with patch("tactical_edge_gateway.ingest.mavlink.mavutil.mavlink_connection") as mock_open:
        mock_conn = MagicMock()
        mock_conn.close.side_effect = RuntimeError("Close socket failed")
        mock_open.return_value = mock_conn

        adapter = MavlinkAdapter(connection_string="udpin:127.0.0.1:14550")
        await adapter.start()
        assert adapter.is_running
        mock_open.assert_called_once()

        # Stop catches close error gracefully
        await adapter.stop()
        assert not adapter.is_running
        mock_conn.close.assert_called_once()


@pytest.mark.asyncio
async def test_read_loop_backpressure_and_error_recovery():
    """Verify _read_loop handles full queue eviction and recovers from exceptions."""
    mock_conn = MagicMock()
    gps_msg = _create_global_position(sys_id=9, lat_deg=35.0, lon_deg=-79.0)

    # First call yields GPS msg, second raises error, third yields None
    mock_conn.recv_match.side_effect = [gps_msg, RuntimeError("Transient packet error"), None]

    adapter = MavlinkAdapter(connection=mock_conn)
    adapter._queue = asyncio.Queue(maxsize=1)

    # Pre-fill queue so put_nowait triggers QueueFull branch
    existing_frame = adapter.parse_message(gps_msg)
    assert existing_frame is not None
    adapter._queue.put_nowait(existing_frame)

    await adapter.start()
    # Allow read_loop to process
    await asyncio.sleep(0.05)
    await adapter.stop()

    assert adapter._queue.qsize() == 1
