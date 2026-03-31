# autonomy-and-watchdogs Specification

## Purpose

Define how NexusCrew transitions from reactive chat handling into proactive, low-noise autonomy with explicit watchdog, heartbeat, and escalation behavior.

## Requirements

### Requirement: Agent-Level Heartbeats
The system SHALL emit lightweight heartbeats when an agent stays silent beyond the configured threshold while work is still inflight.

#### Scenario: Long-running agent step
- GIVEN an agent has not replied within the configured heartbeat interval
- WHEN the runtime is still waiting on that step
- THEN the operator surface emits a heartbeat summary without dumping raw logs

### Requirement: Task-Level Watchdog
The system SHALL track stale tasks separately from current inflight work.

#### Scenario: Task is stale with an inflight run
- GIVEN a task exceeds the task-stage SLA
- WHEN an inflight background run still exists for that task
- THEN the watchdog reports the stale inflight task without auto-closing it

#### Scenario: Task is stale with no inflight run
- GIVEN a task exceeds the task-stage SLA
- WHEN no inflight background run exists for that task
- THEN the runtime may auto-close or auto-fail the task

### Requirement: Low-Noise Proactive Autonomy
The system SHALL support proactive checks and escalations without flooding chat surfaces.

#### Scenario: A watchdog cycle finds multiple stale tasks
- GIVEN multiple tasks are stale
- WHEN the watchdog runs
- THEN the operator surface receives grouped, low-noise reporting rather than one message per stale task

### Requirement: Cheap Autonomous Checks
The system SHALL allow periodic health, continuity, and coordination checks to run on lower-cost reasoning paths than full delivery work.

#### Scenario: Periodic background autonomy tick
- GIVEN the system runs a proactive health or continuity check
- WHEN no heavy reasoning is required
- THEN the runtime uses a lightweight check path rather than a full delivery chain

## Implementation Anchors

- `nexuscrew/orchestrator.py` — `_run_agent_with_watchdog()`, `watchdog_tick()`, `proactive_tick()`
- `nexuscrew/runtime/runner.py` — inflight vs waiting run semantics used by watchdog and status output
- `nexuscrew/task_state.py` — stale vs inflight/waiting classification

## Test Anchors

- `tests/test_orchestrator_substance.py`
- `tests/test_runtime_events.py`
- `tests/test_pause_resume.py`
