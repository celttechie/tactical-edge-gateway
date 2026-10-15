"""Live UDP Integration tests for MAVLink Telemetry Ingest Adapter.

Validates end-to-end packet transmission, network loopback reception,
binary MAVLink 2.0 parsing, multi-vehicle state tracking, and error resilience
over real OS network sockets.
"""

from __future__ import annotations

import asyncio
import math
import socket

import pytest
from pymavlink import mavutil
from pymavlink.dialects.v20 import common as mavlink2

from tactical_edge_gateway.core.models import TelemetryFrame, VehicleDomain
from tactical_edge_gateway.ingest.mavlink import MavlinkAdapter
from tactical_edge_gateway.ingest.synthetic import SyntheticAdapter


def _find_available_udp_port() -> int:
    """Finds an unused UDP port on localhost."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.mark.asyncio
async def test_live_udp_end_to_end_telemetry():
    """Verify live UDP transmission, reception, and complete TelemetryFrame normalization."""
    port = _find_available_udp_port()
    adapter = MavlinkAdapter(
        connection_string=f"udpin:127.0.0.1:{port}",
        callsign_prefix="VIPER",
    )
    await adapter.start()
    assert adapter.is_running

    sender = mavutil.mavlink_connection(f"udpout:127.0.0.1:{port}", source_system=15)

    # 1. Send HEARTBEAT (Quadrotor, Armed)
    sender.mav.heartbeat_send(
        mavlink2.MAV_TYPE_QUADROTOR,
        mavlink2.MAV_AUTOPILOT_PX4,
        mavlink2.MAV_MODE_FLAG_SAFETY_ARMED,
        0,
        mavlink2.MAV_STATE_ACTIVE,
    )

    # 2. Send ATTITUDE (Roll ~5 deg, Pitch ~-10 deg, Yaw ~90 deg)
    sender.mav.attitude_send(
        0,
        roll=math.radians(5.0),
        pitch=math.radians(-10.0),
        yaw=math.radians(90.0),
        rollspeed=0.0,
        pitchspeed=0.0,
        yawspeed=0.0,
    )

    # 3. Send SYS_STATUS (Battery 92%, 22.8V, 0 packet drop)
    sender.mav.sys_status_send(
        onboard_control_sensors_present=0,
        onboard_control_sensors_enabled=0,
        onboard_control_sensors_health=0,
        load=150,
        voltage_battery=22800,  # mV -> 22.8V
        current_battery=1200,  # cA
        battery_remaining=92,  # %
        drop_rate_comm=0,
        errors_comm=0,
        errors_count1=0,
        errors_count2=0,
        errors_count3=0,
        errors_count4=0,
    )

    # 4. Send GLOBAL_POSITION_INT (triggers frame generation)
    # Lat: 36.8529, Lon: -75.9780, MSL: 150m, HAE: 125m, vx: 6.0 m/s, vy: 8.0 m/s, hdg: 90 deg
    sender.mav.global_position_int_send(
        0,
        int(36.8529 * 1e7),
        int(-75.9780 * 1e7),
        int(150.0 * 1e3),
        int(125.0 * 1e3),
        600,  # cm/s vx
        800,  # cm/s vy (total speed = 10.0 m/s)
        0,  # vz
        9000,  # cdeg -> 90.0 deg
    )

    received_frames: list[TelemetryFrame] = []
    async for frame in adapter.stream():
        received_frames.append(frame)
        break

    await adapter.stop()
    sender.close()

    assert len(received_frames) == 1
    frame = received_frames[0]

    # Verify identity & symbology
    assert frame.uid == "mavlink-15"
    assert frame.callsign == "VIPER-15"
    assert frame.domain == VehicleDomain.AIR
    assert frame.default_cot_type() == "a-f-A-M-F"

    # Verify geodetic position
    assert math.isclose(frame.position.latitude, 36.8529, abs_tol=1e-5)
    assert math.isclose(frame.position.longitude, -75.9780, abs_tol=1e-5)
    assert frame.position.altitude_msl_m == 150.0
    assert frame.position.altitude_hae_m == 125.0
    assert frame.position.heading_deg == 90.0
    assert frame.position.speed_mps is not None
    assert math.isclose(frame.position.speed_mps, 10.0, abs_tol=0.01)

    # Verify converted attitude
    assert frame.attitude is not None
    assert math.isclose(frame.attitude.roll_deg, 5.0, abs_tol=0.1)
    assert math.isclose(frame.attitude.pitch_deg, -10.0, abs_tol=0.1)
    assert math.isclose(frame.attitude.yaw_deg, 90.0, abs_tol=0.1)

    # Verify platform health
    assert frame.health is not None
    assert frame.health.is_armed is True
    assert frame.health.battery_percent == 92.0
    assert frame.health.battery_voltage_v == 22.8
    assert frame.health.link_quality_percent == 100.0

    # Verify Pydantic JSON round-trip serialization
    json_data = frame.model_dump_json()
    reconstituted = TelemetryFrame.model_validate_json(json_data)
    assert reconstituted == frame


@pytest.mark.asyncio
async def test_live_udp_multi_vehicle_interleaved():
    """Verify live UDP reception from multiple vehicles with distinct domains."""
    port = _find_available_udp_port()
    adapter = MavlinkAdapter(connection_string=f"udpin:127.0.0.1:{port}")
    await adapter.start()

    # Create two senders on the same UDP target port:
    # Air drone (sys_id 10) & Ground rover (sys_id 20)
    sender_air = mavutil.mavlink_connection(f"udpout:127.0.0.1:{port}", source_system=10)
    sender_ground = mavutil.mavlink_connection(f"udpout:127.0.0.1:{port}", source_system=20)

    # Send Heartbeats
    sender_air.mav.heartbeat_send(
        mavlink2.MAV_TYPE_QUADROTOR,
        mavlink2.MAV_AUTOPILOT_PX4,
        mavlink2.MAV_MODE_FLAG_SAFETY_ARMED,
        0,
        mavlink2.MAV_STATE_ACTIVE,
    )
    sender_ground.mav.heartbeat_send(
        mavlink2.MAV_TYPE_GROUND_ROVER,
        mavlink2.MAV_AUTOPILOT_ARDUPILOTMEGA,
        mavlink2.MAV_MODE_FLAG_SAFETY_ARMED,
        0,
        mavlink2.MAV_STATE_ACTIVE,
    )

    # Send GPS fixes
    sender_air.mav.global_position_int_send(
        0, int(35.0 * 1e7), int(-79.0 * 1e7), int(100 * 1e3), 0, 0, 0, 0, 0
    )
    sender_ground.mav.global_position_int_send(
        0, int(35.1 * 1e7), int(-79.1 * 1e7), int(10 * 1e3), 0, 0, 0, 0, 0
    )

    frames: dict[str, TelemetryFrame] = {}
    async for frame in adapter.stream():
        frames[frame.uid] = frame
        if len(frames) >= 2:
            break

    await adapter.stop()
    sender_air.close()
    sender_ground.close()

    assert "mavlink-10" in frames
    assert "mavlink-20" in frames

    air_frame = frames["mavlink-10"]
    ground_frame = frames["mavlink-20"]

    assert air_frame.domain == VehicleDomain.AIR
    assert air_frame.default_cot_type() == "a-f-A-M-F"
    assert math.isclose(air_frame.position.latitude, 35.0, abs_tol=1e-5)

    assert ground_frame.domain == VehicleDomain.GROUND
    assert ground_frame.default_cot_type() == "a-f-G-E-V-U"
    assert math.isclose(ground_frame.position.latitude, 35.1, abs_tol=1e-5)


@pytest.mark.asyncio
async def test_live_udp_socket_resilience_to_garbage_bytes():
    """Verify that corrupt datagrams on UDP port do not crash the reader loop."""
    port = _find_available_udp_port()
    adapter = MavlinkAdapter(connection_string=f"udpin:127.0.0.1:{port}")
    await adapter.start()

    # Blast raw malformed bytes directly to UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(b"\x00\xff\xfe\x01\xaa\xbb\xccRANDOM_CORRUPTION_BYTES", ("127.0.0.1", port))
    sock.close()

    # Small delay for garbage processing
    await asyncio.sleep(0.05)

    # Now send a valid MAVLink packet
    sender = mavutil.mavlink_connection(f"udpout:127.0.0.1:{port}", source_system=30)
    sender.mav.heartbeat_send(
        mavlink2.MAV_TYPE_HEXAROTOR,
        mavlink2.MAV_AUTOPILOT_PX4,
        0,
        0,
        mavlink2.MAV_STATE_ACTIVE,
    )
    sender.mav.global_position_int_send(
        0, int(32.5 * 1e7), int(-117.0 * 1e7), int(50 * 1e3), 0, 0, 0, 0, 0
    )

    received_frames: list[TelemetryFrame] = []
    async for frame in adapter.stream():
        received_frames.append(frame)
        break

    await adapter.stop()
    sender.close()

    assert len(received_frames) == 1
    assert received_frames[0].uid == "mavlink-30"
    assert math.isclose(received_frames[0].position.latitude, 32.5, abs_tol=1e-5)


@pytest.mark.asyncio
async def test_live_udp_lifecycle_and_port_rebind():
    """Verify adapter clean shutdown and socket release allows immediate rebind."""
    port = _find_available_udp_port()

    # Instance 1
    adapter1 = MavlinkAdapter(connection_string=f"udpin:127.0.0.1:{port}")
    await adapter1.start()
    assert adapter1.is_running
    await adapter1.stop()
    assert not adapter1.is_running

    # Instance 2 re-binding immediately to the same port
    adapter2 = MavlinkAdapter(connection_string=f"udpin:127.0.0.1:{port}")
    await adapter2.start()
    assert adapter2.is_running
    await adapter2.stop()
    assert not adapter2.is_running


@pytest.mark.asyncio
async def test_live_udp_burst_backpressure():
    """Verify high-rate burst over UDP drops oldest frames without queue locking."""
    port = _find_available_udp_port()
    adapter = MavlinkAdapter(connection_string=f"udpin:127.0.0.1:{port}", queue_size=3)
    await adapter.start()

    sender = mavutil.mavlink_connection(f"udpout:127.0.0.1:{port}", source_system=40)
    sender.mav.heartbeat_send(
        mavlink2.MAV_TYPE_QUADROTOR,
        mavlink2.MAV_AUTOPILOT_PX4,
        0,
        0,
        mavlink2.MAV_STATE_ACTIVE,
    )

    # Send 10 positions with distinct latitudes
    for i in range(10):
        sender.mav.global_position_int_send(
            0, int((30.0 + i) * 1e7), int(-80.0 * 1e7), 0, 0, 0, 0, 0, 0
        )

    # Allow network packets to be received and processed into the small queue
    await asyncio.sleep(0.1)

    # Queue size should be bounded by maxsize
    assert adapter._queue.qsize() <= 3

    received: list[TelemetryFrame] = []
    while not adapter._queue.empty():
        received.append(adapter._queue.get_nowait())

    await adapter.stop()
    sender.close()

    # Verify that received frames contain the latest packets (higher latitudes)
    assert len(received) <= 3
    assert received[-1].position.latitude >= 37.0


@pytest.mark.asyncio
async def test_multi_adapter_concurrent_streaming():
    """Verify simultaneous ingestion from both Synthetic and live UDP MAVLink adapters."""
    port = _find_available_udp_port()

    synthetic_adapter = SyntheticAdapter(uid="sim-01", rate_hz=50.0, max_iterations=4)
    mavlink_adapter = MavlinkAdapter(connection_string=f"udpin:127.0.0.1:{port}")

    await synthetic_adapter.start()
    await mavlink_adapter.start()

    sender = mavutil.mavlink_connection(f"udpout:127.0.0.1:{port}", source_system=50)
    sender.mav.heartbeat_send(
        mavlink2.MAV_TYPE_QUADROTOR,
        mavlink2.MAV_AUTOPILOT_PX4,
        0,
        0,
        mavlink2.MAV_STATE_ACTIVE,
    )
    sender.mav.global_position_int_send(0, int(38.0 * 1e7), int(-77.0 * 1e7), 0, 0, 0, 0, 0, 0)

    collected_frames: list[TelemetryFrame] = []

    async def _collect_synthetic() -> None:
        async for frame in synthetic_adapter.stream():
            collected_frames.append(frame)

    async def _collect_mavlink() -> None:
        async for frame in mavlink_adapter.stream():
            collected_frames.append(frame)
            break

    await asyncio.gather(_collect_synthetic(), _collect_mavlink())

    await synthetic_adapter.stop()
    await mavlink_adapter.stop()
    sender.close()

    uids = {f.uid for f in collected_frames}
    assert "sim-01" in uids
    assert "mavlink-50" in uids
