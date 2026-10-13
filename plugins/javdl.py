import asyncio
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from io import BytesIO
from PIL import Image
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from plugins.config import Config, get_upload_session
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
active_saved_batches = set()
active_batch_users = set()
MAX_CHANNEL_PAGES = 50
MAX_BATCH_VIDEOS = 200
PARALLEL_SPEED_PROFILES = {"safe": (1, 1), "balanced": (2, 2), "fast": (3, 3)}
DEFAULT_PARALLEL_SPEED = os.getenv("PARALLEL_SPEED", "balanced").strip().lower()
if DEFAULT_PARALLEL_SPEED not in PARALLEL_SPEED_PROFILES:
    DEFAULT_PARALLEL_SPEED = "balanced"
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



def _load_saved_channel_urls_sync():
    """Load Javtiful channel URLs from text-like files under this repository's data/ folder."""
    api_url = "https://api.github.com/repos/Telebotfaroff/URL-uploader/git/trees/main?recursive=1"
    response = requests.get(api_url, timeout=20, headers={"User-Agent": "URL-Uploader-Bot"})
    response.raise_for_status()
    tree = response.json().get("tree", [])
    candidates = [
        item for item in tree
        if item.get("type") == "blob"
        and item.get("path", "").startswith("data/")
        and int(item.get("size") or 0) <= 2 * 1024 * 1024
    ]
    found = []
    seen = set()
    pattern = re.compile(
        r"(?:https?://)?(?:www\.)?javtiful\.com/channel/[^\s<>\"'\x60]+",
        re.IGNORECASE,
    )
    for item in candidates:
        path = item.get("path", "")
        # Ignore likely binary assets; URL lists are expected to be text-like files.
        if Path(path).suffix.lower() in {
            ".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf", ".zip", ".mp4", ".mkv"
        }:
            continue
        raw_url = "https://raw.githubusercontent.com/Telebotfaroff/URL-uploader/main/" + quote(path, safe="/")
        try:
            file_response = requests.get(raw_url, timeout=15, headers={"User-Agent": "URL-Uploader-Bot"})
            file_response.raise_for_status()
            content = file_response.content.decode("utf-8", errors="ignore")
        except Exception:
            logger.warning("Could not read saved channel list file: %s", path, exc_info=True)
            continue
        for match in pattern.findall(content):
            url = match.rstrip(".,;:!?)]}'’”")
            if not url.lower().startswith(("http://", "https://")):
                url = "https://" + url
            if _valid_url(url, "/channel/"):
                canonical = _canonical_video_url(url)
                if canonical not in seen:
                    seen.add(canonical)
                    found.append(url)
    return found

def _load_saved_channels_catalog_sync():
    """Load channel names and metadata from the repository's generated channel catalog."""
    url = "https://raw.githubusercontent.com/Telebotfaroff/URL-uploader/main/data/channels.json"
    response = requests.get(url, timeout=20, headers={"User-Agent": "URL-Uploader-Bot"})
    response.raise_for_status()
    payload = response.json()
    entries = payload.get("channels", []) if isinstance(payload, dict) else []
    channels = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        channel_url = str(entry.get("url") or "").strip()
        if not _valid_url(channel_url, "/channel/"):
            continue
        canonical = _canonical_video_url(channel_url)
        if canonical in seen:
            continue
        seen.add(canonical)
        slug = str(entry.get("slug") or urlparse(channel_url).path.rstrip("/").split("/")[-1])
        name = re.sub(r"\s+", " ", str(entry.get("name") or slug.replace("-", " "))).strip()
        try:
            video_count = int(entry.get("video_count")) if entry.get("video_count") is not None else None
        except (TypeError, ValueError):
            video_count = None
        channels.append({
            "name": name or slug,
            "slug": slug,
            "url": channel_url,
            "video_count": video_count,
            "thumbnail": entry.get("thumbnail"),
        })
    channels.sort(key=lambda item: item["name"].casefold())
    return channels


