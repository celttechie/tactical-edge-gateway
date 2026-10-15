# 9. Asynchronous Service Lifecycle & Context Management

Date: 2026-10-15

## Status
Accepted

## Context
Multiple components in the gateway represent long-running asynchronous background workers:
- Ingestion adapters (`BaseTelemetryAdapter`, `MavlinkAdapter`, `ROS2Adapter`, `SyntheticAdapter`)
- Egress clients (`BaseEgressClient`, `TcpEgressClient`, `UdpEgressClient`)
- Buffer workers (`StoreAndForwardWorker`)
- Core supervisor (`GatewayEngine`)

Previously, each component independently implemented its own `self._running: bool` flag, manual idempotency guards, and stop cancellation routines. Furthermore, only `GatewayEngine` implemented asynchronous context manager methods (`__aenter__` and `__aexit__`), forcing consumers of adapters, clients, and workers to manually manage `start()` and `stop()` in `try/finally` blocks.

## Decision
1. Introduce an abstract base class `AsyncService` in `src/tactical_edge_gateway/core/service.py`.
2. Standardize lifecycle conventions:
   - Synchronized `start()` and `stop()` methods with re-entrant idempotency guards.
   - Abstract internal hooks `_on_start()` and `_on_stop()`.
   - Built-in asynchronous context manager support (`__aenter__` / `__aexit__`).
3. Refactor all long-running adapters, egress clients, workers, and engines to inherit from `AsyncService`.

## Consequences
- Uniform lifecycle behavior and state inspection (`is_running`) across all background services.
- Clean resource acquisition and cleanup via `async with` idioms in application code and test suites.
- Elimination of repetitive start/stop boilerplate across multiple subsystems.
