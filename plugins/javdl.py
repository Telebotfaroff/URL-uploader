import asyncio
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from plugins.config import Config
from plugins.javdl_core.channel import JavtifulChannelExtractor
from plugins.javdl_core.downloader import Downloader
from plugins.javdl_core.extractor_javtiful import JavtifulExtractor

logger = logging.getLogger(__name__)

extractor = JavtifulExtractor()
channel_extractor = JavtifulChannelExtractor()
downloader = Downloader(workdir=Config.DOWNLOAD_LOCATION)

# Per-user interaction state for inline quality/channel choices.
pending = {}
MAX_CHANNEL_PAGES = 50
MAX_BATCH_VIDEOS = 200


def _valid_url(value, route):
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname in {"javtiful.com", "www.javtiful.com"}
        and parsed.path.startswith(route)
    )


def _duration_text(value):
    if not value:
        return "Unknown"
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return str(value)
    hours, rem = divmod(seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


async def _safe_edit(message, text):
    try:
        await message.edit_text(text)
    except Exception:
        pass


def _download_progress(message, loop, title):
    last_update = {"time": 0.0}

    def callback(current, total, stage):
        now = time.monotonic()
        if now - last_update["time"] < 3 and current != total:
            return
        last_update["time"] = now
        if total:
            percent = min(100, int(current * 100 / total))
            text = f"⬇️ Downloading: {title}\n{percent}% ({current // (1024 * 1024)} / {total // (1024 * 1024)} MB)"
        else:
            text = f"⬇️ Downloading: {title}\n{current // (1024 * 1024)} MB received"
        loop.call_soon_threadsafe(
            lambda: asyncio.create_task(_safe_edit(message, text))
        )

    return callback


async def _upload_progress(current, total, message, title, state):
    now = time.monotonic()
    if now - state["time"] < 3 and current != total:
        return
    state["time"] = now
    percent = min(100, int(current * 100 / total)) if total else 0
    await _safe_edit(
        message,
        f"📤 Uploading: {title}\n{percent}% ({current // (1024 * 1024)} / {total // (1024 * 1024)} MB)",
    )


def _download_thumbnail(url):
    if not url:
        return None
    try:
        response = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("image/") or len(response.content) > 8 * 1024 * 1024:
            return None
        suffix = ".jpg"
        path = Path(Config.DOWNLOAD_LOCATION) / f"thumb_{int(time.time() * 1000)}{suffix}"
        path.write_bytes(response.content)
        return str(path)
    except Exception:
        logger.debug("Unable to fetch video thumbnail", exc_info=True)
        return None


async def _download_and_upload(bot, chat_id, url, quality, status, caption=None):
    video = None
    thumb_path = None
    try:
        await status.edit_text("🔎 Extracting video details…")
        video = await asyncio.to_thread(extractor.extract, url)
        if not video.qualities:
            raise RuntimeError("No downloadable video stream was found. The page may require browser playback or may have changed.")

        loop = asyncio.get_running_loop()
        progress = _download_progress(status, loop, video.title or "Video")
        await status.edit_text(f"⬇️ Preparing download: {video.title or 'Video'}")
        await asyncio.to_thread(downloader.download, video, quality, progress)

        thumb_path = await asyncio.to_thread(_download_thumbnail, video.thumbnail)
        upload_state = {"time": 0.0}
        title = video.title or "Video"
        await status.edit_text(f"📤 Uploading: {title}")
        send_kwargs = {
            "chat_id": chat_id,
            "video": video.local_path,
            "caption": (caption or title)[:1024],
            "supports_streaming": True,
            "progress": _upload_progress,
            "progress_args": (status, title, upload_state),
        }
        if video.duration:
            send_kwargs["duration"] = int(video.duration)
        if thumb_path:
            send_kwargs["thumb"] = thumb_path
        await bot.send_video(**send_kwargs)
        await status.edit_text(f"✅ Uploaded successfully\n\n🎬 {title}\n🎚 Quality: {video.selected_quality or quality}")
        return title
    finally:
        for path in (getattr(video, "local_path", None), thumb_path):
            if path:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass


def _quality_keyboard(qualities):
    rows = []
    for index, quality in enumerate(qualities[:8]):
        rows.append([InlineKeyboardButton(f"⬇️ {quality}", callback_data=f"javdl|q|{index}")])
    return InlineKeyboardMarkup(rows)


def _channel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬇️ Download first page", callback_data="javdl|page")],
        [InlineKeyboardButton("📥 Download all videos", callback_data="javdl|all")],
    ])


async def _process_channel(bot, message, state, all_pages=False):
    status = await message.reply_text("⏳ Starting channel download…")
    uploaded = failed = skipped = 0
    seen = set()
    try:
        total_pages = max(1, min(int(state.get("total_pages") or 1), MAX_CHANNEL_PAGES))
        pages = range(total_pages) if all_pages else range(1)
        total_limit = MAX_BATCH_VIDEOS
        for page_number in pages:
            page_data = state.get("first_page") if page_number == 0 else await asyncio.to_thread(channel_extractor.page, state["url"], page_number)
            items = (page_data or {}).get("items") or (page_data or {}).get("first_page_items") or []
            for item in items:
                if uploaded + failed + skipped >= total_limit:
                    break
                url = item.get("post_url")
                if not url or url in seen:
                    skipped += 1
                    continue
                seen.add(url)
                title = item.get("title") or "Video"
                try:
                    await status.edit_text(f"🎬 Channel: {state.get('channel_name', 'Channel')}\nVideo {uploaded + failed + skipped + 1}\n\n{title}")
                    await _download_and_upload(bot, message.chat.id, url, "best", status, caption=title)
                    uploaded += 1
                except Exception as exc:
                    failed += 1
                    logger.exception("Channel video failed: %s", url)
                    await _safe_edit(status, f"⚠️ Failed: {title}\n{type(exc).__name__}: {str(exc)[:500]}\n\nContinuing with the next video…")
            if uploaded + failed + skipped >= total_limit:
                break
        await status.edit_text(
            f"🏁 Channel processing finished\n\n"
            f"📺 {state.get('channel_name', 'Channel')}\n"
            f"✅ Uploaded: {uploaded}\n⏭️ Skipped: {skipped}\n❌ Failed: {failed}\n"
            f"Pages scanned: {min(total_pages, len(pages) if hasattr(pages, '__len__') else total_pages)}"
        )
    except Exception as exc:
        await _safe_edit(status, f"❌ Channel processing failed\n{type(exc).__name__}: {str(exc)[:700]}")
    finally:
        pending.pop(message.from_user.id, None)


