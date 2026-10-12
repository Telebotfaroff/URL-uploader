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
SCREENSHOT_CHANNEL_ID = -1003963567446
try:
    OWNER_ID = int(os.environ.get("OWNER_ID", "7367490186").strip())
except ValueError:
    OWNER_ID = 0

PAGE_SIZE = 1
MAX_SEARCHES_PER_OWNER = 10
SEARCHES = {}
BULK_JOBS = {}
ACTIVE_DOWNLOADS = set()
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_LOCATION", "./gofile_downloads")

# Runtime-configurable parallelism; changes apply to the next bulk batch.
PARALLEL_SETTINGS = {"downloads": 3, "uploads": 2, "screenshots": 1}
PARALLEL_LIMITS = {"downloads": (1, 5), "uploads": (1, 3), "screenshots": (1, 2)}


def _parallel_keyboard():
    rows = []
    for key, label in (("downloads", "⬇️ Downloads"), ("uploads", "☁️ Uploads"), ("screenshots", "📸 Screenshots")):
        low, high = PARALLEL_LIMITS[key]
        rows.append([
            InlineKeyboardButton(
                f"{label}: {value}{' ✓' if PARALLEL_SETTINGS[key] == value else ''}",
                callback_data=f"par:{key}:{value}",
            )
            for value in range(low, high + 1)
        ])
    return InlineKeyboardMarkup(rows)


def _parallel_settings_text():
    return (
        "⚙️ <b>Bulk parallel settings</b>\n"
        f"⬇️ Download workers: <b>{PARALLEL_SETTINGS['downloads']}</b> (1–5)\n"
        f"☁️ Upload workers: <b>{PARALLEL_SETTINGS['uploads']}</b> (1–3)\n"
        f"📸 Screenshot workers: <b>{PARALLEL_SETTINGS['screenshots']}</b> (1–2)\n\n"
        "Changes apply to the next bulk batch. Active video jobs are capped at the download-worker setting to limit temporary storage."
    )


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
    if mode == "count":
        percent = min(100, int(current * 100 / total)) if total > 0 else 0
        filled = min(12, percent * 12 // 100)
        bar = "█" * filled + "░" * (12 - filled)
        return f"{phase}\n{bar} {percent}%\n🖼️ Uploaded {current}/{total} images"
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


def _destination_quantity_keyboard(callback_prefix, identifier, index_or_mode=None):
    """Offer each supported screenshot count for chat or private-channel delivery."""
    if callback_prefix == "bulkq":
        mode, bulk_id = identifier, index_or_mode
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("5 here", callback_data=f"bulkq:{mode}:{bulk_id}:5:chat"),
             InlineKeyboardButton("10 here", callback_data=f"bulkq:{mode}:{bulk_id}:10:chat")],
            [InlineKeyboardButton("15 here", callback_data=f"bulkq:{mode}:{bulk_id}:15:chat"),
             InlineKeyboardButton("20 here", callback_data=f"bulkq:{mode}:{bulk_id}:20:chat")],
            [InlineKeyboardButton("5 → private channel", callback_data=f"bulkq:{mode}:{bulk_id}:5:channel"),
             InlineKeyboardButton("10 → private channel", callback_data=f"bulkq:{mode}:{bulk_id}:10:channel")],
            [InlineKeyboardButton("15 → private channel", callback_data=f"bulkq:{mode}:{bulk_id}:15:channel"),
             InlineKeyboardButton("20 → private channel", callback_data=f"bulkq:{mode}:{bulk_id}:20:channel")],
        ])
    search_id, index = identifier, index_or_mode
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("5 here", callback_data=f"{callback_prefix}:{search_id}:{index}:5:chat"),
         InlineKeyboardButton("10 here", callback_data=f"{callback_prefix}:{search_id}:{index}:10:chat")],
        [InlineKeyboardButton("15 here", callback_data=f"{callback_prefix}:{search_id}:{index}:15:chat"),
         InlineKeyboardButton("20 here", callback_data=f"{callback_prefix}:{search_id}:{index}:20:chat")],
        [InlineKeyboardButton("5 → private channel", callback_data=f"{callback_prefix}:{search_id}:{index}:5:channel"),
         InlineKeyboardButton("10 → private channel", callback_data=f"{callback_prefix}:{search_id}:{index}:10:channel")],
        [InlineKeyboardButton("15 → private channel", callback_data=f"{callback_prefix}:{search_id}:{index}:15:channel"),
         InlineKeyboardButton("20 → private channel", callback_data=f"{callback_prefix}:{search_id}:{index}:20:channel")],
    ])


