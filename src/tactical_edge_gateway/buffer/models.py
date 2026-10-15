"""Data models and state enumerations for the DIL store-and-forward buffer."""

from __future__ import annotations

import time
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class BufferState(str, Enum):
    """Lifecycle states for messages stored in the persistent buffer."""

    PENDING = "PENDING"
    TRANSMITTING = "TRANSMITTING"
    SYNCED = "SYNCED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class CircuitState(str, Enum):
    """Operating states of the network circuit breaker."""

    CLOSED = "CLOSED"  # Normal operation; link healthy and transmitting
    OPEN = "OPEN"  # Link failure detected; traffic diverted to disk buffer
    HALF_OPEN = "HALF_OPEN"  # Probing link recovery with throttled traffic


class BufferedMessage(BaseModel):
    """Represents a telemetry message persisted in the SQLite buffer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    uid: str
    cot_type: str
    timestamp: float
    stale_timestamp: float
    payload: bytes
    state: BufferState = BufferState.PENDING
    retry_count: int = 0
    created_at: float = Field(default_factory=time.time)
    synced_at: float | None = None

    @property
    def is_stale(self) -> bool:
        """Determines if the message has passed its CoT stale expiration timestamp."""
        return time.time() >= self.stale_timestamp


class BufferStats(BaseModel):
    """Aggregated operational statistics of the persistent SQLite buffer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total_records: int = 0
    pending_records: int = 0
    synced_records: int = 0
    failed_records: int = 0
    expired_records: int = 0
    db_size_bytes: int = 0


class CircuitBreakerStats(BaseModel):
    """Operational telemetry of the network circuit breaker."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    consecutive_failures: int = 0
    success_count: int = 0
    consecutive_successes: int = 0
    total_trips: int = 0
    last_failure_at: float | None = None
    last_success_at: float | None = None
    last_state_change_at: float = Field(default_factory=time.time)
