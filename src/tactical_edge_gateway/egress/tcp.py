"""TCP Cursor on Target (CoT) Network Egress Client.

Streams Cursor on Target XML packets over persistent TCP (with optional TLS)
to FreeTAKServer and DoD TAK Server instances (e.g. port 8087 / 8089).
Includes automatic reconnection with exponential backoff and queue backpressure.
"""

from __future__ import annotations

import asyncio
import logging
import ssl

from tactical_edge_gateway.egress.base import BaseEgressClient
from tactical_edge_gateway.transform.cot import CoTTransformer

logger = logging.getLogger(__name__)

DEFAULT_TAK_TCP_PORT = 8087
DEFAULT_TAK_TLS_PORT = 8089


class TcpEgressClient(BaseEgressClient):
    """TCP / TLS streaming client for persistent connection to TAK Server."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = DEFAULT_TAK_TCP_PORT,
        *,
        ssl_context: ssl.SSLContext | None = None,
        reconnect_interval_s: float = 1.0,
        max_reconnect_interval_s: float = 30.0,
        transformer: CoTTransformer | None = None,
        queue_size: int = 500,
    ) -> None:
        """Initializes the TCP streaming egress client.

        Args:
            host: TAK Server IP address or hostname.
            port: Destination TCP port (e.g. 8087 plain or 8089 TLS).
            ssl_context: Optional SSLContext configured with client/server certs.
            reconnect_interval_s: Initial retry backoff in seconds.
            max_reconnect_interval_s: Maximum retry backoff in seconds.
            transformer: Optional CoTTransformer instance.
            queue_size: Maximum buffered packets under backpressure.
        """
        super().__init__(name=f"tcp-egress-{host}:{port}", transformer=transformer)
        self.host = host
        self.port = port
        self.ssl_context = ssl_context
        self.reconnect_interval_s = reconnect_interval_s
        self.max_reconnect_interval_s = max_reconnect_interval_s

        self._connected = False
        self._writer: asyncio.StreamWriter | None = None
        self._reader: asyncio.StreamReader | None = None
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=queue_size)
        self._worker_task: asyncio.Task[None] | None = None
        self._rx_task: asyncio.Task[None] | None = None

    @property
    def is_connected(self) -> bool:
        """Indicates whether an active TCP connection to TAK Server is established."""
        return self._connected and self._writer is not None

    async def start(self) -> None:
        """Initiates the background reconnection and transmission loop."""
        if self._running:
            return

        self._running = True
        self._worker_task = asyncio.create_task(self._connection_and_tx_loop())
        logger.info(
            "TCP egress client started for %s:%d (tls=%s)",
            self.host,
            self.port,
            self.ssl_context is not None,
        )

    async def stop(self) -> None:
        """Gracefully closes TCP connection and terminates worker task."""
        self._running = False

        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

        if self._rx_task is not None:
            self._rx_task.cancel()
            self._rx_task = None

        await self._close_connection()
        logger.info("TCP egress client stopped for %s:%d", self.host, self.port)

    async def send_cot(self, cot_xml: str | bytes) -> bool:
        """Queues a CoT XML message for transmission over the TCP stream.

        Args:
            cot_xml: XML string or UTF-8 encoded bytes.

        Returns:
            True if queued successfully, False if dropped or client stopped.
        """
        if not self._running:
            logger.warning("Attempted to send CoT via stopped TCP client %s", self.name)
            self._record_error()
            return False

        payload = cot_xml.encode("utf-8") if isinstance(cot_xml, str) else cot_xml

        try:
            self._queue.put_nowait(payload)
            return True
        except asyncio.QueueFull:
            # Evict oldest packet to maintain real-time situational awareness
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover
                pass
            try:
                self._queue.put_nowait(payload)
                return True
            except asyncio.QueueFull:  # pragma: no cover
                self._record_error()
                return False

    async def _close_connection(self) -> None:
        """Internal helper closing active stream reader/writer."""
        self._connected = False
        if self._rx_task is not None:
            self._rx_task.cancel()
            self._rx_task = None
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception as e:
                logger.debug("Exception closing TCP writer: %s", e)
            self._writer = None
            self._reader = None

    async def _rx_monitor(self, reader: asyncio.StreamReader) -> None:
        """Inbound reader monitoring server connection status and detecting disconnects promptly."""
        try:
            while self._running and self._connected:
                data = await reader.read(4096)
                if not data:
                    break
        except Exception as e:
            logger.debug("Rx monitor connection ended: %s", e)
        finally:
            if self._connected:
                logger.info("Remote TAK Server closed connection on %s:%d", self.host, self.port)
                self._connected = False

    async def _connect(self) -> bool:
        """Attempts to establish a TCP/TLS connection to the target server."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self.host,
                    self.port,
                    ssl=self.ssl_context,
                ),
                timeout=5.0,
            )
            self._reader = reader
            self._writer = writer
            self._connected = True
            self._rx_task = asyncio.create_task(self._rx_monitor(reader))
            logger.info("Connected TCP egress to %s:%d", self.host, self.port)
            return True
        except Exception as e:
            logger.debug("Failed connection to %s:%d: %s", self.host, self.port, e)
            self._connected = False
            return False

    async def _connection_and_tx_loop(self) -> None:
        """Main supervisory loop maintaining connectivity and transmitting queued CoT."""
        backoff = self.reconnect_interval_s

        while self._running:
            # 1. Maintain connection
            if not self._connected or self._writer is None:
                connected = await self._connect()
                if not connected:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2.0, self.max_reconnect_interval_s)
                    continue
                # Connected successfully - reset backoff
                backoff = self.reconnect_interval_s

            # 2. Dequeue and transmit
            try:
                payload = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                if self._writer is not None:
                    self._writer.write(payload)
                    await self._writer.drain()
                    self._record_success(len(payload))
                    self._queue.task_done()
            except asyncio.TimeoutError:
                # Idle heartbeat check to verify connection remains alive
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(
                    "TCP socket error during transmit to %s:%d: %s", self.host, self.port, e
                )
                self._record_error()
                await self._close_connection()
                await asyncio.sleep(0.5)
