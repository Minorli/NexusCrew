# Delta for review-quality-and-acceptance

## MODIFIED Requirements

### Requirement: Git-First Review Packet
The system SHALL treat the review packet as the canonical review handoff for architect review, QA quality gate, and PM acceptance.

#### Scenario: Review-ready implementation
- GIVEN Dev has task-scoped changes and successful validation
- WHEN the handoff is generated
- THEN the runtime produces a review packet with task-scoped files, diff summary, validation result, and explicit next action

#### Scenario: Validation failure
- GIVEN a Dev reply contains a review request
- WHEN validation output indicates failure
- THEN the runtime does not enter architect review
- AND the next action is to repair validation

## ADDED Requirements

### Requirement: QA Quality Gate as Standard Runtime Stage
The system SHALL use QA quality gate behavior in normal tasks, not only in drills.

#### Scenario: Architect approves a normal task
- GIVEN architect review returns `LGTM`
- WHEN QA is available
- THEN the task enters QA quality gate before PM acceptance

### Requirement: Acceptance Gate as Standard Runtime Stage
The system SHALL require an explicit PM acceptance outcome before normal tasks are considered complete.

#### Scenario: QA passes and PM accepts
- GIVEN QA has already passed the task
- WHEN PM issues an acceptance pass decision
- THEN the task transitions to done

### Requirement: Merge Readiness Summary
The system SHALL summarize merge readiness from task state, approvals, CI, and artifacts.

#### Scenario: Operator inspects merge readiness
- GIVEN a task has current CI, artifact, and approval state
- WHEN merge readiness is rendered
- THEN the operator sees a clear merge-ready or blocked summary with reasons

## Implementation Anchors

- `nexuscrew/orchestrator.py` — gate artifacts and task detail
- `nexuscrew/git/merge_gate.py` — merge summary
- `nexuscrew/trace/store.py` — gate timeline formatting

## Test Anchors

- `tests/test_runtime_events.py`
- `tests/test_next_stack.py`
- `tests/test_access_dashboard.py`
- `tests/test_dashboard_detail.py`
