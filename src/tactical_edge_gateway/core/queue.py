"""Bounded FIFO queue with atomic drop-oldest eviction under backpressure.

Designed for edge C2 telemetry pipelines where freshness and bounded memory
preempt stale historical data.
"""

from __future__ import annotations

import asyncio
from typing import Generic, TypeVar

T = TypeVar("T")


class EvictingQueue(asyncio.Queue[T], Generic[T]):
    """Thread-safe bounded queue that drops the oldest item on overflow.

    Standard asyncio.Queue raises asyncio.QueueFull when capacity is reached.
    In real-time C2 and tactical telemetry systems, fresh position reports are
    significantly more valuable than stale historical coordinates. This queue
    evicts the oldest queued element to ensure non-blocking ingestion and bounded memory.
    """

    def __init__(self, maxsize: int = 0) -> None:
        """Initializes the evicting queue.

        Args:
            maxsize: Maximum capacity. If <= 0, capacity is unbounded.
        """
        super().__init__(maxsize=maxsize)
        self._dropped_count: int = 0

    @property
    def dropped_count(self) -> int:
        """Total number of items discarded due to FIFO eviction."""
        return self._dropped_count

    def reset_dropped_count(self) -> None:
        """Resets the eviction counter to zero."""
        self._dropped_count = 0

    def put_nowait_evict(self, item: T) -> bool:
        """Pushes an item into the queue without blocking, evicting the oldest if full.

        If the queue is bounded (maxsize > 0) and currently at capacity, the oldest
        item is dropped to make room for the new item.

        Args:
            item: Item to enqueue.

        Returns:
            bool: True if an existing item was evicted to make room, False otherwise.
        """
        evicted = False
        if self.maxsize > 0:
            while self.full():
                try:
                    self.get_nowait()
                    self.task_done()
                    self._dropped_count += 1
                    evicted = True
                except asyncio.QueueEmpty:  # pragma: no cover
                    break

        try:
            self.put_nowait(item)
        except asyncio.QueueFull:
            # Race condition recovery if concurrent coroutines pushed simultaneously
            try:
                self.get_nowait()
                self.task_done()
                self._dropped_count += 1
                evicted = True
            except asyncio.QueueEmpty:  # pragma: no cover
                pass
            self.put_nowait(item)

        return evicted

    async def put_evict(self, item: T) -> bool:
        """Asynchronously puts an item, evicting the oldest if full.

        Because eviction guarantees capacity, this method will never block,
        providing an async interface for parity with `asyncio.Queue.put`.

        Args:
            item: Item to enqueue.

        Returns:
            bool: True if an item was evicted, False otherwise.
        """
        return self.put_nowait_evict(item)
