# Design

## Context

NexusCrew already has several advanced traits:
- multi-agent runtime
- task state tracking
- Telegram and Slack surfaces
- durable SQLite state
- review / QA / acceptance gates
- watchdogs and recovery

But the current design still behaves too much like a chat chain and not enough like a long-running control plane. OpenClaw's architecture suggests a better organizing principle: treat the runtime itself as the product surface, and treat channels as inputs and outputs into that runtime.

## Official patterns absorbed

From OpenClaw's official repository and docs:
- Gateway-centric architecture with a single long-running process as control plane
- Router dispatch as a first-class step in data flow
- Heartbeat autonomy that can proactively act without flooding the user
- Local-first persistent state and transparent files
- Session / channel concepts used to isolate work
- Strong onboarding and operational health tooling

From OpenSpec's official project:
- baseline specs as current truth
- per-change proposal / design / task / delta-spec artifacts
- artifact-guided workflow rather than chat-history-only planning

## Design Goals

1. Make NexusCrew behave like a durable control plane, not just a sequential message chain
2. Preserve one coherent task identity across retries, reviews, escalations, and follow-ups
3. Separate inflight work, waiting work, stale work, and archived work cleanly
4. Reduce Telegram noise while increasing runtime observability
5. Make review / QA / acceptance consume structured artifacts instead of transcripts
6. Let agents proactively ask for help and continue execution without fake loops
7. Make operator trust proportional to runtime truth
8. Put product and runtime evolution under OpenSpec governance

## Non-Goals

- Turning NexusCrew into a generic personal assistant platform
- Replacing Telegram-first delivery workflows with an OpenClaw clone
- Replacing the current Python runtime with OpenClaw's TypeScript runtime
- Building all proposed enhancements in a single implementation batch

## Architecture direction

### 1. Promote the Orchestrator into an explicit Control Plane

NexusCrew should continue converging toward:

```text
Input surface -> router -> control plane -> task/session runtime -> gates/tools/memory -> output surface
```

Practical implications:
- the control plane owns truth for routing, state transitions, run semantics, gates, continuity, and escalation
- surfaces are adapters, not business-logic centers
- agent prompts remain important, but runtime policy becomes equally important

### 2. Introduce session-style continuity on top of task continuity

OpenClaw is strong at long-lived conversations and session routing. NexusCrew should adapt that idea as:
- chat session
- task session
- role handoff session
- gate session

This does not require copying OpenClaw's exact session model. It requires adding enough runtime structure so that:
- one task does not explode into five sibling tasks
- PM/Dev/Architect/QA interactions have a stable continuity object
- follow-ups know whether they are:
  - a new task
  - a continuation
  - a retry
  - a gate decision

### 3. Split autonomy into cheap health checks vs expensive delivery work

OpenClaw's heartbeat model should inform NexusCrew in two layers:
- cheap periodic control-plane checks
- expensive delivery execution only when needed

This suggests:
- lightweight autonomy/heartbeat model path
- delivery model path
- quiet hours / notification windows / escalation windows

### 4. Make git-first handoff the default runtime contract

The runtime should treat the review packet as a contract:
- delivery summary
- changed files
- diff summary
- validation
- explicit next ask

Architect, QA, and PM acceptance should consume that contract rather than raw transcripts.

### 5. Strengthen operator-facing truth surfaces

The operator should be able to ask:
- what is inflight?
- what is merely waiting?
- what is stale?
- what is blocked?
- what is currently under review?
- what is waiting for QA?
- what was the last valid artifact?

without reading logs.

## Enhancement catalog

The following grouped enhancement set defines the intended scope for this change. The total size is intentionally large and cumulative.

### Workstream A — Control Plane Hardening (22 reinforcements)
1. Explicit control-plane state object
2. Route decision objects
3. Handoff reason codes
4. Gate transition reason codes
5. Input classification before task creation
6. Message intent categories
7. Surface adapter contracts
8. Reply policy contracts
9. Better chain completion semantics
10. Better interrupted semantics
11. Better waiting semantics
12. Better operator-visible failure semantics
13. Better escalation reason persistence
14. Better system-generated action classification
15. Distinguish reactive vs proactive runs
16. Distinguish human-triggered vs watchdog-triggered runs
17. Distinguish review vs implementation loops
18. Distinguish approval-blocked vs execution-blocked runs
19. Richer run summary artifacts
20. Control-plane versioned events
21. Replay-safe state restoration
22. Strict invariants around task ownership

