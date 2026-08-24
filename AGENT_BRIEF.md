# Dropsofknowledge — Agent Instructions

## Project Status

**Tasks 1–6 complete and verified:**
- Architecture audit
- Local Whisper transcription (Groq API, provider-swappable)
- Telegram audio auto-fetch with dedupe
- Cloud backup to private GitHub repo
- Dataset logging (transcript + clip_plan pairs)
- Publishing framework (YouTube, Facebook) with approval queue and title/description drafting

**Platform scope:** YouTube and Facebook only. These were the original traffic sources.
X, TikTok, and Instagram are explicitly out of scope for now — not rejected, just deferred.
If added later, they get their own task; do not build toward them speculatively.

**Current work:** Task 7 (clip-plan generation) + Task 8 (credential setup for YouTube/Facebook).

---

## PREREQUISITE — before any new task

Before starting Task 7, check `git status` in the main repo.
If there are uncommitted or untracked files: **stop and commit/push them first.**
Do not layer new work on top of an uncommitted tree. This applies at the start of every session, not just once.

---

## Task 7 — Automated clip-plan generation

### What this task is

Build a script that:
- Takes a transcript file (`.json` with timestamps from `scripts/transcribe.py`)
- Calls a free LLM API to generate candidate clip plans
- Outputs a staging file with proposed clips
- Shows the user what was generated
- Requires explicit approval before the plan is saved to the actual clip_plan.json

This automates the current manual step where you paste a transcript into an LLM GUI and copy back the clipping times.

### What this task is NOT

- Does NOT automatically render or publish clips
- Does NOT replace human editorial review
- Does NOT integrate into a "one-touch" pipeline yet
- The generated plan sits in staging; you review and approve it before it goes anywhere

### Implementation

Build `scripts/generate_clip_plan.py`:

**Input:** path to a transcript.json file (produced by `scripts/transcribe.py`)

**Processing:**
1. Read the transcript and build a clean context string with timestamps
2. Call a free LLM API (same approach as your current manual process):
   - Use an OpenRouter free model or equivalent free-tier API
   - Send the transcript + context about your lecture
   - Request JSON output in your exact `clip_plan.json` schema
3. Validate the output schema — reject malformed JSON
4. Write to a staging file: `state/clip_plan_staging_<timestamp>.json`

**Output:** a staging clip plan, not yet approved

