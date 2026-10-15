# Milestone 3.5: Core Abstractions Refactoring & Codebase Modularization

**Status:** In Progress
**Objective:** Eliminate code redundancy, standardize asynchronous service lifecycles, centralize queue backpressure primitives, and decouple gateway configuration from the CLI to ensure long-term architectural maintainability and extensibility.

---

## Context & Motivation

Following the completion of Milestone 3 (DIL Mesh Resilience & Store-and-Forward Buffering), the gateway comprises 21 modules with 100% test coverage across all components. However, rapid multi-protocol expansion has introduced duplicated patterns in queue backpressure eviction, asynchronous service start/stop lifecycles, and configuration construction. Refactoring these primitives into clean, reusable abstractions now prevents technical debt from compounding during containerization (Milestone 4) and deployment demonstrations (Milestone 5).

---

## Logical Execution Stages

### Stage 1: Core Concurrency & Queue Backpressure Primitive (`EvictingQueue`)
- [ ] Implement `EvictingQueue[T](asyncio.Queue[T])` in `src/tactical_edge_gateway/core/queue.py`.
- [ ] Centralize drop-oldest FIFO overflow handling and `dropped_count` operational metrics.
- [ ] Refactor 5 modules to use `EvictingQueue`:
  - `src/tactical_edge_gateway/ingest/ros2.py`
  - `src/tactical_edge_gateway/ingest/mavlink.py`
  - `src/tactical_edge_gateway/egress/tcp.py`
  - `src/tactical_edge_gateway/egress/udp.py`
  - `src/tactical_edge_gateway/core/engine.py`
- [ ] Add unit tests in `tests/unit/test_queue.py` verifying thread-safety and bounded capacity.

### Stage 2: Standardized Async Service Lifecycle (`AsyncService`)
- [ ] Implement `AsyncService` abstract base class/mixin in `src/tactical_edge_gateway/core/service.py`.
- [ ] Standardize state management (`is_running`, `_running`, start/stop idempotency guards).
- [ ] Provide uniform asynchronous context manager (`async with`) support across all services.
- [ ] Migrate `BaseTelemetryAdapter`, `BaseEgressClient`, and `StoreAndForwardWorker` to `AsyncService`.

### Stage 3: Domain Math & Coordinate Normalization
- [ ] Consolidate degree wrapping and pitch/roll clamping into `AttitudeVector.from_unclamped_degrees()`.
- [ ] Remove duplicate manual coordinate bounds checks in `mavlink.py` and `ros2.py`, relying on canonical Pydantic model validation.

### Stage 4: Structured Gateway Configuration & Engine Decoupling
- [ ] Implement `GatewayConfig` Pydantic model in `src/tactical_edge_gateway/core/config.py`.
- [ ] Implement `GatewayEngine.from_config(config)` factory method.
- [ ] Refactor `main.py` so CLI argument parsing maps directly to `GatewayConfig`, enabling programmatic embedding.

### Stage 5: Standardized Operational Metrics (`Stats` Hierarchy)
- [ ] Unify operational metrics under immutable Pydantic models in `src/tactical_edge_gateway/core/stats.py`.
- [ ] Introduce formal `AdapterStats` to `BaseTelemetryAdapter`.
- [ ] Ensure uniform JSON serialization for telemetry export (Prometheus, logging, REST).

---

## Acceptance Criteria
1. Bounded FIFO drop-oldest logic is implemented in exactly one place (`EvictingQueue`).
2. All long-running services support `async with` context management.
3. `GatewayEngine` can be fully initialized programmatically via `GatewayConfig` without invoking `argparse`.
4. Zero functional regressions across MAVLink, ROS2, CoT translation, SQLite buffering, and TAK egress.
5. Codebase maintains **100% test coverage** across all modules and passes all pre-commit security gates.
