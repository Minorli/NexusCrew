# NexusCrew

NexusCrew is a Telegram-native multi-agent software delivery runtime.

It turns a Telegram group into a real operating room for software work:

- PM agents decompose requests
- Dev agents execute code and shell actions
- Architect agents review for correctness and safety
- HR agents track quality, trends, and execution health
- task activity is mirrored into GitHub issues and PR workflow artifacts

## Why It Feels Different

Most coding agents operate as a single assistant inside an editor.
NexusCrew is built around visible team collaboration:

- real multi-bot identity model in Telegram
- asynchronous background task execution
- task state tracking, pause/resume/replay
- risk-aware approvals for high-risk shell actions
- GitHub issue/comment mirroring for durable communication
- event log, checkpoints, artifacts, and trace timeline

The goal is not just "generate code", but "run a governable software delivery process".

## Core Capabilities

### Multi-Agent Team Runtime

- Heterogeneous roles: PM, Dev, Architect, HR
- Dynamic crew initialization from command line or YAML
- Multi-bot dispatcher with single-bot fallback
- Role routing and explicit `@mention` handoff

### Delivery Workflow

- Task tracker with lifecycle states
- Automatic branch session support
- PR draft generation hooks
- CI status ingestion hooks
- Merge-gate summaries

### Operational Control

- Background jobs for long-running work
- `/pause`, `/resume`, `/replay`
- `/tasks`, `/task`, `/cancel`
- `/approvals`, `/approve`, `/reject`
- `/doctor` for system health and HR-oriented diagnostics

### Traceability

- Append-only runtime event log
- Checkpoint persistence
- Artifact store
- Scoped memory and retrieval
- GitHub issue/comment mirroring

## Architecture Snapshot

```text
Telegram Group
    -> Dispatcher Bot Layer
    -> Orchestrator
    -> PM / Dev / Architect / HR Agents
    -> Shell / Git / GitHub / Memory / Metrics / Trace
```

In practice, NexusCrew behaves more like a small autonomous engineering organization than a plain chatbot.

## Quick Start

### 1. Install

```bash
pip install anthropic openai python-telegram-bot pyyaml
```

If you explicitly want Gemini CLI as an optional backend later, run `gemini auth login`.

### 2. Configure

Recommended: start the local setup wizard.

```bash
python3 -m nexuscrew setup
```

The wizard binds to `0.0.0.0` by default and will print both `127.0.0.1` and detected LAN IP URLs.

Then open the printed URL and complete:

- Telegram
- model credentials
- GitHub / Slack integrations
- default crew
- validation and launch

The setup UI can also configure:

- dedicated per-agent Telegram bots
- bot username to agent-name mapping
- dashboard / webhook / recovery controls

If you prefer manual setup, you can still create local-only files from:

- `secrets.example.py`
- `crew.example.yaml`

### 3. Run

```bash
python3 -m nexuscrew
```

If `secrets.py` is missing or invalid, NexusCrew will automatically start the local setup wizard instead of failing immediately.

You can still preload a specific crew configuration manually:

```bash
python3 -m nexuscrew start -c crew.example.yaml
```

### 4. Start a Crew in Telegram

```text
/crew ~/myproject pm:alice dev:bob dev:charlie architect:dave hr:carol
@alice Add JWT authentication for the user service
```

## Key Commands

| Command | Purpose |
|---|---|
| `/crew <path> [agents]` | Create a runtime crew from inline spec |
| `/load <crew.yaml>` | Load a crew from YAML |
| `/status` | Show agents, task board, and background jobs |
| `/tasks` | Show background runs |
| `/task <task_id>` | Show one task with GitHub / trace / artifact context |
| `/pause <task_id>` | Pause a task at the next safe boundary |
| `/resume <task_id>` | Resume from latest checkpoint |
| `/replay <task_id>` | Replay a task run |
| `/approvals` | List pending risky actions |
| `/approve <id>` | Approve and execute a gated action |
| `/reject <id>` | Reject a gated action |
| `/doctor` | Show system, task, and HR-oriented health summary |
| `/skills` | Show built-in workflow skills |

## GitHub Conversation Mirror

When GitHub sync is enabled, NexusCrew can keep the durable engineering conversation in GitHub as well:

- each tracked task can open or attach to a GitHub issue
- human requests are mirrored as issue comments
- agent replies and shell summaries are mirrored as comments
- PR draft and review workflow can be layered on top of the same task context

This means Telegram remains the live command surface, while GitHub keeps the durable written record.

## Security Model

- `secrets.py` is local-only and gitignored
- risky shell actions are gated behind approval requests
- runtime state and internal design files can remain local-only
- recommended deployment target is a dedicated, controlled machine

## Repository Layout

Public repository contents are intended to emphasize:

- source code
- tests
- public-facing usage docs

Internal planning notes, private prompts, local runtime state, and local-only design artifacts are intentionally kept out of Git history.

## Public Docs

- [TELEGRAM_SETUP.md](TELEGRAM_SETUP.md)
- [crew.example.yaml](crew.example.yaml)
- [secrets.example.py](secrets.example.py)

## License

MIT
