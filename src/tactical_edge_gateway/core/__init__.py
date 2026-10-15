"""Core data abstractions and models for Tactical Edge Gateway."""

from tactical_edge_gateway.core.engine import EngineStats, GatewayEngine
from tactical_edge_gateway.core.models import (
    AttitudeVector,
    GeodeticPosition,
    PlatformHealth,
    TelemetryFrame,
    VehicleDomain,
)

__all__ = [
    "AttitudeVector",
    "EngineStats",
    "GatewayEngine",
    "GeodeticPosition",
    "PlatformHealth",
    "TelemetryFrame",
    "VehicleDomain",
]
