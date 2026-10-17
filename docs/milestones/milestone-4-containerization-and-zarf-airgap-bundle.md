# Milestone 4: Containerization, Zarf Airgap Bundle & Lula Compliance

**Status:** In Progress
**Objective:** Deliver 12-factor environment configuration via `pydantic-settings`, an immutable rootless OCI container adhering to DISA STIGs, a declarative Defense Unicorns Zarf package for offline deployment, and automated NIST SP 800-53 compliance assessment via Lula.

---

## Key Deliverables & Execution Plan

### 1. 12-Factor Configuration via `pydantic-settings`
- [x] Add `pydantic-settings>=2.0.0` dependency to `pyproject.toml`.
- [x] Upgrade `GatewayConfig` from `pydantic.BaseModel` to `pydantic_settings.BaseSettings`:
  - Support automatic environment variable parsing with prefix `TEG_` and nested delimiter `__` (e.g., `TEG_EGRESS__HOST=192.168.1.50`, `TEG_BUFFER__DB_PATH=/data/buffer.db`).
  - Support optional `.env` file loading for local development and container testing.
  - Implement deterministic configuration hierarchy: **CLI Flags > Environment Variables > Config File (YAML/JSON) > Defaults**.
- [x] Add comprehensive unit tests in `tests/unit/test_config.py` verifying environment variable inheritance and `.env` parsing.

### 2. DISA STIG-Compliant Rootless Containerization
- [x] Multi-stage Dockerfile adhering to DISA Container Hardening STIG guidelines:
  - Non-root user execution (`UID 10001`, `GID 10001`, `edgeuser`).
  - Read-only root filesystem support with explicit writable `/data` volume for SQLite buffer and logs.
  - Minimal attack surface: zero unnecessary OS binaries, package managers, or compilers in the final runtime layer.
  - OCI standard container metadata and labels.
- [x] Container build automation and vulnerability scanning (`trivy` container image scan with 0 findings).

### 3. Defense Unicorns Zarf Air-Gap Package (`zarf.yaml`)
- [ ] Declarative `zarf.yaml` package definition:
  - Bundles the gateway container image and FreeTAKServer container.
  - Declares local data volume mounts, environment variables, and network port mappings.
  - Pins all OCI registry components for 100% offline, air-gapped reproducibility.
- [ ] Air-gap verification test script running `zarf package create` and `zarf package deploy` in disconnected network conditions.

### 4. Defense Unicorns Lula Integration (`lula.yaml`)
- [ ] OSCAL Component Definition mapping gateway controls to NIST SP 800-53 Rev 5:
  - **SC-8 (Transmission Confidentiality and Integrity):** TLS encrypted TAK TCP transport.
  - **AC-3 / AC-6 (Access Enforcement & Least Privilege):** Non-root container execution (`UID 10001`) and read-only rootfs.
  - **SI-4 (Information System Monitoring):** Operational stats tracking and circuit breaker health probes.
- [ ] Validation script executing `lula validate` during continuous delivery pipelines.

---

## Acceptance Criteria
1. `GatewayConfig` seamlessly overrides configuration values from `TEG_*` environment variables and `.env` files.
2. Container image builds successfully, executes as non-root user `10001`, and passes security vulnerability scanning (`trivy`).
3. Zarf produces a self-contained, reproducible `.tar.zst` air-gap package file.
4. `lula validate` executes cleanly against live deployment, producing audit-ready OSCAL compliance output.
5. Deploying the package on an air-gapped host bootstraps the entire tactical stack without external internet requests.
