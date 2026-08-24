#!/usr/bin/env python3
"""
Telegram audio auto-fetch for Dropsofknowledge.

Connects to the source group from .env, finds audio messages not yet
downloaded, and saves them with their original filename into

    Downloads/Telegram/<group>/

A state ledger (state/telegram_state.json) tracks processed message ids so
nothing is ever re-downloaded, and runs are retry-safe.

Usage:
    python scripts\\telegram_fetch.py            # catch up on new audio, exit
    python scripts\\telegram_fetch.py --watch    # keep polling every N seconds
    python scripts\\telegram_fetch.py --limit 50 --dry-run

Credentials come from .env only (never hardcoded, never printed):
    TELEGRAM_API_ID=123456
    TELEGRAM_API_HASH=abcd...
    TELEGRAM_SESSION=dok_fetcher        # created once by telegram_login.py
    TELEGRAM_SOURCE_GROUP=-1001234567890
"""

import argparse
import asyncio
import json
import os
import sys
import time
import traceback

from telethon import TelegramClient, errors
from telethon.tl.types import (DocumentAttributeAudio, DocumentAttributeFilename,
                               DocumentAttributeVideo)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(ROOT, 'state')
STATE_PATH = os.path.join(STATE_DIR, 'telegram_state.json')
DEFAULT_INBOX = os.path.join(ROOT, 'Downloads', 'Telegram')
MEDIA_EXTS = ['.mp3', '.amr', '.wav', '.m4a', '.m4b', '.aac', '.ogg', '.opus',
              '.wma', '.flac']
MAX_ATTEMPTS = 5

# Arabic chat names must survive the Windows console (default cp1252).
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
# config + state
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


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding='utf-8') as fh:
                return json.load(fh)
        except Exception as exc:
            log('WARN', f'state file unreadable ({exc}); starting fresh')
    return {'groups': {}}


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, STATE_PATH)


def group_bucket(state, group_key):
    return state['groups'].setdefault(group_key, {'processed': {},
                                                  'last_message_id': 0})


# --------------------------------------------------------------------------- #
# media detection - preserves the original Telegram filename/bytes
# --------------------------------------------------------------------------- #
def describe_audio(msg):
    """Return (original_name, kind) if a message carries downloadable audio."""
    doc = getattr(msg, 'document', None)
    if doc is None:
        return None
    name, is_audio, is_video = None, False, False
    for attr in doc.attributes or []:
        if isinstance(attr, DocumentAttributeAudio):
            is_audio = True
            if attr.title:
                base = attr.title
                ext = os.path.splitext(getattr(attr, 'file_name', '') or '')[1] \
                    or '.mp3'
                name = base + ext
        elif isinstance(attr, DocumentAttributeFilename):
            name = name or attr.file_name
        elif isinstance(attr, DocumentAttributeVideo):
            is_video = True
    if name:
        low = name.lower()
        if os.path.splitext(low)[1] in MEDIA_EXTS:
            is_audio = True
    if not is_audio or is_video:
        return None
    return name or f'audio_{msg.id}', ('voice' if getattr(
        doc.attributes[0], 'voice', False) else 'audio')


# --------------------------------------------------------------------------- #
# connection with retries / reconnects
# --------------------------------------------------------------------------- #
async def connect_with_retry(client):
    attempt = 0
    while True:
        attempt += 1
        try:
            await client.connect()
            if await client.is_user_authorized():
                return
            die('Session exists but is NOT authorized. Run '
                'scripts\\telegram_login.py once to (re)authenticate.')
        except errors.FloodWaitError as exc:
            wait = min(int(exc.seconds) + 1, 3600)
            log('WARN', f'Telegram flood control: waiting {wait}s...')
            await asyncio.sleep(wait)
        except (ConnectionError, OSError, asyncio.TimeoutError) as exc:
            if attempt >= MAX_ATTEMPTS:
                die(f'Cannot reach Telegram after {attempt} attempts: '
                    f'{type(exc).__name__}')
            wait = 2 ** attempt
            log('WARN', f'Connection attempt {attempt} failed; '
                        f'retrying in {wait}s...')
            await asyncio.sleep(wait)


