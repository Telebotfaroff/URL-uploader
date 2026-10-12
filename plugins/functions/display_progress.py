
import math
import time
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from plugins.script import Translation
from pyrogram import enums 




async def progress_for_pyrogram(current, total, ud_type, message, start, user_id=None):
    now = time.time()
    diff = max(now - start, 0.001)
    if not hasattr(progress_for_pyrogram, "_last_update"):
        progress_for_pyrogram._last_update = {}
    message_key = id(message)
    if now - progress_for_pyrogram._last_update.get(message_key, 0) < 2 and current != total:
        return
    progress_for_pyrogram._last_update[message_key] = now
    percentage = min(100.0, current * 100 / total) if total else 0.0
    speed = current / diff if diff > 0 else 0.0
    elapsed_time = round(diff * 1000)
    time_to_completion = round(max(total - current, 0) / speed * 1000) if total and speed > 0 else 0
    estimated_total_time = elapsed_time + time_to_completion

        elapsed_time = TimeFormatter(milliseconds=elapsed_time)
        estimated_total_time = TimeFormatter(milliseconds=estimated_total_time)

        progress = "┏━━━━✦[{0}{1}]✦━━━━".format(
            ''.join(["▣" for i in range(math.floor(percentage / 10))]),
            ''.join(["▢" for i in range(10 - math.floor(percentage / 10))])
        )

        tmp = progress + Translation.PROGRESS.format(
            round(percentage, 2),
            humanbytes(current),
            humanbytes(total),
            humanbytes(speed),
            estimated_total_time if estimated_total_time != '' else "0 s"
        )
        try:
            await message.edit(
              text= Translation.PROGRES.format(
              ud_type,
              tmp
                ),
                parse_mode=enums.ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [ 
                        InlineKeyboardButton('⛔ Cancel', callback_data=f"cancel_download+{user_id}" if user_id is not None else "cancel_download+unknown", style=enums.ButtonStyle.DANGER)
                       ]
                   ]
                 )
            )
        except:
            pass


def humanbytes(size):
    # https://stackoverflow.com/a/49361727/4723940
    # 2**10 = 1024
    if not size:
        return ""
    power = 2 ** 10
    n = 0
    Dic_powerN = {0: ' ', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return str(round(size, 2)) + " " + Dic_powerN[n] + 'B'


def TimeFormatter(milliseconds: int) -> str:
    seconds, milliseconds = divmod(int(milliseconds), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    tmp = ((str(days) + "d, ") if days else "") + \
          ((str(hours) + "h, ") if hours else "") + \
          ((str(minutes) + "m, ") if minutes else "") + \
          ((str(seconds) + "s, ") if seconds else "") + \
          ((str(milliseconds) + "ms, ") if milliseconds else "")
    return tmp[:-2]
