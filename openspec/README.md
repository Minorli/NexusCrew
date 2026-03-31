# NexusCrew OpenSpec Workspace

This repository uses OpenSpec-style artifacts to keep major runtime and product changes explicit, reviewable, and durable.

## Why it exists

NexusCrew is no longer a small Telegram bot. It now has:
- a long-running runtime
- multiple agent roles
- gated delivery stages
- durable state
- multi-surface operator control

Those changes are too important to manage only through chat history and code diff. OpenSpec gives the project:
- baseline specs for how the system behaves today
- change folders for proposed behavior changes
- explicit proposal / design / task artifacts before implementation
- a stable place to anchor tests against runtime behavior

## Directory structure

```text
openspec/
├── README.md
├── AGENTS.md
├── project.md
├── specs/
│   ├── autonomy-and-watchdogs/
│   ├── control-plane-and-routing/
│   ├── review-quality-and-acceptance/
│   ├── task-continuity-and-state/
│   └── operator-surfaces/
└── changes/
    ├── archive/
    ├── implement-nexuscrew-roadmap/
    └── adopt-openclaw-runtime-patterns/
```

## Baseline vs change artifacts

Use `openspec/specs/` for current operational truth.

Use `openspec/changes/<change-id>/` for proposed behavior changes that modify:
- runtime semantics
- routing or continuity rules
- gate behavior
- autonomy/watchdog behavior
- operator-facing control surfaces
- durable state or recovery behavior

`DESIGN.md` remains historical architecture context. The baseline specs are the place to describe what the system currently guarantees.

## Intended workflow

1. Create or update a change proposal in `openspec/changes/<change-id>/`
2. Review:
   - `proposal.md`
   - `design.md`
   - `tasks.md`
   - spec deltas under `specs/`
3. Implement only after the proposal is approved
4. Add or update pytest coverage for the changed runtime behavior
5. Update baseline specs in `openspec/specs/` when the change ships

## Spec writing policy

Each major spec should describe:
- purpose
- requirements
- executable scenarios
- implementation anchors in the codebase
- test anchors in the pytest suite

That keeps specs connected to real code and real regression coverage, not just design intent.

## Validation policy

Before considering a major runtime change complete:

1. Validate OpenSpec artifacts:

```bash
openspec validate --strict
```

2. Run targeted pytest suites for the affected behavior.

At minimum, control-plane changes should point to one or more of:
- `tests/test_runtime_events.py`
- `tests/test_task_state.py`
- `tests/test_pause_resume.py`
- `tests/test_chatops_service.py`
- `tests/test_access_dashboard.py`
- `tests/test_dashboard_detail.py`
- `tests/test_next_stack.py`
- `tests/test_orchestrator_substance.py`
- `tests/test_recovery_webhooks.py`

## OpenSpec CLI

If you want to use the official OpenSpec CLI locally:

```bash
npm install -g @fission-ai/openspec@latest
openspec validate --strict
```

This repository does not require the CLI to read or review spec artifacts, but the directory layout is intentionally compatible with the official tool and workflow.

## Current policy

- Use OpenSpec for architecture, workflow, routing, autonomy, reliability, recovery, and governance changes
- Do not create a proposal for trivial typo-only edits
- Keep baseline specs concise but strict
- Keep changes phased and testable
- Prefer updating existing specs over creating overlapping ones
