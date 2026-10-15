"""Unit tests for AsyncService lifecycle and context management."""

from __future__ import annotations

import pytest

from tactical_edge_gateway.core.service import AsyncService


class DummyService(AsyncService):
    """Concrete service for testing lifecycle hooks."""

    def __init__(self, name: str = "dummy-service") -> None:
        super().__init__(name=name)
        self.start_calls = 0
        self.stop_calls = 0
        self.raise_on_start = False
        self.raise_on_stop = False

    async def _on_start(self) -> None:
        if self.raise_on_start:
            raise RuntimeError("Forced start failure")
        self.start_calls += 1

    async def _on_stop(self) -> None:
        if self.raise_on_stop:
            raise RuntimeError("Forced stop failure")
        self.stop_calls += 1


class MinimalService(AsyncService):
    """Concrete service relying on default no-op lifecycle hooks."""


@pytest.mark.asyncio
async def test_async_service_standard_lifecycle():
    """Verify start and stop flow with idempotency guarantees."""
    service = DummyService("test-svc")
    assert not service.is_running
    assert service.name == "test-svc"
    assert service.start_calls == 0
    assert service.stop_calls == 0

    # Start service
    await service.start()
    assert service.is_running
    assert service.start_calls == 1

    # Idempotent start call should be a no-op
    await service.start()
    assert service.is_running
    assert service.start_calls == 1

    # Stop service
    await service.stop()
    assert not service.is_running
    assert service.stop_calls == 1

    # Idempotent stop call should be a no-op
    await service.stop()
    assert not service.is_running
    assert service.stop_calls == 1


@pytest.mark.asyncio
async def test_async_service_start_exception_resets_state():
    """Verify start exception resets is_running flag to False."""
    service = DummyService("fail-start")
    service.raise_on_start = True

    with pytest.raises(RuntimeError, match="Forced start failure"):
        await service.start()

    assert not service.is_running


@pytest.mark.asyncio
async def test_async_service_stop_exception_propagates():
    """Verify stop exception propagates while updating state."""
    service = DummyService("fail-stop")
    await service.start()
    assert service.is_running

    service.raise_on_stop = True
    with pytest.raises(RuntimeError, match="Forced stop failure"):
        await service.stop()

    assert not service.is_running


@pytest.mark.asyncio
async def test_async_service_context_manager():
    """Verify async with syntax starts and stops the service automatically."""
    service = DummyService("cm-service")

    async with service as s:
        assert s is service
        assert s.is_running
        assert s.start_calls == 1
        assert s.stop_calls == 0

    assert not service.is_running
    assert service.stop_calls == 1


@pytest.mark.asyncio
async def test_async_service_context_manager_on_error():
    """Verify async with cleans up even if exception raised within block."""
    service = DummyService("cm-error")

    with pytest.raises(ValueError, match="Block error"):
        async with service:
            assert service.is_running
            raise ValueError("Block error")

    assert not service.is_running
    assert service.stop_calls == 1


@pytest.mark.asyncio
async def test_async_service_default_hooks():
    """Verify service without overridden hooks runs and stops without error."""
    service = MinimalService("minimal")
    assert not service.is_running

    await service.start()
    assert service.is_running

    await service.stop()
    assert not service.is_running


@pytest.mark.asyncio
async def test_concrete_services_context_management():
    """Verify adapters and egress clients support async with context manager syntax."""
    from tactical_edge_gateway.egress.tcp import TcpEgressClient
    from tactical_edge_gateway.egress.udp import UdpEgressClient
    from tactical_edge_gateway.ingest.ros2 import ROS2Adapter
    from tactical_edge_gateway.ingest.synthetic import SyntheticAdapter

    # Ingest adapter context management
    synth = SyntheticAdapter(max_iterations=1)
    async with synth as s:
        assert s.is_running
    assert not synth.is_running

    ros2 = ROS2Adapter()
    async with ros2 as r:
        assert r.is_running
    assert not ros2.is_running

    # Egress client context management
    udp = UdpEgressClient(host="127.0.0.1", port=4444)
    async with udp as u:
        assert u.is_running
    assert not udp.is_running

    tcp = TcpEgressClient(host="127.0.0.1", port=8888)
    async with tcp as t:
        assert t.is_running
    assert not tcp.is_running
