# DropsofKnowledge — Operations Guide

## Daily Operation Flow

```
Fetch → Transcribe → Generate Plan → Review/Approve (status.py) → Render → Publish
```

### 1. Fetch / Refresh Transcripts
- If new lectures arrive in the Telegram source group (`@abunaasir`), run the fetcher:
  ```bash
  python scripts/fetch_telegram.py   # (if such script exists) or manual download
  ```
- Place new `.amr` files in `Projects/<lecture-id>/` alongside any existing `transcript.json`.

### 2. Transcribe (if new audio)
```bash
python scripts/transcribe.py Projects/ad_daa_0059/audio.amr
```
- This writes `transcript.json` (Groq Whisper large-v3 output).
- The file is automatically added to `state/dataset_log.db` (Task 4).

### 3. Generate Clip Plan
```bash
python scripts/generate_clip_plan.py Projects/ad_daa_0059/transcript.json \
    --series ad_daa --episode 0059 --speaker "Abu Naasir Ibrahim Abdulrauf"
```
- A staging file `state/clip_plan_staging_*.json` appears.
- Inspect it. If the AI's choices match your intent, approve:
```bash
python scripts/generate_clip_plan.py --approve state/clip_plan_staging_*.json --to Projects/ad_daa_0059_test
```
- If you want to tweak a clip, edit the staging file (open with `e <num>` in the status dashboard, or edit JSON directly) and re‑approve.

### 4. Review in status.py Dashboard
```bash
python scripts/status.py
```
- Confirm the pending items are correct.
- Use numbers to approve, `e` to edit, `q` to exit.

### 5. Render the Clips
Ensure the test project folder has:
- `audio.amr` (the source audio)
- `transcript.json` (the Groq transcript)
- `clip_plan.json` (the approved plan from step 3)

Then render:
```bash
powershell -ExecutionPolicy Bypass -File scripts/render_project.ps1 -ProjectRoot Projects/ad_daa_0059_test -RootDir .
```
- Output appears in `Projects/ad_daa_0059_test/output/<clip-id>/`:
  - `clip.mp4` — the final video
  - `preview.jpg` — thumbnail
  - `report.json` — QA metadata (duration, score, etc.)

### 6. Publish
#### YouTube
```bash
python scripts/publish.py approve --id ad_daa_0059_0059-01
# (entry status flips to "approved")
python scripts/publish.py upload --id ad_daa_0059_0059-01
# (OAuth flow; video uploaded and scheduled)
```

#### Facebook (if credentials available)
```bash
python scripts/upload_facebook.py --id ad_daa_0059_0059-01   # actual upload
# or, to validate without posting:
python scripts/upload_facebook.py --id ad_daa_0059_0059-01 --dry-run
```

#### TikTok / Instagram
- Requires app‑specific keys (see `docs/PUBLISHING_SETUP.md`).
- The `scripts/` directory has skeleton upload functions, but they need your API tokens.

### 7. Post‑Publish Housekeeping
- The `publish_queue.json` entry for the clip is updated with `published.{youtube,facebook}` timestamps and post IDs.
- Run the backup push to keep the cloud repo in sync:
  ```bash
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/backup.ps1
  ```
- Commit any new/changed files to the main repo (per brief prerequisite: "stop and commit/push them first").

## Task Order Recap
1. Architecture audit
2. Base scaffolding
3. Telegram audio fetch
4. Dataset logger
5. Publishing framework
6. LLM title/description drafting
7. Clip‑plan generation
8. Credential tooling (`--dry-run`)
9. Dashboard (status.py) — **LAST**

## Troubleshooting
- **TPM exceeded on Groq**: Reduce `CLIPPLAN_WINDOW_CHARS` in `.env` (default 7500). Use `--max-completion-tokens 3000`.
- **Clip plan refused overwrite**: The `--approve` command will not replace an existing `clip_plan.json` without `--force`. Remove or rename the existing file first, or use a different target folder.
- **Missing credentials**: Run `python scripts/status.py` — missing keys are listed at the top. Add them to `.env` per `.env.example`.
- **Renderer fails on captions**: Ensure the `transcript.json` in the project folder matches the timing used during clip selection (the plan was generated against that specific transcript). If you re‑transcribe, you may need to regenerate the plan.