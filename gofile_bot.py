import asyncio
import html
import logging
import os
import re
import tempfile
import time
import uuid
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters,
)

from gofile_media import download_video
from gofile_search import search_videos
from gofile_uploader import upload_guest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
LOGGER = logging.getLogger("gofile-bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
try:
    OWNER_ID = int(os.environ.get("OWNER_ID", "0").strip())
except ValueError:
    OWNER_ID = 0

PAGE_SIZE = 1
MAX_SEARCHES_PER_OWNER = 10
SEARCHES = {}
ACTIVE_DOWNLOADS = set()
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_LOCATION", "./gofile_downloads")


def is_owner(update):
    user = update.effective_user
    return bool(user and user.id == OWNER_ID and OWNER_ID > 0)


def _bar(current, total, width=12):
    if total <= 0:
        return "░" * width + "  size unknown"
    percent = min(100, int(current * 100 / total))
    filled = min(width, percent * width // 100)
    return "█" * filled + "░" * (width - filled) + f" {percent}%"


def _transfer_text(phase, current, total, state, mode="bytes"):
    now = time.monotonic()
    elapsed = max(now - state.setdefault("started", now), 0.1)
    if mode == "time" and total > 0:
        percent = min(100, int(current * 100 / total))
        filled = min(12, percent * 12 // 100)
        bar = "█" * filled + "░" * (12 - filled)
        eta = max(0, int(total - current))
        current_text = f"{int(current) // 60:02d}:{int(current) % 60:02d}"
        total_text = f"{int(total) // 60:02d}:{int(total) % 60:02d}"
        return f"{phase}\n{bar} {percent}%\n🎞 Processed {current_text} / {total_text}\n⏳ ETA {eta // 60:02d}:{eta % 60:02d}"
    speed = current / elapsed
    if total > 0:
        eta = max(0, int((total - current) / speed)) if speed > 0 else 0
        eta_text = f"{eta // 60:02d}:{eta % 60:02d}"
        size = f"{current / 1048576:.1f}/{total / 1048576:.1f} MB"
    else:
        eta_text = "calculating…" if current == 0 else "—"
        size = f"{current / 1048576:.1f} MB received"
    return (
        f"{phase}\n{_bar(current, total)}\n"
        f"📦 {size}\n🚀 {speed / 1048576:.2f} MB/s • ⏳ ETA {eta_text}"
    )


async def _update_transfer(message, phase, current, total, state, force=False, mode="bytes"):
    now = time.monotonic()
    if not force and now - state.get("last_edit", 0) < 2.0:
        return
    state["last_edit"] = now
    try:
        await message.edit_text(_transfer_text(phase, current, total, state, mode=mode))
    except Exception as exc:
        if "message is not modified" not in str(exc).lower():
            LOGGER.debug("Progress message edit failed: %s", type(exc).__name__)


def _keyboard(search_id, index, count):
    row = []
    if index > 0:
        row.append(InlineKeyboardButton("◀️ Previous", callback_data=f"p:{search_id}:{index-1}"))
    if index + 1 < count:
        row.append(InlineKeyboardButton("Next ▶️", callback_data=f"p:{search_id}:{index+1}"))
    rows = [row] if row else []
    rows.append([InlineKeyboardButton("📥 Download to GoFile", callback_data=f"d:{search_id}:{index}")])
    rows.append([InlineKeyboardButton("🔎 Open on Javtiful", url=SEARCHES[search_id]["items"][index]["url"])])
    return InlineKeyboardMarkup(rows)


def _caption(item, index, count):
    title = html.escape(item.get("title") or "Video")
    code = html.escape(item.get("code") or "Unknown")
    return f"<b>{title}</b>\n🎞 Code: <code>{code}</code>\n📄 Result {index + 1} of {count}"


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    await update.effective_message.reply_text(
        "🔎 Send a Javtiful video code (example: STAR-765).\n"
        "I'll show matching results with thumbnails, navigation, and a GoFile guest-upload button."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_command(update, context)


async def search_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    code = (update.effective_message.text or "").strip()
    if code.startswith("/"):
        return
    if not re.fullmatch(r"[A-Za-z0-9]{2,10}[-_ ]?\d{2,8}(?:[-_][A-Za-z0-9]{1,8})?", code):
        await update.effective_message.reply_text("Send a video code such as STAR-765.")
        return
    status = await update.effective_message.reply_text(f"🔎 Searching Javtiful for {html.escape(code.upper())}…")
    try:
        items = await asyncio.to_thread(search_videos, code)
        if not items:
            await status.edit_text("No results found. Check the code or try again later.")
            return
        search_id = uuid.uuid4().hex[:8]
        SEARCHES[search_id] = {"owner_id": OWNER_ID, "items": items, "created": time.monotonic()}
        while len(SEARCHES) > MAX_SEARCHES_PER_OWNER:
            oldest = min(SEARCHES, key=lambda key: SEARCHES[key]["created"])
            SEARCHES.pop(oldest, None)
        item = items[0]
        caption = _caption(item, 0, len(items))
        keyboard = _keyboard(search_id, 0, len(items))
        if item.get("thumbnail"):
            try:
                await update.effective_message.reply_photo(
                    photo=item["thumbnail"], caption=caption, parse_mode="HTML", reply_markup=keyboard
                )
                await status.delete()
            except Exception:
                try:
                    await status.edit_text(caption, parse_mode="HTML", reply_markup=keyboard)
                except Exception:
                    await update.effective_message.reply_text(caption, parse_mode="HTML", reply_markup=keyboard)
        else:
            await status.edit_text(caption, parse_mode="HTML", reply_markup=keyboard)
    except Exception as exc:
        LOGGER.exception("Javtiful search failed")
        await status.edit_text(f"❌ Search failed: {html.escape(str(exc)[:500])}")


async def search_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(update):
        await query.answer("Owner only.", show_alert=True)
        return
    try:
        _, search_id, raw_index = query.data.split(":")
        index = int(raw_index)
        session = SEARCHES[search_id]
        if session["owner_id"] != OWNER_ID:
            raise KeyError(search_id)
        item = session["items"][index]
    except (ValueError, KeyError, IndexError):
        await query.answer("This search has expired. Send the code again.", show_alert=True)
        return

    await query.answer()
    caption = _caption(item, index, len(session["items"]))
    keyboard = _keyboard(search_id, index, len(session["items"]))
    try:
        if item.get("thumbnail"):
            await query.message.edit_media(
                media=InputMediaPhoto(media=item["thumbnail"], caption=caption, parse_mode="HTML"),
                reply_markup=keyboard,
            )
        else:
            await query.message.edit_caption(caption=caption, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        try:
            await query.message.edit_caption(caption=caption, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            await query.message.reply_text(caption, parse_mode="HTML", reply_markup=keyboard)


async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(update):
        await query.answer("Owner only.", show_alert=True)
        return
    try:
        _, search_id, raw_index = query.data.split(":")
        index = int(raw_index)
        session = SEARCHES[search_id]
        item = session["items"][index]
    except (ValueError, KeyError, IndexError):
        await query.answer("This search has expired. Search again.", show_alert=True)
        return

    job_key = (OWNER_ID, item["url"])
    if job_key in ACTIVE_DOWNLOADS:
        await query.answer("This video is already processing.", show_alert=True)
        return
    ACTIVE_DOWNLOADS.add(job_key)
    await query.answer("Starting download and GoFile upload…")
    status = await query.message.reply_text("🔎 Preparing video download…")
    temp_dir = tempfile.mkdtemp(prefix="gofile_video_")
    video = None
    path = None
    try:
        loop = asyncio.get_running_loop()
        download_state = {"started": time.monotonic(), "last_edit": 0.0}
        def download_progress(current, total, mode="bytes"):
            asyncio.run_coroutine_threadsafe(
                _update_transfer(status, "⬇️ DOWNLOADING", current, total, download_state, mode=mode),
                loop,
            )
        video, path, quality = await asyncio.to_thread(
            download_video, item["url"], temp_dir, download_progress
        )
        await _update_transfer(status, "⬇️ Download complete", path.stat().st_size, path.stat().st_size, download_state, force=True)

        upload_state = {"started": time.monotonic(), "last_edit": 0.0}
        def upload_progress(current, total):
            asyncio.run_coroutine_threadsafe(
                _update_transfer(status, "☁️ UPLOADING TO GOFILE", current, total, upload_state),
                loop,
            )
        link = await asyncio.to_thread(upload_guest, str(path), upload_progress)
        await status.edit_text("✅ GoFile upload complete. Sending the result…")
        thumb = video.thumbnail or item.get("thumbnail")
        caption = (
            f"<b>{html.escape(video.title or item['title'])}</b>\n"
            f"📦 Quality: {html.escape(str(quality))}\n"
            f"🔗 <a href=\"{html.escape(link, quote=True)}\">Open GoFile download</a>"
        )
        try:
            if thumb:
                await query.message.reply_photo(photo=thumb, caption=caption, parse_mode="HTML")
            else:
                await query.message.reply_text(caption, parse_mode="HTML", disable_web_page_preview=False)
        except Exception:
            await query.message.reply_text(caption, parse_mode="HTML", disable_web_page_preview=False)
        await status.delete()
    except Exception as exc:
        LOGGER.exception("Download/upload job failed")
        try:
            await status.edit_text(f"❌ Failed: {html.escape(str(exc)[:700])}")
        except Exception:
            pass
    finally:
        ACTIVE_DOWNLOADS.discard(job_key)
        if path:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


async def unknown_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.callback_query.answer("Owner only.", show_alert=True)
        return
    await update.callback_query.answer("Unknown button. Search again.", show_alert=True)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is required.")
    if OWNER_ID <= 0:
        raise RuntimeError("OWNER_ID must be a positive numeric Telegram user ID.")
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(search_navigation, pattern=r"^p:"))
    application.add_handler(CallbackQueryHandler(download_callback, pattern=r"^d:"))
    application.add_handler(CallbackQueryHandler(unknown_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_message))
    LOGGER.info("GoFile bot starting in owner-only mode; no database configured.")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
