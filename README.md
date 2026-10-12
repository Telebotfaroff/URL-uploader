# Javtiful → GoFile Telegram Bot

A Telegram bot that searches Javtiful by video code, lets you browse matching results, downloads the selected video, and uploads it to GoFile using guest upload.

## Features

- Search Javtiful using full codes such as `HNTRZ-010` or prefixes such as `IPZZ`.
- Browse matching results with thumbnails and Previous/Next buttons.
- Download a selected result and upload it to GoFile without a GoFile account token.
- Extract 5, 10, 15, or 20 evenly spaced screenshots from a selected video using the **SS** button.
- For **SS**, **DL + SS**, and bulk screenshot jobs, choose whether screenshots go to the current chat or private Telegram channel `-1003963567446`.
- Bulk mode supports up to 20 codes separated by new lines, commas, or semicolons. Choose **Download all**, **Screenshots all**, or **Download + Screenshots all**.
- **Parallel bulk pipeline:** different videos can be in progress concurrently. Each video follows download → screenshots (if selected) → GoFile upload (if selected), with independent limits for downloads, screenshot extraction, and uploads.
- Configure worker counts using `/parallel`: 1–5 download workers, 1–3 GoFile upload workers, and 1–2 screenshot workers. Defaults: 3 downloads, 2 uploads, 1 screenshot worker. Settings apply to the next batch.
- Active video jobs are capped by the download-worker count, and temporary files are cleaned up after each video to limit Colab storage use.
- Per-video download and upload progress messages show transfer speed and ETA when available.
- Bulk successes are consolidated into **one compact Telegram status message**, for example: `START-618✅ START-627✅ IPZZ-950✅`. Individual progress messages are removed after successful completion.
- Display the GoFile URL as visible, copyable text in the result message.
- Restrict bot commands and button callbacks to the configured owner Telegram ID.
- Keep search sessions in memory only; no database or persistent history is required.

## Requirements

You need your Telegram bot token, entered directly in the Colab Setup cell.

| Setting | Description |
| --- | --- |
| `BOT_TOKEN` | Entered at runtime in the Setup cell; input is hidden and the token is not stored in the notebook source. |
| `OWNER_ID` | Optional environment variable; defaults to `7367490186` in `gofile_bot.py`. |

No database, Neon, `API_ID`, `API_HASH`, or GoFile API token is required. FFmpeg is installed by the Colab notebook to support HLS streams. Add the bot to private channel `-1003963567446` with permission to post messages before choosing the private-channel screenshot destination.

## Run in Google Colab

1. Open the [GoFile Colab notebook](https://colab.research.google.com/github/Telebotfaroff/URL-uploader/blob/gofile/URL_Uploader_Gofile_Colab.ipynb).
2. Run **Setup** and paste your bot token when prompted. Input is hidden; the owner ID `7367490186` is preset in the bot code.
3. Run **Check** to compile the bot modules and execute the GoFile regression tests.
4. Run **Start bot** and keep that cell running.
5. Open the bot in Telegram and send a full video code such as `IPZZ-989` or a prefix such as `IPZZ`. Choose a result, then press **DL** to upload to GoFile, **SS** to extract screenshots, or **DL + SS** to do both.
6. For bulk mode, send multiple full codes (maximum 20), then choose **Download all**, **Screenshots all**, or **Download + Screenshots all**. For screenshot actions, select the screenshot quantity and destination.
7. Use `/parallel` to adjust download, screenshot, and upload worker counts. The settings apply to the next batch. The pipeline overlaps different videos while keeping active video jobs bounded.
8. To send screenshots to the private channel, add the bot to channel `-1003963567446` and grant permission to post messages.

## Project files

- `gofile_bot.py` — Telegram handlers, owner checks, bulk pipeline, buttons, and progress updates.
- `gofile_search.py` — Javtiful code search, result parsing, and pagination.
- `gofile_media.py` — video extraction and downloading.
- `gofile_uploader.py` — GoFile guest upload.
- `plugins/javdl_core/` — Javtiful media extraction components.
- `URL_Uploader_Gofile_Colab.ipynb` — Colab setup, checks, and run cells.
- `tests/test_gofile_bot.py` — regression tests for bulk success formatting, worker settings, screenshot destinations, and parsing helpers.

## Troubleshooting

- **Missing token:** rerun Setup and enter the bot token. It is held in the active Colab runtime; restarting the runtime requires entering it again.
- **Bot rejects your commands:** the preset owner ID is `7367490186`. Set an `OWNER_ID` environment variable only if you want to override it.
- **No search results / download fails:** the source site's HTML or playback behavior may have changed, or the source may be unavailable.
- **Upload fails:** retry later; GoFile guest upload availability and limits can change.
- **Private-channel screenshot delivery fails:** check that the bot is a member of channel `-1003963567446` and has permission to post messages.

## Scope

The `gofile` branch is separate from `main` and contains the GoFile bot implementation. Search sessions are temporary and disappear when the process restarts. The bot does not require a database or persistent search history.
