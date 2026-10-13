"""Unit tests for canonical telemetry Pydantic data models."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from tactical_edge_gateway.core.models import (
    AttitudeVector,
    GeodeticPosition,
    PlatformHealth,
    TelemetryFrame,
    VehicleDomain,
)


def test_valid_telemetry_frame_creation():
    """Verify standard valid telemetry frame builds correctly with all fields."""
    pos = GeodeticPosition(
        latitude=35.1415,
        longitude=-79.0069,
        altitude_msl_m=150.0,
        altitude_hae_m=120.0,
        heading_deg=90.0,
        speed_mps=12.5,
    )
    att = AttitudeVector(roll_deg=5.0, pitch_deg=-2.0, yaw_deg=90.0)
    health = PlatformHealth(
        battery_percent=88.5,
        battery_voltage_v=24.5,
        link_quality_percent=95.0,
        is_armed=True,
    )

    frame = TelemetryFrame(
        uid="uav-alpha-01",
        callsign="ALPHA-01",
        domain=VehicleDomain.AIR,
        position=pos,
        attitude=att,
        health=health,
        metadata={"source": "test"},
    )

    assert frame.uid == "uav-alpha-01"
    assert frame.callsign == "ALPHA-01"
    assert frame.domain == VehicleDomain.AIR
    assert frame.position.latitude == 35.1415
    assert frame.position.longitude == -79.0069
    assert frame.attitude is not None
    assert frame.attitude.roll_deg == 5.0
    assert frame.health is not None
    assert frame.health.battery_percent == 88.5
    assert frame.default_cot_type() == "a-f-A-M-F"


def test_minimal_telemetry_frame():
    """Verify minimal frame requires only mandatory fields and defaults others."""
    pos = GeodeticPosition(latitude=0.0, longitude=0.0)
    frame = TelemetryFrame(
        uid="ugv-01",
        callsign="UGV-01",
        domain=VehicleDomain.GROUND,
        position=pos,
    )

    assert frame.attitude is None
    assert frame.health is None
    assert frame.position.circular_error_m == 10.0
    assert frame.default_cot_type() == "a-f-G-E-V-U"


@pytest.mark.parametrize(
    "domain,expected_cot",
    [
        (VehicleDomain.AIR, "a-f-A-M-F"),
        (VehicleDomain.GROUND, "a-f-G-E-V-U"),
        (VehicleDomain.SURFACE, "a-f-S-X-U"),
        (VehicleDomain.SUBSURFACE, "a-f-U-U-U"),
    ],
)
def test_cot_type_mapping(domain: VehicleDomain, expected_cot: str):
    """Verify correct MIL-STD-2525 CoT type hierarchy per vehicle domain."""
    pos = GeodeticPosition(latitude=10.0, longitude=20.0)
    frame = TelemetryFrame(
        uid="veh-01",
        callsign="VEH-01",
        domain=domain,
        position=pos,
    )
    assert frame.default_cot_type() == expected_cot


def test_coordinate_validation_bounds():
    """Verify latitude and longitude strict bounds enforcement."""
    # Latitude out of bounds
    with pytest.raises(ValidationError):
        GeodeticPosition(latitude=91.0, longitude=0.0)
    with pytest.raises(ValidationError):
        GeodeticPosition(latitude=-90.1, longitude=0.0)

    # Longitude out of bounds
    with pytest.raises(ValidationError):
        GeodeticPosition(latitude=0.0, longitude=180.1)
    with pytest.raises(ValidationError):
        GeodeticPosition(latitude=0.0, longitude=-180.1)

    # Negative speed error
    with pytest.raises(ValidationError):
        GeodeticPosition(latitude=0.0, longitude=0.0, speed_mps=-1.0)


def test_attitude_bounds():
    """Verify pitch, roll, and yaw bound constraints."""
    with pytest.raises(ValidationError):
        AttitudeVector(roll_deg=181.0, pitch_deg=0.0, yaw_deg=0.0)
    with pytest.raises(ValidationError):
        AttitudeVector(roll_deg=0.0, pitch_deg=91.0, yaw_deg=0.0)
    with pytest.raises(ValidationError):
        AttitudeVector(roll_deg=0.0, pitch_deg=0.0, yaw_deg=361.0)


def test_model_immutability():
    """Verify frozen models reject attribute mutation."""
    pos = GeodeticPosition(latitude=10.0, longitude=20.0)
    frame = TelemetryFrame(
        uid="uav-01",
        callsign="UAV-01",
        domain=VehicleDomain.AIR,
        position=pos,
    )

    with pytest.raises(ValidationError):
        frame.callsign = "MUTATED"  # type: ignore[misc]

    with pytest.raises(ValidationError):
        pos.latitude = 50.0  # type: ignore[misc]


def test_extra_fields_forbidden():
    """Verify strict schema rejects unexpected fields."""
    with pytest.raises(ValidationError):
        GeodeticPosition(latitude=0.0, longitude=0.0, rogue_field=123)  # type: ignore[call-arg]


def test_naive_timestamp_converted_to_utc():
    """Verify naive datetimes are converted to UTC timezone."""
    naive_dt = datetime(2026, 10, 13, 12, 0, 0)
    pos = GeodeticPosition(latitude=0.0, longitude=0.0)
    frame = TelemetryFrame(
        uid="uav-01",
        callsign="UAV-01",
        domain=VehicleDomain.AIR,
        timestamp=naive_dt,
        position=pos,
    )

    assert frame.timestamp.tzinfo is not None
    assert frame.timestamp.tzinfo == timezone.utc


def test_json_roundtrip_serialization():
    """Verify JSON export and re-import preserves all attributes."""
    pos = GeodeticPosition(
        latitude=34.0,
        longitude=-118.0,
        altitude_msl_m=50.0,
        heading_deg=270.0,
        speed_mps=8.0,
    )
    frame = TelemetryFrame(
        uid="ugv-mars-01",
        callsign="CURIOSITY",
        domain=VehicleDomain.GROUND,
        position=pos,
        metadata={"mode": "autonomous"},
    )

    json_str = frame.model_dump_json()
    reconstructed = TelemetryFrame.model_validate_json(json_str)

    assert reconstructed == frame
