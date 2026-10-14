"""Unit tests for tactical C2 egress clients (UDP and TCP)."""

from __future__ import annotations

import pytest

from tactical_edge_gateway.core.models import GeodeticPosition, TelemetryFrame, VehicleDomain
from tactical_edge_gateway.egress.base import EgressStats
from tactical_edge_gateway.egress.tcp import TcpEgressClient
from tactical_edge_gateway.egress.udp import UdpEgressClient, _is_multicast_address


@pytest.fixture
def sample_frame() -> TelemetryFrame:
    """Provides a sample TelemetryFrame for egress tests."""
    return TelemetryFrame(
        uid="uav-test-01",
        callsign="VIPER-TEST",
        domain=VehicleDomain.AIR,
        position=GeodeticPosition(latitude=36.0, longitude=-78.0, altitude_msl_m=100.0),
    )


def test_multicast_address_detection():
    """Verify multicast IP address detection helper."""
    assert _is_multicast_address("239.2.3.1") is True
    assert _is_multicast_address("224.0.0.1") is True
    assert _is_multicast_address("127.0.0.1") is False
    assert _is_multicast_address("192.168.1.100") is False
    assert _is_multicast_address("invalid_ip") is False


def test_egress_stats_defaults():
    """Verify default initial stats values."""
    stats = EgressStats()
    assert stats.packets_sent == 0
    assert stats.bytes_sent == 0
    assert stats.errors_count == 0
    assert stats.last_sent_at is None


@pytest.mark.asyncio
async def test_udp_egress_initialization_and_send_frame(sample_frame: TelemetryFrame):
    """Verify UdpEgressClient lifecycle and send_frame serialization."""
    client = UdpEgressClient(host="127.0.0.1", port=4242)
    assert not client.is_running
    assert client.host == "127.0.0.1"
    assert client.port == 4242
    assert client.name == "udp-egress-127.0.0.1:4242"

    await client.start()
    assert client.is_running

    # Enqueue a frame
    enqueued = await client.send_frame(sample_frame)
    assert enqueued is True

    # Sending when stopped fails
    await client.stop()
    assert not client.is_running
    assert await client.send_frame(sample_frame) is False
    assert client.stats.errors_count >= 1


@pytest.mark.asyncio
async def test_udp_egress_queue_backpressure_eviction():
    """Verify oldest datagram is evicted when UDP queue fills."""
    client = UdpEgressClient(host="127.0.0.1", port=4242, queue_size=2)
    client._running = True

    # Fill queue
    await client.send_cot("<event uid='1'/>")
    await client.send_cot("<event uid='2'/>")
    assert client._queue.qsize() == 2

    # Third send should evict oldest without error
    success = await client.send_cot("<event uid='3'/>")
    assert success is True
    assert client._queue.qsize() == 2

    q1 = client._queue.get_nowait()
    q2 = client._queue.get_nowait()
    assert q1 == b"<event uid='2'/>"
    assert q2 == b"<event uid='3'/>"


@pytest.mark.asyncio
async def test_tcp_egress_initialization_and_send_frame(sample_frame: TelemetryFrame):
    """Verify TcpEgressClient configuration and queue buffering."""
    client = TcpEgressClient(host="127.0.0.1", port=8087)
    assert not client.is_running
    assert not client.is_connected
    assert client.name == "tcp-egress-127.0.0.1:8087"

    await client.start()
    assert client.is_running

    enqueued = await client.send_frame(sample_frame)
    assert enqueued is True

    await client.stop()
    assert not client.is_running
    assert not client.is_connected
    assert await client.send_frame(sample_frame) is False


@pytest.mark.asyncio
async def test_tcp_egress_queue_backpressure_eviction():
    """Verify oldest packet is evicted when TCP queue fills."""
    client = TcpEgressClient(host="127.0.0.1", port=8087, queue_size=2)
    client._running = True

    await client.send_cot("<event uid='1'/>")
    await client.send_cot("<event uid='2'/>")
    assert client._queue.qsize() == 2

    # Third packet evicts oldest
    success = await client.send_cot("<event uid='3'/>")
    assert success is True
    assert client._queue.qsize() == 2

    q1 = client._queue.get_nowait()
    q2 = client._queue.get_nowait()
    assert q1 == b"<event uid='2'/>"
    assert q2 == b"<event uid='3'/>"
