"""Live Integration tests for ROS2 ground autonomy to CoT egress pipeline.

Validates end-to-end integration:
ROS2 NavSatFix & Odometry -> ROS2Adapter -> CoTTransformer -> UdpEgressClient -> ATAK Listener
verifying correct MIL-STD ground symbology (a-f-G-E-V-U) and kinematics over live network sockets.
"""

from __future__ import annotations

import asyncio
import math
import socket

import pytest

from tactical_edge_gateway.core.models import VehicleDomain
from tactical_edge_gateway.egress.udp import UdpEgressClient
from tactical_edge_gateway.ingest.ros2 import ROS2Adapter
from tactical_edge_gateway.transform.cot import CoTTransformer


class _MockVector3:
    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0):
        self.x = x
        self.y = y
        self.z = z


class _MockQuaternion:
    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0, w: float = 1.0):
        self.x = x
        self.y = y
        self.z = z
        self.w = w


class _MockPose:
    def __init__(self, orientation: _MockQuaternion | None = None):
        self.orientation = orientation or _MockQuaternion()


class _MockPoseWithCovariance:
    def __init__(self, pose: _MockPose | None = None):
        self.pose = pose or _MockPose()


class _MockTwist:
    def __init__(self, linear: _MockVector3 | None = None):
        self.linear = linear or _MockVector3()


class _MockTwistWithCovariance:
    def __init__(self, twist: _MockTwist | None = None):
        self.twist = twist or _MockTwist()


class _MockOdometry:
    def __init__(
        self,
        pose: _MockPoseWithCovariance | None = None,
        twist: _MockTwistWithCovariance | None = None,
    ):
        self.pose = pose or _MockPoseWithCovariance()
        self.twist = twist or _MockTwistWithCovariance()


class _MockNavSatStatus:
    def __init__(self, status: int = 0):
        self.status = status


class _MockNavSatFix:
    def __init__(
        self,
        latitude: float = 36.8529,
        longitude: float = -75.9780,
        altitude: float = 25.0,
        position_covariance: list[float] | None = None,
        status: _MockNavSatStatus | None = None,
    ):
        self.latitude = latitude
        self.longitude = longitude
        self.altitude = altitude
        self.position_covariance = (
            position_covariance
            if position_covariance is not None
            else [4.0, 0.0, 0.0, 0.0, 4.0, 0.0, 0.0, 0.0, 4.0]
        )
        self.status = status or _MockNavSatStatus()


def _find_available_port() -> int:
    """Finds an unused local UDP port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.mark.asyncio
async def test_ros2_to_cot_live_udp_pipeline():
    """Verify ROS2 ground telemetry maps to CoT XML and broadcasts to ATAK listener."""
    port = _find_available_port()

    # 1. ATAK Listener socket on loopback
    atak_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    atak_sock.bind(("127.0.0.1", port))
    atak_sock.setblocking(False)

    # 2. UdpEgressClient targeting that port
    egress = UdpEgressClient(host="127.0.0.1", port=port)
    await egress.start()

    # 3. ROS2Adapter for ground UGV
    ros2_adapter = ROS2Adapter(
        uid="ugv-warthog-01",
        callsign="WARTHOG-01",
        domain=VehicleDomain.GROUND,
    )
    await ros2_adapter.start()

    # 4. Pipeline worker task forwarding ROS2 frames to Egress
    async def _forward_pipeline():
        async for frame in ros2_adapter.stream():
            await egress.send_frame(frame)
            break

    pipeline_task = asyncio.create_task(_forward_pipeline())

    # 5. Ingest ROS2 Odometry (heading 180 deg, speed 4.0 m/s)
    # 180 deg yaw quaternion: (0, 0, sin(90), cos(90)) = (0, 0, 1, 0)
    odom = _MockOdometry(
        pose=_MockPoseWithCovariance(pose=_MockPose(orientation=_MockQuaternion(z=1.0, w=0.0))),
        twist=_MockTwistWithCovariance(twist=_MockTwist(linear=_MockVector3(x=4.0, y=0.0))),
    )
    ros2_adapter.handle_odometry(odom)

    # Ingest NavSatFix
    fix = _MockNavSatFix(latitude=36.8529, longitude=-75.9780, altitude=30.0)
    ros2_adapter.handle_nav_sat_fix(fix)

    # 6. Receive CoT datagram at ATAK listener
    loop = asyncio.get_running_loop()
    raw_cot = await asyncio.wait_for(loop.sock_recv(atak_sock, 4096), timeout=2.0)

    # Cleanup
    await pipeline_task
    await ros2_adapter.stop()
    await egress.stop()
    atak_sock.close()

    # 7. Validate received CoT XML
    transformer = CoTTransformer()
    parsed = transformer.parse_cot(raw_cot)
    event = parsed["event"]

    # Verify UGV identity and MIL-STD ground symbology
    assert event["@uid"] == "ugv-warthog-01"
    assert event["@type"] == "a-f-G-E-V-U"  # Friendly Ground Unmanned Vehicle
    assert math.isclose(float(event["point"]["@lat"]), 36.8529, abs_tol=1e-5)
    assert math.isclose(float(event["point"]["@lon"]), -75.9780, abs_tol=1e-5)
    assert float(event["point"]["@hae"]) == 30.0

    # Verify tactical details
    detail = event["detail"]
    assert detail["contact"]["@callsign"] == "WARTHOG-01"
    assert float(detail["track"]["@course"]) == 180.0
    assert float(detail["track"]["@speed"]) == 4.0
    assert detail["status"]["@is_armed"] == "true"
