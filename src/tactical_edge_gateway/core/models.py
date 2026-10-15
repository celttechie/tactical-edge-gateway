"""Tactical Edge Gateway - Canonical Telemetry Models.

Defines normalized, strongly typed Pydantic data schemas representing vehicle
kinematics, geodetic position, orientation, and platform health across
heterogeneous autonomy protocols (MAVLink and ROS2).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VehicleDomain(str, Enum):
    """Operational vehicle domain categorization."""

    AIR = "air"
    GROUND = "ground"
    SURFACE = "surface"
    SUBSURFACE = "subsurface"


class GeodeticPosition(BaseModel):
    """WGS-84 geodetic position and kinematic velocity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    latitude: float = Field(
        ...,
        ge=-90.0,
        le=90.0,
        description="WGS-84 latitude in decimal degrees (-90.0 to 90.0)",
    )
    longitude: float = Field(
        ...,
        ge=-180.0,
        le=180.0,
        description="WGS-84 longitude in decimal degrees (-180.0 to 180.0)",
    )
    altitude_hae_m: float | None = Field(
        default=None,
        description="Height Above Ellipsoid (HAE) in meters",
    )
    altitude_msl_m: float | None = Field(
        default=None,
        description="Altitude above Mean Sea Level (MSL) in meters",
    )
    heading_deg: float | None = Field(
        default=None,
        ge=0.0,
        le=360.0,
        description="True heading in degrees clockwise from North (0.0 to 360.0)",
    )
    speed_mps: float | None = Field(
        default=None,
        ge=0.0,
        description="Ground speed in meters per second (>= 0.0)",
    )
    circular_error_m: float = Field(
        default=10.0,
        ge=0.0,
        description="Circular error (horizontal 1-sigma uncertainty) in meters",
    )
    linear_error_m: float = Field(
        default=10.0,
        ge=0.0,
        description="Linear error (vertical 1-sigma uncertainty) in meters",
    )


class AttitudeVector(BaseModel):
    """Platform attitude and angular orientation in aerospace Tait-Bryan coordinates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    roll_deg: float = Field(
        ...,
        ge=-180.0,
        le=180.0,
        description="Roll angle in degrees (-180.0 to 180.0)",
    )
    pitch_deg: float = Field(
        ...,
        ge=-90.0,
        le=90.0,
        description="Pitch angle in degrees (-90.0 to 90.0)",
    )
    yaw_deg: float = Field(
        ...,
        ge=0.0,
        le=360.0,
        description="Yaw angle in degrees clockwise from North (0.0 to 360.0)",
    )

    @classmethod
    def from_unclamped_degrees(
        cls,
        roll: float,
        pitch: float,
        yaw: float,
        round_digits: int | None = None,
    ) -> AttitudeVector:
        """Constructs an AttitudeVector by safely clamping and normalizing raw degrees.

        Clamps roll to [-180.0, 180.0], pitch to [-90.0, 90.0], and wraps
        yaw modulo 360.0 into [0.0, 360.0].

        Args:
            roll: Raw roll angle in degrees.
            pitch: Raw pitch angle in degrees.
            yaw: Raw yaw / azimuth angle in degrees.
            round_digits: Optional decimal places to round output coordinates.

        Returns:
            Normalized AttitudeVector adhering to Tait-Bryan boundary constraints.
        """
        r = max(-180.0, min(180.0, float(roll)))
        p = max(-90.0, min(90.0, float(pitch)))
        y = max(0.0, min(360.0, float(yaw) % 360.0))

        if round_digits is not None:
            r = round(r, round_digits)
            p = round(p, round_digits)
            y = round(y, round_digits)
            r = max(-180.0, min(180.0, r))
            p = max(-90.0, min(90.0, p))
            y = max(0.0, min(360.0, y))

        return cls(roll_deg=r, pitch_deg=p, yaw_deg=y)


class PlatformHealth(BaseModel):
    """Platform subsystem telemetry, battery, and radio metrics."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    battery_percent: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Battery charge state remaining percentage (0.0 to 100.0)",
    )
    battery_voltage_v: float | None = Field(
        default=None,
        ge=0.0,
        description="Battery bus voltage in volts",
    )
    link_quality_percent: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Tactical radio / telemetry link quality percentage (0.0 to 100.0)",
    )
    is_armed: bool | None = Field(
        default=None,
        description="Vehicle motor / safety interlock armed state",
    )
    error_codes: tuple[str, ...] = Field(
        default=(),
        description="Active diagnostic trouble codes or subsystem alerts",
    )


class TelemetryFrame(BaseModel):
    """Canonical normalized telemetry frame.

    Serves as the internal universal lingua franca of the gateway, bridging
    raw autonomy wire messages to downstream Cursor on Target (CoT) and buffer queues.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    uid: str = Field(
        ...,
        min_length=1,
        description="Deterministic platform identifier (e.g., 'uav-raptor-01')",
    )
    callsign: str = Field(
        ...,
        min_length=1,
        description="Human-readable tactical callsign (e.g., 'RAPTOR-01')",
    )
    domain: VehicleDomain = Field(
        ...,
        description="Operational physical domain (air, ground, surface, subsurface)",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Observation timestamp in UTC",
    )
    position: GeodeticPosition = Field(
        ...,
        description="Geodetic 3D coordinate and kinematics",
    )
    attitude: AttitudeVector | None = Field(
        default=None,
        description="Optional platform orientation vector",
    )
    health: PlatformHealth | None = Field(
        default=None,
        description="Optional subsystem battery and link metrics",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Protocol-specific raw telemetry attributes",
    )

    @field_validator("timestamp")
    @classmethod
    def ensure_utc_timezone(cls, v: datetime) -> datetime:
        """Enforces UTC timezone awareness on timestamp values."""
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    def default_cot_type(self) -> str:
        """Computes MIL-STD-2525 CoT type indicator based on vehicle domain.

        Returns:
            Standard 2525 Cursor on Target 15-character hierarchy string.
        """
        match self.domain:
            case VehicleDomain.AIR:
                return "a-f-A-M-F"  # Friendly Airborne Military Drone / Multi-role
            case VehicleDomain.GROUND:
                return "a-f-G-E-V-U"  # Friendly Ground Unmanned Vehicle
            case VehicleDomain.SURFACE:
                return "a-f-S-X-U"  # Friendly Surface Unmanned Craft
            case VehicleDomain.SUBSURFACE:
                return "a-f-U-U-U"  # Friendly Subsurface Unmanned Craft
