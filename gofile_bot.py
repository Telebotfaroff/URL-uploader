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

from gofile_media import download_video, extract_screenshots
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
    OWNER_ID = int(os.environ.get("OWNER_ID", "7367490186").strip())
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
    rows.append([
        InlineKeyboardButton("📥 DL", callback_data=f"d:{search_id}:{index}"),
        InlineKeyboardButton("📸 SS", callback_data=f"ss:{search_id}:{index}"),
    ])
    rows.append([
        InlineKeyboardButton("📥 DL + 📸 SS", callback_data=f"both:{search_id}:{index}"),
    ])
    return InlineKeyboardMarkup(rows)


def _screenshot_quantity_keyboard(search_id, index):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("5 screenshots", callback_data=f"sc:{search_id}:{index}:5"),
            InlineKeyboardButton("10 screenshots", callback_data=f"sc:{search_id}:{index}:10"),
        ],
        [
            InlineKeyboardButton("15 screenshots", callback_data=f"sc:{search_id}:{index}:15"),
            InlineKeyboardButton("20 screenshots", callback_data=f"sc:{search_id}:{index}:20"),
        ],
    ])


def _combined_quantity_keyboard(search_id, index):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("5 screenshots", callback_data=f"bc:{search_id}:{index}:5"),
            InlineKeyboardButton("10 screenshots", callback_data=f"bc:{search_id}:{index}:10"),
        ],
        [
            InlineKeyboardButton("15 screenshots", callback_data=f"bc:{search_id}:{index}:15"),
            InlineKeyboardButton("20 screenshots", callback_data=f"bc:{search_id}:{index}:20"),
        ],
    ])


