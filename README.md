# DropsofKnowledge Renderer

A Windows content-production system that turns one long lecture into many
branded **9:16 short-form videos** automatically. You import a lecture folder,
supply a `clip_plan.json`, and run one command to render every clip — with
**resume**, **per-clip status tracking**, and **failure resilience** built in.

Built to the v6 specification. The rendering engine, project orchestration, and
editorial planning are kept as separate layers.

---

## 1. Requirements

| Tool | Why | Notes |
|------|-----|-------|
| **Windows + PowerShell 5.1+** | runs the `.ps1` / `.cmd` scripts | PowerShell 7 also works |
| **FFmpeg** (`ffmpeg`, `ffprobe`) | audio convert, render, probe, preview | https://ffmpeg.org |
| **ImageMagick** (`magick`) | rasterises the SVG title/speaker overlay | https://imagemagick.org |

Put `ffmpeg`, `ffprobe`, and `magick` on your `PATH`, **or** set explicit paths
in `scripts/config.ps1` (or env vars `DOK_FFMPEG`, `DOK_FFPROBE`, `DOK_MAGICK`).

---

## 2. Quick start

1. Double-click **`START_HERE.cmd`**.
2. Choose **Import Lecture** → enter series (e.g. `Kabair`), episode (e.g. `0048`),
   and the lecture source folder.
3. The system creates `Projects/Kabair_0048/`, copies recognised files, and
   generates `RUN_PROJECT.cmd`, `render_state.json`, and a placeholder plan.
4. Drop your real **`clip_plan.json`** into the project folder.
5. Double-click **`RUN_PROJECT.cmd`** inside that folder.
6. Every clip renders to `output/<id>/clip.mp4` with a preview, manifest, and report.

Re-running skips completed clips and retries only pending/failed ones.
Pass `-Force` to `render_project.ps1` to re-render everything.

---

## 3. Folder structure

```text
DropsofKnowledge/
├── START_HERE.cmd            # root menu launcher (spec 8)
├── scripts/
│   ├── start_here.ps1        # interactive menu backend
│   ├── new_episode.ps1       # Import Lecture wizard (spec 7)
│   ├── render_project.ps1    # batch render loop (spec 10, 17)
│   ├── dashboard.ps1         # project / root dashboard (spec 16)
│   ├── config.ps1            # optional explicit tool paths
│   └── lib/
│       ├── logging.ps1       # console + file logging
│       ├── state.ps1         # render_state.json (resume)
│       ├── validation.ps1    # pre-flight checks (spec 11)
│       ├── production.ps1    # import, paths, QA, manifests
│       ├── layout.ps1        # SVG overlay + auto-fit (spec 12-13)
│       ├── captions.ps1      # transcript -> ASS subtitles (spec 14)
│       ├── audio.ps1         # audio resolve/convert/probe
│       └── ffmpeg.ps1        # ffmpeg / ImageMagick wrappers
├── templates/
│   ├── kabair/layout.json    # per-series template
│   └── default/layout.json   # fallback template
├── assets/
│   └── default_background.png
├── fonts/                    # drop custom .ttf here (optional)
├── Projects/                 # one folder per lecture
├── harness/
│   └── dok_harness.py        # portable verification harness (see §7)
└── README.md
```

Each project folder:

```text
Projects/Kabair_0048/
├── audio.mp3            transcript.srt        background.png
├── clip_plan.json       render_state.json     RUN_PROJECT.cmd
├── output/  <id>/clip.mp4 + preview.jpg + manifest.json + report.json
├── logs/    reports/    cache/
```

---

## 4. Supported inputs

- **Audio / media** (named `audio.*`): mp3, amr, wav, m4a, m4b, aac, ogg, opus,
  wma, flac, mp4, mov, webm, m4v, mkv.
- **Transcript** (`transcript.*`): json (Whisper-style `segments`), srt, vtt.
- **Background** (`background.*`): jpg, jpeg, png, webp. Falls back to
  `assets/default_background.png`.
