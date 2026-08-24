#!/usr/bin/env python3
"""
Instagram Reels uploader for Dropsofknowledge (AGENT_BRIEF task 6).

Publishes an approved queue entry as a Reel on an Instagram professional
(Business/Creator) account using the free Graph API:

  1. POST /{ig-user-id}/media  (media_type=REELS, upload_type=resumable)
  2. binary upload to rupload.facebook.com (no public server needed)
  3. poll container status until FINISHED
  4. POST /{ig-user-id}/media_publish

Requirements (owner setup, see docs/PUBLISHING_SETUP.md):
  - Instagram Business or Creator account linked to a Facebook Page
  - Meta app with instagram_business_basic + instagram_business_content_publish
    (your own accounts work in Development Mode - no formal review needed)

Discover your IG account id:
    python scripts\\upload_instagram.py --discover

Publish an approved entry:
    python scripts\\upload_instagram.py --id <entry-id>

Credentials come from .env only:
    IG_USER_ID=1784xxxxxxxxxxx
    IG_ACCESS_TOKEN=...
    FB_GRAPH_VERSION=v21.0
"""

import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import publish as pk


def die(msg, code=2):
    print(f'[ERROR] {msg}', flush=True)
    sys.exit(code)


def graph(method, url, token):
    req = urllib.request.Request(url, method=method)
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    attempt = 0
    while True:
        attempt += 1
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
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


def env_cfg(env):
    ver = env.get('FB_GRAPH_VERSION') or 'v21.0'
    ig_id = env.get('IG_USER_ID')
    token = env.get('IG_ACCESS_TOKEN')
    return ver, ig_id, token


def discover(env):
    page_id = env.get('FB_PAGE_ID')
    token = env.get('FB_PAGE_ACCESS_TOKEN') or env.get('IG_ACCESS_TOKEN')
    if not page_id or not token:
        die('--discover needs FB_PAGE_ID and a Page/user token in .env.')
    ver = env.get('FB_GRAPH_VERSION') or 'v21.0'
    data = graph('GET', f'https://graph.facebook.com/{ver}/{page_id}'
                 f'?fields=instagram_accounts{{id,username}}'
                 f'&access_token={token}', None)
    accounts = ((data.get('instagram_accounts') or {}).get('data')) or []
    if not accounts:
        die('No Instagram professional account linked to that Page. '
            'Link one in the Instagram app: Settings -> Business/Creator.')
    for a in accounts:
        print(f"IG account id={a['id']}  @{a.get('username')}")
    print('\nPut this in .env:')
    print(f'IG_USER_ID={accounts[0]["id"]}')


def publish_entry(env, entry):
    ver, ig_id, token = env_cfg(env)
    if not ig_id or not token:
        die('Missing IG_USER_ID / IG_ACCESS_TOKEN in .env.')
    clip_path = os.path.join(pk.ROOT, *entry['clip_file'].split('/'))
    size = os.path.getsize(clip_path)

    # 1. resumable container
    fields = urllib.parse.urlencode({
        'media_type': 'REELS',
        'upload_type': 'resumable',
        'caption': f"{entry['title']}\n\n{entry['description']}"[:2200],
    }).encode()
    req = urllib.request.Request(
        f'https://graph.facebook.com/{ver}/{ig_id}/media', data=fields,
        method='POST')
    req.add_header('Authorization', f'Bearer {token}')
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            cont = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        die(f'container creation failed: '
            f'{exc.read().decode("utf-8", "replace")[:300]}')
    container = cont.get('id')
    uri = cont.get('uri')
    if not container:
        die(f'no container id: {json.dumps(cont)[:200]}')
    print(f'  container {container}', flush=True)

    # 2. binary upload to rupload host
    up_host = uri or (f'https://rupload.facebook.com/ig-api-upload/{ver}/'
                      f'{container}')
    with open(clip_path, 'rb') as fh:
        blob = fh.read()
    up_req = urllib.request.Request(up_host, data=blob, method='POST')
    up_req.add_header('Authorization', f'OAuth {token}')
    up_req.add_header('Content-Type', 'video/mp4')
    up_req.add_header('offset', '0')
    up_req.add_header('file_size', str(size))
    try:
        with urllib.request.urlopen(up_req, timeout=1800) as resp:
            print('  upload:', resp.read().decode()[:120], flush=True)
    except urllib.error.HTTPError as exc:
        die(f'rupload failed ({exc.code}): '
            f'{exc.read().decode("utf-8", "replace")[:300]}')

    # 3. poll status until FINISHED
    deadline = time.time() + 900
    while True:
        st = graph('GET', f'https://graph.facebook.com/{ver}/{container}'
                   f'?fields=status_code&access_token={token}', None)
        code = st.get('status_code')
        if code == 'FINISHED':
            break
        if code in ('ERROR', 'EXPIRED'):
            die(f'container processing failed: {json.dumps(st)[:200]}')
        if time.time() > deadline:
            die(f'timed out waiting for processing; last={code}')
        time.sleep(5)
    print('  container FINISHED', flush=True)

    # 4. publish
    pub_fields = urllib.parse.urlencode({'creation_id': container}).encode()
    pub_req = urllib.request.Request(
        f'https://graph.facebook.com/{ver}/{ig_id}/media_publish',
        data=pub_fields, method='POST')
    pub_req.add_header('Authorization', f'Bearer {token}')
    with urllib.request.urlopen(pub_req, timeout=120) as resp:
        result = json.loads(resp.read().decode())
    media_id = result.get('id')
    if not media_id:
        die(f'publish failed: {json.dumps(result)[:200]}')
    return media_id


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--discover', action='store_true',
                    help='list the IG account(s) linked to FB_PAGE_ID')
    ap.add_argument('--id', help='queue entry id')
    args = ap.parse_args()

    env = pk.load_env()
    if args.discover and not args.id:
        discover(env)
        return 0

    q = pk.load_queue()
    entry = pk.require_entry(q, args.id)
    if entry['status'] != 'approved':
        die(f"{entry['id']} is '{entry['status']}' - only approved entries "
            'can be published.')
    if 'instagram' in entry.get('published', {}):
        die(f"{entry['id']} already published to Instagram.")
    if 'instagram' not in entry.get('platforms', []):
        die(f"{entry['id']} does not target instagram.")
    dur = entry.get('duration_sec') or 0
    if dur > 170:
        print(f"[WARN] {dur:.0f}s exceeds the ~90s Reels-tab guideline; "
              'it may still publish as a >90s reel (3-min cap since 2025).')
    clip_path = os.path.join(pk.ROOT, *entry['clip_file'].split('/'))
    if not os.path.exists(clip_path):
        die(f'Clip missing: {clip_path}')

    print(f"[STEP] Publishing {entry['clip_file']} as an Instagram Reel ...")
    media_id = publish_entry(env, entry)
    entry.setdefault('published', {})['instagram'] = {
        'post_id': media_id,
        'url': f'https://www.instagram.com/reel/{media_id}',
        'at': datetime.datetime.now(datetime.timezone.utc)
                       .isoformat(timespec='seconds'),
    }
    pk.save_queue(q)
    print(f'[OK] Confirmed by Graph API: media id {media_id}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