def _caption(item, index, count):
    title = html.escape(item.get("title") or "Video")
    code = html.escape(item.get("code") or "Unknown")
    return f"<b>{title}</b>\n🎞 Code: <code>{code}</code>\n📄 Result {index + 1} of {count}"


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    await update.effective_message.reply_text(
        "🔎 Send a Javtiful video code (example: STAR-765).\n"
        "I'll show matching results with thumbnails, navigation, a DL button for GoFile, and an SS button for screenshots."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_command(update, context)


async def search_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    code = (update.effective_message.text or "").strip()
    if code.startswith("/"):
        return
    if not re.fullmatch(r"[A-Za-z]{2,10}(?:[-_ ]?\d{2,8}(?:[-_][A-Za-z0-9]{1,8})?)?", code):
        await update.effective_message.reply_text("Send a video code such as STAR-765 or a code prefix such as IPZZ.")
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
            f"<b>{html.escape(item.get('title') or video.title or 'Video')}</b>\n"
            f"📦 Quality: {html.escape(str(quality))}\n"
            f"🔗 GoFile link (copy):\n<code>{html.escape(link)}</code>"
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


async def screenshot_quantity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await query.answer("This search has expired. Search again.", show_alert=True)
        return
    await query.answer()
    title = html.escape(item.get("title") or item.get("code") or "Video")
    await query.message.reply_text(
        f"📸 <b>{title}</b>\nChoose how many screenshots to extract:",
        parse_mode="HTML",
        reply_markup=_screenshot_quantity_keyboard(search_id, index),
    )


async def screenshot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(update):
        await query.answer("Owner only.", show_alert=True)
        return
    try:
        _, search_id, raw_index, raw_count = query.data.split(":")
        index = int(raw_index)
        count = int(raw_count)
        session = SEARCHES[search_id]
        if session["owner_id"] != OWNER_ID:
            raise KeyError(search_id)
        item = session["items"][index]
        if count not in (5, 10, 15, 20):
            raise ValueError("Invalid screenshot quantity")
    except (ValueError, KeyError, IndexError):
        await query.answer("This selection has expired or is invalid.", show_alert=True)
        return

    job_key = (OWNER_ID, item["url"])
    if job_key in ACTIVE_DOWNLOADS:
        await query.answer("This video is already processing.", show_alert=True)
        return
    ACTIVE_DOWNLOADS.add(job_key)
    await query.answer(f"Extracting {count} screenshots…")
    status = await query.message.reply_text(
        f"📸 Preparing to extract {count} screenshots…"
    )
    temp_dir = tempfile.mkdtemp(prefix="gofile_screenshots_")
    path = None
    try:
        loop = asyncio.get_running_loop()
        download_state = {"started": time.monotonic(), "last_edit": 0.0}

        def download_progress(current, total, mode="bytes"):
            asyncio.run_coroutine_threadsafe(
                _update_transfer(
                    status, "⬇️ DOWNLOADING VIDEO FOR SCREENSHOTS",
                    current, total, download_state, mode=mode,
                ),
                loop,
            )

        video, path, quality = await asyncio.to_thread(
            download_video, item["url"], temp_dir, download_progress
        )
        await _update_transfer(
            status, "🎞 Video ready; extracting screenshots",
            path.stat().st_size, path.stat().st_size, download_state, force=True,
        )
        screenshots = await asyncio.to_thread(
            extract_screenshots, str(path), temp_dir, count, video.duration
        )

        # Put the video code in every screenshot caption, including each album.
        video_code = html.escape(item.get("code") or "Unknown")
        # Telegram albums accept at most 10 photos per message group.
        for start in range(0, len(screenshots), 10):
            batch = screenshots[start:start + 10]
            opened_files = []
            try:
                media = []
                for screenshot in batch:
                    handle = screenshot.open("rb")
                    opened_files.append(handle)
                    media.append(InputMediaPhoto(
                        media=handle,
                        caption=video_code,
                    ))
                await query.message.reply_media_group(media=media)
            finally:
                for handle in opened_files:
                    handle.close()

        await status.delete()
    except Exception as exc:
        LOGGER.exception("Screenshot extraction failed")
        try:
            await status.edit_text(f"❌ Screenshot extraction failed: {html.escape(str(exc)[:600])}")
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


async def combined_quantity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await query.answer("This search has expired. Search again.", show_alert=True)
        return
    await query.answer()
    title = html.escape(item.get("title") or item.get("code") or "Video")
    await query.message.reply_text(
        f"📥📸 <b>{title}</b>\nChoose screenshot quantity. I'll upload the video to GoFile and send screenshots too:",
        parse_mode="HTML",
        reply_markup=_combined_quantity_keyboard(search_id, index),
    )


async def combined_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(update):
        await query.answer("Owner only.", show_alert=True)
        return
    try:
        _, search_id, raw_index, raw_count = query.data.split(":")
        index = int(raw_index)
        count = int(raw_count)
        session = SEARCHES[search_id]
        if session["owner_id"] != OWNER_ID:
            raise KeyError(search_id)
        item = session["items"][index]
        if count not in (5, 10, 15, 20):
            raise ValueError("Invalid screenshot quantity")
    except (ValueError, KeyError, IndexError):
        await query.answer("This selection has expired or is invalid.", show_alert=True)
        return

    job_key = (OWNER_ID, item["url"])
    if job_key in ACTIVE_DOWNLOADS:
        await query.answer("This video is already processing.", show_alert=True)
        return
    ACTIVE_DOWNLOADS.add(job_key)
    await query.answer("Downloading, uploading, and generating screenshots…")
    status = await query.message.reply_text(
        f"📥📸 Starting GoFile upload and extracting {count} screenshots…"
    )
    temp_dir = tempfile.mkdtemp(prefix="gofile_combined_")
    path = None
    try:
        loop = asyncio.get_running_loop()
        download_state = {"started": time.monotonic(), "last_edit": 0.0}

        def download_progress(current, total, mode="bytes"):
            asyncio.run_coroutine_threadsafe(
                _update_transfer(
                    status, "⬇️ DOWNLOADING VIDEO", current, total,
                    download_state, mode=mode,
                ),
                loop,
            )

        video, path, quality = await asyncio.to_thread(
            download_video, item["url"], temp_dir, download_progress
        )
        await _update_transfer(
            status, "⬇️ Download complete", path.stat().st_size,
            path.stat().st_size, download_state, force=True,
        )

        upload_state = {"started": time.monotonic(), "last_edit": 0.0}

        def upload_progress(current, total):
            asyncio.run_coroutine_threadsafe(
                _update_transfer(
                    status, "☁️ UPLOADING TO GOFILE", current, total, upload_state
                ),
                loop,
            )

        link = await asyncio.to_thread(upload_guest, str(path), upload_progress)
        await _update_transfer(
            status, "📸 Extracting screenshots", 0, 0,
            {"started": time.monotonic(), "last_edit": 0.0}, force=True,
        )
        screenshots = await asyncio.to_thread(
            extract_screenshots, str(path), temp_dir, count, video.duration
        )

        title = html.escape(item.get("title") or video.title or "Video")
        code = html.escape(item.get("code") or "Unknown")
        caption = (
            f"<b>{title}</b>\n🎞 Code: <code>{code}</code>\n"
            f"📦 Quality: {html.escape(str(quality))}\n"
            f"🔗 GoFile link (copy):\n<code>{html.escape(link)}</code>"
        )
        thumb = video.thumbnail or item.get("thumbnail")
        try:
            if thumb:
                await query.message.reply_photo(photo=thumb, caption=caption, parse_mode="HTML")
            else:
                await query.message.reply_text(caption, parse_mode="HTML", disable_web_page_preview=False)
        except Exception:
            await query.message.reply_text(caption, parse_mode="HTML", disable_web_page_preview=False)

        # Every screenshot is captioned with the video code; albums are limited to 10 photos.
        for start in range(0, len(screenshots), 10):
            batch = screenshots[start:start + 10]
            opened_files = []
            try:
                media = []
                for screenshot in batch:
                    handle = screenshot.open("rb")
                    opened_files.append(handle)
                    media.append(InputMediaPhoto(media=handle, caption=code))
                await query.message.reply_media_group(media=media)
            finally:
                for handle in opened_files:
                    handle.close()
        await status.delete()
    except Exception as exc:
        LOGGER.exception("Combined download/upload/screenshot job failed")
        try:
            await status.edit_text(f"❌ Combined task failed: {html.escape(str(exc)[:600])}")
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
    application.add_handler(CallbackQueryHandler(screenshot_quantity_callback, pattern=r"^ss:"))
    application.add_handler(CallbackQueryHandler(screenshot_callback, pattern=r"^sc:"))
    application.add_handler(CallbackQueryHandler(combined_quantity_callback, pattern=r"^both:"))
    application.add_handler(CallbackQueryHandler(combined_callback, pattern=r"^bc:"))
    application.add_handler(CallbackQueryHandler(unknown_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_message))
    LOGGER.info("GoFile bot starting in owner-only mode; no database configured.")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
