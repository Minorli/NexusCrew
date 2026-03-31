# Design

## Context

This change implements the roadmap already described in `DESIGN.md` and decomposed in `IMPLEMENTATION.md`. The OpenSpec change exists to track the delivery plan and the normative behavior that will be added across the remaining phases.

## Goals

- Keep implementation aligned with the documented Phase 1-4 task order
- Encode the remaining architecture into OpenSpec requirements before code changes continue
- Preserve existing working paths while layering new capabilities incrementally

## Non-Goals

- Redesigning the architecture beyond `DESIGN.md`
- Introducing capabilities not explicitly listed in `IMPLEMENTATION.md`
- Replacing sync backends with async SDKs

## Decisions

- `DESIGN.md` remains the architecture source of truth; this change mirrors it rather than replacing it.
- OpenSpec is introduced with project context plus one active roadmap change instead of attempting to retroactively spec every already-implemented feature.
- The roadmap is grouped into three capability areas: crew configuration/dispatch, HR performance management, and advanced orchestration.

## Risks / Trade-offs

- Some roadmap items cross multiple modules; tests must be added alongside implementation to avoid regressions.
- The repository may not always be inside a git checkout, so git workflow support must fail soft.
- Telegram multi-bot behavior depends on runtime credentials and group membership, so tests should focus on deterministic integration boundaries.

## Migration Plan

1. Introduce OpenSpec scaffolding and roadmap change files.
2. Implement tasks in `IMPLEMENTATION.md` order.
3. Keep OpenSpec task checklists synchronized with delivered code.
4. Archive the change after the roadmap ships and baseline specs are updated.
