# DropsofKnowledge — Architecture Audit

Date: 2026-08-23 · Scope: infrastructure audit only, no code changes, no architecture proposals.
Method: read every script/module in `scripts/`, the Python harness, templates, `.gitignore`, git state, and actual data under `Projects/` and `examples/`. Findings below are verified from the code and files, not assumed.

---

## 0. Headline finding: there is no transcription code in this repo at all

A full-text search for `whisper`, `colab`, `openai`, and `transcri*` finds **zero invocation code**. The repo consumes *finished* transcripts (`transcript.srt/.json/.vtt`) that someone drops into the project folder. Whatever produces those transcripts (per the owner's brief: Google Colab Whisper runs) lives entirely outside this repository. So "remove the Colab dependency" means **adding** a scriptable transcription step that doesn't exist here yet — not porting existing notebook code.

---

## 1. Component inventory — INPUT / PROCESSING / OUTPUT / STATUS

### 1.1 Import wizard — `scripts/new_episode.ps1` (+ `lib/production.ps1`)
- **INPUT:** interactive prompts (series, episode, source folder path) or params.
- **PROCESSING:** `Import-DokLecture` creates `Projects/<series>_<episode>/` with `output/ logs/ reports/ cache/`; copies recognised files without clobbering (`audio.*`, `transcript.*`, `background.*`, `clip_plan.json`, `layout.json`, plus series-named images); seeds project-local `layout.json` + series background from the series template; writes placeholder `clip_plan.json` and initial `render_state.json`; generates `RUN_PROJECT.cmd` with the Dok root baked in.
- **OUTPUT:** populated project folder ready for a real clip plan.
- **STATUS:** working. Evidence: all four live projects contain generated `RUN_PROJECT.cmd`, seeded layouts, plans.

### 1.2 Batch renderer — `scripts/render_project.ps1`
- **INPUT:** `-ProjectRoot`, `-RootDir`, optional `-Force`. Reads `clip_plan.json` (auto-normalises legacy `clip.json` if present), resolves tools (`Get-DokTools`: PATH or `config.ps1`/`DOK_FFMPEG`/`DOK_FFPROBE`/`DOK_MAGICK`), resolves paths/background/template, probes source duration, runs pre-flight validation.
- **PROCESSING (per clip):** status check against `render_state.json` (skip completed unless `-Force`) → mark invalid clips failed without attempting → overlay SVG built (`New-DokOverlaySvg`) → rasterised via ImageMagick → composited onto background → transcript sliced/chunked to cues → ASS written → FFmpeg renders static frame + audio slice with burned captions → preview extracted → ffprobe QA (file size, even-dimensioned resolution, audio stream present).
- **OUTPUT:** `output/<id>/{overlay.png, base.png, captions.ass, clip.mp4, preview.jpg, manifest.json, report.json}`, `reports/<id>.json`, `reports/_summary.json`, `logs/render_*.log`, updated `render_state.json`.
- **RESILIENCE:** per-clip try/catch — one failure never aborts the batch; atomic state writes (tmp + move); exit codes 2/3/4 distinguish missing plan / missing tool / validation abort.
- **STATUS:** working. Evidence: 4 projects, 29 rendered MP4s (~109 MB) with QA-passing manifests/reports; `_summary.json` files show completed/skipped/failed accounting.

### 1.3 Validation — `lib/validation.ps1`
- **INPUT:** resolved paths object, parsed plan, probed source duration.
- **PROCESSING:** timestamp parsing tolerant of `HH:MM:SS.mmm` / `MM:SS` / seconds / comma decimals; errors = missing audio, missing plan, missing template, unwritable output, bad/reversed timestamps, end > source duration; warns on missing transcript/background/zero clips.
- **OUTPUT:** issue array `{severity, message, clip}`; project-level errors abort, clip-level errors mark that clip failed.
- **STATUS:** working. Note: this is *structural* validation of a hand-authored plan only — it does **not** validate AI-generated plans (that is reserved scope).

### 1.4 Layout/templates — `lib/layout.ps1` + `templates/<series>/layout.json`
- **INPUT:** layout resolution order: project-local `layout.json` → `templates/<series>/` → `templates/default/`. Fonts from `fonts/` incl. Arabic (`NotoNaskhArabic-Regular.ttf`).
- **PROCESSING:** builds SVG overlay with auto-fit text blocks (font_max→font_min search, width+char wrap, max lines), mixed Latin/Arabic font selection per character, baked-background-aware element positions (id/title/speaker/caption_box). `allow_project_background:false` makes the template background win over a project-supplied one.
- **OUTPUT:** SVG string → PNG overlay.
- **STATUS:** working. Templates present: kabair, ad_daa, adab, extras, default (all currently modified jpg→png backgrounds in working tree, uncommitted).

### 1.5 Captions — `lib/captions.ps1`
- **INPUT:** transcript json (Whisper-style `segments`) / srt / vtt.
- **PROCESSING:** parse cues → slice to clip window → chunk to ≤N words (punctuation-aware) → evenly distribute timing → ASS file. Two styles: poster caption-box mode (`elements.caption_box`, `\pos` centered dark-on-white) and legacy bottom-subtitle mode (`zones.subtitle`).
- **OUTPUT:** `output/<id>/captions.ass`.
- **STATUS:** working.

### 1.6 Audio handling — `lib/audio.ps1`
- **INPUT:** any `audio.{mp3,amr,wav,m4a,m4b,aac,ogg,opus,wma,flac,mp4,mov,webm,m4v,mkv}`.
- **PROCESSING:** duration probe via ffprobe; non-m4a/aac/mp3 sources converted once to `cache/audio_work.m4a` (AAC 192k), reused across clips/runs.
- **STATUS:** working. All four live projects use `.amr` sources with cached conversions.

### 1.7 FFmpeg/ImageMagick wrappers — `lib/ffmpeg.ps1`
- Commands actually used:
  - `magick -background none -size WxH tmp.svg out.png` (overlay rasterise)
  - `magick bg.png -resize WxH^ -gravity center -extent WxH overlay.png -composite base.png` (base frame; solid-colour fallback)
  - `ffmpeg -loop 1 -framerate FPS -i base.png -ss S -t D -i audio.m4a -vf "ass='file':fontsdir='fonts',pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p" -c:v libx264 -preset veryfast -crf 20 -c:a aac -b:a 192k -movflags +faststart out.mp4`
  - `ffmpeg -ss 0.5 -i clip.mp4 -frames:v 1 -q:v 3 preview.jpg`
  - `ffprobe` for duration/resolution/audio checks
- Native stderr handled via `Invoke-DokNativeQuiet` (PowerShell 5.1 NativeCommandError workaround).
- **STATUS:** working.

### 1.8 State/resume — `lib/state.ps1`
- **INPUT/OUTPUT:** `render_state.json` (`{project, last_run, clips:[{id,status,output?,reason?}]}`), atomic writes, statuses pending/rendering/completed/failed.
- **STATUS:** working. Plan is never mutated; runtime status kept separate.

### 1.9 Menu/dashboard — `scripts/start_here.ps1`, `scripts/dashboard.ps1`, `START_HERE.cmd`
- Root launcher passes root explicitly; menu drives import/render/dashboard; dashboard prints planned/completed/failed/pending per project.
- **STATUS:** working.

### 1.10 Python harness — `harness/dok_harness.py`
- Portable mirror (import/render/dashboard) using Pillow instead of ImageMagick/SVG. Not shipped product; exists so logic can be proven cross-platform.
- **STATUS:** working as a parallel implementation; drift risk noted (two implementations must be kept in lockstep manually — e.g. harness lacks the newer `allow_project_background:false` template-wins precedence… actually it implements it; but any future change doubles maintenance).

### 1.11 Transcription (Whisper) — **MISSING**
- No local invocation, no API call, no notebook, nothing. Transcript consumption is implemented; production is entirely outside the repo (owner states Colab).
- **STATUS:** missing (external, manual, non-scriptable).

### 1.12 Raw-lecture ingestion (Telegram etc.) — **MISSING**
- Lectures arrive by whatever manual means into a source folder; import copies them.
- **STATUS:** missing.

### 1.13 Clip-plan generation / AI validation / end-to-end orchestration — **OUT OF SCOPE**
- Deliberately absent; reserved for the owner's later curriculum work. Flagged, not touched.

---

## 2. Actual `clip_plan.json` schema (as used in the wild)

From `Projects/ad_daa_0059/clip_plan.json` and `Projects/kabair_0051/`:

```json
{
  "series": "DropsofKnowledge",        // string; lowercased/sanitised for folder+template lookup
  "episode": "0059",                   // string
  "speaker": "Abu Naasir Ibrahim Abdulrauf",
  "version": "5.3.1",                  // optional, ignored by renderer
  "source": "ad_daa059.amr",           // optional, ignored by renderer
  "clips": [
    {
      "id": "0059-01",                 // string; live convention "<episode>-NN"
      "start": "00:08:34.140",
      "end": "00:10:04.140",
      "headline": "...",
      "confidence": 0.88,
      "flag": null
    }
  ]
}
```

- Timestamps accept `HH:MM:SS.mmm`, `MM:SS`, plain seconds, comma decimals.
- Extra keys (`priority`, `confidence`, `flag`, `version`, `source`) are tolerated and ignored — validation only inspects ids/timestamps.
- Placeholder plan written by import: `{series, episode, speaker:"", clips:[]}`.
- Legacy `clip.json` is copied to `clip_plan.json` automatically at render time.

## 3. Data flow today

```
[manual] lecture audio file          [manual] transcript.srt        [manual] clip_plan.json
     │                                     │                              │
     └────────────► Import wizard ◄────────┴──────────────────────────────┘
                        │  creates Projects/<series>_<episode>/ + RUN_PROJECT.cmd
                        ▼
                  RUN_PROJECT.cmd ──► render_project.ps1
                        │  validate → overlay(SVG→PNG) → base frame → ASS → ffmpeg mp4 → QA
                        ▼
        output/<id>/clip.mp4 + preview + manifest + report ; render_state.json ; _summary.json
```

Every arrow into the import wizard is a manual step today: obtaining the audio, transcribing it, and authoring the plan.

## 4. Manual steps remaining (verified)

1. **Obtain lecture audio** — no automated fetch exists.
2. **Transcribe** — done externally (Colab, per owner); result hand-placed as `transcript.srt`.
3. **Author clip_plan.json** — fully manual editorial work (reserved; must stay manual for now).
4. Clicking through import + run — scripted, minimal friction.

## 5. Folder structure / entry points / live-vs-dead

```text
START_HERE.cmd            live  – root menu launcher
scripts/
  start_here.ps1          live  – menu backend
  new_episode.ps1         live  – import wizard (uses logging, production, audio)
  render_project.ps1      live  – batch renderer (loads all lib modules)
  dashboard.ps1           live
  config.ps1              live  – optional explicit tool paths (env vars)
  lib/logging.ps1         live
  lib/state.ps1           live
  lib/validation.ps1      live
  lib/production.ps1      live  – paths/import/RUN_PROJECT.cmd/QA
  lib/layout.ps1          live  – SVG overlay generation
  lib/captions.ps1        live
  lib/audio.ps1           live
  lib/ffmpeg.ps1          live
harness/dok_harness.py    live  – portable Python mirror (verification only)
templates/{kabair,ad_daa,adab,extras,default}/  live
assets/default_background.png                   live (fallback)
fonts/*.ttf                                     live
Projects/                 untracked working data (4 projects: adab_0491, ad_daa_0055, ad_daa_0059, kabair_0051)
examples/sample_lecture/  sample inputs
docs/                     AGENT_BRIEF.md, this file
```

No dead/orphaned scripts found — every entry point is reachable from `START_HERE.cmd` or `RUN_PROJECT.cmd`.

## 6. Repo-state observations relevant to upcoming tasks (no changes made)

- **Git:** branch `main`, clean sync with `origin/github.com/Dropsofknowledge/Dropsofknowledge_pipeline`, but the working tree has substantial uncommitted modifications (template background jpg→png swaps, layout tweaks, harness/renderer/import edits) and `Projects/`, `docs/` are untracked.
- **Large files:** `Projects/*/cache/audio_work.m4a` ≈ 30–41 MB each (~140 MB total), `audio.amr` ≈ 19 MB total, rendered `.mp4`s ≈ 109 MB total. `.gitignore` excludes `*.mp3/*.wav/*.mp4/transcripts/` but **not** `.amr`, `.m4a`, or `.png` — adding `Projects/` as-is would commit large binaries. This matters for the backup task (LFS vs artifacts-only decision).
- **Tooling assumptions:** PowerShell 5.1-safe patterns throughout (`Invoke-DokNativeQuiet`, PSObject property guards); FFmpeg + ImageMagick on PATH or via env vars; no Python dependency for the shipped pipeline except the harness (Pillow).
- **Testing:** no formal test suite; verification is via the Python harness and real rendered outputs.

---

*Audit complete. Per the brief, stopping here — no code changed, architecture proposals deliberately withheld.*
