"""Tactical Edge Gateway - Structured Configuration Models.

Defines declarative, strongly typed Pydantic models for configuring telemetry
ingestion adapters, downstream C2 egress transports, and store-and-forward
resilience buffers. Decouples core engine orchestration from CLI parsing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tactical_edge_gateway.core.models import VehicleDomain


def parse_host_port(target: str, default_port: int) -> tuple[str, int]:
    """Parses a 'host:port' or 'host' string into a (host, port) tuple.

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


class MavlinkConfig(BaseModel):
    """Configuration for MAVLink 2.0 telemetry ingestion adapter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    connection_string: str = Field(
        ...,
        description="MAVLink connection URI (e.g. 'udpin:0.0.0.0:14550')",
    )

    default_domain: VehicleDomain = Field(
        default=VehicleDomain.AIR,
        description="Default domain assigned to incoming MAVLink systems",
    )
    callsign_prefix: str = Field(
        default="MAVLINK",
        description="Callsign prefix for discovered MAVLink vehicles",
    )


class ROS2Config(BaseModel):
    """Configuration for ROS2 / DDS ground vehicle telemetry adapter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    uid: str = Field(
        default="ugv-ros2-01",
        description="Unique identifier for the ROS2 vehicle",
    )
    callsign: str = Field(
        default="ROVER-ALPHA",
        description="Tactical callsign for the ROS2 platform",
    )
    domain: VehicleDomain = Field(
        default=VehicleDomain.GROUND,
        description="Vehicle domain categorization (default: ground)",
    )
    fix_topic: str = Field(
        default="/fix",
        description="ROS2 NavSatFix topic name",
    )
    odom_topic: str = Field(
        default="/odom",
        description="ROS2 Odometry topic name",
    )


class SyntheticConfig(BaseModel):
    """Configuration for synthetic kinematic trajectory generator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    uid: str = Field(
        default="synth-01",
        description="Unique identifier for synthetic platform",
    )
    callsign: str = Field(
        default="VIPER-SYNTH",
        description="Tactical callsign for synthetic platform",
    )
    domain: VehicleDomain = Field(
        default=VehicleDomain.AIR,
        description="Vehicle operational domain (air or ground)",
    )
    altitude_msl_m: float = Field(
        default=120.0,
        description="Mean Sea Level altitude in meters",
    )
    speed_mps: float = Field(
        default=15.0,
        ge=0.0,
        description="Simulated ground speed in meters/second",
    )
    rate_hz: float = Field(
        default=1.0,
        gt=0.0,
        description="Telemetry publication frequency in Hertz",
    )


class UdpEgressConfig(BaseModel):
    """Configuration for ATAK/TAK UDP egress transport."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = Field(
        default="127.0.0.1",
        description="Target destination IP or multicast group address",
    )
    port: int = Field(
        default=4242,
        ge=1,
        le=65535,
        description="Target destination UDP port",
    )
    broadcast: bool = Field(
        default=False,
        description="Enable socket broadcast permissions",
    )
    multicast_ttl: int = Field(
        default=1,
        ge=1,
        le=255,
        description="Time-To-Live hop limit for multicast traffic",
    )
    queue_size: int = Field(
        default=1000,
        gt=0,
        description="Capacity of egress backpressure queue",
    )


class TcpEgressConfig(BaseModel):
    """Configuration for TAK Server streaming TCP/TLS egress transport."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = Field(
        default="127.0.0.1",
        description="Target TAK Server hostname or IP address",
    )
    port: int = Field(
        default=8087,
        ge=1,
        le=65535,
        description="Target TAK Server TCP port (default: 8087, TLS: 8089)",
    )
    use_tls: bool = Field(
        default=False,
        description="Enable TLS encryption for socket transport",
    )
    reconnect_interval_s: float = Field(
        default=5.0,
        ge=0.0,
        description="Initial delay in seconds between reconnection attempts",
    )

    queue_size: int = Field(
        default=1000,
        gt=0,
        description="Capacity of egress backpressure queue",
    )


class BufferConfig(BaseModel):
    """Configuration for SQLite Write-Ahead Logging store-and-forward buffer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    db_path: str = Field(
        default="data/buffer.db",
        description="Filesystem path to SQLite database file or ':memory:'",
    )
    max_records: int = Field(
        default=10000,
        gt=0,
        description="Maximum records preserved before FIFO pruning",
    )
    replay_rate_hz: float = Field(
        default=20.0,
        gt=0.0,
        description="Burst replay rate limit in messages per second upon reconnect",
    )


