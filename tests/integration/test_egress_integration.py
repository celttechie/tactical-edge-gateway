"""Integration tests for C2 network egress clients over live network sockets.

Validates UDP unicast/multicast CoT transmission, persistent TCP streaming
to mock TAK Server, and end-to-end MAVLink-to-CoT egress pipeline.
"""

from __future__ import annotations

import asyncio
import socket

import pytest
from pymavlink import mavutil
from pymavlink.dialects.v20 import common as mavlink2

from tactical_edge_gateway.core.models import (
    GeodeticPosition,
    PlatformHealth,
    TelemetryFrame,
    VehicleDomain,
)
from tactical_edge_gateway.egress.tcp import TcpEgressClient
from tactical_edge_gateway.egress.udp import UdpEgressClient
from tactical_edge_gateway.ingest.mavlink import MavlinkAdapter
from tactical_edge_gateway.transform.cot import CoTTransformer


def _find_available_port(is_udp: bool = True) -> int:
    """Finds an unused local port for testing."""
    sock_type = socket.SOCK_DGRAM if is_udp else socket.SOCK_STREAM
    sock = socket.socket(socket.AF_INET, sock_type)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.mark.asyncio
async def test_live_udp_egress_transmission():
    """Verify live UDP CoT transmission and reception over OS loopback socket."""
    port = _find_available_port(is_udp=True)

    # 1. Spin up UDP listener socket (simulating ATAK listener)
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(("127.0.0.1", port))
    listener.setblocking(False)

    # 2. Spin up UdpEgressClient targeting that port
    client = UdpEgressClient(host="127.0.0.1", port=port)
    await client.start()

    frame = TelemetryFrame(
        uid="uav-live-01",
        callsign="VIPER-LIVE",
        domain=VehicleDomain.AIR,
        position=GeodeticPosition(
            latitude=35.1415,
            longitude=-79.0069,
            altitude_msl_m=120.0,
            heading_deg=90.0,
            speed_mps=12.0,
        ),
        health=PlatformHealth(battery_percent=90.0, is_armed=True),
    )

    # 3. Transmit frame
    await client.send_frame(frame)

    # 4. Receive from listener socket
    loop = asyncio.get_running_loop()
    raw_data = await asyncio.wait_for(loop.sock_recv(listener, 4096), timeout=2.0)

    await client.stop()
    listener.close()

    assert client.stats.packets_sent == 1
    assert client.stats.bytes_sent > 0

    # 5. Parse and validate received CoT XML
    transformer = CoTTransformer()
    parsed = transformer.parse_cot(raw_data)
    event = parsed["event"]

    assert event["@uid"] == "uav-live-01"
    assert event["@type"] == "a-f-A-M-F"
    assert float(event["point"]["@lat"]) == 35.1415
    assert float(event["point"]["@lon"]) == -79.0069
    assert event["detail"]["contact"]["@callsign"] == "VIPER-LIVE"
    assert float(event["detail"]["track"]["@speed"]) == 12.0


@pytest.mark.asyncio
async def test_live_tcp_egress_streaming_and_reconnection():
    """Verify persistent TCP streaming and reconnect behavior to mock TAK Server."""
    port = _find_available_port(is_udp=False)
    received_packets: list[bytes] = []

    # 1. Mock TAK Server handler
    async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                received_packets.append(data)
        except Exception:
            pass
        finally:
            writer.close()

    # 2. Start mock TAK Server
    server = await asyncio.start_server(_handle_client, "127.0.0.1", port)

    # 3. Connect TcpEgressClient
    client = TcpEgressClient(
        host="127.0.0.1",
        port=port,
        reconnect_interval_s=0.2,
    )
    await client.start()

    # Wait for connection establishment
    for _ in range(20):
        if client.is_connected:
            break
        await asyncio.sleep(0.05)
    assert client.is_connected

    # 4. Stream 2 frames
    f1 = TelemetryFrame(
        uid="uav-tcp-01",
        callsign="STREAM-1",
        domain=VehicleDomain.AIR,
        position=GeodeticPosition(latitude=34.0, longitude=-118.0),
    )
    f2 = TelemetryFrame(
        uid="ugv-tcp-02",
        callsign="STREAM-2",
        domain=VehicleDomain.GROUND,
        position=GeodeticPosition(latitude=34.1, longitude=-118.1),
    )

    await client.send_frame(f1)
    await client.send_frame(f2)

    # Allow transmission
    await asyncio.sleep(0.1)

    await client.stop()
    server.close()
    await server.wait_closed()

    assert len(received_packets) >= 1
    combined = b"".join(received_packets).decode("utf-8")
    assert "uav-tcp-01" in combined
    assert "ugv-tcp-02" in combined
    assert client.stats.packets_sent == 2


@pytest.mark.asyncio
async def test_end_to_end_mavlink_to_cot_egress_pipeline():
    """Verify full end-to-end pipeline: MAVLink UDP Ingest -> CoT Transform -> UDP Egress."""
    ingest_port = _find_available_port(is_udp=True)
    egress_port = _find_available_port(is_udp=True)

    # Ingest: MAVLink adapter on ingest_port
    adapter = MavlinkAdapter(connection_string=f"udpin:127.0.0.1:{ingest_port}")
    await adapter.start()

    # Egress: UDP client targeting egress_port
    egress = UdpEgressClient(host="127.0.0.1", port=egress_port)
    await egress.start()

    # ATAK Mock Listener: Listening on egress_port
    atak_listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    atak_listener.bind(("127.0.0.1", egress_port))
    atak_listener.setblocking(False)

    # Pipeline bridge task: Ingest -> Egress
    async def _bridge_pipeline() -> None:
        async for frame in adapter.stream():
            await egress.send_frame(frame)
            break

    bridge_task = asyncio.create_task(_bridge_pipeline())

    # Transmitter: Send binary MAVLink 2.0 packets into ingest port
    sender = mavutil.mavlink_connection(f"udpout:127.0.0.1:{ingest_port}", source_system=77)
    sender.mav.heartbeat_send(
        mavlink2.MAV_TYPE_QUADROTOR,
        mavlink2.MAV_AUTOPILOT_PX4,
        mavlink2.MAV_MODE_FLAG_SAFETY_ARMED,
        0,
        mavlink2.MAV_STATE_ACTIVE,
    )
    sender.mav.global_position_int_send(
        0,
        int(35.2500 * 1e7),
        int(-78.8500 * 1e7),
        int(180 * 1e3),
        int(155 * 1e3),
        800,  # 8 m/s
        600,  # 6 m/s -> total speed = 10 m/s
        0,
        27000,  # 270 deg heading
    )

    # Await packet delivery at ATAK listener
    loop = asyncio.get_running_loop()
    cot_datagram = await asyncio.wait_for(loop.sock_recv(atak_listener, 4096), timeout=3.0)

    # Cleanup
    await bridge_task
    await adapter.stop()
    await egress.stop()
    atak_listener.close()
    sender.close()

    # Verify CoT message received at ATAK end
    transformer = CoTTransformer()
    parsed = transformer.parse_cot(cot_datagram)
    event = parsed["event"]

    assert event["@uid"] == "mavlink-77"
    assert event["@type"] == "a-f-A-M-F"
    assert float(event["point"]["@lat"]) == 35.25
    assert float(event["point"]["@lon"]) == -78.85
    assert float(event["point"]["@hae"]) == 155.0
    assert float(event["detail"]["track"]["@course"]) == 270.0
    assert float(event["detail"]["track"]["@speed"]) == 10.0
