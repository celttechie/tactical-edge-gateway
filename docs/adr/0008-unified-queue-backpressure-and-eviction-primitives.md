# 8. Unified Queue Backpressure & Eviction Primitives

Date: 2026-10-15

## Status
Accepted

## Context
Across the gateway architecture (MAVLink ingestion, ROS2 subscription, router distribution, TCP streaming egress, and UDP broadcast egress), asynchronous queues decouple producers from consumers. Because edge single-board computers have limited memory and real-time C2 situational awareness values timeliness over stale historical telemetry, internal queues must enforce strict bounded capacity.

Previously, bounded capacity was enforced by manually catching `asyncio.QueueFull` and calling `get_nowait()` across five separate modules (`ros2.py`, `mavlink.py`, `tcp.py`, `udp.py`, `engine.py`). This caused:
- Duplicated boilerplate concurrency code.
- Inconsistent drop-oldest error handling and race-condition edge cases.
- Lack of centralized drop metrics across the ingestion, routing, and egress pipelines.

## Decision
1. Implement a specialized, typed queue primitive `EvictingQueue[T](asyncio.Queue[T])` in `src/tactical_edge_gateway/core/queue.py`.
2. Provide atomic `put_nowait_evict(item: T) -> bool` that automatically evicts the oldest queued item when the queue is full and increments an internal `dropped_count`.
3. Standardize all internal buffers (`BaseTelemetryAdapter`, `BaseEgressClient`, and `GatewayEngine`) on `EvictingQueue`.

## Consequences
- Eliminates duplicated FIFO drop-oldest logic across the codebase.
- Provides consistent, race-free drop-oldest semantics.
- Centralizes operational metrics tracking for dropped telemetry.
- Simplifies unit testing of backpressure and queue limits.
