# Dropsofknowledge — Agent Instructions

## Project Status

**Tasks 1–9 complete and verified:**
- Architecture audit
- Local Whisper transcription (Groq API, provider-swappable)
- Telegram audio auto-fetch with dedupe
- Cloud backup to private GitHub repo
- Dataset logging (transcript + clip_plan pairs)
- Publishing framework (YouTube, Facebook) with approval queue and title/description drafting
- Automated clip-plan generation (`scripts/generate_clip_plan.py`) with staging → approval flow
- Platform credentials & Facebook uploader `--dry-run` flag
- Interactive status + approval dashboard (`scripts/status.py`)
- Full documentation in `docs/SETUP.md` and `docs/OPERATIONS.md`

**Platform scope:** YouTube and Facebook only. (X, TikTok, Instagram are deferred).
**Runtime Independence:** All scripts must run standard Python locally, calling APIs directly via keys in `.env`. No script may depend on OpenCode or an external IDE tool at runtime.

**Current work:** Tasks 10–12 (Transcript code-switching fix, One-touch orchestrator, Facebook Reels optimization).

---

## PREREQUISITE — before any new task

Before starting any task, run `git status`.
If there are uncommitted or untracked files: **stop and commit/push them first.**
Do not layer new work on top of an uncommitted working tree.

---

## Task 10 — Code-Switching & Mixed-Script Transcript Fix

### Problem
Lectures consist of English explanations mixed with Arabic verses, Hadith, and Islamic terminology.
- Forcing Groq Whisper language to `ar` renders English speech into phonetic Arabic script.
- Forcing language to `en` skips or mangles Arabic Quranic recitation.

### Requirements

#### Step 1: Update `scripts/transcribe.py`
1. Remove fixed `language="ar"` forcing. Set language to `en` or allow auto-detection.
2. Pass an `initial_prompt` parameter to the Groq/Whisper API call:
   ```text
   "This is an Islamic lecture spoken primarily in English, containing classical Arabic Quranic verses, Hadith, and supplications (e.g., Bismillah, Alhamdulillah, Sallallahu 'alayhi wa sallam). Use Latin English alphabet for English speech, and Arabic script for Quranic/Hadith Arabic recitations."
   ```

#### Step 2: Build `scripts/refine_transcript.py` (Script Correction Pass)
1. Takes a generated `transcript.json` and `.srt` file.
2. Sends text segments to a free LLM API configured in `.env` (Groq/OpenRouter).
3. **LLM System Prompt:**
   > "You are an expert Islamic media transcript editor. Fix script mismatches in the following lecture segment. Any English words accidentally written in Arabic letters must be converted to standard English spelling (Latin alphabet). Quranic verses, Hadith recitations, and pure Arabic supplications must remain in Arabic script. Do NOT translate or summarize — only fix the alphabet script mismatch so English is in English and Arabic text is in Arabic."
4. Writes the refined output back to `transcript_refined.json` and `transcript_refined.srt`.

---

## Task 11 — One-Touch Pipeline Orchestrator (`scripts/main.py`)

### Goal
Build a single entry-point script (`scripts/main.py`) that chains the automated intake and preparation tasks while preserving all human approval gates.

### Pipeline Chain
1. Run `scripts/telegram_fetch.py` to pull new audio files.
2. If new audio exists, run `scripts/transcribe.py <audio_folder>`.
3. Run `scripts/refine_transcript.py <transcript.json>` to fix mixed-script text.
4. Run `scripts/generate_clip_plan.py <transcript_refined.json>` to produce candidate clips in `state/clip_plan_staging_<timestamp>.json`.
5. Display a clean terminal summary:
   ```text
   Pipeline run complete!
   New clip plan staged at: state/clip_plan_staging_20260824_152710.json
   Run "python scripts/status.py" to review, edit, or approve clips.
   ```

### Hard Boundaries
- `main.py` MUST NOT automatically trigger `render.py` or publishing/uploading scripts.
- Rendering and uploading strictly require explicit human approval via `python scripts/status.py` or direct CLI commands.

---

## Task 12 — Facebook Reels Video API Optimization

### Goal
Ensure `scripts/upload_facebook.py` supports posting short-form video clips as **Facebook Reels** (using the Meta Page Reels Video API endpoint) rather than standard legacy page posts, as Reels maximize reach.

### Implementation
1. In `scripts/upload_facebook.py`, update video publishing logic to target the Meta Page Reels API endpoint (`/{page-id}/video_reels`).
2. Implement chunked/resumable upload handling for reliability on local connections.
3. Keep the existing `--dry-run` flag functional: verify page permissions and Reels publishing scope without creating a post.
4. Support drafting/scheduling options where available via Page access tokens.

---

## Combined Workflow (Tasks 1–12 Complete)

```text
1. python scripts\main.py
   → Fetches Telegram audio → Transcribes → Refines English/Arabic script → Generates staged clip plan

2. python scripts\status.py
   → Single dashboard to review staged clip plans, edit in Notepad, or approve
   → Triggers rendering for approved clip plans

3. python scripts\publish.py draft <clip-file> --platforms youtube facebook
   → Drafts title/description via LLM

4. python scripts\status.py
   → Review drafted captions & approve queue entries

5. python scripts\upload_youtube.py --approve <queue-entry-id>
   python scripts\upload_facebook.py --approve <queue-entry-id>
   → Publishes live post / Reel

6. BACKUP.cmd
   → Backup artifacts and logs to private repo
```

---

## Working Constraints

- Everything runs locally on Windows with Python 3.x and active internet.
- `.env` holds all API secrets; `.env.example` has placeholders only.
- Scripts call APIs directly; zero dependence on OpenCode or IDEs at runtime.
- No files are renamed, moved, or deleted without explicit prompt instructions.
- All real renders and uploads require explicit approval steps.

---

## Next Steps

1. **Check git status** — commit/push anything uncommitted before starting.
2. **Complete Task 10:** Update `transcribe.py` with `initial_prompt` and build `refine_transcript.py`.
3. **Complete Task 11:** Build `scripts/main.py` orchestrator script.
4. **Complete Task 12:** Update `upload_facebook.py` for Reels API publishing and verify with `--dry-run`.
5. **Run Backup:** Execute `BACKUP.cmd` and update docs.