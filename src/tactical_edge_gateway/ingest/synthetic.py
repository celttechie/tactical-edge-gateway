"""Synthetic Telemetry Ingestion Adapter.

Generates realistic geodetic orbital paths and kinematic kinematics for UAVs
and UGVs. Provides deterministic, headless testing in CI and air-gapped testbeds
without requiring live physical hardware or external simulation daemons.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from tactical_edge_gateway.core.models import (
    AttitudeVector,
    GeodeticPosition,
    PlatformHealth,
    TelemetryFrame,
    VehicleDomain,
)
from tactical_edge_gateway.ingest.base import BaseTelemetryAdapter


class SyntheticAdapter(BaseTelemetryAdapter):
    """Generates synthetic telemetry along circular orbital or ground routes."""

    # Approximate meters per degree of latitude on WGS-84
    METERS_PER_DEG_LAT = 111_139.0

    def __init__(
        self,
        uid: str = "sim-uav-01",
        callsign: str = "VALKYRIE-01",
        domain: VehicleDomain = VehicleDomain.AIR,
        origin_lat: float = 35.1415,
        origin_lon: float = -79.0069,
        altitude_msl_m: float = 120.0,
        speed_mps: float = 15.0,
        orbit_radius_m: float = 400.0,
        rate_hz: float = 1.0,
        max_iterations: int | None = None,
    ) -> None:
        """Initializes the synthetic vehicle trajectory generator.

        Args:
            uid: Unique platform ID.
            callsign: Tactical callsign for C2 displays.
            domain: Vehicle domain (AIR, GROUND, etc.).
            origin_lat: Orbit center latitude (WGS-84 decimal degrees).
            origin_lon: Orbit center longitude (WGS-84 decimal degrees).
            altitude_msl_m: Target altitude above MSL in meters.
            speed_mps: Tangential ground speed in meters/second.
            orbit_radius_m: Orbit radius in meters.
            rate_hz: Telemetry update emission frequency in Hertz.
            max_iterations: Maximum frames to yield before terminating, or None for infinite.
        """
        super().__init__(name=f"synthetic-{uid}")
        self.uid = uid
        self.callsign = callsign
        self.domain = domain
        self.origin_lat = origin_lat
        self.origin_lon = origin_lon
        self.altitude_msl_m = altitude_msl_m
        self.speed_mps = speed_mps
        self.orbit_radius_m = orbit_radius_m
        self.rate_hz = rate_hz
        self.max_iterations = max_iterations

        self._theta_rad = 0.0
        self._iteration_count = 0
        self._battery_pct = 100.0

    def next_frame(self) -> TelemetryFrame:
        """Computes the next deterministic kinematic frame along the trajectory.

        Returns:
            TelemetryFrame: Populated frame with updated position, heading, and health.
        """
        # Angular velocity omega = v / r
        omega = self.speed_mps / max(self.orbit_radius_m, 1.0)
        dt = 1.0 / max(self.rate_hz, 0.001)

        # Update angle
        self._theta_rad = (self._theta_rad + (omega * dt)) % (2.0 * math.pi)

        # Coordinate offsets in meters
        dx_meters = self.orbit_radius_m * math.cos(self._theta_rad)
        dy_meters = self.orbit_radius_m * math.sin(self._theta_rad)

        # Convert meter offsets to geodetic degrees
        lat_offset = dy_meters / self.METERS_PER_DEG_LAT
        meters_per_deg_lon = self.METERS_PER_DEG_LAT * math.cos(math.radians(self.origin_lat))
        lon_offset = dx_meters / max(meters_per_deg_lon, 1.0)

        current_lat = self.origin_lat + lat_offset
        current_lon = self.origin_lon + lon_offset

        # Tangent heading clockwise from North (degrees)
        # Vector is (-sin(theta), cos(theta))
        heading_rad = math.atan2(-math.sin(self._theta_rad), math.cos(self._theta_rad))
        heading_deg = (math.degrees(heading_rad) + 360.0) % 360.0

        # Calculate coordinated bank angle (roll) for aircraft
        if self.domain == VehicleDomain.AIR:
            centripetal_accel = (self.speed_mps**2) / max(self.orbit_radius_m, 1.0)
            roll_deg = math.degrees(math.atan(centripetal_accel / 9.80665))
        else:
            roll_deg = 0.0

        # Battery slowly discharges
        self._battery_pct = max(0.0, self._battery_pct - 0.01)

        position = GeodeticPosition(
            latitude=round(current_lat, 7),
            longitude=round(current_lon, 7),
            altitude_msl_m=self.altitude_msl_m,
            altitude_hae_m=self.altitude_msl_m - 30.0,  # approximate geoid separation
            heading_deg=round(heading_deg, 1),
            speed_mps=self.speed_mps,
            circular_error_m=2.5,
            linear_error_m=3.0,
        )

        attitude = AttitudeVector(
            roll_deg=round(roll_deg, 2),
            pitch_deg=1.2,
            yaw_deg=round(heading_deg, 1),
        )

        health = PlatformHealth(
            battery_percent=round(self._battery_pct, 1),
            battery_voltage_v=round(22.2 * (self._battery_pct / 100.0), 2),
            link_quality_percent=98.5,
            is_armed=True,
            error_codes=(),
        )

        frame = TelemetryFrame(
            uid=self.uid,
            callsign=self.callsign,
            domain=self.domain,
            timestamp=datetime.now(timezone.utc),
            position=position,
            attitude=attitude,
            health=health,
            metadata={"adapter": "SyntheticAdapter", "iteration": self._iteration_count},
        )

        self._iteration_count += 1
        self._stats.frames_received += 1
        self._stats.frames_parsed += 1
        self._stats.last_received_at = datetime.now(timezone.utc)
        return frame

    async def stream(self) -> AsyncIterator[TelemetryFrame]:
        """Asynchronously streams generated frames at the configured update rate."""
        if not self._running:
            await self.start()

        sleep_interval = 1.0 / max(self.rate_hz, 0.001)

        while self._running:
            if self.max_iterations is not None and self._iteration_count >= self.max_iterations:
                break

            yield self.next_frame()

            if self.rate_hz > 0:
                await asyncio.sleep(sleep_interval)
