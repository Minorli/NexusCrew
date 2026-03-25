# NexusCrew — Telegram 群组搭建手册

> 本文档为运营手册，面向部署人员。架构原理详见 [DESIGN.md § 13](DESIGN.md#13-telegram-群组架构)。

---

## 概念速览

NexusCrew 使用 **多 Bot + Dispatcher** 模式：

- 每个 Agent（alice / bob / charlie / dave）是一个独立的 Telegram Bot，拥有自己的 @username
- 一个 **Dispatcher Bot** 负责监听群里所有消息并路由给后端
- 后端用对应 Agent 的 Bot token 把回复发回群里
- 结果：群里每条消息都有真实的发送者身份，原生支持 @mention

```
群成员列表示意：
  👤 你（Human，群主）
  🤖 @nexus_dispatch   ← 监听者，接收所有消息
  🤖 @nexus_alice      ← PM Agent
  🤖 @nexus_bob        ← Dev Agent
  🤖 @nexus_charlie    ← Dev Agent
  🤖 @nexus_dave       ← Architect Agent
```

---

## 第一阶段：创建所有 Bot

打开 Telegram，找到 **@BotFather**，按以下顺序创建 Bot。

### 1-A. Dispatcher Bot（核心监听者）

```
/newbot
名称（显示名）: NexusCrew
username:       nexus_dispatch_bot
```
保存返回的 Token，记为 `DISPATCHER_BOT_TOKEN`。

**立即关闭隐私模式（必须）：**
```
/setprivacy
→ 选择 @nexus_dispatch_bot
→ Disable
```
> 此步骤若跳过，Dispatcher 只能收到 @mention 自己的消息，无法监听全群。

### 1-B. Agent Bot（每个 Agent 一个）

按下表重复 `/newbot` 流程，名称和 username 可自定义：

| Agent 角色 | 建议显示名 | 建议 username | Token 变量名 |
|---|---|---|---|
| PM | NexusCrew PM | nexus_alice_bot | `alice` |
| Dev | NexusCrew Dev 1 | nexus_bob_bot | `bob` |
| Dev | NexusCrew Dev 2 | nexus_charlie_bot | `charlie` |
| Architect | NexusCrew Arch | nexus_dave_bot | `dave` |

Agent Bot **保持默认隐私模式（Enabled）**，无需额外配置。

---

## 第二阶段：创建群组并拉入 Bot

### 2-A. 创建群组

1. Telegram → 新建群组
2. 命名：`NexusCrew — [项目名]`
3. 初始只加入自己，完成创建

### 2-B. 添加 Bot 成员

进入群组 → 成员管理 → 添加成员，依次搜索并添加：

```
@nexus_dispatch_bot    ← 设为管理员（推荐授予「固定消息」权限）
@nexus_alice_bot
@nexus_bob_bot
@nexus_charlie_bot
@nexus_dave_bot
```

### 2-C. 获取群组 chat_id

将 Dispatcher Bot 加入群后，在群里发一条任意消息，然后访问：

```
https://api.telegram.org/bot<DISPATCHER_BOT_TOKEN>/getUpdates
```

在返回 JSON 的 `result[*].message.chat.id` 字段中找到群组 ID（负数，如 `-1001234567890`）。
将此 ID 加入 `secrets.py` 的 `TELEGRAM_ALLOWED_CHAT_IDS`。

---

## 第三阶段：填写 secrets.py

```python
# Telegram
TELEGRAM_BOT_TOKEN        = "<DISPATCHER_BOT_TOKEN>"   # Dispatcher
TELEGRAM_ALLOWED_CHAT_IDS = [-1001234567890]            # 群组 chat_id

# Agent Bot Tokens
AGENT_BOT_TOKENS = {
    "alice":   "<nexus_alice_bot token>",
    "bob":     "<nexus_bob_bot token>",
    "charlie": "<nexus_charlie_bot token>",
    "dave":    "<nexus_dave_bot token>",
}

# Bot username → Agent 名字映射（Router 用）
BOT_USERNAME_MAP = {
    "nexus_alice_bot":   "alice",
    "nexus_bob_bot":     "bob",
    "nexus_charlie_bot": "charlie",
    "nexus_dave_bot":    "dave",
}
```

---

## 第四阶段：启动与验证

```bash
python tg_orchestrator.py
```

在群里发送：

```
/start
```

应收到 Dispatcher Bot 的欢迎消息。然后编组：

```
/crew ~/myproject pm:alice dev:bob dev:charlie architect:dave
```

应收到编组成功的摘要，并在群里看到 `/status` 可用。

---

## 最简配置（单 Bot 模式）

如果只想快速验证功能，不需要独立 Agent @username，
只创建 1 个 Bot（Dispatcher），`AGENT_BOT_TOKENS` 留空：

```python
AGENT_BOT_TOKENS = {}   # 所有 Agent 由 Dispatcher 代发，加名字前缀
```

系统自动降级，功能完整，体验略差（Agent 没有独立身份）。

---

## 常见问题

**Q: Bot 加入群后不响应消息**
A: 检查 Dispatcher Bot 的隐私模式是否已关闭（`/setprivacy → Disable`）。
重新将 Bot 踢出并重新加入群组，使新的隐私模式设置生效。

**Q: `getUpdates` 返回空**
A: 若之前用过 Webhook，需先清除：`/deleteWebhook`。
Bot 同一时间只能用 polling 或 webhook 其中一种。

**Q: Agent Bot 能不能也收消息、有独立逻辑？**
A: 可以，但会极大增加架构复杂度（多进程协调、消息去重）。
当前架构选择让 Agent Bot 只负责「发言」，所有逻辑集中在后端，
这是复杂度与功能性的最优平衡点。

**Q: 群里能加多少个 Agent Bot？**
A: Telegram 群组支持最多 20 万成员，Bot 数量无额外限制。
实际限制来自后端并发处理能力和 API 速率限制（每秒 30 条消息）。

**Q: 如何让 Agent 发送代码块、图片等富媒体？**
A: `send_message()` 支持 `parse_mode="Markdown"` 或 `"HTML"`，
代码块用 `` ``` `` 包裹即可在群里正常渲染。
图片/文件用 `send_photo()` / `send_document()`，
Dev Agent 执行后可以把生成的图表文件路径传给 Telegram 发送。
