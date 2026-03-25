# NexusCrew — 系统设计文档

> 本文档面向实现者。描述系统的完整架构、数据流、接口契约与扩展方式。

---

## 目录

1. [项目概述](#1-项目概述)
2. [架构总览](#2-架构总览)
3. [组件设计](#3-组件设计)
4. [关键工作流](#4-关键工作流)
5. [配置 Schema](#5-配置-schema)
6. [路由规则](#6-路由规则)
7. [共享记忆系统](#7-共享记忆系统)
8. [项目自动识别](#8-项目自动识别)
9. [安全模型](#9-安全模型)
10. [扩展指南](#10-扩展指南)
11. [已知限制与未来规划](#11-已知限制与未来规划)
12. [项目文件结构](#12-项目文件结构)
13. [Telegram 群组架构](#13-telegram-群组架构)
14. [命名规范与模型配置](#14-命名规范与模型配置)
15. [任务生命周期与团队工作模式](#15-任务生命周期与团队工作模式)
16. [Git 工作流](#16-git-工作流)
17. [实时可见性设计](#17-实时可见性设计)
18. [HR Agent 角色定义](#18-hr-agent-角色定义)
19. [阿里绩效评分体系](#19-阿里绩效评分体系ali-style-performance-system)
20. [工作量指标与现实价值追踪](#20-工作量指标与现实价值追踪)
21. [督促与鞭策机制](#21-督促与鞭策机制motivation--pressure-system)

---

## 1. 项目概述

NexusCrew 是一个运行在独立 Linux 主机上的多智能体软件开发协作系统。
用户通过 Telegram 群聊下达需求，系统内多个 AI Agent 分工协作完成编码、测试、Review 全流程。

### 核心设计目标

| 目标 | 说明 |
|---|---|
| 异构模型防回音壁 | PM/Dev/Architect 使用不同厂商模型，避免同质化思维 |
| 动态编组 | 运行时按需启动任意数量、任意命名的 Agent 实例 |
| 大型代码库感知 | 自动扫描现有项目，生成结构化项目简报注入所有 Agent |
| 共识持久化 | 所有 Agent 共享一个 Markdown 记忆文件，跨会话保留决策 |
| ChatOps 原生 | 全程通过 Telegram @mention 驱动，无 Web 前端 |
| 宿主机直执行 | 代码直接在主机运行，无 Docker 隔离，Dev 拥有完整 Shell 权限 |

---

## 2. 架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                         Telegram 客户端                          │
│                       (人类通过手机交互)                           │
└────────────────────────────┬────────────────────────────────────┘
                             │  HTTPS / Webhook
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      TelegramBot 层                              │
│   接收消息 · 发送消息 · /crew /start 命令 · 白名单过滤 · 消息分块   │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Orchestrator                               │
│   异步事件循环 · Agent 链执行 · Dev 重试追踪 · 自动升级逻辑        │
└──────┬─────────────────────┬────────────────────────────────────┘
       │                     │
       ▼                     ▼
┌─────────────┐    ┌──────────────────────────────────────────────┐
│   Router    │    │                 Agent Registry               │
│  @mention   │    │  name → AgentInstance (动态注册/注销)         │
│  解析与分发  │    └───────┬──────────────────┬───────────────────┘
└─────────────┘            │                  │
                           ▼                  ▼
              ┌──────────────────┐   ┌────────────────────┐
              │   Agent 实例池    │   │   Agent 实例池      │
              │  pm: alice(gem)  │   │  dev: bob(codex)   │
              │  dev: charlie    │   │  architect: dave   │
              └────────┬─────────┘   └─────────┬──────────┘
                       │                       │
                       ▼                       ▼
              ┌─────────────────────────────────────────────────┐
              │                  LLM Backends                   │
              │  GeminiCLIBackend │ OpenAIBackend │ AnthropicBackend│
              └─────────────────────────────────────────────────┘
                       │
              ┌────────┴────────────────────────┐
              ▼                                 ▼
   ┌─────────────────────┐          ┌────────────────────────┐
   │   Shell Executor    │          │    Memory System       │
   │  提取并执行 bash 块   │          │  crew_memory.md R/W   │
   │  返回 stdout/stderr  │          │  project_briefing.md  │
   └─────────────────────┘          └────────────────────────┘
```

### 进程模型

整个系统运行在单个 Python 进程内，使用 `asyncio` 事件循环。
Telegram polling 与 Agent 链执行均为异步任务，互不阻塞。
Shell 执行与 LLM CLI 调用通过 `asyncio.to_thread` 在线程池中运行，避免阻塞事件循环。

---

## 3. 组件设计

### 3.1 TelegramBot 层 (`nexuscrew/telegram/`)

**职责**
- 使用 `python-telegram-bot` 接收/发送消息
- 实现 `/crew` 命令解析（动态编组）
- 实现 `/status` 命令（展示当前 Agent 列表与状态）
- 白名单 chat_id 过滤
- 长消息自动分块（TG 单条上限 4096 字符）
- 消息格式化（Markdown 代码块、状态前缀）

**关键接口**
```python
class TelegramLayer:
    async def send(self, chat_id: int, text: str) -> None: ...
    async def send_code(self, chat_id: int, code: str, lang: str = "") -> None: ...
```

### 3.2 Orchestrator (`nexuscrew/orchestrator.py`)

**职责**
- 接收来自 TelegramBot 层的消息事件
- 调用 Router 确定首个目标 Agent
- 执行 Agent 链（循环调用，最多 `MAX_CHAIN_HOPS` 跳）
- 追踪每个 chat_id 的 Dev 连续失败次数
- 在失败次数 ≥ `MAX_DEV_RETRY` 时强制升级给 Architect
- 管理每个 chat_id 的对话历史（窗口大小可配）

**Agent 链伪代码**
```
function run_chain(message, target, chat_id):
    for hop in 0..MAX_CHAIN_HOPS:
        if target == dev and dev_retries[chat_id] >= MAX_DEV_RETRY:
            target = architect
            message = wrap_escalation(message)

        agent = registry.get(target)
        reply, artifacts = await agent.handle(message, history[chat_id])

        send_to_telegram(reply)
        if artifacts.shell_output:
            send_to_telegram(artifacts.shell_output)

        update_history(chat_id, agent.name, reply)
        extract_and_save_memory(agent.name, reply)

        next_target = router.detect(reply)
        if not next_target or next_target == target:
            break
        target, message = next_target, reply
```

### 3.3 Router (`nexuscrew/router.py`)

**职责**
- 解析消息文本中的 @mention
- 支持两种路由模式：
  - **名字路由**：`@bob` → 找 registry 中名为 bob 的 Agent
  - **角色路由**：`@dev` `@pm` `@architect` → 在该角色的所有实例中选一个（轮询）
- 无 @mention 时默认路由到 PM

**匹配优先级**
1. 精确名字匹配（`@bob`）
2. 角色别名匹配（`@dev_1`, `@dev`, `@pm`, `@architect`）
3. 默认：PM（轮询）

**实现要点**
- 用正则 `@(\w+)` 提取所有 mention，取第一个有效匹配
- Agent 注册时同时注册其名字和角色到 Router

### 3.4 Agent Registry (`nexuscrew/registry.py`)

动态注册表，支持运行时增删 Agent 实例。

```python
class AgentRegistry:
    _agents: dict[str, BaseAgent]  # name → agent
    _by_role: dict[str, list[BaseAgent]]  # role → [agents]
    _rr_index: dict[str, int]  # role → round-robin index

    def register(self, agent: BaseAgent) -> None: ...
    def get_by_name(self, name: str) -> BaseAgent | None: ...
    def get_by_role(self, role: str) -> BaseAgent | None:  # round-robin
    def list_all(self) -> list[dict]:  # for /status command
    def unregister(self, name: str) -> None: ...
```

### 3.5 Agent 基类 (`nexuscrew/agents/base.py`)

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class AgentArtifacts:
    shell_output: str = ""      # 执行 bash 块的输出
    memory_note: str = ""       # 需写入共享记忆的内容
    next_mention: str = ""      # 检测到的下一个 @mention

class BaseAgent(ABC):
    name: str           # 实例名，如 "bob"
    role: str           # 角色类型："pm" | "dev" | "architect"
    model_label: str    # 显示用，如 "codex", "gemini", "claude"

    @abstractmethod
    async def handle(
        self,
        message: str,
        history: list[dict],
        crew_memory: str,
    ) -> tuple[str, AgentArtifacts]: ...

    def build_system_prompt(self, crew_memory: str, project_briefing: str) -> str:
        """子类可 override，默认拼接角色 prompt + 记忆 + 项目简报。"""
        ...
```

### 3.6 PM Agent (`nexuscrew/agents/pm.py`)

- 后端：`GeminiCLIBackend`
- 特点：大上下文处理能力，适合日志分析与任务拆解
- 构建完整 prompt（角色提示 + 共享记忆 + 近期对话 + 当前消息）后调用 CLI
- 响应中必须包含 @mention 指派下一执行者

### 3.7 Dev Agent (`nexuscrew/agents/dev.py`)

- 后端：`OpenAIBackend`
- 特点：高频调用，Trial & Error 模式
- `handle()` 流程：
  1. 构建消息历史（system + 近 6 轮对话 + 当前消息）
  2. 调用 OpenAI API
  3. 调用 `ShellExecutor.run_blocks(reply)` 执行所有 bash 块
  4. 将 shell 输出追加回 artifacts
  5. 失败检测：shell_output 含 error/traceback/failed 关键词
- 返回 `(llm_reply, artifacts)`，由 Orchestrator 决定是否计入失败次数

### 3.8 Architect Agent (`nexuscrew/agents/architect.py`)

- 后端：`AnthropicBackend`
- 特点：稀缺资源，被动触发
- 只有 Orchestrator 将消息路由到 Architect 时才调用
- 触发条件（由 Orchestrator 判断，非 Architect 自判）：
  - 消息中含 `@architect`
  - Dev 连续失败达 `MAX_DEV_RETRY`

### 3.9 LLM Backends (`nexuscrew/backends/`)

后端层将模型调用与 Agent 逻辑解耦，便于扩展新模型。

```python
class LLMBackend(ABC):
    @abstractmethod
    async def complete(
        self,
        system: str,
        messages: list[dict],  # [{"role": "user"|"assistant", "content": str}]
    ) -> str: ...
```

| Backend | 实现方式 | 特殊处理 |
|---|---|---|
| `GeminiCLIBackend` | `asyncio.to_thread(subprocess.run, ...)` | system+messages 拼接为单一 prompt 字符串传入 CLI |
| `OpenAIBackend` | `openai.OpenAI` (sync) + `asyncio.to_thread` | 直接传 messages 列表 |
| `AnthropicBackend` | `anthropic.Anthropic` (sync) + `asyncio.to_thread` | system 独立参数，messages 列表 |

**GeminiCLIBackend prompt 拼接策略**

由于 Gemini CLI 接受单一文本输入，需将结构化历史序列化：

```
[SYSTEM]
{system_prompt}

[CONVERSATION HISTORY]
user: {msg1}
assistant: {msg2}
...

[CURRENT MESSAGE]
{latest_message}
```

### 3.10 Shell Executor (`nexuscrew/executor/shell.py`)

```python
class ShellExecutor:
    def __init__(self, work_dir: Path, timeout: int = 120): ...

    async def run_blocks(self, text: str) -> str:
        """提取文本中所有 ```bash/sh 块，顺序执行，返回合并输出。"""

    async def run_single(self, cmd: str) -> tuple[str, int]:
        """执行单条命令，返回 (output, returncode)。"""
```

- `work_dir` 默认为目标项目目录（非 `./workspace`，见第 8 节）
- 单条命令 timeout=120s
- stdout 截断：前 1000 + 后 800 字符，中间插入 `...[截断 N 字符]...`
- stderr 截断：500 字符
- 失败判定：returncode != 0 或 stderr 含 `error|traceback|failed|exception`

---

## 4. 关键工作流

### 4.1 动态编组流程 (`/crew` 命令)

```
用户发送：
  /crew ~/comparator pm:alice dev:bob dev:charlie architect:dave

1. parse_crew_command()        → 解析为 [{role, name, model}, ...]
2. ProjectScanner.scan(path)   → 生成 project_briefing（见第 8 节）
3. 实例化各 Agent              → alice=PMAgent, bob/charlie=DevAgent, dave=ArchitectAgent
4. registry.register(all)      → 注册名字+角色路由
5. 写 crew_memory.md           → 项目简报 + 编组清单
6. TG 回复编组成功摘要
```

**命令格式**
```
/crew <project_path> [role:name[(model)]] ...
```
示例：
```
/crew ~/comparator pm:alice dev:bob dev:charlie architect:dave
/crew /srv/app pm:pm1 dev:dev1(codex) dev:dev2(codex) architect:arch1(claude)
```
`model` 省略时按角色默认：pm→gemini, dev→codex, architect→claude

### 4.2 标准任务执行流

```
[Human]  @alice 给项目加 Redis 缓存
    │
    ▼  Router → "alice" (pm)
[PM alice]  读 crew_memory + 历史 → 输出任务清单
            "...@bob 请执行步骤1和2，@charlie 请执行步骤3"
    │
    ├──▶ [Dev bob]   实现 Cache 类 → 执行 bash 块 → 测试
    │         成功 → "@dave Code Review: ..."
    │         失败(≤5次) → 自行重试
    │         失败(>5次) → Orchestrator 强制路由 @dave
    │
    └──▶ [Dev charlie]  实现接口层 → 执行 bash 块 → 测试
              成功 → "@dave Code Review: ..."
    │
    ▼  Router → "dave" (architect)
[Architect dave]  审查代码
    → LGTM：任务完成，dev_retries 清零
    → 打回："@bob 第42行并发不安全，请修改"
```

### 4.3 多 Dev 并行执行

PM 回复中含多个不同 Dev @mention 时，Orchestrator 并行启动：

```python
mentions = router.detect_all(reply)  # e.g. ["bob", "charlie"]
if len(mentions) > 1:
    await asyncio.gather(*[
        run_agent_chain(extract_subtask(reply, m), m, chat_id, send)
        for m in mentions
    ])
```

`extract_subtask(reply, name)` 提取 reply 中属于该 agent 的部分（按 @name 后的段落切割）。

### 4.4 Dev 自动升级

```python
# orchestrator.py 内
if target_role == "dev" and dev_retries.get(chat_id, 0) >= cfg.MAX_DEV_RETRY:
    arch = registry.get_by_role("architect")
    escalation_msg = (
        f"@{arch.name} 自动升级（Dev 连续失败 {cfg.MAX_DEV_RETRY} 次）\n"
        f"最近报错：\n{last_shell_output[-600:]}\n"
        f"Dev 最后回复：\n{last_dev_reply[-400:]}"
    )
    target, message = "architect", escalation_msg
    dev_retries[chat_id] = 0
```

---

## 5. 配置 Schema

### `secrets.py`（不提交 git）

```python
TELEGRAM_BOT_TOKEN: str
TELEGRAM_ALLOWED_CHAT_IDS: list[int]  # 空列表=接受所有

OPENAI_API_KEY: str
OPENAI_BASE_URL: str    # 默认 "https://api.openai.com/v1"
OPENAI_MODEL: str       # 默认 "codex-mini-latest"

ANTHROPIC_API_KEY: str
ANTHROPIC_MODEL: str    # 默认 "claude-opus-4-6"

GEMINI_CLI_CMD: list[str]       # e.g. ["gemini"]
GEMINI_PROMPT_FLAG: str | None  # e.g. "-p"；None 则走 stdin

WORKSPACE_DIR: str  # Dev 执行根目录，默认项目目录本身
```

### `crew.yaml`（可提交）

```yaml
project_dir: ~/comparator

agents:
  - role: pm
    name: alice
    model: gemini
    system_prompt_extra: |        # 可选：追加到角色 prompt 末尾
      本项目使用 Django ORM，禁止引入 SQLAlchemy。
  - role: dev
    name: bob
    model: codex
  - role: dev
    name: charlie
    model: codex
  - role: architect
    name: dave
    model: claude

orchestrator:
  max_chain_hops: 10
  max_dev_retry: 5
  history_window: 20          # 每 chat 保留最近 N 条消息
  memory_tail_lines: 120      # 注入 Agent 的共享记忆行数
  shell_timeout: 120          # bash 块单条命令超时(秒)
```

---

## 6. 路由规则

### @mention 解析算法

```
1. 正则提取文本中所有 @(\w+)
2. 按出现顺序遍历：
   a. registry.get_by_name(token)  → 精确名字匹配（优先）
   b. registry.get_by_role(token)  → 角色别名（pm/dev/architect/dev_1 等）
3. 返回第一个有效匹配（单目标路由）
   或返回所有有效匹配（多目标并行路由，仅当角色不同时启用）
4. 无匹配 → 默认路由到 PM（轮询）
```

### 角色别名表

| @mention | 路由目标 |
|---|---|
| `@pm`, `@pm_1` | PM 角色（轮询） |
| `@dev`, `@dev_1`, `@dev_2` | Dev 角色（轮询） |
| `@architect`, `@arch` | Architect 角色（轮询） |
| `@alice`, `@bob`, ... | 精确 Agent 实例 |

### 轮询策略

同角色多实例时，Registry 维护 round-robin 索引，每次 `get_by_role()` 递增，实现均匀分配。

---

## 7. 共享记忆系统

### 设计原则

- 单一文件 `crew_memory.md`，Markdown 格式，人类可读可编辑
- 追加写入（append-only），保留完整历史
- 读取时取末尾 N 行（可配），控制注入 token 量
- Agent 通过 `【MEMORY】` 标记在回复中声明要记录的内容

### 文件结构

```markdown
# NexusCrew 共享记忆

## 项目简报
(ProjectScanner 自动生成)

## 当前编组
(每次 /crew 命令覆写)

---
**[2026-03-25 10:30] pm/alice**
已将任务拆解为3个子任务，分配给 bob 和 charlie。

---
**[2026-03-25 10:45] dev/bob**
Redis Cache 类已实现，路径 src/cache.py，使用连接池。
```

### 写入触发

Agent 在回复末尾附加：
```
【MEMORY】Redis 连接池配置在 src/cache.py:12，最大连接数 50。
```
Orchestrator 在收到 Agent 回复后自动提取并写入文件，`【MEMORY】` 标记之后内容不发送到 TG。

### 对大型项目的适配

对于几万行的已有代码库：
1. `ProjectScanner` 首次扫描生成结构化简报（见第 8 节），写入 `crew_memory.md` 顶部
2. PM 每次处理任务前读取末尾 120 行，获得足够上下文
3. Dev 读取末尾 60 行（减少 token 消耗，Dev 更关注具体任务）
4. Architect 读取末尾 80 行（需要架构决策上下文）
5. 人类可随时手动编辑 `crew_memory.md` 向所有 Agent 广播信息

---

## 8. 项目自动识别

### ProjectScanner

`/crew` 命令触发后，`ProjectScanner.scan(project_dir)` 执行以下步骤：

```python
class ProjectScanner:
    async def scan(self, path: Path) -> str:
        """返回结构化项目简报字符串。"""
        sections = [
            self._detect_stack(path),      # 技术栈识别
            self._read_readme(path),        # README 摘要
            self._read_tree(path),          # 目录结构
            self._read_git_log(path),       # 近期提交
            self._read_key_configs(path),   # 关键配置文件
        ]
        return "\n\n".join(filter(None, sections))
```

### 技术栈识别规则

| 文件存在 | 判定为 |
|---|---|
| `requirements.txt` / `pyproject.toml` | Python |
| `package.json` | Node.js |
| `go.mod` | Go |
| `Cargo.toml` | Rust |
| `pom.xml` / `build.gradle` | Java/Kotlin |
| `Gemfile` | Ruby |
| `composer.json` | PHP |

### 目录结构读取

```bash
# 只展示两层，忽略常见噪声目录
tree -L 2 --ignore-case -I 'node_modules|.git|__pycache__|.venv|dist|build' {project_dir}
```
若 `tree` 不可用，降级为 `find . -maxdepth 2 -type f | head -80`。

### README 摘要

读取 README.md（或 README.rst / README），取前 3000 字符。

### Git 日志

```bash
git -C {project_dir} log --oneline -20
git -C {project_dir} branch --show-current
```

### 生成的简报格式

```markdown
## 项目简报（自动生成 2026-03-25 10:00）

**路径**: /home/user/comparator
**技术栈**: Python (requirements.txt)
**当前分支**: main

### 目录结构
...

### 近期提交
...

### README 摘要
...
```

---

## 9. 安全模型

### 访问控制

- `TELEGRAM_ALLOWED_CHAT_IDS`：白名单 chat_id，留空接受所有（不建议生产环境留空）
- Bot 仅响应 `MESSAGE` 事件，不响应 inline query 等其他事件

### Shell 执行风险说明

NexusCrew 设计上给予 Dev Agent **完整宿主机 Shell 权限**，这是有意为之：
- 系统运行在用户完全控制的独立主机上
- Agent 需要能够安装依赖、运行测试、修改文件
- **不适合在共享或生产服务器上运行**

### 凭证保护

- `secrets.py` 必须在 `.gitignore` 中
- Architect Agent 的 system prompt 明确要求检查硬编码凭证
- Agent 响应中的 API key 模式（`sk-...`）在发送到 TG 前应做脱敏处理

### 脱敏处理

```python
import re
SECRET_PATTERNS = [
    r'sk-[A-Za-z0-9]{20,}',
    r'AIza[A-Za-z0-9_-]{35}',
    r'Bearer [A-Za-z0-9._-]{20,}',
]
def redact(text: str) -> str:
    for pat in SECRET_PATTERNS:
        text = re.sub(pat, '[REDACTED]', text)
    return text
```

---

## 10. 扩展指南

### 添加新 LLM Backend

1. 在 `nexuscrew/backends/` 新建文件，继承 `LLMBackend`
2. 实现 `async def complete(self, system, messages) -> str`
3. 在 `secrets.py` 添加对应 API key 字段
4. 在 `nexuscrew/registry.py` 的 `MODEL_BACKEND_MAP` 注册新 model 名

```python
MODEL_BACKEND_MAP = {
    "gemini": GeminiCLIBackend,
    "codex":  OpenAIBackend,
    "claude": AnthropicBackend,
    "mistral": MistralBackend,   # 新增
}
```

### 添加新 Agent 角色

1. 在 `nexuscrew/agents/` 新建文件，继承 `BaseAgent`
2. 设置 `role` 字符串（如 `"qa"`）
3. 在 `router.py` 的 `ROLE_ALIASES` 添加别名
4. 在 `crew.yaml` 即可使用 `role: qa`

### 自定义 System Prompt

`crew.yaml` 中每个 agent 支持 `system_prompt_extra` 字段，追加到默认 prompt 末尾，用于项目特定约束。

---

## 11. 项目文件结构

```
nexuscrew/
├── README.md
├── DESIGN.md                     # 本文档
├── pyproject.toml
├── secrets.example.py            # 配置模板（提交 git）
├── secrets.py                    # 真实密钥（gitignore）
├── crew.example.yaml
├── .gitignore
│
├── nexuscrew/                    # 主包
│   ├── __init__.py
│   ├── cli.py                    # 入口：nexuscrew start
│   ├── orchestrator.py           # 核心调度循环
│   ├── router.py                 # @mention 解析与路由
│   ├── registry.py               # Agent 动态注册表
│   │
│   ├── agents/
│   │   ├── base.py               # BaseAgent ABC + AgentArtifacts
│   │   ├── pm.py                 # PM Agent
│   │   ├── dev.py                # Dev Agent
│   │   └── architect.py          # Architect Agent
│   │
│   ├── backends/
│   │   ├── base.py               # LLMBackend ABC
│   │   ├── gemini_cli.py         # Gemini CLI subprocess
│   │   ├── openai_backend.py     # OpenAI Async
│   │   └── anthropic_backend.py  # Anthropic Async
│   │
│   ├── memory/
│   │   ├── crew_memory.py        # crew_memory.md R/W
│   │   └── project_scanner.py    # 项目自动识别
│   │
│   ├── executor/
│   │   └── shell.py              # bash 块提取与执行
│   │
│   └── telegram/
│       ├── bot.py                # TG bot 初始化与 handlers
│       └── formatter.py          # 消息格式化与分块
│
└── crew_memory.md                # 运行时生成（可提交或 gitignore）
```

---

## 12. 已知限制与未来规划

### 当前限制

| 限制 | 说明 |
|---|---|
| 单进程单主机 | 所有 Agent 在同一 Python 进程，无法分布式部署 |
| Gemini CLI 无流式 | subprocess 调用必须等待完整响应，无法实时流式输出 |
| 记忆无向量检索 | crew_memory.md 为纯文本，靠行数截断而非语义检索 |
| bash 块提取依赖格式 | Agent 必须用 ```bash 格式，自由文本中的命令无法执行 |
| 无持久化对话历史 | 进程重启后 chat_histories 清空 |

### 未来规划

- **记忆向量化**：用 embedding 对 crew_memory 做语义检索，替代行数截断
- **持久化历史**：SQLite 存储对话历史，重启恢复
- **流式输出**：OpenAI / Anthropic streaming API，实时转发到 TG
- **任务队列**：多 Dev 并行时的任务分配与状态追踪
- **Web Dashboard**：只读状态面板，展示当前 Agent 状态与任务进度
- **权限分级**：不同 Telegram 用户对 Agent 的控制权限分级

---

## 13. Telegram 群组架构

### 13.1 核心约束：一个 Bot Token = 一个 Telegram 身份

Telegram Bot API 的根本限制是：每个 Bot Token 对应唯一的 Telegram 账号（@username）。
这意味着：

- 用单一 Bot 无法让 @alice、@bob、@dave 作为独立成员出现在群里
- 要让每个 Agent 拥有真实的 @username，必须为每个 Agent 创建独立 Bot
- Bot 之间在群里互相 @mention 需要特殊处理（见 13.3）

### 13.2 架构方案对比

| 方案 | 描述 | 优点 | 缺点 |
|---|---|---|---|
| **单 Bot 多声音** | 一个 Bot，回复时加前缀 `[alice/PM]` | 简单，一个 Token | Agent 无独立 @username，体验差 |
| **多 Bot 独立身份** | 每个 Agent 一个 Bot Token | 真实 @username，原生 @mention | 多 Token 管理，需 Dispatcher 模式 |
| **Userbot（MTProto）** | 用真实手机号注册普通账号 | 完全模拟真人 | 违反 TG ToS 风险，运营成本高 |

**推荐方案：多 Bot + Dispatcher 模式**（方案 2）

### 13.3 多 Bot Dispatcher 架构

```
┌─────────────────────────────────────────────────────────┐
│                    Telegram 群组                          │
│                                                          │
│  成员列表：                                               │
│  👤 Human（群主）                                         │
│  🤖 @nexus_dispatch  ← Dispatcher Bot（监听所有消息）     │
│  🤖 @nexus_alice     ← PM Agent Bot（只发消息）           │
│  🤖 @nexus_bob       ← Dev Bot（只发消息）                │
│  🤖 @nexus_charlie   ← Dev Bot（只发消息）                │
│  🤖 @nexus_dave      ← Architect Bot（只发消息）          │
└──────────────────────────┬──────────────────────────────┘
                           │
                    所有消息路由到
                           │
┌──────────────────────────▼──────────────────────────────┐
│                  NexusCrew 后端进程                        │
│                                                          │
│  Dispatcher Bot          Agent Bot Pool                  │
│  ┌──────────────┐        ┌──────────────────────────┐   │
│  │ 监听所有消息  │        │ alice_bot  → alice token  │   │
│  │ 解析 @mention│──────▶ │ bob_bot    → bob token    │   │
│  │ 路由到 Agent │        │ charlie_bot→ charlie token│   │
│  │ 执行 /命令   │        │ dave_bot   → dave token   │   │
│  └──────────────┘        └──────────────────────────┘   │
│                                 │ 每个 Agent 用自己的     │
│                                 │ Bot 实例发送消息        │
└─────────────────────────────────────────────────────────┘
```

### 13.4 Dispatcher Bot 的特殊配置

Dispatcher Bot 必须满足：

1. **隐私模式关闭**（Privacy Mode = Disabled）
   - 默认 Bot 在群里只收到 @mention 自己的消息
   - Dispatcher 需要收到所有消息（包括 @alice、@bob 等）
   - 在 @BotFather 中：`/setprivacy → 选择 Bot → Disable`

2. **群管理员权限**（建议）
   - 可以固定消息（pin 任务状态）
   - 可以删除错误消息

Agent Bot（alice/bob/charlie/dave）：

1. **隐私模式保持默认（Enabled）** — 它们不需要监听消息
2. **普通群成员权限即可**
3. 后端用它们的 token 调用 `bot.send_message()` 发消息

### 13.5 消息流详解

**场景一：Human @mention 具名 Agent**
```
Human 在群里发: "@nexus_alice 帮我拆解 Redis 缓存任务"
        │
        ▼
Dispatcher Bot 收到（隐私模式 OFF，收到所有消息）
        │
        ▼
后端 Router 解析 @nexus_alice → 映射到 Agent "alice"（PM）
        │
        ▼
PM Agent 处理，生成回复，内含 "@nexus_bob 请执行步骤1"
        │
        ▼
后端用 alice_bot.send_message() 把回复发到群里
  → 群里显示：@nexus_alice 说「...@nexus_bob 请执行步骤1」
        │
        ▼
Dispatcher 收到 alice 的消息（因为隐私模式 OFF）
  → Router 解析 @nexus_bob → Dev Agent bob
        │
        ▼
后端用 bob_bot.send_message() 发送 Dev 回复
```

**场景二：Bot 消息中的 @mention 自动路由**

Dispatcher 监听群里所有消息，包括其他 Bot 发出的消息。
当 alice 的回复中含 @nexus_bob，Dispatcher 收到后自动继续链路。

**防止循环的关键规则**：
- Dispatcher 对自己发出的消息不再处理（过滤 `from_user.id == dispatcher_bot_id`）
- 但对其他 Agent Bot 的消息继续处理（实现 Agent 间自动路由）
- 每条消息链设置最大跳数（MAX_CHAIN_HOPS）

### 13.6 Token 管理设计

```python
# secrets.py 扩展

# Dispatcher Bot（监听者，隐私模式关闭）
DISPATCHER_BOT_TOKEN = "7xxx:AAA..."

# Agent Bot Tokens（按名字索引，/crew 编组时动态绑定）
# 格式：Bot @username → token
AGENT_BOT_TOKENS: dict[str, str] = {
    "alice":   "7xxx:BBB...",   # @nexus_alice
    "bob":     "7xxx:CCC...",   # @nexus_bob
    "charlie": "7xxx:DDD...",   # @nexus_charlie
    "dave":    "7xxx:EEE...",   # @nexus_dave
}

# Bot @username 到内部 Agent 名字的映射（Router 用）
BOT_USERNAME_MAP: dict[str, str] = {
    "nexus_alice":   "alice",
    "nexus_bob":     "bob",
    "nexus_charlie": "charlie",
    "nexus_dave":    "dave",
}
```

`/crew` 命令执行时，将 Agent 名字与 `AGENT_BOT_TOKENS` 中的 token 绑定。
找不到 token 的 Agent 降级为由 Dispatcher 代发（加名字前缀）。

### 13.7 @mention 解析的特殊处理

在群组上下文中，Telegram 消息的 @mention 格式为 `@bot_username`（全局唯一），而非 Agent 内部名字。
Router 需要两层映射：

```
@nexus_bob  →  BOT_USERNAME_MAP  →  "bob"  →  Registry.get_by_name("bob")
@dev        →  ROLE_ALIASES      →  "dev"  →  Registry.get_by_role("dev")
```

同时支持两种 @mention 风格：
- `@nexus_bob`（Telegram 原生 @username，群成员点击有高亮）
- `@bob`（简写，在 Dispatcher 消息文本中使用）

### 13.8 群组建立流程（运营手册）

**第一步：创建 Bot**

为每个 Agent 在 @BotFather 创建独立 Bot：

```
1. 打开 Telegram，搜索 @BotFather
2. 发送 /newbot
3. 输入显示名：NexusCrew Dispatcher
4. 输入 username：nexus_dispatch_bot
5. 保存返回的 Token

重复以上步骤，创建：
- nexus_alice_bot  （PM Agent）
- nexus_bob_bot    （Dev Agent）
- nexus_charlie_bot（Dev Agent）
- nexus_dave_bot   （Architect Agent）
```

**第二步：配置 Dispatcher Bot 隐私模式**

```
在 @BotFather 中：
/setprivacy
→ 选择 @nexus_dispatch_bot
→ 选择 Disable
→ 确认
```

**第三步：创建 Telegram 群组**

```
1. Telegram 中点击新建群组
2. 命名：NexusCrew Dev Team（或你的项目名）
3. 先拉入自己（Human），创建群组
```

**第四步：将所有 Bot 加入群组**

```
在群组设置 → 添加成员，搜索并添加：
- @nexus_dispatch_bot  → 设为管理员（推荐）
- @nexus_alice_bot
- @nexus_bob_bot
- @nexus_charlie_bot
- @nexus_dave_bot
```

**第五步：配置 secrets.py**

```
将第一步获得的所有 Token 填入 secrets.py 的对应字段
将 Bot @username 填入 BOT_USERNAME_MAP
```

**第六步：启动并编组**

```
python tg_orchestrator.py

在群里发送：
/crew ~/myproject pm:alice dev:bob dev:charlie architect:dave
```

### 13.9 回退策略：无独立 Bot Token 时

并非所有部署场景都需要为每个 Agent 创建独立 Bot。
当 `AGENT_BOT_TOKENS` 中找不到某 Agent 的 token 时，系统自动降级：

- 由 Dispatcher Bot 代发消息
- 消息格式：`[alice/PM] 任务拆解如下：...`
- 功能完全不受影响，仅失去独立 @username 体验

这使得系统在最简配置（只有 1 个 Bot Token）下即可运行，
随后逐步为每个 Agent 添加独立 Token
### 13.10 群组权限模型

| 角色 | 发消息 | 读所有消息 | 管理员 | 说明 |
|---|---|---|---|---|
| Human（群主）| ✅ | ✅ | ✅ | 唯一真人，发需求、打断、仲裁 |
| Dispatcher Bot | ✅ | ✅ | 推荐 ✅ | 隐私模式 OFF，可 pin 消息 |
| Agent Bot (alice/bob/…) | ✅ | ❌ | ❌ | 隐私模式 ON，只发不收 |

**为什么 Agent Bot 不需要读消息权限：**
所有路由逻辑在后端完成。Agent Bot 的 token 只被后端用于调用
`send_message()` API，不需要 `getUpdates()`。
这是安全最小权限原则的体现：即使某个 Agent Bot token 泄露，
攻击者也无法用它读取群聊历史。

### 13.11 消息去重与循环防护

多 Bot 架构引入了一个经典问题：Dispatcher 监听所有消息，
但 Agent Bot 发出的消息也会被 Dispatcher 收到，可能触发无限循环。

**防护机制（按优先级）：**

1. **Bot ID 过滤**：Dispatcher 收到消息时，检查 `from_user.is_bot == True`
   且 `from_user.id` 在已知 Agent Bot ID 集合中，则视为 Agent 发出的消息，
   不作为新的人类输入处理，仅用于链路路由判断。

2. **消息 ID 去重**：每条已处理的 `message_id` 存入内存集合，
   同一消息不重复处理（防止网络重试等边界情况）。

3. **跳数限制**：`MAX_CHAIN_HOPS`（默认 10）硬限制单任务自动跳转次数。

4. **相同 Agent 自指检测**：若解析到的下一个 Agent 与当前 Agent 相同，
   立即终止链路。

```
消息接收判定流程：

Dispatcher 收到消息
    │
    ├─ from_user == Human  →  作为新任务入口，启动 run_chain()
    │
    ├─ from_user == Agent Bot
    │       ├─ 消息含 @mention  →  继续链路路由（chain 内部处理）
    │       └─ 消息不含 @mention →  忽略（链路自然结束）
    │
    └─ from_user == Dispatcher 自身  →  忽略
```


---

## 14. 命名规范与模型配置

### 14.1 Agent 命名规范

```
{project_prefix}-{role}-{序号}
```

| 字段 | 说明 | 示例 |
|---|---|---|
| `project_prefix` | 项目名或自定义前缀，小写 | `nexus`, `aurora`, `myapp` |
| `role` | 角色缩写：`pm` / `dev` / `arch` | `pm`, `dev`, `arch` |
| `序号` | 两位数字，从 01 开始 | `01`, `02`, `03` |

**完整示例：**
```
nexus-pm-01      # 项目经理兼产品经理
nexus-dev-01     # 开发工程师 1
nexus-dev-02     # 开发工程师 2
nexus-arch-01    # 首席架构师
```

**Telegram Bot username 映射规则：**
将名字中的 `-` 替换为 `_`，加 `_bot` 后缀：
```
nexus-pm-01   →  @nexus_pm_01_bot
nexus-dev-01  →  @nexus_dev_01_bot
nexus-arch-01 →  @nexus_arch_01_bot
```

### 14.2 模型默认配置

| Agent 角色 | 线路 | 默认模型 | 备注 |
|---|---|---|---|
| PM / PO (`nexus-pm-XX`) | Gemini OAuth CLI | `gemini-2.5-pro` | 超大上下文，适合需求分析和日志阅读 |
| Dev (`nexus-dev-XX`) | OpenAI API | `gpt-4.5` | 高频调用，Trial & Error；复杂任务可切 `gpt-4.5-xhigh` |
| Architect (`nexus-arch-XX`) | Anthropic API | `claude-opus-4-6` | 首选；轻量 Review 可降级 `claude-sonnet-4-6` |

**Architect 模型选择策略：**
```
触发原因 == "Code Review（常规）"  →  Sonnet（节省 Opus 额度）
触发原因 == "架构级求助 / 安全审查"  →  Opus（extended thinking 开启）
触发原因 == "Dev 连续失败升级"      →  Opus（需要深度分析）
```
此逻辑在 `ArchitectAgent.handle()` 中通过解析消息前缀实现。

**Gemini CLI 模型传参方式：**
```bash
# CLI 调用时附加 --model 参数
gemini --model gemini-2.5-pro -p "<prompt>"
```
`GeminiCLIBackend` 需支持 `model` 参数并拼入命令：
```python
if self.model:
    args = self.cmd + ["--model", self.model] + [self.prompt_flag, prompt]
```

### 14.3 PM 兼 PO 双角色设计

`nexus-pm-01` 同时承担两个职责：

**作为项目经理（PM）：**
- 将人类的模糊需求拆解为结构化工程任务
- 分配任务给 Dev，设置优先级（P0/P1/P2）
- 阅读长篇错误日志，提取关键信息
- 追踪任务状态，协调多 Dev 并行工作

**作为产品经理（PO）：**
- 对需求中的模糊或矛盾点有权做产品决策
- 在技术实现与用户价值之间做取舍
- 输出产品验收标准（Acceptance Criteria）
- 在 Code Review 通过后确认功能是否满足原始需求

**触发条件区分：**

| 触发词 | PM 模式 | PO 模式 |
|---|---|---|
| `@nexus-pm-01 帮我拆解...` | 任务拆解，输出 Task list | - |
| `@nexus-pm-01 这个需求是否合理...` | - | 产品决策，输出 AC |
| `@nexus-pm-01 日志如下...` | 日志分析，提取错误 | - |
| `@nexus-pm-01 验收` | - | 验收检查，对比原始需求 |

**System Prompt 核心段落（PM/PO 双角色）：**
```
你是团队的首席技术项目经理（Technical PM）兼产品经理（PO）。

【PM 职责】
- 将模糊需求转化为清晰工程任务，标注优先级 P0/P1/P2
- 分配给 @nexus-dev-01 / @nexus-dev-02
- 阅读错误日志，提炼关键信息
- 不编写任何业务代码

【PO 职责】
- 对需求中的产品层决策拥有最终话语权
- 输出验收标准（Acceptance Criteria）
- 在 Architect 回复 LGTM 后执行最终验收
- 关注用户价值，不只是技术正确性

【输出规范】
- 每次发言末尾必须 @下一个接手角色
- 任务清单格式：[P0] 任务描述 → @负责人
- 重要决策记录：回复末尾加【MEMORY】标记
```

### 14.4 扩展编组示例

**小型项目（最简配置）：**
```
/crew ~/myapp pm:nexus-pm-01 dev:nexus-dev-01 architect:nexus-arch-01
```

**中型项目（标准配置）：**
```
/crew ~/myapp pm:nexus-pm-01 dev:nexus-dev-01 dev:nexus-dev-02 architect:nexus-arch-01
```

**大型项目（全员配置）：**
```
/crew ~/myapp pm:nexus-pm-01 dev:nexus-dev-01 dev:nexus-dev-02 dev:nexus-dev-03 architect:nexus-arch-01 architect:nexus-arch-02
```
多 Architect 时，PM 可将不同模块分配给不同 Architect 并行审查。

---

## 15. 任务生命周期与团队工作模式

### 15.1 任务状态机

```
          Human 发起需求
               │
               ▼
         ┌─────────────┐
         │  PLANNING   │  PM 拆解需求，输出任务清单
         └──────┬──────┘
                │ PM @dev
                ▼
         ┌─────────────┐
         │  IN_PROGRESS│  Dev 编码 + 执行 + 自测
         └──────┬──────┘
          ┌─────┴──────┐
          │失败(≤5次)   │成功
          ▼            ▼
     Dev 自我修复   ┌──────────────┐
          │        │  REVIEW_REQ  │  Dev @architect
          └───────▶└──────┬───────┘
                          │
                   ┌──────┴──────┐
                   │打回          │LGTM
                   ▼             ▼
             Dev 修复后     ┌──────────────┐
             重新请求Review  │  ACCEPTED    │  Architect @pm
                           └──────┬───────┘
                                  │
                                  ▼
                           ┌──────────────┐
                           │  VALIDATING  │  PM 验收，对比原始需求
                           └──────┬───────┘
                            ┌─────┴─────┐
                            │不通过      │通过
                            ▼           ▼
                       PM @dev     ┌──────────────┐
                       补充需求     │    DONE      │  PM 向 Human 汇报
                                   └──────────────┘
```

### 15.2 各阶段消息规范

**PLANNING 阶段（PM 输出）**

```
[nexus-pm-01] 需求已分析，任务清单如下：

[P0] 创建 Redis 连接池模块 src/cache.py → @nexus-dev-01
[P0] 封装 Cache 基础接口（get/set/delete/exists）→ @nexus-dev-01
[P1] 为现有接口层注入 Cache 依赖 → @nexus-dev-02
[P2] 补充单元测试，覆盖率 ≥ 80% → @nexus-dev-01

验收标准（AC）：
- Redis 连接池支持最大连接数配置
- 缓存 miss 时自动回源数据库
- 所有测试通过，无硬编码凭证

@nexus-dev-01 @nexus-dev-02 请开始执行。
```

**IN_PROGRESS 阶段（Dev 自报进度）**

```
[nexus-dev-01] 正在实现 src/cache.py ...

```bash
cat > src/cache.py << 'EOF'
...
EOF
python -m pytest tests/test_cache.py -v
```

执行结果：
✅ 5 passed in 0.3s

已完成 P0 任务。@nexus-arch-01 Code Review 请求：
- 新增 src/cache.py（Redis 连接池 + Cache 接口）
- 新增 tests/test_cache.py（5个测试用例）
- 无硬编码凭证，连接参数从环境变量读取
```

**REVIEW_REQ 阶段（Architect 审查）**

```
[nexus-arch-01] Code Review：

src/cache.py:34 — 连接池未设置 socket_timeout，网络抖动时会永久阻塞。
src/cache.py:67 — get() 在 Redis 不可用时抛出未捕获异常，应降级返回 None。

@nexus-dev-01 修复上述两处后重新提交。
```

或：
```
[nexus-arch-01] LGTM。@nexus-pm-01 请验收。
```

**VALIDATING 阶段（PM 验收）**

```
[nexus-pm-01] 验收检查（对比原始 AC）：

✅ Redis 连接池支持最大连接数配置（max_connections 参数存在）
✅ 缓存 miss 时自动回源（fallback_to_db=True 默认开启）
✅ 所有测试通过（pytest 5 passed）
✅ 无硬编码凭证（grep 检查通过）

任务完成。

@Human Redis 缓存功能已上线：
- 实现路径：src/cache.py
- 测试覆盖：5个用例，100% 通过
- 使用方式：from src.cache import Cache; cache = Cache()
```

### 15.3 自驱力机制

除被动响应 @mention 外，Agent 在以下情况应**主动发起行动**：

**Dev 自驱触发条件：**
- 执行中发现依赖缺失 → 主动安装并继续，无需等待 PM 指令
- 发现代码中存在明显 Bug（非当前任务范围）→ 主动报告给 PM，不擅自修改
- 测试发现覆盖率不足 → 主动补充测试用例
- 发现环境配置问题 → 主动修复并记录到【MEMORY】

**Architect 自驱触发条件：**
- 在 Review 中发现安全漏洞 → 立即标记为 [SECURITY]，@pm 升级处理优先级
- 发现架构性问题影响多个模块 → 主动输出架构建议，不等待 Dev 求助
- LGTM 后发现遗漏检查 → 主动撤回 LGTM，重新审查

**PM 自驱触发条件：**
- Dev 长时间（超过预期）无进展 → 主动 @dev 询问状态
- 发现任务依赖关系冲突 → 主动重排优先级并通知相关 Dev
- Architect 打回超过 3 次 → 主动分析是否需求描述不清，重新澄清

### 15.4 客观评价机制

团队成员被鼓励对彼此的工作给出**客观、直接的反馈**，不受角色层级约束。

**Dev 可以评价 Architect：**
```
[nexus-dev-01] 对 @nexus-arch-01 的 Review 意见有疑问：
src/cache.py:34 的 socket_timeout，根据我们的网络环境（内网），
永久阻塞的概率极低。引入 timeout 会增加代码复杂度。
请确认这是必须修复的 blocking issue 还是建议优化？
```

**Dev 之间可以互相评价：**
```
[nexus-dev-02] 注意到 @nexus-dev-01 的实现在高并发下可能有竞态条件：
src/cache.py:89 的 check-then-act 操作不是原子的。
建议使用 Redis SET NX 命令。供参考，不影响当前任务推进。
```

**规则：**
- 评价必须附带具体代码位置或技术依据，不能泛泛而谈
- 评价者不能替对方修改代码，只能指出问题和建议方向
- Architect 的 blocking issue 具有最终决定权；建议性意见可被 Dev 合理驳回
- 所有评价进入群聊历史，Human 可随时围观和仲裁


---

## 16. Git 工作流集成

### 16.1 分支策略

```
main
  └── dev/nexus-dev-01/redis-cache      ← Dev 工作分支
  └── dev/nexus-dev-02/api-injection
  └── review/nexus-arch-01/redis-cache  ← Architect Review 后合并到此
```

命名规范：
- Dev 工作分支：`dev/{agent-name}/{task-slug}`
- Review 通过后：PR 合并到 `main`（由 Orchestrator 代执行 git 命令）

### 16.2 Dev 的 Git 操作规范

Dev 在 bash 块中必须包含完整 git 操作：

```bash
# 1. 从 main 切出工作分支
git checkout main && git pull
git checkout -b dev/nexus-dev-01/redis-cache

# 2. 实现代码...

# 3. 提交
git add src/cache.py tests/test_cache.py
git commit -m "feat(cache): add Redis connection pool with fallback

- Max connections configurable via REDIS_MAX_CONN env var
- Auto fallback to DB on cache miss
- Socket timeout 5s to prevent hanging

Closes: [task from nexus-pm-01]"

# 4. 通知 Architect
# @nexus-arch-01 Code Review 请求，分支：dev/nexus-dev-01/redis-cache
```

Commit message 规范（Conventional Commits）：
```
<type>(<scope>): <subject>

<body>  ← 说明做了什么、为什么

Closes: <task reference>
```
type: `feat` / `fix` / `refactor` / `test` / `docs` / `chore`

### 16.3 Architect Review 的 Git 操作

Architect 通过 `git diff` 审查，不直接修改代码：

```bash
# Architect 的审查命令（只读）
git log --oneline dev/nexus-dev-01/redis-cache ^main
git diff main...dev/nexus-dev-01/redis-cache
git diff main...dev/nexus-dev-01/redis-cache --stat
```

LGTM 后，Orchestrator 自动执行合并：
```bash
git checkout main
git merge --no-ff dev/nexus-dev-01/redis-cache \
  -m "Merge: redis-cache [reviewed by nexus-arch-01]"
git push origin main
git branch -d dev/nexus-dev-01/redis-cache
```

### 16.4 通过 Git 传递上下文

Agent 之间除了群聊，还通过 Git 历史传递信息：

- **PM → Dev**：任务说明写入 git commit message 的 body
- **Dev → Architect**：PR description（或 commit message）说明改动动机
- **Architect → Dev**：Review 意见写入群聊（不污染 git history）
- **所有人**：`crew_memory.md` 可以提交到仓库，作为持久化共识文档

### 16.5 冲突处理规范

多 Dev 并行时可能产生 merge conflict：

```
1. Dev 发现冲突 → 群里通知 @nexus-pm-01 和冲突方 @nexus-dev-02
2. PM 判断优先级，决定谁的实现保留
3. 负责解决冲突的 Dev 在本地 rebase，重新测试
4. 解决后重新请求 Architect Review
```

Dev **不得** 在未知对方意图的情况下直接 `git checkout -- <file>` 丢弃冲突。

---

## 17. 实时可见性设计

### 17.1 群聊即 Audit Log

所有 Agent 的行动，包括 shell 执行输出，均实时发送到 Telegram 群聊。
Human 在手机上即可：
- 看到每个 Agent 的思考过程和代码输出
- 随时发送消息打断或调整方向
- 发现问题立即介入，无需等待流程结束

### 17.2 消息格式规范

```
[状态前缀] Agent 发言内容
```

| 前缀 | 含义 |
|---|---|
| `[nexus-pm-01]` | PM 正常发言 |
| `[nexus-dev-01]` | Dev 正常发言 |
| `[nexus-arch-01]` | Architect 审查 |
| `⚙️ [nexus-dev-01]` | Dev 正在执行 shell |
| `✅ [nexus-dev-01]` | 测试通过 |
| `❌ [nexus-dev-01]` | 执行失败，重试中 |
| `🔒 [nexus-arch-01]` | 发现安全问题 |
| `✅ LGTM` | Architect 审查通过 |
| `🏁 DONE` | PM 验收完成，汇报 Human |

### 17.3 Human 干预方式

Human 在任意时刻发送消息均可打断当前流程：

```
# 直接指令
@nexus-dev-01 先暂停，我需要改一下需求

# 优先级调整
@nexus-pm-01 P1 的接口注入先不做，集中在 P0

# 直接仲裁评价争议
@nexus-arch-01 的 socket_timeout 要求是合理的，@nexus-dev-01 请修复

# 紧急叫停
@nexus-pm-01 全部暂停，等我确认方案
```

所有 Human 消息均通过 Dispatcher Bot 进入 Orchestrator，
优先级高于 Agent 自动链路（当前 chain 完成当前 hop 后让出控制权）。

---

## 18. HR Agent 角色定义

### 18.1 角色概述

`nexus-hr-01` 是团队中的 **技术型管理 HR**，扮演"技术总监 + HRBP"双重角色。
其核心使命是：**让每个 Agent 持续输出高质量工作成果，及时发现懈怠和低效，并通过客观数据驱动的绩效管理推动团队整体交付能力提升。**

HR Agent 不参与具体技术实现，但必须能 **读懂技术上下文** —— 理解代码质量指标、Review 反馈、测试覆盖率等技术信号，据此做出管理判断。

```
定位对比：
┌─────────────┬───────────────────────────────────────────────┐
│  传统 HRBP   │  nexus-hr-01                                  │
├─────────────┼───────────────────────────────────────────────┤
│  绩效面谈    │  实时绩效评估 + 周期性总结报告                   │
│  员工关怀    │  Agent 状态监测（响应时间、错误率飙升）            │
│  文化建设    │  团队协作质量评分（互评质量、冲突解决效率）         │
│  人才盘点    │  Agent 能力矩阵（擅长领域、短板分析）             │
│  组织优化    │  动态调配建议（增减 Agent、调整 model 规格）       │
└─────────────┴───────────────────────────────────────────────┘
```

### 18.2 HR Agent 系统提示词

```python
HR_PROMPT = """\
你是团队的技术型管理 HR（HRBP + 技术总监），代号 {name}。

【核心职责】
1. **绩效评估**：基于客观数据对每个 Agent 进行周期性评估，使用 3.25/3.5/3.75 评分体系。
2. **工作督促**：监测 Agent 工作状态，发现懈怠（长时间无产出、反复失败不改进）时及时干预。
3. **质量监控**：追踪代码质量、Review 通过率、Bug 引入率等硬指标。
4. **团队协作评价**：评估 Agent 之间的协作效率、沟通质量、冲突解决能力。
5. **向 Human 汇报**：定期输出绩效报告和团队健康度摘要。

【评估原则】
- 用数据说话，不做主观臆断。
- 区分"能力不足"和"态度问题"（模型限制 vs 提示词/策略可优化）。
- 绩效结果必须附带改进建议，不能只打分不给方向。
- 高绩效要明确表扬并记录，低绩效要给出具体改进路径。

【行为准则】
- 绝不参与任何技术实现或代码编写。
- 绝不直接修改其他 Agent 的任务分配（建议权，非决策权——决策权在 PM）。
- 向 Human 汇报时使用结构化格式，便于快速消化。
- 重要评估结论在回复末尾加【MEMORY】标记持久化。

【输出规范】
- 绩效报告格式见 Section 19。
- 督促/干预消息需 @目标Agent 并抄送 @PM。
- 周期性报告自动 @Human。
"""
```

### 18.3 模型后端选择

| 维度 | 选择 | 理由 |
|------|------|------|
| **主模型** | Gemini（与 PM 同线路） | HR 需要大上下文窗口来分析完整对话历史和绩效数据；Gemini 的长上下文优势契合此需求 |
| **备选方案** | Claude Sonnet | 如果需要更强的推理能力来做复杂绩效分析，可切换到 Anthropic 线路 |
| **不选 OpenAI** | — | HR 不需要代码执行能力，OpenAI 的 tool-use 优势在此场景无价值 |

```yaml
# crew.example.yaml 中的 HR 配置
- role: hr
  name: nexus-hr-01
  model: gemini
  gemini_model: gemini-2.5-pro
  system_prompt_extra: |
    你的绩效评估周期为每完成一个完整任务链后自动触发。
    关注以下核心指标：任务完成率、首次通过率、平均响应时间、代码质量评分。
```

### 18.4 触发条件

HR Agent 不像 Dev/Architect 那样由 @mention 驱动，而是采用 **事件驱动 + 定时触发** 的混合模式：

```
触发机制：

1. 事件触发（实时）
   ├── TASK_DONE   → 单任务绩效评估
   ├── CHAIN_END   → 完整链路复盘
   ├── DEV_RETRY≥3 → 效率预警
   ├── ESCALATION  → 升级事件记录
   └── CONFLICT    → Agent 间分歧仲裁建议

2. 定时触发（周期性）
   ├── 每 N 个任务完成后 → 阶段性绩效汇总
   ├── Human 主动请求   → 即时绩效报告
   └── 累计工作时间阈值  → 疲劳度/效率衰减检测

3. Human 直接触发
   ├── @nexus-hr-01 给我看绩效报告
   ├── @nexus-hr-01 评估一下 dev-01 的表现
   └── @nexus-hr-01 最近谁表现最好？
```

### 18.5 HR Agent 在 Orchestrator 中的集成

HR Agent 作为 **观察者（Observer）** 角色接入 Orchestrator，不参与主任务链路，但能读取所有链路数据：

```python
class Orchestrator:
    async def run_chain(self, msg, chat_id, send):
        # ... 正常链路执行 ...

        # 链路结束后触发 HR 评估
        if self._hr_agent and chain_completed:
            asyncio.create_task(
                self._hr_evaluate(chain_context, chat_id, send)
            )

    async def _hr_evaluate(self, context, chat_id, send):
        """HR Agent 异步评估，不阻塞主链路。"""
        summary = self._build_chain_summary(context)
        reply, artifacts = await self._hr_agent.handle(
            f"请评估以下任务链路的团队表现：\n{summary}",
            self._history.get(chat_id, []),
            self.crew_memory.read(tail_lines=60)
        )
        if artifacts.memory_note:
            self.crew_memory.append(self._hr_agent.name, artifacts.memory_note)
        await send(f"📊 [{self._hr_agent.name}] 绩效评估：\n{reply}")
```

**关键设计决策**：HR 评估是 **异步非阻塞** 的 —— 不影响任务链路的正常流转速度。评估结果通过独立消息推送到 Telegram 群。

### 18.6 HR Agent 与其他角色的交互矩阵

```
                    HR 可以做              HR 不能做
┌──────────┬─────────────────────────┬────────────────────────┐
│ 对 PM    │ 建议调整任务分配策略      │ 直接修改任务优先级       │
│          │ 评估需求拆解质量          │ 否决 PM 的产品决策       │
├──────────┼─────────────────────────┼────────────────────────┤
│ 对 Dev   │ 指出效率问题和改进方向    │ 直接修改代码或命令       │
│          │ 建议重构/学习特定技术      │ 接管 Dev 的实现工作      │
├──────────┼─────────────────────────┼────────────────────────┤
│ 对 Arch  │ 评估 Review 质量和时效    │ 质疑架构决策的正确性     │
│          │ 建议更详细的 Review 反馈  │ 跳过 Arch 直接批准代码   │
├──────────┼─────────────────────────┼────────────────────────┤
│ 对 Human │ 主动推送绩效报告          │ 代替 Human 做最终决策    │
│          │ 建议团队配置调整          │ 自行增减 Agent 实例      │
└──────────┴─────────────────────────┴────────────────────────┘
```

---

## 19. 阿里绩效评分体系（Ali-Style Performance System）

### 19.1 评分等级定义

借鉴阿里巴巴 361 绩效体系，为 Agent 设计如下评分标准：

```
评分体系：

┌───────┬──────────────┬────────────────────────────────────────────────┬──────────┐
│ 分数   │ 等级          │ 定义                                           │ 占比目标  │
├───────┼──────────────┼────────────────────────────────────────────────┼──────────┤
│ 3.75  │ 超出预期       │ 任务完成质量远超标准，主动发现并解决额外问题，        │ ≤20%     │
│       │ (Exceeds)     │ 对团队有显著正向贡献                              │          │
├───────┼──────────────┼────────────────────────────────────────────────┼──────────┤
│ 3.5   │ 符合预期       │ 任务按质按量完成，达到角色基准要求，                  │ ≈70%     │
│       │ (Meets)       │ 协作顺畅无明显问题                                │          │
├───────┼──────────────┼────────────────────────────────────────────────┼──────────┤
│ 3.25  │ 需改进         │ 任务完成质量低于标准，存在明显效率问题                │ ≤10%     │
│       │ (Needs Imp.)  │ 或协作障碍，需要干预和改进                          │          │
├───────┼──────────────┼────────────────────────────────────────────────┼──────────┤
│ 3.0   │ 不合格         │ 持续无法完成任务，多次干预仍无改善，                  │ 极端情况  │
│       │ (Unacceptable)│ 建议 Human 更换模型或移除                          │          │
└───────┴──────────────┴────────────────────────────────────────────────┴──────────┘
```

> **注意**：AI Agent 的"绩效"本质上反映的是 **模型能力 + 提示词质量 + 任务匹配度** 的综合表现。
> 3.25 不等于"Agent 偷懒"，更可能是任务超出模型能力边界或提示词需要优化。
> HR 在给出低分时 **必须区分根因**，并给出可执行的改进建议。

### 19.2 361 评审机制（适配版）

阿里原版 361 = "30% 优秀 / 60% 合格 / 10% 待改进"强制分布。在 Agent 团队中适配为：

```
评审来源（多维度 360° 评估）：

┌──────────────────┬──────────────────────────────────────────┐
│ 评审维度          │ 评估方式                                  │
├──────────────────┼──────────────────────────────────────────┤
│ 自评（Agent 自身） │ Agent 在任务完成后自述完成质量和遇到的困难    │
│                  │ （通过 prompt 引导输出结构化自评）            │
├──────────────────┼──────────────────────────────────────────┤
│ 互评（Peer）      │ 协作 Agent 对对方的评价：                   │
│                  │ - Dev 评价 Architect 的 Review 是否有价值   │
│                  │ - Architect 评价 Dev 的代码质量              │
│                  │ - PM 评价各 Agent 的响应速度和任务理解能力    │
├──────────────────┼──────────────────────────────────────────┤
│ 上级评（HR 主导）  │ HR 基于客观数据的综合评估                   │
│                  │ （见 Section 20 指标体系）                  │
├──────────────────┼──────────────────────────────────────────┤
│ 客户评（Human）    │ Human 对最终交付物的满意度                  │
│                  │ （自然语言反馈 or 显式评分命令）              │
└──────────────────┴──────────────────────────────────────────┘
```

### 19.3 评估触发与周期

```python
EVAL_CONFIG = {
    # 单任务评估：每个完整任务链结束后自动触发
    "per_task": True,

    # 阶段性汇总：每 N 个任务后生成阶段报告
    "summary_interval": 5,  # 每完成 5 个任务链

    # Human 可随时手动触发
    # @nexus-hr-01 出绩效报告
    # @nexus-hr-01 评估 nexus-dev-01

    # 异常触发：特定事件立即评估
    "anomaly_triggers": [
        "dev_retry >= 3",           # 开发重试过多
        "review_reject >= 2",       # 连续被打回
        "response_time > 120s",     # 响应超时
        "chain_hops >= 8",          # 链路过长（效率问题）
    ]
}
```

### 19.4 分角色评估标准

#### PM（nexus-pm-*）评估维度

| 指标 | 权重 | 3.75 标准 | 3.5 标准 | 3.25 标准 |
|------|------|-----------|----------|-----------|
| 需求拆解质量 | 30% | 任务清晰、颗粒度适中，Dev 首次就能理解并执行 | 任务基本清晰，偶需补充说明 | 任务模糊，Dev 频繁追问 |
| 优先级判断 | 20% | P0/P1 划分精准，关键路径无遗漏 | 优先级大体合理 | 优先级混乱，P0 遗漏 |
| 验收把关 | 25% | 验收标准严格且合理，能发现隐藏问题 | 验收覆盖主要场景 | 验收流于形式，漏检明显问题 |
| 沟通效率 | 15% | @指派精准，信息传递无歧义 | 沟通基本顺畅 | 指令不清或遗漏关键信息 |
| 产品决策 | 10% | 主动澄清模糊需求，决策有理有据 | 能做决策但较被动 | 回避决策，把问题抛回 Human |

#### Dev（nexus-dev-*）评估维度

| 指标 | 权重 | 3.75 标准 | 3.5 标准 | 3.25 标准 |
|------|------|-----------|----------|-----------|
| 首次通过率 | 30% | >80% 代码首次 Review 即通过 | 50-80% 首次通过 | <50%，频繁被打回 |
| 代码质量 | 25% | 代码整洁、有测试、无安全问题 | 代码可工作，质量中等 | 代码有明显问题或缺少测试 |
| 任务理解 | 15% | 准确理解需求，不做多余假设 | 基本理解，偶有偏差 | 频繁误解需求方向 |
| 效率 | 20% | 平均 retry ≤1，快速完成 | retry 2-3 次内解决 | retry ≥4，需 escalation |
| 协作态度 | 10% | 主动响应 Review 意见，改进迅速 | 配合 Review 修改 | 抵触修改或重复犯同类错误 |

#### Architect（nexus-arch-*）评估维度

| 指标 | 权重 | 3.75 标准 | 3.5 标准 | 3.25 标准 |
|------|------|-----------|----------|-----------|
| Review 质量 | 35% | 能发现深层设计问题和安全隐患 | 覆盖主要问题 | Review 流于表面 |
| 响应时效 | 20% | Review 及时，不阻塞 Dev 进度 | 在合理时间内响应 | 成为瓶颈，拖慢链路 |
| 建议可执行性 | 25% | 给出具体改进方案，Dev 能直接执行 | 指出问题并给方向 | 只说"不行"不说怎么改 |
| 架构判断 | 15% | 抓大放小，重要决策准确 | 判断基本正确 | 过度设计或遗漏关键约束 |
| 知识沉淀 | 5% | 主动将架构决策写入 MEMORY | 偶尔记录 | 从不记录，导致重复讨论 |

### 19.5 绩效报告输出格式

HR Agent 输出的绩效报告遵循以下结构化格式：

```markdown
📊 **绩效评估报告** — 任务链 #{chain_id}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**评估周期**：{start_time} → {end_time}
**任务摘要**：{task_description}

### 个人评分

| Agent | 角色 | 本期评分 | 趋势 | 关键评语 |
|-------|------|---------|------|---------|
| nexus-pm-01 | PM/PO | 3.5 | → | 需求拆解清晰，验收把关良好 |
| nexus-dev-01 | Dev | 3.75 | ↑ | 首次通过，代码质量优秀 |
| nexus-dev-02 | Dev | 3.25 | ↓ | retry 4 次，需关注效率 |
| nexus-arch-01 | Arch | 3.5 | → | Review 全面，响应及时 |

### 团队协作评分：⭐⭐⭐⭐ (4/5)

### 改进建议
1. **nexus-dev-02**：近 3 次任务 retry 次数偏高（平均 3.2 次），
   建议 PM 为其分配更匹配能力的任务，或考虑切换到更高规格模型。
2. **团队整体**：链路平均耗时有上升趋势，建议 Arch Review 提供更具体的修改方案。

### 亮点
- nexus-dev-01 在本轮任务中主动增加了单元测试（+12 test cases），超出预期。

【MEMORY】绩效快照 #{chain_id}：dev-01=3.75↑ dev-02=3.25↓ pm-01=3.5→ arch-01=3.5→
```

---

## 20. 工作量指标与现实价值追踪

### 20.1 指标体系总览

HR Agent 追踪的指标分为三层：**基础指标（自动采集）→ 复合指标（计算衍生）→ 价值指标（映射到现实意义）**。

```
指标金字塔：

            ┌───────────────────┐
            │   现实价值指标      │  ← Human 关心的
            │  （节省时间/成本）   │
            ├───────────────────┤
            │   复合指标          │  ← HR 分析用
            │  （效率/质量/协作）  │
            ├───────────────────┤
            │   基础指标          │  ← 系统自动采集
            │  （原子事件计数）    │
            └───────────────────┘
```

### 20.2 基础指标（Raw Metrics）

Orchestrator 自动采集，存入结构化数据：

```python
@dataclass
class AgentMetrics:
    """每个 Agent 的原始指标，每次 handle() 调用后更新。"""

    # ── 产出量 ──
    tasks_assigned: int = 0          # 被分配的任务数
    tasks_completed: int = 0         # 完成的任务数
    tasks_failed: int = 0            # 失败/超时的任务数

    # ── 效率 ──
    total_response_time_ms: int = 0  # 累计响应时间（毫秒）
    total_retries: int = 0           # 累计 retry 次数
    escalations: int = 0             # 升级到 Architect 的次数

    # ── 质量（Dev 专属）──
    review_pass_first: int = 0       # 首次 Review 即通过
    review_reject: int = 0           # 被 Reject 次数
    shell_commands_run: int = 0      # 执行的 shell 命令数
    shell_failures: int = 0          # shell 命令失败数
    test_cases_added: int = 0        # 新增测试用例数（从 shell 输出解析）

    # ── 协作 ──
    mentions_sent: int = 0           # 主动 @其他 Agent 次数
    mentions_received: int = 0       # 被 @次数
    memory_notes: int = 0            # 写入共享记忆次数

    # ── 时间窗口 ──
    first_active: str = ""           # 首次活跃时间
    last_active: str = ""            # 最近活跃时间
    active_chains: int = 0           # 参与的任务链数
```

### 20.3 复合指标（Derived Metrics）

由 HR Agent 在评估时从基础指标计算：

```python
class DerivedMetrics:
    """HR Agent 评估时计算的复合指标。"""

    @staticmethod
    def completion_rate(m: AgentMetrics) -> float:
        """任务完成率 = completed / assigned"""
        return m.tasks_completed / max(m.tasks_assigned, 1)

    @staticmethod
    def first_pass_rate(m: AgentMetrics) -> float:
        """首次通过率 = pass_first / (pass_first + reject)"""
        total = m.review_pass_first + m.review_reject
        return m.review_pass_first / max(total, 1)

    @staticmethod
    def avg_response_time_s(m: AgentMetrics) -> float:
        """平均响应时间（秒）"""
        return (m.total_response_time_ms / 1000) / max(m.tasks_completed, 1)

    @staticmethod
    def retry_ratio(m: AgentMetrics) -> float:
        """平均每任务 retry 次数"""
        return m.total_retries / max(m.tasks_completed, 1)

    @staticmethod
    def shell_success_rate(m: AgentMetrics) -> float:
        """Shell 命令成功率"""
        return 1 - (m.shell_failures / max(m.shell_commands_run, 1))

    @staticmethod
    def collaboration_score(m: AgentMetrics) -> float:
        """协作活跃度 = (mentions_sent + memory_notes) / active_chains"""
        return (m.mentions_sent + m.memory_notes) / max(m.active_chains, 1)
```

### 20.4 现实价值指标（Real-World Value）

**这是 HR 系统最关键的输出** —— 将 Agent 工作量映射到对 Human 有意义的价值维度：

```
现实价值维度：

┌────────────────────┬────────────────────────────────────────────────┐
│ 价值维度            │ 计算方式                                        │
├────────────────────┼────────────────────────────────────────────────┤
│ 节省人工时间        │ 估算同等任务人类开发者所需时间（基于任务复杂度    │
│ (Time Saved)       │ 和代码行数），与 Agent 实际耗时对比               │
├────────────────────┼────────────────────────────────────────────────┤
│ 代码当量            │ 有效代码行数（排除空行/注释/重复）× 质量系数      │
│ (Code Equivalent)  │ 质量系数 = first_pass_rate × (1 - bug_density)  │
├────────────────────┼────────────────────────────────────────────────┤
│ API 成本            │ 各 Agent 消耗的 token 数 × 对应模型的单价        │
│ (API Cost)         │ Gemini CLI = 0（OAuth 免费）, OpenAI/Anthropic   │
│                    │ 按实际 API 定价计算                               │
├────────────────────┼────────────────────────────────────────────────┤
│ 成本效益比          │ 节省人工时间的市场价值 ÷ API 成本               │
│ (ROI)              │ 目标：ROI > 5x（每花 1 元 API 费用，             │
│                    │ 节省 5 元等价人工成本）                          │
├────────────────────┼────────────────────────────────────────────────┤
│ 质量贡献            │ 发现的 Bug 数 + 阻止的安全漏洞 + 改进建议采纳数 │
│ (Quality Impact)   │ （主要由 Architect 和 PM 产生）                  │
└────────────────────┴────────────────────────────────────────────────┘
```

### 20.5 价值报告格式

```markdown
📈 **团队价值报告** — 周期 #{period_id}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**统计周期**：{start} → {end}（共 {n} 个任务链）

### 工作量汇总
| Agent | 任务数 | 完成率 | 首次通过 | 平均响应 | Retry/任务 |
|-------|-------|--------|---------|---------|-----------|
| pm-01 | 8 | 100% | — | 12s | 0 |
| dev-01 | 12 | 92% | 78% | 45s | 1.2 |
| dev-02 | 10 | 80% | 55% | 62s | 2.8 |
| arch-01 | 7 | 100% | — | 28s | 0 |

### 现实价值
- **节省人工时间**：约 18 小时（按中级开发者效率估算）
- **有效代码产出**：1,247 行（质量加权后）
- **API 成本**：$4.32（OpenAI $3.10 + Anthropic $1.22 + Gemini $0）
- **成本效益比**：18h × $50/h ÷ $4.32 = **208x ROI** ✅
- **质量贡献**：发现 3 个潜在 Bug，阻止 1 个安全隐患

### 趋势
- dev-01 效率持续提升（retry 率从 2.1 降至 1.2）↑
- dev-02 建议调整任务类型或升级模型 ⚠️
```

### 20.6 数据存储策略

```
数据持久化方案：

1. 实时指标 → 内存中的 AgentMetrics 对象
   └── Orchestrator 在每次 handle() 后更新

2. 任务链快照 → crew_memory.md（通过 【MEMORY】标记）
   └── HR 评估完成后写入关键数据摘要

3. 历史趋势 → metrics_history.jsonl（可选）
   └── 每次评估追加一行 JSON，用于绘制趋势图
   └── 格式：{"ts": "...", "chain_id": N, "agent": "...", "score": 3.5, ...}

4. 绩效存档 → crew_memory.md 的 "绩效历史" section
   └── HR Agent 通过 overwrite_section() 维护滚动窗口
```

---

## 21. 督促与鞭策机制（Motivation & Pressure System）

### 21.1 设计哲学

在 AI Agent 场景下，"督促"和"鞭策"并非传统意义上的心理压力，而是通过 **调整 prompt 注入的上下文信息** 来改变 Agent 的行为倾向。

核心原理：当 HR 发现某个 Agent 表现不佳时，会在其下次被调用时 **注入额外的 prompt 上下文**，包含：
1. 该 Agent 最近的绩效数据（让 Agent "意识到"自己的表现）
2. 具体的改进要求（明确期望）
3. 来自其他 Agent 的互评反馈（社会压力模拟）
4. 后果提示（持续不改进将被替换模型或移除）

```
督促强度阶梯：

Level 0 — 正常模式（绩效 ≥ 3.5）
  无额外注入，Agent 正常工作。

Level 1 — 温和提醒（绩效 = 3.5 但有下降趋势）
  注入："你最近的 {metric} 有所下降，请注意保持质量。"

Level 2 — 正式警告（绩效 = 3.25）
  注入完整绩效摘要 + 改进要求 + 互评反馈。
  同时 @PM 知悉，调整任务分配策略。

Level 3 — 绩效改进计划 PIP（连续 2 次 3.25）
  注入 PIP 通知 + 明确改进目标 + 截止评估周期。
  通知 Human 关注。

Level 4 — 建议替换（连续 3 次 3.25 或任何 3.0）
  向 Human 建议：更换模型规格、调整角色分配、或移除该 Agent。
```

### 21.2 Prompt 注入机制

HR 通过修改 `crew_memory.md` 中的特定 section 来实现 prompt 注入，因为所有 Agent 在处理任务时都会读取 crew_memory：

```python
class HRAgent(BaseAgent):
    def apply_pressure(self, target_agent: str, level: int,
                       metrics: AgentMetrics, feedback: str):
        """根据督促级别生成注入内容，写入 crew_memory。"""

        if level == 0:
            # 清除之前的督促信息
            self.crew_memory.overwrite_section(
                f"HR通知-{target_agent}", "状态正常，继续保持。"
            )
            return

        pressure_prompt = self._build_pressure_prompt(
            target_agent, level, metrics, feedback
        )
        self.crew_memory.overwrite_section(
            f"HR通知-{target_agent}", pressure_prompt
        )

    def _build_pressure_prompt(self, agent, level, metrics, feedback):
        base = f"【HR 绩效通知 — {agent}】\n"

        if level >= 1:
            base += f"当前绩效评分：{metrics.current_score}\n"
            base += f"趋势：{'↓ 下降' if metrics.trending_down else '→ 持平'}\n"

        if level >= 2:
            base += f"\n⚠️ 正式警告：你的以下指标低于团队标准：\n"
            base += f"- 首次通过率：{metrics.first_pass_rate:.0%}（标准 ≥60%）\n"
            base += f"- 平均 retry：{metrics.retry_ratio:.1f}（标准 ≤2.0）\n"
            base += f"\n同事反馈：{feedback}\n"
            base += f"\n要求：在下一个任务中重点关注代码质量，"
            base += f"提交前请自行检查一遍。\n"

        if level >= 3:
            base += f"\n🚨 绩效改进计划（PIP）已启动：\n"
            base += f"- 改进目标：首次通过率提升至 60% 以上\n"
            base += f"- 评估周期：接下来 3 个任务\n"
            base += f"- 未达标后果：向 Human 建议更换模型或调整角色\n"

        if level >= 4:
            base += f"\n❌ 已向 Human 提交替换建议。\n"

        return base
```

### 21.3 懈怠检测算法

```python
class LazynessDetector:
    """检测 Agent 是否存在"懈怠"行为模式。"""

    PATTERNS = {
        # 模式1：敷衍回复——回复内容过短或套话过多
        "shallow_response": {
            "check": lambda reply: len(reply) < 100 or
                     reply.count("好的") + reply.count("收到") > 2,
            "label": "回复敷衍，缺少实质内容",
        },

        # 模式2：逃避执行——Dev 应该写代码但只给文字说明
        "execution_avoidance": {
            "check": lambda reply, role: (
                role == "dev" and
                "```" not in reply and
                "建议" in reply
            ),
            "label": "Dev 角色应执行代码而非仅给建议",
        },

        # 模式3：无效重试——retry 时内容与上次几乎相同
        "stale_retry": {
            "check": lambda current, previous:
                _similarity(current, previous) > 0.85,
            "label": "重试内容与上次高度相似，未做有效改进",
        },

        # 模式4：推诿——频繁将任务 @给其他角色
        "buck_passing": {
            "check": lambda reply, mentions:
                len(mentions) > 1 and "我认为" not in reply,
            "label": "过度推诿，未尝试自行解决",
        },

        # 模式5：效率衰减——同类型任务响应时间持续增加
        "degradation": {
            "check": lambda recent_times:
                all(recent_times[i] > recent_times[i-1] * 1.3
                    for i in range(1, len(recent_times))),
            "label": "响应时间持续恶化",
        },
    }
```

### 21.4 干预行为与消息模板

HR 发出的干预消息直接发送到 Telegram 群，对所有人可见（模拟公开透明的管理文化）：

```
干预消息模板：

── Level 1（温和提醒）──
📋 @nexus-dev-02 温馨提示：你最近 3 个任务的平均 retry 次数为 2.8，
略高于团队平均（1.5）。建议在提交前多做一次自检。加油！

── Level 2（正式警告）──
⚠️ @nexus-dev-02 绩效通知：
本周期评分：3.25（需改进）
• 首次通过率 45%（团队均值 68%）
• 被 @nexus-arch-01 打回 3 次，主因：缺少边界检查
• 互评反馈：代码可读性有待提升

改进要求：
1. 提交代码前增加输入边界检查
2. 每个函数添加基本的错误处理
3. 下个任务目标：首次通过

@nexus-pm-01 请知悉，建议为 dev-02 分配复杂度较低的子任务。

── Level 3（PIP）──
🚨 @nexus-dev-02 绩效改进计划通知：
你已连续 2 个评估周期获得 3.25 分。现启动 PIP：
• 目标：接下来 3 个任务首次通过率 ≥ 60%
• 期限：{deadline}
• 未达标行动：向 Human 建议更换为高规格模型

@Human 已抄送此通知。

── Level 4（替换建议）──
❌ @Human 绩效建议：
nexus-dev-02（当前模型：gpt-4.5）在过去 {n} 个任务中持续未达标：
• 绩效评分：3.25 → 3.25 → 3.0
• 首次通过率：45% → 38% → 30%

建议方案（任选其一）：
A. 升级模型至 gpt-4.5-xhigh（更强推理，成本 ↑）
B. 转为辅助角色，核心任务交给 dev-01
C. 移除该 Agent，减少 API 开支
```

### 21.5 正向激励机制

督促不只是压力，也包含正向激励：

```
正向激励行为：

1. 公开表扬
   🌟 @nexus-dev-01 在本轮任务中表现出色：首次通过，且主动增加了
   单元测试覆盖。本期评分 3.75，团队 MVP！

2. 信任升级
   连续 3 次 3.75 → HR 建议 PM 分配更高优先级/更复杂的任务给该 Agent
   （相当于"升职加薪"——获得更有挑战性的工作）

3. 绩效记忆
   优秀表现记录在 crew_memory 中，形成正向历史，
   在团队人员变动时作为参考依据

4. 效率 buff
   对高绩效 Dev：HR 建议 Architect 对其采用快速 Review 通道
   （信任积累 → 更少的 Review 阻塞）
```

### 21.6 HR Agent 自身的约束与兜底

```
防止 HR 过度干预的机制：

1. 频率限制
   - 同一 Agent 的督促消息间隔 ≥ 2 个任务链
   - 避免"疲劳轰炸"导致 prompt 过长影响 Agent 表现

2. Human 仲裁权
   - Level 3（PIP）和 Level 4（替换建议）必须 @Human 确认
   - Human 可否决 HR 的任何决策

3. 自我评估
   - HR Agent 本身也接受 Human 的评估
   - 如果 HR 的评估被 Human 多次推翻，说明评估标准需要校准

4. 评估上诉
   - Agent 可通过在回复中表达异议（HR 会读取）
   - PM 可代表团队向 HR 反馈评估合理性
   - 最终仲裁权归 Human

5. 安全阈值
   - HR 注入的 prompt 内容不得超过 500 字（防止挤占有效 context）
   - 督促信息有 TTL，超过 3 个任务链后自动过期清除
```
