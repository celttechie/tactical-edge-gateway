"""Unit tests for the ROS2 / DDS ground autonomy telemetry adapter."""

from __future__ import annotations

import asyncio
import math

import pytest

from tactical_edge_gateway.core.models import VehicleDomain
from tactical_edge_gateway.ingest.ros2 import ROS2Adapter, quaternion_to_euler_deg


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
        latitude: float = 35.1415,
        longitude: float = -79.0069,
        altitude: float = 120.0,
        position_covariance: list[float] | None = None,
        status: _MockNavSatStatus | None = None,
    ):
        self.latitude = latitude
        self.longitude = longitude
        self.altitude = altitude
        self.position_covariance = (
            position_covariance
            if position_covariance is not None
            else [9.0, 0.0, 0.0, 0.0, 16.0, 0.0, 0.0, 0.0, 25.0]
        )
        self.status = status or _MockNavSatStatus()


def test_quaternion_to_euler_angles():
    """Verify quaternion to Euler degrees conversion across canonical rotations."""
    # 1. Identity quaternion -> (0, 0, 0)
    roll, pitch, yaw = quaternion_to_euler_deg(0.0, 0.0, 0.0, 1.0)
    assert math.isclose(roll, 0.0, abs_tol=1e-5)
    assert math.isclose(pitch, 0.0, abs_tol=1e-5)
    assert math.isclose(yaw, 0.0, abs_tol=1e-5)

    # 2. 90 deg Yaw rotation around Z axis
    s90 = math.sin(math.radians(45))
    c90 = math.cos(math.radians(45))
    r_yaw, p_yaw, y_yaw = quaternion_to_euler_deg(0.0, 0.0, s90, c90)
    assert math.isclose(r_yaw, 0.0, abs_tol=1e-5)
    assert math.isclose(p_yaw, 0.0, abs_tol=1e-5)
    assert math.isclose(y_yaw, 90.0, abs_tol=1e-5)

    # 3. 45 deg Pitch rotation around Y axis
    s45 = math.sin(math.radians(22.5))
    c45 = math.cos(math.radians(22.5))
    r_pitch, p_pitch, y_pitch = quaternion_to_euler_deg(0.0, s45, 0.0, c45)
    assert math.isclose(r_pitch, 0.0, abs_tol=1e-5)
    assert math.isclose(p_pitch, 45.0, abs_tol=1e-5)
    assert math.isclose(y_pitch, 0.0, abs_tol=1e-5)

    # 4. 30 deg Roll rotation around X axis
    s30 = math.sin(math.radians(15.0))
    c30 = math.cos(math.radians(15.0))
    r_roll, p_roll, y_roll = quaternion_to_euler_deg(s30, 0.0, 0.0, c30)
    assert math.isclose(r_roll, 30.0, abs_tol=1e-5)
    assert math.isclose(p_roll, 0.0, abs_tol=1e-5)
    assert math.isclose(y_roll, 0.0, abs_tol=1e-5)


def test_ros2_adapter_initialization_defaults():
    """Verify ROS2Adapter default attributes and naming."""
    adapter = ROS2Adapter()
    assert adapter.uid == "ugv-01"
    assert adapter.callsign == "WARTHOG-01"
    assert adapter.domain == VehicleDomain.GROUND
    assert adapter.name == "ros2-ugv-01"
    assert not adapter.is_running


def test_handle_odometry_kinematics():
    """Verify odometry processing extracts heading, orientation, and speed."""
    adapter = ROS2Adapter()

    # Orientation: ~90 deg yaw, Speed: 3 m/s vx, 4 m/s vy -> 5 m/s total speed
    s90 = math.sin(math.radians(45))
    c90 = math.cos(math.radians(45))
    odom_msg = _MockOdometry(
        pose=_MockPoseWithCovariance(pose=_MockPose(orientation=_MockQuaternion(z=s90, w=c90))),
        twist=_MockTwistWithCovariance(twist=_MockTwist(linear=_MockVector3(x=3.0, y=4.0))),
    )

    adapter.handle_odometry(odom_msg)

    assert adapter._current_attitude is not None
    assert math.isclose(adapter._current_attitude.yaw_deg, 90.0, abs_tol=1e-4)
    assert adapter._current_heading_deg is not None
    assert math.isclose(adapter._current_heading_deg, 90.0, abs_tol=1e-4)
    assert adapter._current_speed_mps is not None
    assert math.isclose(adapter._current_speed_mps, 5.0, abs_tol=1e-4)