**The review step:**
- Show the user the staged plan (list clips with start/end times, headlines)
- Allow editing (JSON is fine, doesn't need a UI)
- User explicitly runs:
  ```
  python scripts/generate_clip_plan.py --approve state/clip_plan_staging_<timestamp>.json
  ```
- Only then: copy to the real `clip_plan.json` location and proceed to rendering

**Provider choice:**
Use whichever free API is most reliable at the time:
- OpenRouter free tier (currently available for many models)
- Groq free tier (same as you use for transcription — provider-swappable)
- Another equivalent
Do NOT hardcode Ox Alpha or any specific model. Make it configurable via `.env`.

**Prompt structure:**
The LLM should receive:
- Speaker name and lecture context
- Full transcript with timestamps
- Your selection criteria:
  * Practical Islamic guidance, meaningful principles, explained warnings
  * Avoid contextless quotes or controversy-baiting
  * Preserve complete thoughts and necessary context
  * Target length ~2 minutes per clip
- Expected output schema (your actual clip_plan.json fields)

**Test with real data:**
- Use one of your actual completed transcripts
- Generate, review, edit, approve
- Render a clip from the approved plan
- Verify it matches your manual selection quality

### Hard boundaries (preserved from earlier)

- No clip generation touches the renderer
- No approval step is skipped
- The staging file cannot be auto-promoted to final; you must explicitly approve
- Secrets stay in `.env`, not hardcoded

---

## Task 8 — Credential setup and upload verification (YouTube + Facebook only)

Once clip-plan generation is working, finalize the publishing layer by setting up real account credentials and running one test upload through each platform.

### YouTube

1. Create a Google Cloud project at console.cloud.google.com
2. Enable "YouTube Data API v3"
3. Create an OAuth 2.0 Desktop Client credential
4. Download the JSON and extract:
   - `client_id`
   - `client_secret`
5. Add to `.env`:
   ```
   YOUTUBE_CLIENT_ID=<your-client-id>
   YOUTUBE_CLIENT_SECRET=<your-client-secret>
   ```
6. Run (first time only):
   ```
   python scripts\upload_youtube.py --login
   ```
   This opens a browser, you approve, and the token is cached locally.
7. Run a real upload:
   ```
   python scripts\upload_youtube.py --approve state/publish_queue.json <entry-id>
   ```
   Verify the clip appears on your channel with a real video ID.

### Facebook

1. Go to developers.facebook.com and create an app
2. Add "Pages" as a product
3. Generate a Page Access Token for your Page with the `pages_manage_posts` permission
4. Extract:
   - `page_id` (your Page's numeric ID)
   - `page_access_token`
5. Add to `.env`:
   ```
   FB_PAGE_ID=<your-page-id>
   FB_PAGE_ACCESS_TOKEN=<page-token>
   ```
6. Test:
   ```
   python scripts\upload_facebook.py --dry-run
   ```
7. Run a real post:
   ```
   python scripts\upload_facebook.py --approve state/publish_queue.json <entry-id>
   ```
   Verify it appears as a draft or scheduled post on your Page.

### After both are verified

Once YouTube and Facebook have each received at least one real test post:
- Run `BACKUP.cmd` to commit the updated scripts and .env.example (but NOT .env)
- Document in `docs/SETUP.md` exactly which credentials are needed and where to get them
- Declare the publishing layer complete for these two platforms

---

## Task 9 — Interactive status + approval dashboard (do this LAST, after Tasks 7 and 8, and only once everything is committed)

### Why

The user currently has to open several files/folders to know what needs attention, and then leave that view to actually approve anything. This task builds one command that shows everything pending AND lets the user act on it — approve, or open in editor — from the same screen.

### What this task is NOT

- Does NOT move, rename, or restructure any existing files or folders
- Does NOT change any file paths that other scripts depend on
- Does NOT reimplement approval logic — it must call the *same* approve functions/commands the existing scripts already use
- This is a thin interactive layer over existing scripts, not a rebuild of them

If physically moving files ever becomes genuinely necessary later, it's a separate, explicit task done on a clean committed tree with every path reference updated and tested — not bundled into this one.

### Implementation

Build `scripts/status.py`. Running it with no arguments shows a numbered menu:

```
python scripts\status.py
```

Example output:
```
[1] Clip plan awaiting approval: state/clip_plan_staging_20250601.json (4 clips)
[2] Publish queue entry awaiting approval: yt_ep12_clip3 (YouTube + Facebook draft)
[3] Missing credential: FB_PAGE_ACCESS_TOKEN not set in .env

Type a number to act on it, "e <number>" to open it in your editor first, or "q" to quit.
```

- Selecting a clip-plan item runs the exact same approval call as
  `generate_clip_plan.py --approve <file>`
- Selecting a publish-queue item runs the exact same approval call as
  `publish.py approve <entry-id>`
- Selecting a missing-credential item just tells you what to add and where — it cannot create credentials for you
- Also show a short "recent activity" section: last 5 completed uploads/renders with timestamps, so nothing silently failed unnoticed

### Hard boundary

If at any point it seems like the fix "requires" moving files, duplicating approval logic, or bypassing an existing check — stop and flag it instead of doing it. This script is a control panel that reaches into the existing pipeline; it does not become a second, separate pipeline.

---

## Combined workflow (Tasks 1–9 complete)

```
1. python scripts\status.py
   → One screen: shows everything awaiting review/approval, or missing setup
   → Approve items directly from here, or drill into a file first

2. python scripts\telegram_fetch.py
   → Downloads new lectures, dedupes

3. python scripts\transcribe.py <lecture-folder> --language ar
   → Whisper transcription, outputs .json + .srt

4. python scripts\generate_clip_plan.py <transcript.json>
   → Generates staging clip plan
   → Review/approve via status.py, or directly:
     python scripts\generate_clip_plan.py --approve <staging-file>

5. python scripts\render.py <clip_plan.json>
   (existing renderer, unchanged)
   → Produces final MP4 clips

6. python scripts\publish.py draft <clip-file> --platforms youtube facebook
   → Drafts title/description via LLM
   → Review/approve via status.py, or directly:
     python scripts\publish.py approve <queue-entry-id>

7. python scripts\upload_youtube.py --approve <queue-entry-id>
   python scripts\upload_facebook.py --approve <queue-entry-id>
   → Real posts go live

8. BACKUP.cmd
   → Commit artifacts and logs to backup repo
```

Two review gates: one after clip generation, one after caption drafting. No automation without your approval.

---

## Working constraints

- Everything runs locally on Windows with internet (for Telegram, LLM APIs, platform uploads)
- No Google Colab, no Docker, no server required
- `.env` contains all secrets; `.env.example` has placeholders only
- All scripts read provider/model info from config, not hardcoded
- Credentials are owner-managed (you own the accounts, billing, verification)
- All real uploads require an `--approve` flag and explicit queue entry
- No task moves, renames, or restructures existing files unless that task's spec explicitly says so
- The dashboard (Task 9) must call existing approval functions, never duplicate them

---

## Next steps

1. **Check git status** — commit/push anything uncommitted before starting
2. **Complete Task 7:** Build `generate_clip_plan.py` with staging → approval flow
3. **Test Task 7:** One real transcript through to rendered clip using AI-generated plan
4. **Complete Task 8:** Verify YouTube and Facebook uploaders work with real test posts
5. **Complete Task 9:** Build the interactive status + approval dashboard
6. **Run backup:** Final push to backup repo
7. Document the complete workflow in `docs/SETUP.md` and `docs/OPERATIONS.md`

---

## Out of scope (reserved for later)

- X, TikTok, Instagram publishing
- End-to-end orchestration (chaining tasks into one command)
- Automatic scheduling or timer-based publishing
- Any physical reorganization of the file/folder structure
- Anything that removes the human approval gates