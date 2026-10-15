"""Network circuit breaker detecting link outages and coordinating recovery probes."""

from __future__ import annotations

import logging
import time

from tactical_edge_gateway.buffer.models import CircuitBreakerStats, CircuitState

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Monitors link health and coordinates diversion to persistent storage upon link failure."""

    def __init__(
        self,
        name: str = "network-circuit-breaker",
        failure_threshold: int = 3,
        recovery_timeout: float = 5.0,
        success_threshold: int = 2,
        backoff_factor: float = 1.5,
        max_recovery_timeout: float = 60.0,
    ) -> None:
        """Initializes the circuit breaker.

        Args:
            name: Identifier for logging and diagnostics.
            failure_threshold: Consecutive failures required to trip from CLOSED to OPEN.
            recovery_timeout: Initial seconds in OPEN state before testing HALF_OPEN.
            success_threshold: Consecutive successes in HALF_OPEN required to reset to CLOSED.
            backoff_factor: Multiplier for recovery_timeout on successive trips.
            max_recovery_timeout: Maximum backoff delay in seconds.
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.backoff_factor = backoff_factor
        self.max_recovery_timeout = max_recovery_timeout

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._total_failures = 0
        self._total_successes = 0
        self._total_trips = 0
        self._current_recovery_timeout = recovery_timeout

        self._last_state_change = time.time()
        self._last_failure_at: float | None = None
        self._last_success_at: float | None = None

    @property
    def state(self) -> CircuitState:
        """Returns the current state, evaluating timeout transitions from OPEN to HALF_OPEN."""
        now = time.time()
        if self._state == CircuitState.OPEN:
            elapsed = now - self._last_state_change
            if elapsed >= self._current_recovery_timeout:
                self._transition_to(CircuitState.HALF_OPEN)
        return self._state

    def can_execute(self) -> bool:
        """Determines if the link is available for live transmission attempts."""
        current_state = self.state
        return current_state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self) -> None:
        """Records a successful network transmission."""
        now = time.time()
        self._total_successes += 1
        self._consecutive_failures = 0
        self._last_success_at = now

        if self._state == CircuitState.HALF_OPEN:
            self._consecutive_successes += 1
            if self._consecutive_successes >= self.success_threshold:
                # Reset recovery timeout back to base
                self._current_recovery_timeout = self.recovery_timeout
                self._transition_to(CircuitState.CLOSED)
        elif self._state == CircuitState.CLOSED:
            self._consecutive_successes += 1

    def record_failure(self) -> None:
        """Records a transmission failure, evaluating thresholds to trip the circuit."""
        now = time.time()
        self._total_failures += 1
        self._consecutive_successes = 0
        self._consecutive_failures += 1
        self._last_failure_at = now

        if self._state == CircuitState.CLOSED:
            if self._consecutive_failures >= self.failure_threshold:
                self.trip()
        elif self._state == CircuitState.HALF_OPEN:
            # Probe failed in HALF_OPEN; apply exponential backoff and trip back to OPEN
            self._current_recovery_timeout = min(
                self._current_recovery_timeout * self.backoff_factor,
                self.max_recovery_timeout,
            )
            self.trip()

    def trip(self) -> None:
        """Manually or automatically trips the circuit to OPEN state."""
        self._total_trips += 1
        self._transition_to(CircuitState.OPEN)

    def reset(self) -> None:
        """Manually resets the circuit to CLOSED state and clears error counters."""
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._current_recovery_timeout = self.recovery_timeout
        self._transition_to(CircuitState.CLOSED)

    def _transition_to(self, new_state: CircuitState) -> None:
        """Internal helper handling state transitions and logging."""
        if self._state != new_state:
            prev = self._state
            self._state = new_state
            self._last_state_change = time.time()
            self._consecutive_successes = 0
            logger.warning(
                "Circuit breaker '%s' changed state: %s -> %s (trips=%d, backoff=%.1fs)",
                self.name,
                prev.value,
                new_state.value,
                self._total_trips,
                self._current_recovery_timeout,
            )

    @property
    def stats(self) -> CircuitBreakerStats:
        """Returns current operational telemetry of the circuit breaker."""
        return CircuitBreakerStats(
            state=self.state,
            failure_count=self._total_failures,
            consecutive_failures=self._consecutive_failures,
            success_count=self._total_successes,
            consecutive_successes=self._consecutive_successes,
            total_trips=self._total_trips,
            last_failure_at=self._last_failure_at,
            last_success_at=self._last_success_at,
            last_state_change_at=self._last_state_change,
        )
