# Delta for autonomy-and-watchdogs

## ADDED Requirements

### Requirement: Cheap Proactive Autonomy
The system SHALL support periodic low-cost proactive checks that are distinct from full delivery execution.

#### Scenario: Heartbeat cycle runs
- GIVEN the runtime triggers a heartbeat or autonomy cycle
- WHEN no heavy delivery reasoning is required
- THEN the system uses a lightweight path and grouped reporting

### Requirement: Quiet Low-Noise Watchdogs
The system SHALL avoid turning watchdog and heartbeat behavior into operator spam.

#### Scenario: Multiple stale tasks are discovered
- GIVEN several tasks are stale
- WHEN the watchdog reports them
- THEN the operator receives grouped, low-noise output instead of per-task spam

### Requirement: Inflight vs stale watchdog behavior
The system SHALL distinguish stale tasks that are still inflight from stale tasks that have no active run.

#### Scenario: A stale inflight task is observed
- GIVEN a task exceeds SLA but still has an active run
- WHEN the watchdog evaluates it
- THEN the runtime reports the stale inflight state without auto-failing the task

## Implementation Anchors

- `nexuscrew/orchestrator.py` — `_run_agent_with_watchdog()`, `watchdog_tick()`, `proactive_tick()`
- `nexuscrew/task_state.py` — stale classification
- `nexuscrew/runtime/runner.py` — active and waiting run truth

## Test Anchors

- `tests/test_orchestrator_substance.py`
- `tests/test_runtime_events.py`
- `tests/test_pause_resume.py`
