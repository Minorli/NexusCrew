## ADDED Requirements

### Requirement: Architect Model Selection
The system SHALL allow the Architect agent to choose between the primary Anthropic model and an optional light model, and SHALL enable extended thinking for heavy architecture work.

#### Scenario: Heavy architecture question
- **WHEN** the Architect handles a message about architecture, security, migration, performance, or major refactoring
- **THEN** the backend enables extended thinking when supported by the selected model

#### Scenario: Routine review
- **WHEN** the Architect handles a lightweight review request and a light model is configured
- **THEN** the backend uses the light model instead of the primary model

### Requirement: Task Lifecycle Tracking
The system SHALL track the lifecycle of user requests through planning, implementation, review, validation, and completion states.

#### Scenario: Valid transition
- **WHEN** a task transitions from `planning` to `in_progress`
- **THEN** the state machine accepts the transition and records the change in task history

#### Scenario: Invalid transition
- **WHEN** a task attempts to move directly from `planning` to `done`
- **THEN** the state machine rejects the transition

### Requirement: Status Board Visibility
The system SHALL expose active task states through the `/status` command.

#### Scenario: Active tasks exist
- **WHEN** a chat has active tracked tasks
- **THEN** `/status` includes the active task board alongside the agent roster

### Requirement: Git Workflow Helpers
The system SHALL provide helper methods for creating feature branches, committing work, and reading the current branch, while degrading safely outside git repositories.

#### Scenario: Git project available
- **WHEN** a Dev task starts in a valid git working tree
- **THEN** the executor can create a feature branch derived from the tracked task identifier

#### Scenario: Non-git project
- **WHEN** git helper commands run in a directory that is not a git repository
- **THEN** the orchestration flow continues without crashing
