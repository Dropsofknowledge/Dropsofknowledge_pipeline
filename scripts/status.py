#!/usr/bin/env python3
"""DropsofKnowledge status dashboard.

Read-only interactive dashboard that scans state files, checks credentials,
and offers commands to approve clip plans or publish queue entries.

Does NOT duplicate logic -- it calls existing scripts via subprocess.
"""

import json
import os
import subprocess
import sys
import glob as globmod
from datetime import datetime, timezone, timedelta

# ── Paths ──────────────────────────────────────────────────────────────
BASE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(BASE)                              # CWD parent
STATE_DIR = os.path.join(REPO, "state")
PROJECTS = os.path.join(REPO, "Projects")

ENV = os.path.join(REPO, ".env")
EXAMPLE_ENV = os.path.join(REPO, ".env.example")

CLIP_PLAN_PATTERN = os.path.join(STATE_DIR, "clip_plan_staging_*.json")
PUBQUEUE = os.path.join(STATE_DIR, "publish_queue.json")
YOUTUBE_TOKEN = os.path.join(STATE_DIR, "youtube_token.json")

# ── Helpers ─────────────────────────────────────────────────────────────

def iso_now():
    return datetime.now(timezone.utc).astimezone().isoformat()

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def find_missing_keys(env, example_path):
    example = read_env(example_path)
    missing = []
    for k in sorted(example.keys()):
        if k not in env or env[k] in ("", "your-key-here", "xxxx*", "123456"):
            missing.append(k)
    return missing

def read_env(path):
    env = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env

def recent_activity(limit=5):
    """Scan Projects and state for last `limit` completed renders/uploads."""
    events = []

    if os.path.isdir(PROJECTS):
        for root, dirs, files in os.walk(PROJECTS):
            for f in files:
                if f == "report.json":
                    try:
                        rp = load_json(os.path.join(root, f))
                        ev = rp.get("result", {})
                        if ev.get("status") in ("completed", "success"):
                            events.append({
                                "time": rp.get("rendered_at", ""),
                                "what": "Render " + os.path.basename(root),
                                "detail": ev.get("detail", ""),
                            })
                    except Exception:
                        pass

    if os.path.isfile(PUBQUEUE):
        try:
            pq = load_json(PUBQUEUE)
            for e in pq.get("entries", []):
                pb = e.get("published", {})
                if pb:
                    for plat, info in pb.items():
                        ts = info.get("at", "")
                        if ts:
                            events.append({
                                "time": ts,
                                "what": "Publish " + e.get("id", ""),
                                "detail": e.get("headline", ""),
                            })
        except Exception:
            pass

    try:
        events.sort(key=lambda x: x.get("time", ""), reverse=True)
    except Exception:
        pass
    return events[:limit]

def scan_clip_plans():
    """Return list of (path, data) for staging files with _staging.status == 'staged'."""
    results = []
    for path in sorted(globmod.glob(CLIP_PLAN_PATTERN)):
        try:
            data = load_json(path)
        except Exception:
            continue
        status = data.get("_staging", {}).get("status", "")
        if status == "staged":
            results.append((path, data))
    return results

def scan_draft_queue():
    """Return list of draft entries from publish_queue.json."""
    if not os.path.isfile(PUBQUEUE):
        return []
    try:
        pq = load_json(PUBQUEUE)
    except Exception:
        return []
    return [e for e in pq.get("entries", []) if e.get("status") == "draft"]

