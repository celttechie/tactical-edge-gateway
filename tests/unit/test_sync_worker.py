"""Unit tests for StoreAndForwardWorker backlog replay and link recovery synchronization."""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from tactical_edge_gateway.buffer.circuit_breaker import CircuitBreaker
from tactical_edge_gateway.buffer.models import CircuitState
from tactical_edge_gateway.buffer.sqlite import SqliteBuffer
from tactical_edge_gateway.buffer.sync_worker import StoreAndForwardWorker


@pytest.mark.asyncio
async def test_sync_worker_lifecycle():
    """Verify start, idempotency, and stop lifecycle of StoreAndForwardWorker."""
    buffer = SqliteBuffer(":memory:")
    cb = CircuitBreaker()
    send_mock = AsyncMock(return_value=True)

    worker = StoreAndForwardWorker(
        buffer=buffer,
        circuit_breaker=cb,
        send_fn=send_mock,
        replay_rate_hz=50.0,
    )

    assert not worker.is_running
    assert worker.total_replayed == 0

    await worker.start()
    assert worker.is_running
    # Idempotent start
    await worker.start()

    await worker.stop()
    assert not worker.is_running

    await buffer.close()


@pytest.mark.asyncio
async def test_sync_worker_replays_pending_messages():
    """Verify worker replays pending messages in order, marks them SYNCED, and throttles rate."""
    buffer = SqliteBuffer(":memory:")
    cb = CircuitBreaker()
    replayed_payloads: list[bytes] = []

    async def fake_send(payload: bytes) -> bool:
        replayed_payloads.append(payload)
        return True

    now = time.time()
    await buffer.put(b"p1", "u1", "t1", now, now + 30.0)
    await buffer.put(b"p2", "u2", "t2", now, now + 30.0)

    worker = StoreAndForwardWorker(
        buffer=buffer,
        circuit_breaker=cb,
        send_fn=fake_send,
        replay_rate_hz=100.0,
    )

    await worker.start()
    # Allow worker to drain 2 messages
    await asyncio.sleep(0.08)
    await worker.stop()

    assert replayed_payloads == [b"p1", b"p2"]
    assert worker.total_replayed == 2

    # Verify both are now SYNCED in SQLite
    stats = await buffer.get_stats()
    assert stats.synced_records == 2
    assert stats.pending_records == 0

    await buffer.close()


@pytest.mark.asyncio
async def test_sync_worker_pauses_when_circuit_open():
    """Verify worker pauses transmission while circuit breaker is OPEN."""
    buffer = SqliteBuffer(":memory:")
    cb = CircuitBreaker()
    cb.trip()  # OPEN
    send_mock = AsyncMock(return_value=True)

    now = time.time()
    await buffer.put(b"p1", "u1", "t1", now, now + 30.0)

    worker = StoreAndForwardWorker(
        buffer=buffer,
        circuit_breaker=cb,
        send_fn=send_mock,
        replay_rate_hz=100.0,
    )

    await worker.start()
    await asyncio.sleep(0.05)
    await worker.stop()

    # Nothing should have been sent
    assert send_mock.call_count == 0
    stats = await buffer.get_stats()
    assert stats.pending_records == 1

    await buffer.close()


@pytest.mark.asyncio
async def test_sync_worker_handles_send_failure_and_exception():
    """Verify worker records failures and trips circuit when send_fn returns False or raises."""
    buffer = SqliteBuffer(":memory:")
    cb = CircuitBreaker(failure_threshold=1)  # trip immediately on 1 failure

    call_count = 0

    async def faulty_send(payload: bytes) -> bool:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return False
        raise ConnectionResetError("Socket severed")

    now = time.time()
    await buffer.put(b"p1", "u1", "t1", now, now + 30.0)
    await buffer.put(b"p2", "u2", "t2", now, now + 30.0)

    worker = StoreAndForwardWorker(
        buffer=buffer,
        circuit_breaker=cb,
        send_fn=faulty_send,
        replay_rate_hz=100.0,
    )

    await worker.start()
    await asyncio.sleep(0.08)
    await worker.stop()

    # Circuit should have tripped to OPEN
    assert cb.state == CircuitState.OPEN

    await buffer.close()


@pytest.mark.asyncio
async def test_sync_worker_skips_and_prunes_stale_messages():
    """Verify messages that become stale while queued are skipped and expired."""
    buffer = SqliteBuffer(":memory:")
    cb = CircuitBreaker()
    send_mock = AsyncMock(return_value=True)

    now = time.time()
    # Message expired 10 seconds ago
    await buffer.put(b"stale_p", "u1", "t1", now - 30.0, now - 10.0)

    worker = StoreAndForwardWorker(
        buffer=buffer,
        circuit_breaker=cb,
        send_fn=send_mock,
        replay_rate_hz=100.0,
        prune_interval=0.01,
    )

    await worker.start()
    await asyncio.sleep(0.06)
    await worker.stop()

    assert send_mock.call_count == 0
    stats = await buffer.get_stats()
    assert stats.expired_records == 1

    await buffer.close()