def _channel_catalog_keyboard(channels, page=0, page_size=8):
    """Build a paginated picker that displays channel names instead of raw URLs."""
    total_pages = max(1, (len(channels) + page_size - 1) // page_size)
    page = max(0, min(int(page), total_pages - 1))
    start = page * page_size
    rows = []
    for index in range(start, min(start + page_size, len(channels))):
        item = channels[index]
        name = item.get("name") or "Channel"
        if len(name) > 34:
            name = name[:31].rstrip() + "..."
        count = item.get("video_count")
        label = f"📺 {name}" + (f" · {count} videos" if count is not None else "")
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"savedch|select|{index}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"savedch|page|{page - 1}"))
    nav.append(InlineKeyboardButton(f"Page {page + 1}/{total_pages}", callback_data="savedch|noop"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"savedch|page|{page + 1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)


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


# Telegram edit throttling: limit updates per message and across the chat.
EDIT_MIN_INTERVAL = 1.2
PROGRESS_MIN_INTERVAL = 5.0
_edit_last_at = {}
_edit_last_text = {}
_chat_edit_at = {}
_chat_edit_locks = {}


async def _safe_edit(message, text, force=None, **edit_kwargs):
    """Edit status messages without flooding Telegram; prioritize final states."""
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    message_id = getattr(message, "id", getattr(message, "message_id", None))
    key = (chat_id, message_id)
    if force is None:
        force = str(text).startswith(("✅", "❌", "⚠️", "🏁", "🔄"))
    if _edit_last_text.get(key) == text:
        return

    lock = _chat_edit_locks.setdefault(chat_id, asyncio.Lock())
    try:
        async with lock:
            now = time.monotonic()
            if _edit_last_text.get(key) == text:
                return
            # Drop stale intermediate updates instead of queueing excess edits.
            if not force:
                if now - _edit_last_at.get(key, 0.0) < PROGRESS_MIN_INTERVAL:
                    return
                if now - _chat_edit_at.get(chat_id, 0.0) < EDIT_MIN_INTERVAL:
                    return
            else:
                wait = EDIT_MIN_INTERVAL - (now - _chat_edit_at.get(chat_id, 0.0))
                if wait > 0:
                    await asyncio.sleep(wait)

            try:
                await message.edit_text(text, **edit_kwargs)
            except Exception as exc:
                if exc.__class__.__name__ == "FloodWait":
                    await asyncio.sleep(max(1, int(getattr(exc, "value", 1))))
                    await message.edit_text(text, **edit_kwargs)
                else:
                    raise
            now = time.monotonic()
            _edit_last_at[key] = now
            _edit_last_text[key] = text
            _chat_edit_at[chat_id] = now
    except Exception:
        logger.debug("Status message edit skipped or failed", exc_info=True)


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


# One shared live dashboard per batch status message.
_BATCH_DASHBOARDS = {}


def _dashboard_key(message):
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    message_id = getattr(message, "id", getattr(message, "message_id", None))
    return (chat_id, message_id)


def _format_active_transfer(item):
    # Keep titles on one line for reliable, readable Telegram dashboards.
    title = re.sub(r"\s+", " ", str(item.get("title") or "Video")).strip()
    if len(title) > 48:
        title = title[:45].rstrip() + "..."
    current = max(0, int(item.get("current") or 0))
    total = max(0, int(item.get("total") or 0))
    now = time.monotonic()
    elapsed = max(now - item.get("started", now), 0.001)
    speed = current / elapsed
    percent = min(100, int(current * 100 / total)) if total else 0
    filled = min(8, int(percent / 12.5))
    bar = "█" * filled + "░" * (8 - filled)
    if total:
        eta = "—"
        if speed > 0 and current < total:
            remaining = max(0, total - current) / speed
            eta = f"{int(remaining // 60):02d}:{int(remaining % 60):02d}"
        size = f"{current / (1024 * 1024):.0f}/{total / (1024 * 1024):.0f} MB"
        details = f"  {bar} {percent:3d}%  •  {size}  •  {speed / (1024 * 1024):.1f} MB/s  •  ETA {eta}"
    else:
        details = "  Preparing transfer..."
    return f"• {title}\n{details}"


# One shared live dashboard per batch status message.
_BATCH_DASHBOARDS = {}


def _dashboard_key(message):
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    message_id = getattr(message, "id", getattr(message, "message_id", None))
    return (chat_id, message_id)


async def _dashboard_refresh(status, force=False):
    dashboard = _BATCH_DASHBOARDS.get(_dashboard_key(status))
    if not dashboard:
        return
    # Download and upload callbacks can fire concurrently. Serialize rendering
    # and coalesce intermediate edits to prevent stale Telegram updates winning.
    refresh_lock = dashboard.setdefault("refresh_lock", asyncio.Lock())
    async with refresh_lock:
        now = time.monotonic()
        if not force and now - dashboard.get("last_refresh_at", 0.0) < 2.0:
            return
        dashboard["last_refresh_at"] = now
        downloads = list(dashboard["downloads"].values())
        uploads = list(dashboard["uploads"].values())
        lines = [f"📊 BATCH • {dashboard['mode'].upper()}"]
        channel_index = dashboard.get("channel_index")
        channel_total = dashboard.get("channel_total")
        channel_name = dashboard.get("channel_name")
        if channel_index and channel_total:
            lines.append(f"📚 CHANNEL {channel_index}/{channel_total} • {channel_name or 'Listing'}")
        lines.extend(["", f"⬇️ DOWNLOADING ({len(downloads)})"])
        lines.extend(_format_active_transfer(item) for item in downloads[:4])
        if len(downloads) > 4:
            lines.append(f"• +{len(downloads) - 4} more downloads")
        if not downloads:
            lines.append("• None")
        lines.extend(["", f"📤 UPLOADING ({len(uploads)})"])
        lines.extend(_format_active_transfer(item) for item in uploads[:4])
        if len(uploads) > 4:
            lines.append(f"• +{len(uploads) - 4} more uploads")
        if not uploads:
            lines.append("• None")
        active = len(downloads) + len(uploads)
        queued = max(0, dashboard["total"] - dashboard["completed"] - dashboard["failed"] - active)
        lines.extend([
            "",
            f"✅ Complete: {dashboard['completed']}/{dashboard['total']}  •  ⏳ Queue: {queued}",
            f"⏭️ Skipped: {dashboard['skipped']}  •  ❌ Failed: {dashboard['failed']}",
        ])
        if channel_index and channel_total:
            run_uploaded = dashboard.get("run_uploaded_base", 0) + dashboard["completed"]
            run_skipped = dashboard.get("run_skipped_base", 0) + dashboard["skipped"]
            run_failed = dashboard.get("run_failed_base", 0) + dashboard["failed"]
            lines.append(f"📈 Run totals: {run_uploaded} uploaded • {run_skipped} skipped • {run_failed} failed")
        # The dashboard itself is throttled above, so force the edit here; the
        # generic status-message throttle would otherwise drop fresh progress.
        await _safe_edit(status, "\n".join(lines), force=True)

async def _dashboard_start(status, phase, title):
    dashboard = _BATCH_DASHBOARDS.get(_dashboard_key(status))
    if not dashboard:
        return False
    item = {"title": title, "current": 0, "total": 0, "started": time.monotonic(),
            "previous_at": time.monotonic(), "previous_bytes": 0}
    dashboard["downloads" if phase == "download" else "uploads"][title] = item
    await _dashboard_refresh(status)
    return True


async def _dashboard_progress(status, phase, title, current, total):
    dashboard = _BATCH_DASHBOARDS.get(_dashboard_key(status))
    if not dashboard:
        return False
    item = dashboard["downloads" if phase == "download" else "uploads"].get(title)
    if item is None:
        item = {"title": title, "current": 0, "total": 0, "started": time.monotonic(),
                "previous_at": time.monotonic(), "previous_bytes": 0}
        dashboard["downloads" if phase == "download" else "uploads"][title] = item
    item["current"] = max(0, int(current or 0))
    item["total"] = max(0, int(total or 0))
    await _dashboard_refresh(status)
    return True


async def _dashboard_remove(status, title, phase=None):
    dashboard = _BATCH_DASHBOARDS.get(_dashboard_key(status))
    if not dashboard:
        return False
    phases = (phase,) if phase else ("download", "upload")
    for name in phases:
        dashboard["downloads" if name == "download" else "uploads"].pop(title, None)
    await _dashboard_refresh(status)
    return True


def _download_progress(message, loop, title):
    state = {"time": 0.0, "started": time.monotonic()}

    def callback(current, total, stage):
        now = time.monotonic()
        if now - state["time"] < PROGRESS_MIN_INTERVAL and current != total:
            return
        state["time"] = now
        def _publish():
            async def _update():
                handled = await _dashboard_progress(message, "download", title, current, total)
                if not handled:
                    text = f"⬇️ DOWNLOADING\n🎬 {title[:180]}\n{_progress_details(current, total, state)}"
                    await _safe_edit(message, text, force=(total > 0 and current >= total))
            asyncio.create_task(_update())
        loop.call_soon_threadsafe(_publish)

    return callback


async def _upload_progress(current, total, message, title, state):
    now = time.monotonic()
    if now - state["time"] < PROGRESS_MIN_INTERVAL and current != total:
        return
    state["time"] = now
    state.setdefault("started", now)
    handled = await _dashboard_progress(message, "upload", title, current, total)
    if not handled:
        await _safe_edit(
            message,
            f"📤 UPLOADING TO TELEGRAM\n🎬 {title[:180]}\n{_progress_details(current, total, state)}",
            force=(total > 0 and current >= total),
        )


def _is_retryable_batch_error(exc):
    """Retry only failures known to occur before Telegram accepts an upload."""
    if isinstance(exc, requests.RequestException):
        return True
    if isinstance(exc, subprocess.TimeoutExpired):
        return True
    if isinstance(exc, RuntimeError):
        message = str(exc).lower()
        return (
            message.startswith("http download failed after ")
            or message.startswith("ffmpeg hls download failed")
            or message.startswith("ffmpeg hls download timed out")
        )
    return False


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
    title = caption or "Video"
    dashboard_active = _dashboard_key(status) in _BATCH_DASHBOARDS
    try:
        if not dashboard_active:
            await _safe_edit(status, "🔎 Extracting video details…")
        video = await asyncio.to_thread(extractor.extract, url)
        if not video.qualities:
            raise RuntimeError("No downloadable video stream was found. The page may require browser playback or may have changed.")

        loop = asyncio.get_running_loop()
        title = video.title or caption or "Video"
        progress = _download_progress(status, loop, title)
        if download_semaphore is None:
            if not await _dashboard_start(status, "download", title):
                await _safe_edit(status, f"⬇️ Preparing download: {title}")
            await asyncio.to_thread(downloader.download, video, quality, progress)
        else:
            async with download_semaphore:
                if not await _dashboard_start(status, "download", title):
                    await _safe_edit(status, f"⬇️ Preparing download: {title}")
                await asyncio.to_thread(downloader.download, video, quality, progress)
        await _dashboard_remove(status, title, "download")

        thumb_path = await asyncio.to_thread(_download_thumbnail, video.thumbnail)
        upload_state = {"time": 0.0}
        title = video.title or caption or "Video"
        if _dashboard_key(status) not in _BATCH_DASHBOARDS:
            await _safe_edit(status, f"📤 Uploading: {title}")
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
        upload_client, upload_session_lock = get_upload_session(bot)

        async def _send_with_fallback():
            try:
                await upload_client.send_video(**send_kwargs)
                return "\n📦 Destination: dump channel" if dump_chat_id is not None else ""
            except Exception as exc:
                # Only fall back for errors that clearly indicate the bot cannot
                # post to the configured destination. Network/upload errors can
                # be ambiguous: retrying them elsewhere may create duplicates.
                destination_errors = {
                    "ChatAdminRequired", "ChatWriteForbidden", "PeerIdInvalid",
                    "ChannelInvalid", "ChannelPrivate", "UserNotParticipant",
                    "ChatIdInvalid",
                }
                if dump_chat_id is None or exc.__class__.__name__ not in destination_errors:
                    raise
                logger.warning(
                    "Dump channel rejected the upload (%s); falling back to user chat",
                    exc.__class__.__name__,
                )
                if _dashboard_key(status) not in _BATCH_DASHBOARDS:
                    await _safe_edit(status, "⚠️ Dump channel unavailable for posting. Sending to your chat…")
                send_kwargs["chat_id"] = chat_id
                upload_state["time"] = 0.0
                upload_state["started"] = time.monotonic()
                await upload_client.send_video(**send_kwargs)
                return "\n↩️ Destination: your chat (fallback)"

        if upload_semaphore is None:
            async with upload_session_lock:
                await _dashboard_start(status, "upload", title)
                destination_note = await _send_with_fallback()
        else:
            async with upload_semaphore:
                async with upload_session_lock:
                    await _dashboard_start(status, "upload", title)
                    destination_note = await _send_with_fallback()
        if _dashboard_key(status) not in _BATCH_DASHBOARDS:
            await _safe_edit(status,
                f"✅ Uploaded successfully\n\n🎬 {title}\n"
                f"🎚 Quality: {video.selected_quality or quality}{destination_note}"
            )
        return title
    finally:
        await _dashboard_remove(status, title)
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


def _channel_keyboard(skip_uploaded=True, speed=DEFAULT_PARALLEL_SPEED):
    profiles = PARALLEL_SPEED_PROFILES
    down, up = profiles.get(speed, profiles["balanced"])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"⏭️ Skip already uploaded: {'ON' if skip_uploaded else 'OFF'}",
            callback_data="javdl|skip_toggle",
        )],
        [InlineKeyboardButton(f"⚙️ Speed: {speed.title()} ({down}↓/{up}↑)", callback_data="javdl|speed")],
        [InlineKeyboardButton("⬇️ Download first page", callback_data="javdl|page")],
        [InlineKeyboardButton("🎯 Choose custom page", callback_data="javdl|custom_page")],
        [InlineKeyboardButton("📥 Download all videos", callback_data="javdl|all")],
    ])