def _screenshot_quantity_keyboard(search_id, index):
    return _destination_quantity_keyboard("sc", search_id, index)


def _combined_quantity_keyboard(search_id, index):
    return _destination_quantity_keyboard("bc", search_id, index)



async def _send_screenshot_albums(bot, screenshots, caption, destination_chat_id):
    """Send Telegram photo albums in batches of at most ten images."""
    for start in range(0, len(screenshots), 10):
        opened_files = []
        try:
            media = []
            for screenshot in screenshots[start:start + 10]:
                handle = screenshot.open("rb")
                opened_files.append(handle)
                media.append(InputMediaPhoto(media=handle, caption=caption))
            await bot.send_media_group(chat_id=destination_chat_id, media=media)
        finally:
            for handle in opened_files:
                handle.close()


def _screenshot_destination(update, destination):
    return SCREENSHOT_CHANNEL_ID if destination == "channel" else update.effective_chat.id


def _caption(item, index, count):
    title = html.escape(item.get("title") or "Video")
    code = html.escape(item.get("code") or "Unknown")
    return f"<b>{title}</b>\n🎞 Code: <code>{code}</code>\n📄 Result {index + 1} of {count}"


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    await update.effective_message.reply_text(
        "🔎 Send a Javtiful video code (example: STAR-765).\n"
        "I'll show matching results with thumbnails, navigation, a DL button for GoFile, and an SS button for screenshots.\n\nFor bulk mode, send up to 20 video codes separated by new lines, commas, or semicolons. Then choose Download all, Screenshots all, or Download + Screenshots all. Use /parallel to configure bulk worker counts."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_command(update, context)


async def parallel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    await update.effective_message.reply_text(
        _parallel_settings_text(), parse_mode="HTML", reply_markup=_parallel_keyboard()
    )


async def parallel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(update):
        await query.answer("Owner only.", show_alert=True)
        return
    try:
        _, key, raw_value = query.data.split(":")
        value = int(raw_value)
        low, high = PARALLEL_LIMITS[key]
        if not low <= value <= high:
            raise ValueError("out of range")
    except (ValueError, KeyError):
        await query.answer("Invalid parallel setting.", show_alert=True)
        return
    PARALLEL_SETTINGS[key] = value
    await query.answer(f"{key.title()} workers set to {value}")
    await query.edit_message_text(
        _parallel_settings_text(), parse_mode="HTML", reply_markup=_parallel_keyboard()
    )


async def search_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    code = (update.effective_message.text or "").strip()
    if code.startswith("/"):
        return
    if re.search(r"[,;\n\r]", code):
        raw_codes = [part.strip() for part in re.split(r"[,;\n\r]+", code) if part.strip()]
        if len(raw_codes) > 1:
            if len(raw_codes) > 20:
                await update.effective_message.reply_text("Bulk mode supports up to 20 video codes per batch.")
                return
            code_pattern = re.compile(r"[A-Za-z]{2,10}(?:[-_ ]?\d{2,8}(?:[-_][A-Za-z0-9]{1,8})?)?")
            invalid = [value for value in raw_codes if not code_pattern.fullmatch(value)]
            if invalid:
                await update.effective_message.reply_text("These entries do not look like video codes: " + ", ".join(invalid[:5]))
                return
            await start_bulk_search(update, raw_codes)
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
        _, search_id, raw_index, raw_count, destination = query.data.split(":")
        index = int(raw_index)
        count = int(raw_count)
        if destination not in ("chat", "channel"):
            raise ValueError("Invalid screenshot destination")
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

        video_code = html.escape(item.get("code") or "Unknown")
        await _send_screenshot_albums(
            context.bot, screenshots, video_code, _screenshot_destination(update, destination)
        )
        await status.edit_text(
            f"✅ Sent {len(screenshots)} screenshot(s) to "
            f"{'the private channel' if destination == 'channel' else 'this chat'}."
        )
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
        _, search_id, raw_index, raw_count, destination = query.data.split(":")
        index = int(raw_index)
        count = int(raw_count)
        if destination not in ("chat", "channel"):
            raise ValueError("Invalid screenshot destination")
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

        await _send_screenshot_albums(
            context.bot, screenshots, code, _screenshot_destination(update, destination)
        )
        await status.edit_text(
            f"✅ GoFile upload complete. Sent {len(screenshots)} screenshot(s) to "
            f"{'the private channel' if destination == 'channel' else 'this chat'}."
        )
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


def _bulk_keyboard(bulk_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Download all", callback_data=f"bulk:dl:{bulk_id}")],
        [InlineKeyboardButton("📸 Screenshots all", callback_data=f"bulk:ss:{bulk_id}")],
        [InlineKeyboardButton("📥📸 Download + Screenshots all", callback_data=f"bulk:both:{bulk_id}")],
    ])


