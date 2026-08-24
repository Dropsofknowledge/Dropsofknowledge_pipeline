#!/usr/bin/env python3
"""
Facebook Page video uploader for Dropsofknowledge (AGENT_BRIEF task 6).

Reads an APPROVED entry from the publish queue and POSTs the clip to
Graph API /{page-id}/videos. If the entry has a scheduled_time (>=10 min
and <=30 days ahead), it uses Facebook's native scheduling:
published=false + scheduled_publish_time=<unix ts>. Otherwise it posts as
an unpublished draft for manual review on the Page.

Publish one entry:
    python scripts\\upload_facebook.py --id ad_daa_0059_0059-02

Credentials come from .env only:
    FB_PAGE_ID=1234567890
    FB_PAGE_ACCESS_TOKEN=...      # Page token with pages_manage_posts
    # optional:
    FB_GRAPH_VERSION=v21.0
"""

import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import publish as pk


def die(msg, code=2):
    print(f'[ERROR] {msg}', flush=True)
    sys.exit(code)


def graph_post(url, fields, file_field, file_path):
    boundary = 'dokfb' + uuid.uuid4().hex
    with open(file_path, 'rb') as fh:
        payload = fh.read()
    lines = []
    for k, v in fields.items():
        if v is None:
            continue
        lines.append(f'--{boundary}'.encode())
        lines.append(f'Content-Disposition: form-data; name="{k}"'.encode())
        lines.append(b'')
        lines.append(str(v).encode('utf-8'))
    lines.append(f'--{boundary}'.encode())
    lines.append(f'Content-Disposition: form-data; name="{file_field}"; '
                 f'filename="clip.mp4"'.encode())
    lines.append(b'Content-Type: video/mp4')
    lines.append(b'')
    lines.append(payload)
    lines.append(f'--{boundary}--'.encode())
    body = b'\r\n'.join(lines)

    req = urllib.request.Request(url, data=body, method='POST')
    req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
    attempt = 0
    while True:
        attempt += 1
        try:
            with urllib.request.urlopen(req, timeout=1800) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', 'replace')[:400]
            retryable = exc.code == 429 or exc.code >= 500
            if not retryable or attempt >= 4:
                raise RuntimeError(f'HTTP {exc.code}: {detail}') from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt >= 4:
                raise RuntimeError(f'network: {exc}') from None
        time.sleep(min(30, 2 ** attempt))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--id', required=True, help='queue entry id')
    args = ap.parse_args()

    env = pk.load_env()
    version = env.get('FB_GRAPH_VERSION') or 'v21.0'
    q = pk.load_queue()
    entry = pk.require_entry(q, args.id)
    if entry['status'] != 'approved':
        die(f"{entry['id']} is '{entry['status']}' - only approved entries "
            'can be published.')
    if 'facebook' in entry.get('published', {}):
        die(f"{entry['id']} already published to Facebook.")
    if 'facebook' not in entry.get('platforms', []):
        die(f"{entry['id']} does not target facebook.")
    clip_path = os.path.join(pk.ROOT, *entry['clip_file'].split('/'))
    if not os.path.exists(clip_path):
        die(f'Clip missing: {clip_path}')
    page_id = env.get('FB_PAGE_ID')
    token = env.get('FB_PAGE_ACCESS_TOKEN')
    missing = [k for k in ('FB_PAGE_ID', 'FB_PAGE_ACCESS_TOKEN')
               if not env.get(k)]
    if missing:
        die(f'Missing .env keys: {missing}')

    fields = {
        'description': f"{entry['title']}\n\n{entry['description']}",
        'access_token': token,
        'title': entry['title'][:255],
    }
    sched_note = 'unpublished (manual review)'
    if entry.get('scheduled_time'):
        dt = datetime.datetime.fromisoformat(entry['scheduled_time'])
        if dt.tzinfo is None:
            dt = dt.astimezone()
        ts = int(dt.timestamp())
        now = time.time()
        if ts < now + 600 or ts > now + 30 * 86400:
            die(f"{entry['id']}: Facebook requires scheduled_publish_time "
                'between 10 minutes and 30 days ahead '
                f"(got {dt.isoformat()}).")
        fields['published'] = 'false'
        fields['scheduled_publish_time'] = str(ts)
        sched_note = f'natively scheduled for {dt.isoformat()}'

    url = f'https://graph.facebook.com/{version}/{page_id}/videos'
    print(f"[STEP] Uploading {clip_path} to Facebook page {page_id} "
          f"({sched_note}) ...")
    try:
        resp = graph_post(url, fields, 'source', clip_path)
    except RuntimeError as exc:
        die(str(exc))
    post_id = resp.get('id') or (resp.get('video_id') if isinstance(resp, dict)
                                 else None)
    if not post_id:
        die(f'Unexpected API response: {json.dumps(resp)[:300]}')
    entry.setdefault('published', {})['facebook'] = {
        'post_id': post_id,
        'url': f'https://graph.facebook.com/{version}/{post_id}',
        'at': datetime.datetime.now(datetime.timezone.utc)
                       .isoformat(timespec='seconds'),
    }
    pk.save_queue(q)
    print(f"[OK] Confirmed by Graph API: video id {post_id} ({sched_note})")
    return 0


if __name__ == '__main__':
    sys.exit(main())
