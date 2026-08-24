#!/usr/bin/env python3
"""
Automated clip-plan generation with staging + explicit approval
(AGENT_BRIEF Task 7).

Generates a CANDIDATE clip plan from a transcript.json produced by
scripts/transcribe.py, using a free LLM API configured via .env.
Nothing is written to any project folder until you explicitly approve.

Flow:
    python scripts\\generate_clip_plan.py <transcript.json> \\
        [--series ad_daa] [--episode 0059] [--speaker "..."] [--max-clips N]
        -> writes state/clip_plan_staging_<timestamp>.json and shows it

    # edit the staging file by hand if you want, then either approve:
    python scripts\\generate_clip_plan.py --approve <staging-file> \\
        [--to Projects/ad_daa_0059]
    -> copies it to <project>/clip_plan.json (refuses overwrite w/o --force)

    # or discard it (just delete the file)

Hard boundaries (per brief):
    - never touches the renderer or any existing script
    - staged plans are NEVER auto-promoted; approval is an explicit command
    - provider/model come from .env, nothing hardcoded

.env keys (all optional; defaults shown):
    CLIPPLAN_BASE_URL=https://api.groq.com/openai/v1
    CLIPPLAN_MODEL=openai/gpt-oss-120b
    GROQ_API_KEY=...            (or OPENAI_API_KEY / CLIPPLAN_API_KEY)
"""

import argparse
import datetime
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGING_DIR = os.path.join(ROOT, 'state')

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
# env / provider config (provider-swappable, nothing hardcoded)
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


def provider_config(env, model_override=None):
    key = (env.get('CLIPPLAN_API_KEY') or env.get('GROQ_API_KEY')
           or env.get('OPENAI_API_KEY'))
    base = (env.get('CLIPPLAN_BASE_URL')
            or 'https://api.groq.com/openai/v1').rstrip('/')
    model = model_override or env.get('CLIPPLAN_MODEL') \
        or 'openai/gpt-oss-120b'
    if not key:
        die('No LLM key found. Put GROQ_API_KEY=... in .env '
            '(free at console.groq.com) or set CLIPPLAN_API_KEY.')
    return key, f'{base}/chat/completions', model