async def _process_channel(bot, message, state, user_id, all_pages=False, selected_page=None, status_message=None):
    if user_id in active_batch_users:
        return await message.reply_text("⏳ A batch is already running. Let it finish before starting another.")
    active_batch_users.add(user_id)
    status = None
    uploaded = failed = skipped = already_uploaded = 0
    seen = set()
    skip_uploaded = bool(state.get("skip_uploaded", True))
    profiles = PARALLEL_SPEED_PROFILES
    speed = state.get("speed", DEFAULT_PARALLEL_SPEED)
    download_limit, upload_limit = profiles.get(speed, profiles["balanced"])
    download_semaphore = asyncio.Semaphore(download_limit)
    upload_semaphore = asyncio.Semaphore(upload_limit)
    # Backpressure: reserve a pipeline slot BEFORE downloading. Allow at most
    # upload_limit active uploads plus one waiting/download slot per uploader.
    # This prevents completed downloads from piling up on disk behind uploads.
    pipeline_slots = asyncio.Semaphore(upload_limit * 2)
    worker_count = download_limit + upload_limit
    try:
        status = status_message or await message.reply_text("⏳ Preparing channel download queue…")
        total_pages = max(1, min(int(state.get("total_pages") or 1), MAX_CHANNEL_PAGES))
        if selected_page is not None:
            if not 1 <= int(selected_page) <= total_pages:
                raise ValueError(f"Page must be between 1 and {total_pages}.")
            pages = [int(selected_page) - 1]
        else:
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
            run_manifest = state.get("saved_channels_run")
            run_setting_key = state.get("saved_channels_setting_key")
            if run_manifest is not None and run_setting_key:
                run_manifest["current_page"] = page_number + 1
                run_manifest["current_channel_index"] = state.get("batch_channel_index")
                try:
                    await db.set_setting(run_setting_key, json.dumps(run_manifest))
                except Exception:
                    logger.exception("Could not persist page checkpoint for saved channel %s", page_number + 1)
            if state.get("batch_channel_index"):
                await _safe_edit(
                    status,
                    f"🔎 SAVED CHANNELS • Channel {state['batch_channel_index']}/{state['batch_channel_total']}\n"
                    f"📺 {state.get('channel_name', 'Listing')}\n"
                    f"📄 Scanning page {page_number + 1}/{len(pages)} • {len(jobs)} new videos queued",
                )
        if not jobs:
            await _safe_edit(status, 
                f"🏁 Nothing to upload.\n\n📺 {state.get('channel_name', 'Channel')}\n"
                f"⏭️ Skipped: {skipped} (already uploaded: {already_uploaded})"
            )
            try:
                await db.set_setting(f"batch_resume_{user_id}", None)
                await db.clear_batch_checkpoint(user_id)
            except Exception:
                logger.exception("Could not clear completed batch checkpoint")
            return {
                "status": "empty", "uploaded": 0, "failed": 0,
                "skipped": skipped, "already_uploaded": already_uploaded,
            }
        try:
            checkpoint = {
                "url": state["url"], "type": state.get("listing_type", "channel"),
                "all_pages": bool(all_pages), "selected_page": selected_page,
                "skip_uploaded": skip_uploaded, "speed": speed,
                "channel_name": state.get("channel_name", "Listing"),
            }
            await db.set_setting(f"batch_resume_{user_id}", json.dumps(checkpoint))
            await db.save_batch_checkpoint(user_id, state["url"], checkpoint["type"], all_pages, speed, "running")
        except Exception:
            logger.exception("Could not persist batch checkpoint")
        queue = asyncio.Queue()
        for job in jobs:
            queue.put_nowait(job)
        _BATCH_DASHBOARDS[_dashboard_key(status)] = {
            "mode": speed, "total": len(jobs), "completed": 0, "failed": 0,
            "skipped": skipped, "downloads": {}, "uploads": {},
            "channel_index": state.get("batch_channel_index"),
            "channel_total": state.get("batch_channel_total"),
            "channel_name": state.get("channel_name"),
            "run_uploaded_base": state.get("run_uploaded_base", 0),
            "run_skipped_base": state.get("run_skipped_base", 0),
            "run_failed_base": state.get("run_failed_base", 0),
        }
        await _dashboard_refresh(status, force=True)

        async def worker():
            nonlocal uploaded, failed
            while True:
                try:
                    url, title = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                # Hold this slot from before download until upload (or failure)
                # finishes, so slow uploads naturally pause new downloads.
                await pipeline_slots.acquire()
                try:
                    uploaded_title = None
                    for attempt in range(1, 4):
                        try:
                            uploaded_title = await _download_and_upload(
                                bot, message.chat.id, url, "best", status, caption=title,
                                download_semaphore=download_semaphore,
                                upload_semaphore=upload_semaphore,
                            )
                            break
                        except Exception as exc:
                            if attempt >= 3 or not _is_retryable_batch_error(exc):
                                raise
                            logger.warning(
                                "Retrying transient pre-upload failure %s (attempt %s/3) for %s",
                                type(exc).__name__, attempt, url,
                            )
                            if _dashboard_key(status) not in _BATCH_DASHBOARDS:
                                await _safe_edit(status, f"🔁 RETRY {attempt}/2\n🎬 {title[:180]}")
                            await asyncio.sleep(attempt * 2)
                    try:
                        await db.record_channel_upload(_canonical_video_url(url), url, uploaded_title or title)
                    except Exception:
                        logger.exception("Could not save successful upload to Neon history")
                    uploaded += 1
                    dashboard = _BATCH_DASHBOARDS.get(_dashboard_key(status))
                    if dashboard:
                        dashboard["completed"] = uploaded
                        dashboard["failed"] = failed
                        await _dashboard_refresh(status, force=True)
                    else:
                        await _safe_edit(status, f"✅ VIDEO COMPLETE\n🎬 {uploaded_title or title}\n\n📊 {uploaded}/{len(jobs)} processed  •  ⏭️ {skipped} skipped  •  ❌ {failed} failed")
                except Exception as exc:
                    failed += 1
                    logger.exception("Channel video failed: %s", url)
                    dashboard = _BATCH_DASHBOARDS.get(_dashboard_key(status))
                    if dashboard:
                        dashboard["completed"] = uploaded
                        dashboard["failed"] = failed
                        await _dashboard_refresh(status, force=True)
                    else:
                        await _safe_edit(status, f"⚠️ VIDEO FAILED\n🎬 {title[:180]}\n{type(exc).__name__}: {str(exc)[:300]}\n\n📊 {uploaded}/{len(jobs)} processed  •  ⏭️ {skipped} skipped  •  ❌ {failed} failed")
                finally:
                    pipeline_slots.release()
                    queue.task_done()
        await asyncio.gather(*(worker() for _ in range(min(worker_count, len(jobs)))))
        await _safe_edit(status, 
            f"🏁 Listing processing finished\n\n📺 {state.get('channel_name', 'Channel')}\n"
            f"✅ Uploaded: {uploaded}\n⏭️ Skipped: {skipped} (already uploaded: {already_uploaded})\n❌ Failed: {failed}\n"
            f"📄 Pages scanned: {len(pages)}"
            + (f" (selected page: {selected_page})" if selected_page is not None else "")
            + f"\n⚙️ Mode: {speed.title()} • {download_limit} downloads + {upload_limit} uploads"
        )
        if failed == 0:
            try:
                await db.set_setting(f"batch_resume_{user_id}", None)
                await db.clear_batch_checkpoint(user_id)
            except Exception:
                logger.exception("Could not clear completed batch checkpoint")
        return {
            "status": "completed" if failed == 0 else "completed_with_failures",
            "uploaded": uploaded, "failed": failed,
            "skipped": skipped, "already_uploaded": already_uploaded,
        }
    except Exception as exc:
        logger.exception("Channel batch failed for user %s", user_id)
        if status is not None:
            await _safe_edit(status, f"❌ Channel processing failed\n{type(exc).__name__}: {str(exc)[:700]}")
        return {
            "status": "error", "uploaded": uploaded, "failed": failed,
            "skipped": skipped, "already_uploaded": already_uploaded,
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
        }
    finally:
        if status is not None:
            _BATCH_DASHBOARDS.pop(_dashboard_key(status), None)
        active_batch_users.discard(user_id)
        if pending.get(user_id) is state:
            pending.pop(user_id, None)


