# control-plane-and-routing Specification

## Purpose

Define how NexusCrew accepts work from supported surfaces and turns it into one authoritative control-plane truth for routing, task continuity, gate progression, and operator-visible state.

## Requirements

### Requirement: Gateway-Style Control Plane
The system SHALL behave as a single control-plane runtime that owns task binding, route selection, state transitions, background execution, and operator-visible truth across Telegram, Slack, dashboard views, GitHub callbacks, and future supported surfaces.

#### Scenario: A request enters from a supported surface
- GIVEN a human request arrives from Telegram, Slack, or another supported surface
- WHEN NexusCrew accepts the request
- THEN one control-plane runtime chooses the task binding, target role, run identity, and execution path

### Requirement: Explicit Routing Semantics
The system SHALL route by explicit mention, task context, and continuity rules rather than by ambiguous free-text heuristics alone.

#### Scenario: A human mentions a role alias
- GIVEN a human sends `@pm` or `@architect`
- WHEN the message is parsed
- THEN the router resolves the message to the appropriate agent identity

#### Scenario: A human follows up on an existing task
- GIVEN an active task already exists in the chat
- WHEN the message is a continuation rather than a new request
- THEN the control plane binds the message to the current task instead of creating a duplicate task

#### Scenario: A same-topic message lacks an explicit task id
- GIVEN a human message overlaps with an active unresolved issue
- WHEN the message is judged to be a same-topic continuation
- THEN the runtime reuses the existing continuity anchor instead of opening a sibling task

### Requirement: Route Decisions Are Structured Runtime Facts
The system SHALL persist route decisions as durable control-plane facts rather than leaving routing intent implicit in chat history.

#### Scenario: A route decision is recorded
- GIVEN the runtime selects the next responsible role
- WHEN the route decision is persisted
- THEN the durable event records the reason, source signal, target agent, target role, and continuity anchor for task, session, and family

### Requirement: Multi-Agent Handoff Safety
The system SHALL support agent-to-agent handoff without short-cycle routing loops and without prematurely terminating legitimate revisits.

#### Scenario: Legitimate revisit
- GIVEN a flow `PM -> Dev -> Architect -> PM`
- WHEN PM needs to re-plan after review feedback
- THEN the route continues normally

#### Scenario: Ping-pong loop
- GIVEN a short-cycle route such as `Dev -> Architect -> Dev -> Architect`
- WHEN the same cycle repeats beyond the allowed threshold
- THEN the runtime stops the loop and records a routing failure

### Requirement: Surface Adapters Do Not Invent Runtime State
Operator surfaces SHALL render control-plane truth and SHALL not create a separate interpretation of task, gate, or run state.

#### Scenario: Status is rendered from different surfaces
- GIVEN the runtime has active, waiting, blocked, or failed work
- WHEN Telegram, Slack, or dashboard status views are requested
- THEN each surface renders the same control-plane truth using its own presentation format

## Implementation Anchors

- `nexuscrew/orchestrator.py` — `run_chain()`, `record_route_decision()`, `format_task_detail()`
- `nexuscrew/router.py` — mention parsing and default routing
- `nexuscrew/surfaces/service.py` — `submit_message()` and operator command routing
- `nexuscrew/telegram/bot.py` — surface entrypoints and dashboard detail routing
- `nexuscrew/trace/store.py` — route and gate timeline formatting

## Test Anchors

- `tests/test_runtime_events.py`
- `tests/test_chatops_service.py`
- `tests/test_dashboard_detail.py`
- `tests/test_access_dashboard.py`