def test_handle_nav_sat_fix_success():
    """Verify NavSatFix aggregates prior kinematics and produces valid TelemetryFrame."""
    adapter = ROS2Adapter(uid="ugv-scout", callsign="BADGER-01")

    # Set prior kinematics from odom
    odom_msg = _MockOdometry(
        pose=_MockPoseWithCovariance(
            pose=_MockPose(
                orientation=_MockQuaternion(
                    z=math.sin(math.radians(45)), w=math.cos(math.radians(45))
                )
            )
        ),
        twist=_MockTwistWithCovariance(twist=_MockTwist(linear=_MockVector3(x=6.0, y=8.0))),
    )
    adapter.handle_odometry(odom_msg)

    # Ingest NavSatFix: Covariance [9, 0, ..., 16] -> CE = sqrt(9 + 16) = 5.0
    fix_msg = _MockNavSatFix(
        latitude=35.1415,
        longitude=-79.0069,
        altitude=150.0,
        position_covariance=[9.0, 0.0, 0.0, 0.0, 16.0, 0.0, 0.0, 0.0, 0.0],
    )

    frame = adapter.handle_nav_sat_fix(fix_msg)

    assert frame is not None
    assert frame.uid == "ugv-scout"
    assert frame.callsign == "BADGER-01"
    assert frame.domain == VehicleDomain.GROUND
    assert frame.default_cot_type() == "a-f-G-E-V-U"
    assert frame.position.latitude == 35.1415
    assert frame.position.longitude == -79.0069
    assert frame.position.altitude_msl_m == 150.0
    assert frame.position.altitude_hae_m == 150.0
    assert frame.position.speed_mps == 10.0
    assert math.isclose(frame.position.circular_error_m, 5.0, abs_tol=1e-4)

    assert frame.attitude is not None
    assert math.isclose(frame.attitude.yaw_deg, 90.0, abs_tol=1e-4)
    assert frame.health is not None
    assert frame.health.is_armed is True


def test_handle_nav_sat_fix_no_fix_and_invalid_coords():
    """Verify fixes with STATUS_NO_FIX or out-of-bounds coordinates are discarded."""
    adapter = ROS2Adapter()

    # 1. STATUS_NO_FIX (status = -1)
    no_fix_msg = _MockNavSatFix(status=_MockNavSatStatus(status=-1))
    assert adapter.handle_nav_sat_fix(no_fix_msg) is None

    # 2. Out-of-bounds latitude
    bad_lat = _MockNavSatFix(latitude=95.0, longitude=-79.0)
    assert adapter.handle_nav_sat_fix(bad_lat) is None

    # 3. Malformed object
    assert adapter.handle_nav_sat_fix(object()) is None


@pytest.mark.asyncio
async def test_ros2_adapter_async_stream_and_stop():
    """Verify asynchronous stream yielding and stop lifecycle."""
    adapter = ROS2Adapter()
    await adapter.start()
    assert adapter.is_running

    # Inject fix
    fix_msg = _MockNavSatFix(latitude=36.0, longitude=-78.0)
    adapter.handle_nav_sat_fix(fix_msg)

    frames = []
    async for frame in adapter.stream():
        frames.append(frame)
        break

    await adapter.stop()
    assert not adapter.is_running
    assert len(frames) == 1
    assert frames[0].position.latitude == 36.0


@pytest.mark.asyncio
async def test_ros2_adapter_queue_backpressure_eviction():
    """Verify queue backpressure evicts oldest frame when full."""
    adapter = ROS2Adapter()
    adapter._queue = asyncio.Queue(maxsize=2)

    f1 = _MockNavSatFix(latitude=30.0, longitude=-80.0)
    f2 = _MockNavSatFix(latitude=31.0, longitude=-80.0)
    f3 = _MockNavSatFix(latitude=32.0, longitude=-80.0)

    adapter.handle_nav_sat_fix(f1)
    adapter.handle_nav_sat_fix(f2)
    assert adapter._queue.qsize() == 2

    # Third frame triggers eviction
    adapter.handle_nav_sat_fix(f3)
    assert adapter._queue.qsize() == 2

    r1 = adapter._queue.get_nowait()
    r2 = adapter._queue.get_nowait()
    assert r1.position.latitude == 31.0
    assert r2.position.latitude == 32.0


def test_ros2_is_rclpy_available():
    """Verify is_rclpy_available property returns underlying flag."""
    adapter = ROS2Adapter()
    assert adapter.is_rclpy_available == adapter._running or not adapter.is_rclpy_available


@pytest.mark.asyncio
async def test_ros2_adapter_with_mocked_rclpy(monkeypatch):
    """Verify live ROS2 node creation, subscription bindings, and destruction with mocked rclpy."""
    import sys
    from unittest.mock import MagicMock

    import tactical_edge_gateway.ingest.ros2 as ros2_mod

    mock_rclpy = MagicMock()
    mock_rclpy.ok.return_value = False
    mock_node = MagicMock()
    mock_rclpy.create_node.return_value = mock_node

    # Mock nav_msgs and sensor_msgs in sys.modules
    mock_sensor_msgs = MagicMock()
    mock_nav_msgs = MagicMock()
    monkeypatch.setitem(sys.modules, "sensor_msgs.msg", mock_sensor_msgs)
    monkeypatch.setitem(sys.modules, "nav_msgs.msg", mock_nav_msgs)
    monkeypatch.setattr(ros2_mod, "RCLPY_AVAILABLE", True)
    monkeypatch.setattr(ros2_mod, "rclpy", mock_rclpy)

    def mock_spin(node):
        pass

    mock_rclpy.spin = mock_spin

    adapter = ROS2Adapter(node_name="test_ugv_node")
    await adapter.start()

    assert adapter.is_running
    assert mock_rclpy.init.called
    mock_rclpy.create_node.assert_called_with("test_ugv_node")
    assert mock_node.create_subscription.call_count == 2

    # Idempotent start
    await adapter.start()

    # Stop and clean up
    await adapter.stop()
    assert not adapter.is_running
    assert mock_node.destroy_node.called


