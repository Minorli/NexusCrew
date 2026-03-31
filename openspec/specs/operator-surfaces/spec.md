# operator-surfaces Specification

## Purpose

Define the operator-facing surfaces through which NexusCrew is configured, observed, and controlled.

## Requirements

### Requirement: Multi-Surface Operator Control
The system SHALL expose a consistent operator control model across Telegram, Slack, dashboard views, and future surfaces.

#### Scenario: Operator checks task state
- GIVEN an operator requests task status from a supported surface
- WHEN the runtime renders status
- THEN the system reports active work, waiting work, failures, and current ownership consistently

### Requirement: Setup and Onboarding
The system SHALL support a first-run onboarding path that configures channels, agents, and local runtime state without manual file editing.

#### Scenario: No valid local configuration exists
- GIVEN the operator starts the system without valid config
- WHEN the CLI initializes
- THEN the setup wizard is offered as the default onboarding path

### Requirement: Human-Readable Durable Context
The system SHALL keep operator-visible summaries human-readable while storing runtime truth in durable machine-friendly state.

#### Scenario: Runtime memory grows over time
- GIVEN long-lived execution history accumulates
- WHEN the operator inspects human-readable context
- THEN the summary stays compact
- AND durable state remains queryable without bloating the human-facing file

### Requirement: Permission and Failure Boundaries Are Explicit
Operator surfaces SHALL return explicit permission or execution errors when the action cannot be performed.

#### Scenario: Operator lacks permission
- GIVEN a user without the required operator or approver role attempts a protected action
- WHEN the command is processed
- THEN the surface responds with a permission denial instead of silently proceeding

#### Scenario: Surface refresh fails
- GIVEN a surface command depends on runtime refresh or executor interaction
- WHEN that boundary call fails
- THEN the operator sees an explicit failure message tied to the failed action

### Requirement: Dashboard Detail Contract
The dashboard SHALL expose stable detail views for tasks, artifacts, trace, gates, continuation, runs, family, and session state.

#### Scenario: Operator opens a detail route
- GIVEN a valid dashboard detail path
- WHEN the route is resolved
- THEN the returned payload reflects the same control-plane truth visible through command surfaces

## Implementation Anchors

- `nexuscrew/surfaces/service.py` — shared operator commands and action boundaries
- `nexuscrew/telegram/bot.py` — Telegram commands, dashboard snapshot, dashboard detail routes, RBAC hooks
- `nexuscrew/dashboard/server.py` — dashboard HTTP surface
- `nexuscrew/policy/access.py` — permission model

## Test Anchors

- `tests/test_chatops_service.py`
- `tests/test_access_dashboard.py`
- `tests/test_dashboard_detail.py`
