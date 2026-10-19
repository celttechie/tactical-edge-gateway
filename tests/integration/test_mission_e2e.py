"""High-level end-to-end integration test of the Tactical Edge Gateway.

Tests concurrent multi-domain autonomy telemetry ingestion (MAVLink 2.0 UAV,
ROS2 Ground Rover, and Synthetic Scout) routed simultaneously through the GatewayEngine
to both persistent TCP TAK Server streaming and UDP ATAK mesh sockets.
Also verifies link severance, queue buffering, and automatic reconnection.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import pytest
from pymavlink import mavutil
from pymavlink.dialects.v20 import common as mavlink2

from scripts.common import find_free_port as _find_free_port
from tactical_edge_gateway.core.engine import GatewayEngine
from tactical_edge_gateway.core.models import VehicleDomain
from tactical_edge_gateway.egress.tcp import TcpEgressClient
from tactical_edge_gateway.egress.udp import UdpEgressClient
from tactical_edge_gateway.ingest.mavlink import MavlinkAdapter
from tactical_edge_gateway.ingest.ros2 import ROS2Adapter
from tactical_edge_gateway.ingest.synthetic import SyntheticAdapter
from tactical_edge_gateway.transform.cot import CoTTransformer


class MockHeader:
    def __init__(self, frame_id: str = "base_link") -> None:
        self.frame_id = frame_id


class MockPoint:
    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> None:
        self.x = x
        self.y = y
        self.z = z


class MockQuaternion:
    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0, w: float = 1.0) -> None:
        self.x = x
        self.y = y
        self.z = z
        self.w = w


class MockPose:
    def __init__(self) -> None:
        self.position = MockPoint(10.0, 5.0, 0.0)
        self.orientation = MockQuaternion(0.0, 0.0, 0.3826834, 0.9238795)  # yaw ~45 deg


class MockPoseWithCovariance:
    def __init__(self) -> None:
        self.pose = MockPose()


class MockTwist:
    def __init__(self) -> None:
        self.linear = MockPoint(3.0, 4.0, 0.0)  # total speed = 5.0 m/s
        self.angular = MockPoint(0.0, 0.0, 0.1)


class MockTwistWithCovariance:
    def __init__(self) -> None:
        self.twist = MockTwist()


class MockOdometry:
    def __init__(self) -> None:
        self.header = MockHeader("odom")
        self.child_frame_id = "base_footprint"
        self.pose = MockPoseWithCovariance()
        self.twist = MockTwistWithCovariance()


class MockNavSatStatus:
    def __init__(self, status: int = 0) -> None:
        self.status = status


class MockNavSatFix:
    def __init__(
        self,
        lat: float = 35.1600,
        lon: float = -79.0600,
        alt: float = 45.0,
        status: int = 0,
    ) -> None:
        self.header = MockHeader("gps")
        self.status = MockNavSatStatus(status)
        self.latitude = lat
        self.longitude = lon
        self.altitude = alt
        self.position_covariance = [4.0, 0.0, 0.0, 0.0, 4.0, 0.0, 0.0, 0.0, 9.0]


@pytest.mark.asyncio
async def test_multi_vehicle_concurrent_mission_e2e():
    """Verify simultaneous multi-vehicle multi-domain mission routing to dual C2 egresses."""
    mavlink_port = _find_free_port(is_udp=True)
    tak_tcp_port = _find_free_port(is_udp=False)
    atak_udp_port = _find_free_port(is_udp=True)

    # 1. Start Mock TAK Server (TCP streaming receiver)
    tcp_received_chunks: list[bytes] = []

    async def _handle_tak_client(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                tcp_received_chunks.append(data)
        except Exception:
            pass
        finally:
            writer.close()

    tak_server = await asyncio.start_server(_handle_tak_client, "127.0.0.1", tak_tcp_port)

    # 2. Start ATAK Listener (UDP socket)
    atak_listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    atak_listener.bind(("127.0.0.1", atak_udp_port))
    atak_listener.setblocking(False)

    # 3. Instantiate Adapters
    # Ingest 1: MAVLink 2.0 Quadrotor
    mavlink_adapter = MavlinkAdapter(
        connection_string=f"udpin:127.0.0.1:{mavlink_port}",
        callsign_prefix="VIPER",
    )
    # Ingest 2: ROS2 Ground Rover
    ros2_adapter = ROS2Adapter(
        uid="ugv-titan-01",
        callsign="TITAN-GROUND",
        domain=VehicleDomain.GROUND,
    )
    # Ingest 3: Synthetic Airborne Scout (fixed iterations)
    synth_adapter = SyntheticAdapter(
        uid="uav-hawk-02",
        callsign="HAWK-EYE",
        domain=VehicleDomain.AIR,
        rate_hz=20.0,
    )

    # 4. Instantiate Egress Transports
    tcp_egress = TcpEgressClient(
        host="127.0.0.1",
        port=tak_tcp_port,
        reconnect_interval_s=0.1,
    )
    udp_egress = UdpEgressClient(
        host="127.0.0.1",
        port=atak_udp_port,
    )

    # 5. Build and launch GatewayEngine
    engine = GatewayEngine(
        adapters=[mavlink_adapter, ros2_adapter, synth_adapter],
        egress_clients=[tcp_egress, udp_egress],
    )
    await engine.start()

    # Wait briefly for TCP client connection to TAK server
    for _ in range(25):
        if tcp_egress.is_connected:
            break
        await asyncio.sleep(0.04)
    assert tcp_egress.is_connected, "TcpEgressClient failed to connect to TAK Server"

    # 6. Transmit vehicle telemetry
    # --- Vehicle 1: MAVLink over UDP ---
    mav_sender = mavutil.mavlink_connection(
        f"udpout:127.0.0.1:{mavlink_port}",
        source_system=42,
    )
    mav_sender.mav.heartbeat_send(
        mavlink2.MAV_TYPE_QUADROTOR,
        mavlink2.MAV_AUTOPILOT_PX4,
        mavlink2.MAV_MODE_FLAG_SAFETY_ARMED,
        0,
        mavlink2.MAV_STATE_ACTIVE,
    )
    mav_sender.mav.attitude_send(
        0,
        0.05,  # roll
        -0.02,  # pitch
        3.14,  # yaw ~180 deg
        0.0,
        0.0,
        0.0,
    )
    mav_sender.mav.sys_status_send(0, 0, 0, 0, 15200, -1, 88, 0, 0, 0, 0, 0, 0)
    mav_sender.mav.global_position_int_send(
        0,
        int(35.1500 * 1e7),
        int(-79.0500 * 1e7),
        int(175 * 1e3),
        int(150 * 1e3),
        0,
        -1500,  # speed 15 m/s south
        0,
        18000,  # 180 deg
    )

    # --- Vehicle 2: ROS2 Ground Rover ---
    ros2_adapter.handle_odometry(MockOdometry())
    ros2_adapter.handle_nav_sat_fix(MockNavSatFix(lat=35.1600, lon=-79.0600, alt=45.0))

    # 7. Collect UDP datagrams at ATAK listener
    loop = asyncio.get_running_loop()
    transformer = CoTTransformer()
    udp_uids: dict[str, Any] = {}

    async def _collect_udp() -> None:
        while len(udp_uids) < 3:
            try:
                pkt = await asyncio.wait_for(loop.sock_recv(atak_listener, 4096), timeout=1.0)
                evt = transformer.parse_cot(pkt)["event"]
                udp_uids[evt["@uid"]] = evt
            except asyncio.TimeoutError:
                break

    await asyncio.wait_for(_collect_udp(), timeout=4.0)

    # Allow TCP buffer transmission to flush
    await asyncio.sleep(0.2)

    # 8. Shutdown and cleanup
    await engine.stop()
    tak_server.close()
    await tak_server.wait_closed()
    atak_listener.close()
    mav_sender.close()

    # 9. Verify Engine and Client Telemetry Metrics
    assert engine.stats.frames_ingested >= 3
    assert engine.stats.frames_routed >= 3
    assert tcp_egress.stats.packets_sent >= 3
    assert udp_egress.stats.packets_sent >= 3
    assert engine.stats.errors_count == 0

    # 10. Verify UDP CoT datagrams at ATAK listener
    # All three vehicles must be present in the UDP mesh broadcast
    assert "mavlink-42" in udp_uids
    assert "ugv-titan-01" in udp_uids
    assert "uav-hawk-02" in udp_uids

    # Verify Airborne MAVLink Quadrotor details
    mav_event = udp_uids["mavlink-42"]
    assert mav_event["@type"] == "a-f-A-M-F"  # Friendly Airborne Military Drone
    assert float(mav_event["point"]["@lat"]) == 35.15
    assert float(mav_event["point"]["@lon"]) == -79.05
    assert float(mav_event["point"]["@hae"]) == 150.0
    assert float(mav_event["detail"]["track"]["@speed"]) == 15.0
    assert float(mav_event["detail"]["track"]["@course"]) == 180.0
    assert int(mav_event["detail"]["status"]["@battery"]) == 88

    # Verify Ground ROS2 Rover details
    ugv_event = udp_uids["ugv-titan-01"]
    assert ugv_event["@type"] == "a-f-G-E-V-U"  # Friendly Ground Unmanned Vehicle
    assert float(ugv_event["point"]["@lat"]) == 35.16
    assert float(ugv_event["point"]["@lon"]) == -79.06
    assert ugv_event["detail"]["contact"]["@callsign"] == "TITAN-GROUND"
    assert float(ugv_event["detail"]["track"]["@speed"]) == 5.0
    assert abs(float(ugv_event["detail"]["track"]["@course"]) - 45.0) < 1.0

    # Verify Airborne Synthetic Scout details
    synth_event = udp_uids["uav-hawk-02"]
    assert synth_event["@type"] == "a-f-A-M-F"
    assert synth_event["detail"]["contact"]["@callsign"] == "HAWK-EYE"

    # 11. Verify TCP streaming to TAK Server
    assert len(tcp_received_chunks) >= 1
    tcp_stream_combined = b"".join(tcp_received_chunks).decode("utf-8")
    assert "mavlink-42" in tcp_stream_combined
    assert "ugv-titan-01" in tcp_stream_combined
    assert "uav-hawk-02" in tcp_stream_combined


@pytest.mark.asyncio
async def test_e2e_adverse_network_disconnect_and_recovery():
    """Verify gateway resilience under TCP link severance and automatic reconnection."""
    tak_tcp_port = _find_free_port(is_udp=False)
    received_packets_phase1: list[bytes] = []
    received_packets_phase2: list[bytes] = []
    active_writers: list[asyncio.StreamWriter] = []

    # Handler for Phase 1
    async def _handle_phase1(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        active_writers.append(writer)
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                received_packets_phase1.append(data)
        except Exception:
            pass
        finally:
            writer.close()

    # 1. Start initial TAK Server
    server1 = await asyncio.start_server(_handle_phase1, "127.0.0.1", tak_tcp_port)

    # 2. Configure GatewayEngine with Synthetic stream and fast reconnect
    synth_adapter = SyntheticAdapter(
        uid="uav-resilient-01",
        callsign="RESILIENT-1",
        rate_hz=20.0,
    )
    tcp_egress = TcpEgressClient(
        host="127.0.0.1",
        port=tak_tcp_port,
        reconnect_interval_s=0.1,
        max_reconnect_interval_s=0.3,
    )
    engine = GatewayEngine(adapters=[synth_adapter], egress_clients=[tcp_egress])
    await engine.start()

    # Wait for connection and initial transmission
    for _ in range(20):
        if tcp_egress.is_connected and len(received_packets_phase1) > 0:
            break
        await asyncio.sleep(0.05)
    assert tcp_egress.is_connected

    # 3. Simulate severing the network connection abruptly
    server1.close()
    for w in active_writers:
        w.close()
    await server1.wait_closed()

    # Wait for client to detect disconnect
    for _ in range(20):
        if not tcp_egress.is_connected:
            break
        await asyncio.sleep(0.05)
    assert not tcp_egress.is_connected, "Client should detect dropped connection"

    # 4. Handler for Phase 2 (Server recovered)
    async def _handle_phase2(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                received_packets_phase2.append(data)
        except Exception:
            pass
        finally:
            writer.close()

    # Restart TAK Server on same port
    server2 = await asyncio.start_server(_handle_phase2, "127.0.0.1", tak_tcp_port)

    # 5. Wait for automated exponential backoff reconnection
    for _ in range(30):
        if tcp_egress.is_connected and len(received_packets_phase2) > 0:
            break
        await asyncio.sleep(0.08)

    assert tcp_egress.is_connected, "TcpEgressClient failed to auto-reconnect"
    assert len(received_packets_phase2) > 0, "No packets received after recovery"

    # Cleanup
    await engine.stop()
    server2.close()
    await server2.wait_closed()

    combined_phase2 = b"".join(received_packets_phase2).decode("utf-8")
    assert "uav-resilient-01" in combined_phase2
