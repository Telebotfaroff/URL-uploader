import math
import time

from pyrogram import enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from plugins.script import Translation


async def progress_for_pyrogram(current, total, ud_type, message, start, user_id=None):
    now = time.monotonic()
    elapsed = max(now - start, 0.001)
    percentage = min(100.0, current * 100 / total) if total and total > 0 else 0.0
    speed = current / elapsed if elapsed > 0 else 0.0
    eta_ms = round(max(total - current, 0) / speed * 1000) if total and speed > 0 else 0
    elapsed_text = TimeFormatter(round(elapsed * 1000))
    eta_text = TimeFormatter(eta_ms) if eta_ms else "0 s"

    progress = "┏━━━━✦[{0}{1}]✦━━━━".format(
        "".join(["▣" for _ in range(min(10, math.floor(percentage / 10)))]),
        "".join(["▢" for _ in range(max(0, 10 - math.floor(percentage / 10)))])
    )
    tmp = progress + Translation.PROGRESS.format(
        round(percentage, 2),
        humanbytes(current),
        humanbytes(total),
        humanbytes(speed),
        eta_text,
    )
    try:
        buttons = []
        if user_id is not None:
            buttons = [[InlineKeyboardButton(
                "⛔ Cancel",
                callback_data=f"cancel_download+{int(user_id)}",
                style=enums.ButtonStyle.DANGER,
            )]]
        await message.edit(
            text=Translation.PROGRES.format(ud_type, tmp),
            parse_mode=enums.ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        )
    except Exception:
        # Telegram may reject rapid edits or an edit after the message changes.
        pass


def humanbytes(size):
    if not size:
        return "0 B"
    power = 1024
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    value = float(size)
    index = 0
    while value >= power and index < len(units) - 1:
        value /= power
        index += 1
    return f"{round(value, 2)} {units[index]}"


def TimeFormatter(milliseconds: int) -> str:
    seconds, milliseconds = divmod(max(0, int(milliseconds)), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds:
        parts.append(f"{seconds}s")
    if milliseconds and not parts:
        parts.append(f"{milliseconds}ms")
    return ", ".join(parts) or "0 s"
