# Milestone 4: Containerization, Zarf Airgap Bundle & Lula Compliance

**Status:** Planned  
**Objective:** Deliver an immutable, rootless OCI container, a declarative Defense Unicorns Zarf package for offline deployment, and automated NIST SP 800-53 compliance assessment via Lula.

## Key Deliverables
- [ ] Multi-stage Dockerfile adhering to DISA Container Hardening STIG:
  - Non-root user execution (`UID 10001`, `edgeuser`).
  - Minimal attack surface using distroless / alpine base.
  - Zero unnecessary OS binaries or package managers in final layer.
- [ ] Declarative `zarf.yaml` package definition:
  - Embeds gateway container image and FreeTAKServer container.
  - Declares local data volume mounts and network port mappings.
  - Pinned OCI registry components.
- [ ] Defense Unicorns **Lula** integration (`lula.yaml`):
  - OSCAL Component Definition mapping gateway controls to NIST SP 800-53 Rev 5 (SC-8, AC-3, AC-6, SI-4).
  - Validation script executing `lula validate` during continuous delivery.
- [ ] Air-gap verification test script running `zarf package create` and `zarf package deploy` with network interfaces disconnected.

## Acceptance Criteria
1. Container image builds successfully and passes vulnerability / non-root linting.
2. Zarf produces a self-contained `.tar.zst` package file.
3. `lula validate` executes cleanly against live deployment, producing audit-ready OSCAL compliance output.
4. Deploying the package on an air-gapped host bootstraps the entire tactical stack without external internet requests.