@Client.on_message(filters.private & filters.command(["channels"], ["/", "."]))
async def saved_channels_command(bot, message):
    """Process saved channel URLs sequentially with a durable run manifest."""
    if not message.from_user:
        return
    user_id = message.from_user.id
    if user_id in active_batch_users:
        return await message.reply_text("⏳ A channel/actress batch is already running.")
    if user_id in active_saved_batches:
        return await message.reply_text("⏳ Your saved-channel batch is already running.")
    parts = message.command or []
    if len(parts) < 2 or parts[1].lower() != "all":
        search_term = ""
        if len(parts) >= 3 and parts[1].lower() == "search":
            search_term = " ".join(parts[2:]).strip().casefold()
        elif len(parts) >= 2:
            return await message.reply_text(
                "Usage:\n"
                "• /channels — choose a channel by name\n"
                "• /channels search Attackers — search channel names\n"
                "• /channels all — process every saved channel sequentially"
            )
        status = await message.reply_text("🔎 Loading channel names…")
        try:
            channels = await asyncio.to_thread(_load_saved_channels_catalog_sync)
            if search_term:
                channels = [item for item in channels if search_term in item["name"].casefold() or search_term in item["slug"].casefold()]
            if not channels:
                return await _safe_edit(
                    status,
                    "ℹ️ No matching saved channels found." if search_term else
                    "ℹ️ No channel metadata found in data/channels.json. Check the repository's channel export workflow.",
                )
            pending[user_id] = {"type": "saved_channel_picker", "channels": channels, "page": 0}
            heading = (
                f"🔎 Channel search: {search_term}\n" if search_term else "📚 Choose a Javtiful channel\n"
            )
            await _safe_edit(
                status,
                heading + f"Found {len(channels)} channel(s). Tap a name to inspect its videos.\n\n"
                "For every saved channel in sequence, use /channels all.",
                reply_markup=_channel_catalog_keyboard(channels, 0),
            )
        except Exception as exc:
            logger.exception("Could not load channel catalog")
            await _safe_edit(status, f"❌ Could not load channel names\n{type(exc).__name__}: {str(exc)[:400]}")
        return
    active_saved_batches.add(user_id)
    status = await message.reply_text("🔎 Checking saved-channel run…")
    setting_key = f"saved_channels_run_{user_id}"
    try:
        try:
            raw_run = await db.get_setting(setting_key)
            run = json.loads(raw_run) if raw_run else None
            if not isinstance(run, dict) or not isinstance(run.get("urls"), list) or not run.get("urls"):
                run = None
        except Exception as exc:
            logger.exception("Could not read saved-channel run manifest")
            return await _safe_edit(
                status,
                f"❌ Could not read saved-channel checkpoint: {type(exc).__name__}. Check DATABASE_URL.",
            )

        if run:
            urls = [str(url) for url in run["urls"] if isinstance(url, str) and url.strip()]
            next_index = max(0, min(int(run.get("next_index") or 0), len(urls)))
            await _safe_edit(
                status,
                f"🔄 Resuming saved-channel run at channel {min(next_index + 1, len(urls))}/{len(urls)}.\n"
                "The current channel may be rescanned; Neon upload history skips videos already uploaded.",
            )
            run["urls"] = urls
            run["next_index"] = next_index
        else:
            await _safe_edit(status, "🔎 Reading saved channel URLs from GitHub…")
            try:
                urls = await asyncio.to_thread(_load_saved_channel_urls_sync)
            except Exception as exc:
                logger.exception("Could not load saved channel URLs from GitHub")
                return await _safe_edit(
                    status,
                    f"❌ Could not read the data/ folder from GitHub.\n{type(exc).__name__}: {str(exc)[:500]}",
                )
            if not urls:
                return await _safe_edit(
                    status,
                    "ℹ️ No Javtiful /channel/ URLs were found in text files inside the repository's data/ folder.",
                )
            run = {
                "urls": urls,
                "next_index": 0,
                "completed_channels": 0,
                "channel_errors": [],
                "uploaded": 0,
                "failed_videos": 0,
                "skipped": 0,
                "already_uploaded": 0,
            }
            try:
                await db.set_setting(setting_key, json.dumps(run))
            except Exception as exc:
                logger.exception("Could not save saved-channel run manifest")
                return await _safe_edit(
                    status,
                    f"❌ Could not save run checkpoint: {type(exc).__name__}. Check DATABASE_URL.",
                )
            await _safe_edit(
                status,
                f"📚 Found {len(urls)} saved channel URL(s).\n"
                "🔒 Processing strictly one channel at a time.",
            )

        urls = run["urls"]
        completed = int(run.get("completed_channels") or 0)
        errors = list(run.get("channel_errors") or [])
        uploaded_total = int(run.get("uploaded") or 0)
        failed_videos_total = int(run.get("failed_videos") or 0)
        skipped_total = int(run.get("skipped") or 0)
        already_uploaded_total = int(run.get("already_uploaded") or 0)

        for index in range(int(run.get("next_index") or 0), len(urls)):
            url = urls[index]
            run["current_index"] = index
            run["current_url"] = url
            try:
                await db.set_setting(setting_key, json.dumps(run))
            except Exception:
                logger.exception("Could not update saved-channel checkpoint before channel %s", index + 1)

            try:
                info = await asyncio.to_thread(channel_extractor.inspect, url)
                state = {
                    "type": "channel",
                    "listing_type": "channel",
                    "speed": DEFAULT_PARALLEL_SPEED,
                    "url": url,
                    "channel_name": info.get("channel_name") or f"Channel {index + 1}",
                    "total_pages": max(1, int(info.get("total_pages") or 1)),
                    "total_videos": info.get("total_videos"),
                    "first_page": {"items": info.get("first_page_items") or []},
                    "skip_uploaded": True,
                    "batch_channel_index": index + 1,
                    "batch_channel_total": len(urls),
                    "run_uploaded_base": uploaded_total,
                    "run_skipped_base": skipped_total,
                    "run_failed_base": failed_videos_total,
                    "saved_channels_run": run,
                    "saved_channels_setting_key": setting_key,
                }
                pending[user_id] = state
                await _safe_edit(
                    status,
                    f"📚 SAVED CHANNELS • {DEFAULT_PARALLEL_SPEED.upper()}\n"
                    f"📺 Channel {index + 1}/{len(urls)} • {state['channel_name']}\n"
                    f"🎬 Videos: {info.get('total_videos') if info.get('total_videos') is not None else 'Unknown'}"
                    f" • 📄 Pages: {state['total_pages']}\n"
                    f"📈 Run totals: {uploaded_total} uploaded • {skipped_total} skipped • {failed_videos_total} failed",
                )
                result = await _process_channel(
                    bot, status, state, user_id, all_pages=True, status_message=status
                )
                result = result if isinstance(result, dict) else {
                    "status": "error", "uploaded": 0, "failed": 0,
                    "skipped": 0, "already_uploaded": 0,
                    "error": "Channel processor returned no result",
                }
                uploaded_total += int(result.get("uploaded") or 0)
                failed_videos_total += int(result.get("failed") or 0)
                skipped_total += int(result.get("skipped") or 0)
                already_uploaded_total += int(result.get("already_uploaded") or 0)
                if result.get("status") == "error":
                    errors.append({"url": url, "error": result.get("error", "Processing failed")})
                else:
                    completed += 1
            except Exception as exc:
                errors.append({"url": url, "error": f"{type(exc).__name__}: {str(exc)[:180]}"})
                logger.exception("Saved channel failed: %s", url)
                await _safe_edit(
                    status,
                    f"⚠️ Channel {index + 1}/{len(urls)} failed; moving to the next one.\n"
                    f"{type(exc).__name__}: {str(exc)[:250]}",
                )

            run.update({
                "next_index": index + 1,
                "completed_channels": completed,
                "channel_errors": errors,
                "uploaded": uploaded_total,
                "failed_videos": failed_videos_total,
                "skipped": skipped_total,
                "already_uploaded": already_uploaded_total,
            })
            run.pop("current_index", None)
            run.pop("current_url", None)
            run.pop("current_page", None)
            run.pop("current_channel_index", None)
            try:
                await db.set_setting(setting_key, json.dumps(run))
            except Exception:
                logger.exception("Could not persist saved-channel checkpoint after channel %s", index + 1)

        summary = (
            f"🏁 Saved-channel batch finished\n\n"
            f"📚 Total saved channels: {len(urls)}\n"
            f"✅ Channels completed: {completed}\n"
            f"📤 Videos uploaded: {uploaded_total}\n"
            f"⏭️ Videos skipped: {skipped_total} (already uploaded: {already_uploaded_total})\n"
            f"❌ Video failures: {failed_videos_total}\n"
            f"⚠️ Channels with errors: {len(errors)}\n\n"
            "Channels were processed sequentially. Run /channels again to rescan the current GitHub URL list; upload history skips successful uploads."
        )
        if errors:
            summary += "\n\nChannels with errors:\n" + "\n".join(
                f"• {item.get('url', 'Unknown URL')}" if isinstance(item, dict) else f"• {item}"
                for item in errors[:8]
            )
        await _safe_edit(status, summary)
        try:
            await db.set_setting(setting_key, None)
        except Exception:
            logger.exception("Could not clear completed saved-channel manifest")
    finally:
        active_saved_batches.discard(user_id)
        pending.pop(user_id, None)

