"""Telemetry ingestion adapters and streaming interfaces."""

from tactical_edge_gateway.ingest.base import BaseTelemetryAdapter
from tactical_edge_gateway.ingest.synthetic import SyntheticAdapter

__all__ = [
    "BaseTelemetryAdapter",
    "SyntheticAdapter",
]
