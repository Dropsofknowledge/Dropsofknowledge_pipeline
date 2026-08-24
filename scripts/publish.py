#!/usr/bin/env python3
"""
Publishing framework core for Dropsofknowledge - approval queue, LLM title/
description drafting, and deterministic scheduling.

Scope guard (per AGENT_BRIEF task 6): this tool NEVER decides what gets
clipped. Entries are created only from clips that already exist in a
project's output/ folder, produced from a human-authored clip_plan.json.
Drafting only writes text into queue entries and never approves them;
only a human flips an entry to "approved".

Queue file: state/publish_queue.json

Usage:
    python scripts\\publish.py add --project Projects\\ad_daa_0059 ^
        [--clips 0059-01,0059-02] [--platforms youtube,x,facebook]
    python scripts\\publish.py list
    python scripts\\publish.py draft [--id ENTRY_ID | --all-drafts]
    python scripts\\publish.py approve --id ENTRY_ID
    python scripts\\publish.py unapprove --id ENTRY_ID
    python scripts\\publish.py schedule [--every-days N] [--at HH:MM]
                                       [--start YYYY-MM-DD]
    python scripts\\publish.py show --id ENTRY_ID
"""

import argparse
import datetime
import io
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUEUE_PATH = os.path.join(ROOT, 'state', 'publish_queue.json')
DB_PATH = os.path.join(ROOT, 'state', 'dataset_log.db')
PLATFORMS = ('youtube', 'tiktok', 'instagram', 'facebook')

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, 'reconfigure'):
        try:
            _s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


def log(level, msg):
    print(f"[{time.strftime('%H:%M:%S')}] [{level:<5}] {msg}", flush=True)


def die(msg, code=2):
    log('ERROR', msg)
    sys.exit(code)


# --------------------------------------------------------------------------- #
# env
# --------------------------------------------------------------------------- #
def load_env():
    path = os.path.join(ROOT, '.env')
    env = {}
    if os.path.exists(path):
        with open(path, encoding='utf-8-sig') as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, _, v = line.partition('=')
                env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if v})
    return env


# --------------------------------------------------------------------------- #
# queue persistence (atomic writes; schema is plain JSON on purpose)
# --------------------------------------------------------------------------- #
def load_queue():
    if os.path.exists(QUEUE_PATH):
        with open(QUEUE_PATH, encoding='utf-8') as fh:
            return json.load(fh)
    return {'version': 1, 'entries': []}


def save_queue(q):
    os.makedirs(os.path.dirname(QUEUE_PATH), exist_ok=True)
    tmp = QUEUE_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(q, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, QUEUE_PATH)


def get_entry(q, entry_id):
    for e in q['entries']:
        if e['id'] == entry_id:
            return e
    return None


def require_entry(q, entry_id):
    e = get_entry(q, entry_id)
    if not e:
        die(f'No queue entry {entry_id!r}. See `list`.')
    return e


def parse_platforms(raw):
    plats = [p.strip().lower() for p in (raw or '').split(',') if p.strip()]
    bad = [p for p in plats if p not in PLATFORMS]
    if bad:
        die(f'Unknown platform(s): {bad}. Valid: {",".join(PLATFORMS)}')
    return plats


# --------------------------------------------------------------------------- #
# add - creates drafts ONLY from existing rendered clips
# --------------------------------------------------------------------------- #
def sec(v):
    total = 0.0
    for p in str(v).split(':'):
        total = total * 60 + float(p.replace(',', '.'))
    return total


def cmd_add(args):
    proj = os.path.abspath(args.project)
    if not os.path.isdir(proj):
        die(f'Project folder not found: {proj}')
    plan_path = os.path.join(proj, 'clip_plan.json')
    if not os.path.exists(plan_path):
        die('clip_plan.json missing - nothing to add.')
    with open(plan_path, encoding='utf-8-sig') as fh:
        plan = json.load(fh)
    lecture_id = os.path.basename(proj)
    platforms = parse_platforms(args.platforms)
    want = set(c.strip() for c in args.clips.split(',') if c.strip()) \
        if args.clips else None

    q = load_queue()
    added = skipped = 0
    for clip in plan.get('clips') or []:
        cid = str(clip.get('id'))
        if want and cid not in want:
            continue
        rel = f'output/{cid}/clip.mp4'
        mp4 = os.path.join(proj, *rel.split('/'))
        if not os.path.exists(mp4) or os.path.getsize(mp4) < 1024:
            log('SKIP', f'{cid}: not rendered yet ({rel})')
            skipped += 1
            continue
        eid = f'{lecture_id}_{cid}'
        if get_entry(q, eid):
            log('SKIP', f'{eid}: already in queue')
            skipped += 1
            continue
        start_s = sec(clip['start'])
        end_s = sec(clip['end'])
        q['entries'].append({
            'id': eid,
            'status': 'draft',
            'lecture_id': lecture_id,
            'clip_id': cid,
            'headline': clip.get('headline', ''),
            'speaker': plan.get('speaker', ''),
            'series': plan.get('series', ''),
            'clip_file': os.path.relpath(mp4, ROOT).replace('\\', '/'),
            'duration_sec': round(end_s - start_s, 3),
            'clip_start_sec': round(start_s, 3),
            'clip_end_sec': round(end_s, 3),
            'platforms': platforms,
            'title': '',
            'description': '',
            'scheduled_time': None,
            'published': {},
        })
        added += 1
        log('OK', f'added draft {eid} -> {rel}')
    save_queue(q)
    log('STEP', f'{added} added, {skipped} skipped')