@Client.on_callback_query(filters.regex(r"^savedch\|"))
async def saved_channel_picker_callback(bot, query):
    state = pending.get(query.from_user.id)
    if not state or state.get("type") != "saved_channel_picker":
        return await query.answer("Channel list expired. Send /channels again.", show_alert=True)
    action = query.data.split("|")
    channels = state.get("channels") or []
    if len(action) < 2:
        return await query.answer("Invalid channel selection.", show_alert=True)
    if action[1] == "noop":
        return await query.answer()
    if action[1] == "page":
        try:
            page = int(action[2])
        except (IndexError, ValueError):
            return await query.answer("Invalid page.", show_alert=True)
        page_size = 8
        total_pages = max(1, (len(channels) + page_size - 1) // page_size)
        if not 0 <= page < total_pages:
            return await query.answer("Page no longer exists.", show_alert=True)
        state["page"] = page
        await query.answer()
        return await query.message.edit_reply_markup(
            reply_markup=_channel_catalog_keyboard(channels, page)
        )
    if action[1] == "select":
        try:
            index = int(action[2])
            selected = channels[index]
        except (IndexError, ValueError, TypeError):
            return await query.answer("That channel is no longer available.", show_alert=True)
        await query.answer(f"Opening {selected.get('name', 'channel')}…")
        status = query.message
        try:
            info = await asyncio.to_thread(channel_extractor.inspect, selected["url"])
            total_pages = max(1, int(info.get("total_pages") or 1))
            channel_name = info.get("channel_name") or selected.get("name") or "Channel"
            new_state = {
                "type": "channel",
                "listing_type": "channel",
                "speed": DEFAULT_PARALLEL_SPEED,
                "url": selected["url"],
                "channel_name": channel_name,
                "total_pages": total_pages,
                "total_videos": info.get("total_videos", selected.get("video_count")),
                "first_page": {"items": info.get("first_page_items") or []},
                "skip_uploaded": True,
            }
            pending[query.from_user.id] = new_state
            count = new_state.get("total_videos")
            await _safe_edit(
                status,
                f"📺 {channel_name}\n"
                f"🎬 Videos: {count if count is not None else 'Unknown'}\n"
                f"📄 Pages: {total_pages}\n"
                f"📌 First-page entries: {len(new_state['first_page']['items'])}\n\n"
                "Choose what to process:",
                reply_markup=_channel_keyboard(new_state["skip_uploaded"], new_state["speed"]),
            )
        except Exception as exc:
            logger.exception("Could not inspect selected saved channel")
            await _safe_edit(status, f"❌ Could not inspect {selected.get('name', 'channel')}\n{type(exc).__name__}: {str(exc)[:500]}")
        return
    return await query.answer("Unknown channel-picker action.", show_alert=True)


@Client.on_message(filters.private & filters.command(["resume"], ["/", "."]))
async def resume_batch_command(bot, message):
    if not message.from_user:
        return
    user_id = message.from_user.id
    if user_id in active_batch_users or user_id in active_saved_batches:
        return await message.reply_text("⏳ Another batch is already running. Please wait for it to finish.")
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
            "skip_uploaded": True, "speed": data.get("speed", DEFAULT_PARALLEL_SPEED),
        }
        pending[user_id] = state
        await _safe_edit(status, "🔄 Resuming saved batch. Upload history will skip completed videos.")
        await _process_channel(
            bot, message, state, user_id,
            all_pages=bool(data.get("all_pages", True)),
            selected_page=data.get("selected_page"),
        )
    except Exception as exc:
        logger.exception("Could not resume saved batch")
        await _safe_edit(status, f"❌ Resume failed: {type(exc).__name__}: {str(exc)[:500]}")

