"""Unit tests for GatewayConfig models, conversion factories, and engine decoupling.

Validates ADR-0010 implementation:
- Declarative Pydantic configuration schemas.
- Conversion from argparse CLI arguments.
- Loading from JSON and YAML configuration files.
- Programmatic engine instantiation via GatewayEngine.from_config.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tactical_edge_gateway.core.config import (
    BufferConfig,
    GatewayConfig,
    MavlinkConfig,
    ROS2Config,
    SyntheticConfig,
    TcpEgressConfig,
    UdpEgressConfig,
    parse_host_port,
)
from tactical_edge_gateway.core.engine import GatewayEngine
from tactical_edge_gateway.core.models import VehicleDomain
from tactical_edge_gateway.main import build_parser, run_gateway


def test_parse_host_port():
    """Verify parse_host_port handles host:port and bare host formats."""
    host, port = parse_host_port("192.168.1.50:6969", default_port=4242)
    assert host == "192.168.1.50"
    assert port == 6969

    host2, port2 = parse_host_port("10.0.0.1", default_port=8087)
    assert host2 == "10.0.0.1"
    assert port2 == 8087


def test_gateway_config_from_cli_defaults():
    """Verify CLI parser with no options yields default synthetic adapter and UDP egress."""
    parser = build_parser()
    args = parser.parse_args([])
    config = GatewayConfig.from_cli_args(args)

    assert config.queue_size == 1000
    assert config.mavlink is None
    assert config.ros2 is None
    assert config.synthetic is not None
    assert config.synthetic.uid == "synth-01"
    assert config.synthetic.callsign == "VIPER-SYNTH"
    assert config.synthetic.domain == VehicleDomain.AIR
    assert len(config.udp_egress) == 1
    assert config.udp_egress[0].host == "127.0.0.1"
    assert config.udp_egress[0].port == 4242
    assert len(config.tcp_egress) == 0
    assert config.buffer is None


def test_gateway_config_from_cli_full():
    """Verify CLI parser with all flags generates complete GatewayConfig."""
    parser = build_parser()
    args = parser.parse_args(
        [
            "--queue-size",
            "500",
            "--mavlink",
            "udpin:0.0.0.0:14550",
            "--ros2",
            "--ros2-uid",
            "ugv-test-01",
            "--ros2-callsign",
            "ALPHA-ROVER",
            "--ros2-fix-topic",
            "/gps/fix",
            "--ros2-odom-topic",
            "/wheel/odom",
            "--synthetic",
            "--synthetic-domain",
            "ground",
            "--synthetic-uid",
            "synth-ground",
            "--synthetic-callsign",
            "G-SYNTH",
            "--tak-udp",
            "239.2.3.1:6969",
            "--tak-tcp",
            "10.0.0.5:8089",
            "--tak-tcp-tls",
            "--buffer-path",
            "/tmp/test_buffer.db",
            "--max-buffer-records",
            "5000",
            "--replay-rate-hz",
            "50.0",
        ]
    )
    config = GatewayConfig.from_cli_args(args)

    assert config.queue_size == 500
    assert config.mavlink is not None
    assert config.mavlink.connection_string == "udpin:0.0.0.0:14550"
    assert config.ros2 is not None
    assert config.ros2.uid == "ugv-test-01"
    assert config.ros2.callsign == "ALPHA-ROVER"
    assert config.ros2.fix_topic == "/gps/fix"
    assert config.ros2.odom_topic == "/wheel/odom"
    assert config.synthetic is not None
    assert config.synthetic.domain == VehicleDomain.GROUND
    assert len(config.udp_egress) == 1
    assert config.udp_egress[0].host == "239.2.3.1"
    assert config.udp_egress[0].port == 6969
    assert len(config.tcp_egress) == 1
    assert config.tcp_egress[0].host == "10.0.0.5"
    assert config.tcp_egress[0].port == 8089
    assert config.tcp_egress[0].use_tls is True
    assert config.buffer is not None
    assert config.buffer.db_path == "/tmp/test_buffer.db"
    assert config.buffer.max_records == 5000
    assert config.buffer.replay_rate_hz == 50.0


def test_gateway_config_from_dict_and_validation():
    """Verify dictionary parsing and strict validation."""
    data = {
        "queue_size": 250,
        "synthetic": {
            "uid": "dict-synth",
            "callsign": "DICT-VIPER",
            "domain": "air",
        },
        "udp_egress": [{"host": "127.0.0.1", "port": 4242}],
    }
    config = GatewayConfig.from_dict(data)
    assert config.queue_size == 250
    assert config.synthetic is not None
    assert config.synthetic.callsign == "DICT-VIPER"

    # Extra forbidden field
    invalid_data = {**data, "unexpected_key": 123}
    with pytest.raises(ValidationError):
        GatewayConfig.from_dict(invalid_data)


def test_gateway_config_json_file(tmp_path: Path):
    """Verify loading from JSON file."""
    config_file = tmp_path / "gateway_config.json"
    data = {
        "queue_size": 300,
        "mavlink": {"connection_string": "udpin:127.0.0.1:14550"},
        "udp_egress": [{"host": "127.0.0.1", "port": 4242}],
    }
    config_file.write_text(json.dumps(data), encoding="utf-8")

    config = GatewayConfig.from_json_file(config_file)
    assert config.queue_size == 300
    assert config.mavlink is not None
    assert config.mavlink.connection_string == "udpin:127.0.0.1:14550"


def test_gateway_config_yaml_file(tmp_path: Path):
    """Verify loading from YAML / JSON-compatible file."""
    config_file = tmp_path / "gateway_config.yaml"
    # Valid JSON is valid YAML 1.2
    data = {
        "queue_size": 400,
        "ros2": {
            "uid": "ugv-yaml",
            "callsign": "YAML-ROVER",
        },
        "tcp_egress": [{"host": "127.0.0.1", "port": 8087}],
    }
    config_file.write_text(json.dumps(data), encoding="utf-8")

    config = GatewayConfig.from_yaml_file(config_file)
    assert config.queue_size == 400
    assert config.ros2 is not None
    assert config.ros2.uid == "ugv-yaml"


def test_gateway_config_yaml_file_invalid(tmp_path: Path):
    """Verify invalid YAML content raises ValueError."""
    bad_file = tmp_path / "bad.yaml"
    bad_file.write_text("['not', 'a', 'dictionary']", encoding="utf-8")

    with pytest.raises(ValueError, match="did not resolve to a dictionary"):
        GatewayConfig.from_yaml_file(bad_file)


def test_gateway_config_yaml_fallback_without_yaml_module(monkeypatch, tmp_path: Path):
    """Verify from_yaml_file falls back to JSON parser when yaml module is absent."""
    import builtins

    orig_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("No module named 'yaml'")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    # 1. Valid JSON-compatible file succeeds without PyYAML
    valid_file = tmp_path / "fallback.yaml"
    valid_file.write_text(json.dumps({"queue_size": 150}), encoding="utf-8")
    cfg = GatewayConfig.from_yaml_file(valid_file)
    assert cfg.queue_size == 150

    # 2. Non-dict fallback raises ValueError
    bad_file = tmp_path / "fallback_bad.yaml"
    bad_file.write_text(json.dumps(["not", "dict"]), encoding="utf-8")
    with pytest.raises(ValueError, match="did not resolve to a dictionary"):
        GatewayConfig.from_yaml_file(bad_file)


def test_engine_from_config(tmp_path: Path):
    """Verify GatewayEngine.from_config wires all adapters, egress transports, and buffer."""
    db_path = str(tmp_path / "engine_buf.db")
    config = GatewayConfig(
        queue_size=750,
        mavlink=MavlinkConfig(connection_string="udpin:127.0.0.1:14550"),
        ros2=ROS2Config(uid="ugv-wire", callsign="WIRE-ROVER"),
        synthetic=SyntheticConfig(uid="synth-wire", callsign="WIRE-SYNTH"),
        udp_egress=[UdpEgressConfig(host="127.0.0.1", port=4242)],
        tcp_egress=[TcpEgressConfig(host="127.0.0.1", port=8087, use_tls=True)],
        buffer=BufferConfig(db_path=db_path, max_records=2000, replay_rate_hz=10.0),
    )

    engine = GatewayEngine.from_config(config)

    assert engine.queue_size == 750
    assert len(engine.adapters) == 3
    assert len(engine.egress_clients) == 2
    assert engine.buffer is not None
    assert engine.circuit_breaker is not None
    assert engine.sync_worker is not None
    assert engine.sync_worker.replay_rate_hz == 10.0


@pytest.mark.asyncio
async def test_run_gateway_with_gateway_config_object():
    """Verify run_gateway executes directly when passed a GatewayConfig instance."""
    config = GatewayConfig(
        synthetic=SyntheticConfig(uid="synth-direct", callsign="DIRECT-SYNTH"),
        udp_egress=[UdpEgressConfig(host="127.0.0.1", port=4242)],
    )

    stop_event = asyncio.Event()

    async def _stopper():
        await asyncio.sleep(0.05)
        stop_event.set()

    task = asyncio.create_task(_stopper())
    exit_code = await run_gateway(config, stop_event=stop_event)
    await task

    assert exit_code == 0


@pytest.mark.asyncio
async def test_run_gateway_with_config_cli_flag(tmp_path: Path):
    """Verify CLI --config flag loads file and executes gateway."""
    config_file = tmp_path / "test_run.json"
    data = {
        "synthetic": {"uid": "synth-cli", "callsign": "CLI-SYNTH"},
        "udp_egress": [{"host": "127.0.0.1", "port": 4242}],
    }
    config_file.write_text(json.dumps(data), encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(["--config", str(config_file)])

    stop_event = asyncio.Event()

    async def _stopper():
        await asyncio.sleep(0.05)
        stop_event.set()

    task = asyncio.create_task(_stopper())
    exit_code = await run_gateway(args, stop_event=stop_event)
    await task

    assert exit_code == 0


@pytest.mark.asyncio
async def test_run_gateway_with_yaml_config_cli_flag(tmp_path: Path):
    """Verify CLI --config flag with .yaml extension executes gateway."""
    config_file = tmp_path / "test_run.yaml"
    data = {
        "synthetic": {"uid": "synth-yaml", "callsign": "YAML-SYNTH"},
        "udp_egress": [{"host": "127.0.0.1", "port": 4242}],
    }
    config_file.write_text(json.dumps(data), encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(["--config", str(config_file)])

    stop_event = asyncio.Event()

    async def _stopper():
        await asyncio.sleep(0.05)
        stop_event.set()

    task = asyncio.create_task(_stopper())
    exit_code = await run_gateway(args, stop_event=stop_event)
    await task

    assert exit_code == 0
