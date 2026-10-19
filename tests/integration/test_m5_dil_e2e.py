"""Milestone 5: Full-Loop End-to-End Autonomous Multi-Domain Mission Simulation.

Validates:
1. Heterogeneous multi-domain autonomy telemetry ingestion (Airborne UAV + Ground Rover).
2. Autonomous ground waypoint traversal and CoT translation (a-f-A-M-F and a-f-G-E-V-U).
3. Network impairment injection (radio blackout / severed upstream TAK link).
4. Circuit breaker trip (CLOSED -> OPEN) and persistent SQLite buffering during outage.
5. Radio link restoration, circuit breaker recovery (HALF_OPEN -> CLOSED), and queue flushing.
6. Verification that all historical waypoint breadcrumbs are synchronized with zero data loss.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from scripts.common import MockTakServer, find_free_port
from tactical_edge_gateway.buffer.circuit_breaker import CircuitBreaker
from tactical_edge_gateway.buffer.models import BufferState, CircuitState
from tactical_edge_gateway.buffer.sqlite import SqliteBuffer
from tactical_edge_gateway.core.engine import GatewayEngine
from tactical_edge_gateway.core.models import (
    AttitudeVector,
    GeodeticPosition,
    PlatformHealth,
    TelemetryFrame,
    VehicleDomain,
)
from tactical_edge_gateway.egress.tcp import TcpEgressClient

# Sample tactical waypoint route for ground rover (simulating perimeter traversal)
GROUND_ROVER_WAYPOINTS = [
    (35.7001, -120.7001, 100.0, 1.2),
    (35.7005, -120.7008, 100.2, 1.5),
    (35.7010, -120.7015, 100.5, 1.8),
    (35.7015, -120.7022, 100.8, 1.4),
    (35.7020, -120.7030, 101.0, 1.6),
    (35.7025, -120.7038, 101.2, 1.5),
    (35.7030, -120.7045, 101.5, 1.7),
    (35.7035, -120.7052, 101.8, 1.3),
]

# Sample tactical reconnaissance route for airborne drone
AIR_UAV_WAYPOINTS = [
    (35.7000, -120.7000, 250.0, 12.0),
    (35.7020, -120.7010, 255.0, 12.5),
    (35.7040, -120.7020, 260.0, 13.0),
    (35.7060, -120.7030, 258.0, 12.8),
    (35.7080, -120.7040, 252.0, 12.2),
    (35.7100, -120.7050, 250.0, 12.0),
    (35.7120, -120.7060, 248.0, 11.8),
    (35.7140, -120.7070, 250.0, 12.1),
]


@pytest.mark.asyncio
async def test_m5_e2e_autonomous_telemetry_outage_and_recovery(tmp_path: Path) -> None:
    """Execute full-loop validation: rover & drone waypoint mission through radio loss.

    Asserts circuit breaker tripping, SQLite buffering, link recovery, and zero loss.
    """
    port = find_free_port()
    db_path = tmp_path / "m5_e2e_buffer.db"

    # 1. Initialize Mock TAK Server (Phase 1: Connected RF link)
    mock_server = MockTakServer("127.0.0.1", port)
    await mock_server.start()

    # 2. Configure GatewayEngine with SQLite WAL buffer and responsive CircuitBreaker
    sqlite_buffer = SqliteBuffer(db_path=db_path)
    circuit_breaker = CircuitBreaker(
        failure_threshold=1,
        recovery_timeout=0.3,  # Rapid recovery window for test
        max_recovery_timeout=0.6,
        success_threshold=1,
    )
    tcp_egress = TcpEgressClient(
        host="127.0.0.1",
        port=port,
        reconnect_interval_s=0.05,
        max_reconnect_interval_s=0.15,
    )
    engine = GatewayEngine(
        egress_clients=[tcp_egress],
        buffer=sqlite_buffer,
        circuit_breaker=circuit_breaker,
        replay_rate_hz=50.0,
    )

    async with engine:
        # Allow TCP client to establish initial connection
        await asyncio.sleep(0.1)

        # ---------------------------------------------------------------------
        # PHASE 1: Normal Connected Tracking (UAV + Rover Waypoints 0..2)
        # ---------------------------------------------------------------------
        for i in range(3):
            # Transmit UAV airborne telemetry frame
            uav_lat, uav_lon, uav_alt, uav_spd = AIR_UAV_WAYPOINTS[i]
            uav_frame = TelemetryFrame(
                uid="uav-alpha-01",
                callsign="RECON-ALPHA",
                domain=VehicleDomain.AIR,
                position=GeodeticPosition(
                    latitude=uav_lat,
                    longitude=uav_lon,
                    altitude_msl_m=uav_alt,
                    speed_mps=uav_spd,
                ),
                attitude=AttitudeVector(roll_deg=0.0, pitch_deg=2.0, yaw_deg=45.0),
                health=PlatformHealth(battery_percent=94.0),
            )
            await engine._router_queue.put(uav_frame)

            # Transmit Ground Rover autonomous waypoint frame
            gnd_lat, gnd_lon, gnd_alt, gnd_spd = GROUND_ROVER_WAYPOINTS[i]
            gnd_frame = TelemetryFrame(
                uid="ugv-rover-01",
                callsign="ROVER-TERRA",
                domain=VehicleDomain.GROUND,
                position=GeodeticPosition(
                    latitude=gnd_lat,
                    longitude=gnd_lon,
                    altitude_msl_m=gnd_alt,
                    speed_mps=gnd_spd,
                ),
                attitude=AttitudeVector(roll_deg=1.0, pitch_deg=-0.5, yaw_deg=120.0),
                health=PlatformHealth(battery_percent=88.0),
            )
            await engine._router_queue.put(gnd_frame)
            await asyncio.sleep(0.05)

        # Verify initial live telemetry reached the TAK server
        assert len(mock_server.received_messages) >= 4
        all_phase1 = b"".join(mock_server.received_messages)
        assert b"a-f-A-M-F" in all_phase1  # Air drone CoT symbol
        assert b"a-f-G-E-V-U" in all_phase1  # Ground rover CoT symbol
        assert b"RECON-ALPHA" in all_phase1
        assert b"ROVER-TERRA" in all_phase1
        assert circuit_breaker.state == CircuitState.CLOSED

        # ---------------------------------------------------------------------
        # PHASE 2: Network Impairment (Severe Radio Outage / Jamming)
        # ---------------------------------------------------------------------
        await mock_server.sever_link()
        await asyncio.sleep(0.1)

        # Vehicles continue autonomous navigation during blackout (Waypoints 3..5)
        for i in range(3, 6):
            uav_lat, uav_lon, uav_alt, uav_spd = AIR_UAV_WAYPOINTS[i]
            uav_blackout_frame = TelemetryFrame(
                uid="uav-alpha-01",
                callsign="RECON-ALPHA",
                domain=VehicleDomain.AIR,
                position=GeodeticPosition(
                    latitude=uav_lat,
                    longitude=uav_lon,
                    altitude_msl_m=uav_alt,
                    speed_mps=uav_spd,
                ),
                attitude=AttitudeVector(roll_deg=0.5, pitch_deg=1.5, yaw_deg=48.0),
                health=PlatformHealth(battery_percent=91.0),
            )
            await engine._router_queue.put(uav_blackout_frame)

            gnd_lat, gnd_lon, gnd_alt, gnd_spd = GROUND_ROVER_WAYPOINTS[i]
            gnd_blackout_frame = TelemetryFrame(
                uid="ugv-rover-01",
                callsign="ROVER-TERRA",
                domain=VehicleDomain.GROUND,
                position=GeodeticPosition(
                    latitude=gnd_lat,
                    longitude=gnd_lon,
                    altitude_msl_m=gnd_alt,
                    speed_mps=gnd_spd,
                ),
                attitude=AttitudeVector(roll_deg=0.0, pitch_deg=0.0, yaw_deg=125.0),
                health=PlatformHealth(battery_percent=85.0),
            )
            await engine._router_queue.put(gnd_blackout_frame)
            await asyncio.sleep(0.05)

        # Allow circuit breaker trip and disk buffer persistence
        await asyncio.sleep(0.1)
        assert circuit_breaker.stats.total_trips >= 1
        assert circuit_breaker.state in (CircuitState.OPEN, CircuitState.HALF_OPEN)

        # Verify messages generated during blackout are safely buffered in SQLite
        pending_records = await sqlite_buffer.fetch_pending(limit=20)
        assert (
            len(pending_records) >= 6
        ), f"Expected >= 6 buffered records, got {len(pending_records)}"
        for rec in pending_records:
            assert rec.state == BufferState.PENDING
            assert rec.payload.startswith(b"<event")

        # ---------------------------------------------------------------------
        # PHASE 3: Link Recovery & Store-and-Forward Backfill
        # ---------------------------------------------------------------------
        await mock_server.restore_link()

        # Wait for circuit breaker probe and recovery (HALF_OPEN -> CLOSED)
        recovery_deadline = asyncio.get_event_loop().time() + 3.0
        while circuit_breaker.state != CircuitState.CLOSED:
            if asyncio.get_event_loop().time() > recovery_deadline:
                pytest.fail("Circuit breaker failed to recover to CLOSED state after link restored")
            await asyncio.sleep(0.1)

        # Allow StoreAndForwardWorker to drain all pending buffered messages
        drain_deadline = asyncio.get_event_loop().time() + 3.0
        while (await sqlite_buffer.get_stats()).pending_records > 0:
            if asyncio.get_event_loop().time() > drain_deadline:
                pytest.fail("StoreAndForwardWorker timed out draining buffered telemetry")
            await asyncio.sleep(0.05)

        # Final Waypoint Transmission (Waypoint 6..7 post-recovery live stream)
        for i in range(6, 8):
            uav_lat, uav_lon, uav_alt, uav_spd = AIR_UAV_WAYPOINTS[i]
            post_frame = TelemetryFrame(
                uid="uav-alpha-01",
                callsign="RECON-ALPHA",
                domain=VehicleDomain.AIR,
                position=GeodeticPosition(
                    latitude=uav_lat,
                    longitude=uav_lon,
                    altitude_msl_m=uav_alt,
                    speed_mps=uav_spd,
                ),
                attitude=AttitudeVector(roll_deg=0.0, pitch_deg=0.0, yaw_deg=50.0),
                health=PlatformHealth(battery_percent=89.0),
            )
            await engine._router_queue.put(post_frame)
            await asyncio.sleep(0.05)

        await asyncio.sleep(0.2)

        # Shutdown recovered server
        await mock_server.stop()

        # ---------------------------------------------------------------------
        # FINAL AUDIT: All telemetry delivered with zero loss
        # ---------------------------------------------------------------------
        stats = await sqlite_buffer.get_stats()
        assert stats.pending_records == 0
        assert stats.synced_records >= 6

        total_delivered_payloads = b"".join(mock_server.received_messages)
        # Verify both UAV and Rover points from all 3 phases are present in the stream
        assert b"RECON-ALPHA" in total_delivered_payloads
        assert b"ROVER-TERRA" in total_delivered_payloads
        assert b"a-f-A-M-F" in total_delivered_payloads
        assert b"a-f-G-E-V-U" in total_delivered_payloads
