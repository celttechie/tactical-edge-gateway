"""Standardized asynchronous service lifecycle and context management.

Provides a unified lifecycle contract (start, stop, context manager) for all
long-running components across the gateway architecture.
"""

from __future__ import annotations

import logging
from abc import ABC
from typing import TypeVar

logger = logging.getLogger(__name__)

ServiceT = TypeVar("ServiceT", bound="AsyncService")


class AsyncService(ABC):
    """Abstract base class standardizing lifecycle and context management for async services.

    Ensures re-entrant idempotency guards for `start()` and `stop()`, consistent
    `is_running` state inspection, and built-in asynchronous context manager (`async with`)
    support across ingestion adapters, egress clients, synchronization workers, and engines.
    """

    def __init__(self, name: str) -> None:
        """Initializes the async service.

        Args:
            name: Human-readable identifier for logging and telemetry.
        """
        self.name: str = name
        self._running: bool = False

    @property
    def is_running(self) -> bool:
        """Indicates whether the service is actively running."""
        return self._running

    async def start(self) -> None:
        """Idempotently starts the service, invoking `_on_start()` if not already running."""
        if self._running:
            return
        self._running = True
        logger.debug("Service %s starting", self.name)
        try:
            await self._on_start()
            logger.info("Service %s started", self.name)
        except Exception:
            self._running = False
            raise

    async def stop(self) -> None:
        """Idempotently stops the service, invoking `_on_stop()` if running."""
        if not self._running:
            return
        self._running = False
        logger.debug("Service %s stopping", self.name)
        try:
            await self._on_stop()
            logger.info("Service %s stopped", self.name)
        except Exception as exc:
            logger.error("Error stopping service %s: %s", self.name, exc)
            raise

    async def _on_start(self) -> None:
        """Hook called when the service is starting. Subclasses may override."""

    async def _on_stop(self) -> None:
        """Hook called when the service is stopping. Subclasses may override."""

    async def __aenter__(self: ServiceT) -> ServiceT:
        """Enters the async context manager, starting the service."""
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Exits the async context manager, stopping the service."""
        await self.stop()
