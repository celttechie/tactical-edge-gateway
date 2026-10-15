"""ROS2 / DDS Telemetry Ingestion Adapter.

Subscribes to standard ROS2 robotics topics (`sensor_msgs/NavSatFix` for GPS coordinates
and `nav_msgs/Odometry` for kinematic pose and velocity) to normalize tactical ground
unmanned vehicle (UGV) telemetry into canonical TelemetryFrames.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from tactical_edge_gateway.core.models import (
    AttitudeVector,
    GeodeticPosition,
    PlatformHealth,
    TelemetryFrame,
    VehicleDomain,
)
from tactical_edge_gateway.ingest.base import BaseTelemetryAdapter

logger = logging.getLogger(__name__)

# Check if rclpy is installed in the current environment
try:  # pragma: no cover
    import rclpy  # type: ignore[import-untyped]
    from rclpy.node import Node  # type: ignore[import-untyped]

    RCLPY_AVAILABLE = True
except ImportError:
    rclpy = None  # type: ignore[assignment]
    Node = object  # type: ignore[assignment, misc]
    RCLPY_AVAILABLE = False


def quaternion_to_euler_deg(x: float, y: float, z: float, w: float) -> tuple[float, float, float]:
    """Converts a quaternion (x, y, z, w) into Euler angles (roll, pitch, yaw) in degrees.

    Follows Tait-Bryan angles (Z-Y-X rotation convention).
    Output yaw is normalized to [0, 360] degrees.
    """
    # Roll (x-axis rotation)
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll_rad = math.atan2(sinr_cosp, cosr_cosp)

    # Pitch (y-axis rotation)
    sinp = 2.0 * (w * y - z * x)
    # Clamp to prevent NaN from slight numerical overflow in asin
    sinp = max(-1.0, min(1.0, sinp))
    pitch_rad = math.asin(sinp)

    # Yaw (z-axis rotation)
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw_rad = math.atan2(siny_cosp, cosy_cosp)

    roll_deg = math.degrees(roll_rad)
    pitch_deg = math.degrees(pitch_rad)
    yaw_deg = (math.degrees(yaw_rad) + 360.0) % 360.0

    # Ensure bounds enforcement matches schema
    roll_deg = max(-180.0, min(180.0, roll_deg))
    pitch_deg = max(-90.0, min(90.0, pitch_deg))
    yaw_deg = max(0.0, min(360.0, yaw_deg))

    return roll_deg, pitch_deg, yaw_deg


class ROS2Adapter(BaseTelemetryAdapter):
    """Adapter ingesting ROS2 NavSatFix and Odometry messages into TelemetryFrames."""

    def __init__(
        self,
        uid: str = "ugv-01",
        callsign: str = "WARTHOG-01",
        domain: VehicleDomain = VehicleDomain.GROUND,
        fix_topic: str = "/fix",
        odom_topic: str = "/odom",
        *,
        node_name: str = "tactical_edge_gateway_node",
        queue_size: int = 500,
    ) -> None:
        """Initializes the ROS2 telemetry adapter.

        Args:
            uid: Unique platform identifier (e.g. ugv-01).
            callsign: Tactical callsign (e.g. WARTHOG-01).
            domain: Vehicle domain (defaults to GROUND).
            fix_topic: ROS2 topic for sensor_msgs/NavSatFix.
            odom_topic: ROS2 topic for nav_msgs/Odometry.
            node_name: ROS2 node identifier.
            queue_size: Max queued frames under downstream backpressure.
        """
        super().__init__(name=f"ros2-{uid}")
        self.uid = uid
        self.callsign = callsign
        self.domain = domain
        self.fix_topic = fix_topic
        self.odom_topic = odom_topic
        self.node_name = node_name

        self._queue: asyncio.Queue[TelemetryFrame] = asyncio.Queue(maxsize=queue_size)
        self._current_attitude: AttitudeVector | None = None
        self._current_speed_mps: float | None = None
        self._current_heading_deg: float | None = None
        self._node: Any | None = None
        self._ros_thread_task: asyncio.Task[None] | None = None

    @property
    def is_rclpy_available(self) -> bool:
        """Indicates whether the rclpy ROS2 runtime is installed."""
        return RCLPY_AVAILABLE

    def handle_odometry(self, msg: Any) -> None:
        """Extracts orientation quaternion and linear velocity from a nav_msgs/Odometry.

        Args:
            msg: nav_msgs/Odometry object or mock containing pose and twist.
        """
        try:
            # 1. Orientation quaternion -> AttitudeVector
            pose = getattr(msg, "pose", None)
            pose_data = getattr(pose, "pose", pose)
            orientation = getattr(pose_data, "orientation", None)

            if orientation is not None:
                qx = float(getattr(orientation, "x", 0.0))
                qy = float(getattr(orientation, "y", 0.0))
                qz = float(getattr(orientation, "z", 0.0))
                qw = float(getattr(orientation, "w", 1.0))

                roll_deg, pitch_deg, yaw_deg = quaternion_to_euler_deg(qx, qy, qz, qw)
                self._current_attitude = AttitudeVector(
                    roll_deg=roll_deg,
                    pitch_deg=pitch_deg,
                    yaw_deg=yaw_deg,
                )
                self._current_heading_deg = yaw_deg

            # 2. Twist linear velocity -> speed_mps
            twist = getattr(msg, "twist", None)
            twist_data = getattr(twist, "twist", twist)
            linear = getattr(twist_data, "linear", None)

            if linear is not None:
                vx = float(getattr(linear, "x", 0.0))
                vy = float(getattr(linear, "y", 0.0))
                self._current_speed_mps = math.sqrt(vx**2 + vy**2)

        except Exception as e:
            logger.debug("Error processing ROS2 Odometry message: %s", e)

    def handle_nav_sat_fix(self, msg: Any) -> TelemetryFrame | None:
        """Parses sensor_msgs/NavSatFix, aggregates kinematics, and enqueues TelemetryFrame.

        Args:
            msg: sensor_msgs/NavSatFix object or mock containing coordinates.

        Returns:
            TelemetryFrame if coordinate fix was valid, None otherwise.
        """
        if msg is None or not hasattr(msg, "latitude") or not hasattr(msg, "longitude"):
            return None

        try:
            # Check GPS status if present (status < 0 indicates STATUS_NO_FIX)
            status_obj = getattr(msg, "status", None)
            status_val = getattr(status_obj, "status", 0)
            if status_val < 0:
                logger.debug("Discarding NavSatFix with STATUS_NO_FIX (%d)", status_val)
                return None

            lat = float(msg.latitude)
            lon = float(msg.longitude)
            alt = float(getattr(msg, "altitude", 0.0))

            # Validate basic latitude/longitude ranges
            if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                logger.warning("Invalid coordinates in NavSatFix: lat=%f, lon=%f", lat, lon)
                return None

            # Calculate horizontal accuracy / circular error from covariance matrix
            cov = getattr(msg, "position_covariance", None)
            ce = 10.0
            if cov is not None and len(cov) >= 5:
                var_x = float(cov[0])
                var_y = float(cov[4])
                if var_x > 0 and var_y > 0:
                    ce = math.sqrt(var_x + var_y)

            # Construct GeodeticPosition with current kinematics
            pos = GeodeticPosition(
                latitude=lat,
                longitude=lon,
                altitude_hae_m=alt,
                altitude_msl_m=alt,
                heading_deg=self._current_heading_deg,
                speed_mps=self._current_speed_mps,
                circular_error_m=ce,
                linear_error_m=10.0,
            )

            frame = TelemetryFrame(
                uid=self.uid,
                callsign=self.callsign,
                domain=self.domain,
                timestamp=datetime.now(timezone.utc),
                position=pos,
                attitude=self._current_attitude,
                health=PlatformHealth(is_armed=True),
                metadata={"source": "ros2_dds", "topic": self.fix_topic},
            )

            self._enqueue_frame(frame)
            return frame

        except Exception as e:
            logger.error("Error processing ROS2 NavSatFix message: %s", e)
            return None

    def _enqueue_frame(self, frame: TelemetryFrame) -> None:
        """Enqueues frame with FIFO backpressure protection."""
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._queue.put_nowait(frame)

    async def start(self) -> None:
        """Initiates ROS2 node and subscriptions if rclpy is installed."""
        if self._running:
            return

        self._running = True

        if RCLPY_AVAILABLE and rclpy is not None:
            try:
                if not rclpy.ok():
                    rclpy.init()

                from nav_msgs.msg import Odometry  # type: ignore[import-untyped]
                from sensor_msgs.msg import NavSatFix  # type: ignore[import-untyped]

                node = rclpy.create_node(self.node_name)
                self._node = node
                if node is not None:
                    node.create_subscription(
                        NavSatFix,
                        self.fix_topic,
                        self.handle_nav_sat_fix,
                        10,
                    )
                    node.create_subscription(
                        Odometry,
                        self.odom_topic,
                        self.handle_odometry,
                        10,
                    )
                    self._ros_thread_task = asyncio.create_task(asyncio.to_thread(rclpy.spin, node))
                logger.info(
                    "ROS2 node %s started subscribing to %s and %s",
                    self.node_name,
                    self.fix_topic,
                    self.odom_topic,
                )
            except Exception as e:
                logger.warning(
                    "Failed to initialize live ROS2 subscriptions: %s. "
                    "Running in simulated / callback-driven mode.",
                    e,
                )
        else:
            logger.info(
                "ROS2 adapter %s initialized in standalone/callback mode (rclpy not installed)",
                self.name,
            )

    async def stop(self) -> None:
        """Shuts down active ROS2 node and worker task."""
        self._running = False

        if self._node is not None:
            try:
                self._node.destroy_node()
            except Exception as e:
                logger.debug("Error destroying ROS2 node: %s", e)
            self._node = None

        if self._ros_thread_task is not None:
            self._ros_thread_task.cancel()
            try:
                await self._ros_thread_task
            except asyncio.CancelledError:
                pass
            self._ros_thread_task = None

        logger.info("ROS2 adapter %s stopped", self.name)

    async def stream(self) -> AsyncIterator[TelemetryFrame]:
        """Asynchronously emits normalized TelemetryFrame instances as they arrive.

        Yields:
            TelemetryFrame: Validated ground autonomy telemetry frame.
        """
        if not self._running:
            await self.start()

        while self._running:
            try:
                frame = await asyncio.wait_for(self._queue.get(), timeout=0.2)
                yield frame
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
