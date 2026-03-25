<!-- OPENSPEC:START -->
# OpenSpec Instructions

These instructions are for AI assistants working in this project.

Always open `@/openspec/AGENTS.md` when the request:
- Mentions planning or proposals (words like proposal, spec, change, plan)
- Introduces new capabilities, breaking changes, architecture shifts, or big performance/security work
- Sounds ambiguous and you need the authoritative spec before coding

Use `@/openspec/AGENTS.md` to learn:
- How to create and apply change proposals
- Spec format and conventions
- Project structure and guidelines

Keep this managed block so `openspec update` can refresh the instructions.
<!-- OPENSPEC:END -->

# Codex 指令

> 本文件由 Codex 自动读取，作为整个会话的持久指令。

## 目标

按照 `IMPLEMENTATION.md` 的 Phase/Task 顺序，完成 NexusCrew 的全部实现。
架构设计详见 `DESIGN.md`，不要偏离其中的决策。

## 执行顺序

```
Phase 1: Foundation Fixes（可并行）
  1.1 YAML 配置加载器 → nexuscrew/config.py（新建）+ telegram/bot.py
  1.2 Router HR 别名 → router.py
  1.3 Backend 错误处理 → openai_backend.py + anthropic_backend.py

Phase 2: Multi-Bot Dispatcher（依赖 1.1）
  2.1 Dispatcher Bot 架构 → telegram/dispatcher.py（新建）+ telegram/bot.py
  2.2 Agent Bot 群组验证 → telegram/dispatcher.py
  2.3 单 Bot 降级兼容 → 无新代码，验证 2.1 的 fallback
  2.4 CLI 入口更新 → cli.py

Phase 3: HR Agent（依赖 1.2）
  3.1 HR Agent 骨架 → agents/hr.py（新建）+ telegram/bot.py
  3.2 AgentMetrics 数据采集 → metrics.py（新建）+ orchestrator.py
  3.3 HR 评估触发器 → orchestrator.py
  3.4 督促 prompt 注入 → agents/base.py + orchestrator.py
  3.5 懈怠检测 → metrics.py

Phase 4: Advanced（依赖 Phase 1-3）
  4.1 Anthropic extended thinking → anthropic_backend.py
  4.2 任务状态机 → 独立
  4.3 Git 工作流 → 独立
  4.4 指标持久化 → 依赖 3.2
```

## 核心规则

1. **严格按 Task 顺序**：每个 Task 的依赖关系写在 IMPLEMENTATION.md 里，不要跳步。
2. **每个 Task 完成后跑测试**：`python3 -m pytest tests/ -v`，全部通过再继续。
3. **遇到不确定的问题**：查 DESIGN.md 对应章节，不要问我。
4. **新增模块必须配套测试**：写到 `tests/` 目录，pytest 风格，参考现有的 `test_router.py` 和 `test_registry.py`。
5. **不要自作主张加功能**：只实现 IMPLEMENTATION.md 里明确列出的内容。

## 代码约定

### Import 风格
- 包内一律用相对导入：`from .xxx import`、`from ..xxx import`
- 外部库正常导入：`import openai`

### Backend 模式
- 所有 Backend 用 **sync client + `asyncio.to_thread`**，不要改成 async client
- OpenAI: `openai.OpenAI(api_key=..., base_url=...)`
- Anthropic: `anthropic.Anthropic(api_key=..., base_url=...)`
- Gemini: subprocess 调用本地 CLI，无 API key

### 配置读取
- 所有 API key / URL 从 `secrets.py` 读取，**绝不依赖环境变量**
- 只修改 `secrets.example.py` 模板，**不要碰 secrets.py**
- `secrets.py` 已在 `.gitignore`

### 协议约定（不要改）
- Agent 回复末尾 `【MEMORY】` 标记由 Orchestrator 提取写入 `crew_memory.md`
- `@mention` 路由由 Router 处理，正则 `@(\w+)`
- Shell 代码块格式 ` ```bash ... ``` `，由 ShellExecutor 提取执行
- 消息分块上限 3800 字符（Telegram 硬限 4096，留 margin）

## 验收标准

每个 Task 在 IMPLEMENTATION.md 中都有明确的验收标准，严格对照。
总体验收：
- `python3 -m pytest tests/ -v` 全绿
- `python3 -m nexuscrew` 能启动（不需要真实 token，不报 import 错即可）
- 无 linting 级别的明显问题（未使用 import、语法错误等）
