# Tactical Edge Gateway (`tactical-edge-gateway`)

[![Status](https://img.shields.io/badge/Status-Active%20Development%20%7C%20Untested-orange.svg)](docs/IMPLEMENTATION_PLAN.md)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg)](pyproject.toml)
[![Zarf](https://img.shields.io/badge/Packaging-Defense%20Unicorns%20Zarf-orange.svg)](packages/zarf/zarf.yaml)
[![Compliance](https://img.shields.io/badge/Compliance-Defense%20Unicorns%20Lula-teal.svg)](docs/adr/0007-continuous-compliance-and-oscal-assessment-lula.md)
[![Security](https://img.shields.io/badge/Identity-Deterministic%20Ed25519-purple.svg)](scripts/generate-deterministic-keys.py)

An air-gapped, resilient tactical telemetry gateway bridging heterogeneous autonomy and unmanned systems data streams (**MAVLink** and **ROS2 / DDS**) to military Command and Control (C2) situational awareness tools (**Cursor on Target / TAK Server**) over contested, disconnected (DIL) tactical radio links.

---

## Architecture Overview

```mermaid
flowchart TD
    subgraph Edge["Tactical Autonomous Edge (UAV / UGV / Payloads)"]
        direction TB
        subgraph AutonomySources["Heterogeneous Autonomy Telemetry"]
            UAV["Air Autonomy\n(MAVLink / PX4 / UDP 14550)"]
            UGV["Ground Robotics\n(ROS2 / DDS)"]
        end

        Gateway["Tactical Edge Gateway Engine\n(Telemetry Normalization & Router)"]
        DB[("Local SQLite WAL\nStore & Forward Buffer")]

        UAV -- "Binary MAVLink Stream" --> Gateway
        UGV -- "Pub/Sub Topics" --> Gateway
        Gateway -- "If Radio Disconnected" --> DB
        DB -- "Throttled Replay on Reconnect" --> Gateway
    end

    subgraph RF["Contested Tactical Link (DIL / MANET)"]
        Link["Lossy Radio Link\n(Silvus / TrellisWare / Mesh RF)"]
    end

    subgraph C2["Tactical Platform / C2 Operations"]
        TAK["TAK Server / FreeTAKServer\n(CoT Ingestion Port 4242)"]
        ATAK["ATAK-CIV / WinTAK / WebTAK\n(Air & Ground Blue Force Tracking)"]

        TAK --> ATAK
    end

    Gateway -- "Cursor on Target (CoT XML)\n(UDP / TCP / TLS)" --> Link
    Link --> TAK
```

---

## Key Capabilities

1. **Multi-Protocol Autonomy Ingestion:**
   * **MAVLink (UAS/Drones):** Ingests binary MAVLink streams (`HEARTBEAT`, `GLOBAL_POSITION_INT`, `ATTITUDE`, `SYS_STATUS`) over UDP port 14550 via `pymavlink`.
   * **ROS2 / DDS (UGV/Robotics):** Decoupled subscriber adapters for ROS2 `sensor_msgs/NavSatFix` and `nav_msgs/Odometry`.
   * **Synthetic Telemetry (Edge Simulator):** Generates deterministic flight and ground waypoint navigation tracks for offline headless validation and CI.
2. **Cursor on Target (CoT) & MIL-STD Symbology:** Maps multi-domain autonomy telemetry into compliant MITRE Cursor on Target (CoT) XML events, distinguishing airborne drones (`a-f-A-M-F`) from ground vehicles (`a-f-G-E-V-U`).
3. **DIL (Disconnected, Intermittent, Limited) Resilience:** Implements an embedded store-and-forward SQLite buffer that queues telemetry during radio outages and bursts historical breadcrumbs at a throttled rate upon link recovery.
4. **Declarative Air-Gap Packaging (Zarf):** Bundles all gateway layers, configuration manifests, and server containers into an offline Defense Unicorns **Zarf package** for zero-internet tactical deployments.
5. **Continuous Compliance as Code (Lula):** Employs Defense Unicorns **Lula** with machine-readable OSCAL component definitions, mapping live system configuration to NIST SP 800-53 controls (SC-8, AC-3, AC-6, SI-4) for cATO readiness.
6. **Deterministic Cryptographic Identities:** Generates reproducible Ed25519 host keypairs from an HKDF seed, eliminating the dangerous `StrictHostKeyChecking=no` anti-pattern in ephemeral testbeds.

---

## Directory Structure

```text
tactical-edge-gateway/
├── docs/
│   ├── IMPLEMENTATION_PLAN.md    # Step-by-step engineering roadmap
│   ├── adr/                      # Architecture Decision Records (ADRs 0001-0007)
│   ├── milestones/               # Tracked milestones (1 through 5)
│   └── security/                 # Threat model & deterministic key specs
├── packages/
│   └── zarf/                     # Air-gap Zarf package definition (zarf.yaml)
├── scripts/
│   ├── generate-deterministic-keys.py  # HKDF-SHA256 Ed25519 generator
│   └── setup-ssh.sh                    # Host config & SSH onboarding
├── src/
│   └── tactical_edge_gateway/    # Core Python application modules
├── tests/
│   ├── unit/                     # Unit test suites
│   └── integration/              # End-to-end integration tests
├── Makefile                      # Build, test, and automation commands
├── pyproject.toml                # Project metadata and dependencies
└── README.md
```

---

## Quickstart

### 1. Environment Setup
```bash
# Clone and enter the repository
git clone https://github.com/celttechie/tactical-edge-gateway.git
cd tactical-edge-gateway

# Set up Python virtual environment and install dependencies
make dev-install
```

### 2. Generate Deterministic Host Keys
```bash
# Generate reproducible Ed25519 keys for the testbed
make keys
```

### 3. Run Linting & Test Suites
```bash
make lint
make test
```

### 4. Air-Gapped Packaging with Zarf
```bash
# Package the gateway and its dependencies into a self-contained archive
make zarf-package
```

---

## Architecture Decision Records (ADRs)

| ADR | Title | Status |
| :--- | :--- | :--- |
| [0001](docs/adr/0001-record-architecture-decisions.md) | Record Architecture Decisions | Accepted |
| [0002](docs/adr/0002-pubsub-telemetry-ingestion-nt4-mavlink.md) | Multi-Protocol Autonomy Ingestion (MAVLink and ROS2/DDS) | Accepted |
| [0003](docs/adr/0003-cursor-on-target-cot-xml-translation.md) | Cursor on Target (CoT) XML Translation & Schema Validation | Accepted |
| [0004](docs/adr/0004-disconnected-store-and-forward-crdt-buffering.md) | DIL Store-and-Forward Edge Buffering | Accepted |
| [0005](docs/adr/0005-airgap-delivery-zarf-and-container-least-privilege.md) | Air-Gap Delivery via Defense Unicorns Zarf | Accepted |
| [0006](docs/adr/0006-deterministic-cryptographic-identities-and-ssh-key-management.md) | Deterministic Cryptographic Identities & SSH Key Management | Accepted |
| [0007](docs/adr/0007-continuous-compliance-and-oscal-assessment-lula.md) | Continuous Compliance & OSCAL Assessment via Defense Unicorns Lula | Accepted |

---

## License

This project is licensed under the Apache 2.0 License.
