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
from pathlib import Path
from typing import Sequence

from tactical_edge_gateway.core.config import GatewayConfig, parse_host_port
from tactical_edge_gateway.core.engine import GatewayEngine

__all__ = [
    "GatewayConfig",
    "GatewayEngine",
    "build_parser",
    "main",
    "parse_host_port",
    "run_gateway",
]

logger = logging.getLogger("tactical_edge_gateway")


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
    parser.add_argument(
        "--config",
        metavar="CONFIG_FILE",
        help="Path to YAML or JSON declarative configuration file",
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

    # DIL Resilience & Persistent Buffering
    buffer_group = parser.add_argument_group("DIL Resilience & Buffering")
    buffer_group.add_argument(
        "--buffer-path",
        metavar="PATH",
        help="Path to persistent SQLite WAL buffer database (e.g. 'data/buffer.db' or ':memory:')",
    )
    buffer_group.add_argument(
        "--max-buffer-records",
        type=int,
        default=10000,
        help="Max buffered records retained in SQLite before FIFO pruning (default: 10000)",
    )
    buffer_group.add_argument(
        "--replay-rate-hz",
        type=float,
        default=20.0,
        help="Burst sync replay rate in messages/sec upon link recovery (default: 20.0)",
    )

    return parser


async def run_gateway(
    args: argparse.Namespace | GatewayConfig,
    stop_event: asyncio.Event | None = None,
) -> int:
    """Instantiates and executes the GatewayEngine supervisor.

    Args:
        args: Parsed CLI namespace arguments or strongly-typed GatewayConfig.
        stop_event: Optional external shutdown event (useful for test suites).

    Returns:
        int: Exit status code (0 for success).
    """
    if isinstance(args, GatewayConfig):
        config = args
    elif getattr(args, "config", None):
        cfg_path = Path(args.config)
        if cfg_path.suffix.lower() in (".yaml", ".yml"):
            config = GatewayConfig.from_yaml_file(cfg_path)
        else:
            config = GatewayConfig.from_json_file(cfg_path)
    else:
        config = GatewayConfig.from_cli_args(args)

    engine = GatewayEngine.from_config(config)

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
