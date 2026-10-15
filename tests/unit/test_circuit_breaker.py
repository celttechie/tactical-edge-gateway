"""Unit tests for network circuit breaker link health monitoring."""

import time
from unittest.mock import patch

from tactical_edge_gateway.buffer.circuit_breaker import CircuitBreaker
from tactical_edge_gateway.buffer.models import CircuitState


def test_circuit_breaker_initialization_defaults():
    """Verify CircuitBreaker default values and initial CLOSED state."""
    cb = CircuitBreaker()
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True
    stats = cb.stats
    assert stats.failure_count == 0
    assert stats.success_count == 0
    assert stats.total_trips == 0


def test_circuit_breaker_normal_success_flow():
    """Verify recording successes increments success counters and clears failures."""
    cb = CircuitBreaker()
    cb.record_failure()
    assert cb.stats.consecutive_failures == 1

    cb.record_success()
    assert cb.stats.consecutive_failures == 0
    assert cb.stats.consecutive_successes == 1
    assert cb.stats.success_count == 1
    assert cb.stats.last_success_at is not None


def test_circuit_breaker_trips_to_open_on_threshold():
    """Verify circuit trips from CLOSED to OPEN after failure_threshold consecutive errors."""
    cb = CircuitBreaker(failure_threshold=3)

    cb.record_failure()
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    cb.record_failure()
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False
    assert cb.stats.total_trips == 1


def test_circuit_breaker_recovery_timeout_to_half_open():
    """Verify circuit transitions from OPEN to HALF_OPEN when recovery_timeout elapses."""
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=5.0)
    now = time.time()

    with patch("time.time", return_value=now):
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

    # Advance time by 3 seconds (less than recovery_timeout 5.0)
    with patch("time.time", return_value=now + 3.0):
        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

    # Advance time by 5.1 seconds (exceeds recovery_timeout)
    with patch("time.time", return_value=now + 5.1):
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.can_execute() is True


def test_circuit_breaker_half_open_failure_re_trips_with_backoff():
    """Verify failure in HALF_OPEN state immediately re-trips to OPEN with exponential backoff."""
    cb = CircuitBreaker(
        failure_threshold=1,
        recovery_timeout=4.0,
        backoff_factor=2.0,
        max_recovery_timeout=20.0,
    )
    now = time.time()

    with patch("time.time", return_value=now):
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    # Transition to HALF_OPEN
    with patch("time.time", return_value=now + 4.1):
        assert cb.state == CircuitState.HALF_OPEN

        # Failure occurs during probe in HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.stats.total_trips == 2
        # Backoff increased from 4.0 to 8.0
        assert cb._current_recovery_timeout == 8.0


def test_circuit_breaker_half_open_success_resets_to_closed():
    """Verify success_threshold consecutive successes in HALF_OPEN reset to CLOSED."""
    cb = CircuitBreaker(
        failure_threshold=1,
        recovery_timeout=4.0,
        success_threshold=2,
    )
    now = time.time()

    with patch("time.time", return_value=now):
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    # Transition to HALF_OPEN
    with patch("time.time", return_value=now + 4.1):
        assert cb.state == CircuitState.HALF_OPEN

        # First probe success
        cb.record_success()
        assert cb.state == CircuitState.HALF_OPEN

        # Second probe success reaches threshold
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True
        assert cb._current_recovery_timeout == 4.0


def test_circuit_breaker_manual_trip_and_reset():
    """Verify manual trip and reset overrides."""
    cb = CircuitBreaker()
    assert cb.state == CircuitState.CLOSED

    cb.trip()
    assert cb.state == CircuitState.OPEN
    assert cb.stats.total_trips == 1

    cb.reset()
    assert cb.state == CircuitState.CLOSED
    assert cb.stats.consecutive_failures == 0
    assert cb.stats.consecutive_successes == 0


def test_circuit_breaker_backoff_cap():
    """Verify backoff does not exceed max_recovery_timeout."""
    cb = CircuitBreaker(
        failure_threshold=1,
        recovery_timeout=10.0,
        backoff_factor=3.0,
        max_recovery_timeout=15.0,
    )
    cb.trip()
    cb._transition_to(CircuitState.HALF_OPEN)
    cb.record_failure()
    assert cb._current_recovery_timeout == 15.0  # capped at 15.0, not 30.0
