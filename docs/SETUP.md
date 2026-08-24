# DropsofKnowledge — Setup Guide

## Overview
This repository orchestrates a pipeline for turning Islamic lecture transcripts into short-form video clips for YouTube (primary) and secondary platforms.

## Workflow

### 1. Fetch & Transcribe
- Audio is fetched from Telegram (Task 3) or provided as `transcript.json`.
- Transcription uses the **Groq free tier** model `whisper-large-v3` (no credit card needed).
- Environment: `.env` must contain `GROQ_API_KEY` (and optionally `TRANSCRIBE_BASE_URL`, `TRANSCRIBE_MODEL`).
- Run: `python scripts/transcribe.py --login` (one-time OAuth) then `python scripts\transcribe.py <audio.amr>`.

### 2. Generate Plan (Task 7)
- The transcript is fed to `scripts/generate_clip_plan.py` with a free LLM API (Groq `openai/gpt-oss-120b`).
- The LLM returns candidates with **your criteria prompt**: first-3-seconds rule, Islamic benefit, completeness, mute‑watchability, scoring hierarchy, duration targets.
- Output is a **staging file**: `state/clip_plan_staging_*.json`. Nothing is written to the renderer yet.
- Review the staging file. If satisfied, promote it:
  ```bash
  python scripts/generate_clip_plan.py --approve state/clip_plan_staging_20260824_152710.json --to Projects/ad_daa_0059_test
  ```
- The command refuses if a `clip_plan.json` already exists in the target folder (safe default).

### 3. Review / Approve (Task 9 — Dashboard)
- Run the interactive dashboard:
  ```bash
  python scripts/status.py
  ```
- The dashboard shows:
  - Pending clip plans (staged files in `state/`)
  - Pending publish queue entries (drafts in `state/publish_queue.json`)
  - Missing credential keys (compares `.env` vs `.env.example`)
  - Last 5 completed renders/uploads
- Menu actions:
  - **Number** → approve the selected clip plan or queue entry
  - **e Number** → open the staging file in Notepad for editing
  - **q** → quit

### 4. Render (Untouched Renderer)
- After approval, place the approved `clip_plan.json` alongside `audio.amr` and `transcript.json` in a project folder.
- Run the renderer (no LLM involvement):
  ```bash
  powershell -ExecutionPolicy Bypass -File scripts/render_project.ps1 -ProjectRoot Projects/ad_daa_0059_test -RootDir .
  ```
- Each clip is rendered MP4 + preview JPG with QA scoring.

### 5. Publish
- **YouTube**: `python scripts/publish.py approve --id <entry-id>` flips the draft to approved; then `python scripts/publish.py upload --id <entry-id>` uploads via Google OAuth.
- **Facebook**: `python scripts/upload_facebook.py --id <entry-id>` (or `--dry-run` to validate credentials without posting).
- **TikTok / Instagram**: Frameworks exist in `scripts/` but require owner‑provided API keys (see `docs/PUBLISHING_SETUP.md`).

## Credentials (.env)
Copy `.env.example` to `.env` and fill real values. Key sections:
- **Transcription**: `GROQ_API_KEY`, `TRANSCRIBE_BASE_URL`, `TRANSCRIBE_MODEL`
- **Task 7**: `CLIPPLAN_BASE_URL`, `CLIPPLAN_MODEL` (fallback to `GROQ_API_KEY`)
- **YouTube**: `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_TOKEN_FILE`
- **Facebook**: `FB_PAGE_ID`, `FB_PAGE_ACCESS_TOKEN`, `FB_GRAPH_VERSION`
- **Telegram** (Task 3): `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SOURCE_GROUP`
- **TikTok / Instagram**: keys documented in `docs/PUBLISHING_SETUP.md`

> **Never commit `.env`** — it is gitignored and excluded from backup.

## Backup
All scripts, state files, and docs are regularly pushed to the cloud backup repo:
```
https://github.com/Dropsofknowledge/dok-backup.git
```

## Task Order (per brief)
1. Task 1 — Architecture audit (`docs/ARCHITECTURE_AUDIT.md`)
2. Task 2 — (Implicit) Base scaffolding
3. Task 3 — Telegram audio fetch
4. Task 4 — Dataset logger (`state/dataset_log.db`)
5. Task 5 — (Implicit) Publishing framework
6. Task 6 — LLM title/description drafting
7. Task 7 — Clip‑plan generation (this task)
8. Task 8 — Credential tooling (`--dry-run` on facebook uploader)
9. Task 9 — Dashboard (`scripts/status.py`) — **LAST**, after 7 & 8 are committed