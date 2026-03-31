## ADDED Requirements

### Requirement: HR Role Support
The system SHALL support an `hr` role in routing, crew construction, and direct invocation.

#### Scenario: HR is initialized in a crew
- **WHEN** a crew is created with an `hr` agent specification
- **THEN** the registry includes that agent
- **AND** `@hr` and exact HR agent names route to the HR agent

### Requirement: Agent Metrics Collection
The system SHALL collect per-agent productivity, quality, efficiency, and collaboration metrics during orchestration.

#### Scenario: Agent handle completes
- **WHEN** an agent finishes one `handle()` call
- **THEN** the collector updates task counts and response time
- **AND** shell execution, retries, review results, and memory note counters are updated when applicable

### Requirement: Automatic HR Evaluation
The system SHALL trigger HR evaluation asynchronously after a task chain completes when an HR agent is present.

#### Scenario: Completed task chain with HR
- **WHEN** orchestration finishes a task chain and an HR agent exists
- **THEN** the orchestrator schedules an HR evaluation without blocking the main chain response
- **AND** the HR reply is sent back to the chat

### Requirement: Pressure and Laziness Signals
The system SHALL derive pressure prompts and laziness signals from recent agent performance.

#### Scenario: Low recent performance
- **WHEN** an agent's recent scores and metrics indicate sustained underperformance
- **THEN** the system computes a non-zero pressure level
- **AND** writes an HR notice for that agent into shared memory

#### Scenario: Shallow or evasive reply
- **WHEN** an agent returns a shallow, repetitive, evasive, or buck-passing reply
- **THEN** the laziness detector reports the triggered heuristic labels

### Requirement: Metrics History Persistence
The system SHALL append evaluation snapshots to a JSONL history file for later trend analysis.

#### Scenario: HR evaluation is recorded
- **WHEN** a score is produced for an agent
- **THEN** the system appends a JSONL record containing the score and metric summary
- **AND** later reads can return recent score history for that agent