# --------------------------------------------------------------------------- #
# transcript segment lookup (from the dataset log recorded earlier)
# --------------------------------------------------------------------------- #
def transcript_segment(lecture_id, start_s, end_s, max_chars=1800):
    """Pull the clip's transcript text from the dataset log (storage only)."""
    if not os.path.exists(DB_PATH):
        return None
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            'SELECT lecture_id, content FROM transcripts').fetchall()
        content = None
        for lid, c in rows:
            if lid.lower() == lecture_id.lower():
                content = c
                break
        if content is None:
            return None
        cues = json.loads(content)
        parts, used = [], 0
        for c in cues:
            cs, ce = sec(c['start']), sec(c['end'])
            if ce <= start_s or cs >= end_s:
                continue
            parts.append(c['text'])
            used += len(c['text']) + 1
            if used >= max_chars:
                break
        return ' '.join(parts)[:max_chars] or None
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# LLM drafting - provider agnostic, same pattern as transcribe.py
# --------------------------------------------------------------------------- #
def chat_completion(env, system, user):
    key = env.get('GROQ_API_KEY') or env.get('OPENAI_API_KEY') \
        or env.get('DRAFT_LLM_API_KEY')
    base = (env.get('DRAFT_LLM_BASE_URL')
            or 'https://api.groq.com/openai/v1').rstrip('/')
    model = env.get('DRAFT_LLM_MODEL') or 'openai/gpt-oss-120b'
    if not key:
        die('No LLM key found (GROQ_API_KEY in .env).')
    body = json.dumps({
        'model': model,
        'temperature': float(env.get('DRAFT_LLM_TEMPERATURE', '0.7')),
        'messages': [{'role': 'system', 'content': system},
                     {'role': 'user', 'content': user}],
    }).encode()
    req = urllib.request.Request(f'{base}/chat/completions', data=body,
                                 method='POST')
    req.add_header('Authorization', f'Bearer {key}')
    req.add_header('Content-Type', 'application/json')
    req.add_header('User-Agent', 'DropsofKnowledge-Pipeline/1.0')
    attempt = 0
    while True:
        attempt += 1
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode())
                return data['choices'][0]['message']['content']
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', 'replace')[:300]
            retryable = exc.code >= 500 or exc.code == 429
            if not retryable or attempt >= 4:
                die(f'LLM error {exc.code}: {detail}')
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt >= 4:
                die('LLM unreachable after retries')
        time.sleep(min(30, 2 ** attempt))


DRAFT_SYSTEM = (
    'You write social-media titles and descriptions for short clips from '
    'Islamic lectures (Quran/Sunnah mainstream teaching). Rules: '
    '(1) Title max 90 chars, clear and dignified, no clickbait, no ALL CAPS, '
    'no emoji, do NOT invent quotes or promises. '
    '(2) Description 2-3 sentences: what the speaker addresses, then a '
    'neutral invitation to watch/listen. Include the speaker name. '
    '(3) Reply with STRICT JSON only: {"title": "...", "description": "..."}')


def draft_entry(env, entry):
    segment = transcript_segment(entry['lecture_id'],
                                 entry.get('clip_start_sec', 0),
                                 entry.get('clip_end_sec', 0))
    if not segment:
        segment = ''
    user = (f"Series: {entry.get('series')}\n"
            f"Speaker: {entry.get('speaker')}\n"
            f"Editorial headline: {entry.get('headline')}\n"
            f"Clip transcript excerpt:\n{segment or entry.get('headline')}\n\n"
            'Write the title and description.')
    raw = chat_completion(env, DRAFT_SYSTEM, user)
    m = re.search(r'\{.*\}', raw, re.S)
    if not m:
        raise ValueError(f'no JSON in model reply: {raw[:200]}')
    obj = json.loads(m.group(0))
    entry['title'] = str(obj.get('title', '')).strip()
    entry['description'] = str(obj.get('description', '')).strip()
    if not entry['title']:
        raise ValueError('model returned empty title')


