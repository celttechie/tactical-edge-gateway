"""Unit tests for buffer subsystem models and state enumerations."""

import time

import pytest
from pydantic import ValidationError

from tactical_edge_gateway.buffer.models import (
    BufferedMessage,
    BufferState,
    BufferStats,
    CircuitBreakerStats,
    CircuitState,
)


def test_buffer_state_enum_values():
    """Verify BufferState enumeration values."""
    assert BufferState.PENDING.value == "PENDING"
    assert BufferState.TRANSMITTING.value == "TRANSMITTING"
    assert BufferState.SYNCED.value == "SYNCED"
    assert BufferState.FAILED.value == "FAILED"
    assert BufferState.EXPIRED.value == "EXPIRED"


def test_circuit_state_enum_values():
    """Verify CircuitState enumeration values."""
    assert CircuitState.CLOSED.value == "CLOSED"
    assert CircuitState.OPEN.value == "OPEN"
    assert CircuitState.HALF_OPEN.value == "HALF_OPEN"


def test_buffered_message_creation_and_staleness():
    """Verify BufferedMessage attributes, default values, and staleness calculation."""
    now = time.time()
    msg = BufferedMessage(
        id=1,
        uid="uav-01",
        cot_type="a-f-A-M-F",
        timestamp=now,
        stale_timestamp=now + 10.0,
        payload=b"<event uid='uav-01'/>",
    )
    assert msg.id == 1
    assert msg.uid == "uav-01"
    assert msg.cot_type == "a-f-A-M-F"
    assert msg.payload == b"<event uid='uav-01'/>"
    assert msg.state == BufferState.PENDING
    assert msg.retry_count == 0
    assert msg.synced_at is None
    assert not msg.is_stale

    # Stale message
    stale_msg = BufferedMessage(
        id=2,
        uid="uav-01",
        cot_type="a-f-A-M-F",
        timestamp=now - 20.0,
        stale_timestamp=now - 5.0,
        payload=b"<event uid='uav-01'/>",
    )
    assert stale_msg.is_stale


def test_buffered_message_immutability_and_validation():
    """Verify BufferedMessage enforces immutability and forbids extra fields."""
    msg = BufferedMessage(
        id=1,
        uid="uav-01",
        cot_type="a-f-A-M-F",
        timestamp=100.0,
        stale_timestamp=110.0,
        payload=b"test",
    )
    with pytest.raises(ValidationError):
        msg.state = BufferState.SYNCED  # type: ignore[misc]

    with pytest.raises(ValidationError):
        BufferedMessage(
            id=2,
            uid="uav-01",
            cot_type="a-f-A-M-F",
            timestamp=100.0,
            stale_timestamp=110.0,
            payload=b"test",
            extra_field="invalid",  # type: ignore[call-arg]
        )


def test_buffer_stats_defaults_and_validation():
    """Verify BufferStats default counts and model constraints."""
    stats = BufferStats()
    assert stats.total_records == 0
    assert stats.pending_records == 0
    assert stats.synced_records == 0
    assert stats.failed_records == 0
    assert stats.expired_records == 0
    assert stats.db_size_bytes == 0

    with pytest.raises(ValidationError):
        stats.total_records = 5  # type: ignore[misc]

    with pytest.raises(ValidationError):
        BufferStats(extra="invalid")  # type: ignore[call-arg]


def test_circuit_breaker_stats_defaults_and_validation():
    """Verify CircuitBreakerStats default counts and model constraints."""
    stats = CircuitBreakerStats()
    assert stats.state == CircuitState.CLOSED
    assert stats.failure_count == 0
    assert stats.consecutive_failures == 0
    assert stats.success_count == 0
    assert stats.consecutive_successes == 0
    assert stats.total_trips == 0
    assert stats.last_failure_at is None
    assert stats.last_success_at is None

    with pytest.raises(ValidationError):
        stats.total_trips = 1  # type: ignore[misc]

    with pytest.raises(ValidationError):
        CircuitBreakerStats(unknown=True)  # type: ignore[call-arg]