- **Plan**: `clip_plan.json`.

---

## 5. Data models

### `clip_plan.json` (editorial input — declarative)
```json
{
  "series": "kabair",
  "episode": "0048",
  "speaker": "Sheikh Salih Al-Fawzan",
  "clips": [
    { "id": "0001", "start": "00:03:15.200", "end": "00:04:08.800",
      "headline": "Never Underestimate Minor Sins",
      "priority": 98, "confidence": 0.97, "flag": null }
  ]
}
```
Timestamps accept `HH:MM:SS.mmm`, `MM:SS`, or plain seconds.

### `render_state.json` (runtime status — written by the renderer)
```json
{
  "project": "kabair_0048",
  "last_run": "2026-06-28T12:45:54Z",
  "clips": [
    { "id": "0001", "status": "completed", "output": "output/0001/clip.mp4" },
    { "id": "0003", "status": "failed", "reason": "Timestamp exceeds source duration (330.0s)." }
  ]
}
```
The plan is never mutated; runtime status lives only here (spec 23).

---

## 6. Template system

Templates live in `templates/<series>/layout.json` and own **all** layout
positions (canvas, safe areas, title/speaker/subtitle zones, fonts, branding).
The renderer never hardcodes positions. Auto-fit chooses the largest title size
that fits its zone, wraps to ≤ 2 lines, keeps the speaker name on one line, and
chunks captions into short readable cues styled white-on-black-outline.

To brand a new series, copy `templates/default/` to `templates/<series>/`,
adjust `layout.json`, and (optionally) drop a series `background.*`.

---

## 7. Verifying the logic (portable harness)

The shipped product is the PowerShell tree. Because that needs Windows, a
**portable Python harness** (`harness/dok_harness.py`) re-implements the same
orchestration/validation/state/caption/layout logic function-for-function so the
algorithms can be executed and proven on any OS with FFmpeg + ImageMagick:

```bash
python3 harness/dok_harness.py import   <root> <series> <episode> <source_folder>
python3 harness/dok_harness.py render   <project_dir> <root> [--force]
python3 harness/dok_harness.py dashboard <root> [project_dir]
```

This repo ships with a rendered sample under `Projects/Kabair_0048/` proving:
2 clips rendered (QA 100, 1080×1920 h264/aac), 1 clip correctly failed
("exceeds source duration") without aborting the batch, and a rerun skipped the
already-completed clip — exactly the acceptance criteria in spec 22.

---

## 8. Acceptance criteria coverage (spec 22)

- ✅ Import a new project from one lecture folder — `new_episode.ps1`
- ✅ Supply a clip plan as JSON — `clip_plan.json`
- ✅ One command renders all clips — `RUN_PROJECT.cmd` → `render_project.ps1`
- ✅ A failed clip does not stop others — per-clip try/catch + state save
- ✅ A rerun skips completed clips — `render_state.json` resume
- ✅ MP4 + report + manifest + preview per clip — `output/<id>/`
- ✅ Startup menu + import wizard with explicit paths — no CWD guessing
- ✅ Rebuildable from the spec alone — modular scripts, no hidden state

## Kabair poster template status

The Kabair renderer has been updated to use the blank poster background in:

```text
templates/kabair/background.png
```

That background contains the fixed design elements only: top banner text, “By Sheikh:”, the cream banner, caption box, footer, and socials. The renderer dynamically adds:

- clip ID, e.g. `#0005`
- the clip headline from `clip_plan.json`
- the sheikh/speaker name from `clip_plan.json`
- timed captions inside the built-in white caption box

The Kabair template is configured in:

```text
templates/kabair/layout.json
```

Important: if a project folder contains its own `background.png`, that project-specific background is used. If no project background is supplied, the renderer uses the series template background. For consistent Kabair output, either omit project-level `background.*` files or make sure they are the same blank poster template.
