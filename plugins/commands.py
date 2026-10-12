import os
import time
import psutil
import shutil
import string
import asyncio
import random
from plugins.config import *
from asyncio import TimeoutError
from pyrogram.types import Message 
from plugins.config import Config
from pyrogram import enums
from plugins.script import Translation
from plugins.database.add import AddUser
from plugins.database.database import db
from pyrogram.errors import MessageNotModified
from pyrogram import Client, filters, types, errors
from plugins.settings.settings import OpenSettings
from plugins.download_manager import cancel_process
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery, ForceReply, LinkPreviewOptions




@Client.on_message(filters.private & filters.command(["start"]))
async def start(bot, update):
    await AddUser(bot, update)
    await update.reply_text(
        text=Translation.START_TEXT.format(update.from_user.mention),
        reply_markup=Translation.START_BUTTONS,
        reply_to_message_id=update.id
    )


@Client.on_message(filters.command("help", [".", "/"]) & filters.private)
async def help_bot(_, m: Message):
    await AddUser(_, m)
    return await m.reply_text(
        Translation.HELP_TEXT,
        reply_markup=Translation.HELP_BUTTONS,
       link_preview_options=LinkPreviewOptions(is_disabled=True),
    )

@Client.on_message(filters.command("about", [".", "/"]) & filters.private)
async def aboutme(_, m: Message):
    await AddUser(_, m)
    return await m.reply_text(
        Translation.ABOUT_TEXT,
        reply_markup=Translation.ABOUT_BUTTONS,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )

@Client.on_message(filters.private & filters.reply & filters.text)
async def edit_caption(bot, update):
    await AddUser(bot, update)
    try:
        await bot.send_cached_media(
            chat_id=update.chat.id,
            file_id=update.reply_to_message.video.file_id,
            caption=update.text
        )
    except:
        try:
            await bot.send_cached_media(
                chat_id=update.chat.id,
                file_id=update.reply_to_message.document.file_id,
                caption=update.text
            )
        except:
            pass


@Client.on_message(filters.private & filters.command(["caption"], [".", "/"]))
async def add_caption_help(bot, update):
    await AddUser(bot, update)
    await bot.send_message(
        chat_id=update.chat.id,
        text=Translation.ADD_CAPTION_HELP,
        reply_markup=Translation.BUTTONS,
    )


@Client.on_callback_query(filters.regex(r"^cancel_download\\+"))
async def cancel_cb(c, m):
    raw_user_id = m.data.split("+", 1)[1]
    if raw_user_id == "unknown":
        await m.answer("This download cannot be cancelled from this progress message.", show_alert=True)
        return
    try:
        target_user_id = int(raw_user_id)
    except ValueError:
        await m.answer("Invalid cancellation request.", show_alert=True)
        return
    if target_user_id != m.from_user.id:
        await m.answer("You can only cancel your own download.", show_alert=True)
        return
    if await cancel_process(target_user_id):
        await m.answer("Download cancellation requested.")
        await m.message.edit_text("⛔ Download cancellation requested.")
    else:
        await m.answer("No active download process was found.", show_alert=True)
        await m.message.edit_text("No active download process was found. It may already be finished.")



