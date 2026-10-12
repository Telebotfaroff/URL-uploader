import asyncio
import os
import logging

from pyrogram import Client, StopPropagation, enums, filters, idle
from pyrogram.handlers import CallbackQueryHandler, MessageHandler

from plugins.config import Config, configure_upload_clients

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
logging.getLogger("asyncpg").setLevel(logging.WARNING)
logging.getLogger("aiosqlite").setLevel(logging.WARNING)
logging.getLogger("requests_cache").setLevel(logging.WARNING)


async def owner_only_message(_, message):
    """Block every incoming message from anyone except the configured owner."""
    if message.from_user and message.from_user.id == Config.OWNER_ID:
        return

    if message.from_user and message.chat.type == enums.ChatType.PRIVATE:
        try:
            await message.reply_text("🔒 This is a private bot. Only its owner can use it.")
        except Exception:
            pass

    raise StopPropagation


async def owner_only_callback(_, callback_query):
    """Prevent non-owners from using buttons, including old messages."""
    if callback_query.from_user and callback_query.from_user.id == Config.OWNER_ID:
        return

    try:
        await callback_query.answer(
            "This is a private bot. Only its owner can use it.",
            show_alert=True,
        )
    except Exception:
        pass

    raise StopPropagation


if __name__ == "__main__":
    if not Config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is required.")
    if Config.API_ID <= 0 or not Config.API_HASH:
        raise RuntimeError("Set valid API_ID and API_HASH environment variables.")
    if Config.OWNER_ID <= 0:
        raise RuntimeError(
            "Set OWNER_ID to your own numeric Telegram user ID. "
            "The bot will not start without an owner ID."
        )

    print("\n" + "=" * 60)
    print("PRIVATE BOT MODE")
    print("- Only the configured OWNER_ID can use the bot.")
    print("- Keep BOT_TOKEN, API_HASH, and other secrets private.")
    print("=" * 60 + "\n")

    os.makedirs(Config.DOWNLOAD_LOCATION, exist_ok=True)

    async def run_clients():
        # Construct Pyrogram clients inside the running asyncio loop. Creating
        # them before asyncio.run() can bind their storage executor to another
        # loop and cause "Future attached to a different loop" on Colab.
        app = Client(
            "URLUploaderBot",
            bot_token=Config.BOT_TOKEN,
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            sleep_threshold=300,
            plugins=dict(root="plugins"),
        )
        # A second MTProto connection with the same bot identity is used only
        # for uploads. no_updates=True prevents it from competing for updates.
        upload_client = Client(
            "URLUploaderBotUpload2",
            bot_token=Config.BOT_TOKEN,
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            sleep_threshold=300,
            no_updates=True,
        )
        configure_upload_clients([app, upload_client])

        # Group -1 runs before plugin handlers, so unauthorized messages and
        # callback queries never reach downloader, settings, or admin handlers.
        app.add_handler(MessageHandler(owner_only_message, filters.all), group=-1)
        app.add_handler(CallbackQueryHandler(owner_only_callback), group=-1)

        try:
            await app.start()
            await upload_client.start()
            print("Private bot is running with 2 upload sessions.")
            await idle()
        finally:
            if upload_client.is_connected:
                await upload_client.stop()
            if app.is_connected:
                await app.stop()

    asyncio.run(run_clients())
