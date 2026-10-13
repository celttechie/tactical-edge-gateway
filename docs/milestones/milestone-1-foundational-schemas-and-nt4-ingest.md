# Milestone 1: Multi-Protocol Telemetry Ingestion (MAVLink & NetworkTables)

**Status:** Completed / Baseline  
**Objective:** Establish core Python architecture, typed telemetry models, and first-class ingestion for MAVLink (drones/UAVs) and NetworkTables 4 (ground robotics).

## Key Deliverables
- [x] Modern Python project scaffolding (`pyproject.toml`, `Makefile`, `.flake8`, `.gitignore`).
- [x] Deterministic cryptographic identity derivation (`scripts/generate-deterministic-keys.py`).
- [x] Developer SSH environment onboarding (`scripts/setup-ssh.sh`).
- [ ] Core data models: `TelemetryFrame`, `GeodeticPosition`, `AttitudeVector`, `PlatformHealth`, `VehicleDomain`.
- [ ] Ingestion adapters:
  - `MavlinkAdapter` ingesting `GLOBAL_POSITION_INT`, `ATTITUDE`, `HEARTBEAT`, and `SYS_STATUS` via `pymavlink` over UDP.
  - `NetworkTablesAdapter` subscribing to RobotPy / WPILib NT4 topics (`/robot/pose`, `/robot/status`).
  - `SyntheticTelemetryAdapter` generating waypoint tracks for deterministic offline testing and headless CI.
- [ ] Unit tests for frame serialization, MAVLink packet parsing, and NT4 conversion.

## Acceptance Criteria
1. Gateway successfully consumes real MAVLink UDP streams (port 14550) and NT4 streams, normalizing both into `TelemetryFrame` structures.
2. All data models pass strict Pydantic runtime schema validation.
3. Code achieves 100% pass rate under `flake8` and `black`.
