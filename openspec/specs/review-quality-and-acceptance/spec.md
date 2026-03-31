# review-quality-and-acceptance Specification

## Purpose

Define the delivery gates that turn raw implementation into review-ready, testable, acceptance-ready, and merge-ready work.

## Requirements

### Requirement: Git-First Review Packet
The system SHALL prepare a compact, task-scoped review packet for code review rather than relying on raw shell transcript or pasted code.

#### Scenario: Dev requests review
- GIVEN a Dev agent believes implementation is ready for review
- WHEN the public handoff is generated
- THEN the handoff includes task-scoped files, diff summary, validation result, and next review action

#### Scenario: No task-scoped changes exist
- GIVEN a Dev agent has not produced task-local code changes
- WHEN the public handoff is generated
- THEN the system does not issue a real review request
- AND the next step remains implementation

### Requirement: Validation Failure Blocks Review
The system SHALL not continue into architect review if validation failed.

#### Scenario: Tests fail
- GIVEN a Dev reply includes a review request
- WHEN the recorded validation output indicates failure
- THEN the next step remains fixing validation
- AND architect review is not triggered

### Requirement: Architect Review Gate
The system SHALL normalize architect review into explicit gate outcomes.

#### Scenario: Architect approves the change
- GIVEN architect review returns `LGTM`
- WHEN the gate decision is recorded
- THEN the task advances out of review state
- AND a `review:approved` gate artifact is persisted

#### Scenario: Architect rejects or requests changes
- GIVEN architect review returns a rejection or change request
- WHEN the gate decision is recorded
- THEN the task returns to implementation
- AND the rejection remains visible in gate summaries and operator views

### Requirement: Quality Gate
The system SHALL support a QA quality gate between architect review and PM acceptance when a QA role is available.

#### Scenario: Architect approves the change
- GIVEN architect review returns `LGTM`
- WHEN QA is present in the crew
- THEN the task enters a quality gate before final acceptance

#### Scenario: QA blocks the change
- GIVEN QA returns `No-Go`
- WHEN the quality gate completes
- THEN the task returns to `in_progress`

### Requirement: Acceptance Gate
The system SHALL require a PM acceptance conclusion before a task is considered complete.

#### Scenario: QA passes and PM accepts
- GIVEN QA returns `Go` or `Conditional Go`
- WHEN PM marks acceptance as passed
- THEN the task enters the completed state

#### Scenario: PM rejects acceptance
- GIVEN PM determines acceptance criteria are not met
- WHEN PM rejects the change
- THEN the task returns to `in_progress`

### Requirement: Merge Readiness Summary
The system SHALL expose a merge-readiness summary that combines task state, approvals, CI, and delivery artifacts.

#### Scenario: Operator inspects merge readiness
- GIVEN a task has review, CI, artifact, and approval state
- WHEN merge readiness is rendered
- THEN the output clearly states whether the task is merge-ready and why or why not

### Requirement: Gate Explanations Stay Operator Visible
The system SHALL keep recent gate outcomes visible in task detail, gate summaries, traces, and dashboard-friendly views.

#### Scenario: Operator asks why a task is blocked
- GIVEN the latest gate decision rejected or blocked the task
- WHEN task detail or gate summary is requested
- THEN the operator can see the latest gate outcome without reading raw transcripts

## Implementation Anchors

- `nexuscrew/orchestrator.py` — gate detection, gate artifacts, task detail, task progression
- `nexuscrew/git/merge_gate.py` — merge readiness summary
- `nexuscrew/trace/store.py` — gate timeline formatting
- `nexuscrew/dashboard/server.py` — gate and task detail surface exposure

## Test Anchors

- `tests/test_runtime_events.py`
- `tests/test_next_stack.py`
- `tests/test_access_dashboard.py`
- `tests/test_dashboard_detail.py`
