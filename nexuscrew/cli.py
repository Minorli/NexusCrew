"""CLI entry point: nexuscrew start [--config crew.yaml]."""
import argparse

from .config import load_crew_config
from .telegram.bot import NexusCrewBot


def main():
    import secrets as cfg

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

    args = parser.parse_args()
    if args.command is None:
        args.command = "start"
    if not hasattr(args, "config"):
        args.config = None

    if not cfg.TELEGRAM_BOT_TOKEN or cfg.TELEGRAM_BOT_TOKEN.startswith("YOUR_"):
        raise SystemExit("请先在 secrets.py 中填写 TELEGRAM_BOT_TOKEN")
    if args.command != "start":
        raise SystemExit(f"未知命令: {args.command}")

    bot = NexusCrewBot()
    if args.config:
        # Task 2.4 完成: CLI 支持预加载 crew.yaml 配置。
        bot.preload_config = load_crew_config(args.config)
    app = bot.build_app()
    print("NexusCrew 启动，监听 Telegram 消息...")
    app.run_polling()


if __name__ == "__main__":
    main()
