# Publishing setup — platform credentials (task 6)

Platforms: **YouTube, TikTok, Instagram, Facebook** (X removed — pay-per-use).
All values go into `.env` only. After each section, verify with:

    python scripts\publish_preflight.py --live-tiktok --live-ig --live-fb

---

## YouTube (free)

1. https://console.cloud.google.com → create/select a project
2. APIs & Services → Library → enable **YouTube Data API v3**
3. OAuth consent screen → External → add your own Google account as test user
4. Credentials → **Create credentials → OAuth client ID** → type **Desktop app**
5. Copy into `.env`:
   ```
   YOUTUBE_CLIENT_ID=....apps.googleusercontent.com
   YOUTUBE_CLIENT_SECRET=....
   ```
6. One-time consent:
   ```
   python scripts\upload_youtube.py --login
   ```

Scheduled entries upload as `private` with `publishAt` (YouTube schedules natively).

## TikTok (free)

1. https://developers.tiktok.com → register a developer account
   (**business email**; personal accounts are not eligible)
2. Create an app → add the **Content Posting API** product ("Video: Direct Post")
3. Copy the **Client Key** and **Client Secret** into `.env`:
   ```
   TIKTOK_CLIENT_KEY=...
   TIKTOK_CLIENT_SECRET=...
   TIKTOK_PRIVACY_LEVEL=SELF_ONLY
   ```
4. One-time consent:
   ```
   python scripts\upload_tiktok.py --login
   ```
5. Auth check without posting:
   ```
   python scripts\publish_preflight.py --live-tiktok
   ```

**Audit caveat:** until your app passes TikTok's Content Posting Audit
(submitted in the developer portal; typically 2–6 weeks), every API post is
forced to SELF_ONLY (private). Build/test privately now, submit the audit
early, then set `TIKTOK_PRIVACY_LEVEL=PUBLIC_TO_EVERYONE`. Access tokens last
24h — the uploader auto-refreshes via the stored refresh token.

## Instagram Reels (free)

Requirements: an Instagram **Business or Creator** account linked to a
Facebook Page you manage.

1. In the Instagram app: Settings → Account type → switch to Business/Creator,
   then link it to your Facebook Page.
2. https://developers.facebook.com → create app (**Business** type) →
   add `FB_APP_ID` / `FB_APP_SECRET` to `.env`
3. Generate a user token at the Graph API Explorer with scopes:
   `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`,
   `instagram_basic`, `instagram_content_publish`
4. Derive the Page token + find the IG account id:
   ```
   python scripts\fb_token_helper.py --exchange <explorer_token>
   python scripts\fb_token_helper.py --page-token     # paste FB_PAGE_* lines
   python scripts\upload_instagram.py --discover      # prints IG_USER_ID line
   ```
5. `.env` gets:
   ```
   IG_USER_ID=1784xxxxxxxxxxx
   IG_ACCESS_TOKEN=...
   ```
   (Page tokens from a long-lived exchange effectively never expire.)
6. Read-only check:
   ```
   python scripts\publish_preflight.py --live-ig
   ```

Upload uses the resumable flow (`rupload.facebook.com`) — no public hosting
needed. Your own IG account works while the app is in Development Mode;
formal App Review is only needed if other people's accounts will publish.
Reels guideline: ~90 s ideal; up to 3 min publishes but may rank differently.

## Facebook Page (free)

Covered in step 3–4 above (same Meta app + Page token). Entries with a
`scheduled_time` 10 min – 30 days ahead are uploaded unpublished with
`scheduled_publish_time`; Facebook publishes them natively at that moment.

---

## When keys land — verification sequence (one small real upload each)

1. `python scripts\upload_youtube.py --id <approved-entry>`
2. `python scripts\upload_tiktok.py --id <entry>` (SELF_ONLY is safe to test)
3. `python scripts\upload_instagram.py --id <entry>`
4. `python scripts\upload_facebook.py --id <entry>`

Each uploader refuses drafts, double-publishes, and wrong platforms; post ids
land in `state/publish_queue.json` only after confirmed API responses.