def run_cmd(cmd):
    """Run a command via subprocess and return (returncode, stdout_text)."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, cwd=REPO, timeout=120
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except Exception as e:
        return -1, str(e)

# ── Menu ───────────────────────────────────────────────────────────────

def main():
    # Pre-flight: commit check is already done (per brief prerequisite)

    while True:
        # ── Scan items ─────────────────────────────────────────────
        staged_plans = scan_clip_plans()
        draft_entries = scan_draft_queue()
        missing = find_missing_keys(read_env(ENV), EXAMPLE_ENV)
        act = recent_activity(5)

        os.system("cls" if os.name == "nt" else "clear")

        print("=" * 60)
        print("  DropsofKnowledge - Status Dashboard")
        print("=" * 60)
        print("  Loaded at: " + iso_now())
        print()

        # ── Pending clip plans ─────────────────────────────────────
        print("  PENDING CLIP PLANS (" + str(len(staged_plans)) + " staged):")
        for i, (path, data) in enumerate(staged_plans, start=1):
            ep = data.get("episode", "?")
            sp = data.get("speaker", "?")[:25]
            nclips = len(data.get("clips", []))
            print("  [" + str(i) + "] series=" + ep + " speaker=" + sp + " clips=" + str(nclips))
            if data.get("clips"):
                h = data["clips"][0].get("headline", "")
                if h:
                    print("       headline: " + h[:55])

        # ── Pending publish queue entries ────────────────────────────
        print("\n  PENDING PUBLISH QUEUE ENTRIES (" + str(len(draft_entries)) + " draft):")
        for i, e in enumerate(draft_entries, start=len(staged_plans) + 1):
            eid = e.get("id", "?")
            h = e.get("headline", "")[:45]
            plt = ", ".join(e.get("platforms", []))
            print("  [" + str(i) + "] " + eid + "  headline: " + h + "  platforms: " + plt)

        # ── Missing credentials ────────────────────────────────────
        print("\n  MISSING CREDENTIAL KEYS (" + str(len(missing)) + "):")
        if missing:
            for k in missing:
                print("    - " + k)
        else:
            print("    None -- all keys present in .env")

        # ── Recent activity ────────────────────────────────────────
        print("\n  LAST 5 ACTIVITIES:")
        if act:
            for a in act:
                t = a.get("time", "")[:16].replace("T", " ")
                d = a.get("detail", "")[:55] if a.get("detail") else ""
                print("    " + t + " -- " + a["what"], end="")
                if d:
                    print(":" + d, end="")
                print()
        else:
            print("    No completed renders or uploads found.")

        # ── Instructions ───────────────────────────────────────────
        print("\n  ? Number    -> approve that item")
        print("  e Number    -> open staging file in Notepad (clip plans only)")
        print("  q           -> quit")

        # ── Read input ─────────────────────────────────────────────
        choice = input("\n> ").strip()

        if choice == "q":
            break

        # e<number> -- open staging file
        if choice.startswith("e ") and len(choice) > 1:
            idx = int(choice[2:]) - 1  # 1-based to 0-based
            if 0 <= idx < len(staged_plans):
                path = staged_plans[idx][0]
                opener = "notepad.exe" if os.name == "nt" else "notepad"
                subprocess.run([opener, path], cwd=REPO, timeout=30)
            continue

        # plain number -- approve item
        if choice.isdigit():
            num = int(choice)
            # clip plans are 1..len(staged_plans)
            if 1 <= num <= len(staged_plans):
                path, data = staged_plans[num - 1]
                # Run the approve command
                cmd = [sys.executable, os.path.join(BASE, "generate_clip_plan.py"),
                       "--approve", path]
                rc, out = run_cmd(cmd)
                print("\n--- Command output ---")
                print(out[:2000] if out else "(no output)")
                input("Press Enter to continue...")
                continue

            # queue entries: offset after clip plans
            if len(staged_plans) < num <= len(staged_plans) + len(draft_entries):
                idx = num - len(staged_plans) - 1
                entry = draft_entries[idx]
                eid = entry.get("id", "")
                cmd = [sys.executable, os.path.join(BASE, "publish.py"), "approve", "--id", eid]
                rc, out = run_cmd(cmd)
                print("\n--- Command output ---")
                print(out[:2000] if out else "(no output)")
                input("Press Enter to continue...")
                continue

        # anything else -- stay in loop
        print("\n  Unrecognized input -- try a number, 'e N', or 'q'.")
        input("Press Enter to continue...")


if __name__ == "__main__":
    main()