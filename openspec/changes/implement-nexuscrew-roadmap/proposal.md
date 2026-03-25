# Change: Implement NexusCrew Roadmap

## Why

`DESIGN.md` defines the target architecture, but the codebase currently only implements the core PM/Dev/Architect single-bot flow. The remaining roadmap in `IMPLEMENTATION.md` is required to reach the intended configuration, routing, HR, and advanced orchestration behavior.

## What Changes

- Add YAML-driven crew configuration loading and CLI preload support
- Add multi-bot dispatcher support with dedicated agent bots and fallback mode
- Add HR role support, metrics collection, evaluation triggers, pressure prompts, and laziness detection
- Add advanced orchestration capabilities: Anthropic dual-model thinking, task state tracking, git helpers, and metrics persistence

## Impact

- Affected specs: `crew-config-and-dispatch`, `hr-performance`, `advanced-orchestration`
- Affected code: `nexuscrew/telegram/`, `nexuscrew/orchestrator.py`, `nexuscrew/router.py`, `nexuscrew/backends/`, `nexuscrew/agents/`, `nexuscrew/executor/`, `nexuscrew/cli.py`
- Test impact: add unit coverage for all new modules and new orchestration behaviors
