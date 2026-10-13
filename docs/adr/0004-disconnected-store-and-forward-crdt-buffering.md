# 4. Disconnected, Intermittent, Limited (DIL) Store-and-Forward Edge Buffering

Date: 2026-10-13

## Status
Accepted

## Context
Tactical operations take place in Disconnected, Intermittent, and Limited (DIL) bandwidth environments. Mobile Ad-hoc Networks (MANET) and mesh radios frequently drop links due to terrain masking, electronic warfare, RF interference, or platform movement. 

Traditional client-server telemetry streaming freezes or throws unhandled socket errors upon link degradation. Dropping telemetry outright loses critical situational awareness, while unbounded in-memory queues cause out-of-memory crashes on resource-constrained edge single-board computers (SBCs).

## Decision
1. Implement a persistent, zero-dependency **Store-and-Forward Buffer** backed by an embedded SQLite database in WAL (Write-Ahead Logging) mode.
2. Structure queued events with monotonic sequence IDs, UTC timestamps, and delivery state flags (`PENDING`, `TRANSMITTING`, `CONFIRMED`).
3. Implement a stateful **Egress Sync Worker** with:
   - Circuit-breaker link health monitoring.
   - Exponential backoff with jitter on link failure.
   - Throttled "burst sync" upon link restoration to prevent flooding tactical radio links.
   - Automatic pruning policies (FIFO eviction when max buffer size is reached, and dropping telemetry past its CoT `stale` threshold).

## Consequences
- Guaranteed zero telemetry loss during transient network partitions within storage limits.
- Bounded memory and disk consumption protecting edge compute nodes from resource starvation.
- Smooth, rate-limited reintegration into central C2 upon radio link recovery.