def cmd_draft(args):
    env = load_env()
    q = load_queue()
    targets = []
    if args.id:
        targets = [require_entry(q, args.id)]
    elif args.all_drafts:
        targets = [e for e in q['entries'] if e['status'] == 'draft']
    else:
        die('Give --id ENTRY_ID or --all-drafts.')
    ok = failed = 0
    for e in targets:
        try:
            draft_entry(env, e)
            save_queue(q)          # persist progress after each entry
            log('OK', f"{e['id']} drafted: \"{e['title']}\"")
            ok += 1
        except Exception as exc:
            failed += 1
            log('ERROR', f"{e['id']}: drafting failed: {exc}")
    log('STEP', f'Drafted {ok}, failed {failed}. Status stays "draft" - '
                f'editing + approving is yours.')


# --------------------------------------------------------------------------- #
# approve / list / show
# --------------------------------------------------------------------------- #
def cmd_approve(args):
    q = load_queue()
    e = require_entry(q, args.id)
    if e['status'] == 'published':
        die(f"{e['id']} is already published.")
    if not (e.get('title') and e.get('description')):
        log('WARN', 'Entry has no drafted/edited title or description yet.')
    if not e.get('platforms'):
        die('Entry has no platforms set.')
    e['status'] = 'approved'
    e['approved_at'] = now_iso()
    save_queue(q)
    log('OK', f"{e['id']} -> approved")


def cmd_unapprove(args):
    q = load_queue()
    e = require_entry(q, args.id)
    if e.get('published'):
        die('Cannot unapprove a published entry.')
    e['status'] = 'draft'
    save_queue(q)
    log('OK', f"{e['id']} -> draft")


def cmd_list(args):
    q = load_queue()
    if not q['entries']:
        print('(queue empty)')
    for e in q['entries']:
        sched = e.get('scheduled_time') or '-'
        pubs = ','.join(sorted(e.get('published', {}))) or '-'
        print(f"{e['status']:9} {e['id']:28} [{','.join(e['platforms']):23}] "
              f"sched={sched:16} pub={pubs:16} "
              f"title={(e.get('title') or '')[:48]}")


def cmd_show(args):
    q = load_queue()
    e = require_entry(q, args.id)
    print(json.dumps(e, ensure_ascii=False, indent=2))


# --------------------------------------------------------------------------- #
# deterministic scheduling - plain arithmetic, no model calls
# --------------------------------------------------------------------------- #
def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def cmd_schedule(args):
    q = load_queue()
    every = args.every_days
    at_h, at_m = map(int, args.at.split(':'))
    if args.start:
        cursor = datetime.datetime.fromisoformat(args.start)
    else:
        cursor = datetime.datetime.now() + datetime.timedelta(days=1)
    slot0 = cursor.replace(hour=at_h, minute=at_m, second=0, microsecond=0)

    waiting = [e for e in q['entries']
               if e['status'] == 'approved' and not e.get('scheduled_time')]
    waiting.sort(key=lambda e: e['id'])
    if not waiting:
        log('INFO', 'No approved entries awaiting a slot.')
        return
    # rotate platforms across consecutive entries when several share targets
    rotation = []
    for i, e in enumerate(waiting):
        rotation.append(e['platforms'][i % len(e['platforms'])])
    for i, e in enumerate(waiting):
        slot = slot0 + datetime.timedelta(days=i * every)
        e['scheduled_time'] = slot.isoformat(timespec='minutes')
        e['platform_rotation'] = rotation[i]
        log('OK', f"{e['id']}: {e['scheduled_time']} "
                  f"(primary {rotation[i]})")
    save_queue(q)


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('add', help='create draft entries from rendered clips')
    p.add_argument('--project', required=True)
    p.add_argument('--clips', help='comma list of clip ids (default: all)')
    p.add_argument('--platforms', default='youtube',
                   help=f'comma list from {",".join(PLATFORMS)}')
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser('list', help='show all queue entries')
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser('show', help='dump one entry as JSON')
    p.add_argument('--id', required=True)
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser('draft', help='LLM-draft title/description (stays draft)')
    p.add_argument('--id')
    p.add_argument('--all-drafts', action='store_true')
    p.set_defaults(fn=cmd_draft)

    p = sub.add_parser('approve', help='human approval: flip draft->approved')
    p.add_argument('--id', required=True)
    p.set_defaults(fn=cmd_approve)

    p = sub.add_parser('unapprove', help='flip back to draft')
    p.add_argument('--id', required=True)
    p.set_defaults(fn=cmd_unapprove)

    p = sub.add_parser('schedule',
                       help='assign fixed-cadence slots to approved entries')
    p.add_argument('--every-days', type=int, default=2)
    p.add_argument('--at', default='17:00')
    p.add_argument('--start', help='YYYY-MM-DD first slot date')
    p.set_defaults(fn=cmd_schedule)

    args = ap.parse_args()
    args.fn(args)


if __name__ == '__main__':
    main()
