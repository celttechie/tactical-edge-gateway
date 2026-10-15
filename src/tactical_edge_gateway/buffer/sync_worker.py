"""Store-and-forward synchronization worker replaying backlog over restored links."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from tactical_edge_gateway.buffer.circuit_breaker import CircuitBreaker
from tactical_edge_gateway.buffer.models import CircuitState
from tactical_edge_gateway.buffer.sqlite import SqliteBuffer
from tactical_edge_gateway.core.service import AsyncService

logger = logging.getLogger(__name__)


class StoreAndForwardWorker(AsyncService):
    """Coordinates backlog replay from persistent SQLite storage to network egress."""

    def __init__(
        self,
        buffer: SqliteBuffer,
        circuit_breaker: CircuitBreaker,
        send_fn: Callable[[bytes], Awaitable[bool]],
        *,
        replay_rate_hz: float = 20.0,
        batch_size: int = 50,
        prune_interval: float = 10.0,
        max_buffer_records: int = 10000,
    ) -> None:
        """Initializes the store-and-forward synchronization worker.

        Args:
            buffer: Persistent SQLite buffer instance.
            circuit_breaker: Circuit breaker monitoring egress link availability.
            send_fn: Async callback invoked to transmit payload bytes over the link.
            replay_rate_hz: Maximum replay throughput in messages per second.
            batch_size: Number of pending messages fetched per query.
            prune_interval: Seconds between background stale packet and FIFO pruning runs.
            max_buffer_records: Maximum records retained in SQLite before FIFO eviction.
        """
        super().__init__(name="store-and-forward-worker")
        self.buffer = buffer
        self.circuit_breaker = circuit_breaker
        self.send_fn = send_fn
        self.replay_rate_hz = max(0.1, replay_rate_hz)
        self.batch_size = max(1, batch_size)
        self.prune_interval = max(1.0, prune_interval)
        self.max_buffer_records = max_buffer_records

        self._worker_task: asyncio.Task[None] | None = None
        self._total_replayed = 0

    @property
    def total_replayed(self) -> int:
        """Returns total count of replayed backlog messages successfully synchronized."""
        return self._total_replayed

    async def _on_start(self) -> None:
        """Starts the background backlog synchronization loop."""
        self._worker_task = asyncio.create_task(self._sync_loop())
        logger.info(
            "StoreAndForwardWorker started (rate=%.1f Hz, batch=%d, max_records=%d)",
            self.replay_rate_hz,
            self.batch_size,
            self.max_buffer_records,
        )

    async def _on_stop(self) -> None:
        """Stops the synchronization worker gracefully."""
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        logger.info("StoreAndForwardWorker stopped (total_replayed=%d)", self._total_replayed)

    async def _sync_loop(self) -> None:
        """Main background loop handling throttled backlog replay and pruning."""
        delay = 1.0 / self.replay_rate_hz
        last_prune = 0.0

        while self._running:
            try:
                # 1. Periodic maintenance: prune stale CoT messages and enforce FIFO caps
                now = asyncio.get_running_loop().time()
                if now - last_prune >= self.prune_interval:
                    await self.buffer.prune_stale()
                    await self.buffer.prune_fifo(self.max_buffer_records)
                    last_prune = now

                # 2. Check if circuit breaker allows egress attempts
                if not self.circuit_breaker.can_execute():
                    # Link is down (OPEN); sleep before checking again
                    await asyncio.sleep(0.5)
                    continue

                # 3. Fetch batch of pending messages
                messages = await self.buffer.fetch_pending(limit=self.batch_size)
                if not messages:
                    # Backlog drained; idle briefly
                    await asyncio.sleep(min(0.2, self.prune_interval))
                    continue

                for msg in messages:
                    if not self._running:
                        break

                    # If message expired while queued, skip transmitting
                    if msg.is_stale:
                        await self.buffer.prune_stale()
                        continue

                    # If circuit tripped during batch processing, pause
                    if self.circuit_breaker.state == CircuitState.OPEN:
                        break

                    await self.buffer.mark_transmitting([msg.id])
                    try:
                        success = await self.send_fn(msg.payload)
                        if success:
                            self.circuit_breaker.record_success()
                            await self.buffer.mark_synced([msg.id])
                            self._total_replayed += 1
                        else:
                            self.circuit_breaker.record_failure()
                            await self.buffer.mark_failed([msg.id])
                            # If failure tripped circuit, break batch loop
                            if self.circuit_breaker.state == CircuitState.OPEN:
                                break
                    except Exception as e:
                        logger.error("Error replaying message id=%d: %s", msg.id, e)
                        self.circuit_breaker.record_failure()
                        await self.buffer.mark_failed([msg.id])
                        if self.circuit_breaker.state == CircuitState.OPEN:
                            break

                    # Enforce throttled replay rate to protect contested tactical radios
                    await asyncio.sleep(delay)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Unexpected error in StoreAndForwardWorker sync loop: %s", e)
                await asyncio.sleep(delay)