### Workstream B — Task Continuity (24 reinforcements)
1. Same-topic follow-up binding
2. Duplicate-task suppression
3. Task family grouping
4. Parent/child task relationships
5. Retry lineage
6. Escalation lineage
7. Gate lineage
8. Human follow-up classification
9. PM follow-up classification
10. Architect feedback continuity
11. QA verdict continuity
12. Acceptance continuity
13. Background-run to task binding hardening
14. Waiting-run to task binding hardening
15. Better stale-task demotion
16. Better stale-task archival
17. Active-task selection by strongest continuity signal
18. Distinguish unblock vs replan follow-up
19. Distinguish “status request” vs “continue task”
20. Distinguish “cancel task” vs “abandon branch”
21. Preserve task-local branch intent
22. Preserve task-local review packet history
23. Preserve task-local gate history
24. Preserve task-local delivery verdicts

### Workstream C — Scheduling and Queues (20 reinforcements)
1. Per-chat queue visibility
2. Per-task queue visibility
3. Per-agent queue visibility
4. Better queue fairness
5. Better retry backoff
6. Separate delivery queue from heartbeat queue
7. Separate approval queue from execution queue
8. Better stalled-run resumption policy
9. Better run deduplication
10. Better delayed follow-up scheduling
11. Better timeout reason taxonomy
12. Better gate timeout taxonomy
13. Architect review timeout vs silence timeout split
14. QA timeout vs no-verdict split
15. PM acceptance timeout vs indecision split
16. Per-stage SLA configuration
17. Queue-aware operator status board
18. Queue-aware drill reporting
19. Better flood-control aware scheduling
20. Run priority model

### Workstream D — Presence and Heartbeats (18 reinforcements)
1. Role presence summary
2. Agent busy/idle/waiting signal
3. Role-level heartbeat policies
4. Cheap heartbeat reasoning path
5. Quiet hours
6. Escalation quiet windows
7. Manual heartbeat trigger
8. Heartbeat dry-run
9. Heartbeat operator report
10. Heartbeat task types
11. Per-role proactive checklists
12. PM-specific proactive checks
13. QA-specific proactive checks
14. Dev-specific proactive checks
15. Architect-specific proactive checks
16. Noise-suppressed grouped heartbeat output
17. Better heartbeat event persistence
18. Better heartbeat cost controls

### Workstream E — Review / QA / Acceptance (24 reinforcements)
1. Review packet versioning
2. Review packet validation
3. Review packet lineage
4. Architect verdict normalization
5. QA verdict normalization
6. Acceptance verdict normalization
7. Structured rejection reason schema
8. Structured risk schema
9. Structured validation schema
10. Gate replay support
11. Gate re-entry support
12. Gate timeout policies
13. Gate-owner reassignment rules
14. PR-aware review packet enrichment
15. CI-aware review packet enrichment
16. QA evidence packet
17. Acceptance evidence packet
18. Release readiness packet
19. Better no-go demotion semantics
20. Better lgtm promotion semantics
21. Better acceptance completion semantics
22. Better release-blocked semantics
23. Better post-acceptance reporting
24. Better operator gate summaries

### Workstream F — Memory and Checkpoints (20 reinforcements)
1. Session memory tier
2. Task memory tier
3. Role memory tier
4. Shared memory tier
5. Canonical spec memory tier
6. Memory TTL policy
7. Memory compaction policy
8. Better checkpoint reason codes
9. Better checkpoint stage labels
10. Checkpoint recovery previews
11. Better resume targeting
12. Better replay targeting
13. Better context-budget summaries
14. Better memory write reasons
15. Better memory provenance
16. Review packet memory links
17. Acceptance memory links
18. Better stale memory cleanup
19. Better human-readable summary sync
20. Searchable memory views

### Workstream G — Operator Surfaces (18 reinforcements)
1. Better status board taxonomy
2. Better failed archive taxonomy
3. Better waiting-task board
4. Better gate board
5. Better assignee board
6. Better branch/PR board
7. Better review/QA/acceptance board
8. Better dashboard detail routes
9. Better setup-wizard operational hints
10. Better doctor diagnostics
11. Better restart diagnostics
12. Better onboarding diagnostics
13. Better live presence summaries
14. Better stale task explanation
15. Better “why did this route” explanation
16. Better “why is this waiting” explanation
17. Better “why did this fail” explanation
18. Better operator handoff commands