@Client.on_message(filters.private & filters.command(["setdump", "setdumpoff"], ["/", "."]))
async def set_dump_command(bot, message):
    """Set or clear the destination channel for video uploads (owner only)."""
    if not message.from_user or message.from_user.id != Config.OWNER_ID:
        return await message.reply_text("⛔ Only the bot owner can change the dump channel.")

    parts = message.command or []
    command_name = (parts[0] if parts else "").lower()
    if command_name == "setdumpoff":
        parts = ["setdump", "off"]
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
    if message.from_user and (message.from_user.id in active_batch_users or message.from_user.id in active_saved_batches):
        return await message.reply_text("⏳ A batch is running. Wait until it finishes before starting a single-video download.")
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
            return await _safe_edit(status, "❌ No downloadable stream was found for this video.")
        qualities = list(video.qualities)
        qualities.sort(key=lambda value: int(re.search(r"\d+", value).group()) if re.search(r"\d+", value) else 0, reverse=True)
        qualities = qualities[:8]
        pending[message.from_user.id] = {"type": "video", "url": url, "qualities": qualities}
        await _safe_edit(status, 
            f"🎬 {video.title or 'Video'}\n\n"
            f"⏱ Duration: {_duration_text(video.duration)}\n"
            f"🎚 Available qualities: {', '.join(qualities)}\n\n"
            "Choose a quality to download and upload here:",
            reply_markup=_quality_keyboard(qualities),
        )
    except Exception as exc:
        logger.exception("JAVDL video extraction failed")
        await _safe_edit(status, f"❌ Extraction failed\n{type(exc).__name__}: {str(exc)[:700]}")


