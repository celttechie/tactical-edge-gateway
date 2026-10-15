"""Cursor on Target (CoT) XML Transformation Engine.

Converts canonical TelemetryFrame instances into standardized MITRE Cursor on Target
(CoT) XML conforming to MIL-STD-2525 symbology standards for consumption by ATAK,
WinTAK, and TAK Server (FreeTAKServer).
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET  # nosec B405 - used strictly for XML generation, not parsing
from datetime import datetime, timedelta, timezone
from typing import Any

import xmltodict

from tactical_edge_gateway.core.models import (
    AttitudeVector,
    GeodeticPosition,
    PlatformHealth,
    TelemetryFrame,
    VehicleDomain,
)

logger = logging.getLogger(__name__)

# Default CoT timing configuration
DEFAULT_STALE_DURATION_S = 30.0
DEFAULT_HOW = "m-g"  # Machine-generated, GPS-derived
DEFAULT_CE = 10.0  # Default circular error in meters
DEFAULT_LE = 10.0  # Default linear error in meters

# Domain mapping from CoT types (e.g. a-f-A-M-F -> AIR)
_COT_TYPE_DOMAIN_MAP: dict[str, VehicleDomain] = {
    "a-f-A": VehicleDomain.AIR,
    "a-f-G": VehicleDomain.GROUND,
    "a-f-S": VehicleDomain.SURFACE,
    "a-f-U": VehicleDomain.SUBSURFACE,
}


class CoTValidationError(ValueError):
    """Raised when CoT XML is structurally invalid or missing required attributes."""


def format_cot_timestamp(dt: datetime) -> str:
    """Formats datetime as ISO-8601 UTC timestamp expected by TAK clients.

    Output format: YYYY-MM-DDTHH:MM:SS.sssZ
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    # Format to millisecond precision with explicit Z suffix
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_cot_timestamp(ts_str: str) -> datetime:
    """Parses an ISO-8601 CoT timestamp string into a UTC datetime."""
    # Normalize Z to +00:00 for fromisoformat compatibility in Python 3.10+
    clean_str = ts_str.strip()
    if clean_str.endswith("Z"):
        clean_str = clean_str[:-1] + "+00:00"
    return datetime.fromisoformat(clean_str).astimezone(timezone.utc)


