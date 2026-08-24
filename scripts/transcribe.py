#!/usr/bin/env python3
"""
DropsofKnowledge - scriptable transcription (replaces the Google Colab step).

Sends lecture audio to the hosted OpenAI Whisper API and writes transcript.srt +
transcript.json next to the audio, using the naming the renderer already expects.

Why hosted: this laptop has no CUDA GPU (Intel HD 520 only), so local
medium/large Whisper would be slower than realtime and inaccurate for Arabic.
The API call is fully scriptable - no notebook interaction.

Usage:
    python scripts/transcribe.py <media_file_or_folder> [options]

Options:
    --out DIR        output directory (default: alongside the input)
    --model NAME     Whisper model id (default: whisper-1 = large-v3 server-side)
    --language CODE  hint, e.g. ar (default: auto-detect)
    --prompt TEXT    vocabulary hint, e.g. Islamic terminology spellings
    --force          re-transcribe even if transcript.srt already exists
    --keep-wav       keep the compressed intermediate file instead of deleting it

Retry-safe:
    - skips work if transcript.srt already exists (unless --force)
    - long audio is uploaded in <25MB chunks cached in .transcribe_cache/;
      a failed run resumes from the last completed chunk
    - transient HTTP/network errors are retried with exponential backoff

Credentials come from .env (OPENAI_API_KEY=...) in the repo root only.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

AUDIO_EXT = ['.mp3', '.amr', '.wav', '.m4a', '.m4b', '.aac', '.ogg', '.opus',
             '.wma', '.flac', '.mp4', '.mov', '.webm', '.m4v', '.mkv']
DEFAULT_BASE_URL = 'https://api.groq.com/openai/v1'   # free whisper-large-v3
MAX_UPLOAD_BYTES = 24 * 1024 * 1024   # stay safely under the API's 25MB cap
TARGET_RATE = 16000                   # Whisper's native sample rate
TARGET_BITRATE = '48k'                # mono speech - ample for ASR
MAX_ATTEMPTS = 5
HTTP_TIMEOUT = 900


def log(level, msg):
    print(f"[{time.strftime('%H:%M:%S')}] [{level:<5}] {msg}", flush=True)


def die(msg, code=1):
    log('ERROR', msg)
    sys.exit(code)


# --------------------------------------------------------------------------- #
# .env loading - values are never printed, logged, or echoed
# --------------------------------------------------------------------------- #
def load_env(repo_root):
    path = os.path.join(repo_root, '.env')
    if not os.path.exists(path):
        return {}
    env = {}
    with open(path, encoding='utf-8-sig') as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def get_api_config(repo_root):
    """Resolve provider config. Groq (free whisper-large-v3) is the default;
    any OpenAI-compatible transcription endpoint works via env override."""
    env = dict(load_env(repo_root))
    env.update({k: v for k, v in os.environ.items() if v})
    key = (env.get('GROQ_API_KEY') or env.get('OPENAI_API_KEY')
           or env.get('TRANSCRIBE_API_KEY'))
    base_url = (env.get('TRANSCRIBE_BASE_URL') or DEFAULT_BASE_URL).rstrip('/')
    model = env.get('TRANSCRIBE_MODEL') or 'whisper-large-v3'
    if not key:
        die('No API key found. Put GROQ_API_KEY=... (free: console.groq.com) '
            'or OPENAI_API_KEY=... in .env at the repo root '
            '(see .env.example). Never paste it into chat.')
    return key, f'{base_url}/audio/transcriptions', model


# --------------------------------------------------------------------------- #
# tool discovery
# --------------------------------------------------------------------------- #
def find_tool(name):
    exe = shutil.which(name)
    if not exe:
        die(f"'{name}' not found on PATH. Install FFmpeg first.")
    return exe


def probe_duration(ffprobe, path):
    res = subprocess.run(
        [ffprobe, '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=noprint_wrappers=1:nokey=1', path],
        capture_output=True, text=True)
    try:
        return float(res.stdout.strip())
    except ValueError:
        return 0.0


# --------------------------------------------------------------------------- #
# audio preparation
# --------------------------------------------------------------------------- #
def prepare_audio(ffmpeg, src, workdir):
    """Re-encode anything to 16 kHz mono mp3 so every source type uploads small."""
    os.makedirs(workdir, exist_ok=True)
    out = os.path.join(workdir, 'upload_source.mp3')
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    log('STEP', f'Converting to 16 kHz mono mp3: {os.path.basename(src)}')
    res = subprocess.run(
        [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-i', src,
         '-vn', '-ac', '1', '-ar', str(TARGET_RATE), '-b:a', TARGET_BITRATE, out],
        capture_output=True, text=True)
    if res.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
        tail = (res.stderr or '').strip().splitlines()[-1:] or ['unknown error']
        die(f'ffmpeg could not convert {src}: {tail[0]}')
    return out


def split_chunks(ffmpeg, ffprobe, audio_path, workdir):
    """Return [(path, offset_seconds)] pieces, each under the upload cap."""
    size = os.path.getsize(audio_path)
    if size <= MAX_UPLOAD_BYTES:
        return [(audio_path, 0.0)]
    total = probe_duration(ffprobe, audio_path)
    if total <= 0:
        die(f'Cannot determine duration of {audio_path}; cannot chunk.')
    # Explicit cut points: exactly n_chunks pieces, with any rounding
    # remainder absorbed into the last one (never a sliver file).
    n_chunks = -(-size // MAX_UPLOAD_BYTES)          # ceil division
    seg_dur = (total / n_chunks) * 0.998
    log('INFO', f'{size / (1024 * 1024):.1f} MB > 25 MB cap -> splitting into '
                f'{n_chunks} chunks of ~{seg_dur / 60:.1f} min')
    chunks = []
    pattern = os.path.join(workdir, 'chunk_%03d.mp3')
    cut_points = ','.join(f'{i * seg_dur:.3f}' for i in range(1, n_chunks))
    subprocess.run(
        [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-i', audio_path,
         '-f', 'segment', '-segment_times', cut_points,
         '-c', 'copy', pattern],
        capture_output=True, text=True, check=True)
    # segment_time with -c copy can drift slightly; trust ffprobe per chunk
    offset = 0.0
    i = 0
    while True:
        p = pattern % i
        if not os.path.exists(p):
            break
        chunks.append((p, offset))
        offset += probe_duration(ffprobe, p)
        i += 1
    if not chunks:
        die('Chunking produced no output.')
    for p, _ in chunks:
        if os.path.getsize(p) > MAX_UPLOAD_BYTES:
            die(f'Chunk {os.path.basename(p)} still exceeds the upload cap; '
                'lower TARGET_BITRATE or split manually.')
    return chunks


# --------------------------------------------------------------------------- #
# API call with retries
# --------------------------------------------------------------------------- #
def multipart_body(fields, file_field, file_name, file_bytes, boundary):
    lines = []
    for name, value in fields.items():
        lines.append(f'--{boundary}'.encode())
        lines.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        lines.append(b'')
        lines.append(str(value).encode('utf-8'))
    lines.append(f'--{boundary}'.encode())
    lines.append(
        f'Content-Disposition: form-data; name="{file_field}"; '
        f'filename="{file_name}"'.encode())
    lines.append(b'Content-Type: application/octet-stream')
    lines.append(b'')
    lines.append(file_bytes)
    lines.append(f'--{boundary}--'.encode())
    return b'\r\n'.join(lines)


def transcribe_chunk(api_key, api_url, model, language, prompt, chunk_path):
    with open(chunk_path, 'rb') as fh:
        payload = fh.read()
    fields = {'model': model, 'response_format': 'verbose_json'}
    if language:
        fields['language'] = language
    if prompt:
        fields['prompt'] = prompt
    boundary = f'dok{int(time.time() * 1000)}'
    body = multipart_body(fields, 'file', os.path.basename(chunk_path),
                          payload, boundary)
    req = urllib.request.Request(api_url, data=body, method='POST')
    req.add_header('Authorization', f'Bearer {api_key}')
    req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
    # Cloudflare (Groq's edge) rejects the default Python urllib UA with 403/1010.
    req.add_header('User-Agent', 'DropsofKnowledge-Pipeline/1.0')

    attempt = 0
    while True:
        attempt += 1
        retry_after = None
        fail_code = 'network'
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', 'replace')[:500]
            fail_code = str(exc.code)
            raw_ra = exc.headers.get('retry-after') if exc.headers else None
            try:
                retry_after = int(raw_ra)
            except (TypeError, ValueError):
                retry_after = None
            if exc.code == 401:
                die(f'API rejected the key (401). Check the key in .env. [{detail}]')
            if exc.code == 400:
                die(f'API rejected the request (400): {detail}')
            retryable = exc.code >= 500 or exc.code == 429
            if not retryable or attempt >= MAX_ATTEMPTS:
                die(f'API error {exc.code} after {attempt} attempt(s): {detail}')
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt >= MAX_ATTEMPTS:
                die(f'Network error after {attempt} attempt(s)')
        wait = retry_after or min(60, 2 ** attempt)
        log('WARN', f'Attempt {attempt} failed ({fail_code}); '
                    f'retrying in {wait}s...')
        time.sleep(wait)


# --------------------------------------------------------------------------- #
# output writers
# --------------------------------------------------------------------------- #
def fmt_srt(seconds):
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f'{h:02d}:{m:02d}:{s:06.3f}'.replace('.', ',')


def write_outputs(segments, out_dir, meta):
    srt_path = os.path.join(out_dir, 'transcript.srt')
    json_path = os.path.join(out_dir, 'transcript.json')
    with open(srt_path, 'w', encoding='utf-8') as fh:
        for i, seg in enumerate(segments, 1):
            fh.write(f"{i}\n{fmt_srt(seg['start'])} --> {fmt_srt(seg['end'])}\n"
                     f"{seg['text'].strip()}\n\n")
    doc = dict(text=' '.join(s['text'].strip() for s in segments),
               segments=segments, **meta)
    with open(json_path, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
    return srt_path, json_path


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def find_media(target):
    if os.path.isfile(target):
        return target
    if not os.path.isdir(target):
        die(f'Input not found: {target}')
    for ext in AUDIO_EXT:
        cand = os.path.join(target, f'audio{ext}')
        if os.path.exists(cand):
            return cand
    for name in sorted(os.listdir(target)):
        if os.path.splitext(name)[1].lower() in AUDIO_EXT:
            return os.path.join(target, name)
    die(f'No audio file found in {target}')


def repo_root_of(script_dir):
    return os.path.dirname(os.path.dirname(os.path.abspath(script_dir)))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input', help='media file or folder containing audio.*')
    ap.add_argument('--out', help='output directory (default: alongside input)')
    ap.add_argument('--model', default=None,
                    help='override model (default: TRANSCRIBE_MODEL env or '
                         'whisper-large-v3)')
    ap.add_argument('--language', default=None,
                    help='ISO code hint, e.g. ar (default: auto)')
    ap.add_argument('--prompt', default=None,
                    help='vocabulary hint for domain terms')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--keep-wav', action='store_true',
                    help='keep intermediate mp3 instead of deleting it')
    args = ap.parse_args()

    root = repo_root_of(__file__)
    api_key, api_url, default_model = get_api_config(root)
    model = args.model or default_model

    src = find_media(args.input)
    out_dir = args.out or (os.path.dirname(os.path.abspath(src)))
    srt_target = os.path.join(out_dir, 'transcript.srt')

    if os.path.exists(srt_target) and not args.force:
        log('SKIP', f'{srt_target} already exists (use --force to redo).')
        return 0

    ffmpeg = find_tool('ffmpeg')
    ffprobe = find_tool('ffprobe')
    workdir = os.path.join(out_dir or os.path.dirname(src), '.transcribe_cache')
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(workdir, exist_ok=True)

    started = time.time()
    audio = prepare_audio(ffmpeg, src, workdir)
    chunks = split_chunks(ffmpeg, ffprobe, audio, workdir)

    all_segments = []
    detected_language = None
    for idx, (chunk_path, offset) in enumerate(chunks):
        cache_file = os.path.join(workdir, f'response_{idx:03d}.json')
        if os.path.exists(cache_file) and os.path.getsize(cache_file) > 2:
            log('RESUME', f'chunk {idx + 1}/{len(chunks)} cached - skipping upload')
            with open(cache_file, encoding='utf-8') as fh:
                result = json.load(fh)
        else:
            mb = os.path.getsize(chunk_path) / (1024 * 1024)
            log('STEP', f'Transcribing chunk {idx + 1}/{len(chunks)} ({mb:.1f} MB)...')
            result = transcribe_chunk(api_key, api_url, model, args.language,
                                      args.prompt, chunk_path)
            tmp = cache_file + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(result, fh, ensure_ascii=False)
            os.replace(tmp, cache_file)
        detected_language = detected_language or result.get('language')
        for seg in result.get('segments') or []:
            start = float(seg['start']) + offset
            end = float(seg['end']) + offset
            text = str(seg.get('text', '')).strip()
            if text:
                all_segments.append({'start': round(start, 3),
                                     'end': round(end, 3), 'text': text})

    if not all_segments:
        die('Transcription returned zero segments.')

    srt_path, json_path = write_outputs(
        all_segments, out_dir,
        meta={'source': os.path.basename(src), 'model': model,
              'language': detected_language,
              'chunks': len(chunks),
              'transcribed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})

    mins = len(all_segments) and max(s['end'] for s in all_segments) / 60.0
    log('OK', f'{len(all_segments)} cues written ({mins:.1f} min of audio) '
              f'in {time.time() - started:.0f}s')
    log('OK', f'SRT:  {srt_path}')
    log('OK', f'JSON: {json_path}')

    if not args.keep_wav:
        for p, _ in chunks:
            if p != audio:
                try:
                    os.remove(p)
                except OSError:
                    pass
        try:
            os.remove(audio)
        except OSError:
            pass
        try:
            os.rmdir(workdir) if not os.listdir(workdir) else None
        except OSError:
            pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
