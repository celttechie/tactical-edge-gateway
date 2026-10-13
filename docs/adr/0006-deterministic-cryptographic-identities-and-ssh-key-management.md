# 6. Deterministic Cryptographic Identities & SSH Key Management

Date: 2026-10-13

## Status
Accepted

## Context
In ephemeral tactical edge test environments, automated CI/CD pipelines, and disconnected sandboxes, edge nodes are frequently rebuilt from scratch.

If SSH host keys are generated randomly on each boot, operators encounter `WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!`. This routinely induces dangerous engineering anti-patterns, such as appending `StrictHostKeyChecking no` and `UserKnownHostsFile /dev/null` into automation scripts, completely compromising transport security. Conversely, checking static private keys into Git repositories introduces serious credential leakage risks.

## Decision
1. Implement a deterministic key derivation engine using RFC-compliant **HKDF-SHA256** and **Ed25519** elliptic curves (`scripts/generate-deterministic-keys.py`).
2. Derive host private and public keys deterministically from a pinned master seed phrase and node identity context string (`edge-gateway-<node_id>`).
3. Seed `known_hosts` pre-emptively on developer and management systems using the deterministic public key, allowing strict host key validation without manual prompt bypasses.
4. Enforce strict filesystem permissions (`0600` for private keys, `0644` for public keys).

## Consequences
- Completely eliminates SSH MITM risks without resorting to disabling security checks.
- Zero private keys committed to Git repositories.
- Deterministic, repeatable test runs across local workstations, nested hypervisors, and tactical edge appliances.