@Client.on_message(filters.private & filters.command(["vid"], ["/", "."]))
async def vid_command(bot, message):
    parts = message.command or []
    if len(parts) < 2:
        return await message.reply_text("Usage: /vid https://javtiful.com/video/…")
    url = parts[1].strip()
    if not _valid_url(url, "/video/"):
        return await message.reply_text("Please provide a valid Javtiful video URL.")
    status = await message.reply_text("🔎 Extracting video details…")
    try:
        video = await asyncio.to_thread(extractor.extract, url)
        if not video.qualities:
            return await status.edit_text("❌ No downloadable stream was found for this video.")
        qualities = list(video.qualities)
        qualities.sort(key=lambda value: int(re.search(r"\d+", value).group()) if re.search(r"\d+", value) else 0, reverse=True)
        qualities = qualities[:8]
        pending[message.from_user.id] = {"type": "video", "url": url, "qualities": qualities}
        await status.edit_text(
            f"🎬 {video.title or 'Video'}\n\n"
            f"⏱ Duration: {_duration_text(video.duration)}\n"
            f"🎚 Available qualities: {', '.join(qualities)}\n\n"
            "Choose a quality to download and upload here:",
            reply_markup=_quality_keyboard(qualities),
        )
    except Exception as exc:
        logger.exception("JAVDL video extraction failed")
        await status.edit_text(f"❌ Extraction failed\n{type(exc).__name__}: {str(exc)[:700]}")


@Client.on_message(filters.private & filters.command(["channel"], ["/", "."]))
async def javtiful_channel_command(bot, message):
    parts = message.command or []
    if len(parts) < 2:
        return await message.reply_text(
            "Usage: /channel https://javtiful.com/channel/…\n\n"
            "This command inspects a Javtiful channel. Use /setchannel to configure a Telegram upload destination."
        )
    url = parts[1].strip()
    if not _valid_url(url, "/channel/"):
        return await message.reply_text("Please provide a valid Javtiful channel URL.")
    status = await message.reply_text("🔎 Inspecting channel…")
    try:
        info = await asyncio.to_thread(channel_extractor.inspect, url)
        total_pages = max(1, int(info.get("total_pages") or 1))
        state = {
            "type": "channel",
            "url": url,
            "channel_name": info.get("channel_name") or "Channel",
            "total_pages": total_pages,
            "total_videos": info.get("total_videos"),
            "first_page": {"items": info.get("first_page_items") or []},
        }
        pending[message.from_user.id] = state
        count = info.get("total_videos")
        await status.edit_text(
            f"📺 {state['channel_name']}\n"
            f"🎬 Videos: {count if count is not None else 'Unknown'}\n"
            f"📄 Pages: {total_pages}\n"
            f"📌 First-page entries: {len(state['first_page']['items'])}\n\n"
            "Choose what to process:",
            reply_markup=_channel_keyboard(),
        )
    except Exception as exc:
        logger.exception("JAVDL channel inspection failed")
        await status.edit_text(f"❌ Channel inspection failed\n{type(exc).__name__}: {str(exc)[:700]}")


@Client.on_message(filters.private & filters.command(["actress"], ["/", "."]))
async def actress_placeholder(bot, message):
    await message.reply_text(
        "🚧 /actress is not implemented yet. JAVDL currently supports direct video URLs and channel URLs."
    )


@Client.on_callback_query(filters.regex(r"^javdl\|"))
async def javdl_callback(bot, query):
    state = pending.get(query.from_user.id)
    if not state:
        return await query.answer("Session expired. Send the command again.", show_alert=True)

    action = query.data.split("|", 2)
    if len(action) < 2:
        return await query.answer("Invalid action.", show_alert=True)

    if action[1] == "q":
        if state.get("type") != "video" or len(action) != 3:
            return await query.answer("This video selection has expired.", show_alert=True)
        try:
            index = int(action[2])
            quality = state["qualities"][index]
        except (ValueError, IndexError, KeyError):
            return await query.answer("Invalid quality selection.", show_alert=True)
        await query.answer(f"Downloading {quality}")
        pending.pop(query.from_user.id, None)
        try:
            await _download_and_upload(bot, query.message.chat.id, state["url"], quality, query.message)
        except Exception as exc:
            logger.exception("JAVDL video download/upload failed")
            await _safe_edit(query.message, f"❌ Download/upload failed\n{type(exc).__name__}: {str(exc)[:700]}")
        return

    if action[1] in {"page", "all"}:
        if state.get("type") != "channel":
            return await query.answer("This channel selection has expired.", show_alert=True)
        await query.answer("Starting channel processing…")
        await _process_channel(bot, query.message, state, all_pages=(action[1] == "all"))
        return

    await query.answer("Unknown action.", show_alert=True)
