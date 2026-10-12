from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram import enums

class Translation(object):

    START_TEXT = """👋 Hello <b>{}</b>!

<blockquote>I'm your URL Uploader Bot. Send me a direct file/video link and I'll upload it to Telegram.</blockquote>

<b>📚 COMMAND MENU</b>
<blockquote>
/start — Show this welcome menu
/help — Show all commands and usage
/about — About the bot
/settings — Configure upload preferences
/caption — Learn how to edit a media caption

<b>🎬 Javtiful Downloader</b>
/vid &lt;video URL&gt; — Choose quality and upload one video
/channel &lt;channel URL&gt; — Browse or batch-download channel videos
/actress &lt;actress URL&gt; — Browse or batch-download an actress listing
/resume — Resume a saved channel/actress batch after a restart
/setdump — View dump-channel setting (owner only)
/setdump &lt;@channel or ID&gt; — Set the dump channel
/setdumpoff — Disable dump-channel uploads (no arguments needed)\n/setdump off — Also disables dump-channel uploads
</blockquote>

<b>📤 Upload a file</b>
Send a direct link, optionally followed by a new filename:
<code>https://example.com/file.mp4 | NewName.mp4</code>

Use the buttons below for settings and more help.
"""

    

    HELP_TEXT = """
<b>📚 All Commands</b>
<blockquote>
<b>General</b>
/start — Show the welcome menu and command list
/help — Show this command guide
/about — About the bot
/settings — Customize upload settings
/caption — Show how to change a media caption

<b>Direct URL Upload</b>
Send a direct file/video URL to the bot.
Optional rename format:
<code>https://example.com/file.mp4 | NewName.mp4</code>
Choose the upload format when prompted.

<b>Javtiful Downloader</b>
/vid &lt;video URL&gt; — Inspect a video, choose quality, then upload it
/channel &lt;channel URL&gt; — Inspect a channel; download the first page or all available pages
/actress &lt;actress URL&gt; — Inspect an actress/model listing; download the first page or all available pages
/resume — Restore the last saved batch after a Colab restart; completed uploads are skipped using history

<b>Dump Channel (owner only)</b>
/setdump — Show the current dump channel
/setdump &lt;@channelusername or -100... ID&gt; — Set the destination channel
/setdump off — Disable the dump channel

<b>Batch controls</b>
On the /channel and /actress buttons, tap the speed button to cycle:
🛡 Safe: 1 download + 1 upload
⚖️ Balanced: 2 downloads + 2 uploads
⚡ Fast: 3 downloads + 3 uploads
Toggle duplicate skipping before starting a batch.

<b>Notes</b>
• The bot is private and only its configured owner can use it.
• Keep your Colab runtime running during a batch. After a restart, use /resume to rescan the saved listing.
</blockquote>
"""
    
    ABOUT_TEXT = """
╭───────────────⍟
│ 📛 **Bot Name** : URL Uploader Bot
│ 📢 **Framework** : <a href="https://docs.pyrogram.org/">PyroBlock 2.3.82</a>
│ 💻 **Language** : <a href="https://www.python.org">Python 3.14.7</a>
│ 💾 **Database** : <a href="https://cloud.mongodb.com">MongoDB</a>
│ 🚨 **Support Group** : <a href="https://t.me/NT_BOTS_SUPPORT">NT Support</a>
│ 🥏 **Channel** : <a href="https://t.me/NT_BOT_CHANNEL">NT Bot Channel</a>
│ 👨‍💻 **Creator** : @NT_BOT_CHANNEL
╰───────────────⍟
"""


    PROGRESS = """
┃ 📦 Progress : {0}%
┃ ✅ Done    : {1}
┃ 📁 Total   : {2}
┃ 🚀 Speed   : {3}/s
┃ 🕒 Time    : {4}
┗━━━━━━━━━━━━━━━━━━━━━
"""

    PROGRES = """
`{}`\n{}"""


    INFO_TEXT = """
╭───────────────〄
│ 📛 First Name  : <b>{}</b>
│ 📛 Last Name   : <b>{}</b>
│ 👤 Username    : <b>@{}</b>
│ 🆔 Telegram ID : <code>{}</code>
│ 🖇️ Profile Link: <b>{}</b>
│ 📡 DC          : <b>{}</b>
│ 💮 Language    : <b>{}</b>
│ 💫 Status      : <b>{}</b>
╰──────────────────〄
"""


    START_BUTTONS = InlineKeyboardMarkup(
        [[
        InlineKeyboardButton('🛠️ Settings', callback_data='OpenSettings', style=enums.ButtonStyle.SUCCESS)
        ],[
        InlineKeyboardButton('🤝 Help', callback_data='help', style=enums.ButtonStyle.PRIMARY),
        InlineKeyboardButton('ℹ️ About', callback_data='about', style=enums.ButtonStyle.PRIMARY)
        ],[
        InlineKeyboardButton('✖️ Close', callback_data='close', style=enums.ButtonStyle.DANGER)
        ]]
    )
    HELP_BUTTONS = InlineKeyboardMarkup(
        [[
        InlineKeyboardButton('🛠️ Settings', callback_data='OpenSettings', style=enums.ButtonStyle.SUCCESS)
        ],[
        InlineKeyboardButton('🔙 Back', callback_data='home', style=enums.ButtonStyle.PRIMARY),
        InlineKeyboardButton('ℹ️ About', callback_data='about', style=enums.ButtonStyle.PRIMARY)
        ],[
        InlineKeyboardButton('✖️ Close', callback_data='close', style=enums.ButtonStyle.DANGER)
        ]]
    )
    ABOUT_BUTTONS = InlineKeyboardMarkup(
        [[
        InlineKeyboardButton('🛠️ Settings', callback_data='OpenSettings', style=enums.ButtonStyle.SUCCESS)
        ],[
        InlineKeyboardButton('🔙 Back', callback_data='home', style=enums.ButtonStyle.PRIMARY),
        InlineKeyboardButton('🤝 Help', callback_data='help', style=enums.ButtonStyle.PRIMARY)
        ],[
        InlineKeyboardButton('✖️ Close', callback_data='close', style=enums.ButtonStyle.DANGER)
        ]]
    )
    PLANS_BUTTONS = InlineKeyboardMarkup(
        [[
        InlineKeyboardButton('ℹ️ About', callback_data='about', style=enums.ButtonStyle.SUCCESS)
        ],[
        InlineKeyboardButton('🔙 Back', callback_data='home', style=enums.ButtonStyle.PRIMARY),
        InlineKeyboardButton('🤝 Help', callback_data='help', style=enums.ButtonStyle.PRIMARY)
        ],[
        InlineKeyboardButton('✖️ Close', callback_data='close', style=enums.ButtonStyle.DANGER)
        ]]
   )
    BUTTONS = InlineKeyboardMarkup(
        [[
        InlineKeyboardButton('✖️ Close', callback_data='close', style=enums.ButtonStyle.DANGER)
        ]]
    )
    INCORRECT_REQUEST = """Error"""
    DOWNLOAD_FAILED = "🔴 Error 🔴"
    TEXT = "Sᴇɴᴅ ᴍᴇ ʏᴏᴜʀ ᴄᴜsᴛᴏᴍ ᴛʜᴜᴍʙɴᴀɪʟ"
    IFLONG_FILE_NAME = " Only 64 characters can be named . "
    RENAME_403_ERR = "Sorry. You are not permitted to rename this file."
    ABS_TEXT = " Please don't be selfish."
    FORMAT_SELECTION = "<b>Sᴇʟᴇᴄᴛ Yᴏᴜʀ Fᴏʀᴍᴀᴛ 👇</b>\n"
    SET_CUSTOM_USERNAME_PASSWORD = """<b>🎥 Vɪᴅᴇᴏ = Uᴘʟᴏᴀᴅ As Sᴛʀᴇᴀᴍʙʟᴇ</b>\n\n<b>📂 Fɪʟᴇ = Uᴘʟᴏᴀᴅ As Fɪʟᴇ</b>\n\n<b>👮‍♂ Pᴏᴡᴇʀᴇᴅ Bʏ :</b> @NT_BOT_CHANNEL"""
    DOWNLOAD_START = "📥 Downloading... 📥\n\nFile Name: {}"
    UPLOAD_START = "📤 Uploading... 📤"
    RCHD_BOT_API_LIMIT = "size greater than maximum allowed size (50MB). Neverthless, trying to upload."
    RCHD_TG_API_LIMIT = "Downloaded in {} seconds.\nDetected File Size: {}\nSorry. But, I cannot upload files greater than 2000MB due to Telegram API limitations.\n\nUse 4GB @UploaderXNTBot"
    AFTER_SUCCESSFUL_UPLOAD_MSG_WITH_TS = "**𝘛𝘏𝘈𝘕𝘒𝘚 𝘍𝘖𝘙 𝘜𝘚𝘐𝘕𝘎 𝘔𝘌** 🥰"
    SAVED_CUSTOM_THUMB_NAIL = "🖼 Thumbnail saved permanently."
    DEL_ETED_CUSTOM_THUMB_NAIL = "🗑 Thumbnail deleted successfully."
    FF_MPEG_DEL_ETED_CUSTOM_MEDIA = "✅ Media cleared succesfully."
    CUSTOM_CAPTION_UL_FILE = " "
    NO_CUSTOM_THUMB_NAIL_FOUND = "😅 No thumbnail found."
    NO_VOID_FORMAT_FOUND = "ERROR... <code>{}</code>"
    FILE_NOT_FOUND = "Error, File not Found!!"
    FF_MPEG_RO_BOT_AD_VER_TISE_MENT = "Join : @NT_BOT_CHANNEL \n For the list of Telegram bots. "
    ADD_CAPTION_HELP = """Select an uploaded file/video or forward me <b>Any Telegram File</b> and just write the text you want to be on the file <b>as a reply to the file</b> and the text you wrote will be attached as the caption! 🤩
    
Example: <a href='https://te.legra.ph/file/ecf5297246c5fb574d1a0.jpg'>See This!</a> 👇"""
