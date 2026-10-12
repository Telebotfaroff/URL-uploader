# Javtiful → GoFile Telegram Bot

A minimal, owner-only Telegram bot that searches Javtiful by video code, downloads a selected result, and uploads it to GoFile as a guest.

## Required secrets

- `BOT_TOKEN` — Telegram bot token.
- `OWNER_ID` — your numeric Telegram user ID.

No database, Neon, `API_ID`, `API_HASH`, or GoFile token is required.

## Run in Google Colab

Open `URL_Uploader_Gofile_Colab.ipynb`, add the two secrets above to Colab Secrets, enable notebook access, and run the cells in order.

## Included features

- Search results with thumbnails and previous/next navigation.
- Download and guest-upload buttons.
- Download/upload progress, speed, and ETA when available.
- Owner-only commands and callbacks.
- In-memory search sessions; no database or persistent history.

Javtiful may change its site or playback format, so verify live search/download behavior in Colab. Source checks do not prove that the live integration works.
