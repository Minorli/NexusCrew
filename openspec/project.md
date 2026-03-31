# Project Context

## Purpose

NexusCrew is a Telegram-native multi-agent software delivery runtime. A human interacts in Telegram or another compatible collaboration surface, the control plane routes work across PM, Dev, Architect, QA, and HR roles, and the runtime preserves task continuity, review gates, quality gates, acceptance, memory, and durable execution state.

## Current Product Shape

NexusCrew is no longer just a sequential chat bot. It already contains:
- Telegram as the primary live control surface
- optional Slack and dashboard surfaces
- a durable SQLite-backed runtime state store
- explicit task state tracking
- background execution and recovery
- git-first review packet generation
- architect review, QA quality gate, and PM acceptance patterns
- OpenSpec artifacts for proposal-driven evolution

## System Layers

### Collaboration layer
- role routing across PM / Dev / Architect / QA / HR
- human follow-up binding
- role handoff and gate ownership

### Execution layer
- background runs
- checkpoints and replay
- approvals, branch sessions, PR and CI integration
- durable runtime truth in SQLite and append-only artifacts

### Observation layer
- status
- trace and artifacts
- doctor and dashboards
- presence, queues, family and session summaries

The control plane owns truth across all three layers. Surfaces are adapters, not the business-logic center.

## Tech Stack

- Python 3.11+
- `python-telegram-bot`
- OpenAI-compatible sync clients via `asyncio.to_thread`
- Anthropic sync clients via `asyncio.to_thread`
- Gemini CLI via subprocess
- SQLite for durable runtime state and memory
- PyYAML
- Pytest

## Project Conventions

### Code Style

- Package-internal imports use relative imports
- Keep edits ASCII unless the file already requires Chinese text
- Add only concise comments where behavior is not obvious
- Prefer many focused files over large multi-purpose modules

### Runtime Patterns

- `telegram/bot.py` and other surfaces are adapters, not the business-logic center
- `router.py` resolves `@mention` routing and role aliases
- `orchestrator.py` is the control-plane core for chain execution, retries, gates, continuity, memory extraction, and runtime truth
- `task_state.py` owns task, family, session, and queue semantics
- `runtime/runner.py` and `runtime/sqlite_store.py` own durable run and state semantics
- Agents delegate model calls to backend adapters and return `AgentArtifacts`
- Dev agents execute shell blocks through `executor/shell.py`
- Human-readable memory remains compact while durable memory lives in database form

### Testing Strategy

- Significant runtime changes require pytest coverage
- New modules require focused pytest coverage under `tests/`
- Runtime, routing, gates, shell behavior, continuity, recovery, and surface changes should get targeted regression tests
- OpenSpec changes should point to concrete pytest anchors

### Git Workflow

- Do not assume a git repository is present
- Never revert unrelated user changes
- Git helpers and review packets must degrade safely outside git repositories
- Review should prefer structured task-scoped git evidence over raw transcript output

## Domain Context

- The system is configured through `secrets.py` and YAML crew configuration files
- Agent routing is driven by `@mention` parsing plus continuity rules
- Agent replies may include `【MEMORY】` so the orchestrator can persist knowledge
- Telegram messages are chunked below the platform hard limit
- Review, QA, acceptance, waiting state, and recovery now matter as runtime stages, not just prompt conventions

## Important Constraints

- `DESIGN.md` remains historical architecture context
- `openspec/specs/` should represent current operational truth for major behaviors
- `secrets.py` is user-local and must not be modified in commits
- Backends remain sync clients wrapped with `asyncio.to_thread`
- Major runtime changes should flow through OpenSpec proposal artifacts

## Key Implementation Anchors

- `nexuscrew/orchestrator.py`
- `nexuscrew/task_state.py`
- `nexuscrew/runtime/runner.py`
- `nexuscrew/runtime/sqlite_store.py`
- `nexuscrew/surfaces/service.py`
- `nexuscrew/telegram/bot.py`
- `nexuscrew/dashboard/server.py`
- `nexuscrew/git/merge_gate.py`
- `nexuscrew/trace/store.py`

## Key Test Anchors

- `tests/test_runtime_events.py`
- `tests/test_task_state.py`
- `tests/test_pause_resume.py`
- `tests/test_chatops_service.py`
- `tests/test_access_dashboard.py`
- `tests/test_dashboard_detail.py`
- `tests/test_next_stack.py`
- `tests/test_orchestrator_substance.py`
- `tests/test_recovery_webhooks.py`

## External Dependencies

- Telegram Bot API
- OpenAI-compatible chat completion endpoint
- Anthropic Messages API
- Local Gemini CLI installation
- Git / GitHub / Slack APIs when those integrations are enabled
