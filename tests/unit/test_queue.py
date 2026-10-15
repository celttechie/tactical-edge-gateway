"""Unit tests for EvictingQueue primitive."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from tactical_edge_gateway.core.queue import EvictingQueue


@pytest.mark.asyncio
async def test_evicting_queue_basic_put_and_get():
    """Verify standard FIFO enqueuing and dequeuing without eviction."""
    q: EvictingQueue[str] = EvictingQueue(maxsize=3)
    assert q.maxsize == 3
    assert q.dropped_count == 0
    assert q.empty()

    evicted1 = q.put_nowait_evict("frame-1")
    evicted2 = q.put_nowait_evict("frame-2")

    assert evicted1 is False
    assert evicted2 is False
    assert q.qsize() == 2
    assert q.dropped_count == 0

    assert q.get_nowait() == "frame-1"
    assert q.get_nowait() == "frame-2"
    assert q.empty()


@pytest.mark.asyncio
async def test_evicting_queue_unbounded():
    """Verify unbounded queue (maxsize <= 0) never evicts."""
    q: EvictingQueue[int] = EvictingQueue(maxsize=0)
    for i in range(100):
        evicted = q.put_nowait_evict(i)
        assert evicted is False

    assert q.qsize() == 100
    assert q.dropped_count == 0


@pytest.mark.asyncio
async def test_evicting_queue_overflow_eviction():
    """Verify oldest items are dropped when capacity is reached."""
    q: EvictingQueue[str] = EvictingQueue(maxsize=2)

    assert q.put_nowait_evict("first") is False
    assert q.put_nowait_evict("second") is False
    assert q.qsize() == 2
    assert q.dropped_count == 0

    # Pushing third item evicts 'first'
    assert q.put_nowait_evict("third") is True
    assert q.qsize() == 2
    assert q.dropped_count == 1

    # Pushing fourth item evicts 'second'
    assert q.put_nowait_evict("fourth") is True
    assert q.qsize() == 2
    assert q.dropped_count == 2

    # Verify FIFO contents
    assert q.get_nowait() == "third"
    assert q.get_nowait() == "fourth"
    assert q.empty()


@pytest.mark.asyncio
async def test_evicting_queue_async_put_evict():
    """Verify async put_evict behaves identically to put_nowait_evict."""
    q: EvictingQueue[int] = EvictingQueue(maxsize=1)

    evicted1 = await q.put_evict(100)
    assert evicted1 is False
    assert q.qsize() == 1

    evicted2 = await q.put_evict(200)
    assert evicted2 is True
    assert q.qsize() == 1
    assert q.dropped_count == 1

    val = await q.get()
    assert val == 200


@pytest.mark.asyncio
async def test_evicting_queue_reset_dropped_count():
    """Verify reset_dropped_count clears eviction counter."""
    q: EvictingQueue[str] = EvictingQueue(maxsize=1)
    q.put_nowait_evict("a")
    q.put_nowait_evict("b")
    q.put_nowait_evict("c")

    assert q.dropped_count == 2
    q.reset_dropped_count()
    assert q.dropped_count == 0


@pytest.mark.asyncio
async def test_evicting_queue_task_done_and_join():
    """Verify task accounting allows q.join() to complete after evictions."""
    q: EvictingQueue[str] = EvictingQueue(maxsize=2)

    # Put 4 items into a queue of size 2 (2 items get evicted)
    q.put_nowait_evict("1")
    q.put_nowait_evict("2")
    q.put_nowait_evict("3")
    q.put_nowait_evict("4")

    assert q.dropped_count == 2
    assert q.qsize() == 2

    # Process remaining items
    item1 = await q.get()
    q.task_done()
    assert item1 == "3"

    item2 = await q.get()
    q.task_done()
    assert item2 == "4"

    # join() should complete immediately because evicted items were settled
    await asyncio.wait_for(q.join(), timeout=1.0)


@pytest.mark.asyncio
async def test_evicting_queue_race_condition_recovery():
    """Verify handling when a concurrent race causes QueueFull during put_nowait."""
    q: EvictingQueue[str] = EvictingQueue(maxsize=2)
    q.put_nowait_evict("1")
    q.put_nowait_evict("2")

    orig_put = q.put_nowait
    call_count = 0

    def mock_put(item):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise asyncio.QueueFull()
        return orig_put(item)

    q.put_nowait = MagicMock(side_effect=mock_put)

    evicted = q.put_nowait_evict("3")
    assert evicted is True
    assert q.dropped_count == 2
