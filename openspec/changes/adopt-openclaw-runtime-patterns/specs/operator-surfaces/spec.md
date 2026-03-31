# Delta for operator-surfaces

## ADDED Requirements

### Requirement: OpenSpec-Governed Runtime Evolution
The system SHALL track major runtime behavior changes through OpenSpec proposals, design decisions, tasks, and spec deltas.

#### Scenario: Major orchestration change is proposed
- GIVEN a change modifies runtime semantics for routing, gates, continuity, or autonomy
- WHEN the change is prepared
- THEN the repository contains a corresponding OpenSpec change artifact before implementation proceeds

### Requirement: Better Operator Truth Surfaces
The system SHALL present inflight work, waiting work, stale work, and blocked work distinctly in operator surfaces.

#### Scenario: Operator requests status
- GIVEN the runtime has a mixture of inflight, waiting, and failed work
- WHEN status is rendered
- THEN the output distinguishes those categories explicitly

### Requirement: Explicit Permission and Failure Responses
The system SHALL return explicit permission and execution boundaries on operator surfaces.

#### Scenario: Operator lacks permission
- GIVEN a protected command is invoked by an unauthorized user
- WHEN the command runs
- THEN the surface reports a permission denial

#### Scenario: Detail route is requested
- GIVEN a dashboard detail route targets task, trace, artifact, gate, continuation, family, session, or run data
- WHEN the route resolves
- THEN the payload reflects the same control-plane truth exposed by the command surfaces

## Implementation Anchors

- `nexuscrew/surfaces/service.py` — shared operator commands
- `nexuscrew/telegram/bot.py` — command handlers, snapshot, detail routes
- `nexuscrew/dashboard/server.py` — dashboard HTTP surface
- `nexuscrew/policy/access.py` — access control

## Test Anchors

- `tests/test_chatops_service.py`
- `tests/test_access_dashboard.py`
- `tests/test_dashboard_detail.py`
