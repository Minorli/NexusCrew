# Change: Adopt OpenClaw Runtime Patterns for NexusCrew

## Why

Recent real Telegram task chains and drill runs showed that NexusCrew has crossed the threshold where chat-only coordination is no longer enough. The system now needs stronger runtime continuity, better task identity, lower-noise autonomy, stricter gate semantics, more reliable routing, and a first-class control-plane model.

OpenClaw demonstrates several patterns that are highly compatible with NexusCrew's direction:
- a gateway-centric control plane
- session and channel routing as first-class runtime concerns
- durable, human-readable local state
- proactive heartbeat-based autonomy
- strong onboarding and operator tooling
- tool execution and messaging surfaces unified under one runtime

This change adapts those strengths to NexusCrew's multi-role software delivery model rather than copying OpenClaw mechanically.

## What Changes

This change introduces a top-tier convergence plan that formalizes and phases the following areas:

1. Control plane hardening
2. Task continuity and anti-fragmentation
3. Agent presence, heartbeats, and low-noise autonomy
4. Git-first review and delivery handoff
5. QA quality gate and PM acceptance as strict runtime stages
6. Session-style coordination semantics between roles
7. Route and queue scheduling improvements
8. Better operator surfaces, onboarding, and diagnostics
9. Memory, checkpoint, and continuity upgrades
10. OpenSpec-native product and runtime governance

## Source Patterns Adopted

From official OpenClaw materials, this proposal deliberately absorbs:
- Gateway-centric control plane design
- Router-led dispatch of inbound events
- Heartbeat-based proactive autonomy
- Local-first persistent memory and transparent files/state
- Session and channel isolation as runtime primitives
- Low-friction onboarding and operational doctor tooling

## Expected Outcome

NexusCrew should evolve from:
- a good Telegram multi-agent coding bot

into:
- a durable, low-noise, highly coordinated software delivery runtime
- with better continuity, better state truth, better proactive behavior, and better operator control

## Scale of Change

This proposal intentionally scopes a very large convergence program. It organizes more than 200 concrete reinforcements across runtime, coordination, memory, gating, routing, observability, onboarding, and operator tooling.

## Impact

- Affected specs:
  - `control-plane-and-routing`
  - `task-continuity-and-state`
  - `review-quality-and-acceptance`
  - `autonomy-and-watchdogs`
  - `operator-surfaces`
- Affected code:
  - `nexuscrew/orchestrator.py`
  - `nexuscrew/telegram/`
  - `nexuscrew/runtime/`
  - `nexuscrew/task_state.py`
  - `nexuscrew/memory/`
  - `nexuscrew/executor/`
  - `nexuscrew/surfaces/`
  - `nexuscrew/setup_wizard.py`
  - future control-plane modules
