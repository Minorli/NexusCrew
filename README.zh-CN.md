# NexusCrew

NexusCrew 是一个以 Telegram 群组为主控制面的多 Agent 软件交付运行时。

它的目标不是“像聊天机器人一样回答问题”，而是“像一个真实的软件团队一样推进交付”：
- PM 拆需求、排优先级、做验收
- Dev 写代码、跑命令、补测试
- Architect 做审查和风险判断
- QA 做测试设计、质量闸门和发布前验证
- HR 做团队健康与绩效观察
- GitHub 留下长期可审计的工程记录

For English documentation, see [README.en.md](README.en.md).

## 项目概览

NexusCrew 把一个 Telegram 群组变成“可协作、可治理、可追踪”的软件交付控制台。

核心理念：
- 不是单助手，而是多角色团队协作
- Telegram 负责实时交互
- GitHub 负责长期留痕
- 后台任务可暂停、恢复、重放
- 运行时可恢复、可审计
- 风险动作可审批、可治理

## 核心功能

### 多 Agent 团队运行时

- 支持 PM / Dev / Architect / QA / HR 五类角色
- 支持从 YAML 或 Telegram 指令动态编组
- 支持多 Bot 独立身份发言
- 支持没有专属 Bot 时自动降级为单 Bot 模式
- 支持 `@角色别名`、`@agent 名称`、`@bot username` 路由

### 交付链路

- 任务状态机与任务看板
- 后台任务执行器
- Dev 工作自动创建 Git branch session
- Dev 交付会生成 git-first review packet
- Architect review 后进入 QA quality gate，再进入 PM acceptance gate
- Draft PR 生成
- CI 结果汇总
- Merge gate 支持

### 防阻塞 / 防卡死

- Agent 心跳 watchdog
- 长时间无回复会超时升级，不再静默挂住
- 对 Dev / Architect 的低质量回复自动二次追问
- 防止 PM / Arch / Dev 之间反复兜圈的循环路由保护
- 对没有活跃后台 run 的陈旧任务自动收口为失败
- grouped watchdog，避免 Telegram 被旧任务刷屏
- 状态类问题默认只由 PM 汇总，不再把全员拉起来轮流汇报

### 治理与审计

- 高风险 shell 动作审批
- append-only 事件日志
- checkpoint 持久化
- artifacts 归档
- SQLite 持久化状态层

### 协作表面

- Telegram：主控制面
- GitHub：issue / comment / PR / CI 留痕
- Slack：可选企业协作面
- Dashboard：只读状态快照

## 架构示意

```text
Telegram / Slack
    -> ChatOps 命令层
    -> Router + Orchestrator
    -> PM / Dev / Architect / QA / HR
    -> Shell / Git / Memory / Metrics / Trace
    -> GitHub / PR / CI / Artifacts / SQLite
```

## 安装

安装基础依赖：

```bash
pip install anthropic openai python-telegram-bot pyyaml
```

如果你后续确实要启用 Gemini CLI，再执行：

```bash
gemini auth login
```

### 配置

推荐直接走本地 Web 向导：

```bash
python3 -m nexuscrew setup
```

Setup 向导支持：
- 自动监听 `0.0.0.0`
- 打印本机和局域网地址
- 生成本地 `secrets.py`
- 生成本地 `crew.local.yaml`
- 配置多 Bot 与用户名映射
- 测试 Telegram / GitHub / Slack 连通性
- 保存并直接启动

如果你不想走 Web UI，也可以手工编辑：
- [`secrets.example.py`](secrets.example.py)
- [`crew.example.yaml`](crew.example.yaml)

## 启动

正常启动：

```bash
python3 -m nexuscrew
```

如果本地配置缺失，程序会自动进入 setup 向导，而不是直接崩掉。

如果要指定 crew 配置：

```bash
python3 -m nexuscrew start -c crew.example.yaml
```

## Telegram 使用方式

推荐启动后先在群里执行：

```text
/start
/load crew.local.yaml
@nexus-pm-01 规划一个小功能并安排开发
```

常用命令：

| 命令 | 说明 |
|---|---|
| `/crew <path> [agents]` | 从 inline spec 创建编组 |
| `/load <crew.yaml>` | 从 YAML 载入编组 |
| `/status` | 查看当前 Agent、任务板、活跃后台任务 |
| `/tasks` | 查看活跃后台任务 |
| `/failed` | 查看失败后台任务归档 |
| `/task <task_id>` | 查看单个任务详情 |
| `/pause <task_id>` | 暂停任务 |
| `/resume <task_id>` | 恢复任务 |
| `/replay <task_id>` | 重放任务 |
| `/approvals` | 查看待审批动作 |
| `/approve <id>` | 批准动作 |
| `/reject <id>` | 拒绝动作 |
| `/doctor` | 查看系统健康摘要 |
| `/trace <task_id>` | 查看任务时间线 |
| `/artifacts <task_id>` | 查看任务 artifacts |
| `/pr <task_id>` | 查看 PR 摘要 |
| `/ci <task_id>` | 查看 CI 摘要 |
| `/board` | 查看状态板 |
| `/skills` | 查看内置技能 |
| `/drill` | 跑一次内部协作演练 |

## 多 Bot 模式

NexusCrew 推荐使用：
- 一个 Dispatcher Bot 负责监听群消息
- 多个 Agent Bot 负责以不同身份发言

详细搭建过程见：
- [`TELEGRAM_SETUP.md`](TELEGRAM_SETUP.md)

## GitHub 留痕

开启 GitHub sync 后：
- 每个任务可以自动映射到 GitHub issue
- Human / Agent / Shell 摘要会镜像成 issue comments
- PR draft、CI 状态、merge gate 可以与任务上下文联动

这意味着：
- Telegram 负责“活的控制面”
- GitHub 负责“长期工程账本”

## 交互方式的设计原则

NexusCrew 当前已经明确收口成：
- 群里只看摘要，不看大段代码
- 原始 shell / 代码细节进入 artifacts 和 GitHub
- review / QA / acceptance 优先消费简洁的 git-first handoff，而不是原始终端输出
- 状态类问题由 PM 汇总
- 真正长时间无响应时才发心跳或升级
- 不接受“收到 / 在 / 正在看 / 稍后回复”这类假推进

如果你要的是“像顶尖开发团队一样”的体验，目标就是：
- TG 看决策、状态、阻塞、验收
- GitHub 看 issue、PR、review、长期记录
- artifacts 看细节

## 可靠性说明

当前版本已经包含：
- agent 心跳与超时升级
- 旧任务自动收口
- grouped watchdog，避免 Telegram 刷屏
- GitHub / PR / Slack 的网络重试与降级
- replay / resume / checkpoint

## 安全说明

- `secrets.py` 只留本地，不进 Git
- 高风险 shell 动作可审批
- 私有设计稿、运行态状态文件、内部提示词不进入公开仓库
- 推荐部署在可控的专用机器上

## 公开文档

- [`README.md`](README.md)
- [`README.en.md`](README.en.md)
- [`TELEGRAM_SETUP.md`](TELEGRAM_SETUP.md)
- [`crew.example.yaml`](crew.example.yaml)
- [`secrets.example.py`](secrets.example.py)

## License

MIT
