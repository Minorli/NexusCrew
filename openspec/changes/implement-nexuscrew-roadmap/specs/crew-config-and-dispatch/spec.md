## ADDED Requirements

### Requirement: YAML Crew Configuration
The system SHALL load crew configuration from a YAML file and initialize the same runtime state produced by the `/crew` command.

#### Scenario: Load a valid crew file
- **WHEN** a user runs `/load <path>` with a valid `crew.yaml`
- **THEN** the bot loads `project_dir`, agent specs, and orchestrator settings
- **AND** the project is scanned and shared memory sections are refreshed
- **AND** the registry is reinitialized from the YAML-defined crew

#### Scenario: Reject an invalid crew file
- **WHEN** a user runs `/load <path>` and the file is missing required fields or does not exist
- **THEN** the bot replies with a configuration loading error
- **AND** the current running crew remains unchanged

### Requirement: Dedicated Agent Bot Dispatch
The system SHALL send agent replies through a dedicated Telegram bot when a token is configured for that agent and SHALL fall back to the dispatcher bot otherwise.

#### Scenario: Dedicated bot exists
- **WHEN** an agent reply is sent and that agent has a configured bot token
- **THEN** the message is sent through the dedicated bot identity

#### Scenario: No dedicated bot exists
- **WHEN** an agent reply is sent and no dedicated bot token is configured
- **THEN** the message is sent through the dispatcher bot
- **AND** the message content still identifies the agent source

### Requirement: Agent Bot Group Validation
The system SHALL validate dedicated agent bot membership in the active Telegram group after crew initialization.

#### Scenario: Missing bot membership
- **WHEN** a configured dedicated bot is not present in the target group
- **THEN** the system reports that bot name back to the chat after crew initialization
