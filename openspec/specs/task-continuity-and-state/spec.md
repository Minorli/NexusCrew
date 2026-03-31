# task-continuity-and-state Specification

## Purpose

Define how NexusCrew preserves task continuity, background-run truth, recovery state, and operator-visible summaries so work does not fragment, disappear, or appear falsely complete.

## Requirements

### Requirement: Task Continuity Over Message Continuity
The system SHALL preserve a stable task identity across follow-ups, escalations, retries, and review cycles unless the incoming request is truly a new task.

#### Scenario: Repeated follow-up on the same issue
- GIVEN a task is still active
- WHEN the human asks for progress, correction, retry, or continuation
- THEN the existing task remains the continuity anchor

#### Scenario: Explicit child task creation
- GIVEN a human explicitly asks to split or parallelize work under an existing task
- WHEN the new task is created
- THEN the child task preserves family continuity and parent linkage

### Requirement: Background-Run Truth
The system SHALL distinguish inflight work, waiting work, interrupted work, and terminal work.

#### Scenario: A task is waiting for the next action
- GIVEN the current background run has finished its immediate execution
- WHEN the underlying task is still open
- THEN the background run enters `waiting`
- AND the system does not present it as actively executing

#### Scenario: A task is truly inflight
- GIVEN an agent is actively executing a current step
- WHEN operators inspect status
- THEN the task appears as inflight and assigned to the current responsible role

#### Scenario: A process restarts during active execution
- GIVEN a background run was previously `pending` or `running`
- WHEN the runtime reloads durable state on startup
- THEN the stored run is marked `interrupted` until it is resumed, replayed, cancelled, or replaced

### Requirement: Session and Family Continuity
The system SHALL preserve session and family rollups so related tasks remain visible as one coordinated continuity object.

#### Scenario: Operators inspect a task family
- GIVEN a parent task and one or more related child tasks exist
- WHEN family summary is requested
- THEN the runtime reports family state, blocked reasons, completion state, and next actions across the family

#### Scenario: Operators inspect a session
- GIVEN multiple tasks belong to the same continuity session
- WHEN session summary is requested
- THEN the runtime reports a unified completion and blocked view for that session

### Requirement: Pause Resume Replay Closure
The system SHALL support pause, resume, and replay without losing the continuity anchor for the task.

#### Scenario: Resume uses the latest checkpoint
- GIVEN a paused task has a persisted checkpoint
- WHEN resume is requested
- THEN the runtime restores the latest checkpoint and continues the existing run

#### Scenario: Resume falls back to continuation
- GIVEN a paused task has no recoverable checkpoint
- WHEN a continuation artifact exists
- THEN the runtime may resume from the latest continuation summary instead of failing silently

### Requirement: Review-State Accuracy
The system SHALL not leave tasks visually stuck in `reviewing` after the review step has already fallen back, been rejected, or failed to produce a valid verdict.

#### Scenario: Invalid architect review reply
- GIVEN an architect produces no valid review conclusion
- WHEN the runtime falls back to PM re-planning
- THEN the task leaves `reviewing`
- AND ownership returns to PM or the next responsible role

### Requirement: Stale Task Handling
The system SHALL differentiate stale historical tasks from current task execution and SHALL avoid letting stale tasks pollute the active operator view.

#### Scenario: Historical task with no inflight run
- GIVEN a task is old and has no inflight background run
- WHEN watchdog checks task freshness
- THEN the task may be auto-failed or archived
- AND it is not presented as currently executing

## Implementation Anchors

- `nexuscrew/task_state.py` — task, family, session, and queue semantics
- `nexuscrew/runtime/runner.py` — background run lifecycle and interrupted/waiting truth
- `nexuscrew/runtime/sqlite_store.py` — durable persistence for tasks, checkpoints, events, and background runs
- `nexuscrew/orchestrator.py` — pause, resume, replay, checkpoint, continuation, and summaries
- `nexuscrew/runtime/recovery.py` — recovery and webhook-adjacent state restoration paths

## Test Anchors

- `tests/test_task_state.py`
- `tests/test_pause_resume.py`
- `tests/test_runtime_events.py`
- `tests/test_recovery_webhooks.py`
