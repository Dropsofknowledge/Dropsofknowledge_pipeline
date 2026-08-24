#!/usr/bin/env python3
"""
Dataset logger for Dropsofknowledge - storage only.

Scans Projects/ for lectures that have BOTH a transcript and a clip_plan.json
and records them into a SQLite database (state/dataset_log.db) together with
content hashes, so they survive as evaluation/training data even if project
folders are later deleted or modified.

This tool NEVER generates, edits, or validates clip plans. It only records
pairs that already exist on disk.

Usage:
    python scripts\\log_dataset.py            # scan Projects/, upsert, report
    python scripts\\log_dataset.py --db PATH  # custom database location
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRANSCRIPT_EXTS = ['.json', '.srt', '.vtt']


def log(level, msg):
    print(f"[{time.strftime('%H:%M:%S')}] [{level:<5}] {msg}", flush=True)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def now_iso():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def init_db(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS lectures (
        lecture_id   TEXT PRIMARY KEY,
        series       TEXT,
        episode      TEXT,
        speaker      TEXT,
        first_seen   TEXT NOT NULL,
        last_updated TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS transcripts (
        lecture_id  TEXT PRIMARY KEY REFERENCES lectures(lecture_id),
        file_name   TEXT NOT NULL,
        format      TEXT NOT NULL,
        n_cues      INTEGER,
        text_sha256 TEXT NOT NULL,
        content     TEXT NOT NULL,
        recorded_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS clip_plans (
        lecture_id  TEXT PRIMARY KEY REFERENCES lectures(lecture_id),
        file_name   TEXT NOT NULL,
        n_clips     INTEGER,
        content_sha TEXT NOT NULL,
        content     TEXT NOT NULL,
        recorded_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS clips (
        lecture_id  TEXT NOT NULL REFERENCES lectures(lecture_id),
        clip_id     TEXT NOT NULL,
        start_raw   TEXT,
        end_raw     TEXT,
        headline    TEXT,
        priority    REAL,
        confidence  REAL,
        flag        TEXT,
        PRIMARY KEY (lecture_id, clip_id)
    );
    """)


def parse_srt_cues(path):
    cues = []
    with open(path, encoding='utf-8-sig', errors='replace') as fh:
        blocks = fh.read().replace('\r', '').split('\n\n')
    for b in blocks:
        lines = [l for l in b.split('\n') if l.strip()]
        timing = next((l for l in lines if '-->' in l), None)
        if not timing:
            continue
        idx = lines.index(timing)
        text = ' '.join(lines[idx + 1:]).strip()
        if text:
            cues.append({'start': timing.split('-->')[0].strip(),
                         'end': timing.split('-->')[1].strip(),
                         'text': text})
    return cues