@pytest.mark.asyncio
async def test_ros2_adapter_start_exception_handling(monkeypatch):
    """Verify start catches subscription/node initialization exceptions gracefully."""
    import sys
    from unittest.mock import MagicMock

    import tactical_edge_gateway.ingest.ros2 as ros2_mod

    mock_rclpy = MagicMock()
    mock_rclpy.ok.return_value = True
    mock_rclpy.create_node.side_effect = RuntimeError("Failed to bind DDS participant")

    mock_sensor_msgs = MagicMock()
    mock_nav_msgs = MagicMock()
    monkeypatch.setitem(sys.modules, "sensor_msgs.msg", mock_sensor_msgs)
    monkeypatch.setitem(sys.modules, "nav_msgs.msg", mock_nav_msgs)
    monkeypatch.setattr(ros2_mod, "RCLPY_AVAILABLE", True)
    monkeypatch.setattr(ros2_mod, "rclpy", mock_rclpy)

    adapter = ROS2Adapter()
    await adapter.start()
    assert adapter.is_running
    assert adapter._node is None
    await adapter.stop()


@pytest.mark.asyncio
async def test_ros2_adapter_stop_destroy_node_exception():
    """Verify stop handles destroy_node exceptions without bubbling."""
    from unittest.mock import MagicMock

    adapter = ROS2Adapter()
    adapter._running = True
    mock_node = MagicMock()
    mock_node.destroy_node.side_effect = RuntimeError("Node already destroyed")
    adapter._node = mock_node

    await adapter.stop()
    assert not adapter.is_running
    assert adapter._node is None


def test_ros2_handle_odometry_exception():
    """Verify handle_odometry traps exceptions when processing bad structures."""
    adapter = ROS2Adapter()

    class BadMsg:
        @property
        def pose(self):
            raise RuntimeError("Corrupt memory buffer")

    # Should not raise
    adapter.handle_odometry(BadMsg())


def test_ros2_handle_nav_sat_fix_exception():
    """Verify handle_nav_sat_fix catches exceptions during frame construction."""
    adapter = ROS2Adapter()

    class BrokenFix:
        latitude = 35.0
        longitude = -78.0
        status = _MockNavSatStatus(0)
        position_covariance = [0.0] * 9

        @property
        def altitude(self):
            raise RuntimeError("Hardware ADC fault")

    assert adapter.handle_nav_sat_fix(BrokenFix()) is None


def test_ros2_enqueue_frame_queue_empty_branch(monkeypatch):
    """Verify _enqueue_frame handles QueueEmpty if queue is drained concurrently."""
    adapter = ROS2Adapter(queue_size=1)
    fix1 = _MockNavSatFix(latitude=35.0, longitude=-78.0)
    fix2 = _MockNavSatFix(latitude=35.1, longitude=-78.1)

    adapter.handle_nav_sat_fix(fix1)
    # Monkeypatch get_nowait to raise QueueEmpty on eviction attempt
    orig_get = adapter._queue.get_nowait

    def fake_get_nowait():
        # Empty it so put_nowait still succeeds
        orig_get()
        raise asyncio.QueueEmpty()

    monkeypatch.setattr(adapter._queue, "get_nowait", fake_get_nowait)
    # Enqueue should still handle and put new frame
    adapter.handle_nav_sat_fix(fix2)
    assert adapter._queue.qsize() == 1


@pytest.mark.asyncio
async def test_ros2_stream_autostarts():
    """Verify stream() auto-invokes start() if adapter is not running."""
    adapter = ROS2Adapter()
    assert not adapter.is_running

    fix = _MockNavSatFix(latitude=35.0, longitude=-78.0)
    adapter.handle_nav_sat_fix(fix)

    frames = []
    async for frame in adapter.stream():
        frames.append(frame)
        break

    await adapter.stop()
    assert len(frames) == 1


@pytest.mark.asyncio
async def test_ros2_stream_timeout_continue():
    """Verify stream() handles queue timeout and continues iteration."""
    adapter = ROS2Adapter()
    await adapter.start()

    async def _drain():
        async for _ in adapter.stream():
            break

    task = asyncio.create_task(_drain())
    # Wait long enough for wait_for(timeout=0.2) to trigger TimeoutError
    await asyncio.sleep(0.25)
    adapter.handle_nav_sat_fix(_MockNavSatFix(latitude=35.0, longitude=-78.0))
    await task
    await adapter.stop()
