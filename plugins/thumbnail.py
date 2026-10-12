import logging
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

import random
import numpy
import os
from PIL import Image
import time
from pyrogram import enums
# the Strings used for this "thing"
from plugins.script import Translation
from pyrogram import Client
from plugins.database.add import AddUser
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
logging.getLogger("pyrogram").setLevel(logging.WARNING)
from pyrogram import filters
from plugins.functions.help_Nekmo_ffmpeg import take_screen_shot
import psutil
import shutil
import string
import asyncio
from asyncio import TimeoutError
from pyrogram.errors import MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery, ForceReply
from plugins.database.database import db
from plugins.config import Config
from plugins.database.database import db
from plugins.settings.settings import *


@Client.on_message(filters.photo)
async def save_photo(bot, update):
    await AddUser(bot, update)
    # received single photo
    download_location = os.path.join(
        Config.DOWNLOAD_LOCATION,
        str(update.from_user.id) + ".jpg"
    )
    await bot.download_media(
        message=update,
        file_name=download_location
    )
    await bot.send_message(
        chat_id=update.chat.id,
        text=Translation.SAVED_CUSTOM_THUMB_NAIL,
        #reply_to_message_id=update.id
    )
    await db.set_thumbnail(update.from_user.id, thumbnail=update.photo.file_id)


@Client.on_message(filters.command(["delthumb"]))
async def delete_thumbnail(bot, update):

    await AddUser(bot, update)
    download_location = os.path.join(
        Config.DOWNLOAD_LOCATION,
        str(update.from_user.id)
    )
    try:
        os.remove(download_location + ".jpg")
        # os.remove(download_location + ".json")
    except:
        pass
    await bot.send_message(
        chat_id=update.chat.id,
        text=Translation.DEL_ETED_CUSTOM_THUMB_NAIL,
    )
    await db.set_thumbnail(update.from_user.id, thumbnail=None)

@Client.on_message(filters.command("showthumb"))
async def viewthumbnail(bot, update):

    await AddUser(bot, update)

    thumbnail = await db.get_thumbnail(update.from_user.id)
    if thumbnail is not None:
        await bot.send_photo(
        chat_id=update.chat.id,
        photo=thumbnail,
        caption=f"YOUR THUMBNAIL 🏞",
        reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🗑️ 𝙳𝙴𝙻𝙴𝚃𝙴 𝚃𝙷𝚄𝙼𝙱𝙽𝙰𝙸𝙻", callback_data="deleteThumbnail", style=enums.ButtonStyle.DANGER)]]
                ),
         )
    else:
        await update.reply_text(text=f"𝙽𝙾 𝚃𝙷𝚄𝙼𝙱𝙽𝙰𝙸𝙻 😐")


def _normalize_thumbnail(path):
    """Convert a custom thumbnail to JPEG and resize it without distorting it."""
    if not path:
        return None
    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((320, 320))
            image.save(path, "JPEG", quality=90, optimize=True)
        return path
    except (OSError, ValueError):
        logger.warning("Invalid custom thumbnail; falling back to Telegram's default", exc_info=True)
        try:
            os.remove(path)
        except OSError:
            pass
        return None


async def Gthumb01(bot, update):
    thumb_image_path = f"{Config.DOWNLOAD_LOCATION}/{str(update.from_user.id)}.jpg"
    db_thumbnail = await db.get_thumbnail(update.from_user.id)
    if db_thumbnail is not None:
        thumbnail = await bot.download_media(message=db_thumbnail, file_name=thumb_image_path)
        return _normalize_thumbnail(thumbnail)
    return None

async def Gthumb02(bot, update, duration, download_directory):
    thumb_image_path = f"{Config.DOWNLOAD_LOCATION}/{str(update.from_user.id)}.jpg"
    db_thumbnail = await db.get_thumbnail(update.from_user.id)
    
    if db_thumbnail is not None:
        thumbnail = await bot.download_media(message=db_thumbnail, file_name=thumb_image_path)
        return _normalize_thumbnail(thumbnail)
    elif duration > 1:
        return await take_screen_shot(download_directory, os.path.dirname(download_directory), random.randint(0, duration - 1))
    else:

        return None
async def Mdata01(download_directory):

          width = 0
          height = 0
          duration = 0
          metadata = extractMetadata(createParser(download_directory))
          if metadata is not None:
              if metadata.has("duration"):
                  duration = metadata.get('duration').seconds
              if metadata.has("width"):
                  width = metadata.get("width")
              if metadata.has("height"):
                  height = metadata.get("height")

          return width, height, duration

async def Mdata02(download_directory):

          width = 0
          duration = 0
          metadata = extractMetadata(createParser(download_directory))
          if metadata is not None:
              if metadata.has("duration"):
                  duration = metadata.get('duration').seconds
              if metadata.has("width"):
                  width = metadata.get("width")

          return width, duration

async def Mdata03(download_directory):

    metadata = extractMetadata(createParser(download_directory))
    return (
        metadata.get('duration').seconds
        if metadata is not None and metadata.has("duration")
        else 0
    )
