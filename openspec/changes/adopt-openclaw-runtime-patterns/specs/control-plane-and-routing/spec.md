# Delta for control-plane-and-routing

## MODIFIED Requirements

### Requirement: Gateway-Style Control Plane
The system SHALL evolve from a chat-chain orchestrator into a clearer control-plane runtime that owns routing, task continuity, run semantics, gates, and operator truth across all supported surfaces.

#### Scenario: Multi-surface delivery runtime
- GIVEN a human request arrives from Telegram or another supported surface
- WHEN the control plane accepts the request
- THEN the runtime preserves one authoritative task/run/gate state model independent of the source surface

## ADDED Requirements

### Requirement: Route Decision Objects
The system SHALL persist route decisions as structured control-plane artifacts rather than inferring them only from chat history.

#### Scenario: A route is chosen
- GIVEN the runtime selects the next responsible role
- WHEN the route decision is made
- THEN the decision records reason, source signal, target role, and continuity anchor

### Requirement: Session-Style Coordination
The system SHALL add session-style continuity semantics on top of task tracking so that multi-role collaboration can survive retries, escalations, and gate handoffs.

#### Scenario: A task crosses multiple roles
- GIVEN a task moves from PM to Dev to Architect to QA to PM
- WHEN operators inspect the task
- THEN the task shows one coherent continuity object instead of fragmented mini-chains

## Implementation Anchors

- `nexuscrew/orchestrator.py` — `run_chain()`, `record_route_decision()`, `format_task_detail()`
- `nexuscrew/router.py` — mention parsing and default routing
- `nexuscrew/surfaces/service.py` — `submit_message()` and explicit task binding
- `nexuscrew/trace/store.py` — route timeline formatting

## Test Anchors

- `tests/test_runtime_events.py`
- `tests/test_chatops_service.py`
- `tests/test_access_dashboard.py`
- `tests/test_dashboard_detail.py`
