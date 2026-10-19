# Tactical Edge Gateway - Operational Demonstration Runbook

This runbook provides step-by-step operational instructions for demonstrating the **Tactical Edge Gateway** (TEG). It walks through multi-domain telemetry ingestion (airborne UAV and ground autonomous rover), radio blackout injection over contested DIL links, circuit breaker state transitions, SQLite WAL store-and-forward buffering, and real-time visualization on **ATAK-CIV** / **WinTAK**.

---

## 1. Demonstration Profiles

Choose the profile that matches your test environment:

| Profile | Target Environment | Requirements | Execution Time |
| :--- | :--- | :--- | :--- |
| **Profile 1: Local CLI Simulation** | Workstation / Laptop | Python 3.10+, Virtualenv | ~1 minute |
| **Profile 2: Containerized Edge Stack** | Edge Node / Docker / Podman | Docker Engine 20.10+ | ~2 minutes |
| **Profile 3: Air-Gapped Kubernetes (Zarf)** | K3s / RKE2 Tactical Cluster | Zarf CLI, Kubectl, Lula | ~5 minutes |

---

## 2. Profile 1: Local Interactive Mission Simulation (Quickstart)

This profile runs an autonomous multi-vehicle mission using TEG's internal components and a mock tactical TAK server.

### Step 1: Run Mission Simulation via Make
```bash
make demo
```
*Alternatively, pass custom duration and outage parameters:*
```bash
.venv/bin/python scripts/run-e2e-simulation.py \
  --duration 25 \
  --outage-start 8 \
  --outage-duration 10 \
  --rate 2.0
```

### Step 2: Observe Live Console Telemetry & Circuit Breaker
During execution, the CLI dashboard streams real-time vehicle positions and gateway states:
1. **Phase 1 (T=0 to T=8s) - Normal Connected State**:
   - UAV `RECON-ALPHA` (`a-f-A-M-F`) and Rover `GROUND-ROVER-01` (`a-f-G-E-V-U`) transmit positions.
   - Circuit Breaker: `CLOSED`.
   - SQLite Buffer: 0 pending records.
2. **Phase 2 (T=8s to T=18s) - Contested RF Link Blackout**:
   - Automated impairment injection cuts off the RF link.
   - Circuit Breaker trips: `CLOSED` -> `OPEN`.
   - Dashboard flags `RF:[BLACKOUT] Breaker:OPEN (BUFFERING)`.
   - Telemetry frames are preserved in local SQLite WAL (`pending_records` accumulates).
   - Zero packets are dropped.
3. **Phase 3 (T=18s to T=25s) - RF Link Restoration & Backlog Flush**:
   - RF link recovers.
   - Circuit Breaker probes: `OPEN` -> `HALF_OPEN` -> `CLOSED`.
   - Store-and-Forward engine flushes buffered backlog in chronological order.
   - All pending records transition to `SYNCED`.
   - Data Loss Rate reaches **0.00%**.

---

## 3. Profile 2: Containerized Edge Stack with FreeTAKServer & ATAK

Run the hardened, non-root TEG container alongside FreeTAKServer and connect live ATAK clients.

### Step 1: Start FreeTAKServer
Launch the standard FreeTAKServer container:
```bash
docker run -d --name freetakserver \
  -p 8087:8087/tcp \
  -p 8080:8080/tcp \
  -p 19023:19023/tcp \
  ghcr.io/freetakteam/freetakserver:latest
```

### Step 2: Connect ATAK-CIV / WinTAK
1. Import `packages/atak/mission-package/teg-mission-package.zip` into ATAK-CIV or WinTAK (refer to [ATAK-CIV & WinTAK Connectivity Guide](file:///home/bjarrett/Projects/tactical-edge-gateway/docs/operations/atak-civ-connectivity-guide.md)).
2. Confirm the server connection indicator is **Green** (connected to `127.0.0.1:8087`).
3. Center map on Camp Roberts training area (`35.7100 N, -120.7100 W`).

### Step 3: Run Gateway against FreeTAKServer
```bash
.venv/bin/python scripts/run-e2e-simulation.py \
  --no-mock-server \
  --tak-host 127.0.0.1 \
  --tak-port 8087 \
  --duration 60 \
  --outage-start 15 \
  --outage-duration 15
```

### Step 4: Verify Tactical Visuals on ATAK
- Observe `RECON-ALPHA` (aircraft icon) and `GROUND-ROVER-01` (ground combat vehicle icon) moving across the tactical map.
- At T=15s, when the radio link cuts off, observe that the vehicle icons freeze and their labels turn **Amber / Stale** after the timeout.
- At T=30s, observe that the full track history instantly backfills and live tracking resumes without gaps.

---

## 4. Profile 3: Air-Gapped Tactical Cluster Deployment (Zarf & Lula)

Deploy the declarative air-gapped package to an edge Kubernetes cluster (e.g., K3s on tactical hardware).

### Step 1: Deploy Air-Gap Package via Zarf
```bash
# In an air-gapped node with zarf CLI:
zarf package deploy packages/zarf/zarf-package-tactical-edge-gateway-*.tar.zst --confirm
```

### Step 2: Verify Pod Security & Running Services
```bash
kubectl get pods -n tactical-edge -o wide
kubectl get services -n tactical-edge
```
Confirm:
- Pod `tactical-edge-gateway-*` is running under UID `10001` with `readOnlyRootFilesystem: true`.
- Pod `freetakserver-*` is running and exposing port `8087`.

### Step 3: Run Continuous Compliance Assessment (Lula)
```bash
make lula-validate
```
Verify that all 4 NIST SP 800-53 Rev 5 controls (`SC-8`, `AC-3`, `AC-6`, `SI-4`) pass automated Rego evaluation.

---

## 5. Verification & Acceptance Checklist

| Step | Verification Criteria | Expected Result | Pass / Fail |
| :--- | :--- | :--- | :--- |
| **1. Ingestion** | Airborne UAV & Ground Rover telemetry stream | Both UIDs generated and ingested | PASS |
| **2. CoT Symbology** | Correct MIL-STD-2525 tags | `a-f-A-M-F` (Air) & `a-f-G-E-V-U` (Ground) | PASS |
| **3. Circuit Breaker** | Link disruption trips breaker | State moves `CLOSED` -> `OPEN` (trips >= 1) | PASS |
| **4. Edge Buffer** | Telemetry persisted during outage | SQLite WAL stores all frames (`PENDING`) | PASS |
| **5. Store-and-Forward** | Link recovery flushes buffer | All records synced; `pending_records == 0` | PASS |
| **6. Zero Loss** | Complete audit of sent vs received frames | Data Loss Rate = 0.00% | PASS |
| **7. ATAK Display** | Icons track and catch up smoothly | Stale warning during outage; seamless catch-up | PASS |
