# 10. Decoupled Gateway Configuration & Engine Factory

Date: 2026-10-15

## Status
Accepted

## Context
In the initial architecture, the `GatewayEngine` was tightly coupled to CLI argument parsing in `src/tactical_edge_gateway/main.py`. The `run_gateway` function directly inspected an `argparse.Namespace` object to configure adapters, egress clients, and buffer paths.

This coupling caused:
- Inability to cleanly embed `tactical-edge-gateway` inside other Python applications (e.g., direct ROS2 launch scripts, standalone microservices, or custom test harnesses).
- Inflexible configuration input (CLI arguments only, lacking declarative YAML/JSON/TOML loading).
- Awkward test setups that required mocking `argparse.Namespace` to test engine instantiation.

## Decision
1. Introduce a structured, strongly-typed configuration schema `GatewayConfig` using Pydantic in `src/tactical_edge_gateway/core/config.py`.
2. Provide conversion factories:
   - `GatewayConfig.from_cli_args(args: argparse.Namespace) -> GatewayConfig`
   - `GatewayConfig.from_yaml_file(path: Path) -> GatewayConfig`
3. Provide a factory constructor on the engine: `GatewayEngine.from_config(config: GatewayConfig) -> GatewayEngine`.
4. Refactor `main.py` so that its sole responsibility is parsing CLI arguments into a `GatewayConfig`, passing it to the engine factory, and handling OS signals.

## Consequences
- Complete decoupling of core gateway orchestration from command-line interfaces.
- Seamless programmatic integration and automated testing.
- Ready support for future configuration files (e.g. Zarf-templated YAML configs in Milestone 4).

## Amendment (Milestone 4 - 2026-10-17)
To support 12-factor containerization and Kubernetes deployment patterns in Milestone 4, `GatewayConfig` is upgraded to subclass `pydantic_settings.BaseSettings` (`pydantic-settings`). This enables automatic environment variable mapping (`TEG_*`) with nested property delimiters (`__`), `.env` file parsing, and deterministic hierarchy resolution (CLI flags > environment variables > YAML/JSON files > defaults) without custom parsing boilerplate.