class GatewayConfig(BaseModel):
    """Root configuration schema for Tactical Edge Gateway."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    queue_size: int = Field(
        default=1000,
        gt=0,
        description="Internal telemetry routing queue capacity",
    )
    mavlink: MavlinkConfig | None = Field(
        default=None,
        description="Optional MAVLink 2.0 ingestion configuration",
    )
    ros2: ROS2Config | None = Field(
        default=None,
        description="Optional ROS2 / DDS ingestion configuration",
    )
    synthetic: SyntheticConfig | None = Field(
        default=None,
        description="Optional synthetic telemetry generator configuration",
    )
    udp_egress: list[UdpEgressConfig] = Field(
        default_factory=list,
        description="List of UDP egress endpoints",
    )
    tcp_egress: list[TcpEgressConfig] = Field(
        default_factory=list,
        description="List of TCP / TAK Server streaming egress endpoints",
    )
    buffer: BufferConfig | None = Field(
        default=None,
        description="Optional store-and-forward SQLite persistence configuration",
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GatewayConfig:
        """Constructs and validates a GatewayConfig from a raw dictionary.

        Args:
            data: Configuration dictionary.

        Returns:
            Validated GatewayConfig instance.
        """
        return cls.model_validate(data)

    @classmethod
    def from_json_file(cls, path: str | Path) -> GatewayConfig:
        """Loads and validates a GatewayConfig from a JSON file.

        Args:
            path: Path to the JSON configuration file.

        Returns:
            Validated GatewayConfig instance.
        """
        content = Path(path).read_text(encoding="utf-8")
        return cls.model_validate_json(content)

    @classmethod
    def from_yaml_file(cls, path: str | Path) -> GatewayConfig:
        """Loads and validates a GatewayConfig from a YAML file.

        Args:
            path: Path to the YAML configuration file.

        Returns:
            Validated GatewayConfig instance.
        """
        content = Path(path).read_text(encoding="utf-8")
        try:
            import yaml  # type: ignore[import-untyped]

            data = yaml.safe_load(content)
            if not isinstance(data, dict):
                raise ValueError(f"YAML file {path} did not resolve to a dictionary")
            return cls.model_validate(data)
        except ImportError:
            # Fallback to json parser for JSON-compatible YAML content
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError(f"Configuration file {path} did not resolve to a dictionary")
            return cls.model_validate(data)

    @classmethod
    def from_cli_args(cls, args: argparse.Namespace) -> GatewayConfig:
        """Translates CLI parsed arguments into a structured GatewayConfig.

        Args:
            args: Parsed CLI namespace arguments.

        Returns:
            Validated GatewayConfig instance.
        """
        queue_size = getattr(args, "queue_size", 1000)

        # Ingestion sources
        mavlink_cfg: MavlinkConfig | None = None
        mav_conn = getattr(args, "mavlink", None)
        if mav_conn:
            mavlink_cfg = MavlinkConfig(connection_string=mav_conn)

        ros2_cfg: ROS2Config | None = None
        if getattr(args, "ros2", False):
            ros2_cfg = ROS2Config(
                uid=getattr(args, "ros2_uid", "ugv-ros2-01"),
                callsign=getattr(args, "ros2_callsign", "ROVER-ALPHA"),
                domain=VehicleDomain.GROUND,
                fix_topic=getattr(args, "ros2_fix_topic", "/fix"),
                odom_topic=getattr(args, "ros2_odom_topic", "/odom"),
            )

        synth_cfg: SyntheticConfig | None = None
        if getattr(args, "synthetic", False) or (not mavlink_cfg and not ros2_cfg):
            raw_domain = getattr(args, "synthetic_domain", "air")
            synth_domain = VehicleDomain(raw_domain) if isinstance(raw_domain, str) else raw_domain
            synth_cfg = SyntheticConfig(
                uid=getattr(args, "synthetic_uid", "synth-01"),
                callsign=getattr(args, "synthetic_callsign", "VIPER-SYNTH"),
                domain=synth_domain,
            )

        # Egress transports
        udp_egress: list[UdpEgressConfig] = []
        tak_udp = getattr(args, "tak_udp", None)
        if tak_udp:
            host, port = parse_host_port(tak_udp, default_port=4242)
            udp_egress.append(UdpEgressConfig(host=host, port=port, queue_size=queue_size))

        tcp_egress: list[TcpEgressConfig] = []
        tak_tcp = getattr(args, "tak_tcp", None)
        if tak_tcp:
            host, port = parse_host_port(tak_tcp, default_port=8087)
            use_tls = bool(getattr(args, "tak_tcp_tls", False))
            tcp_egress.append(
                TcpEgressConfig(host=host, port=port, use_tls=use_tls, queue_size=queue_size)
            )

        if not udp_egress and not tcp_egress:
            # Default fallback to UDP 127.0.0.1:4242
            udp_egress.append(UdpEgressConfig(host="127.0.0.1", port=4242, queue_size=queue_size))

        # Buffer configuration
        buffer_cfg: BufferConfig | None = None
        buf_path = getattr(args, "buffer_path", None)
        if buf_path:
            buffer_cfg = BufferConfig(
                db_path=buf_path,
                max_records=getattr(args, "max_buffer_records", 10000),
                replay_rate_hz=getattr(args, "replay_rate_hz", 20.0),
            )

        return cls(
            queue_size=queue_size,
            mavlink=mavlink_cfg,
            ros2=ros2_cfg,
            synthetic=synth_cfg,
            udp_egress=udp_egress,
            tcp_egress=tcp_egress,
            buffer=buffer_cfg,
        )
