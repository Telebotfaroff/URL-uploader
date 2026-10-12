# Javtiful → GoFile Telegram Bot

A lightweight, personal-use Telegram bot that searches Javtiful by video code, lets you browse matching results, downloads the selected video, and uploads it to GoFile using guest upload.

> Only download and upload content you own or are authorized to handle. Use this bot privately and comply with the source site's terms and applicable law.

## Features

- Search Javtiful using full codes such as `HNTRZ-010` or prefixes such as `IPZZ`.
- Browse matching results with thumbnails and Previous/Next buttons.
- Download a selected result and upload it to GoFile without a GoFile account token.
- Extract 5, 10, 15, or 20 evenly spaced screenshots from a selected video using the **SS** button.
- For **SS**, **DL + SS**, and bulk screenshot jobs, choose whether screenshots go to the current chat or private Telegram channel `-1003963567446`.
- Bulk mode: send up to 20 video codes separated by new lines, commas, or semicolons, then choose **Download all**, **Screenshots all**, or **Download + Screenshots all**. Screenshot actions let you choose quantity and destination. Bulk jobs run through a bounded parallel pipeline and report each video's progress, success/failure, and GoFile URL.
- Display the GoFile URL as visible, copyable text in the result message.
- Show separate per-video download/upload progress, transfer speed, and ETA when available.\n- Configure bulk parallelism with `/parallel`: 1–5 download workers, 1–3 GoFile upload workers, and 1–2 screenshot workers. Defaults are 3 downloads, 2 uploads, and 1 screenshot worker.\n- Bound active video jobs by the download-worker count and clean up each video's temporary files after processing to limit Colab disk usage.
- Restrict bot commands and button callbacks to your Telegram user ID.
- Keep search sessions in memory only; no database or persistent history.

## Requirements

You need your Telegram bot token, entered directly in the Colab Setup cell:

| Secret | Description |
| --- | --- |
| `BOT_TOKEN` | Entered at runtime in the Setup cell; input is hidden and it is not stored in the notebook source. |
| `OWNER_ID` | Optional override; defaults to `7367490186` in `gofile_bot.py`. |

No database, Neon, `API_ID`, `API_HASH`, or GoFile API token is required. FFmpeg is installed by the Colab notebook to support HLS streams. Add the bot to private channel `-1003963567446` with permission to post messages before choosing the private-channel destination.

## Run in Google Colab

1. Open the [GoFile Colab notebook](https://colab.research.google.com/github/Telebotfaroff/URL-uploader/blob/gofile/URL_Uploader_Gofile_Colab.ipynb).
2. Run **Setup** and paste your bot token when prompted (the input is hidden). The owner ID `7367490186` is preset in the code.
3. Run **Setup** to install dependencies and clone this branch.
4. Run **Check** to compile the source and execute the basic regression tests.
5. Run **Start bot** and keep that cell running.
7. Add the bot to private channel `-1003963567446` and grant it permission to post messages if you want channel delivery.
8. Open your bot in Telegram and send a full video code such as `IPZZ-989` or a prefix such as `IPZZ`. Choose a result, then press **DL** to upload to GoFile, **SS** to extract screenshots, or **DL + SS** to do both. Screenshot quantity buttons let you choose **here** or **private channel**.
9. For bulk mode, send multiple full codes separated by new lines, commas, or semicolons (maximum 20). Choose **Download all**, **Screenshots all**, or **Download + Screenshots all**, then select screenshot quantity and destination where applicable. Use `/parallel` to adjust download, screenshot, and upload workers; settings apply to the next batch. The pipeline overlaps different videos while keeping the number of active video jobs bounded.

## Project files

- `gofile_bot.py` — Telegram handlers, owner checks, buttons, and progress updates.
- `gofile_search.py` — Javtiful code search, result parsing, and pagination.
- `gofile_media.py` — video extraction and downloading.
- `gofile_uploader.py` — GoFile guest upload.
- `plugins/javdl_core/` — Javtiful media extraction components.
- `URL_Uploader_Gofile_Colab.ipynb` — setup, checks, and run cells.
- `tests/test_gofile_bot.py` — basic regression tests.

## Troubleshooting

- **Missing secret:** rerun Setup and enter the bot token when prompted. The token is held in the active Colab runtime; restarting the runtime requires entering it again.
- **Bot rejects you:** the preset owner ID is `7367490186`; set an `OWNER_ID` environment variable only if you want to override it.
- **No search results / download fails:** Javtiful's HTML and playback system may change or restrict access. A successful source check does not guarantee live site compatibility.
- **Upload fails:** retry later; GoFile guest upload availability and limits may change.

## Scope

This branch is intentionally separate from `main` and contains only the files required for the GoFile bot. Search sessions are temporary and disappear when the process restarts. The bot does not save a database or persistent search history.
