# OpenSpec Instructions

Instructions for AI coding assistants using OpenSpec for spec-driven development.

## TL;DR Quick Checklist

- Search existing work: `openspec spec list --long`, `openspec list`
- Decide scope: new capability vs modify existing capability
- Pick a unique `change-id`: kebab-case, verb-led
- Scaffold: `proposal.md`, `tasks.md`, `design.md` when needed, and delta specs
- Write deltas with `## ADDED|MODIFIED|REMOVED Requirements`
- Include at least one `#### Scenario:` per requirement
- Validate with `openspec validate [change-id] --strict`
- Do not start implementation until the proposal is approved

## Workflow

### Stage 1: Create Changes

Create a proposal when work adds capabilities, changes architecture, introduces breaking behavior, or changes security/performance characteristics.

Skip proposals for narrow bug fixes, typos, formatting-only edits, or tests for existing behavior.

### Stage 2: Implement Changes

1. Read `proposal.md`
2. Read `design.md` if present
3. Read `tasks.md`
4. Implement tasks in order
5. Run validation and tests
6. Mark completed tasks as `- [x]`

### Stage 3: Archive Changes

After deployment:

- Move `openspec/changes/<change-id>/` into `openspec/changes/archive/`
- Update `openspec/specs/` so the current truth matches shipped behavior
- Run `openspec validate --strict`

## Directory Structure

```text
openspec/
├── project.md
├── specs/
└── changes/
    ├── archive/
    └── <change-id>/
        ├── proposal.md
        ├── tasks.md
        ├── design.md
        └── specs/
```

## Project Notes

- `DESIGN.md` is the architecture source of truth for NexusCrew.
- `IMPLEMENTATION.md` defines the concrete task sequence and dependency order.
- Root `AGENTS.md` contains execution constraints that remain authoritative for coding and testing.
- For this repository, task order and acceptance criteria in `IMPLEMENTATION.md` take precedence over ad hoc refactors.
