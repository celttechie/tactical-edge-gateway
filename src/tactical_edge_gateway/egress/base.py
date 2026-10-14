"""Base network egress transport interface.

Defines the abstract interface for streaming Cursor on Target (CoT) XML
packets to downstream C2 endpoints (ATAK, WinTAK, FreeTAKServer, TAK Server).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone

from tactical_edge_gateway.core.models import TelemetryFrame
from tactical_edge_gateway.transform.cot import CoTTransformer


@dataclass
class EgressStats:
    """Telemetry egress operational metrics."""

    packets_sent: int = 0
    bytes_sent: int = 0
    errors_count: int = 0
    last_sent_at: datetime | None = None


class BaseEgressClient(ABC):
    """Abstract base class for all C2 network egress clients."""

    def __init__(
        self,
        name: str,
        transformer: CoTTransformer | None = None,
    ) -> None:
        """Initializes the egress client.

        Args:
            name: Human-readable identifier for logging and monitoring.
            transformer: Optional CoTTransformer instance for frame-to-XML conversion.
        """
        self.name = name
        self.transformer = transformer or CoTTransformer()
        self._running = False
        self._stats = EgressStats()

    @property
    def is_running(self) -> bool:
        """Indicates whether the egress client is actively running."""
        return self._running

    @property
    def stats(self) -> EgressStats:
        """Current operational egress telemetry statistics."""
        return self._stats

    @abstractmethod
    async def start(self) -> None:
        """Starts background tasks, socket bindings, or connection loops."""

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shuts down network connections and worker tasks."""

    @abstractmethod
    async def send_cot(self, cot_xml: str | bytes) -> bool:
        """Transmits raw CoT XML to the destination.

        Args:
            cot_xml: CoT XML string or UTF-8 encoded bytes.

        Returns:
            True if packet was successfully transmitted/enqueued, False otherwise.
        """

    async def send_frame(
        self,
        frame: TelemetryFrame,
        *,
        cot_type: str | None = None,
        stale_duration_s: float | None = None,
        include_declaration: bool = False,
    ) -> bool:
        """Converts a TelemetryFrame into CoT XML and transmits it.

        Args:
            frame: Canonical TelemetryFrame to send.
            cot_type: Optional explicit CoT type override.
            stale_duration_s: Optional custom stale duration in seconds.
            include_declaration: Whether to include XML declaration header.

        Returns:
            True if packet was successfully transmitted/enqueued, False otherwise.
        """
        cot_bytes = self.transformer.transform_bytes(
            frame,
            cot_type=cot_type,
            stale_duration_s=stale_duration_s,
            include_declaration=include_declaration,
        )
        return await self.send_cot(cot_bytes)

    def _record_success(self, byte_count: int) -> None:
        """Internal helper recording successful transmission metrics."""
        self._stats.packets_sent += 1
        self._stats.bytes_sent += byte_count
        self._stats.last_sent_at = datetime.now(timezone.utc)

    def _record_error(self) -> None:
        """Internal helper recording transmission failures."""
        self._stats.errors_count += 1