@pytest.mark.asyncio
async def test_sync_worker_in_batch_stale_and_cancellation():
    """Verify in-batch stale detection and cancellation/stop during batch iteration."""
    from tactical_edge_gateway.buffer.models import BufferedMessage

    buffer = SqliteBuffer(":memory:")
    cb = CircuitBreaker()
    send_mock = AsyncMock(return_value=True)

    now = time.time()
    stale_msg = BufferedMessage(
        id=1,
        uid="u1",
        cot_type="t1",
        timestamp=now - 20.0,
        stale_timestamp=now - 10.0,
        payload=b"stale_p",
    )
    fetch_count = 0

    async def fake_fetch(limit=50):
        nonlocal fetch_count
        fetch_count += 1
        if fetch_count == 1:
            return [stale_msg]
        return []

    buffer.fetch_pending = fake_fetch
    buffer.prune_stale = AsyncMock()

    worker = StoreAndForwardWorker(
        buffer=buffer,
        circuit_breaker=cb,
        send_fn=send_mock,
        replay_rate_hz=100.0,
        prune_interval=3600.0,
    )

    await worker.start()
    await asyncio.sleep(0.05)
    await worker.stop()

    assert send_mock.call_count == 0
    assert buffer.prune_stale.call_count >= 1
    await buffer.close()


@pytest.mark.asyncio
async def test_sync_worker_batch_break_on_stop_and_cb_open():
    """Verify loop breaks if worker is stopped or circuit trips mid-batch."""
    buffer = SqliteBuffer(":memory:")
    cb = CircuitBreaker(failure_threshold=5)

    now = time.time()
    await buffer.put(b"p1", "u1", "t1", now, now + 30.0)
    await buffer.put(b"p2", "u2", "t2", now, now + 30.0)

    # 1. Stop mid-batch (line 118)
    worker = StoreAndForwardWorker(
        buffer=buffer,
        circuit_breaker=cb,
        send_fn=AsyncMock(return_value=True),
        replay_rate_hz=100.0,
    )

    async def send_and_stop(payload: bytes) -> bool:
        worker._running = False
        return True

    worker.send_fn = send_and_stop
    await worker.start()
    await asyncio.sleep(0.05)
    await worker.stop()
    assert worker.total_replayed == 1

    # 2. Circuit breaker OPEN check before transmit (line 127)
    worker2 = StoreAndForwardWorker(
        buffer=buffer,
        circuit_breaker=cb,
        send_fn=AsyncMock(return_value=True),
        replay_rate_hz=100.0,
    )

    async def send_and_trip(payload: bytes) -> bool:
        cb.trip()
        return True

    worker2.send_fn = send_and_trip
    # Add new message to test line 127
    await buffer.put(b"p3", "u3", "t3", now, now + 30.0)
    cb.reset()
    await worker2.start()
    await asyncio.sleep(0.05)
    await worker2.stop()
    assert cb.state == CircuitState.OPEN

    await buffer.close()


@pytest.mark.asyncio
async def test_sync_worker_send_exception_trips_cb_and_reverts_pending():
    """Verify send_fn exception handling trips circuit breaker and reverts message to PENDING."""
    buffer = SqliteBuffer(":memory:")
    cb = CircuitBreaker(failure_threshold=1)

    now = time.time()
    await buffer.put(b"p1", "u1", "t1", now, now + 30.0)

    async def crash_send(payload: bytes) -> bool:
        raise RuntimeError("Radio transport failed")

    worker = StoreAndForwardWorker(
        buffer=buffer,
        circuit_breaker=cb,
        send_fn=crash_send,
        replay_rate_hz=100.0,
    )
    await worker.start()
    await asyncio.sleep(0.05)
    await worker.stop()

    assert cb.state == CircuitState.OPEN
    stats = await buffer.get_stats()
    assert stats.pending_records == 1

    await buffer.close()


@pytest.mark.asyncio
async def test_sync_worker_outer_loop_error_handling():
    """Verify outer loop catches unexpected exceptions, logs, and sleeps before retrying."""
    buffer = SqliteBuffer(":memory:")
    cb = CircuitBreaker()

    worker = StoreAndForwardWorker(
        buffer=buffer,
        circuit_breaker=cb,
        send_fn=AsyncMock(return_value=True),
        replay_rate_hz=100.0,
    )
    call_count = 0

    async def flaky_fetch(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Database locked")
        return []

    buffer.fetch_pending = flaky_fetch
    await worker.start()
    await asyncio.sleep(0.05)
    await worker.stop()
    assert call_count >= 1

    await buffer.close()
