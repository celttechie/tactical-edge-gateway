"""Unit tests for the Synthetic telemetry generator adapter."""

import pytest

from tactical_edge_gateway.core.models import VehicleDomain
from tactical_edge_gateway.ingest.synthetic import SyntheticAdapter


def test_synthetic_adapter_defaults():
    """Verify adapter initializes with correct default configuration."""
    adapter = SyntheticAdapter()
    assert adapter.uid == "sim-uav-01"
    assert adapter.callsign == "VALKYRIE-01"
    assert adapter.domain == VehicleDomain.AIR
    assert adapter.name == "synthetic-sim-uav-01"
    assert not adapter.is_running


def test_synthetic_next_frame_kinematics():
    """Verify kinematic generation and coordinate progression."""
    adapter = SyntheticAdapter(
        origin_lat=35.0,
        origin_lon=-79.0,
        orbit_radius_m=500.0,
        speed_mps=20.0,
    )

    frame1 = adapter.next_frame()
    frame2 = adapter.next_frame()

    # Positions should not be identical
    assert (frame1.position.latitude, frame1.position.longitude) != (
        frame2.position.latitude,
        frame2.position.longitude,
    )
    # Altitude and speed match target
    assert frame1.position.altitude_msl_m == 120.0
    assert frame1.position.speed_mps == 20.0
    # Air domain has coordinated roll
    assert frame1.attitude is not None
    assert frame1.attitude.roll_deg > 0.0
    # Battery state populated
    assert frame1.health is not None
    assert frame1.health.battery_percent is not None


def test_synthetic_ground_domain():
    """Verify ground domain generates 0 roll bank angle."""
    adapter = SyntheticAdapter(domain=VehicleDomain.GROUND)
    frame = adapter.next_frame()

    assert frame.domain == VehicleDomain.GROUND
    assert frame.attitude is not None
    assert frame.attitude.roll_deg == 0.0


@pytest.mark.asyncio
async def test_synthetic_stream_max_iterations():
    """Verify stream yields exactly max_iterations frames and halts."""
    adapter = SyntheticAdapter(rate_hz=100.0, max_iterations=5)

    frames = []
    async for frame in adapter.stream():
        frames.append(frame)

    assert len(frames) == 5
    assert frames[0].metadata["iteration"] == 0
    assert frames[4].metadata["iteration"] == 4


@pytest.mark.asyncio
async def test_synthetic_stop_streaming():
    """Verify stopping adapter halts the async generator."""
    adapter = SyntheticAdapter(rate_hz=100.0)
    await adapter.start()

    frames = []
    async for frame in adapter.stream():
        frames.append(frame)
        if len(frames) >= 3:
            await adapter.stop()

    assert len(frames) == 3
    assert not adapter.is_running
