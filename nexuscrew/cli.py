"""CLI entry point: python -m nexuscrew  or  nexuscrew start"""
from .telegram.bot import NexusCrewBot


def main():
    import secrets as cfg
    if not cfg.TELEGRAM_BOT_TOKEN or cfg.TELEGRAM_BOT_TOKEN.startswith("YOUR_"):
        raise SystemExit("请先在 secrets.py 中填写 TELEGRAM_BOT_TOKEN")
    bot = NexusCrewBot()
    app = bot.build_app()
    print("NexusCrew 启动，监听 Telegram 消息...")
    app.run_polling()


if __name__ == "__main__":
    main()