def _bulk_quantity_keyboard(bulk_id, mode):
    return _destination_quantity_keyboard("bulkq", mode, bulk_id)


async def start_bulk_search(update, codes):
    status = await update.effective_message.reply_text(f"🔎 Searching {len(codes)} video codes one by one…")
    results, missing = [], []
    for code in codes:
        try:
            matches = await asyncio.to_thread(search_videos, code)
            if not matches:
                missing.append(code.upper())
                continue
            wanted = re.sub(r"[-_ ]+", "-", code).upper()
            item = next((entry for entry in matches if re.sub(r"[-_ ]+", "-", str(entry.get("code") or "")).upper() == wanted), matches[0])
            results.append({"requested_code": code.upper(), "item": item})
        except Exception as exc:
            LOGGER.warning("Bulk search failed for %s: %s", code, str(exc)[:200])
            missing.append(code.upper())
    if not results:
        await status.edit_text("❌ No videos were found for any supplied code.")
        return
    bulk_id = uuid.uuid4().hex[:8]
    BULK_JOBS[bulk_id] = {"owner_id": OWNER_ID, "results": results, "created": time.monotonic()}
    while len(BULK_JOBS) > 5:
        oldest = min(BULK_JOBS, key=lambda key: BULK_JOBS[key]["created"])
        BULK_JOBS.pop(oldest, None)
    lines = [
        "📚 <b>Bulk batch ready</b>",
        f"Found: {len(results)}/{len(codes)} video(s)",
        "Choose one action for every found video. Use /parallel to configure worker counts.",
        "",
    ]
    for row in results:
        lines.append(f"• <code>{html.escape(row['requested_code'])}</code> — {html.escape((row['item'].get('title') or 'Video')[:100])}")
    if missing:
        lines.extend(["", "Not found: " + ", ".join(html.escape(code) for code in missing)])
    await status.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=_bulk_keyboard(bulk_id))


async def bulk_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(update):
        await query.answer("Owner only.", show_alert=True)
        return
    try:
        _, mode, bulk_id = query.data.split(":")
        batch = BULK_JOBS[bulk_id]
        if batch["owner_id"] != OWNER_ID or mode not in ("dl", "ss", "both"):
            raise KeyError(bulk_id)
    except (ValueError, KeyError):
        await query.answer("This bulk batch expired. Send the codes again.", show_alert=True)
        return
    await query.answer()
    if mode == "dl":
        await run_bulk_jobs(query, batch, mode, 0)
    else:
        await query.message.reply_text(
            f"Choose screenshot quantity and destination for each of the {len(batch['results'])} videos:",
            reply_markup=_bulk_quantity_keyboard(bulk_id, mode),
        )


