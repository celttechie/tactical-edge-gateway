"""UDP Cursor on Target (CoT) Network Egress Client.

Broadcasts or multicasts Cursor on Target XML packets over UDP to tactical
ATAK/WinTAK clients and FreeTAKServer listeners (e.g. port 4242 or 6969).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket

from tactical_edge_gateway.core.queue import EvictingQueue
from tactical_edge_gateway.egress.base import BaseEgressClient
from tactical_edge_gateway.transform.cot import CoTTransformer

logger = logging.getLogger(__name__)

DEFAULT_TAK_UDP_PORT = 4242
DEFAULT_TAK_MULTICAST_GROUP = "239.2.3.1"
DEFAULT_TAK_MULTICAST_PORT = 6969


def _is_multicast_address(host: str) -> bool:
    """Checks whether an IP address belongs to IPv4 multicast range (224.0.0.0/4)."""
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_multicast
    except ValueError:
        return False


class UdpEgressClient(BaseEgressClient):
    """UDP Egress client for transmitting CoT XML via unicast, broadcast, or multicast."""

    def __init__(
        self,
        host: str = DEFAULT_TAK_MULTICAST_GROUP,
        port: int = DEFAULT_TAK_MULTICAST_PORT,
        *,
        broadcast: bool = False,
        multicast_ttl: int = 1,
        transformer: CoTTransformer | None = None,
        queue_size: int = 500,
    ) -> None:
        """Initializes the UDP egress client.

        Args:
            host: Destination IP address (unicast, broadcast, or multicast group).
            port: Destination UDP port (e.g. 4242 or 6969).
            broadcast: Explicitly enable SO_BROADCAST for subnet broadcast.
            multicast_ttl: Time-to-live for multicast datagrams.
            transformer: Optional CoTTransformer instance.
            queue_size: Maximum buffered packets under transmission backpressure.
        """
        super().__init__(name=f"udp-egress-{host}:{port}", transformer=transformer)
        self.host = host
        self.port = port
        self.broadcast = broadcast or host.endswith(".255")
        self.multicast_ttl = multicast_ttl
        self.is_multicast = _is_multicast_address(host)

        self._socket: socket.socket | None = None
        self._queue: EvictingQueue[bytes] = EvictingQueue(maxsize=queue_size)
        self._worker_task: asyncio.Task[None] | None = None

    async def _on_start(self) -> None:
        """Opens the UDP socket and starts the background transmitter loop."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)

        if self.broadcast:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        if self.is_multicast:
            # Set TTL for multicast packets
            sock.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_MULTICAST_TTL,
                self.multicast_ttl,
            )
            # Enable local loopback delivery so local ATAK/test instances receive datagrams
            sock.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_MULTICAST_LOOP,
                1,
            )

        self._socket = sock
        self._worker_task = asyncio.create_task(self._transmitter_loop())
        logger.info(
            "UDP egress client started for %s:%d (multicast=%s, broadcast=%s)",
            self.host,
            self.port,
            self.is_multicast,
            self.broadcast,
        )

    async def _on_stop(self) -> None:
        """Terminates background transmitter and closes UDP socket."""
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

        if self._socket is not None:
            self._socket.close()
            self._socket = None

        logger.info("UDP egress client stopped for %s:%d", self.host, self.port)

    async def send_cot(self, cot_xml: str | bytes) -> bool:
        """Queues a CoT XML message for immediate UDP transmission.

        Args:
            cot_xml: XML string or UTF-8 encoded bytes.

        Returns:
            True if enqueued successfully, False if client stopped or dropped.
        """
        if not self._running:
            logger.warning("Attempted to send CoT via stopped UDP client %s", self.name)
            self._record_error()
            return False

        payload = cot_xml.encode("utf-8") if isinstance(cot_xml, str) else cot_xml

        self._queue.put_nowait_evict(payload)
        return True

    async def _transmitter_loop(self) -> None:
        """Background coroutine dequeueing and transmitting UDP datagrams."""
        while self._running:
            try:
                payload = await self._queue.get()
                if self._socket is None:
                    continue

                # Transmit over UDP socket
                self._socket.sendto(payload, (self.host, self.port))
                self._record_success(len(payload))
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    "Error transmitting UDP datagram to %s:%d: %s", self.host, self.port, e
                )
                self._record_error()
                await asyncio.sleep(0.05)
