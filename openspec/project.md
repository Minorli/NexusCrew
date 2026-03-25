# Project Context

## Purpose

NexusCrew is a Telegram-native multi-agent software delivery system. A human interacts in a Telegram group, the system routes work across PM, Dev, Architect, and HR agents, and the agents collaborate through shared memory and local shell execution.

## Tech Stack

- Python 3.11+
- `python-telegram-bot`
- OpenAI sync client via `asyncio.to_thread`
- Anthropic sync client via `asyncio.to_thread`
- Gemini CLI via subprocess
- PyYAML
- Pytest

## Project Conventions

### Code Style

- Package-internal imports use relative imports.
- Keep edits ASCII unless the file already requires Chinese text.
- Add only concise comments where behavior is not obvious.
- Preserve the existing module layout unless `IMPLEMENTATION.md` requires a new file.

### Architecture Patterns

- `telegram/bot.py` receives commands and chat messages.
- `router.py` resolves `@mention` routing.
- `orchestrator.py` owns chain execution, retry handling, memory extraction, and agent history.
- Agents delegate model calls to backend adapters and return `AgentArtifacts`.
- Dev agents execute shell blocks through `executor/shell.py`.
- Shared memory is stored in `crew_memory.md`.

### Testing Strategy

- Every implementation task must be followed by `python3 -m pytest tests/ -v`.
- New modules require focused pytest coverage under `tests/`.
- Keep test style aligned with `tests/test_router.py` and `tests/test_registry.py`.

### Git Workflow

- Do not assume a git repository is present.
- Never revert unrelated user changes.
- When git helpers are added, they must degrade safely for non-git project directories.

## Domain Context

- The system is configured through `secrets.py` and optionally `crew.yaml`.
- Agent routing is driven by `@mention` parsing using regex `@(\w+)`.
- Agent replies may include `【MEMORY】` so Orchestrator can persist knowledge into shared memory.
- Telegram messages are chunked below the hard limit.

## Important Constraints

- Follow the phase and task order in `IMPLEMENTATION.md`.
- Do not add features outside the documented scope.
- Read `DESIGN.md` instead of inventing architecture.
- `secrets.py` is user-local and must not be modified.
- All backends remain sync clients wrapped with `asyncio.to_thread`.

## External Dependencies

- Telegram Bot API
- OpenAI-compatible chat completion endpoint
- Anthropic messages API
- Local Gemini CLI installation