# --------------------------------------------------------------------------- #
# fetch pass
# --------------------------------------------------------------------------- #
async def fetch_once(client, group, inbox, limit, dry_run, max_mb):
    me = await client.get_me()
    log('INFO', f'Connected as @{me.username or me.id} (id {me.id})')

    entity = None
    try:
        entity = await client.get_entity(group)
    except Exception as exc:
        die(f'Cannot resolve source group {group!r}: {type(exc).__name__}: {exc}')
    title = getattr(entity, 'title', None) or str(entity)
    group_key = str(getattr(entity, 'id', group))
    log('INFO', f'Source: "{title}" (key {group_key})')

    state = load_state()
    bucket = group_bucket(state, group_key)
    seen = bucket['processed']

    # Ask Telegram for messages newer than the watermark; on first run take
    # the most recent `limit` messages only.
    watermark = int(bucket.get('last_message_id') or 0)
    kwargs = dict(limit=limit) if watermark == 0 else dict(min_id=watermark,
                                                           limit=None)
    new_items, skipped_dupes, too_big, failures = 0, 0, 0, 0
    max_id_seen = watermark

    async for msg in client.iter_messages(entity, **kwargs):
        if msg.id <= watermark and watermark > 0:
            continue
        max_id_seen = max(max_id_seen, msg.id)
        info = describe_audio(msg)
        if not info:
            continue
        orig_name, kind = info
        if str(msg.id) in seen:
            skipped_dupes += 1
            continue
        size_mb = (getattr(msg.document, 'size', 0) or 0) / (1024 * 1024)
        if size_mb > max_mb:
            log('WARN', f'msg {msg.id}: {orig_name} is {size_mb:.0f} MB '
                        f'(>{max_mb} MB cap) - skipping')
            too_big += 1
            continue
        safe = ''.join(c if c not in '\\/:*?"<>|' else '_' for c in orig_name)
        out_dir = os.path.join(inbox, ''.join(
            c if c.isalnum() or c in '-_ .' else '_' for c in title) or 'group')
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f'{msg.id}_{safe}')

        if dry_run:
            log('DRY', f'would download msg {msg.id}: {orig_name} '
                       f'({size_mb:.1f} MB, {kind})')
            new_items += 1
            continue

        attempt = 0
        while True:
            attempt += 1
            try:
                log('STEP', f'Downloading msg {msg.id}: {orig_name} '
                            f'({size_mb:.1f} MB)...')
                tmp_path = out_path + '.part'
                await client.download_media(msg, file=tmp_path)
                if os.path.exists(tmp_path):
                    if os.path.exists(out_path):
                        os.remove(out_path)
                    os.replace(tmp_path, out_path)
                seen[str(msg.id)] = {
                    'file': os.path.relpath(out_path, ROOT).replace('\\', '/'),
                    'bytes': os.path.getsize(out_path),
                    'kind': kind,
                    'date': msg.date.isoformat() if msg.date else None,
                    'downloaded_at': time.strftime('%Y-%m-%dT%H:%M:%SZ',
                                                   time.gmtime())}
                save_state(state)
                log('OK', f'saved -> {os.path.relpath(out_path, ROOT)}')
                new_items += 1
                break
            except errors.FloodWaitError as exc:
                wait = min(int(exc.seconds) + 1, 3600)
                log('WARN', f'FloodWait {wait}s during download of msg {msg.id}')
                await asyncio.sleep(wait)
            except (ConnectionError, OSError, asyncio.TimeoutError) as exc:
                if attempt >= MAX_ATTEMPTS:
                    failures += 1
                    log('ERROR', f'msg {msg.id}: giving up after {attempt} '
                                 f'attempts: {type(exc).__name__}')
                    break
                wait = 2 ** attempt
                log('WARN', f'msg {msg.id} download failed '
                            f'({type(exc).__name__}); retrying in {wait}s')
                await asyncio.sleep(wait)
            except Exception as exc:
                failures += 1
                log('ERROR', f'msg {msg.id}: unexpected '
                             f'{type(exc).__name__}: {exc}')
                traceback.print_exc()
                break

    # Only advance the watermark when every audio message in the scanned
    # window is safely accounted for: never in dry-run mode, and never when
    # something failed (so the next run rescans and retries that window -
    # successful downloads dedupe through the ledger instead).
    if not dry_run and failures == 0 and max_id_seen > watermark:
        bucket['last_message_id'] = max_id_seen
        save_state(state)

    log('STEP', f'Done: {new_items} downloaded, {skipped_dupes} already had, '
                f'{too_big} over size cap, {failures} failed')
    return new_items