class CoTTransformer:
    """Transforms TelemetryFrame instances to and from Cursor on Target (CoT) XML."""

    def __init__(
        self,
        default_stale_duration_s: float = DEFAULT_STALE_DURATION_S,
        default_how: str = DEFAULT_HOW,
        gateway_version: str = "0.1.0",
    ) -> None:
        """Initializes the CoT transformer.

        Args:
            default_stale_duration_s: Stale duration in seconds added to event time.
            default_how: CoT data acquisition method identifier (default: m-g).
            gateway_version: Version identifier reported in TAK detail metadata.
        """
        self.default_stale_duration_s = default_stale_duration_s
        self.default_how = default_how
        self.gateway_version = gateway_version

    def transform(
        self,
        frame: TelemetryFrame,
        *,
        cot_type: str | None = None,
        stale_duration_s: float | None = None,
        include_declaration: bool = False,
    ) -> str:
        """Serializes a TelemetryFrame into a Cursor on Target (CoT) XML string.

        Args:
            frame: Canonical TelemetryFrame to transform.
            cot_type: Optional explicit CoT type (e.g. a-f-A-M-F). Defaults to frame's mapping.
            stale_duration_s: Optional custom stale duration in seconds.
            include_declaration: If True, prepends <?xml version="1.0" encoding="UTF-8"?>.

        Returns:
            CoT XML string conforming to MITRE specification.
        """
        event_elem = self._build_event_element(
            frame,
            cot_type=cot_type,
            stale_duration_s=stale_duration_s,
        )

        xml_bytes = ET.tostring(
            event_elem,
            encoding="utf-8",
            xml_declaration=include_declaration,
        )
        return xml_bytes.decode("utf-8")

    def transform_bytes(
        self,
        frame: TelemetryFrame,
        *,
        cot_type: str | None = None,
        stale_duration_s: float | None = None,
        include_declaration: bool = False,
    ) -> bytes:
        """Serializes a TelemetryFrame into UTF-8 encoded Cursor on Target (CoT) XML bytes.

        Args:
            frame: Canonical TelemetryFrame to transform.
            cot_type: Optional explicit CoT type.
            stale_duration_s: Optional custom stale duration in seconds.
            include_declaration: If True, prepends XML header.

        Returns:
            UTF-8 encoded CoT XML bytes ready for network transmission.
        """
        event_elem = self._build_event_element(
            frame,
            cot_type=cot_type,
            stale_duration_s=stale_duration_s,
        )
        return ET.tostring(
            event_elem,
            encoding="utf-8",
            xml_declaration=include_declaration,
        )

    def _build_event_element(
        self,
        frame: TelemetryFrame,
        *,
        cot_type: str | None = None,
        stale_duration_s: float | None = None,
    ) -> ET.Element:
        """Internal helper constructing the xml.etree Element hierarchy."""
        stale_s = (
            stale_duration_s if stale_duration_s is not None else self.default_stale_duration_s
        )
        resolved_type = cot_type or frame.default_cot_type()

        event_time = frame.timestamp
        stale_time = event_time + timedelta(seconds=stale_s)

        time_str = format_cot_timestamp(event_time)
        start_str = time_str
        stale_str = format_cot_timestamp(stale_time)

        # 1. Root <event> element
        event = ET.Element(
            "event",
            {
                "version": "2.0",
                "uid": frame.uid,
                "type": resolved_type,
                "how": self.default_how,
                "time": time_str,
                "start": start_str,
                "stale": stale_str,
            },
        )

        # 2. Child <point> element
        # Resolve HAE: fallback to MSL altitude or 0.0
        hae_val = (
            frame.position.altitude_hae_m
            if frame.position.altitude_hae_m is not None
            else (
                frame.position.altitude_msl_m if frame.position.altitude_msl_m is not None else 0.0
            )
        )
        ce_val = (
            frame.position.circular_error_m
            if frame.position.circular_error_m is not None
            else DEFAULT_CE
        )
        le_val = (
            frame.position.linear_error_m
            if frame.position.linear_error_m is not None
            else DEFAULT_LE
        )

        point_attrs: dict[str, str] = {
            "lat": f"{frame.position.latitude:.7f}",
            "lon": f"{frame.position.longitude:.7f}",
            "hae": f"{hae_val:.1f}",
            "ce": f"{ce_val:.1f}",
            "le": f"{le_val:.1f}",
        }
        ET.SubElement(event, "point", point_attrs)

        # 3. Child <detail> element
        detail = ET.SubElement(event, "detail")

        # <contact callsign="..." />
        ET.SubElement(detail, "contact", {"callsign": frame.callsign})

        # <track course="..." speed="..." />
        track_attrs: dict[str, str] = {}
        if frame.position.heading_deg is not None:
            track_attrs["course"] = f"{frame.position.heading_deg:.1f}"
        if frame.position.speed_mps is not None:
            track_attrs["speed"] = f"{frame.position.speed_mps:.2f}"
        if track_attrs:
            ET.SubElement(detail, "track", track_attrs)

        # <status battery="..." />
        status_attrs: dict[str, str] = {}
        if frame.health is not None:
            if frame.health.battery_percent is not None:
                status_attrs["battery"] = f"{int(frame.health.battery_percent)}"
            if frame.health.is_armed is not None:
                status_attrs["is_armed"] = "true" if frame.health.is_armed else "false"
        if status_attrs:
            ET.SubElement(detail, "status", status_attrs)

        # <takv platform="..." device="..." os="..." version="..." />
        ET.SubElement(
            detail,
            "takv",
            {
                "platform": "TacticalEdgeGateway",
                "device": "EdgeGateway",
                "os": "Linux",
                "version": self.gateway_version,
            },
        )

        # Optional metadata / remarks
        if frame.metadata:
            remarks_text = "; ".join(f"{k}={v}" for k, v in sorted(frame.metadata.items()))
            remarks_elem = ET.SubElement(detail, "remarks")
            remarks_elem.text = remarks_text

        return event

    def parse_cot(self, xml_content: str | bytes) -> dict[str, Any]:
        """Parses CoT XML into a structured dictionary using xmltodict.

        Args:
            xml_content: CoT XML string or bytes.

        Returns:
            Nested dictionary of CoT structure.

        Raises:
            CoTValidationError: If XML is malformed or invalid.
        """
        if isinstance(xml_content, bytes):
            xml_str = xml_content.decode("utf-8")
        else:
            xml_str = xml_content

        try:
            parsed = xmltodict.parse(xml_str)
        except Exception as e:
            raise CoTValidationError(f"Malformed XML: {e}") from e

        if "event" not in parsed:
            raise CoTValidationError("Missing root <event> element in CoT document")

        event = parsed["event"]
        if not isinstance(event, dict):
            raise CoTValidationError("Root <event> is not a valid XML object")

        # Validate mandatory attributes
        for required_attr in ("@version", "@uid", "@type", "@time", "@stale"):
            if required_attr not in event:
                raise CoTValidationError(f"Missing required CoT event attribute: {required_attr}")

        # Validate <point>
        if "point" not in event or not isinstance(event["point"], dict):
            raise CoTValidationError("Missing required <point> element in CoT document")

        point = event["point"]
        for required_coord in ("@lat", "@lon"):
            if required_coord not in point:
                raise CoTValidationError(
                    f"Missing required coordinate in <point>: {required_coord}"
                )

        return parsed

    def to_telemetry_frame(self, xml_content: str | bytes) -> TelemetryFrame:
        """Reverse transforms a CoT XML message into a canonical TelemetryFrame.

        Args:
            xml_content: CoT XML string or bytes.

        Returns:
            Validated, immutable TelemetryFrame.

        Raises:
            CoTValidationError: If mandatory fields are missing or invalid.
        """
        parsed = self.parse_cot(xml_content)
        event = parsed["event"]
        point = event["point"]

        uid = event["@uid"]
        cot_type = event["@type"]
        timestamp = parse_cot_timestamp(event["@time"])

        try:
            lat = float(point["@lat"])
            lon = float(point["@lon"])
            hae = float(point.get("@hae", 0.0))
            ce = float(point.get("@ce", DEFAULT_CE))
            le = float(point.get("@le", DEFAULT_LE))
        except ValueError as e:
            raise CoTValidationError(f"Invalid numeric value in CoT point coordinates: {e}") from e

        # Extract details if present
        callsign = uid
        heading: float | None = None
        speed: float | None = None
        battery_pct: float | None = None
        is_armed: bool | None = None
        metadata: dict[str, Any] = {}

        detail = event.get("detail")
        if isinstance(detail, dict):
            # Contact
            contact = detail.get("contact")
            if isinstance(contact, dict) and "@callsign" in contact:
                callsign = str(contact["@callsign"])

            # Track
            track = detail.get("track")
            if isinstance(track, dict):
                if "@course" in track:
                    heading = float(track["@course"])
                if "@speed" in track:
                    speed = float(track["@speed"])

            # Status
            status = detail.get("status")
            if isinstance(status, dict):
                if "@battery" in status:
                    battery_pct = float(status["@battery"])
                if "@is_armed" in status:
                    is_armed = status["@is_armed"].lower() in ("true", "1")

            # Remarks
            remarks = detail.get("remarks")
            if isinstance(remarks, str):
                metadata["remarks"] = remarks

        # Determine domain from CoT type prefix
        domain = VehicleDomain.AIR
        for prefix, d in _COT_TYPE_DOMAIN_MAP.items():
            if cot_type.startswith(prefix):
                domain = d
                break

        pos = GeodeticPosition(
            latitude=lat,
            longitude=lon,
            altitude_hae_m=hae,
            altitude_msl_m=hae,
            heading_deg=heading,
            speed_mps=speed,
            circular_error_m=ce,
            linear_error_m=le,
        )

        health: PlatformHealth | None = None
        if battery_pct is not None or is_armed is not None:
            health = PlatformHealth(
                battery_percent=battery_pct,
                is_armed=is_armed,
            )

        return TelemetryFrame(
            uid=uid,
            callsign=callsign,
            domain=domain,
            timestamp=timestamp,
            position=pos,
            attitude=(
                AttitudeVector(roll_deg=0.0, pitch_deg=0.0, yaw_deg=heading)
                if heading is not None
                else None
            ),
            health=health,
            metadata=metadata,
        )
