"""Command-line interface and daemon supervisor for Tactical Edge Gateway.

Bridges autonomous telemetry streams (MAVLink, ROS2, Synthetic) to tactical C2
endpoints (ATAK, WinTAK, FreeTAKServer, TAK Server) over UDP and TCP transports.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from typing import Sequence

from tactical_edge_gateway.core.engine import GatewayEngine
from tactical_edge_gateway.core.models import VehicleDomain
from tactical_edge_gateway.egress.tcp import TcpEgressClient
from tactical_edge_gateway.egress.udp import UdpEgressClient
from tactical_edge_gateway.ingest.mavlink import MavlinkAdapter
from tactical_edge_gateway.ingest.ros2 import ROS2Adapter
from tactical_edge_gateway.ingest.synthetic import SyntheticAdapter

logger = logging.getLogger("tactical_edge_gateway")


def parse_host_port(target: str, default_port: int) -> tuple[str, int]:
    """Parses a 'host:port' string into a tuple of (host, port).

    Args:
        target: Target address in format 'host:port' or just 'host'.
        default_port: Fallback port if not specified in target string.

    Returns:
        tuple[str, int]: Cleaned host and integer port.
    """
    if ":" in target:
        host, port_str = target.rsplit(":", 1)
        return host, int(port_str)
    return target, default_port


def build_parser() -> argparse.ArgumentParser:
    """Builds the command-line argument parser for the gateway."""
    parser = argparse.ArgumentParser(
        prog="tactical-edge-gateway",
        description=(
            "Air-gapped resilient tactical telemetry gateway bridging MAVLink and ROS2 "
            "to TAK Server / CoT."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version="tactical-edge-gateway 0.1.0",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Configure log verbosity (default: INFO)",
    )

    # Ingestion Sources
    ingest_group = parser.add_argument_group("Telemetry Ingestion")
    ingest_group.add_argument(
        "--mavlink",
        metavar="CONNECTION",
        help=(
            "MAVLink 2.0 connection string (e.g. 'udpin:0.0.0.0:14550' or "
            "'udpout:192.168.1.50:14550')"
        ),
    )
    ingest_group.add_argument(
        "--ros2",
        action="store_true",
        help="Enable ROS2 / DDS ground telemetry subscriber",
    )
    ingest_group.add_argument(
        "--ros2-fix-topic",
        default="/fix",
        help="ROS2 NavSatFix topic name (default: /fix)",
    )
    ingest_group.add_argument(
        "--ros2-odom-topic",
        default="/odom",
        help="ROS2 Odometry topic name (default: /odom)",
    )
    ingest_group.add_argument(
        "--ros2-uid",
        default="ugv-ros2-01",
        help="Unique identifier for ROS2 vehicle (default: ugv-ros2-01)",
    )
    ingest_group.add_argument(
        "--ros2-callsign",
        default="ROVER-ALPHA",
        help="Tactical callsign for ROS2 vehicle (default: ROVER-ALPHA)",
    )
    ingest_group.add_argument(
        "--synthetic",
        action="store_true",
        help="Enable synthetic telemetry simulator for test/demonstration",
    )
    ingest_group.add_argument(
        "--synthetic-domain",
        default="air",
        choices=["air", "ground"],
        help="Domain for synthetic vehicle: air (UAV) or ground (UGV) (default: air)",
    )
    ingest_group.add_argument(
        "--synthetic-uid",
        default="synth-01",
        help="UID for synthetic vehicle (default: synth-01)",
    )
    ingest_group.add_argument(
        "--synthetic-callsign",
        default="VIPER-SYNTH",
        help="Callsign for synthetic vehicle (default: VIPER-SYNTH)",
    )

    # Egress Transports
    egress_group = parser.add_argument_group("C2 Network Egress")
    egress_group.add_argument(
        "--tak-udp",
        metavar="HOST[:PORT]",
        help=(
            "Target ATAK UDP broadcast or multicast endpoint (e.g. '239.2.3.1:6969' or "
            "'127.0.0.1:4242')"
        ),
    )
    egress_group.add_argument(
        "--tak-tcp",
        metavar="HOST[:PORT]",
        help="Target FreeTAKServer / TAK Server streaming endpoint (e.g. '127.0.0.1:8087')",
    )
    egress_group.add_argument(
        "--tak-tcp-tls",
        action="store_true",
        help="Enable TLS for TCP streaming connection (port 8089)",
    )
    egress_group.add_argument(
        "--queue-size",
        type=int,
        default=1000,
        help="Max internal buffer queue capacity before dropping stale frames (default: 1000)",
    )

    return parser


async def run_gateway(
    args: argparse.Namespace,
    stop_event: asyncio.Event | None = None,
) -> int:
    """Instantiates and executes the GatewayEngine supervisor.

    Args:
        args: Parsed CLI namespace arguments.
        stop_event: Optional external shutdown event (useful for test suites).

    Returns:
        int: Exit status code (0 for success).
    """
    engine = GatewayEngine(queue_size=args.queue_size)

    # Configure Ingestion Adapters
    if args.mavlink:
        logger.info("Enabling MAVLink adapter on %s", args.mavlink)
        engine.add_adapter(MavlinkAdapter(connection_string=args.mavlink))

    if args.ros2:
        logger.info(
            "Enabling ROS2 adapter for %s (%s) on topics %s and %s",
            args.ros2_callsign,
            args.ros2_uid,
            args.ros2_fix_topic,
            args.ros2_odom_topic,
        )
        engine.add_adapter(
            ROS2Adapter(
                uid=args.ros2_uid,
                callsign=args.ros2_callsign,
                domain=VehicleDomain.GROUND,
                fix_topic=args.ros2_fix_topic,
                odom_topic=args.ros2_odom_topic,
            )
        )

    # If synthetic is explicitly requested, or if no other adapters were specified, add synthetic
    if args.synthetic or (not args.mavlink and not args.ros2):
        domain = VehicleDomain(args.synthetic_domain)
        logger.info(
            "Enabling Synthetic adapter: %s (%s, domain=%s)",
            args.synthetic_callsign,
            args.synthetic_uid,
            domain.value,
        )
        engine.add_adapter(
            SyntheticAdapter(
                uid=args.synthetic_uid,
                callsign=args.synthetic_callsign,
                domain=domain,
            )
        )

    # Configure Egress Transports
    has_egress = False
    if args.tak_udp:
        host, port = parse_host_port(args.tak_udp, default_port=4242)
        logger.info("Enabling UDP egress to %s:%d", host, port)
        engine.add_egress(UdpEgressClient(host=host, port=port))
        has_egress = True

    if args.tak_tcp:
        import ssl

        host, port = parse_host_port(args.tak_tcp, default_port=8087)
        ssl_ctx = ssl.create_default_context() if args.tak_tcp_tls else None
        logger.info("Enabling TCP streaming egress to %s:%d (tls=%s)", host, port, args.tak_tcp_tls)
        engine.add_egress(TcpEgressClient(host=host, port=port, ssl_context=ssl_ctx))
        has_egress = True

    if not has_egress:
        # Default to standard loopback ATAK UDP port 4242
        logger.info("No egress target specified; defaulting to UDP 127.0.0.1:4242")
        engine.add_egress(UdpEgressClient(host="127.0.0.1", port=4242))

    # Signal handling
    shutdown_event = stop_event or asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except (NotImplementedError, RuntimeError):
            # Fallback for platforms/environments without signal handler support
            pass

    async with engine:
        logger.info("Tactical Edge Gateway started. Press Ctrl+C to terminate.")
        await shutdown_event.wait()
        logger.info("Shutdown signal received. Exiting...")

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Main CLI entrypoint for tactical-edge-gateway.

    Args:
        argv: Optional command-line arguments (defaults to sys.argv[1:]).

    Returns:
        int: Process exit code.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        return asyncio.run(run_gateway(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
