"""CLI entry point: nexuscrew start [--config crew.yaml]."""
import argparse
from pathlib import Path

from .config import load_crew_config
from .local_secrets import load_local_secrets
from .setup_wizard import SetupWizardServer
from .telegram.bot import NexusCrewBot


def main():
    parser = argparse.ArgumentParser(
        description="NexusCrew — Multi-Agent Dev Team",
    )
    sub = parser.add_subparsers(dest="command")

    start = sub.add_parser("start", help="启动 Telegram Bot")
    start.add_argument(
        "--config",
        "-c",
        type=str,
        default=None,
        help="crew.yaml 配置文件路径（可选，也可通过 /crew 或 /load 命令加载）",
    )
    setup = sub.add_parser("setup", help="启动首次配置 Web UI")
    setup.add_argument("--host", type=str, default="127.0.0.1")
    setup.add_argument("--port", type=int, default=8766)

    args = parser.parse_args()
    if args.command is None:
        args.command = "start"
    if not hasattr(args, "config"):
        args.config = None
    cfg = load_local_secrets()

    if args.command == "setup":
        wizard = SetupWizardServer(Path.cwd(), host=args.host, port=args.port)
        print(f"NexusCrew Setup Wizard 已启动: http://{args.host}:{args.port}/setup")
        wizard.serve_forever()
        return

    if args.command != "start":
        raise SystemExit(f"未知命令: {args.command}")

    if not cfg.TELEGRAM_BOT_TOKEN or cfg.TELEGRAM_BOT_TOKEN.startswith("YOUR_"):
        host = "127.0.0.1"
        port = 8766
        wizard = SetupWizardServer(Path.cwd(), host=host, port=port)
        print(f"未检测到有效 secrets.py，已启动配置向导: http://{host}:{port}/setup")
        wizard.serve_forever()
        return

    bot = NexusCrewBot()
    if args.config:
        # Task 2.4 完成: CLI 支持预加载 crew.yaml 配置。
        bot.preload_config = load_crew_config(args.config)
    else:
        default_config = Path("crew.local.yaml")
        if default_config.exists():
            bot.preload_config = load_crew_config(default_config)
    app = bot.build_app()
    print("NexusCrew 启动，监听 Telegram 消息...")
    app.run_polling()


if __name__ == "__main__":
    main()
