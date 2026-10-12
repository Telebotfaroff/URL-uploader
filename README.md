# Javtiful → GoFile Telegram Bot

A lightweight, personal-use Telegram bot that searches Javtiful by video code, lets you browse matching results, downloads the selected video, and uploads it to GoFile using guest upload.

> Only download and upload content you own or are authorized to handle. Use this bot privately and comply with the source site's terms and applicable law.

## Features

- Search Javtiful using codes such as `HNTRZ-010`.
- Browse matching results with thumbnails and Previous/Next buttons.
- Download a selected result and upload it to GoFile without a GoFile account token.
- Show download/upload progress, transfer speed, and ETA when available.
- Restrict bot commands and button callbacks to your Telegram user ID.
- Keep search sessions in memory only; no database or persistent history.

## Requirements

You need the `BOT_TOKEN` environment variable / Colab Secret:

| Secret | Description |
| --- | --- |
| `BOT_TOKEN` | Token for your Telegram bot from BotFather. |
| `OWNER_ID` | Optional override; defaults to `7367490186` in `gofile_bot.py`. |

No database, Neon, `API_ID`, `API_HASH`, or GoFile API token is required. FFmpeg is installed by the Colab notebook to support HLS streams.

## Run in Google Colab

1. Open the [GoFile Colab notebook](https://colab.research.google.com/github/Telebotfaroff/URL-uploader/blob/gofile/URL_Uploader_Gofile_Colab.ipynb).
2. In Colab, open **Secrets** and add `BOT_TOKEN` and enable notebook access. The owner ID `7367490186` is preset in the code.
3. Run **Setup** to install dependencies and clone this branch.
4. Run **Check** to compile the source and execute the basic regression tests.
5. Run **Start bot** and keep that cell running.
6. Open your bot in Telegram and send a video code, for example `HNTRZ-010`. Choose a result, then press Download.

## Project files

- `gofile_bot.py` — Telegram handlers, owner checks, buttons, and progress updates.
- `gofile_search.py` — Javtiful code search, result parsing, and pagination.
- `gofile_media.py` — video extraction and downloading.
- `gofile_uploader.py` — GoFile guest upload.
- `plugins/javdl_core/` — Javtiful media extraction components.
- `URL_Uploader_Gofile_Colab.ipynb` — setup, checks, and run cells.
- `tests/test_gofile_bot.py` — basic regression tests.

## Troubleshooting

- **Missing secret:** confirm the `BOT_TOKEN` secret name is exact and notebook access is enabled. `OWNER_ID` is optional because the code has a default.
- **Bot rejects you:** the preset owner ID is `7367490186`; set an `OWNER_ID` environment variable only if you want to override it.
- **No search results / download fails:** Javtiful's HTML and playback system may change or restrict access. A successful source check does not guarantee live site compatibility.
- **Upload fails:** retry later; GoFile guest upload availability and limits may change.

## Scope

This branch is intentionally separate from `main` and contains only the files required for the GoFile bot. Search sessions are temporary and disappear when the process restarts. The bot does not save a database or persistent search history.
