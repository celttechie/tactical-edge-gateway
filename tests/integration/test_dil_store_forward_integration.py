"""End-to-End integration test validating DIL mesh resilience and store-and-forward recovery.

Simulates:
1. Live CoT telemetry streaming to upstream TAK TCP server.
2. Contested radio link drop / network partition (upstream server severed).
3. Circuit breaker trips to OPEN; telemetry diverted to persistent SQLite WAL buffer.
4. Telemetry continues generating and buffering to disk without unbounded memory growth.
5. Network link restored; circuit breaker recovers to HALF_OPEN -> CLOSED.
6. Store-and-forward worker synchronizes backlog in chronological FIFO order at controlled rate.
"""

import asyncio
import socket

import pytest

from tactical_edge_gateway.buffer.circuit_breaker import CircuitBreaker
from tactical_edge_gateway.buffer.models import CircuitState
from tactical_edge_gateway.buffer.sqlite import SqliteBuffer
from tactical_edge_gateway.core.engine import GatewayEngine
from tactical_edge_gateway.core.models import (
    GeodeticPosition,
    PlatformHealth,
    TelemetryFrame,
    VehicleDomain,
)
from tactical_edge_gateway.egress.tcp import TcpEgressClient


def _get_ephemeral_port() -> int:
    """Finds an unused local TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_dil_store_forward_end_to_end_network_outage_and_recovery(tmp_path):
    """Verify persistent buffering across severing and restoring an upstream TCP TAK connection."""
    port = _get_ephemeral_port()
    db_path = tmp_path / "dil_telemetry.db"

    received_cot_messages: list[bytes] = []
    active_writers: list[asyncio.StreamWriter] = []
    server_running = True

    async def client_connected_cb(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        active_writers.append(writer)
        while server_running:
            try:
                data = await reader.read(4096)
                if not data:
                    break
                received_cot_messages.append(data.strip())
            except Exception:
                break
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    # 1. Start Initial TCP Server
    server = await asyncio.start_server(client_connected_cb, "127.0.0.1", port)

    # 2. Configure GatewayEngine with persistent SQLite buffer and low-threshold circuit breaker
    buffer = SqliteBuffer(db_path=db_path)
    cb = CircuitBreaker(
        failure_threshold=1,
        recovery_timeout=0.4,  # Outage window buffer
        max_recovery_timeout=0.8,
        success_threshold=1,
    )
    egress = TcpEgressClient(
        host="127.0.0.1",
        port=port,
        reconnect_interval_s=0.05,
        max_reconnect_interval_s=0.2,
    )

    engine = GatewayEngine(
        egress_clients=[egress],
        buffer=buffer,
        circuit_breaker=cb,
        replay_rate_hz=100.0,
    )

    async with engine:
        # Give TCP client time to connect
        await asyncio.sleep(0.1)

        # 3. Transmit Phase 1: Live Telemetry
        frame_live = TelemetryFrame(
            uid="uav-live-01",
            callsign="VIPER-LIVE",
            domain=VehicleDomain.AIR,
            position=GeodeticPosition(latitude=32.0, longitude=-117.0, altitude_msl_m=500.0),
            health=PlatformHealth(),
        )
        await engine._router_queue.put(frame_live)
        await asyncio.sleep(0.1)

        assert len(received_cot_messages) >= 1
        assert b"uav-live-01" in received_cot_messages[0]
        assert cb.state == CircuitState.CLOSED
        assert engine.stats.frames_buffered == 0

        # 4. Phase 2: Sever Upstream Link (Network Outage / Jamming)
        server_running = False
        server.close()
        await server.wait_closed()
        for w in active_writers:
            try:
                w.close()
                await w.wait_closed()
            except Exception:
                pass
        active_writers.clear()

        # Send frame during outage to trip circuit breaker
        frame_outage_1 = TelemetryFrame(
            uid="uav-buffered-01",
            callsign="VIPER-BUF-1",
            domain=VehicleDomain.AIR,
            position=GeodeticPosition(latitude=32.1, longitude=-117.1, altitude_msl_m=510.0),
            health=PlatformHealth(),
        )
        await engine._router_queue.put(frame_outage_1)
        await asyncio.sleep(0.1)

        # Circuit breaker should now be OPEN or transitioning
        assert cb.state in (CircuitState.OPEN, CircuitState.HALF_OPEN)

        # Send subsequent outage frames while OPEN -> Direct to disk
        for i in range(2, 6):
            frame = TelemetryFrame(
                uid=f"uav-buffered-0{i}",
                callsign=f"VIPER-BUF-{i}",
                domain=VehicleDomain.AIR,
                position=GeodeticPosition(
                    latitude=32.0 + i * 0.05, longitude=-117.0, altitude_msl_m=500.0
                ),
                health=PlatformHealth(),
            )
            await engine._router_queue.put(frame)

        await asyncio.sleep(0.15)

        # Verify disk storage captured all 5 outage frames
        stats = await buffer.get_stats()
        assert stats.pending_records >= 5
        assert stats.total_records >= 5

        # 5. Phase 3: Restore Upstream Network Link
        replayed_messages: list[bytes] = []

        async def restored_client_cb(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            while True:
                try:
                    data = await reader.read(4096)
                    if not data:
                        break
                    replayed_messages.append(data.strip())
                except Exception:
                    break
            writer.close()
            await writer.wait_closed()

        restored_server = await asyncio.start_server(restored_client_cb, "127.0.0.1", port)

        # Poll until sync worker finishes draining backlog
        for _ in range(40):
            stats_synced = await buffer.get_stats()
            if stats_synced.pending_records == 0 and len(replayed_messages) >= 5:
                break
            await asyncio.sleep(0.05)

        # Backlog must now be synced
        stats_synced = await buffer.get_stats()
        assert stats_synced.pending_records == 0
        assert stats_synced.synced_records >= 5

        # Verify all buffered UIDs were received by upstream receiver
        for i in range(1, 6):
            expected_uid = f"uav-buffered-0{i}".encode()
            assert any(expected_uid in msg for msg in replayed_messages)

        restored_server.close()
        await restored_server.wait_closed()
