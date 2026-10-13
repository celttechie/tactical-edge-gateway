# 5. Air-Gap Delivery via Defense Unicorns Zarf & Container Least Privilege

Date: 2026-10-13

## Status
Accepted

## Context
Deploying software to tactical edge systems requires operation in classified, air-gapped, or disconnected environments where outbound internet access to public container registries (Docker Hub, Quay) or package repositories (PyPI) is strictly forbidden.

Furthermore, DoD security baselines (e.g., DISA Container Hardening STIG) mandate that containers run rootless without privileged capabilities.

## Decision
1. Package the Tactical Edge Gateway using a multi-stage Docker build producing a rootless, minimal container image (UID 10001, non-root user `edgeuser`, read-only root filesystem where feasible).
2. Author a declarative **Defense Unicorns Zarf** package specification (`packages/zarf/zarf.yaml`).
3. Bundle all runtime dependencies, container image layers, and configuration manifests into an immutable `.tar.zst` Zarf archive that can be transferred via optical disc, secure thumbdrive, or local registry and deployed with a single command: `zarf package deploy`.

## Consequences
- 100% offline, deterministic deployment capability in air-gapped tactical enclaves.
- Complete adherence to DISA Container Hardening STIG principles and Defense Unicorns ecosystem standards.
