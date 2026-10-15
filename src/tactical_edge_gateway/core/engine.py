"""Core Gateway Engine orchestrating telemetry ingestion, routing, and egress.

Coordinates multiple asynchronous telemetry adapters (MAVLink, ROS2, Synthetic),
normalizes frames, and fans out Cursor on Target (CoT) XML packets to multiple
downstream C2 network transports (TAK Server TCP, ATAK UDP mesh).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from tactical_edge_gateway.core.queue import EvictingQueue
from tactical_edge_gateway.core.service import AsyncService

if TYPE_CHECKING:
    from tactical_edge_gateway.buffer.circuit_breaker import CircuitBreaker
    from tactical_edge_gateway.buffer.sqlite import SqliteBuffer
    from tactical_edge_gateway.buffer.sync_worker import StoreAndForwardWorker
    from tactical_edge_gateway.core.models import TelemetryFrame
    from tactical_edge_gateway.egress.base import BaseEgressClient
    from tactical_edge_gateway.ingest.base import BaseTelemetryAdapter
    from tactical_edge_gateway.transform.cot import CoTTransformer

logger = logging.getLogger(__name__)


@dataclass
class EngineStats:
    """Operational telemetry routing statistics."""

    frames_ingested: int = 0
    frames_routed: int = 0
    frames_dropped: int = 0
    frames_buffered: int = 0
    errors_count: int = 0
    started_at: datetime | None = None


class GatewayEngine(AsyncService):
    """Asynchronous orchestrator routing telemetry from Ingest to Egress transports."""

    def __init__(
        self,
        adapters: list[BaseTelemetryAdapter] | None = None,
        egress_clients: list[BaseEgressClient] | None = None,
        queue_size: int = 1000,
        buffer: SqliteBuffer | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        sync_worker: StoreAndForwardWorker | None = None,
        replay_rate_hz: float = 20.0,
        max_buffer_records: int = 10000,
        transformer: CoTTransformer | None = None,
    ) -> None:
        """Initializes the GatewayEngine.

        Args:
            adapters: Optional initial list of telemetry ingest adapters.
            egress_clients: Optional initial list of C2 egress clients.
            queue_size: Maximum capacity of the internal routing queue before backpressure.
            buffer: Optional persistent SQLite WAL buffer for DIL resilience.
            circuit_breaker: Optional network circuit breaker link health monitor.
            sync_worker: Optional store-and-forward synchronization worker.
            replay_rate_hz: Replay throughput rate limit in messages per second.
            max_buffer_records: Maximum records preserved in SQLite buffer before FIFO pruning.
            transformer: Optional CoTTransformer instance for XML serialization.
        """
        super().__init__(name="gateway-engine")
        self.adapters: list[BaseTelemetryAdapter] = list(adapters or [])
        self.egress_clients: list[BaseEgressClient] = list(egress_clients or [])
        self.queue_size: int = queue_size

        self.buffer = buffer
        self.circuit_breaker = circuit_breaker
        self.sync_worker = sync_worker

        from tactical_edge_gateway.transform.cot import CoTTransformer

        self.transformer: CoTTransformer = transformer or CoTTransformer()

        if self.buffer is not None:
            if self.circuit_breaker is None:
                from tactical_edge_gateway.buffer.circuit_breaker import CircuitBreaker

                self.circuit_breaker = CircuitBreaker(name="engine-circuit-breaker")

            if self.sync_worker is None:
                from tactical_edge_gateway.buffer.sync_worker import StoreAndForwardWorker

                self.sync_worker = StoreAndForwardWorker(
                    buffer=self.buffer,
                    circuit_breaker=self.circuit_breaker,
                    send_fn=self._replay_send_cot,
                    replay_rate_hz=replay_rate_hz,
                    max_buffer_records=max_buffer_records,
                )

        self._router_queue: EvictingQueue[TelemetryFrame] = EvictingQueue(maxsize=queue_size)
        self._adapter_tasks: list[asyncio.Task[None]] = []
        self._router_task: asyncio.Task[None] | None = None
        self._stats: EngineStats = EngineStats()

    @property
    def stats(self) -> EngineStats:
        """Operational statistics for the engine."""
        return self._stats

    def add_adapter(self, adapter: BaseTelemetryAdapter) -> None:
        """Registers a telemetry ingest adapter."""
        if adapter not in self.adapters:
            self.adapters.append(adapter)

    def add_egress(self, client: BaseEgressClient) -> None:
        """Registers a C2 egress transport client."""
        if client not in self.egress_clients:
            self.egress_clients.append(client)

    async def _on_start(self) -> None:
        """Starts all egress clients, ingest adapters, and internal router tasks."""
        self._stats.started_at = datetime.now(timezone.utc)
        logger.info(
            "Starting GatewayEngine with %d adapters and %d egress clients",
            len(self.adapters),
            len(self.egress_clients),
        )

        # 1. Start all egress clients first so listeners are ready
        for egress in self.egress_clients:
            try:
                await egress.start()
            except Exception as e:
                logger.error("Failed to start egress client %s: %s", egress.name, e)
                self._stats.errors_count += 1

        # 2. Start all ingest adapters
        for adapter in self.adapters:
            try:
                await adapter.start()
            except Exception as e:
                logger.error("Failed to start ingest adapter %s: %s", adapter.name, e)
                self._stats.errors_count += 1

        # 3. Launch consumer task for each adapter
        self._adapter_tasks = [
            asyncio.create_task(
                self._consume_adapter(adapter),
                name=f"consume-{adapter.name}",
            )
            for adapter in self.adapters
        ]

        # 4. Launch router fan-out task
        self._router_task = asyncio.create_task(
            self._router_loop(),
            name="gateway-router-loop",
        )

        # 5. Start store-and-forward sync worker if enabled
        if self.sync_worker is not None:
            await self.sync_worker.start()

    async def _on_stop(self) -> None:
        """Gracefully shuts down all workers, adapters, buffer, and egress connections."""
        logger.info("Stopping GatewayEngine...")

        # 1. Stop sync worker first so replay halts cleanly
        if self.sync_worker is not None:
            await self.sync_worker.stop()

        # 2. Cancel adapter worker tasks
        for task in self._adapter_tasks:
            task.cancel()
        if self._adapter_tasks:
            await asyncio.gather(*self._adapter_tasks, return_exceptions=True)
        self._adapter_tasks.clear()

        # 3. Cancel router task
        if self._router_task is not None:
            self._router_task.cancel()
            try:
                await self._router_task
            except asyncio.CancelledError:  # pragma: no cover
                pass
            self._router_task = None

        # 4. Stop all adapters
        for adapter in self.adapters:
            try:
                await adapter.stop()
            except Exception as e:
                logger.warning("Error stopping adapter %s: %s", adapter.name, e)

        # 5. Stop all egress clients
        for egress in self.egress_clients:
            try:
                await egress.stop()
            except Exception as e:
                logger.warning("Error stopping egress client %s: %s", egress.name, e)

        # 6. Close persistent buffer if configured
        if self.buffer is not None:
            await self.buffer.close()

        logger.info("GatewayEngine stopped")

    async def _consume_adapter(self, adapter: BaseTelemetryAdapter) -> None:
        """Consumes frames from a single adapter stream and pushes to the router queue."""
        try:
            async for frame in adapter.stream():
                if not self._running:
                    break
                self._stats.frames_ingested += 1

                if self._router_queue.put_nowait_evict(frame):
                    self._stats.frames_dropped += 1
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("Ingest stream failed for %s: %s", adapter.name, exc)
            self._stats.errors_count += 1

    async def _replay_send_cot(self, cot_payload: bytes) -> bool:
        """Transmits replayed CoT XML payload to all registered egress clients."""
        if not self.egress_clients or not all(c.is_connected for c in self.egress_clients):
            return False
        results = await asyncio.gather(
            *(client.send_cot(cot_payload) for client in self.egress_clients),
            return_exceptions=True,
        )
        return any(res is True for res in results if not isinstance(res, Exception))

    async def _buffer_frame(self, frame: TelemetryFrame) -> None:
        """Transforms a frame and persists it to the SQLite store-and-forward buffer."""
        if self.buffer is None:
            return
        try:
            cot_xml = self.transformer.transform(frame)
            payload = cot_xml.encode("utf-8")
            uid = frame.uid
            cot_type = frame.default_cot_type()
            ts = frame.timestamp.timestamp()
            stale_ts = ts + 10.0
            await self.buffer.put(payload, uid, cot_type, ts, stale_ts)
            self._stats.frames_buffered += 1
        except Exception as e:
            logger.error("Failed to buffer telemetry frame for %s: %s", frame.uid, e)
            self._stats.errors_count += 1

    async def _router_loop(self) -> None:
        """Pulls frames from the router queue and fans out to egress or SQLite buffer."""
        while self._running:
            try:
                frame = await asyncio.wait_for(self._router_queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            if self.buffer is not None and self.circuit_breaker is not None:
                clients_ready = bool(
                    self.egress_clients and all(c.is_connected for c in self.egress_clients)
                )
                if self.circuit_breaker.can_execute() and clients_ready:
                    results = await asyncio.gather(
                        *(client.send_frame(frame) for client in self.egress_clients),
                        return_exceptions=True,
                    )
                    has_success = any(
                        res is True for res in results if not isinstance(res, Exception)
                    )
                    if has_success:
                        self.circuit_breaker.record_success()
                    else:
                        self.circuit_breaker.record_failure()
                        await self._buffer_frame(frame)

                    for res in results:
                        if isinstance(res, Exception):
                            logger.debug("Egress transmission error: %s", res)
                            self._stats.errors_count += 1
                else:
                    # Circuit is OPEN or clients are disconnected;
                    # record link failure and buffer directly to disk
                    self.circuit_breaker.record_failure()
                    await self._buffer_frame(frame)
            else:
                if self.egress_clients:
                    results = await asyncio.gather(
                        *(client.send_frame(frame) for client in self.egress_clients),
                        return_exceptions=True,
                    )
                    for res in results:
                        if isinstance(res, Exception):
                            logger.debug("Egress transmission error: %s", res)
                            self._stats.errors_count += 1
            self._stats.frames_routed += 1
