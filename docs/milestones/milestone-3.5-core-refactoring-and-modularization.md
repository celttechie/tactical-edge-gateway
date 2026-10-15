# Milestone 3.5: Core Abstractions Refactoring & Codebase Modularization

**Status:** In Progress
**Objective:** Eliminate code redundancy, standardize asynchronous service lifecycles, centralize queue backpressure primitives, and decouple gateway configuration from the CLI to ensure long-term architectural maintainability and extensibility.

---

## Engineering Leadership & Strategic Debt Evaluation

### The Architectural Inflection Point
Milestones 1 through 3 moved rapidly to prove technical feasibility across heterogeneous protocols: MAVLink micro-air vehicles, ROS2 ground robotics, MIL-STD-2525D Cursor on Target translation, and disconnected SQLite Write-Ahead Logging store-and-forward resilience. While every component achieved 100% test coverage, rapid feature expansion created duplication across subsystem boundaries.

As the gateway prepared for **Milestone 4 (Containerization, Zarf Air-Gap Packaging & Lula OSCAL Compliance)**, we reached an architectural inflection point. A deliberate decision was made to pause feature accretion and conduct a comprehensive technical debt audit.

### Why Prioritize Refactoring Before Milestone 4?
1. **Preventing Compounding Debt in Containers:** Containerizing poorly factored abstractions cements fragile patterns into production artifacts. Inconsistent asynchronous shutdown logic and ad-hoc queue backpressure loops inside an immutable container manifest as subtle race conditions, orphaned tasks, and flaky container health checks.
2. **Deterministic Process Lifecycles:** Milestone 4 requires non-root container signal handling (`SIGTERM`/`SIGINT`) and clean socket closures. Standardizing on `AsyncService` with `async with` context managers guarantees deterministic resource acquisition and teardown.
3. **Decoupled Embedding vs. CLI Lock-In:** Containerization and air-gap deployment require injecting configuration via environment variables and declarative YAML files. Coupling `GatewayEngine` initialization to `argparse` in `main.py` prevented headless programmatic embedding.

### Strategic Execution: Measured & Atomic
Rather than executing a disruptive rewrite, we scheduled this refactor into **5 measured, atomic stages**:
1. Concurrency Primitives (`EvictingQueue`)
2. Asynchronous Lifecycles (`AsyncService`)
3. Domain Kinematics & Coordinate Normalization (`AttitudeVector`)
4. Structured Decoupled Configuration (`GatewayConfig`)
5. Standardized Operational Metrics (`Stats` Hierarchy)

**Zero-Regression Invariant:** Every stage is implemented and merged via dedicated pull requests, maintaining **100% statement and branch coverage**, passing all pre-commit security gates, and introducing zero functional regressions.

---

## Logical Execution Stages

### Stage 1: Core Concurrency & Queue Backpressure Primitive (`EvictingQueue`)
- [x] Implement `EvictingQueue[T](asyncio.Queue[T])` in `src/tactical_edge_gateway/core/queue.py`.
- [x] Centralize drop-oldest FIFO overflow handling and `dropped_count` operational metrics.
- [x] Refactor 5 modules to use `EvictingQueue`:
  - `src/tactical_edge_gateway/ingest/ros2.py`
  - `src/tactical_edge_gateway/ingest/mavlink.py`
  - `src/tactical_edge_gateway/egress/tcp.py`
  - `src/tactical_edge_gateway/egress/udp.py`
  - `src/tactical_edge_gateway/core/engine.py`
- [x] Add unit tests in `tests/unit/test_queue.py` verifying thread-safety and bounded capacity.

### Stage 2: Standardized Async Service Lifecycle (`AsyncService`)
- [x] Implement `AsyncService` abstract base class/mixin in `src/tactical_edge_gateway/core/service.py`.
- [x] Standardize state management (`is_running`, `_running`, start/stop idempotency guards).
- [x] Provide uniform asynchronous context manager (`async with`) support across all services.
- [x] Migrate `BaseTelemetryAdapter`, `BaseEgressClient`, and `StoreAndForwardWorker` to `AsyncService`.

### Stage 3: Domain Math & Coordinate Normalization
- [x] Consolidate degree wrapping and pitch/roll clamping into `AttitudeVector.from_unclamped_degrees()`.
- [x] Remove duplicate manual coordinate bounds checks in `mavlink.py` and `ros2.py`, relying on canonical Pydantic model validation.

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
