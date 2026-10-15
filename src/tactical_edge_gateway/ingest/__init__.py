"""Telemetry ingestion adapters and streaming interfaces."""

from tactical_edge_gateway.ingest.base import BaseTelemetryAdapter
from tactical_edge_gateway.ingest.mavlink import MavlinkAdapter
from tactical_edge_gateway.ingest.ros2 import ROS2Adapter, quaternion_to_euler_deg
from tactical_edge_gateway.ingest.synthetic import SyntheticAdapter

__all__ = [
    "BaseTelemetryAdapter",
    "MavlinkAdapter",
    "ROS2Adapter",
    "SyntheticAdapter",
    "quaternion_to_euler_deg",
]
