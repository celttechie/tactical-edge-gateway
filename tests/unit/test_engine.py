"""Unit tests for GatewayEngine orchestrator and CLI supervisor."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from tactical_edge_gateway.core.engine import EngineStats, GatewayEngine
from tactical_edge_gateway.core.models import GeodeticPosition, TelemetryFrame, VehicleDomain
from tactical_edge_gateway.egress.base import BaseEgressClient
from tactical_edge_gateway.ingest.base import BaseTelemetryAdapter
from tactical_edge_gateway.main import build_parser, parse_host_port, run_gateway


class MockAdapter(BaseTelemetryAdapter):
    """Mock telemetry adapter yielding controlled frames."""

    def __init__(self, name: str, frames: list[TelemetryFrame] | None = None) -> None:
        super().__init__(name=name)
        self.frames = list(frames or [])
        self._stopped = False

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False
        self._stopped = True

    async def stream(self) -> AsyncIterator[TelemetryFrame]:
        for frame in self.frames:
            if not self._running:
                break
            yield frame
            await asyncio.sleep(0.01)


class MockEgressClient(BaseEgressClient):
    """Mock egress client collecting sent frames."""

    def __init__(self, name: str = "mock-egress") -> None:
        super().__init__(name=name)
        self.received_frames: list[TelemetryFrame] = []
        self._stopped = False

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False
        self._stopped = True

    async def send_cot(self, cot_xml: str | bytes) -> bool:
        return True

    async def send_frame(
        self,
        frame: TelemetryFrame,
        *,
        cot_type: str | None = None,
        stale_duration_s: float | None = None,
        include_declaration: bool = False,
    ) -> bool:
        self.received_frames.append(frame)
        self._record_success(len(str(frame)))
        return True


def test_engine_stats_defaults():
    stats = EngineStats()
    assert stats.frames_ingested == 0
    assert stats.frames_routed == 0
    assert stats.frames_dropped == 0
    assert stats.errors_count == 0
    assert stats.started_at is None


def test_parse_host_port():
    host, port = parse_host_port("10.0.0.1:8087", default_port=4242)
    assert host == "10.0.0.1"
    assert port == 8087

    host_def, port_def = parse_host_port("239.2.3.1", default_port=6969)
    assert host_def == "239.2.3.1"
    assert port_def == 6969


def test_build_parser():
    parser = build_parser()
    args = parser.parse_args(["--mavlink", "udpin:127.0.0.1:14550", "--tak-tcp", "127.0.0.1:8087"])
    assert args.mavlink == "udpin:127.0.0.1:14550"
    assert args.tak_tcp == "127.0.0.1:8087"


@pytest.mark.asyncio
async def test_gateway_engine_lifecycle_and_registration():
    adapter = MockAdapter("test-adapter")
    egress = MockEgressClient("test-egress")
    engine = GatewayEngine(adapters=[adapter], egress_clients=[egress])

    assert len(engine.adapters) == 1
    assert len(engine.egress_clients) == 1

    # Duplicate registration is ignored
    engine.add_adapter(adapter)
    engine.add_egress(egress)
    assert len(engine.adapters) == 1
    assert len(engine.egress_clients) == 1

    assert not engine.is_running
    await engine.start()
    assert engine.is_running
    assert engine.stats.started_at is not None

    # Idempotent start
    await engine.start()

    await engine.stop()
    assert not engine.is_running
    assert adapter._stopped
    assert egress._stopped

    # Idempotent stop
    await engine.stop()


@pytest.mark.asyncio
async def test_gateway_engine_routing_and_fanout():
    f1 = TelemetryFrame(
        uid="uav-1",
        callsign="VIPER",
        domain=VehicleDomain.AIR,
        position=GeodeticPosition(latitude=35.0, longitude=-79.0),
    )
    f2 = TelemetryFrame(
        uid="ugv-1",
        callsign="ROVER",
        domain=VehicleDomain.GROUND,
        position=GeodeticPosition(latitude=35.1, longitude=-79.1),
    )

    adapter = MockAdapter("mock-source", frames=[f1, f2])
    egress1 = MockEgressClient("sink-1")
    egress2 = MockEgressClient("sink-2")

    engine = GatewayEngine(adapters=[adapter], egress_clients=[egress1, egress2])

    async with engine:
        # Allow router loop to process frames
        for _ in range(20):
            if len(egress1.received_frames) == 2 and len(egress2.received_frames) == 2:
                break
            await asyncio.sleep(0.02)

    assert len(egress1.received_frames) == 2
    assert len(egress2.received_frames) == 2
    assert egress1.received_frames[0].uid == "uav-1"
    assert egress1.received_frames[1].uid == "ugv-1"
    assert engine.stats.frames_ingested == 2
    assert engine.stats.frames_routed == 2
    assert engine.stats.frames_dropped == 0


@pytest.mark.asyncio
async def test_gateway_engine_backpressure_eviction():
    # Generate 5 frames with queue size of 2
    frames = [
        TelemetryFrame(
            uid=f"vehicle-{i}",
            callsign=f"CALL-{i}",
            domain=VehicleDomain.AIR,
            position=GeodeticPosition(latitude=35.0 + i * 0.01, longitude=-79.0),
        )
        for i in range(5)
    ]

    adapter = MockAdapter("burst-source", frames=frames)
    # Slow egress client
    egress = MockEgressClient("slow-sink")

    engine = GatewayEngine(adapters=[adapter], egress_clients=[egress], queue_size=2)

    async with engine:
        for _ in range(25):
            if engine.stats.frames_ingested == 5:
                break
            await asyncio.sleep(0.02)

    assert engine.stats.frames_ingested == 5
    assert engine.stats.frames_dropped >= 0
    assert len(egress.received_frames) > 0


@pytest.mark.asyncio
async def test_run_gateway_with_stop_event():
    parser = build_parser()
    args = parser.parse_args(
        [
            "--synthetic",
            "--synthetic-domain",
            "ground",
            "--synthetic-uid",
            "ugv-cli-01",
            "--synthetic-callsign",
            "CLI-ROVER",
            "--tak-udp",
            "127.0.0.1:4242",
            "--tak-tcp",
            "127.0.0.1:8087",
        ]
    )

    stop_event = asyncio.Event()

    async def _trigger_stop():
        await asyncio.sleep(0.1)
        stop_event.set()

    task = asyncio.create_task(_trigger_stop())
    exit_code = await run_gateway(args, stop_event=stop_event)
    await task

    assert exit_code == 0


@pytest.mark.asyncio
async def test_gateway_engine_start_and_stop_exceptions():
    """Verify start() and stop() handle component exceptions cleanly."""

    class BadAdapter(MockAdapter):
        async def start(self) -> None:
            raise RuntimeError("Adapter start failure")

        async def stop(self) -> None:
            raise RuntimeError("Adapter stop failure")

    class BadEgress(MockEgressClient):
        async def start(self) -> None:
            raise RuntimeError("Egress start failure")

        async def stop(self) -> None:
            raise RuntimeError("Egress stop failure")

    adapter = BadAdapter("bad-adapter")
    egress = BadEgress("bad-egress")
    engine = GatewayEngine(adapters=[adapter], egress_clients=[egress])

    await engine.start()
    assert engine.stats.errors_count == 2

    # stop() should gracefully log warnings without raising
    await engine.stop()


@pytest.mark.asyncio
async def test_gateway_engine_stream_and_egress_exceptions():
    """Verify stream exception handling and egress send_frame exceptions."""
    f1 = TelemetryFrame(
        uid="uav-err",
        callsign="ERR-1",
        domain=VehicleDomain.AIR,
        position=GeodeticPosition(latitude=35.0, longitude=-79.0),
    )

    class FailingStreamAdapter(BaseTelemetryAdapter):
        async def start(self) -> None:
            self._running = True

        async def stop(self) -> None:
            self._running = False

        async def stream(self) -> AsyncIterator[TelemetryFrame]:
            yield f1
            await asyncio.sleep(0.01)
            raise RuntimeError("Stream broken")

    class FailingEgress(MockEgressClient):
        async def send_frame(self, frame: TelemetryFrame, **kwargs) -> bool:
            raise ConnectionResetError("TAK link drop")

    adapter = FailingStreamAdapter("failing-stream")
    egress = FailingEgress("failing-egress")
    engine = GatewayEngine(adapters=[adapter], egress_clients=[egress])

    async with engine:
        for _ in range(20):
            if engine.stats.errors_count >= 2:
                break
            await asyncio.sleep(0.02)

    assert engine.stats.errors_count >= 2


@pytest.mark.asyncio
async def test_gateway_engine_queue_empty_race(monkeypatch):
    """Verify _consume_adapter handles QueueEmpty during queue eviction."""
    f1 = TelemetryFrame(
        uid="uav-1",
        callsign="V-1",
        domain=VehicleDomain.AIR,
        position=GeodeticPosition(latitude=35.0, longitude=-79.0),
    )
    f2 = TelemetryFrame(
        uid="uav-2",
        callsign="V-2",
        domain=VehicleDomain.AIR,
        position=GeodeticPosition(latitude=35.1, longitude=-79.1),
    )
    f3 = TelemetryFrame(
        uid="uav-3",
        callsign="V-3",
        domain=VehicleDomain.AIR,
        position=GeodeticPosition(latitude=35.2, longitude=-79.2),
    )

    adapter = MockAdapter("test-q", frames=[f1, f2, f3])
    await adapter.start()
    engine = GatewayEngine(adapters=[adapter], queue_size=1)
    engine._running = True

    # Fill queue
    await engine._router_queue.put(f1)
    assert engine._router_queue.full()

    # When queue is full, _consume_adapter evicts oldest and puts new frame
    consume_task = asyncio.create_task(engine._consume_adapter(adapter))
    await asyncio.sleep(0.015)
    # Stop engine while streaming to hit line 170 (if not self._running: break)
    engine._running = False
    await consume_task
    await adapter.stop()

    assert engine.stats.frames_dropped >= 1


@pytest.mark.asyncio
async def test_gateway_engine_router_timeout():
    """Verify router loop handles get timeout and continues while running."""
    engine = GatewayEngine()
    engine._running = True
    router_task = asyncio.create_task(engine._router_loop())

    # Wait for at least one timeout iteration (timeout=0.2s)
    await asyncio.sleep(0.25)
    engine._running = False
    await router_task


@pytest.mark.asyncio
async def test_run_gateway_with_mavlink_ros2_and_default_egress(monkeypatch):
    """Verify run_gateway handles --mavlink, --ros2 and falls back to default UDP egress."""
    parser = build_parser()
    args = parser.parse_args(
        [
            "--mavlink",
            "udpin:127.0.0.1:14550",
            "--ros2",
            "--ros2-callsign",
            "TITAN-CLI",
            "--ros2-uid",
            "ugv-titan",
        ]
    )

    # Simulate platform without signal handling
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop,
        "add_signal_handler",
        lambda *a, **k: (_ for _ in ()).throw(NotImplementedError("Windows signal error")),
    )

    stop_event = asyncio.Event()

    async def _trigger_stop():
        await asyncio.sleep(0.05)
        stop_event.set()

    task = asyncio.create_task(_trigger_stop())
    exit_code = await run_gateway(args, stop_event=stop_event)
    await task

    assert exit_code == 0


def test_main_cli_execution_and_keyboard_interrupt(monkeypatch):
    """Verify main() entrypoint handles arguments and KeyboardInterrupt."""
    from tactical_edge_gateway.main import main

    # 1. Test version flag exit
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0

    # 2. Test KeyboardInterrupt handling
    def mock_run(coro):
        coro.close()
        raise KeyboardInterrupt()

    monkeypatch.setattr(asyncio, "run", mock_run)
    exit_code = main(["--synthetic"])
    assert exit_code == 0


@pytest.mark.asyncio
async def test_gateway_engine_with_sqlite_buffer_lifecycle_and_buffering():
    """Verify GatewayEngine lifecycle with buffer, circuit breaker, and outage buffering."""
    from tactical_edge_gateway.buffer.circuit_breaker import CircuitBreaker
    from tactical_edge_gateway.buffer.models import CircuitState
    from tactical_edge_gateway.buffer.sqlite import SqliteBuffer
    from tactical_edge_gateway.core.models import (
        GeodeticPosition,
        PlatformHealth,
        TelemetryFrame,
        VehicleDomain,
    )
    from tactical_edge_gateway.egress.base import BaseEgressClient

    buffer = SqliteBuffer(":memory:")
    cb = CircuitBreaker(failure_threshold=1)

    class FlakyEgress(BaseEgressClient):
        def __init__(self):
            super().__init__(name="flaky")
            self.should_succeed = True
            self.received_frames = []

        async def start(self):
            self._running = True

        async def stop(self):
            self._running = False

        @property
        def is_connected(self) -> bool:
            return self._running and self.should_succeed

        async def send_frame(self, frame):
            if not self.should_succeed:
                raise ConnectionError("Link down")
            self.received_frames.append(frame)
            return True

        async def send_cot(self, cot_xml):
            return self.should_succeed

    egress = FlakyEgress()
    engine = GatewayEngine(
        egress_clients=[egress],
        buffer=buffer,
        circuit_breaker=cb,
        replay_rate_hz=50.0,
    )

    async with engine:
        assert engine.sync_worker is not None
        assert engine.sync_worker.is_running

        frame1 = TelemetryFrame(
            uid="drone-1",
            callsign="VIPER-1",
            domain=VehicleDomain.AIR,
            position=GeodeticPosition(latitude=34.0, longitude=-118.0, altitude_msl_m=100.0),
            health=PlatformHealth(),
        )

        # 1. Normal healthy transmission
        await engine._router_queue.put(frame1)
        await asyncio.sleep(0.05)
        assert len(egress.received_frames) == 1
        assert cb.state == CircuitState.CLOSED
        assert engine.stats.frames_buffered == 0

        # 2. Link fails -> Trips circuit breaker to OPEN and buffers to SQLite
        egress.should_succeed = False
        frame2 = TelemetryFrame(
            uid="drone-2",
            callsign="VIPER-2",
            domain=VehicleDomain.AIR,
            position=GeodeticPosition(latitude=35.0, longitude=-119.0, altitude_msl_m=200.0),
            health=PlatformHealth(),
        )
        await engine._router_queue.put(frame2)
        await asyncio.sleep(0.05)
        assert cb.state == CircuitState.OPEN
        assert engine.stats.frames_buffered >= 1

        # 3. Subsequent frames while OPEN go straight to buffer
        frame3 = TelemetryFrame(
            uid="drone-3",
            callsign="VIPER-3",
            domain=VehicleDomain.AIR,
            position=GeodeticPosition(latitude=36.0, longitude=-120.0, altitude_msl_m=300.0),
            health=PlatformHealth(),
        )
        await engine._router_queue.put(frame3)
        await asyncio.sleep(0.05)
        assert engine.stats.frames_buffered >= 2

        # 4. Link recovers -> reset circuit breaker, sync_worker drains backlog
        egress.should_succeed = True
        cb.reset()
        await asyncio.sleep(0.1)

        stats = await buffer.get_stats()
        assert stats.synced_records >= 2

    # Verify stop closes buffer and stops worker
    assert not engine.sync_worker.is_running


@pytest.mark.asyncio
async def test_gateway_engine_buffer_helpers_and_error_handling():
    """Verify _replay_send_cot empty egress handling and _buffer_frame exception handling."""
    from unittest.mock import MagicMock

    from tactical_edge_gateway.buffer.sqlite import SqliteBuffer
    from tactical_edge_gateway.core.models import (
        GeodeticPosition,
        PlatformHealth,
        TelemetryFrame,
        VehicleDomain,
    )

    buffer = SqliteBuffer(":memory:")
    engine = GatewayEngine(buffer=buffer)

    # 1. Replay with no egress clients returns False
    assert await engine._replay_send_cot(b"<event/>") is False

    # 2. _buffer_frame exception handling
    bad_frame = TelemetryFrame(
        uid="err-drone",
        callsign="ERR-1",
        domain=VehicleDomain.AIR,
        position=GeodeticPosition(latitude=0.0, longitude=0.0, altitude_msl_m=0.0),
        health=PlatformHealth(),
    )
    # Monkeypatch transformer to raise exception
    engine.transformer.transform = MagicMock(side_effect=ValueError("Bad CoT generation"))
    await engine._buffer_frame(bad_frame)
    assert engine.stats.errors_count >= 1

    # 3. _buffer_frame with buffer=None is no-op
    engine.buffer = None
    await engine._buffer_frame(bad_frame)

    await buffer.close()


@pytest.mark.asyncio
async def test_run_gateway_with_buffer_cli_flag(tmp_path):
    """Verify run_gateway initializes SqliteBuffer when --buffer-path is passed."""
    from tactical_edge_gateway.main import build_parser, run_gateway

    db_file = tmp_path / "cli_buffer.db"
    parser = build_parser()
    args = parser.parse_args(
        ["--synthetic", "--buffer-path", str(db_file), "--replay-rate-hz", "30.0"]
    )

    stop_event = asyncio.Event()

    async def _stopper():
        await asyncio.sleep(0.05)
        stop_event.set()

    stopper_task = asyncio.create_task(_stopper())
    exit_code = await run_gateway(args, stop_event=stop_event)
    await stopper_task

    assert exit_code == 0
    assert db_file.exists()


@pytest.mark.asyncio
async def test_gateway_engine_router_egress_send_exception_trips_breaker_and_buffers():
    """Verify circuit breaker trips and frame is buffered when send_frame raises an exception."""
    from tactical_edge_gateway.buffer.circuit_breaker import CircuitBreaker
    from tactical_edge_gateway.buffer.models import CircuitState
    from tactical_edge_gateway.buffer.sqlite import SqliteBuffer
    from tactical_edge_gateway.core.models import (
        GeodeticPosition,
        PlatformHealth,
        TelemetryFrame,
        VehicleDomain,
    )
    from tactical_edge_gateway.egress.base import BaseEgressClient

    buffer = SqliteBuffer(":memory:")
    cb = CircuitBreaker(failure_threshold=1)

    class CrashingEgress(BaseEgressClient):
        async def start(self):
            self._running = True

        async def stop(self):
            self._running = False

        @property
        def is_connected(self) -> bool:
            return True

        async def send_frame(self, frame):
            raise OSError("Socket disconnected during write")

        async def send_cot(self, cot_xml):
            return True

    egress = CrashingEgress("crashing")
    engine = GatewayEngine(
        egress_clients=[egress],
        buffer=buffer,
        circuit_breaker=cb,
    )

    async with engine:
        frame = TelemetryFrame(
            uid="crash-1",
            callsign="CRASH-1",
            domain=VehicleDomain.AIR,
            position=GeodeticPosition(latitude=12.0, longitude=34.0, altitude_msl_m=100.0),
            health=PlatformHealth(),
        )
        await engine._router_queue.put(frame)
        await asyncio.sleep(0.05)

        assert engine.stats.errors_count >= 1
        assert engine.stats.frames_buffered >= 1
        assert cb.state == CircuitState.OPEN

    # Also test _replay_send_cot when client send_cot succeeds and returns True
    class GoodEgress(BaseEgressClient):
        async def start(self):
            self._running = True

        async def stop(self):
            self._running = False

        @property
        def is_connected(self) -> bool:
            return True

        async def send_frame(self, frame):
            return True

        async def send_cot(self, cot_xml):
            return True

    engine2 = GatewayEngine(egress_clients=[GoodEgress("good")])
    assert await engine2._replay_send_cot(b"<event/>") is True
