"""Unit tests for Cursor on Target (CoT) XML Transformation Engine."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from tactical_edge_gateway.core.models import (
    AttitudeVector,
    GeodeticPosition,
    PlatformHealth,
    TelemetryFrame,
    VehicleDomain,
)
from tactical_edge_gateway.transform.cot import (
    CoTTransformer,
    CoTValidationError,
    format_cot_timestamp,
    parse_cot_timestamp,
)


@pytest.fixture
def sample_frame() -> TelemetryFrame:
    """Provides a fully populated TelemetryFrame fixture for testing."""
    dt = datetime(2026, 10, 14, 12, 0, 0, 500000, tzinfo=timezone.utc)
    return TelemetryFrame(
        uid="uav-alpha-01",
        callsign="VIPER-01",
        domain=VehicleDomain.AIR,
        timestamp=dt,
        position=GeodeticPosition(
            latitude=35.1415,
            longitude=-79.0069,
            altitude_msl_m=150.0,
            altitude_hae_m=125.0,
            heading_deg=180.0,
            speed_mps=15.5,
            circular_error_m=5.0,
            linear_error_m=7.0,
        ),
        attitude=AttitudeVector(roll_deg=2.5, pitch_deg=-1.0, yaw_deg=180.0),
        health=PlatformHealth(
            battery_percent=85.0,
            battery_voltage_v=22.2,
            is_armed=True,
        ),
        metadata={"mission": "recon", "sensor": "eo_ir"},
    )


def test_cot_transform_full_frame(sample_frame: TelemetryFrame):
    """Verify complete XML hierarchy generated for full TelemetryFrame."""
    transformer = CoTTransformer()
    xml_str = transformer.transform(sample_frame)

    parsed = transformer.parse_cot(xml_str)
    event = parsed["event"]

    assert event["@version"] == "2.0"
    assert event["@uid"] == "uav-alpha-01"
    assert event["@type"] == "a-f-A-M-F"
    assert event["@how"] == "m-g"
    assert event["@time"] == "2026-10-14T12:00:00.500Z"
    assert event["@start"] == "2026-10-14T12:00:00.500Z"
    assert event["@stale"] == "2026-10-14T12:00:30.500Z"

    # Point verification
    point = event["point"]
    assert float(point["@lat"]) == 35.1415
    assert float(point["@lon"]) == -79.0069
    assert float(point["@hae"]) == 125.0
    assert float(point["@ce"]) == 5.0
    assert float(point["@le"]) == 7.0

    # Detail verification
    detail = event["detail"]
    assert detail["contact"]["@callsign"] == "VIPER-01"
    assert float(detail["track"]["@course"]) == 180.0
    assert float(detail["track"]["@speed"]) == 15.5
    assert detail["status"]["@battery"] == "85"
    assert detail["status"]["@is_armed"] == "true"
    assert detail["takv"]["@platform"] == "TacticalEdgeGateway"
    assert "mission=recon" in detail["remarks"]


def test_cot_transform_bytes_and_declaration(sample_frame: TelemetryFrame):
    """Verify bytes serialization and XML declaration header inclusion."""
    transformer = CoTTransformer()

    # Raw bytes without header
    raw_bytes = transformer.transform_bytes(sample_frame)
    assert isinstance(raw_bytes, bytes)
    assert raw_bytes.startswith(b"<event")

    # String with XML declaration header
    xml_declared = transformer.transform(sample_frame, include_declaration=True)
    assert xml_declared.startswith("<?xml")
    assert "<event" in xml_declared


@pytest.mark.parametrize(
    "domain,expected_cot_type",
    [
        (VehicleDomain.AIR, "a-f-A-M-F"),
        (VehicleDomain.GROUND, "a-f-G-E-V-U"),
        (VehicleDomain.SURFACE, "a-f-S-X-U"),
        (VehicleDomain.SUBSURFACE, "a-f-U-U-U"),
    ],
)
def test_cot_domain_symbology_mapping(domain: VehicleDomain, expected_cot_type: str):
    """Verify standard MIL-STD symbology type assigned per domain."""
    frame = TelemetryFrame(
        uid="veh-01",
        callsign="EAGLE",
        domain=domain,
        position=GeodeticPosition(latitude=10.0, longitude=20.0),
    )
    transformer = CoTTransformer()
    xml_str = transformer.transform(frame)
    parsed = transformer.parse_cot(xml_str)
    assert parsed["event"]["@type"] == expected_cot_type


def test_cot_type_override(sample_frame: TelemetryFrame):
    """Verify caller can override automatic CoT type."""
    transformer = CoTTransformer()
    # Override with hostile air drone
    xml_str = transformer.transform(sample_frame, cot_type="a-h-A-M-F")
    parsed = transformer.parse_cot(xml_str)
    assert parsed["event"]["@type"] == "a-h-A-M-F"


def test_cot_stale_duration_calculation(sample_frame: TelemetryFrame):
    """Verify custom stale duration correctly computes stale timestamp."""
    transformer = CoTTransformer()
    xml_str = transformer.transform(sample_frame, stale_duration_s=60.0)
    parsed = transformer.parse_cot(xml_str)
    event = parsed["event"]

    event_time = parse_cot_timestamp(event["@time"])
    stale_time = parse_cot_timestamp(event["@stale"])
    assert stale_time - event_time == timedelta(seconds=60)


def test_cot_minimal_frame():
    """Verify minimal TelemetryFrame produces valid CoT without track or status tags."""
    minimal_frame = TelemetryFrame(
        uid="ugv-min",
        callsign="MIN-ROVER",
        domain=VehicleDomain.GROUND,
        position=GeodeticPosition(latitude=32.0, longitude=-110.0),
    )
    transformer = CoTTransformer()
    xml_str = transformer.transform(minimal_frame)
    parsed = transformer.parse_cot(xml_str)

    event = parsed["event"]
    assert event["@type"] == "a-f-G-E-V-U"
    assert float(event["point"]["@hae"]) == 0.0

    detail = event["detail"]
    assert detail["contact"]["@callsign"] == "MIN-ROVER"
    assert "track" not in detail
    assert "status" not in detail
    assert "remarks" not in detail


def test_cot_roundtrip_transformation(sample_frame: TelemetryFrame):
    """Verify round-trip from TelemetryFrame -> CoT XML -> TelemetryFrame."""
    transformer = CoTTransformer()
    xml_str = transformer.transform(sample_frame)

    reconstructed = transformer.to_telemetry_frame(xml_str)

    assert reconstructed.uid == sample_frame.uid
    assert reconstructed.callsign == sample_frame.callsign
    assert reconstructed.domain == sample_frame.domain
    assert math.isclose(reconstructed.position.latitude, sample_frame.position.latitude)
    assert math.isclose(reconstructed.position.longitude, sample_frame.position.longitude)
    assert math.isclose(
        reconstructed.position.altitude_hae_m or 0.0,
        sample_frame.position.altitude_hae_m or 0.0,
    )
    assert reconstructed.position.heading_deg == sample_frame.position.heading_deg
    assert reconstructed.position.speed_mps == sample_frame.position.speed_mps
    assert sample_frame.health is not None
    assert reconstructed.health is not None
    assert reconstructed.health.battery_percent == sample_frame.health.battery_percent
    assert reconstructed.health.is_armed == sample_frame.health.is_armed


def test_parse_cot_validation_errors():
    """Verify invalid XML input raises CoTValidationError with descriptive messages."""
    transformer = CoTTransformer()

    # 1. Invalid XML syntax
    with pytest.raises(CoTValidationError, match="Malformed XML"):
        transformer.parse_cot("<event><unclosed>")

    # 2. Missing root <event>
    with pytest.raises(CoTValidationError, match="Missing root <event>"):
        transformer.parse_cot("<message><point lat='0' lon='0'/></message>")

    # 3. Missing required attributes in event
    with pytest.raises(CoTValidationError, match="Missing required CoT event attribute"):
        transformer.parse_cot("<event version='2.0' uid='1'><point lat='0' lon='0'/></event>")

    # 4. Missing <point>
    with pytest.raises(CoTValidationError, match="Missing required <point> element"):
        transformer.parse_cot(
            "<event version='2.0' uid='1' type='a-f-A' "
            "time='2026-10-14T12:00:00Z' stale='2026-10-14T12:01:00Z'></event>"
        )

    # 5. Missing lat/lon in <point>
    with pytest.raises(CoTValidationError, match="Missing required coordinate"):
        transformer.parse_cot(
            "<event version='2.0' uid='1' type='a-f-A' "
            "time='2026-10-14T12:00:00Z' stale='2026-10-14T12:01:00Z'><point lat='35.0'/></event>"
        )


def test_to_telemetry_frame_invalid_coordinates():
    """Verify non-numeric coordinates raise CoTValidationError."""
    transformer = CoTTransformer()
    bad_xml = (
        "<event version='2.0' uid='1' type='a-f-A' "
        "time='2026-10-14T12:00:00Z' stale='2026-10-14T12:01:00Z'>"
        "<point lat='NOT_A_FLOAT' lon='-79.0'/></event>"
    )
    with pytest.raises(CoTValidationError, match="Invalid numeric value"):
        transformer.to_telemetry_frame(bad_xml)


def test_timestamp_formatting_and_parsing():
    """Verify timestamp helper functions handle timezones accurately."""
    dt = datetime(2026, 10, 14, 15, 30, 45, 123456, tzinfo=timezone.utc)
    formatted = format_cot_timestamp(dt)
    assert formatted == "2026-10-14T15:30:45.123Z"

    # Naive datetime auto-assumes UTC
    naive_dt = datetime(2026, 10, 14, 15, 30, 45, 123456)
    assert format_cot_timestamp(naive_dt) == "2026-10-14T15:30:45.123Z"

    # Parse round-trip
    parsed = parse_cot_timestamp(formatted)
    assert parsed.year == 2026
    assert parsed.month == 10
    assert parsed.day == 14
    assert parsed.hour == 15
    assert parsed.minute == 30
    assert parsed.second == 45
    assert parsed.microsecond == 123000
    assert parsed.tzinfo == timezone.utc


def test_parse_cot_from_bytes_and_non_dict_event():
    """Verify parse_cot handles raw bytes input and catches non-dict event structure."""
    transformer = CoTTransformer()

    # Valid bytes input
    valid_bytes = (
        b"<event version='2.0' uid='b-1' type='a-f-A' "
        b"time='2026-10-14T12:00:00Z' stale='2026-10-14T12:01:00Z'>"
        b"<point lat='10.0' lon='20.0'/></event>"
    )
    parsed = transformer.parse_cot(valid_bytes)
    assert parsed["event"]["@uid"] == "b-1"

    # Non-dict event (empty text element parsed by xmltodict as None or str)
    with pytest.raises(CoTValidationError, match="not a valid XML object"):
        transformer.parse_cot("<event></event>")
