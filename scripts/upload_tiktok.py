#!/usr/bin/env python3
"""
TikTok uploader for Dropsofknowledge (AGENT_BRIEF task 6).

Direct Post flow against the free Content Posting API:
  creator_info/query -> video/init (FILE_UPLOAD) -> chunked PUT upload ->
  status/fetch polling until PUBLISH_COMPLETE.

IMPORTANT: until your TikTok app passes the Content Posting Audit
(developers.tiktok.com, typically 2-6 weeks), TikTok forces API posts to
SELF_ONLY (private). The privacy level is configurable via TIKTOK_PRIVACY_LEVEL.

One-time login (owner, interactive):
    python scripts\\upload_tiktok.py --login

Publish an approved entry:
    python scripts\\upload_tiktok.py --id <entry-id> [--dry-run]

Credentials come from .env only:
    TIKTOK_CLIENT_KEY=...
    TIKTOK_CLIENT_SECRET=...
    TIKTOK_PRIVACY_LEVEL=SELF_ONLY   # PUBLIC_TO_EVERYONE after audit
    # token saved by --login (gitignored):
    TIKTOK_TOKEN_FILE=state/tiktok_token.json
"""

import argparse
import datetime
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import publish as pk

BASE = 'https://open.tiktokapis.com'
SCOPES = 'user.info.basic,video.publish'


def die(msg, code=2):
    print(f'[ERROR] {msg}', flush=True)
    sys.exit(code)


def http_json(req):
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


