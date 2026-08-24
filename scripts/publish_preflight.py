#!/usr/bin/env python3
"""
Publishing preflight - checks everything that CAN be checked before any
platform upload is attempted. Never posts/uploads anything.

    python scripts\\publish_preflight.py            # config + queue checks
    python scripts\\publish_preflight.py --live-x   # also ping X auth endpoint
    python scripts\\publish_preflight.py --live-fb  # also read page name

Exit code 0 = everything ready for real uploads (given approved entries);
non-zero = something needs attention. Run this first whenever new keys land.
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import publish as pk

RESULTS = []


def check(name, ok, detail=''):
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}"
          f"{(' - ' + detail) if detail else ''}")


def section(title):
    print(f"\n== {title} ==")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--live-tiktok', action='store_true',
                    help='make an auth-only creator_info query (no post)')
    ap.add_argument('--live-fb', action='store_true',
                    help='make a read-only GET of the page name')
    ap.add_argument('--live-ig', action='store_true',
                    help='make a read-only GET of the IG username')
    args = ap.parse_args()
    env = pk.load_env()

    section('.env credentials')
    yt = bool(env.get('YOUTUBE_CLIENT_ID')) and \
        bool(env.get('YOUTUBE_CLIENT_SECRET'))
    check('YOUTUBE_CLIENT_ID/SECRET set', yt)
    tok = env.get('YOUTUBE_TOKEN_FILE') \
        or os.path.join(pk.ROOT, 'state', 'youtube_token.json')
    check('youtube OAuth token file exists', os.path.exists(tok),
          '' if os.path.exists(tok)
          else 'run: python scripts\\upload_youtube.py --login')
    ttkeys = ['TIKTOK_CLIENT_KEY', 'TIKTOK_CLIENT_SECRET']
    missing_tt = [k for k in ttkeys if not env.get(k)]
    check('TIKTOK_CLIENT_KEY/SECRET set', not missing_tt,
          f'missing: {missing_tt}' if missing_tt else '')
    tt_tok = env.get('TIKTOK_TOKEN_FILE') \
        or os.path.join(pk.ROOT, 'state', 'tiktok_token.json')
    check('tiktok OAuth token file exists', os.path.exists(tt_tok),
          '' if os.path.exists(tt_tok)
          else 'run: python scripts\\upload_tiktok.py --login')
    ig = bool(env.get('IG_USER_ID')) and bool(env.get('IG_ACCESS_TOKEN'))
    check('IG_USER_ID/IG_ACCESS_TOKEN set (instagram)', ig)
    fb = bool(env.get('FB_PAGE_ID')) and bool(env.get('FB_PAGE_ACCESS_TOKEN'))
    check('FB_PAGE_ID/FB_PAGE_ACCESS_TOKEN set', fb)

    section('publish queue')
    q = pk.load_queue()
    entries = q.get('entries') or []
    check('queue has entries', bool(entries), f'{len(entries)} entries')
    problems = 0
    for e in entries:
        clip = os.path.join(pk.ROOT, *e.get('clip_file', '').split('/'))
        if e['status'] != 'published':
            if not os.path.exists(clip):
                problems += 1
                check(f"{e['id']}: clip exists", False, e.get('clip_file'))
            badp = [p for p in e.get('platforms', []) if p not in pk.PLATFORMS]
            if badp:
                problems += 1
                check(f"{e['id']}: platforms valid", False, str(badp))
        if e['status'] == 'approved':
            if not (e.get('title') and e.get('description')):
                problems += 1
                check(f"{e['id']}: approved has title/desc", False,
                      'empty title/description')
            st = e.get('scheduled_time')
            if st:
                try:
                    datetime.datetime.fromisoformat(st)
                except ValueError:
                    problems += 1
                    check(f"{e['id']}: scheduled_time parses", False, st)
            else:
                print(f"  [WARN] {e['id']}: approved but unscheduled "
                      '(facebook requires a slot; youtube/x do not)')
    if problems == 0:
        check('all entry fields consistent', True)

    section('live auth pings')
    if args.live_tiktok:
        if missing_tt:
            check('TikTok live auth', False, 'keys missing')
        else:
            try:
                import upload_tiktok as ut
                tokens, tok_path = ut.load_tokens(env)
                token = ut.refresh_if_needed(env, tokens, tok_path)
                if not token:
                    check('TikTok live auth', False,
                          'no token - run upload_tiktok.py --login')
                else:
                    who = ut.api_post(
                        token, f'{ut.BASE}/v2/post/publish/creator_info/query/',
                        {})
                    d = who.get('data') or {}
                    check('TikTok live auth', bool(d),
                          f"creator: {d.get('creator_username') or d}")
            except Exception as exc:
                check('TikTok live auth', False, str(exc)[:200])
    elif not missing_tt:
        print('  [SKIP] TikTok (use --live-tiktok)')
    if args.live_fb:
        if not (env.get('FB_PAGE_ID') and env.get('FB_PAGE_ACCESS_TOKEN')):
            check('FB live read', False, 'keys missing')
        else:
            try:
                import urllib.request
                ver = env.get('FB_GRAPH_VERSION') or 'v21.0'
                url = (f'https://graph.facebook.com/{ver}/'
                       f'{env["FB_PAGE_ID"]}?fields=name'
                       f'&access_token={env["FB_PAGE_ACCESS_TOKEN"]}')
                with urllib.request.urlopen(url, timeout=30) as r:
                    data = json.load(r)
                check('FB live read', 'name' in data,
                      data.get('name', json.dumps(data)[:200]))
            except Exception as exc:
                check('FB live read', False, str(exc)[:200])
    elif env.get('FB_PAGE_ID'):
        print('  [SKIP] Facebook (use --live-fb)')
    ig = bool(env.get('IG_USER_ID')) and bool(env.get('IG_ACCESS_TOKEN'))
    if args.live_ig:
        if not ig:
            check('IG live read', False, 'keys missing')
        else:
            try:
                import urllib.request
                ver = env.get('FB_GRAPH_VERSION') or 'v21.0'
                url = (f'https://graph.facebook.com/{ver}/'
                       f'{env["IG_USER_ID"]}?fields=username'
                       f'&access_token={env["IG_ACCESS_TOKEN"]}')
                with urllib.request.urlopen(url, timeout=30) as r:
                    data = json.load(r)
                check('IG live read', True, data.get('username', 'ok'))
            except Exception as exc:
                check('IG live read', False, str(exc)[:200])
    elif ig:
        print('  [SKIP] Instagram (use --live-ig)')
    print('  [INFO] YouTube: real auth is verified by the --login flow itself.')

    failed = [r for r in RESULTS if not r[1]]
    section('verdict')
    print(f"  {len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
