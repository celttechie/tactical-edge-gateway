"""Unit tests for SqliteBuffer in-memory and persistent file WAL mode."""

import time

import pytest

from tactical_edge_gateway.buffer.models import BufferState
from tactical_edge_gateway.buffer.sqlite import SqliteBuffer


@pytest.mark.asyncio
async def test_sqlite_buffer_in_memory_initialization_and_put():
    """Verify in-memory database creation, record insertion, and retrieval."""
    buffer = SqliteBuffer(":memory:")
    now = time.time()

    record_id = await buffer.put(
        payload=b"<event uid='drone-01'/>",
        uid="drone-01",
        cot_type="a-f-A-M-F",
        timestamp=now,
        stale_timestamp=now + 10.0,
    )
    assert record_id == 1

    pending = await buffer.fetch_pending(limit=10)
    assert len(pending) == 1
    msg = pending[0]
    assert msg.id == 1
    assert msg.uid == "drone-01"
    assert msg.cot_type == "a-f-A-M-F"
    assert msg.payload == b"<event uid='drone-01'/>"
    assert msg.state == BufferState.PENDING

    await buffer.close()


@pytest.mark.asyncio
async def test_sqlite_buffer_file_wal_mode_and_stats(tmp_path):
    """Verify file-based SQLite database sets WAL mode and reports file size metrics."""
    db_file = tmp_path / "buffer" / "telemetry.db"
    buffer = SqliteBuffer(db_file)

    now = time.time()
    await buffer.put(
        payload=b"<event uid='uav-01'/>",
        uid="uav-01",
        cot_type="a-f-A-M-F",
        timestamp=now,
        stale_timestamp=now + 10.0,
    )

    stats = await buffer.get_stats()
    assert stats.total_records == 1
    assert stats.pending_records == 1
    assert stats.synced_records == 0
    assert stats.db_size_bytes > 0

    await buffer.close()


@pytest.mark.asyncio
async def test_sqlite_buffer_put_batch():
    """Verify batch insertion and empty batch handling."""
    buffer = SqliteBuffer(":memory:")
    now = time.time()

    # Empty batch
    empty_ids = await buffer.put_batch([])
    assert empty_ids == []

    # Populated batch
    items = [
        (b"<event uid='uav-1'/>", "uav-1", "a-f-A-M-F", now, now + 10.0),
        (b"<event uid='ugv-2'/>", "ugv-2", "a-f-G-E-V-U", now, now + 10.0),
        (b"<event uid='usv-3'/>", "usv-3", "a-f-S-X-U", now, now + 10.0),
    ]
    ids = await buffer.put_batch(items)
    assert ids == [1, 2, 3]

    pending = await buffer.fetch_pending(limit=2)
    assert len(pending) == 2
    assert pending[0].uid == "uav-1"
    assert pending[1].uid == "ugv-2"

    await buffer.close()


@pytest.mark.asyncio
async def test_sqlite_buffer_lifecycle_states():
    """Verify state transitions: PENDING -> TRANSMITTING -> SYNCED, and empty operations."""
    buffer = SqliteBuffer(":memory:")
    now = time.time()

    # Empty operations
    assert await buffer.mark_transmitting([]) == 0
    assert await buffer.mark_synced([]) == 0
    assert await buffer.mark_failed([]) == 0

    id1 = await buffer.put(b"p1", "u1", "type1", now, now + 10)
    id2 = await buffer.put(b"p2", "u2", "type2", now, now + 10)

    # 1. Mark transmitting
    transmitting_count = await buffer.mark_transmitting([id1, id2])
    assert transmitting_count == 2

    # Since neither is PENDING, fetch_pending returns empty
    pending = await buffer.fetch_pending()
    assert len(pending) == 0

    # 2. Mark synced
    synced_count = await buffer.mark_synced([id1])
    assert synced_count == 1

    stats = await buffer.get_stats()
    assert stats.synced_records == 1

    await buffer.close()


@pytest.mark.asyncio
async def test_sqlite_buffer_mark_failed_and_max_retries():
    """Verify mark_failed increments retries and transitions to FAILED upon exceeding threshold."""
    buffer = SqliteBuffer(":memory:")
    now = time.time()

    id1 = await buffer.put(b"p1", "u1", "type1", now, now + 10)

    # Retry 1: still returns to PENDING
    await buffer.mark_failed([id1], max_retries=2)
    pending = await buffer.fetch_pending()
    assert len(pending) == 1
    assert pending[0].retry_count == 1
    assert pending[0].state == BufferState.PENDING

    # Retry 2: reaches max_retries=2, moves to FAILED
    await buffer.mark_failed([id1], max_retries=2)
    pending_after = await buffer.fetch_pending()
    assert len(pending_after) == 0

    stats = await buffer.get_stats()
    assert stats.failed_records == 1

    await buffer.close()


@pytest.mark.asyncio
async def test_sqlite_buffer_prune_stale():
    """Verify stale CoT packets past expiration threshold are marked EXPIRED."""
    buffer = SqliteBuffer(":memory:")
    now = time.time()

    # Fresh packet
    id_fresh = await buffer.put(b"fresh", "u1", "t1", now, now + 30.0)
    # Stale packet
    await buffer.put(b"stale", "u2", "t2", now - 40.0, now - 10.0)

    expired_count = await buffer.prune_stale(now=now)
    assert expired_count == 1

    pending = await buffer.fetch_pending()
    assert len(pending) == 1
    assert pending[0].id == id_fresh

    stats = await buffer.get_stats()
    assert stats.expired_records == 1
    assert stats.pending_records == 1

    await buffer.close()


@pytest.mark.asyncio
async def test_sqlite_buffer_prune_fifo_and_clear():
    """Verify FIFO pruning enforces max_records bounds and clear() purges all rows."""
    buffer = SqliteBuffer(":memory:")
    now = time.time()

    # Insert 5 records
    for i in range(5):
        await buffer.put(f"payload-{i}".encode(), f"u-{i}", "t", now, now + 10.0)

    # Zero or negative max_records returns 0
    assert await buffer.prune_fifo(max_records=0) == 0
    assert await buffer.prune_fifo(max_records=10) == 0  # Under limit

    # Prune down to 3 records
    pruned = await buffer.prune_fifo(max_records=3)
    assert pruned == 2

    stats = await buffer.get_stats()
    assert stats.total_records == 3

    # The remaining records should be IDs 3, 4, 5 (oldest 1 and 2 pruned)
    pending = await buffer.fetch_pending()
    ids = [p.id for p in pending]
    assert ids == [3, 4, 5]

    # Clear
    await buffer.clear()
    stats_after = await buffer.get_stats()
    assert stats_after.total_records == 0

    # Idempotent close
    await buffer.close()
    await buffer.close()