# --------------------------------------------------------------------------- #
# OAuth with local-redirect login + refresh handling
# --------------------------------------------------------------------------- #
class _Callback(BaseHTTPRequestHandler):
    code = None

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _Callback.code = (q.get('code') or [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'TikTok authorized. Return to the terminal.')


def load_tokens(env):
    path = env.get('TIKTOK_TOKEN_FILE') \
        or os.path.join(pk.ROOT, 'state', 'tiktok_token.json')
    if not os.path.exists(path):
        return None, path
    with open(path, encoding='utf-8') as fh:
        return json.load(fh), path


def save_tokens(path, tokens):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(tokens, fh, indent=2)
    os.replace(tmp, path)


def refresh_if_needed(env, tokens, path):
    if not tokens:
        return None
    expires = float(tokens.get('obtained_at', 0)) \
        + int(tokens.get('expires_in', 0)) - 120
    if time.time() < expires or not tokens.get('refresh_token'):
        return tokens.get('access_token')
    body = urllib.parse.urlencode({
        'client_key': env['TIKTOK_CLIENT_KEY'],
        'client_secret': env['TIKTOK_CLIENT_SECRET'],
        'grant_type': 'refresh_token',
        'refresh_token': tokens['refresh_token'],
    }).encode()
    req = urllib.request.Request(f'{BASE}/v2/oauth/token/', data=body,
                                 method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    data = http_json(req)
    if data.get('access_token'):
        data['obtained_at'] = str(int(time.time()))
        save_tokens(path, data)
        print('[OK] TikTok token refreshed.')
        return data['access_token']
    die(f'Token refresh failed: {json.dumps(data)[:200]}')


def do_login(env):
    redirect = 'http://localhost:8899/callback'
    state = secrets.token_hex(8)
    verifier = secrets.token_urlsafe(32)
    auth = ('https://www.tiktok.com/v2/auth/authorize/?'
            + urllib.parse.urlencode({
                'client_key': env['TIKTOK_CLIENT_KEY'],
                'scope': SCOPES,
                'response_type': 'code',
                'redirect_uri': redirect,
                'state': state,
                'code_challenge': verifier,
                'code_challenge_method': 'plain',
              }))
    print('Opening browser for TikTok consent...')
    print(auth)
    webbrowser.open(auth)
    server = HTTPServer(('localhost', 8899), _Callback)
    server.handle_request()
    code = _Callback.code
    if not code:
        die('No authorization code received.')
    body = urllib.parse.urlencode({
        'client_key': env['TIKTOK_CLIENT_KEY'],
        'client_secret': env['TIKTOK_CLIENT_SECRET'],
        'code': code,
        'grant_type': 'authorization_code',
        'redirect_uri': redirect,
        'code_verifier': verifier,
    }).encode()
    req = urllib.request.Request(f'{BASE}/v2/oauth/token/', data=body,
                                 method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    data = http_json(req)
    if not data.get('access_token'):
        die(f'Login failed: {json.dumps(data)[:300]}')
    _, path = load_tokens(env)
    data['obtained_at'] = str(int(time.time()))
    save_tokens(path, data)
    print('[OK] Token saved. Future uploads need no interaction.')


# --------------------------------------------------------------------------- #
# direct post flow
# --------------------------------------------------------------------------- #
def api_post(token, url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 method='POST')
    req.add_header('Authorization', f'Bearer {token}')
    req.add_header('Content-Type', 'application/json; charset=UTF-8')
    return http_json(req)


def put_chunk(url, blob, start, total):
    req = urllib.request.Request(url, data=blob, method='PUT')
    end = start + len(blob) - 1
    req.add_header('Content-Range',
                   f'bytes {start}-{end}/{total}')
    req.add_header('Content-Type', 'video/mp4')
    with urllib.request.urlopen(req, timeout=1800) as resp:
        resp.read()


def publish_entry(env, token, entry):
    clip_path = os.path.join(pk.ROOT, *entry['clip_file'].split('/'))
    if not os.path.exists(clip_path):
        raise FileNotFoundError(clip_path)
    size = os.path.getsize(clip_path)

    info = api_post(token, f'{BASE}/v2/post/publish/creator_info/query/', {})
    creator = info.get('data') or {}
    print(f"  creator: {creator.get('creator_username') or creator}",
          flush=True)

    chunk = min(size, 64 << 20)
    init = api_post(token, f'{BASE}/v2/post/publish/video/init/', {
        'post_info': {
            'title': entry['title'][:2200],
            'privacy_level': env.get('TIKTOK_PRIVACY_LEVEL') or 'SELF_ONLY',
            'disable_comment': False,
            'video_cover_timestamp_ms': 1000,
        },
        'source_info': {
            'source': 'FILE_UPLOAD',
            'video_size': size,
            'chunk_size': chunk,
            'total_chunk_count': max(1, -(-size // chunk)),
        },
    })
    err = (init.get('error') or {})
    if err.get('code') and err['code'] != 'ok':
        raise RuntimeError(f"init failed: {json.dumps(init)[:300]}")
    upload_url = ((init.get('data') or {}).get('upload_url'))
    pub_id = (init.get('data') or {}).get('publish_id')
    if not upload_url or not pub_id:
        # some responses omit upload_url; fall back to generic endpoint
        upload_url = (f'https://open-upload.tiktokapis.com/v2/post/publish/'
                      f'content/upload/?publish_id={pub_id}')

    sent = 0
    with open(clip_path, 'rb') as fh:
        while True:
            blob = fh.read(chunk)
            if not blob:
                break
            put_chunk(upload_url, blob, sent, size)
            sent += len(blob)
            print(f'  uploaded {sent}/{size}', flush=True)

    deadline = time.time() + 900
    while True:
        st = api_post(token, f'{BASE}/v2/post/publish/status/fetch/',
                      {'publish_id': pub_id})
        status = (st.get('data') or {}).get('status')
        if status == 'PUBLISH_COMPLETE':
            return pub_id
        if status in ('FAILED', 'PUBLISH_FAILED'):
            raise RuntimeError(f"TikTok processing failed: "
                               f"{json.dumps(st)[:300]}")
        if time.time() > deadline:
            raise RuntimeError(f'timed out waiting; last={json.dumps(st)[:200]}')
        time.sleep(5)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--login', action='store_true',
                    help='one-time TikTok OAuth consent')
    ap.add_argument('--id', help='queue entry id')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    env = pk.load_env()

    if args.login and not args.id:
        do_login(env)
        return 0

    q = pk.load_queue()
    entry = pk.require_entry(q, args.id)
    if entry['status'] != 'approved':
        die(f"{entry['id']} is '{entry['status']}' - only approved entries "
            'can be published.')
    if 'tiktok' in entry.get('published', {}):
        die(f"{entry['id']} already published to TikTok.")
    if 'tiktok' not in entry.get('platforms', []):
        die(f"{entry['id']} does not target tiktok.")
    clip_path = os.path.join(pk.ROOT, *entry['clip_file'].split('/'))
    if not os.path.exists(clip_path):
        die(f'Clip missing: {clip_path}')
    missing = [k for k in ('TIKTOK_CLIENT_KEY', 'TIKTOK_CLIENT_SECRET')
               if not env.get(k)]
    if missing:
        die(f'Missing .env keys: {missing}')

    tokens, tok_path = load_tokens(env)
    token = refresh_if_needed(env, tokens, tok_path)
    if not token:
        die('No TikTok token. Run: python scripts\\upload_tiktok.py --login')

    if args.dry_run:
        who = api_post(token, f'{BASE}/v2/post/publish/creator_info/query/', {})
        print(f"[DRY] Auth OK. Creator info: "
              f"{json.dumps((who.get('data') or {}))[:200]}")
        print(f"[DRY] Would post as "
              f"{env.get('TIKTOK_PRIVACY_LEVEL') or 'SELF_ONLY'}: "
              f"{entry['title']!r}")
        return 0

    print(f"[STEP] Direct-posting {entry['clip_file']} to TikTok ...")
    try:
        post_id = publish_entry(env, token, entry)
    except RuntimeError as exc:
        die(str(exc))
    entry.setdefault('published', {})['tiktok'] = {
        'post_id': post_id,
        'url': f'https://www.tiktok.com/@me/video/{post_id}',
        'at': datetime.datetime.now(datetime.timezone.utc)
                       .isoformat(timespec='seconds'),
        'note': 'verify URL on the account; API returns publish_id',
    }
    pk.save_queue(q)
    print(f'[OK] Confirmed PUBLISH_COMPLETE: publish_id {post_id}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
