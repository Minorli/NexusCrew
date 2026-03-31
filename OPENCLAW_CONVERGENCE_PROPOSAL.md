# OpenClaw Convergence Proposal for NexusCrew

This proposal captures how NexusCrew should absorb the strongest runtime patterns from OpenClaw without turning into an OpenClaw clone.

## What is being absorbed

From official OpenClaw materials, the most valuable patterns are:

1. A gateway-centric control plane
2. Session and channel continuity as runtime primitives
3. Heartbeat-driven proactive autonomy
4. Local-first durable state and transparent operator-facing files
5. Strong onboarding and doctor-style operational tooling
6. Clear separation between routing, reasoning, execution, and response surfaces

## What is not being copied blindly

NexusCrew is a software delivery runtime, not a general-purpose personal assistant. So the goal is not:
- 50+ chat surfaces
- personal-life assistant behaviors
- broad consumer-device tooling

The goal is:
- better software-delivery continuity
- better multi-role coordination
- better runtime truth
- better task/gate/operator semantics

## Convergence thesis

OpenClaw shows that autonomy feels excellent when:
- the runtime is always-on
- the control plane owns truth
- proactive loops are cheap and low-noise
- channels are adapters, not the center of the system

NexusCrew should apply exactly that lesson to:
- PM / Dev / Architect / QA / HR collaboration
- task continuity
- git-first review
- quality and acceptance gates
- operator trust in status and recovery

## Main gaps in NexusCrew today

1. A task can still fragment into several siblings under pressure
2. Waiting vs inflight semantics are only partially clean
3. Route/gate decisions still depend too much on transcript interpretation
4. Proactive autonomy exists, but is not yet a first-class cheap control-plane loop
5. Operator surfaces still require too much log-reading
6. OpenSpec exists, but has not yet become the governance default

## Main answer

This proposal turns OpenClaw's strongest patterns into a NexusCrew-specific architecture program:
- task-centric session continuity
- control-plane-first routing
- strict git-first handoff
- standard QA and PM gates
- cheaper proactive runtime checks
- operator surfaces that reflect runtime truth
- OpenSpec as the default architecture-governance mechanism

## Artifact of record

The formal change package for this convergence lives in:

- `openspec/changes/adopt-openclaw-runtime-patterns/`

The baseline operational truth now lives in:

- `openspec/specs/`

## Primary references

- OpenClaw official repository: `https://github.com/openclaw/openclaw`
- OpenClaw official docs / architecture materials: `https://clawdocs.org/architecture/overview`
- OpenClaw product site: `https://openclaw.ai/`
- OpenSpec official repository: `https://github.com/Fission-AI/OpenSpec`
- OpenSpec official site: `https://openspec.dev/`

## Deep research artifact

The deeper primary-source research notes now live in:

- `research/OPENCLAW_DEEP_DIVE.md`
