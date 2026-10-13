# Security Architecture & Threat Model

## 1. Threat Model & Operational Constraints

The Tactical Edge Gateway is designed for deployment on edge compute platforms (single-board computers, ruggedized laptops, tactical vehicle payloads) operating in hostile, contested environments.

### Threat Vectors Considered
1. **Contested RF / Eavesdropping & Tampering:**
   - *Risk:* Adversaries intercepting or spoofing telemetry on unencrypted radio links.
   - *Mitigation:* Support for TLS mutual authentication (mTLS) on TCP CoT egress. Strict separation of local telemetry loopback from upstream radio links.
2. **Denial of Service (DoS) & Link Degradation:**
   - *Risk:* High latency, packet drops, or radio link outages causing buffer bloat and system memory starvation.
   - *Mitigation:* SQLite write-ahead logging (WAL) backing storage with bounded file growth limits and FIFO eviction of expired CoT events.
3. **Container Escape & Privilege Escalation:**
   - *Risk:* Compromised gateway process gaining root access on edge host.
   - *Mitigation:* Rootless container deployment (`UID 10001`), no setuid binaries, read-only root filesystems where practical, and zero required Linux capabilities (`drop: [ALL]`).
4. **Host Key Forgery / Man-in-the-Middle in Ephemeral Testbeds:**
   - *Risk:* Operators disabling `StrictHostKeyChecking` when testing ephemeral virtual nodes.
   - *Mitigation:* Deterministic Ed25519 key generation rooted in HKDF-SHA256, allowing pre-seeding of `known_hosts` without embedding private keys in repositories.

---

## 2. Deterministic Key Derivation Specification

Host and identity keys are generated using the following cryptographic flow:

```
[ Master Seed Phrase ] + [ Platform Identity Context (e.g. 'edge-gateway-alpha') ]
                                │
                                ▼
                       HKDF-SHA256 Extract & Expand
                          (Salt: fixed namespace)
                                │
                                ▼
                      32 Bytes Secure Entropy
                                │
                                ▼
                    Ed25519 Keypair Construction
                                │
              ┌─────────────────┴─────────────────┐
              ▼                                   ▼
   RFC OpenSSH Private Key              RFC OpenSSH Public Key
       (Mode 0600)                         (Mode 0644)
```

### Properties
* **Reproducibility:** Re-running the generator with the identical seed and identity produces the exact bit-for-bit keypair across any Linux/macOS architecture.
* **Auditability:** Zero secrets or private keys are stored in source control; developers generate keys dynamically or via environment injection.
* **Zero Trust:** Host fingerprints remain constant across rebuilds, preserving strict SSH validation in CI/CD and lab environments.