@Client.on_message(filters.private & filters.command(["channel"], ["/", "."]))
async def javtiful_channel_command(bot, message):
    if message.from_user and (message.from_user.id in active_batch_users or message.from_user.id in active_saved_batches):
        return await message.reply_text("⏳ A batch is already running. Wait until it finishes before opening another listing.")
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
            "speed": DEFAULT_PARALLEL_SPEED,
            "url": url,
            "channel_name": info.get("channel_name") or "Channel",
            "total_pages": total_pages,
            "total_videos": info.get("total_videos"),
            "first_page": {"items": info.get("first_page_items") or []},
            "skip_uploaded": True,
        }
        pending[message.from_user.id] = state
        count = info.get("total_videos")
        await _safe_edit(status, 
            f"📺 {state['channel_name']}\n"
            f"🎬 Videos: {count if count is not None else 'Unknown'}\n"
            f"📄 Pages: {total_pages}\n"
            f"📌 First-page entries: {len(state['first_page']['items'])}\n\n"
            "Choose what to process:",
            reply_markup=_channel_keyboard(state.get("skip_uploaded", True), state.get("speed", DEFAULT_PARALLEL_SPEED)),
        )
    except Exception as exc:
        logger.exception("JAVDL channel inspection failed")
        await _safe_edit(status, f"❌ Channel inspection failed\n{type(exc).__name__}: {str(exc)[:700]}")


