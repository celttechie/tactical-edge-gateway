"""Tactical Edge Gateway - Unified Operational Metrics & Statistics Hierarchy.

Defines standardized, strongly typed Pydantic models for operational telemetry
across all gateway components (core engine, ingest adapters, egress clients,
and buffer subsystems). Supports uniform JSON export and Prometheus/REST ingestion.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StatsBase(BaseModel):
    """Base class for all operational telemetry statistics models."""

    model_config = ConfigDict(extra="forbid")

    def to_dict(self) -> dict[str, Any]:
        """Serializes statistics to a Python dictionary."""
        return self.model_dump()

    def to_json(self) -> str:
        """Serializes statistics to a JSON formatted string."""
        return self.model_dump_json()


class EngineStats(StatsBase):
    """Operational telemetry routing statistics for GatewayEngine."""

    frames_ingested: int = Field(
        default=0,
        ge=0,
        description="Total telemetry frames received from all ingest adapters",
    )
    frames_routed: int = Field(
        default=0,
        ge=0,
        description="Total frames successfully transformed and forwarded to egress transports",
    )
    frames_dropped: int = Field(
        default=0,
        ge=0,
        description="Frames discarded due to queue backpressure or validation failure",
    )
    frames_buffered: int = Field(
        default=0,
        ge=0,
        description="Frames persisted to offline buffer during link degradation",
    )
    errors_count: int = Field(
        default=0,
        ge=0,
        description="Total runtime errors encountered during routing or dispatch",
    )
    started_at: datetime | None = Field(
        default=None,
        description="Timestamp when the engine router was started in UTC",
    )


class AdapterStats(StatsBase):
    """Operational metrics for telemetry ingestion adapters."""

    frames_received: int = Field(
        default=0,
        ge=0,
        description="Raw telemetry messages received from hardware or DDS bus",
    )
    frames_parsed: int = Field(
        default=0,
        ge=0,
        description="Frames successfully converted into canonical TelemetryFrame models",
    )
    frames_dropped: int = Field(
        default=0,
        ge=0,
        description="Messages dropped due to invalid schema, coordinates, or backpressure",
    )
    errors_count: int = Field(
        default=0,
        ge=0,
        description="Total protocol parsing or socket communication errors",
    )
    last_received_at: datetime | None = Field(
        default=None,
        description="UTC timestamp of the most recent message received",
    )


class EgressStats(StatsBase):
    """Operational transmission statistics for C2 egress transports."""

    packets_sent: int = Field(
        default=0,
        ge=0,
        description="Total Cursor on Target packets dispatched downstream",
    )
    bytes_sent: int = Field(
        default=0,
        ge=0,
        description="Total payload bytes transmitted across the wire",
    )
    errors_count: int = Field(
        default=0,
        ge=0,
        description="Transmission or socket write failures",
    )
    last_sent_at: datetime | None = Field(
        default=None,
        description="UTC timestamp of the most recent packet transmission",
    )
