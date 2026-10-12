import asyncio
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from io import BytesIO
from PIL import Image
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from plugins.config import Config
from plugins.database.database import db
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
def _canonical_video_url(url):
    """Normalize URLs so query strings and trailing slashes don't create duplicates."""
    parsed = urlparse(str(url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return str(url or "").strip()
    return parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=parsed.path.rstrip("/"),
        params="",
        query="",
        fragment="",
    ).geturl()


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


def _progress_details(current, total, state):
    """Return a compact progress bar, transfer rate, and ETA."""
    now = time.monotonic()
    started = state.setdefault("started", now)
    elapsed = max(now - started, 0.001)
    speed = max(0, current) / elapsed
    percent = min(100, int(current * 100 / total)) if total and total > 0 else None
    if total and speed > 0:
        remaining = max(0, total - current) / speed
        eta = f"{int(remaining // 60):02d}:{int(remaining % 60):02d}"
    else:
        eta = "—"
    if percent is None:
        bar = "□□□□□□□□□□"
        percent_text = "Progress unknown"
    else:
        filled = min(10, percent // 10)
        bar = "▰" * filled + "▱" * (10 - filled)
        percent_text = f"{percent}%"
    current_mb = current / (1024 * 1024)
    if total and total > 0:
        size_text = f"{current_mb:.1f}/{total / (1024 * 1024):.1f} MB"
    else:
        size_text = f"{current_mb:.1f} MB received"
    speed_mb = speed / (1024 * 1024)
    return f"{bar} {percent_text}\n{size_text} • {speed_mb:.2f} MB/s • ETA {eta}"


def _download_progress(message, loop, title):
    state = {"time": 0.0, "started": time.monotonic()}

    def callback(current, total, stage):
        now = time.monotonic()
        if now - state["time"] < 3 and current != total:
            return
        state["time"] = now
        text = f"⬇️ Downloading: {title}\n{_progress_details(current, total, state)}"
        loop.call_soon_threadsafe(
            lambda: asyncio.create_task(_safe_edit(message, text))
        )

    return callback


async def _upload_progress(current, total, message, title, state):
    now = time.monotonic()
    if now - state["time"] < 3 and current != total:
        return
    state["time"] = now
    state.setdefault("started", now)
    await _safe_edit(
        message,
        f"📤 Uploading: {title}\n{_progress_details(current, total, state)}",
    )


def _video_duration_seconds(path, fallback=None):
    """Read the actual media duration, falling back to extractor metadata."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        duration = float(result.stdout.strip())
        if duration > 0:
            return max(1, round(duration))
    except (FileNotFoundError, subprocess.SubprocessError, ValueError):
        logger.debug("Unable to read duration with ffprobe", exc_info=True)

    try:
        duration = float(fallback)
        if duration > 0:
            return max(1, round(duration))
    except (TypeError, ValueError):
        pass
    return None


def _download_thumbnail(url):
    if not url:
        return None
    try:
        response = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("image/") or len(response.content) > 8 * 1024 * 1024:
            return None
        path = Path(Config.DOWNLOAD_LOCATION) / f"thumb_{time.time_ns()}.jpg"
        with Image.open(BytesIO(response.content)) as image:
            image.convert("RGB").save(path, format="JPEG", quality=88, optimize=True)
        return str(path)
    except Exception:
        logger.debug("Unable to fetch video thumbnail", exc_info=True)
        return None


async def _download_and_upload(bot, chat_id, url, quality, status, caption=None, download_semaphore=None, upload_semaphore=None):
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
        if download_semaphore is None:
            await asyncio.to_thread(downloader.download, video, quality, progress)
        else:
            async with download_semaphore:
                await asyncio.to_thread(downloader.download, video, quality, progress)

        thumb_path = await asyncio.to_thread(_download_thumbnail, video.thumbnail)
        upload_state = {"time": 0.0}
        title = video.title or "Video"
        await status.edit_text(f"📤 Uploading: {title}")
        # Upload to the configured dump channel first; if it fails, use the user's chat.
        dump_chat_id = None
        try:
            saved_dump_chat = await db.get_setting("dump_chat_id")
            if saved_dump_chat:
                dump_chat_id = int(saved_dump_chat)
        except Exception:
            logger.exception("Could not read configured dump channel; using user chat")

        target_chat_id = dump_chat_id if dump_chat_id is not None else chat_id
        send_kwargs = {
            "chat_id": target_chat_id,
            "video": video.local_path,
            "caption": (caption or title)[:1024],
            "supports_streaming": True,
            "progress": _upload_progress,
            "progress_args": (status, title, upload_state),
        }
        duration = await asyncio.to_thread(
            _video_duration_seconds, video.local_path, video.duration
        )
        if duration:
            send_kwargs["duration"] = duration
        if thumb_path:
            send_kwargs["thumb"] = thumb_path
        async def _send_with_fallback():
            try:
                await bot.send_video(**send_kwargs)
                return "\n📦 Destination: dump channel" if dump_chat_id is not None else ""
            except Exception:
                if dump_chat_id is None:
                    raise
                logger.exception("Upload to dump channel failed; falling back to user chat")
                await status.edit_text("⚠️ Dump-channel upload failed. Retrying in your chat…")
                send_kwargs["chat_id"] = chat_id
                upload_state["time"] = 0.0
                upload_state["started"] = time.monotonic()
                await bot.send_video(**send_kwargs)
                return "\n↩️ Destination: your chat (fallback)"

        if upload_semaphore is None:
            destination_note = await _send_with_fallback()
        else:
            async with upload_semaphore:
                destination_note = await _send_with_fallback()
        await status.edit_text(
            f"✅ Uploaded successfully\n\n🎬 {title}\n"
            f"🎚 Quality: {video.selected_quality or quality}{destination_note}"
        )
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


def _channel_keyboard(skip_uploaded=True, speed="balanced"):
    profiles = {"safe": (1, 1), "balanced": (2, 2), "fast": (3, 3)}
    down, up = profiles.get(speed, profiles["balanced"])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"⏭️ Skip already uploaded: {'ON' if skip_uploaded else 'OFF'}",
            callback_data="javdl|skip_toggle",
        )],
        [InlineKeyboardButton(f"⚙️ Speed: {speed.title()} ({down}↓/{up}↑)", callback_data="javdl|speed")],
        [InlineKeyboardButton("⬇️ Download first page", callback_data="javdl|page")],
        [InlineKeyboardButton("📥 Download all videos", callback_data="javdl|all")],
    ])


async def _process_channel(bot, message, state, user_id, all_pages=False):
    status = await message.reply_text("⏳ Preparing channel download queue…")
    uploaded = failed = skipped = already_uploaded = 0
    seen = set()
    skip_uploaded = bool(state.get("skip_uploaded", True))
    profiles = {"safe": (1, 1), "balanced": (2, 2), "fast": (3, 3)}
    speed = state.get("speed", "balanced")
    download_limit, upload_limit = profiles.get(speed, profiles["balanced"])
    download_semaphore = asyncio.Semaphore(download_limit)
    upload_semaphore = asyncio.Semaphore(upload_limit)
    worker_count = download_limit + upload_limit
    try:
        total_pages = max(1, min(int(state.get("total_pages") or 1), MAX_CHANNEL_PAGES))
        pages = range(total_pages) if all_pages else range(1)
        total_limit = MAX_BATCH_VIDEOS
        jobs = []
        for page_number in pages:
            if len(jobs) + skipped >= total_limit:
                break
            page_data = (
                state.get("first_page")
                if page_number == 0
                else await asyncio.to_thread(channel_extractor.page, state["url"], page_number)
            )
            items = (page_data or {}).get("items") or (page_data or {}).get("first_page_items") or []
            for item in items:
                if len(jobs) + skipped >= total_limit:
                    break
                url = item.get("post_url")
                if not url or url in seen:
                    skipped += 1
                    continue
                seen.add(url)
                title = item.get("title") or "Video"
                if skip_uploaded:
                    try:
                        was_uploaded = await db.is_channel_video_uploaded(_canonical_video_url(url))
                    except Exception:
                        logger.exception("Could not check Neon channel upload history")
                        was_uploaded = False
                    if was_uploaded:
                        already_uploaded += 1
                        skipped += 1
                        continue
                jobs.append((url, title))
        if not jobs:
            await status.edit_text(
                f"🏁 Nothing to upload.\n\n📺 {state.get('channel_name', 'Channel')}\n"
                f"⏭️ Skipped: {skipped} (already uploaded: {already_uploaded})"
            )
            try:
                await db.set_setting(f"batch_resume_{user_id}", None)
                await db.clear_batch_checkpoint(user_id)
            except Exception:
                logger.exception("Could not clear completed batch checkpoint")
            return
        try:
            checkpoint = {
                "url": state["url"], "type": state.get("listing_type", "channel"),
                "all_pages": bool(all_pages), "skip_uploaded": skip_uploaded,
                "speed": speed, "channel_name": state.get("channel_name", "Listing"),
            }
            await db.set_setting(f"batch_resume_{user_id}", json.dumps(checkpoint))
            await db.save_batch_checkpoint(user_id, state["url"], checkpoint["type"], all_pages, speed, "running")
        except Exception:
            logger.exception("Could not persist batch checkpoint")
        queue = asyncio.Queue()
        for job in jobs:
            queue.put_nowait(job)
        async def worker():
            nonlocal uploaded, failed
            item_status = await message.reply_text("🧵 Parallel worker ready…")
            while True:
                try:
                    url, title = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                await _safe_edit(item_status, f"🧵 Queued: {title}\n⏳ Waiting for a download slot…")
                try:
                    uploaded_title = None
                    for attempt in range(1, 4):
                        try:
                            uploaded_title = await _download_and_upload(
                                bot, message.chat.id, url, "best", item_status, caption=title,
                                download_semaphore=download_semaphore,
                                upload_semaphore=upload_semaphore,
                            )
                            break
                        except Exception:
                            if attempt >= 3:
                                raise
                            await _safe_edit(item_status, f"🔁 Retry {attempt}/2: {title}")
                            await asyncio.sleep(attempt * 2)
                    try:
                        await db.record_channel_upload(_canonical_video_url(url), url, uploaded_title or title)
                    except Exception:
                        logger.exception("Could not save successful upload to Neon history")
                    uploaded += 1
                    await _safe_edit(item_status, f"✅ Complete: {uploaded_title or title}")
                except Exception as exc:
                    failed += 1
                    logger.exception("Channel video failed: %s", url)
                    await _safe_edit(item_status, f"⚠️ Failed: {title}\n{type(exc).__name__}: {str(exc)[:400]}")
                finally:
                    queue.task_done()
                    await _safe_edit(
                        status,
                        f"📊 Batch dashboard • {speed.title()} mode\n\n📺 {state.get('channel_name', 'Channel')}\n"
                        f"📦 Waiting: {queue.qsize()} / {len(jobs)} total\n⬇️ Max downloads: {download_limit}\n📤 Max uploads: {upload_limit}\n🔁 Up to 2 retries per video\n\n"
                        f"✅ Uploaded: {uploaded}\n⏭️ Skipped: {skipped} (already uploaded: {already_uploaded})\n❌ Failed: {failed}"
                    )
        await asyncio.gather(*(worker() for _ in range(min(worker_count, len(jobs)))))
        await status.edit_text(
            f"🏁 Listing processing finished\n\n📺 {state.get('channel_name', 'Channel')}\n"
            f"✅ Uploaded: {uploaded}\n⏭️ Skipped: {skipped} (already uploaded: {already_uploaded})\n❌ Failed: {failed}\n"
            f"📄 Pages scanned: {min(total_pages, len(pages) if hasattr(pages, '__len__') else total_pages)}\n"
            f"⚙️ Mode: {speed.title()} • {download_limit} downloads + {upload_limit} uploads"
        )
        if failed == 0:
            try:
                await db.set_setting(f"batch_resume_{user_id}", None)
                await db.clear_batch_checkpoint(user_id)
            except Exception:
                logger.exception("Could not clear completed batch checkpoint")
    except Exception as exc:
        await _safe_edit(status, f"❌ Channel processing failed\n{type(exc).__name__}: {str(exc)[:700]}")
    finally:
        pending.pop(user_id, None)

@Client.on_message(filters.private & filters.command(["resume"], ["/", "."]))
async def resume_batch_command(bot, message):
    if not message.from_user:
        return
    user_id = message.from_user.id
    try:
        saved = await db.get_setting(f"batch_resume_{user_id}")
        data = json.loads(saved) if saved else None
    except Exception as exc:
        logger.exception("Could not read saved batch")
        return await message.reply_text(f"❌ Could not read saved batch: {type(exc).__name__}. Check DATABASE_URL.")
    if not data:
        return await message.reply_text("ℹ️ No saved batch found. Start one with /channel or /actress.")
    status = await message.reply_text("🔄 Restoring saved listing…")
    try:
        info = await asyncio.to_thread(channel_extractor.inspect, data["url"])
        state = {
            "type": "channel", "listing_type": data.get("type", "channel"),
            "url": data["url"], "channel_name": info.get("channel_name") or data.get("channel_name", "Listing"),
            "total_pages": max(1, int(info.get("total_pages") or 1)),
            "total_videos": info.get("total_videos"),
            "first_page": {"items": info.get("first_page_items") or []},
            "skip_uploaded": True, "speed": data.get("speed", "balanced"),
        }
        pending[user_id] = state
        await status.edit_text("🔄 Resuming saved batch. Upload history will skip completed videos.")
        await _process_channel(bot, message, state, user_id, all_pages=bool(data.get("all_pages", True)))
    except Exception as exc:
        logger.exception("Could not resume saved batch")
        await _safe_edit(status, f"❌ Resume failed: {type(exc).__name__}: {str(exc)[:500]}")

@Client.on_message(filters.private & filters.command(["setdump"], ["/", "."]))
async def set_dump_command(bot, message):
    """Set or clear the destination channel for video uploads (owner only)."""
    if not message.from_user or message.from_user.id != Config.OWNER_ID:
        return await message.reply_text("⛔ Only the bot owner can change the dump channel.")

    parts = message.command or []
    if len(parts) < 2:
        try:
            saved = await db.get_setting("dump_chat_id")
        except Exception as exc:
            logger.exception("Could not read dump-channel setting")
            return await message.reply_text(
                f"❌ Could not read dump-channel setting: {type(exc).__name__}. Check DATABASE_URL."
            )
        if not saved:
            return await message.reply_text(
                "📦 Dump channel is not configured.\n\n"
                "Use /setdump @channelusername or /setdump -1001234567890\n"
                "Use /setdump off to disable it. The bot must be an administrator with permission to post."
            )
        try:
            chat = await bot.get_chat(int(saved))
            label = f"@{chat.username}" if chat.username else (chat.title or str(chat.id))
        except Exception:
            label = saved
        return await message.reply_text(f"📦 Current dump channel: {label} (ID: {saved})\n\nUse /setdump off to disable it.")

    target = parts[1].strip()
    if target.lower() in {"off", "none", "disable"}:
        try:
            await db.set_setting("dump_chat_id", None)
        except Exception as exc:
            logger.exception("Could not clear dump-channel setting")
            return await message.reply_text(f"❌ Could not save setting: {type(exc).__name__}. Check DATABASE_URL.")
        return await message.reply_text("✅ Dump channel disabled. Videos will upload to the user chat.")

    try:
        chat = await bot.get_chat(target if target.startswith("@") else int(target))
        if str(getattr(chat.type, "value", chat.type)).lower().split(".")[-1] != "channel":
            return await message.reply_text("❌ Please provide a Telegram channel username or channel ID.")
        me = await bot.get_me()
        member = await bot.get_chat_member(chat.id, me.id)
        member_status = str(getattr(member.status, "value", member.status)).lower().split(".")[-1]
        if member_status not in {"administrator", "owner", "creator"}:
            return await message.reply_text("❌ Make the bot an administrator in that channel with permission to post messages, then try again.")
        privileges = getattr(member, "privileges", None)
        if member_status == "administrator" and privileges is not None and getattr(privileges, "can_post_messages", True) is False:
            return await message.reply_text("❌ The bot needs the channel permission to post messages.")
        await db.set_setting("dump_chat_id", chat.id)
    except Exception as exc:
        logger.exception("Failed to validate/save dump channel")
        return await message.reply_text(
            f"❌ Could not configure that channel ({type(exc).__name__}). "
            "Add the bot as an administrator with post permission and check DATABASE_URL."
        )

    label = f"@{chat.username}" if chat.username else (chat.title or str(chat.id))
    await message.reply_text(
        f"✅ Dump channel saved: {label} (ID: {chat.id}).\n"
        "Uploads will go there first. If a dump-channel upload fails, the bot will retry in your chat."
    )


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
            "This command inspects a Javtiful channel and offers first-page or batch downloads. Videos go to the configured dump channel first, with your chat as fallback. Configure it with /setdump."
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
            "listing_type": "channel",
            "speed": "balanced",
            "url": url,
            "channel_name": info.get("channel_name") or "Channel",
            "total_pages": total_pages,
            "total_videos": info.get("total_videos"),
            "first_page": {"items": info.get("first_page_items") or []},
            "skip_uploaded": True,
            "speed": "balanced",
            "listing_type": "channel",
        }
        pending[message.from_user.id] = state
        count = info.get("total_videos")
        await status.edit_text(
            f"📺 {state['channel_name']}\n"
            f"🎬 Videos: {count if count is not None else 'Unknown'}\n"
            f"📄 Pages: {total_pages}\n"
            f"📌 First-page entries: {len(state['first_page']['items'])}\n\n"
            "Choose what to process:",
            reply_markup=_channel_keyboard(state.get("skip_uploaded", True), state.get("speed", "balanced")),
        )
    except Exception as exc:
        logger.exception("JAVDL channel inspection failed")
        await status.edit_text(f"❌ Channel inspection failed\n{type(exc).__name__}: {str(exc)[:700]}")


@Client.on_message(filters.private & filters.command(["actress"], ["/", "."]))
async def actress_command(bot, message):
    parts = message.command or []
    if len(parts) < 2:
        return await message.reply_text("Usage: /actress https://javtiful.com/actress/…")
    url = parts[1].strip()
    if not (_valid_url(url, "/actress/") or _valid_url(url, "/model/")):
        return await message.reply_text("Please provide a valid Javtiful actress/model listing URL.")
    status = await message.reply_text("🔎 Inspecting actress listing…")
    try:
        info = await asyncio.to_thread(channel_extractor.inspect, url)
        total_pages = max(1, int(info.get("total_pages") or 1))
        state = {
            "type": "channel",
            "url": url,
            "channel_name": info.get("channel_name") or "Actress listing",
            "total_pages": total_pages,
            "total_videos": info.get("total_videos"),
            "first_page": {"items": info.get("first_page_items") or []},
        }
        state["speed"] = "balanced"
        state["listing_type"] = "actress"
        state["skip_uploaded"] = True
        pending[message.from_user.id] = state
        count = info.get("total_videos")
        await status.edit_text(
            f"🎭 {state['channel_name']}\n"
            f"🎬 Videos: {count if count is not None else 'Unknown'}\n"
            f"📄 Pages: {total_pages}\n"
            f"📌 First-page entries: {len(state['first_page']['items'])}\n\n"
            "Choose what to process:",
            reply_markup=_channel_keyboard(state.get("skip_uploaded", True), state.get("speed", "balanced")),
        )
    except Exception as exc:
        logger.exception("Javtiful actress listing inspection failed")
        await status.edit_text(f"❌ Actress listing inspection failed\n{type(exc).__name__}: {str(exc)[:700]}")


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

    if action[1] == "speed":
        if state.get("type") != "channel":
            return await query.answer("This listing selection has expired.", show_alert=True)
        if state.get("processing"):
            return await query.answer("A batch is already running.", show_alert=True)
        cycle = {"safe": "balanced", "balanced": "fast", "fast": "safe"}
        state["speed"] = cycle.get(state.get("speed", "balanced"), "balanced")
        await query.answer(f"Speed mode: {state['speed'].title()}")
        return await query.message.edit_reply_markup(
            reply_markup=_channel_keyboard(state.get("skip_uploaded", True), state["speed"])
        )

    if action[1] == "skip_toggle":
        if state.get("type") != "channel":
            return await query.answer("This listing selection has expired.", show_alert=True)
        if state.get("processing"):
            return await query.answer("A batch is already running.", show_alert=True)
        state["skip_uploaded"] = not bool(state.get("skip_uploaded", True))
        await query.answer(
            "Already-uploaded videos will be skipped." if state["skip_uploaded"]
            else "Already-uploaded videos will be downloaded again."
        )
        return await query.message.edit_reply_markup(
            reply_markup=_channel_keyboard(state["skip_uploaded"])
        )

    if action[1] in {"page", "all"}:
        if state.get("type") != "channel":
            return await query.answer("This listing selection has expired.", show_alert=True)
        if state.get("processing"):
            return await query.answer("This batch is already running.", show_alert=True)
        state["processing"] = True
        await query.answer("Starting listing processing…")
        await _process_channel(bot, query.message, state, query.from_user.id, all_pages=(action[1] == "all"))
        return

    await query.answer("Unknown action.", show_alert=True)