async def discover(client):
    """List the account's groups so TELEGRAM_SOURCE_GROUP can be filled in."""
    me = await client.get_me()
    log('INFO', f'Connected as @{me.username or me.id} - listing your chats:')
    print()
    count = 0
    async for dialog in client.iter_dialogs():
        if not dialog.is_group and not dialog.is_channel:
            continue
        kind = 'channel' if dialog.is_channel else 'group'
        username = getattr(dialog.entity, 'username', None)
        hint = f'@{username}' if username else '(private - use the numeric id)'
        print(f'  [{kind:7}] id={dialog.id:<15} {hint:32} {dialog.name}')
        count += 1
    print(f'\n{count} chats found. Put one in .env as:')
    print('  TELEGRAM_SOURCE_GROUP=@username      (public)')
    print('  TELEGRAM_SOURCE_GROUP=-100xxxxxxxxxx (private, use the id above)')


async def run(args):
    env = load_env()
    api_id = env.get('TELEGRAM_API_ID')
    api_hash = env.get('TELEGRAM_API_HASH')
    session = env.get('TELEGRAM_SESSION') or 'dok_fetcher'
    group = env.get('TELEGRAM_SOURCE_GROUP')
    if not api_id or not api_hash:
        die('TELEGRAM_API_ID / TELEGRAM_API_HASH missing from .env '
            '(see .env.example, https://my.telegram.org).')
    if not args.discover and not group:
        die('TELEGRAM_SOURCE_GROUP missing from .env (group id like '
            '-1001234567890, or @publicname). Run with --discover to list '
            'your groups and their ids.')
    session_path = session if os.path.isabs(session) \
        else os.path.join(ROOT, session)
    if not os.path.exists(session_path + '.session'):
        die(f'Session file {session_path}.session not found. Run '
            'scripts\\telegram_login.py once first.')

    client = TelegramClient(session_path, int(api_id), api_hash,
                            auto_reconnect=True, retry_delay=2)
    await connect_with_retry(client)
    try:
        if args.discover:
            await discover(client)
            return 0
        while True:
            try:
                n = await fetch_once(client, group, args.inbox or DEFAULT_INBOX,
                                     args.limit, args.dry_run, args.max_mb)
                if not args.watch:
                    return 0 if (n >= 0) else 1
            except (ConnectionError, OSError) as exc:
                log('WARN', f'Lost connection ({type(exc).__name__}); '
                            'reconnecting...')
                await connect_with_retry(client)
            if args.watch:
                await asyncio.sleep(args.interval)
    finally:
        await client.disconnect()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--discover', action='store_true',
                    help='list your groups/channels and their ids, then exit')
    ap.add_argument('--watch', action='store_true',
                    help='keep running and poll for new audio forever')
    ap.add_argument('--interval', type=int, default=300,
                    help='poll interval seconds in --watch mode (default 300)')
    ap.add_argument('--inbox', help='download folder override')
    ap.add_argument('--limit', type=int, default=200,
                    help='max recent messages scanned on FIRST run '
                         '(default 200)')
    ap.add_argument('--max-mb', type=float, default=500.0,
                    help='skip single files larger than this many MB')
    ap.add_argument('--dry-run', action='store_true',
                    help='list what would be downloaded without downloading')
    args = ap.parse_args()
    try:
        sys.exit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        log('INFO', 'Interrupted by user; progress already saved.')
        sys.exit(130)


if __name__ == '__main__':
    main()