### Workstream H — Skills and Self-Improvement (18 reinforcements)
1. Formal skill registry in runtime
2. Skill-aware planning
3. Skill-aware review
4. Skill-aware QA
5. Skill provenance tracking
6. Skill safety policy
7. Skill recommendation persistence
8. Skill usage metrics
9. Agent self-improvement proposals via OpenSpec
10. Better skill docs
11. Better skill onboarding
12. Better task-to-skill linking
13. Better drill-to-skill validation
14. Better skill drift detection
15. Better model-policy by skill
16. Better role-policy by skill
17. Better skill failure reporting
18. Better curated capability packs

### Workstream I — Onboarding and Deployment (20 reinforcements)
1. Explicit control-plane onboarding checklist
2. Surface-by-surface onboarding
3. Better Telegram deployment checks
4. Better Slack deployment checks
5. Better GitHub webhook checks
6. Better dashboard checks
7. Better memory health checks
8. Better approval-gate checks
9. Better bot-group membership checks
10. Better current chat-id diagnostics
11. Better startup DNS diagnostics
12. Better connectivity self-test
13. Better system service deployment
14. Better restart safety
15. Better live process ownership model
16. Better daemon notes
17. Better secrets health validation
18. Better model routing validation
19. Better post-setup control page
20. Better disaster-recovery runbook

### Workstream J — Observability and Audit (18 reinforcements)
1. Richer event taxonomy
2. Better route decision events
3. Better gate decision events
4. Better proactive-run events
5. Better stalled-run reasons
6. Better grouped failure reasons
7. Better shell-output summaries
8. Better artifact typing
9. Better audit trail around approvals
10. Better audit trail around overrides
11. Better PM override logging
12. Better bypass semantics
13. Better operator action logs
14. Better drill-to-runtime comparison reports
15. Better state mismatch detection
16. Better timeline views
17. Better postmortem generation
18. Better release-note generation

### Workstream K — Security and Governance (16 reinforcements)
1. Better DM/group policy model
2. Better channel allowlist policy
3. Better risk-tier mapping
4. Better approval reason schema
5. Better approval expiry
6. Better override expiry
7. Better sandbox policy visibility
8. Better branch safety policy
9. Better push safety policy
10. Better PR safety policy
11. Better secret-redaction policy
12. Better unsafe transcript suppression
13. Better review bypass recording
14. Better governance-specific doctor checks
15. Better role permission boundaries
16. Better audit export

### Workstream L — OpenSpec-native Product Governance (12 reinforcements)
1. Baseline specs for current runtime truth
2. New change proposal for major architecture shifts
3. Archive discipline for completed changes
4. Runtime capability specs
5. Surface capability specs
6. Gate behavior specs
7. Memory specs
8. Continuity specs
9. Operator experience specs
10. Proposal-first major changes
11. Review against spec deltas, not just code
12. Post-ship baseline updates

Total planned reinforcements in this proposal: 232

## Phases

### Phase 1 — Formalize runtime truth
- deploy baseline OpenSpec specs
- codify current runtime semantics
- remove chat-history-only ambiguity

### Phase 2 — Control-plane and continuity hardening
- unify route, run, task, and waiting semantics
- stop task fragmentation and stale-state pollution

### Phase 3 — Gate and delivery workflow convergence
- make git-first handoff and gate workflow universal in normal tasks
- strengthen QA and acceptance runtime semantics

### Phase 4 — Proactive autonomy and operator experience
- better heartbeats
- better low-noise presence
- better doctor, dashboard, setup, and recovery

### Phase 5 — Governance and self-improving product loop
- formal OpenSpec-first evolution
- runtime postmortems
- proposal-first major improvements

## Risks

- The proposal is large and must be phased; attempting everything at once would create instability.
- NexusCrew is multi-surface and multi-role, so every runtime semantic change needs regression coverage.
- OpenClaw patterns are inspiring but not directly transferable without adaptation to NexusCrew's delivery-focused domain.

## Success Criteria

- task continuity survives real-world multi-role chats
- review / QA / acceptance are strict and low-noise
- waiting vs inflight semantics are operator-visible and trustworthy
- watchdog and heartbeat behavior is proactive without spam
- OpenSpec becomes the default governance model for major runtime changes
