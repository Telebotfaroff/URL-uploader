# Javtiful → GoFile Telegram bot

This is a separate, database-free bot entry point for the \`gofile\` branch. It does not use Pyrogram, Telegram API_ID/API_HASH, Neon, or any database.

## Required environment variables

- \`BOT_TOKEN\`: Telegram bot token from BotFather.
- \`OWNER_ID\`: your numeric Telegram user ID.

## Run in Colab

Use the dedicated \`URL_Uploader_Gofile_Colab.ipynb\` notebook. Add only \`BOT_TOKEN\` and \`OWNER_ID\` to Colab Secrets, enable notebook access, and run the cells in order.

## Features

- Search Javtiful by video code, such as \`STAR-765\`.
- Browse results with Previous/Next buttons and thumbnails.
- Download a selected video using the existing Javtiful media extractor.
- Upload to GoFile without an account token (guest upload).
- Show download/upload progress, transfer rate, and ETA when the source exposes enough information.
- Restrict commands and callbacks to \`OWNER_ID\`.
- Store search sessions in memory only; no database or persistent history.

The Javtiful website may change its HTML or playback system. Search and extraction must be tested live in the running environment; no live site test is implied by source compilation alone.
