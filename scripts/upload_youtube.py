#!/usr/bin/env python3
"""
YouTube uploader for Dropsofknowledge (AGENT_BRIEF task 6).

Reads an APPROVED entry from the publish queue and performs a real resumable
videos.insert upload (YouTube Data API v3, youtube.upload scope). If the
entry has a scheduled_time, the video is uploaded as private with a
publishAt so YouTube itself schedules it; otherwise it goes up as private
for manual review on the platform.

One-time setup (owner, interactive):
    python scripts\\upload_youtube.py --login
    -> browser consent, token saved to state/youtube_token.json

Publish one entry:
    python scripts\\upload_youtube.py --id ad_daa_0059_0059-02

Credentials come from .env only:
    YOUTUBE_CLIENT_ID=...apps.googleusercontent.com
    YOUTUBE_CLIENT_SECRET=...
    # optional overrides:
    YOUTUBE_CATEGORY_ID=27        # 27 = Education, 22 = People & Blogs
    YOUTUBE_TOKEN_FILE=state/youtube_token.json
"""

import argparse
import datetime
import json
import os
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import publish as pk

SCOPES = ['https://www.googleapis.com/auth/youtube.upload']
API_SERVICE_NAME = 'youtube'
API_VERSION = 'v3'


def die(msg, code=2):
    print(f'[ERROR] {msg}', flush=True)
    sys.exit(code)


def client_config_from_env(env):
    return {
        'installed': {
            'client_id': env.get('YOUTUBE_CLIENT_ID'),
            'client_secret': env.get('YOUTUBE_CLIENT_SECRET'),
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token',
            'redirect_uris': ['http://localhost'],
        }
    }


def get_service(env, login_only=False):
    tok_path = env.get('YOUTUBE_TOKEN_FILE') \
        or os.path.join(pk.ROOT, 'state', 'youtube_token.json')
    creds = None
    if os.path.exists(tok_path):
        creds = Credentials.from_authorized_user_file(tok_path, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if login_only:
            flow = InstalledAppFlow.from_client_config(
                client_config_from_env(env), SCOPES)
            creds = flow.run_local_server(port=0)
        else:
            die('No valid YouTube token. Run: '
                'python scripts\\upload_youtube.py --login  (once)')
    os.makedirs(os.path.dirname(tok_path), exist_ok=True)
    with open(tok_path, 'w', encoding='utf-8') as fh:
        fh.write(creds.to_json())
    return build(API_SERVICE_NAME, API_VERSION, credentials=creds)


def to_rfc3339_utc(local_iso):
    dt = datetime.datetime.fromisoformat(local_iso)
    if dt.tzinfo is None:
        dt = dt.astimezone()          # assume local timezone
    return dt.astimezone(datetime.timezone.utc) \
             .isoformat(timespec='seconds').replace('+00:00', 'Z')


def publish_entry(service, entry, env):
    clip_path = os.path.join(pk.ROOT, *entry['clip_file'].split('/'))
    if not os.path.exists(clip_path):
        raise FileNotFoundError(clip_path)
    body = {
        'snippet': {
            'title': entry['title'][:100],
            'description': entry['description'][:5000],
            'categoryId': str(env.get('YOUTUBE_CATEGORY_ID') or '27'),
        },
        'status': {
            'privacyStatus': 'private',
            'selfDeclaredMadeForKids': False,
        },
    }
    if entry.get('scheduled_time'):
        body['status']['publishAt'] = to_rfc3339_utc(entry['scheduled_time'])
    media = MediaFileUpload(clip_path, mimetype='video/mp4', chunksize=8 << 20,
                            resumable=True)
    request = service.videos().insert(
        part=','.join(body.keys()), body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f'  upload {int(status.progress() * 100)}%', flush=True)
    return response


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--login', action='store_true',
                    help='one-time OAuth consent; saves the token')
    ap.add_argument('--id', help='queue entry id')
    args = ap.parse_args()

    env = pk.load_env()
    if not args.login:
        q = pk.load_queue()
        entry = pk.require_entry(q, args.id)
        if entry['status'] != 'approved':
            die(f"{entry['id']} is '{entry['status']}' - only 'approved' "
                'entries can be published. Review/edit it, then approve it.')
        if 'youtube' in entry.get('published', {}):
            die(f"{entry['id']} already published to YouTube.")
        if 'youtube' not in entry.get('platforms', []):
            die(f"{entry['id']} does not target youtube "
                f"(targets {entry.get('platforms')}).")
    if not env.get('YOUTUBE_CLIENT_ID') or not env.get('YOUTUBE_CLIENT_SECRET'):
        die('YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET missing from .env.')
    service = get_service(env, login_only=args.login)
    if args.login and not args.id:
        print('[OK] Token saved. Future uploads need no interaction.')
        return 0

    print(f"[STEP] Uploading {entry['clip_file']} ...")
    try:
        resp = publish_entry(service, entry, env)
    except HttpError as exc:
        die(f'YouTube API error: {exc.status_code} {exc.reason}: '
            f"{exc.content[:300]}")

    video_id = resp.get('id')
    if not video_id:
        die(f'No video id in API response: {json.dumps(resp)[:200]}')
    entry.setdefault('published', {})['youtube'] = {
        'post_id': video_id,
        'url': f'https://youtu.be/{video_id}',
        'at': datetime.datetime.now(datetime.timezone.utc)
                       .isoformat(timespec='seconds'),
    }
    pk.save_queue(q)
    print(f"[OK] Confirmed by YouTube API: https://youtu.be/{video_id} "
          f"(publishAt={entry.get('scheduled_time') or 'manual'})")
    return 0


if __name__ == '__main__':
    sys.exit(main())
