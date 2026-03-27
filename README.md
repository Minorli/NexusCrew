# NexusCrew

NexusCrew is a Telegram-native multi-agent software delivery runtime.  
NexusCrew 是一个以 Telegram 群组为主控制面的多 Agent 软件交付运行时。

## Docs / 文档

- English: [README.en.md](README.en.md)
- 中文: [README.zh-CN.md](README.zh-CN.md)
- Telegram setup: [TELEGRAM_SETUP.md](TELEGRAM_SETUP.md)
- Example crew config: [crew.example.yaml](crew.example.yaml)
- Example secrets template: [secrets.example.py](secrets.example.py)

## Quick Summary / 快速概览

NexusCrew turns a Telegram group into a visible engineering team:
- PM plans and decomposes work
- Dev agents implement and validate changes
- Architect reviews for correctness and risk
- HR tracks delivery quality and team health
- GitHub keeps the durable issue / PR / review record

NexusCrew 把 Telegram 群组变成一个可见的软件工程团队：
- PM 负责拆需求、排优先级、做验收
- Dev 负责写代码、跑命令、补测试
- Architect 负责审查和风险判断
- HR 负责团队健康和绩效观察
- GitHub 负责长期工程留痕

## Start / 启动

Recommended first-run setup:

```bash
python3 -m nexuscrew setup
```

Normal start:

```bash
python3 -m nexuscrew
```

Core Telegram commands:
- `/start`
- `/load crew.local.yaml`
- `/status`
- `/tasks`
- `/failed`
- `/doctor`
- `/drill`

## Design Intent / 设计目标

NexusCrew is optimized for:
- live team-style collaboration in Telegram
- durable engineering records in GitHub
- recoverable runtime state
- background execution without silent stalls
- concise, professional delivery communication instead of terminal spam

NexusCrew 强调：
- 在 Telegram 里像团队一样协作
- 在 GitHub 里留下长期工程记录
- 运行时可恢复、可审计
- 后台任务不静默卡死
- 群里只看摘要，不看大段终端垃圾输出

## License

MIT