def read_transcript(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == '.json':
        with open(path, encoding='utf-8-sig', errors='replace') as fh:
            obj = json.load(fh)
        segs = obj.get('segments') if isinstance(obj, dict) else obj
        return [{'start': s.get('start'), 'end': s.get('end'),
                 'text': str(s.get('text', '')).strip()}
                for s in (segs or [])], 'json'
    return parse_srt_cues(path), ext.lstrip('.')


def to_seconds(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    parts = str(v).strip().split(':')
    total = 0.0
    for p in parts:
        p = p.strip().replace(',', '.')
        try:
            total = total * 60 + float(p)
        except ValueError:
            return None
    return total


def process_project(conn, proj_dir):
    name = os.path.basename(proj_dir.rstrip('\\/'))
    files = {f.lower(): f for f in os.listdir(proj_dir)}
    tpath = next((files[f'transcript{e}'] for e in TRANSCRIPT_EXTS
                  if f'transcript{e}' in files), None)
    ppath = files.get('clip_plan.json')
    if not tpath or not ppath:
        log('SKIP', f'{name}: missing '
                    f'{"transcript" if not tpath else "clip_plan.json"}')
        return False

    plan_path = os.path.join(proj_dir, ppath)
    t_full = os.path.join(proj_dir, tpath)
    with open(plan_path, encoding='utf-8-sig', errors='replace') as fh:
        plan_text = fh.read()
    plan = json.loads(plan_text)
    cues, fmt = read_transcript(t_full)

    lecture_id = name
    series = str(plan.get('series', ''))
    episode = str(plan.get('episode', ''))
    speaker = str(plan.get('speaker', ''))

    conn.execute(
        "INSERT INTO lectures (lecture_id, series, episode, speaker,"
        " first_seen, last_updated) VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(lecture_id) DO UPDATE SET series=excluded.series,"
        " episode=excluded.episode, speaker=excluded.speaker,"
        " last_updated=excluded.last_updated",
        (lecture_id, series, episode, speaker, now_iso(), now_iso()))

    t_sha = sha256_file(t_full)
    old = conn.execute(
        "SELECT text_sha256 FROM transcripts WHERE lecture_id=?",
        (lecture_id,)).fetchone()
    if not old or old[0] != t_sha:
        conn.execute(
            "INSERT INTO transcripts (lecture_id, file_name, format, n_cues,"
            " text_sha256, content, recorded_at) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(lecture_id) DO UPDATE SET file_name=excluded.file_name,"
            " format=excluded.format, n_cues=excluded.n_cues,"
            " text_sha256=excluded.text_sha256, content=excluded.content,"
            " recorded_at=excluded.recorded_at",
            (lecture_id, tpath, fmt, len(cues), t_sha,
             json.dumps(cues, ensure_ascii=False), now_iso()))
        log('OK', f'{name}: transcript {"updated" if old else "recorded"} '
                  f'({len(cues)} cues)')
    else:
        log('INFO', f'{name}: transcript unchanged')

    p_sha = hashlib.sha256(plan_text.encode('utf-8')).hexdigest()
    oldp = conn.execute(
        "SELECT content_sha FROM clip_plans WHERE lecture_id=?",
        (lecture_id,)).fetchone()
    if not oldp or oldp[0] != p_sha:
        conn.execute(
            "INSERT INTO clip_plans (lecture_id, file_name, n_clips,"
            " content_sha, content, recorded_at) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(lecture_id) DO UPDATE SET file_name=excluded.file_name,"
            " n_clips=excluded.n_clips, content_sha=excluded.content_sha,"
            " content=excluded.content, recorded_at=excluded.recorded_at",
            (lecture_id, ppath, len(plan.get('clips') or []), p_sha,
             plan_text, now_iso()))
        conn.execute("DELETE FROM clips WHERE lecture_id=?", (lecture_id,))
        for c in plan.get('clips') or []:
            conn.execute(
                "INSERT OR REPLACE INTO clips (lecture_id, clip_id, start_raw,"
                " end_raw, headline, priority, confidence, flag)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (lecture_id, str(c.get('id')), c.get('start'), c.get('end'),
                 c.get('headline'), c.get('priority'), c.get('confidence'),
                 c.get('flag')))
        log('OK', f'{name}: clip plan {"updated" if oldp else "recorded"} '
                  f'({len(plan.get("clips") or [])} clips)')
    else:
        log('INFO', f'{name}: clip plan unchanged')
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--db',
                    default=os.path.join(ROOT, 'state', 'dataset_log.db'))
    ap.add_argument('--projects-dir', default=os.path.join(ROOT, 'Projects'))
    args = ap.parse_args()

    if not os.path.isdir(args.projects_dir):
        log('ERROR', f'Projects folder not found: {args.projects_dir}')
        return 2

    os.makedirs(os.path.dirname(args.db), exist_ok=True)
    conn = sqlite3.connect(args.db)
    try:
        init_db(conn)
        n_pairs = 0
        for entry in sorted(os.listdir(args.projects_dir)):
            pd = os.path.join(args.projects_dir, entry)
            if os.path.isdir(pd):
                if process_project(conn, pd):
                    n_pairs += 1
        conn.commit()
        stats = conn.execute(
            "SELECT (SELECT COUNT(*) FROM lectures),"
            " (SELECT COUNT(*) FROM transcripts),"
            " (SELECT COUNT(*) FROM clip_plans),"
            " (SELECT COUNT(*) FROM clips)").fetchone()
        avg_conf = conn.execute(
            "SELECT ROUND(AVG(confidence), 3) FROM clips").fetchone()[0]
        log('STEP', f'Pairs found this run: {n_pairs}')
        log('STEP', f'DB totals: {stats[0]} lectures, {stats[1]} transcripts, '
                    f'{stats[2]} clip plans, {stats[3]} clips, '
                    f'mean confidence {avg_conf}')
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