# --------------------------------------------------------------------------- #
# transcript loading
# --------------------------------------------------------------------------- #
def fmt_ts(seconds):
    m = int(seconds // 60)
    s = int(seconds % 60)
    h = int(m // 60)
    return f'{h}:{m % 60:02d}:{s:02d}' if h else f'{m}:{s:02d}'


def load_transcript(path):
    if not os.path.isfile(path):
        die(f'Transcript not found: {path}')
    with open(path, encoding='utf-8-sig') as fh:
        obj = json.load(fh)
    segs = obj.get('segments') if isinstance(obj, dict) else obj
    if not segs:
        die('Transcript has no segments.')
    cues = []
    for s in segs:
        text = str(s.get('text', '')).strip()
        if text:
            cues.append((float(s['start']), float(s['end']), text))
    cues.sort(key=lambda c: c[0])
    total_min = cues[-1][1] / 60.0
    log('INFO', f'{len(cues)} segments, {total_min:.1f} min of audio '
                f'({os.path.basename(path)})')
    return cues


def context_windows(cues, max_chars):
    """Split the transcript into excerpt windows small enough for free-tier
    token-per-minute limits. Timestamps stay absolute so selections from
    different windows are directly comparable."""
    windows = []
    cur, used, t0 = [], 0, None
    for start, end, text in cues:
        line = f'[{fmt_ts(start)}] {text}'
        if t0 is None:
            t0 = start
        if used + len(line) > max_chars and cur:
            windows.append((t0, cur[-1][1] if isinstance(cur[-1], tuple) else end,
                            '\n'.join(cur)))
            cur, used = [], 0
        cur.append(line)
        used += len(line) + 1
    if cur:
        windows.append((t0, cues[-1][1], '\n'.join(cur)))
    return [w[2] for w in windows]


def select_from_window(key, url, model, series, episode, speaker,
                       window_text, idx, total, max_picks):
    user_prompt = (
        f"Lecture context:\n"
        f"Series: {series or '(unknown)'}\n"
        f"Episode: {episode or '(unknown)'}\n"
        f"Speaker: {speaker or '(unknown)'}\n\n"
        f"This is excerpt {idx} of {total} of one continuous lecture "
        '(timestamps are absolute for the whole lecture).\n\n'
        f'{window_text}\n\n'
        f'Select up to {max_picks} of the BEST clip moments from THIS '
        'excerpt only (fewer or zero is fine if nothing qualifies). '
        'Return ONLY the JSON object.')
    return user_prompt


# --------------------------------------------------------------------------- #
# LLM call
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """You are selecting short video clips from an Islamic lecture transcript. Your job is to identify the best candidates for standalone short-form video posts on YouTube and Facebook.

Your primary goal is benefit and completeness, not virality.

Work through the transcript in this order:

Read the entire transcript section to understand the speaker's full meaning before selecting anything
Correct obvious Arabic transliteration errors in your understanding before evaluating
Identify candidate passages first, then evaluate each one
Expand or shrink timestamps so the clip contains the complete thought
Reject anything whose meaning changes when separated from surrounding discussion
Rank remaining candidates and select the strongest ones
CRITERION 1 - THE FIRST 3 SECONDS (evaluated first, before everything else)

Look at the literal first sentence of the candidate clip. It must create an immediate reason to keep watching.

Strong openings:

A direct statement of something important or surprising: "Most Muslims misunderstand this completely."
A command or warning that implies stakes: "Never say this in your prayer."
A Prophetic or Qur'anic reference that signals weight: "The Prophet \uFDFA warned us about exactly this."
A question the viewer immediately wants answered: "Do you know what breaks your fast without you realizing it?"
A consequence that creates concern: "This one habit destroys your good deeds."
Weak openings (move the start timestamp forward, or reject):

Transitional phrases: "And so as I was saying..."
Mid-argument continuations: "...and therefore what this means is..."
Throat-clearing openers: "So, the topic today is..."
Pronoun-dependent openings: "This is something that they did..."
If a clip has weak opening words but the right content starts a few seconds later, move the start timestamp to that stronger point, as long as the meaning remains intact.

CRITERION 2 - ISLAMIC BENEFIT

Prioritize clips that contain one or more of the following:

Practical Islamic guidance: tells the viewer how to behave, think, worship, interact, or deal with a situation
Meaningful Islamic principles: explains why something matters, not merely states a rule
Warnings with explanation: warns against harmful or sinful behavior AND explains the reason, consequence, or principle behind it
Encouragement with purpose: encourages good deeds, repentance, patience, sincerity - stronger when it explains why
Qur'an or Hadith-based lessons: especially valuable when the speaker explains the evidence rather than merely quoting it
Character and spiritual guidance: sincerity, humility, patience, gratitude, repentance, manners, brotherhood, reliance upon Allah
CRITERION 3 - COMPLETENESS AND CONTEXT INDEPENDENCE

A clip must be understandable without the surrounding five minutes. Reject or expand when:

A pronoun depends on something said earlier ("this," "that," "they" are unclear)
The speaker is answering a question whose original question is essential to understanding the answer
A ruling is clipped without its conditions or qualifications
The speaker changes direction halfway through the passage
A statement sounds extreme or controversial when isolated but was qualified immediately before or after it
A good clip follows this arc: Hook -> explanation -> evidence or reason -> conclusion

The viewer should feel "that was a complete lesson" not "what was he going to say next?"

CRITERION 4 - MUTE WATCHABILITY

Most Facebook video is watched on mute. A significant portion of YouTube too.

Ask: would this clip still be clear if someone read the transcript without hearing the audio?

If the lesson only lands because of the speaker's tone, emphasis, or delivery - and the words alone do not carry the full meaning - it is a weaker candidate, especially for Facebook.

Prefer clips where the words themselves deliver the complete lesson.

CRITERION 5 - COMMANDS AND WARNINGS

A command or warning is a bonus, not a requirement. The deciding factor is always whether it contains a complete beneficial lesson.

Weak: "Don't do this."
Strong: "Don't do this, because it leads to X, and Allah has instructed us to Y."

The second is substantially more valuable because it gives the viewer both the instruction and the understanding behind it.

SCORING - when ranking candidates against each other

High value: Islamic benefit, practical usefulness, completeness, context independence, explanation and reasoning, strong hook in first 3 seconds, Qur'an or Hadith with explanation, character and spiritual value, mute-watchable

Bonus: Command or warning with explanation, Qur'anic or Hadith evidence

Low priority: Controversy or shock value alone, emotional impact without substance

Strong negatives: Context dependence, misleading when isolated, fragmented thought, pronoun-dependent opening, weak first 3 seconds with no fix available

DECISION HIERARCHY

Apply in this order:

Does the first sentence create a genuine reason to keep watching?
Is there a genuinely beneficial Islamic lesson?
Is the meaning accurate and complete?
Can it stand alone without misleading the viewer?
Does it teach, guide, warn, encourage, or explain something meaningful?
Would it be clear to someone watching on mute?
Among the good candidates, which are the most valuable and engaging?
TARGET DURATION

Aim for clips under 2 minutes. A complete 1:45 lesson is better than an incomplete 45-second clip. Duration is secondary to completeness - never cut a clip short in a way that removes the explanation or conclusion.

OUTPUT FORMAT

Return valid JSON only, no commentary outside the JSON. Use this exact schema:

{
  "clips": [
    {
      "clip_id": "clip_01",
      "start_time": "00:03:12",
      "end_time": "00:05:04",
      "headline": "Short descriptive title of the lesson",
      "hook_sentence": "The exact first sentence of the clip as it will appear",
      "lesson_summary": "One sentence describing what the viewer will learn",
      "benefit_score": 8,
      "hook_strength": 9,
      "context_independent": true,
      "mute_watchable": true,
      "rejection_reason": null
    }
  ],
  "rejected_candidates": [
    {
      "start_time": "00:07:30",
      "end_time": "00:08:10",
      "rejection_reason": "Pronoun-dependent opening, meaning unclear without prior 3 minutes"
    }
  ]
}
Include rejected candidates so the human reviewer can see what was considered and why it was dropped."""


def call_llm(key, url, model, user_prompt):
    # Explicit completion budget: without it Groq assumes a large default
    # that counts against the free-tier per-request token cap.
    max_out = int(os.environ.get('CLIPPLAN_MAX_OUTPUT_TOKENS') or '3000')
    body = json.dumps({
        'model': model,
        'temperature': 0.4,
        'max_completion_tokens': max_out,
        'messages': [{'role': 'system', 'content': SYSTEM_PROMPT},
                     {'role': 'user', 'content': user_prompt}],
    }).encode()
    req = urllib.request.Request(url, data=body, method='POST')
    req.add_header('Authorization', f'Bearer {key}')
    req.add_header('Content-Type', 'application/json')
    req.add_header('User-Agent', 'DropsofKnowledge-Pipeline/1.0')
    attempt = 0
    while True:
        attempt += 1
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read().decode())
                return data['choices'][0]['message']['content']
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', 'replace')[:400]
            if exc.code == 400 or (exc.code not in (429,) and exc.code < 500):
                raise RuntimeError(f'HTTP {exc.code}: {detail}') from None
            # 429 / 5xx: free-tier token budgets need patient waits.
            if attempt >= 8:
                raise RuntimeError(f'HTTP {exc.code} after {attempt} '
                                   f'attempts: {detail}') from None
            m = re.search(r'try again in ([0-9.]+)\s*s', detail)
            wait = int(float(m.group(1))) + 3 if m else min(60, 2 ** attempt)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt >= 4:
                raise RuntimeError(f'network: {exc}') from None
            wait = min(30, 2 ** attempt)
        log('WARN', f'attempt {attempt} failed ({wait}s wait)...')
        time.sleep(wait)


# --------------------------------------------------------------------------- #
# schema validation - reject malformed output before it reaches staging
# --------------------------------------------------------------------------- #
def parse_seconds(v):
    if isinstance(v, (int, float)):
        return float(v)
    parts = str(v).strip().split(':')
    if not 1 <= len(parts) <= 3:
        raise ValueError(f'unparseable timestamp {v!r}')
    total = 0.0
    for p in parts:
        p = p.strip().replace(',', '.')
        if not re.fullmatch(r'[0-9]+(\.[0-9]+)?', p):
            raise ValueError(f'unparseable timestamp {v!r}')
        total = total * 60 + float(p)
    return total


def extract_json(raw):
    raw = raw.strip()
    # tolerate markdown code fences
    raw = re.sub(r'^```[a-zA-Z]*\s*', '', raw).rstrip('`').strip()
    obj = re.search(r'\{.*\}', raw, re.S)
    if obj:
        return json.loads(obj.group(0))
    arr = re.search(r'\[.*\]', raw, re.S)
    if arr:
        return {'clips': json.loads(arr.group(0))}
    raise ValueError('no JSON object found in model reply')


def validate_plan(plan, episode_default, series_default, speaker_default,
                  duration_floor=30.0, duration_hard_max=240.0,
                  max_clips=12):
    """Final-plan validator for the NORMALIZED internal representation
    (see normalize_clip). Raises ValueError with a human-readable reason.
    Assigns renderer-compatible sequential ids <episode>-NN."""
    if not isinstance(plan, dict):
        raise ValueError('top level is not a JSON object')
    clips = plan.get('clips')
    if not isinstance(clips, list) or not clips:
        raise ValueError('"clips" missing, empty, or not a list')
    if len(clips) > max_clips:
        raise ValueError(f'too many clips ({len(clips)} > {max_clips})')

    episode = str(plan.get('episode') or episode_default or '').strip() \
        or '0000'
    plan['episode'] = episode
    plan['series'] = str(plan.get('series') or series_default or '')
    plan['speaker'] = str(plan.get('speaker') or speaker_default or '')

    seen_ids = set()
    for i, c in enumerate(clips, 1):
        if not isinstance(c, dict):
            raise ValueError(f'clip #{i} is not an object')
        cid = f'{episode}-{i:02d}'
        if cid in seen_ids:
            raise ValueError(f'duplicate clip id {cid}')
        seen_ids.add(cid)
        for field in ('start', 'end'):
            if c.get(field) in (None, ''):
                raise ValueError(f'clip {cid}: missing "{field}"')
        try:
            s = parse_seconds(c['start'])
            e = parse_seconds(c['end'])
        except ValueError as exc:
            raise ValueError(f'clip {cid}: {exc}') from None
        if e <= s:
            raise ValueError(f'clip {cid}: end <= start ({c["start"]} -> '
                             f'{c["end"]})')
        dur = e - s
        # New prompt: completeness outranks duration; 30s floor guards
        # fragments, 240s hard-stops runaway selections.
        if dur < duration_floor:
            raise ValueError(f'clip {cid}: {dur:.0f}s is under the '
                             f'{duration_floor:.0f}s minimum')
        if dur > duration_hard_max:
            raise ValueError(f'clip {cid}: {dur:.0f}s exceeds the '
                             f'{duration_hard_max:.0f}s hard maximum')
        if not str(c.get('headline') or '').strip():
            raise ValueError(f'clip {cid}: empty headline')
        c['id'] = cid
    plan['clips'] = clips
    return plan


def normalize_clip(c, fallback_idx):
    """Map one model-produced clip object (new prompt schema) onto our
    canonical internal representation. Raises ValueError on bad data."""
    if not isinstance(c, dict):
        raise ValueError('clip entry is not an object')
    cid = str(c.get('clip_id') or f'clip_{fallback_idx:02d}')
    headline = str(c.get('headline') or '').strip()
    if not headline:
        raise ValueError(f'{cid}: empty headline')
    st, en = c.get('start_time'), c.get('end_time')
    if not st or not en:
        raise ValueError(f'{cid}: missing start_time / end_time')
    s, e = parse_seconds(st), parse_seconds(en)
    if e <= s:
        raise ValueError(f'{cid}: end <= start ({st} -> {en})')

    def score(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return round(min(10.0, max(0.0, v)), 1)

    return {
        'id': cid,
        'start': str(st).strip(), 'end': str(en).strip(),
        '_start_sec': round(s, 3), '_end_sec': round(e, 3),
        'headline': headline[:80],
        'hook_sentence': str(c.get('hook_sentence') or '').strip(),
        'lesson_summary': str(c.get('lesson_summary') or '').strip(),
        'benefit_score': score(c.get('benefit_score')),
        'hook_strength': score(c.get('hook_strength')),
        'context_independent': bool(c.get('context_independent', True)),
        'mute_watchable': bool(c.get('mute_watchable', True)),
    }


def normalize_rejections(raw_list):
    out = []
    for r in raw_list or []:
        if not isinstance(r, dict):
            continue
        st, en = r.get('start_time'), r.get('end_time')
        reason = str(r.get('rejection_reason') or '').strip()
        if not (st and en and reason):
            continue
        try:
            parse_seconds(st)
            parse_seconds(en)
        except ValueError:
            continue
        out.append({'start': str(st).strip(), 'end': str(en).strip(),
                    'reason': reason})
    return out


# --------------------------------------------------------------------------- #
# staging file
# --------------------------------------------------------------------------- #
def write_staging(plan, meta):
    os.makedirs(STAGING_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    path = os.path.join(STAGING_DIR, f'clip_plan_staging_{stamp}.json')
    doc = dict(plan)
    doc['_staging'] = meta
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def show_staged(path):
    with open(path, encoding='utf-8') as fh:
        doc = json.load(fh)
    meta = doc.get('_staging', {})
    print(f"\nStaged clip plan : {path}")
    print(f"Generated by     : {meta.get('model', '?')} "
          f"at {meta.get('generated_at', '?')}")
    print(f"Source transcript: {meta.get('source', '?')}")
    print(f"Series/Episode   : {doc.get('series')} / {doc.get('episode')}")
    print(f"Speaker          : {doc.get('speaker')}")
    print(f"Clips            : {len(doc.get('clips', []))}")
    print('-' * 78)
    for c in doc.get('clips', []):
        dur = parse_seconds(c['end']) - parse_seconds(c['start'])
        b, h = c.get('benefit_score'), c.get('hook_strength')
        scores = f"benefit={b if b is not None else '-'} " \
                 f"hook={h if h is not None else '-'}"
        flags = []
        if not c.get('context_independent', True):
            flags.append('context-dependent!')
        if not c.get('mute_watchable', True):
            flags.append('weak-on-mute')
        flagtxt = ('  [' + '; '.join(flags) + ']') if flags else ''
        print(f"{c['id']:10} {c['start']:>10} -> {c['end']:<10} "
              f"{dur / 60:4.1f} min  {scores}{flagtxt}")
        print(f"{'':12}{c['headline']}")
        if c.get('hook_sentence'):
            print(f"{'':12}hook: \"{c['hook_sentence']}\"")
        if c.get('lesson_summary'):
            print(f"{'':12}lesson: {c['lesson_summary']}")
    rejections = doc.get('rejected_candidates') or []
    if rejections:
        print('-' * 78)
        print(f"Rejected candidates ({len(rejections)}) - for your review:")
        for r in rejections:
            print(f"  {r['start']:>10} -> {r['end']:<10} {r['reason']}")
    print('-' * 78)
    print("Review/edit this file, then approve:")
    print(f"  python scripts\\generate_clip_plan.py --approve \"{path}\" "
          "[--to Projects/<folder>]")


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_generate(args):
    env = load_env()
    key, url, model = provider_config(env, args.model)
    cues = load_transcript(args.transcript)

    series = args.series or ''
    episode = args.episode or ''
    speaker = args.speaker or ''

    window_chars = int(env.get('CLIPPLAN_WINDOW_CHARS') or '7500')
    windows = context_windows(cues, window_chars)
    log('INFO', f'analysing in {len(windows)} excerpt window(s) '
                f'(free-tier TPM safe)')

    started = time.time()
    picks_per_window = max(1, (args.max_clips or 6) // max(1, len(windows)) + 1)
    merged_clips = []
    all_rejections = []
    last_error = None
    for idx, win_text in enumerate(windows, 1):
        if idx > 1:
            # free-tier TPM is a rolling per-minute budget; pace proactively
            time.sleep(int(env.get('CLIPPLAN_WINDOW_PACING') or '35'))
        log('STEP', f'window {idx}/{len(windows)}: asking {model}...')
        try:
            raw = call_llm(key, url, model,
                           select_from_window(key, url, model, series, episode,
                                              speaker, win_text, idx,
                                              len(windows), picks_per_window))
            plan = None
            for retry in range(3):
                try:
                    plan = extract_json(raw)
                    # validate the clips array of this window only (new
                    # schema: clip_id / start_time / end_time / headline)
                    if not isinstance(plan.get('clips'), list):
                        raise ValueError('"clips" is not a list')
                    normalized = [normalize_clip(c, i)
                                  for i, c in enumerate(plan.get('clips'), 1)]
                    break
                except (ValueError, json.JSONDecodeError) as exc:
                    last_error = str(exc)
                    log('WARN', f'rejected ({last_error}); '
                                'asking model to fix...')
                    raw = call_llm(
                        key, url, model,
                        select_from_window(key, url, model, series, episode,
                                           speaker, win_text, idx,
                                           len(windows), picks_per_window) +
                        f'\n\nYour previous reply was rejected: '
                        f'{last_error}\nReturn corrected STRICT JSON only.')
            else:
                raise RuntimeError(f'validation failed after retries: '
                                   f'{last_error}')
        except RuntimeError as exc:
            # one window failing (rate cap, malformed output, network) must
            # not lose the whole run - skip it and say so loudly
            log('WARN', f'window {idx}/{len(windows)} SKIPPED: {exc}')
            continue
        merged_clips.extend(normalized)
        try:
            all_rejections.extend(
                normalize_rejections(plan.get('rejected_candidates')))
        except Exception:
            pass
        if len(merged_clips) >= (args.max_clips or 6):
            log('INFO', 'enough candidates gathered; skipping later windows')
            break

    plan = {'series': series, 'episode': episode, 'speaker': speaker,
            'clips': merged_clips[:args.max_clips or 6]}
    if not plan['clips']:
        die('No clip candidates produced - every window failed. '
            'Try again later, lower CLIPPLAN_WINDOW_CHARS in .env '
            '(currently 7500), or use a different CLIPPLAN_MODEL.')
    # duration guard: drop outliers with a warning instead of losing
    # the whole batch (the validator remains as a backstop)
    kept = []
    for c in plan['clips']:
        dur = c['_end_sec'] - c['_start_sec']
        if dur < 30:
            log('WARN', f"dropped {c['id']}: {dur:.0f}s is too short "
                        'for a standalone clip')
        elif dur > 240:
            log('WARN', f"dropped {c['id']}: {dur:.0f}s exceeds the "
                        '4 minute hard maximum')
        else:
            kept.append(c)
    plan['clips'] = kept
    if not plan['clips']:
        die('All candidates were dropped by the duration guard. '
            'Re-run generation.')
    # windows can select overlapping moments; dedupe by time span,
    # order chronologically, then assign renderer-compatible ids
    seen_ts = set()
    unique = []
    for c in plan['clips']:
        key = (c['_start_sec'], c['_end_sec'])
        if key in seen_ts:
            continue
        seen_ts.add(key)
        unique.append(c)
    plan['clips'] = sorted(unique, key=lambda c: c['_start_sec'])
    ep = str(episode or '0000')
    for i, c in enumerate(plan['clips'], 1):
        c['id'] = f'{ep}-{i:02d}'
    plan = validate_plan(plan, episode, series, speaker,
                         max_clips=args.max_clips or 12)

    src_hash = hashlib_sha(args.transcript)
    staging_doc = dict(plan)
    staging_doc['rejected_candidates'] = all_rejections
    staging = write_staging(
        staging_doc,
        {'status': 'staged',
         'generated_at': datetime.datetime.now()
                                  .astimezone().isoformat(timespec='seconds'),
         'model': model, 'source': os.path.basename(args.transcript),
         'source_sha256': src_hash,
         'windows': len(windows),
         'llm_seconds': round(time.time() - started, 1)})
    log('OK', f'staged: {staging}')
    show_staged(staging)
    log('INFO', 'NOTHING has been approved yet - the plan lives only in '
                'state/.')


def hashlib_sha(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def cmd_approve(args):
    if not os.path.isfile(args.approve):
        die(f'Staging file not found: {args.approve}')
    with open(args.approve, encoding='utf-8') as fh:
        doc = json.load(fh)
    if '_staging' not in doc:
        die('Refusing: this does not look like a staging file '
            '(missing _staging block).')
    if doc['_staging'].get('status') == 'approved':
        die('This staging file was already approved.')

    plan = {k: v for k, v in doc.items()
            if k not in ('_staging', 'rejected_candidates')}
    # re-validate on promotion so hand edits cannot smuggle in bad timestamps
    try:
        plan = validate_plan(plan, plan.get('episode'),
                             plan.get('series'), plan.get('speaker'))
    except ValueError as exc:
        die(f'Cannot approve - edited plan failed validation: {exc}')

    dest_dir = args.to or os.path.join(
        ROOT, 'Projects',
        f"{re.sub(r'[^A-Za-z0-9_-]', '_', str(plan.get('series') or 'series').lower())}"
        f"_{plan.get('episode')}")
    dest_dir = os.path.abspath(dest_dir)
    dest = os.path.join(dest_dir, 'clip_plan.json')
    if os.path.exists(dest) and not args.force:
        die(f'{dest} already exists. Review it, or pass --force to replace.')
    os.makedirs(dest_dir, exist_ok=True)

    # The renderer contract is id/start/end/headline (+ optional
    # confidence/flag). Everything else the review schema carries is
    # analysis metadata and stays out of the promoted plan.
    RENDERER_FIELDS = ('id', 'start', 'end', 'headline', 'confidence', 'flag')
    renderer_clips = []
    for c in plan['clips']:
        clip = {k: c[k] for k in RENDERER_FIELDS if k in c and c[k] is not None}
        renderer_clips.append(clip)
    final_doc = {'series': plan.get('series'),
                 'episode': plan.get('episode'),
                 'speaker': plan.get('speaker'),
                 'clips': renderer_clips,
                 'version': doc.get('_staging', {}).get('model'),
                 'source': f"staged:{os.path.basename(args.approve)}"}
    tmp = dest + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(final_doc, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, dest)

    doc['_staging']['status'] = 'approved'
    doc['_staging']['approved_at'] = datetime.datetime.now() \
        .astimezone().isoformat(timespec='seconds')
    doc['_staging']['promoted_to'] = os.path.relpath(dest, ROOT) \
        .replace('\\', '/')
    with open(args.approve, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)

    log('OK', f'APPROVED -> {dest} ({len(plan["clips"])} clips)')
    log('INFO', 'Next: place audio+transcript next to it and run '
                'RUN_PROJECT.cmd (renderer untouched by this task).')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('transcript', nargs='?',
                    help='path to transcript.json (from transcribe.py)')
    ap.add_argument('--series')
    ap.add_argument('--episode')
    ap.add_argument('--speaker')
    ap.add_argument('--model', help='override CLIPPLAN_MODEL once')
    ap.add_argument('--max-clips', type=int, default=6)
    ap.add_argument('--show', metavar='FILE',
                    help='display a staging file without acting on it')
    ap.add_argument('--approve', metavar='FILE',
                    help='promote a staging file to the real clip_plan.json')
    ap.add_argument('--to', help='project folder for --approve '
                                 '(default: derived from series_episode)')
    ap.add_argument('--force', action='store_true',
                    help='with --approve: allow replacing existing plan')
    args = ap.parse_args()

    if args.show:
        show_staged(args.show)
        return 0
    if args.approve:
        cmd_approve(args)
        return 0
    if args.transcript:
        cmd_generate(args)
        return 0
    ap.print_help()
    return 64


if __name__ == '__main__':
    sys.exit(main())
