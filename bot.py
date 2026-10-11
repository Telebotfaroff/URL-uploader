import os
from plugins.config import Config
from pyrogram import Client
import logging

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)
logging.getLogger('pymongo').setLevel(logging.WARNING)
logging.getLogger("aiosqlite").setLevel(logging.WARNING)
logging.getLogger("requests_cache").setLevel(logging.WARNING)

if __name__ == "__main__":

    print("\n" + "=" * 60)
    print("SECURITY REMINDER")
    print("-" * 60)
    print("Do not expose BOT_TOKEN, API_ID, API_HASH, or cookies.txt.")
    print("Store secrets in environment variables or a private .env file.")
    print("=" * 60 + "\n")

    if not os.path.isdir(Config.DOWNLOAD_LOCATION):
        os.makedirs(Config.DOWNLOAD_LOCATION)

    plugins = dict(root="plugins")
    Client = Client(
        "URLUploaderBot",
        bot_token=Config.BOT_TOKEN,
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        sleep_threshold=300,
        plugins=plugins
    )

    print("Bot is running.")
    Client.run()
