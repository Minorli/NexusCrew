# Delta for task-continuity-and-state

## ADDED Requirements

### Requirement: Same-Topic Suppression
The system SHALL avoid creating sibling tasks for the same unresolved issue when the new message is clearly a continuation, escalation, or retry.

#### Scenario: Repeated follow-up on an unresolved issue
- GIVEN a task remains open
- WHEN a human message references the same active issue
- THEN the system continues the existing task instead of creating a new sibling task

### Requirement: Waiting vs Inflight Truth
The system SHALL present waiting tasks separately from inflight tasks and SHALL not let waiting tasks pollute inflight detection or stale-task escalation.

#### Scenario: Background step finishes but task is still open
- GIVEN the background run has no active execution left
- WHEN the task is waiting for the next role or human input
- THEN the run is classified as waiting rather than inflight

### Requirement: Resume and Replay Closure
The system SHALL preserve one continuity anchor across pause, resume, replay, and interrupted-run recovery.

#### Scenario: Resume uses checkpoint or continuation fallback
- GIVEN an operator resumes a paused task
- WHEN a checkpoint is missing but a continuation artifact exists
- THEN the runtime may continue from the continuation summary rather than failing silently

## MODIFIED Requirements

### Requirement: Review-State Accuracy
The system SHALL immediately demote a task out of review ownership when architect review falls back, rejects, or fails to produce a valid verdict.

#### Scenario: Invalid review conclusion
- GIVEN architect review fails to provide a valid conclusion
- WHEN the runtime escalates or falls back to PM
- THEN the task leaves `reviewing`
- AND ownership reflects the new responsible role

## Implementation Anchors

- `nexuscrew/task_state.py` — continuity, family, session, queue semantics
- `nexuscrew/runtime/runner.py` — waiting/interrupted status handling
- `nexuscrew/runtime/sqlite_store.py` — checkpoint and task persistence
- `nexuscrew/orchestrator.py` — pause, resume, replay, continuation

## Test Anchors

- `tests/test_task_state.py`
- `tests/test_pause_resume.py`
- `tests/test_runtime_events.py`
- `tests/test_recovery_webhooks.py`
