# Dropsofknowledge — Agent Instructions

## Project Status

**Tasks 1–6 complete and verified:**
- Architecture audit
- Local Whisper transcription (Groq API, provider-swappable)
- Telegram audio auto-fetch with dedupe
- Cloud backup to private GitHub repo
- Dataset logging (transcript + clip_plan pairs)
- Publishing framework (YouTube, X, Facebook) with approval queue and title/description drafting

**Current work:** Task 7 (clip-plan generation) + credential setup for uploaders.

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
- User explicitly runs: `python scripts/generate_clip_plan.py --approve state/clip_plan_staging_<timestamp>.json`
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

## Task 8 — Credential setup and upload verification

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
   Verify the clip appears on your channel.

### X (formerly Twitter)

1. Go to developer.twitter.com and create an app under your account
2. Request **write** access (free tier may require manual approval from X)
3. Enable "OAuth 1.0a" in your app settings
4. Generate/regenerate API credentials:
   - `api_key` (API Key)
   - `api_secret` (API Secret)
   - `access_token` (Personal Access Token)
   - `access_token_secret` (Personal Access Token Secret)
5. Add to `.env`:
   ```
   X_API_KEY=<api-key>
   X_API_SECRET=<api-secret>
   X_ACCESS_TOKEN=<access-token>
   X_ACCESS_TOKEN_SECRET=<access-token-secret>
   ```
6. Test auth (no posting yet):
   ```
   python scripts\upload_x.py --dry-run
   ```
7. Once confirmed, run a real post:
   ```
   python scripts\upload_x.py --approve state/publish_queue.json <entry-id>
   ```
   Verify it appears on your account (it will incur the $0.015 per-post charge).

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

### After all three are verified

Once all three platforms have received at least one real test post:
- Run `BACKUP.cmd` to commit the updated scripts and .env.example (but NOT .env)
- Document in `docs/SETUP.md` exactly which credentials are needed and where to get them
- Declare the publishing layer complete

---

## Combined workflow (Tasks 1–8 complete)

Once you approve credentials and Task 7 verification is done, your full pipeline looks like:

```
1. python scripts\telegram_fetch.py
   → Downloads new lectures, dedupes

2. python scripts\transcribe.py <lecture-folder> --language ar
   → Whisper transcription, outputs .json + .srt

3. python scripts\generate_clip_plan.py <transcript.json>
   → Generates staging clip plan
   → YOU REVIEW + EDIT
   → python scripts\generate_clip_plan.py --approve <staging-file>
   → Copies to real clip_plan.json

4. python scripts\render.py <clip_plan.json>
   (existing renderer, unchanged)
   → Produces final MP4 clips

5. python scripts\publish.py list
   → Shows queue entries ready for drafting

6. python scripts\publish.py draft <clip-file> --platforms youtube x facebook
   → Drafts title/description via LLM
   → YOU REVIEW + EDIT
   → python scripts\publish.py approve <queue-entry-id>

7. python scripts\upload_youtube.py --approve <queue-entry-id>
   python scripts\upload_x.py --approve <queue-entry-id>
   python scripts\upload_facebook.py --approve <queue-entry-id>
   (run each separately, whenever you're ready)
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

---

## Next steps

1. **Complete Task 7:** Build `generate_clip_plan.py` with staging → approval flow
2. **Test Task 7:** One real transcript through to rendered clip using AI-generated plan
3. **Get credentials:** YouTube, X, Facebook (collect the info above)
4. **Complete Task 8:** Verify uploaders work with real test posts
5. **Run backup:** Final push to backup repo
6. Document the complete workflow in `docs/SETUP.md` and `docs/OPERATIONS.md`

---

## Out of scope (reserved for later)

- End-to-end orchestration (chaining tasks into one command)
- Automatic scheduling or timer-based publishing
- Anything that removes the human approval gates
