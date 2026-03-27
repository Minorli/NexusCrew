# NexusCrew

NexusCrew is a Telegram-native multi-agent software delivery runtime.

It is designed to behave less like a single chatbot and more like a visible engineering team:
- PM plans and decomposes work
- Dev agents implement and validate changes
- Architect reviews for correctness and risk
- HR tracks delivery quality and team health
- GitHub keeps the durable engineering record

For Chinese documentation, see [README.zh-CN.md](README.zh-CN.md).

## Overview

NexusCrew turns a Telegram group into a collaborative engineering control room.

Core ideas:
- Real multi-role collaboration instead of a single assistant
- Telegram as the live command surface
- GitHub as the durable issue / PR / review ledger
- Background execution with pause / resume / replay
- Recoverable runtime with checkpoints and task state
- Governed autonomy with approval gates and audit trail

## Core Features

### Multi-Agent Runtime

- Heterogeneous agent roles: PM, Dev, Architect, HR
- Dynamic crew initialization from YAML or Telegram commands
- Multi-bot mode with per-agent Telegram identity
- Single-bot fallback when dedicated agent bots are not configured
- Explicit `@mention` routing with role aliases and bot-username mapping

### Delivery Workflow

- Task lifecycle tracking with explicit states
- Background runs for long-lived tasks
- Git branch session creation for Dev work
- Draft PR generation and PR-aware summaries
- CI summary ingestion and merge-gate support

### Reliability and Anti-Stall Controls

- Agent watchdog heartbeats for long-running tasks
- Automatic escalation when an agent stays silent too long
- Low-signal reply detection for “OK / received / reading code / later” style non-progress messages
- Automatic retry prompt when Dev / Architect replies are non-substantive
- Loop protection to avoid ping-pong routing cycles
- Task-stage watchdog to auto-close stale tasks without active background runs
- Grouped watchdog behavior so historical stale tasks do not flood Telegram

### Governance and Audit

- Approval gate for risky shell actions
- Append-only runtime event log
- Checkpoints for replay / recovery
- Artifacts store for execution side outputs
- SQLite-backed durable state for runs, tasks, checkpoints, approvals, and webhook deliveries

### Collaboration Surfaces

- Telegram: primary live control surface
- GitHub: issue/comment mirror, PR draft integration, CI lifecycle context
- Slack: optional secondary collaboration surface and App Home
- Read-only dashboard snapshot API

## Architecture

```text
Telegram / Slack
    -> ChatOps Layer
    -> Router + Orchestrator
    -> PM / Dev / Architect / HR Agents
    -> Shell / Git / Memory / Metrics / Trace
    -> GitHub / PR / CI / Artifacts / Checkpoints / SQLite State
```

## Installation

Install dependencies:

```bash
pip install anthropic openai python-telegram-bot pyyaml
```

Optional:

```bash
gemini auth login
```

Gemini is optional. A standard production setup can run entirely on Claude + Codex style backends.

## Configuration

Recommended path:

```bash
python3 -m nexuscrew setup
```

The local setup wizard:
- binds to `0.0.0.0` by default
- prints both `127.0.0.1` and detected LAN URLs
- writes local-only `secrets.py`
- writes local-only `crew.local.yaml`
- supports dedicated agent bots and bot-username mapping
- can validate and test integrations before launch

Manual configuration is also possible via:
- [`secrets.example.py`](secrets.example.py)
- [`crew.example.yaml`](crew.example.yaml)

## Run

Start normally:

```bash
python3 -m nexuscrew
```

If `secrets.py` is missing or invalid, NexusCrew automatically launches the setup wizard instead of crashing immediately.

You can preload a specific crew file:

```bash
python3 -m nexuscrew start -c crew.example.yaml
```

## Telegram Usage

Typical start flow:

```text
/start
/load crew.local.yaml
@nexus-pm-01 Plan a small feature and assign work
```

Key commands:

| Command | Description |
|---|---|
| `/crew <path> [agents]` | Create a crew from inline spec |
| `/load <crew.yaml>` | Load crew from YAML |
| `/status` | Show current agents, task board, and active background runs |
| `/tasks` | Show active background runs |
| `/failed` | Show failed background run archive |
| `/task <task_id>` | Show task detail |
| `/pause <task_id>` | Pause a task |
| `/resume <task_id>` | Resume a task |
| `/replay <task_id>` | Replay a task |
| `/approvals` | List pending risky actions |
| `/approve <id>` | Approve a gated action |
| `/reject <id>` | Reject a gated action |
| `/doctor` | Show runtime health summary |
| `/trace <task_id>` | Show task timeline |
| `/artifacts <task_id>` | Show task artifacts |
| `/pr <task_id>` | Show PR summary |
| `/ci <task_id>` | Show CI summary |
| `/board` | Show current status board |
| `/skills` | Show built-in skills |
| `/drill` | Run an internal collaboration drill |

## Collaboration Model

NexusCrew is opinionated about interaction quality:

- Telegram should not be flooded with raw shell logs and giant code blocks
- Dev execution details are summarized in Telegram and preserved in artifacts / GitHub
- Status-style questions should be answered by PM without waking the whole team
- Stale tasks should not spam the group repeatedly
- A top-level team summary belongs in Telegram
- Detailed command logs belong in artifacts, traces, and GitHub

## GitHub Integration

When GitHub sync is enabled:
- tasks can create or attach to issues
- human requests are mirrored to issue comments
- agent summaries are mirrored as durable comments
- PR draft generation can be tied to task context
- CI and PR lifecycle signals can feed back into the task view

This keeps Telegram live and lightweight while GitHub holds the long-lived engineering record.

## Reliability Notes

NexusCrew includes:
- agent watchdogs
- stale-task auto-failure for dead tasks with no active run
- grouped watchdog behavior so old tasks do not flood Telegram
- network retry handling for GitHub / PR / Slack integrations
- graceful degradation when external HTTP operations fail

## Security Notes

- `secrets.py` is local-only and gitignored
- risky shell actions can be approval-gated
- private planning notes and local runtime artifacts are intentionally excluded from public Git history
- recommended deployment target is a dedicated controlled machine

## Public Docs

- [`README.md`](README.md)
- [`README.zh-CN.md`](README.zh-CN.md)
- [`TELEGRAM_SETUP.md`](TELEGRAM_SETUP.md)
- [`crew.example.yaml`](crew.example.yaml)
- [`secrets.example.py`](secrets.example.py)

## License

MIT
