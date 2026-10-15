"""Base telemetry ingestion interface.

Defines the abstract contract for heterogeneous autonomy protocol adapters
normalizing raw byte or message streams into canonical TelemetryFrames.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import AsyncIterator

from tactical_edge_gateway.core.models import TelemetryFrame
from tactical_edge_gateway.core.service import AsyncService


class BaseTelemetryAdapter(AsyncService):
    """Abstract base class for all telemetry ingest adapters."""

    def __init__(self, name: str) -> None:
        """Initializes the telemetry adapter with a descriptive label.

        Args:
            name: Human-readable identifier for logging and telemetry.
        """
        super().__init__(name=name)

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
