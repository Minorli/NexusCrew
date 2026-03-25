# NexusCrew

多智能体 AI 开发团队，通过 Telegram 群聊协作完成编码、测试与 Code Review。

## 特性

- 异构模型：PM(Gemini) / Dev(Codex) / Architect(Claude)
- 动态编组：`/crew` 命令运行时创建任意数量、任意命名的 Agent
- 大型项目感知：自动扫描代码库生成项目简报
- 共享记忆：`crew_memory.md` 跨会话持久化决策
- ChatOps：全程 Telegram @mention 驱动

## 快速开始

```bash
pip install anthropic openai python-telegram-bot pyyaml
gemini auth login
cp secrets.example.py secrets.py && vim secrets.py
python tg_orchestrator.py
```

Telegram 中发送：
```
/crew ~/myproject pm:alice dev:bob dev:charlie architect:dave
@alice 给用户模块加 JWT 认证
```

## 编组格式

```
/crew <path> [role:name[(model)]] ...
```
role: `pm` | `dev` | `architect`，model 省略按角色默认。

## 命令

| 命令 | 说明 |
|---|---|
| `/crew <path> [agents]` | 编组并扫描项目 |
| `/status` | Agent 列表 |
| `/memory [n]` | 共享记忆末尾 n 行 |
| `/reset` | 清空对话历史 |

## 路由

`@名字` 精确路由，`@pm`/`@dev`/`@architect` 按角色轮询，无 mention 默认 PM。

## 共享记忆

```
【MEMORY】Redis 连接池在 src/cache.py:12，需要 REDIS_URL。
```
Agent 回复末尾加此标记自动写入 `crew_memory.md`。

## 安全

- `secrets.py` 已 gitignore，勿提交
- 仅在独立可控主机运行
- 生产环境配置 `TELEGRAM_ALLOWED_CHAT_IDS` 白名单

详见 [DESIGN.md](DESIGN.md)。

## License

MIT
