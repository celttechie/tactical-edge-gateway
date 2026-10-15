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

if TYPE_CHECKING:
    from tactical_edge_gateway.core.models import TelemetryFrame
    from tactical_edge_gateway.egress.base import BaseEgressClient
    from tactical_edge_gateway.ingest.base import BaseTelemetryAdapter

logger = logging.getLogger(__name__)


@dataclass
class EngineStats:
    """Operational telemetry routing statistics."""

    frames_ingested: int = 0
    frames_routed: int = 0
    frames_dropped: int = 0
    errors_count: int = 0
    started_at: datetime | None = None


class GatewayEngine:
    """Asynchronous orchestrator routing telemetry from Ingest to Egress transports."""

    def __init__(
        self,
        adapters: list[BaseTelemetryAdapter] | None = None,
        egress_clients: list[BaseEgressClient] | None = None,
        queue_size: int = 1000,
    ) -> None:
        """Initializes the GatewayEngine.

        Args:
            adapters: Optional initial list of telemetry ingest adapters.
            egress_clients: Optional initial list of C2 egress clients.
            queue_size: Maximum capacity of the internal routing queue before backpressure.
        """
        self.adapters: list[BaseTelemetryAdapter] = list(adapters or [])
        self.egress_clients: list[BaseEgressClient] = list(egress_clients or [])
        self.queue_size: int = queue_size

        self._router_queue: asyncio.Queue[TelemetryFrame] = asyncio.Queue(maxsize=queue_size)
        self._adapter_tasks: list[asyncio.Task[None]] = []
        self._router_task: asyncio.Task[None] | None = None
        self._running: bool = False
        self._stats: EngineStats = EngineStats()

    @property
    def is_running(self) -> bool:
        """Indicates whether the gateway engine is actively executing."""
        return self._running

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

    async def start(self) -> None:
        """Starts all egress clients, ingest adapters, and internal router tasks."""
        if self._running:
            return

        self._running = True
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

    async def stop(self) -> None:
        """Gracefully shuts down all workers, adapters, and egress connections."""
        if not self._running:
            return

        self._running = False
        logger.info("Stopping GatewayEngine...")

        # 1. Cancel adapter worker tasks
        for task in self._adapter_tasks:
            task.cancel()
        if self._adapter_tasks:
            await asyncio.gather(*self._adapter_tasks, return_exceptions=True)
        self._adapter_tasks.clear()

        # 2. Cancel router task
        if self._router_task is not None:
            self._router_task.cancel()
            try:
                await self._router_task
            except asyncio.CancelledError:  # pragma: no cover
                pass
            self._router_task = None

        # 3. Stop all adapters
        for adapter in self.adapters:
            try:
                await adapter.stop()
            except Exception as e:
                logger.warning("Error stopping adapter %s: %s", adapter.name, e)

        # 4. Stop all egress clients
        for egress in self.egress_clients:
            try:
                await egress.stop()
            except Exception as e:
                logger.warning("Error stopping egress client %s: %s", egress.name, e)

        logger.info("GatewayEngine stopped")

    async def _consume_adapter(self, adapter: BaseTelemetryAdapter) -> None:
        """Consumes frames from a single adapter stream and pushes to the router queue."""
        try:
            async for frame in adapter.stream():
                if not self._running:
                    break
                self._stats.frames_ingested += 1

                # If the internal queue is full, evict the oldest to preserve low latency
                if self._router_queue.full():
                    try:
                        self._router_queue.get_nowait()
                        self._stats.frames_dropped += 1
                    except asyncio.QueueEmpty:  # pragma: no cover
                        pass

                await self._router_queue.put(frame)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("Ingest stream failed for %s: %s", adapter.name, exc)
            self._stats.errors_count += 1

    async def _router_loop(self) -> None:
        """Pulls frames from the router queue and fans out to all egress clients."""
        while self._running:
            try:
                frame = await asyncio.wait_for(self._router_queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

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

    async def __aenter__(self) -> GatewayEngine:
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Async context manager exit."""
        await self.stop()
