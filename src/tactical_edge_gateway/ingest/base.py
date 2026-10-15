"""Base telemetry ingestion interface.

Defines the abstract contract for heterogeneous autonomy protocol adapters
normalizing raw byte or message streams into canonical TelemetryFrames.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from tactical_edge_gateway.core.models import TelemetryFrame


class BaseTelemetryAdapter(ABC):
    """Abstract base class for all telemetry ingest adapters."""

    def __init__(self, name: str) -> None:
        """Initializes the telemetry adapter with a descriptive label.

        Args:
            name: Human-readable identifier for logging and telemetry.
        """
        self.name = name
        self._running = False

    @property
    def is_running(self) -> bool:
        """Indicates whether the adapter is actively ingesting."""
        return self._running

    @abstractmethod
    async def start(self) -> None:
        """Starts network sockets, subscriptions, or reader coroutines."""

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully halts network activity and releases bound resources."""

    @abstractmethod
    async def stream(self) -> AsyncIterator[TelemetryFrame]:
        """Asynchronously emits normalized TelemetryFrame instances.

        Yields:
            TelemetryFrame: Next validated frame from the underlying protocol stream.
        """
        if False:  # pragma: no cover
            yield TelemetryFrame(  # type: ignore[call-arg]
                uid="",
                callsign="",
                domain=None,  # type: ignore[arg-type]
                position=None,  # type: ignore[arg-type]
            )