@Client.on_message(filters.private & filters.command(["actress"], ["/", "."]))
async def actress_command(bot, message):
    if message.from_user and (message.from_user.id in active_batch_users or message.from_user.id in active_saved_batches):
        return await message.reply_text("⏳ A batch is already running. Wait until it finishes before opening another listing.")
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
        state["speed"] = DEFAULT_PARALLEL_SPEED
        state["listing_type"] = "actress"
        state["skip_uploaded"] = True
        pending[message.from_user.id] = state
        count = info.get("total_videos")
        await _safe_edit(status, 
            f"🎭 {state['channel_name']}\n"
            f"🎬 Videos: {count if count is not None else 'Unknown'}\n"
            f"📄 Pages: {total_pages}\n"
            f"📌 First-page entries: {len(state['first_page']['items'])}\n\n"
            "Choose what to process:",
            reply_markup=_channel_keyboard(state.get("skip_uploaded", True), state.get("speed", DEFAULT_PARALLEL_SPEED)),
        )
    except Exception as exc:
        logger.exception("Javtiful actress listing inspection failed")
        await _safe_edit(status, f"❌ Actress listing inspection failed\n{type(exc).__name__}: {str(exc)[:700]}")


@Client.on_message(filters.private & filters.text)
async def custom_page_number_message(bot, message):
    """Handle a page number after the user chooses the custom-page option."""
    if not message.from_user:
        return
    state = pending.get(message.from_user.id)
    if not state or not state.get("awaiting_custom_page"):
        return

    value = (message.text or "").strip()
    if value.lower() in {"/cancel", ".cancel"}:
        state["awaiting_custom_page"] = False
        return await message.reply_text("✅ Custom page selection cancelled.")
    if not value.isdigit():
        return await message.reply_text(
            f"Send a page number from 1 to {min(int(state.get('total_pages') or 1), MAX_CHANNEL_PAGES)}, or send /cancel."
        )
    page_number = int(value)
    total_pages = max(1, min(int(state.get("total_pages") or 1), MAX_CHANNEL_PAGES))
    if not 1 <= page_number <= total_pages:
        return await message.reply_text(
            f"❌ Invalid page. Choose a page from 1 to {total_pages}."
        )
    if state.get("processing"):
        return await message.reply_text("⏳ This listing is already being processed.")

    state["awaiting_custom_page"] = False
    state["processing"] = True
    await message.reply_text(f"🎯 Starting page {page_number}…")
    await _process_channel(
        bot, message, state, message.from_user.id, selected_page=page_number
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

    if action[1] == "speed":
        if state.get("type") != "channel":
            return await query.answer("This listing selection has expired.", show_alert=True)
        if state.get("processing"):
            return await query.answer("A batch is already running.", show_alert=True)
        cycle = {"safe": "balanced", "balanced": "fast", "fast": "safe"}
        state["speed"] = cycle.get(state.get("speed", DEFAULT_PARALLEL_SPEED), DEFAULT_PARALLEL_SPEED)
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
            reply_markup=_channel_keyboard(state["skip_uploaded"], state.get("speed", DEFAULT_PARALLEL_SPEED))
        )

    if action[1] == "custom_page":
        if state.get("type") != "channel":
            return await query.answer("This listing selection has expired.", show_alert=True)
        if state.get("processing"):
            return await query.answer("This batch is already running.", show_alert=True)
        total_pages = max(1, min(int(state.get("total_pages") or 1), MAX_CHANNEL_PAGES))
        state["awaiting_custom_page"] = True
        await query.answer("Send a page number")
        return await query.message.edit_text(
            f"🎯 {state.get('channel_name', 'Listing')}\n"
            f"Available pages: 1–{total_pages}\n\n"
            "Send the page number you want to download (for example, 3)."
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
