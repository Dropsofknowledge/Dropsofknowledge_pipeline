#!/usr/bin/env python3
"""
Facebook token helper (AGENT_BRIEF task 6 plumbing).

Tokens from the Graph API Explorer expire in about an hour, which makes
scheduled uploads unreliable. This tool performs the standard exchange:

  1. --exchange <short_user_token>
        -> long-lived USER token (~60 days), printed once
     Put it in .env temporarily as FB_USER_TOKEN, or pass it to step 2.

  2. --page-token [user_token]
        -> lists your Pages and prints a LONG-LIVED PAGE token for the one
           you pick. Page tokens derived from a long-lived user token do
           not expire in practice.
     Put that in .env as FB_PAGE_ACCESS_TOKEN.

Credentials come from .env only:
    FB_APP_ID=<app id>
    FB_APP_SECRET=<app secret>
    # step 2 reads FB_USER_TOKEN or takes the token on the command line
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import publish as pk


def die(msg):
    print(f'[ERROR] {msg}')
    sys.exit(2)


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        die(f'Graph API {e.code}: {e.read().decode("utf-8", "replace")[:400]}')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--exchange', metavar='SHORT_TOKEN',
                    help='exchange a short-lived user token for a '
                         'long-lived one')
    ap.add_argument('--page-token', nargs='?', const='FROM_ENV',
                    metavar='USER_TOKEN',
                    help='derive page tokens from a (long-lived) user token')
    args = ap.parse_args()
    env = pk.load_env()
    app_id = env.get('FB_APP_ID')
    app_secret = env.get('FB_APP_SECRET')
    if not app_id or not app_secret:
        die('FB_APP_ID / FB_APP_SECRET missing from .env.')
    ver = env.get('FB_GRAPH_VERSION') or 'v21.0'

    user_token = None
    if args.exchange:
        q = urllib.parse.urlencode({
            'grant_type': 'fb_exchange_token',
            'client_id': app_id,
            'client_secret': app_secret,
            'fb_exchange_token': args.exchange,
        })
        data = get(f'https://graph.facebook.com/{ver}/oauth/access_token?{q}')
        user_token = data.get('access_token')
        if not user_token:
            die(f'no token in response: {json.dumps(data)[:200]}')
        exp = data.get('expires_in', '?')
        print('[OK] Long-lived USER token (do not share):')
        print(user_token)
        print(f'(expires_in: {exp}s)')
        print('Now run:  python scripts\\fb_token_helper.py --page-token')

    if args.page_token is not None:
        tok = args.page_token if args.page_token != 'FROM_ENV' else None
        tok = tok or env.get('FB_USER_TOKEN') or user_token
        if not tok:
            die('No user token given (argument or FB_USER_TOKEN in .env).')
        pages = get(f'https://graph.facebook.com/{ver}/me/accounts'
                    f'?access_token={tok}').get('data') or []
        if not pages:
            die('This user token grants no Page access. Generate it with '
                'pages_show_list + pages_manage_posts scopes.')
        print('Pages found:')
        for i, p in enumerate(pages):
            print(f"  [{i}] id={p['id']}  {p.get('name')}  "
                  f"(perms: {','.join(sorted(p.get('tasks', []))[:3])}...)")
        pick = input('Which page number? ').strip()
        try:
            page = pages[int(pick)]
        except (ValueError, IndexError):
            die('Invalid selection.')
        print('\n[OK] Add these two lines to .env:')
        print(f'FB_PAGE_ID={page["id"]}')
        print(f'FB_PAGE_ACCESS_TOKEN={page["access_token"]}')


if __name__ == '__main__':
    main()
