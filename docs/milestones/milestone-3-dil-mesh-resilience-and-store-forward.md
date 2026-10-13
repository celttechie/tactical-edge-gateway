# Milestone 3: DIL Mesh Resilience & Store-and-Forward Buffering

**Status:** Planned  
**Objective:** Guarantee telemetry survival across lossy, disconnected tactical radio links using local SQLite storage and throttled burst synchronization.

## Key Deliverables
- [ ] Persistent SQLite buffer with WAL (Write-Ahead Logging) mode.
- [ ] State tracking: Monotonic sequence numbers, timestamps, and delivery states (`PENDING`, `SYNCED`).
- [ ] Network monitor / circuit breaker detecting link drops and restoration.
- [ ] Rate-limited replay worker preventing radio link saturation upon reconnection.
- [ ] Configurable storage retention policies (max DB size, FIFO pruning, stale packet drop).

## Acceptance Criteria
1. When upstream network socket is forcibly closed, telemetry continues queueing to disk without memory leakage.
2. Upon network link restoration, backlog synchronizes in chronological order at the specified rate limit.
3. Total system RAM remains strictly bounded under sustained link outages.
