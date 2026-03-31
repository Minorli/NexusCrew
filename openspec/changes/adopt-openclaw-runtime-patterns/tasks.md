# Tasks

## 1. OpenSpec Deployment
- [x] 1.1 Add `openspec/README.md` with repository-local workflow
- [x] 1.2 Add baseline specs for control plane, continuity, gates, autonomy, and operator surfaces
- [x] 1.3 Update `openspec/project.md` to reflect current runtime architecture
- [ ] 1.4 Archive or supersede obsolete OpenSpec roadmap changes once baseline truth is updated
- [x] 1.5 Add implementation anchors and test anchors to core specs
- [x] 1.6 Align `adopt-openclaw-runtime-patterns` deltas with current runtime and test anchors

## 2. Control Plane Hardening
- [ ] 2.1 Introduce explicit route decision objects
- [ ] 2.2 Introduce explicit gate decision objects
- [ ] 2.3 Separate reactive runs from proactive runs
- [ ] 2.4 Separate inflight, waiting, stale, and terminal run semantics everywhere
- [ ] 2.5 Persist richer route and gate events

## 3. Task Continuity
- [ ] 3.1 Add stronger same-topic follow-up binding
- [ ] 3.2 Add duplicate-task suppression
- [ ] 3.3 Add parent/child or family linking for related tasks
- [ ] 3.4 Preserve retry lineage and gate lineage
- [ ] 3.5 Improve assignment continuity across PM/Dev/Architect/QA/PM loops
- [ ] 3.6 Close pause/resume/replay continuity gaps

## 4. Scheduling and Queues
- [ ] 4.1 Split inflight queues from waiting queues
- [ ] 4.2 Add queue-aware status reporting
- [ ] 4.3 Improve retry backoff and deduplication
- [ ] 4.4 Improve timeout taxonomy by role and stage
- [ ] 4.5 Add better fairness and priority semantics

## 5. Git-First Delivery Workflow
- [ ] 5.1 Make review packet the default review contract everywhere
- [ ] 5.2 Enrich review packets with PR and CI context when available
- [ ] 5.3 Block review when validation failed
- [ ] 5.4 Preserve packet lineage per task
- [ ] 5.5 Add operator-facing packet inspection helpers

## 6. Strict Gates
- [ ] 6.1 Strengthen architect review normalization
- [ ] 6.2 Strengthen QA verdict normalization
- [ ] 6.3 Strengthen PM acceptance normalization
- [ ] 6.4 Add richer gate state summaries
- [ ] 6.5 Add gate timeout and retry semantics
- [ ] 6.6 Strengthen merge-readiness summaries

## 7. Proactive Autonomy
- [ ] 7.1 Split cheap heartbeat checks from expensive delivery work
- [ ] 7.2 Add role-aware heartbeat tasks
- [ ] 7.3 Add quiet hours and escalation windows
- [ ] 7.4 Add grouped, low-noise proactive reporting
- [ ] 7.5 Add manual heartbeat / dry-run operator controls

## 8. Memory and Checkpoints
- [ ] 8.1 Add clearer memory tiers
- [ ] 8.2 Add checkpoint labels by stage and reason
- [ ] 8.3 Add better resume and replay targeting
- [ ] 8.4 Add compact operator memory summaries
- [ ] 8.5 Add stronger provenance between artifacts, memory, and tasks

## 9. Operator Surfaces
- [ ] 9.1 Improve board/status taxonomy
- [ ] 9.2 Add better waiting-task and gate views
- [ ] 9.3 Improve doctor diagnostics for runtime truth mismatches
- [ ] 9.4 Improve setup-wizard diagnostics around live deployment
- [ ] 9.5 Improve dashboard routes for route/gate/run inspection
- [ ] 9.6 Add explicit failure-path coverage for shared command surfaces

## 10. Observability and Governance
- [ ] 10.1 Expand runtime event taxonomy
- [ ] 10.2 Add route/gate postmortem helpers
- [ ] 10.3 Add better stale-task reasoning artifacts
- [ ] 10.4 Add better override / bypass audit behavior
- [ ] 10.5 Require OpenSpec updates for major runtime behavior changes
- [ ] 10.6 Add spec-to-test traceability for core runtime behavior
