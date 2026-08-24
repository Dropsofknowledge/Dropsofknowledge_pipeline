#!/usr/bin/env python3
"""
One-time interactive Telegram login for DropsofKnowledge.

Creates the Telethon session file that telegram_fetch.py uses so that all
later fetch runs are fully non-interactive. Run this once on this laptop:

    python scripts\\telegram_login.py

Credentials come from .env only:
    TELEGRAM_API_ID / TELEGRAM_API_HASH  (https://my.telegram.org)
    TELEGRAM_SESSION                     (session file name, e.g. dok_fetcher)
"""

import asyncio
import os
import sys

from telethon import TelegramClient, errors

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env(root):
    path = os.path.join(root, '.env')
    env = {}
    if os.path.exists(path):
        with open(path, encoding='utf-8-sig') as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, _, v = line.partition('=')
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


async def main():
    env = dict(load_env(ROOT))
    env.update({k: v for k, v in os.environ.items() if v})
    api_id = env.get('TELEGRAM_API_ID')
    api_hash = env.get('TELEGRAM_API_HASH')
    session = env.get('TELEGRAM_SESSION') or 'dok_fetcher'
    if not api_id or not api_hash:
        print('ERROR: TELEGRAM_API_ID / TELEGRAM_API_HASH missing from .env '
              '(see .env.example). Get them at https://my.telegram.org.')
        return 2
    session_path = session if os.path.isabs(session) else os.path.join(ROOT, session)
    print(f'Session file will be created at: {session_path}.session')
    print('You will be asked for your phone number, then a login code that')
    print('Telegram sends you (inside the Telegram app), and possibly your')
    print('2FA password. This happens ONCE on this laptop.')
    try:
        async with TelegramClient(session_path, int(api_id), api_hash) as client:
            me = await client.get_me()
            name = ' '.join(x for x in [me.first_name, me.last_name] if x)
            print(f'Logged in as: {name} (@{me.username}) - session saved.')
            print('telegram_fetch.py can now run non-interactively.')
            return 0
    except errors.ApiIdInvalidError:
        print('ERROR: TELEGRAM_API_ID / TELEGRAM_API_HASH were rejected. '
              'Re-copy them from https://my.telegram.org -> API development '
              'tools (api_id is the NUMBER, api_hash the long hex string).')
        return 3
    except errors.PhoneNumberInvalidError:
        print('ERROR: That phone number looks invalid. Use international '
              'format, e.g. +9715xxxxxxxx.')
        return 3
    except errors.SessionPasswordNeededError:
        print('ERROR: 2FA password prompt failed. Re-run and enter the '
              'password you set in Telegram Settings -> Privacy & Security.')
        return 3
    except (ConnectionError, OSError) as exc:
        print(f'ERROR: Cannot reach Telegram: {exc}. Check your internet '
              'connection or VPN and run this again.')
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