async def bulk_quantity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_owner(update):
        await query.answer("Owner only.", show_alert=True)
        return
    try:
        _, mode, bulk_id, raw_count, destination = query.data.split(":")
        count = int(raw_count)
        if destination not in ("chat", "channel"):
            raise ValueError("Invalid screenshot destination")
        batch = BULK_JOBS[bulk_id]
        if batch["owner_id"] != OWNER_ID or mode not in ("ss", "both") or count not in (5, 10, 15, 20):
            raise KeyError(bulk_id)
    except (ValueError, KeyError):
        await query.answer("This bulk selection expired or is invalid.", show_alert=True)
        return
    await query.answer(f"Starting {len(batch['results'])} video(s)…")
    await run_bulk_jobs(query, batch, mode, count, destination)


async def run_bulk_jobs(query, batch, mode, screenshot_count, destination="chat"):
    rows = batch["results"]
    total = len(rows)
    settings = dict(PARALLEL_SETTINGS)
    # Bound active jobs so downloaded files cannot accumulate without limit.
    inflight_limit = asyncio.Semaphore(settings["downloads"])
    download_limit = asyncio.Semaphore(settings["downloads"])
    screenshot_limit = asyncio.Semaphore(settings["screenshots"])
    upload_limit = asyncio.Semaphore(settings["uploads"])
    status = await query.message.reply_text(
        f"⏳ Bulk job started: {total} video(s). Workers — "
        f"downloads {settings['downloads']}, screenshots {settings['screenshots']}, uploads {settings['uploads']}."
    )
    completed, failed = [], []
    results_lock = asyncio.Lock()
    progress_lock = asyncio.Lock()
    finished_count = 0

    async def process_one(position, row):
        nonlocal finished_count
        item, code = row["item"], row["requested_code"]
        job_key = (OWNER_ID, item["url"])
        async with inflight_limit:
            if job_key in ACTIVE_DOWNLOADS:
                async with results_lock:
                    failed.append((code, "already processing"))
                    finished_count += 1
                return
            ACTIVE_DOWNLOADS.add(job_key)
            temp_dir = tempfile.mkdtemp(prefix="gofile_bulk_")
            path = None
            item_status = await query.message.reply_text(
                f"⏳ <b>{position}/{total}</b> — <code>{html.escape(code)}</code> queued",
                parse_mode="HTML",
            )
            try:
                loop = asyncio.get_running_loop()
                download_state = {"started": time.monotonic(), "last_edit": 0.0}

                def download_progress(current, size, progress_mode="bytes"):
                    asyncio.run_coroutine_threadsafe(
                        _update_transfer(
                            item_status, f"⬇️ DOWNLOAD {position}/{total} — {code}",
                            current, size, download_state, mode=progress_mode,
                        ),
                        loop,
                    )

                async with download_limit:
                    video, path, quality = await asyncio.to_thread(
                        download_video, item["url"], temp_dir, download_progress
                    )

                gofile_link = None
                screenshots = []
                # Per-video order: download -> screenshots -> GoFile upload.
                if mode in ("ss", "both"):
                    async with screenshot_limit:
                        await item_status.edit_text(
                            f"📸 <b>{position}/{total}</b> — <code>{html.escape(code)}</code> extracting screenshots",
                            parse_mode="HTML",
                        )
                        screenshots = await asyncio.to_thread(
                            extract_screenshots, str(path), temp_dir, screenshot_count, video.duration
                        )

                if mode in ("dl", "both"):
                    upload_state = {"started": time.monotonic(), "last_edit": 0.0}

                    def upload_progress(current, size):
                        asyncio.run_coroutine_threadsafe(
                            _update_transfer(
                                item_status, f"☁️ GOFILE {position}/{total} — {code}",
                                current, size, upload_state,
                            ),
                            loop,
                        )

                    async with upload_limit:
                        gofile_link = await asyncio.to_thread(upload_guest, str(path), upload_progress)

                title = html.escape(item.get("title") or video.title or "Video")
                code_html = html.escape(item.get("code") or code)
                result_lines = [f"✅ <b>{title}</b>", f"🎞 Code: <code>{code_html}</code>"]
                if gofile_link:
                    result_lines.append(f"🔗 GoFile: <code>{html.escape(gofile_link)}</code>")
                if screenshots:
                    result_lines.append(f"📸 Screenshots: {len(screenshots)}")
                await query.message.reply_text("\n".join(result_lines), parse_mode="HTML")

                if screenshots:
                    destination_chat_id = SCREENSHOT_CHANNEL_ID if destination == "channel" else query.message.chat_id
                    await _send_screenshot_albums(
                        query.get_bot(), screenshots, code_html, destination_chat_id
                    )
                await item_status.edit_text(
                    f"✅ <b>{position}/{total}</b> — <code>{html.escape(code)}</code> complete",
                    parse_mode="HTML",
                )
                async with results_lock:
                    completed.append(code)
            except Exception as exc:
                LOGGER.exception("Bulk job failed for %s", code)
                async with results_lock:
                    failed.append((code, str(exc)[:180]))
                try:
                    await item_status.edit_text(
                        f"❌ <b>{html.escape(code)}</b>: {html.escape(str(exc)[:400])}",
                        parse_mode="HTML",
                    )
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
                async with progress_lock:
                    finished_count += 1
                    done = finished_count
                    try:
                        await status.edit_text(
                            f"⏳ Bulk progress: {done}/{total} finished\n"
                            f"⬇️ Downloads: {settings['downloads']} • 📸 Screenshots: {settings['screenshots']} • ☁️ Uploads: {settings['uploads']}"
                        )
                    except Exception:
                        pass

    await asyncio.gather(*(process_one(i, row) for i, row in enumerate(rows, 1)))
    summary = [
        "🏁 <b>Bulk job finished</b>",
        f"Mode: {html.escape(mode.upper())}",
        f"Successful: {len(completed)}",
        f"Failed/skipped: {len(failed)}",
        f"Parallel workers used — downloads: {settings['downloads']}, screenshots: {settings['screenshots']}, uploads: {settings['uploads']}",
    ]
    if failed:
        summary.append("Failures: " + ", ".join(
            f"{html.escape(code)} ({html.escape(reason)})" for code, reason in failed[:10]
        ))
    await status.edit_text("\n".join(summary), parse_mode="HTML")

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
    application.add_handler(CommandHandler("parallel", parallel_command))
    application.add_handler(CallbackQueryHandler(parallel_callback, pattern=r"^par:"))
    application.add_handler(CallbackQueryHandler(search_navigation, pattern=r"^p:"))
    application.add_handler(CallbackQueryHandler(download_callback, pattern=r"^d:"))
    application.add_handler(CallbackQueryHandler(screenshot_quantity_callback, pattern=r"^ss:"))
    application.add_handler(CallbackQueryHandler(screenshot_callback, pattern=r"^sc:"))
    application.add_handler(CallbackQueryHandler(combined_quantity_callback, pattern=r"^both:"))
    application.add_handler(CallbackQueryHandler(combined_callback, pattern=r"^bc:"))
    application.add_handler(CallbackQueryHandler(bulk_action_callback, pattern=r"^bulk:"))
    application.add_handler(CallbackQueryHandler(bulk_quantity_callback, pattern=r"^bulkq:"))
    application.add_handler(CallbackQueryHandler(unknown_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_message))
    LOGGER.info("GoFile bot starting in owner-only mode; no database configured.")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
