"""MAVLink Telemetry Ingestion Adapter.

Ingests binary MAVLink 2.0 streams from uncrewed aerial systems (UAVs)
and autopilots (PX4, ArduPilot) over UDP, TCP, or serial connections using pymavlink.
Normalizes incoming frames into canonical TelemetryFrames.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pymavlink import mavutil
from pymavlink.dialects.v20 import common as mavlink2

from tactical_edge_gateway.core.models import (
    AttitudeVector,
    GeodeticPosition,
    PlatformHealth,
    TelemetryFrame,
    VehicleDomain,
)
from tactical_edge_gateway.core.queue import EvictingQueue
from tactical_edge_gateway.ingest.base import BaseTelemetryAdapter

logger = logging.getLogger(__name__)

# Common MAVLink vehicle type mapping to VehicleDomain
_DOMAIN_MAP: dict[int, VehicleDomain] = {
    mavlink2.MAV_TYPE_GROUND_ROVER: VehicleDomain.GROUND,
    mavlink2.MAV_TYPE_SURFACE_BOAT: VehicleDomain.SURFACE,
    mavlink2.MAV_TYPE_SUBMARINE: VehicleDomain.SUBSURFACE,
}


@dataclass
class _PlatformState:
    """Internal cache aggregating multi-packet telemetry for a single MAVLink system."""

    domain: VehicleDomain = VehicleDomain.AIR
    position: GeodeticPosition | None = None
    attitude: AttitudeVector | None = None
    health: PlatformHealth | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class MavlinkAdapter(BaseTelemetryAdapter):
    """Adapter ingesting MAVLink telemetry streams into canonical TelemetryFrames."""

    def __init__(
        self,
        connection_string: str = "udpin:0.0.0.0:14550",
        callsign_prefix: str = "UAV",
        default_domain: VehicleDomain = VehicleDomain.AIR,
        *,
        connection: Any | None = None,
        queue_size: int = 1000,
    ) -> None:
        """Initializes the MAVLink telemetry adapter.

        Args:
            connection_string: Connection string for pymavlink (e.g. udpin:0.0.0.0:14550).
            callsign_prefix: Prefix used for tactical callsigns (e.g. UAV-01).
            default_domain: Default domain if vehicle type is not determined.
            connection: Optional pre-configured mavutil connection for dependency injection.
            queue_size: Maximum capacity of the internal ingest queue.
        """
        super().__init__(name=f"mavlink-{connection_string}")
        self.connection_string = connection_string
        self.callsign_prefix = callsign_prefix
        self.default_domain = default_domain
        self._connection = connection
        self._owns_connection = connection is None
        self._platform_states: dict[int, _PlatformState] = {}
        self._queue: EvictingQueue[TelemetryFrame] = EvictingQueue(maxsize=queue_size)
        self._worker_task: asyncio.Task[None] | None = None

    def get_platform_state(self, sys_id: int) -> _PlatformState:
        """Returns or creates the telemetry state cache for a specific system ID."""
        if sys_id not in self._platform_states:
            self._platform_states[sys_id] = _PlatformState(domain=self.default_domain)
        return self._platform_states[sys_id]

    def parse_message(self, msg: Any) -> TelemetryFrame | None:
        """Parses a single MAVLink message, updating state and returning a frame if complete.

        Args:
            msg: Decoded MAVLink message object.

        Returns:
            TelemetryFrame if the message contained complete kinematic coordinates, else None.
        """
        if msg is None:
            return None

        msg_type = msg.get_type() if hasattr(msg, "get_type") else getattr(msg, "_type", None)
        if not msg_type:
            return None

        sys_id = (
            msg.get_srcSystem() if hasattr(msg, "get_srcSystem") else getattr(msg, "srcSystem", 1)
        )
        if sys_id <= 0:
            return None

        state = self.get_platform_state(sys_id)
        state.last_seen = datetime.now(timezone.utc)

        match msg_type:
            case "HEARTBEAT":
                self._handle_heartbeat(msg, state)
                return None

            case "ATTITUDE":
                self._handle_attitude(msg, state)
                return None

            case "SYS_STATUS":
                self._handle_sys_status(msg, state)
                return None

            case "GLOBAL_POSITION_INT":
                return self._handle_global_position(msg, sys_id, state)

            case _:
                return None

    def _handle_heartbeat(self, msg: Any, state: _PlatformState) -> None:
        """Processes HEARTBEAT to resolve vehicle domain, armed state, and autopilot metadata."""
        v_type = getattr(msg, "type", None)
        if v_type is not None:
            state.domain = _DOMAIN_MAP.get(v_type, VehicleDomain.AIR)

        base_mode = getattr(msg, "base_mode", 0)
        is_armed = bool(base_mode & mavlink2.MAV_MODE_FLAG_SAFETY_ARMED)

        current_health = state.health
        state.health = PlatformHealth(
            battery_percent=current_health.battery_percent if current_health else None,
            battery_voltage_v=current_health.battery_voltage_v if current_health else None,
            link_quality_percent=current_health.link_quality_percent if current_health else None,
            is_armed=is_armed,
            error_codes=current_health.error_codes if current_health else (),
        )
        state.metadata["autopilot"] = getattr(msg, "autopilot", 0)
        state.metadata["system_status"] = getattr(msg, "system_status", 0)

    def _handle_attitude(self, msg: Any, state: _PlatformState) -> None:
        """Processes ATTITUDE to update Tait-Bryan platform orientation."""
        raw_roll = math.degrees(getattr(msg, "roll", 0.0))
        raw_pitch = math.degrees(getattr(msg, "pitch", 0.0))
        raw_yaw = math.degrees(getattr(msg, "yaw", 0.0))

        # Clamp and normalize to model boundaries
        roll_deg = max(-180.0, min(180.0, raw_roll))
        pitch_deg = max(-90.0, min(90.0, raw_pitch))
        yaw_deg = (raw_yaw + 360.0) % 360.0

        state.attitude = AttitudeVector(
            roll_deg=round(roll_deg, 2),
            pitch_deg=round(pitch_deg, 2),
            yaw_deg=round(yaw_deg, 2),
        )

    def _handle_sys_status(self, msg: Any, state: _PlatformState) -> None:
        """Processes SYS_STATUS to update battery metrics and link health."""
        raw_batt_pct = getattr(msg, "battery_remaining", -1)
        batt_pct = float(raw_batt_pct) if (0 <= raw_batt_pct <= 100) else None

        raw_voltage_mv = getattr(msg, "voltage_battery", 0)
        voltage_v = (raw_voltage_mv / 1000.0) if (0 < raw_voltage_mv < 65535) else None

        raw_drop_comm = getattr(msg, "drop_rate_comm", -1)
        link_quality = None
        if raw_drop_comm >= 0:
            link_quality = max(0.0, min(100.0, 100.0 - (raw_drop_comm / 100.0)))

        current_health = state.health
        is_armed = current_health.is_armed if current_health else None
        errors = current_health.error_codes if current_health else ()

        err1 = getattr(msg, "errors_count1", 0)
        if err1 > 0 and str(err1) not in errors:
            errors = errors + (f"HW_ERR_{err1}",)

        state.health = PlatformHealth(
            battery_percent=batt_pct,
            battery_voltage_v=voltage_v,
            link_quality_percent=link_quality,
            is_armed=is_armed,
            error_codes=errors,
        )

    def _handle_global_position(
        self, msg: Any, sys_id: int, state: _PlatformState
    ) -> TelemetryFrame | None:
        """Processes GLOBAL_POSITION_INT to construct and emit a TelemetryFrame."""
        lat = getattr(msg, "lat", 0) / 1e7
        lon = getattr(msg, "lon", 0) / 1e7

        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            logger.warning(
                "Dropping MAVLink GLOBAL_POSITION_INT with invalid coordinates: lat=%s lon=%s",
                lat,
                lon,
            )
            return None

        alt_msl_m = getattr(msg, "alt", 0) / 1000.0
        alt_hae_m = getattr(msg, "relative_alt", 0) / 1000.0

        vx = getattr(msg, "vx", 0)
        vy = getattr(msg, "vy", 0)
        speed_mps = round(math.hypot(vx, vy) / 100.0, 2)

        raw_hdg = getattr(msg, "hdg", 65535)
        heading_deg = round(raw_hdg / 100.0, 2) if (0 <= raw_hdg <= 36000) else None

        position = GeodeticPosition(
            latitude=round(lat, 7),
            longitude=round(lon, 7),
            altitude_msl_m=round(alt_msl_m, 2),
            altitude_hae_m=round(alt_hae_m, 2),
            heading_deg=heading_deg,
            speed_mps=speed_mps,
        )
        state.position = position

        frame = TelemetryFrame(
            uid=f"mavlink-{sys_id}",
            callsign=f"{self.callsign_prefix}-{sys_id:02d}",
            domain=state.domain,
            timestamp=datetime.now(timezone.utc),
            position=position,
            attitude=state.attitude,
            health=state.health,
            metadata={
                "sys_id": sys_id,
                "comp_id": getattr(msg, "comp_id", 1),
                "msg_type": "GLOBAL_POSITION_INT",
                **state.metadata,
            },
        )
        return frame

    async def _read_loop(self) -> None:
        """Asynchronous reader coroutine pulling MAVLink frames and placing into queue."""
        while self._running:
            try:
                msg = await asyncio.to_thread(self._fetch_next_message)
                if msg is not None:
                    frame = self.parse_message(msg)
                    if frame is not None:
                        self._queue.put_nowait_evict(frame)
                else:
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in MAVLink receive loop: %s", e)
                await asyncio.sleep(0.1)

    def _fetch_next_message(self) -> Any:
        """Synchronous wrapper to receive a single message from pymavlink."""
        if not self._connection or not self._running:
            return None
        try:
            return self._connection.recv_match(blocking=True, timeout=0.1)
        except Exception:
            return None

    async def _on_start(self) -> None:
        """Connects to the MAVLink stream and initiates background reader."""
        if self._connection is None and self._owns_connection:
            logger.info("Opening MAVLink connection to %s", self.connection_string)
            self._connection = mavutil.mavlink_connection(
                self.connection_string,
                autoreconnect=True,
            )

        self._worker_task = asyncio.create_task(self._read_loop())

    async def _on_stop(self) -> None:
        """Terminates background reader and closes underlying connection."""
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

        if self._owns_connection and self._connection is not None:
            try:
                self._connection.close()
            except Exception as e:
                logger.debug("Exception closing MAVLink connection: %s", e)
            self._connection = None

    async def stream(self) -> AsyncIterator[TelemetryFrame]:
        """Asynchronously yields normalized TelemetryFrames as they arrive.

        Yields:
            TelemetryFrame: Next validated telemetry frame.
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
