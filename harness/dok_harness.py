#!/usr/bin/env python3
"""
Portable verification harness for the DropsofKnowledge Renderer.

This is NOT the shipped product (that is the PowerShell tree). It re-implements
the SAME orchestration/validation/state/caption/layout logic in Python so the
algorithms can be executed and proven on any platform with ffmpeg + ImageMagick.
It mirrors the .ps1 modules function-for-function so behaviour stays in lockstep.

Usage:
    python3 dok_harness.py render  <project_dir> <root_dir> [--force]
    python3 dok_harness.py import  <root_dir> <series> <episode> <source_folder>
    python3 dok_harness.py dashboard <root_dir> [project_dir]
"""
import sys, os, json, re, subprocess, shutil, tempfile, datetime, html
from PIL import Image, ImageDraw, ImageFont

ROOT_HINT = None  # set at runtime so font paths resolve relative to Dok root


FFMPEG = os.environ.get("DOK_FFMPEG", shutil.which("ffmpeg") or "ffmpeg")
FFPROBE = os.environ.get("DOK_FFPROBE", shutil.which("ffprobe") or "ffprobe")
MAGICK = os.environ.get("DOK_MAGICK", shutil.which("magick") or shutil.which("convert") or "convert")

AUDIO_EXT = ['.mp3','.amr','.wav','.m4a','.m4b','.aac','.ogg','.opus','.wma','.flac','.mp4','.mov','.webm','.m4v','.mkv']
TRANSCRIPT_EXT = ['.json','.srt','.vtt']
BG_EXT = ['.jpg','.jpeg','.png','.webp']


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def log(level, msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [{level:<5}] {msg}")

# ---------- validation.ps1 ----------
def to_seconds(v):
    if v is None: return None
    if isinstance(v, (int, float)): return float(v)
    s = str(v).strip()
    if re.fullmatch(r'[0-9]+(\.[0-9]+)?', s): return float(s)
    parts = s.split(':')
    if not (1 <= len(parts) <= 3): return None
    total = 0.0
    for p in parts:
        if not re.fullmatch(r'[0-9]+(\.[0-9]+)?', p): return None
        total = total * 60 + float(p)
    return total

def safe_name(name):
    out = re.sub(r'[\\/:*?"<>|\s]', '_', str(name)).strip('_.')
    return out or 'untitled'

def project_id(series, episode):
    return f"{safe_name(series)}_{safe_name(episode)}"

# ---------- production.ps1: path resolution ----------
def resolve_paths(project_root, root_dir, series):
    root = os.path.abspath(project_root)
    audio = None
    for e in AUDIO_EXT:
        p = os.path.join(root, f"audio{e}")
        if os.path.exists(p): audio = p; break
    transcript = None
    for e in TRANSCRIPT_EXT:
        p = os.path.join(root, f"transcript{e}")
        if os.path.exists(p): transcript = p; break
    project_background = None
    for e in BG_EXT:
        p = os.path.join(root, f"background{e}")
        if os.path.exists(p): project_background = p; break
    cp = os.path.join(root, "clip_plan.json")
    clip_plan = cp if os.path.exists(cp) else None
    tmpl_dir = None
    if series:
        c = os.path.join(root_dir, "templates", safe_name(series).lower())
        if os.path.exists(os.path.join(c, "layout.json")): tmpl_dir = c
    if not tmpl_dir:
        d = os.path.join(root_dir, "templates/default")
        if os.path.exists(os.path.join(d, "layout.json")): tmpl_dir = d
    template = os.path.join(tmpl_dir, "layout.json") if tmpl_dir else None

    # Prefer a project-specific background supplied in the source folder.
    # If the template opts out, its own background wins. Otherwise, the
    # project background wins over the template background, and the asset
    # fallback is used only if neither exists.
    background = project_background
    if not background and template:
        try:
            with open(template, encoding='utf-8') as fh:
                bg_name = json.load(fh).get('background')
            if bg_name:
                cand = os.path.join(tmpl_dir, bg_name)
                if os.path.exists(cand):
                    background = cand
        except Exception:
            pass
    if not background:
        d = os.path.join(root_dir, "assets/default_background.png")
        if os.path.exists(d):
            background = d
    out_dir = os.path.join(root, "output")
    os.makedirs(out_dir, exist_ok=True)
    writable = os.access(out_dir, os.W_OK)
    for sub in ("logs", "reports", "cache"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    return dict(Root=root, Audio=audio, Transcript=transcript, Background=background,
                ClipPlan=clip_plan, Template=template, TemplateDir=tmpl_dir,
                OutputDir=out_dir, LogsDir=os.path.join(root,"logs"),
                ReportsDir=os.path.join(root,"reports"), CacheDir=os.path.join(root,"cache"),
                OutputWritable=writable)

def validate(project_root, paths, plan, src_dur):
    issues = []
    def add(sev, msg, clip=None): issues.append(dict(severity=sev, message=msg, clip=clip))
    if not os.path.exists(project_root): add('error', f'Project root not found: {project_root}'); return issues
    if not paths['Audio']: add('error', 'No audio file found.')
    if not paths['Transcript']: add('warn', 'No transcript found (captions may be empty).')
    if not paths['Background']: add('warn', 'No background found; default background will be used.')
    if not paths['ClipPlan']: add('error', 'clip_plan.json missing.')
    if not paths['Template']: add('error', 'Template layout.json missing or unparseable.')
    if not paths['OutputWritable']: add('error', 'Output directory is not writable.')
    if plan and 'clips' in plan:
        clips = plan['clips'] or []
        if len(clips) == 0: add('warn', 'Clip plan contains zero clips.')
        for c in clips:
            cid = str(c.get('id'))
            s = to_seconds(c.get('start')); e = to_seconds(c.get('end'))
            if s is None: add('error', 'Invalid start timestamp.', cid); continue
            if e is None: add('error', 'Invalid end timestamp.', cid); continue
            if e <= s: add('error', 'End time is not after start time.', cid)
            if src_dur > 0 and e > src_dur + 0.5:
                add('error', f'Timestamp exceeds source duration ({round(src_dur,1)}s).', cid)
    return issues

def error_count(issues): return len([i for i in issues if i['severity']=='error'])

# ---------- state.ps1 ----------
def get_state(project_root, pid):
    p = os.path.join(project_root, "render_state.json")
    clips = {}
    last_run = None
    if os.path.exists(p):
        try:
            obj = json.load(open(p, encoding='utf-8'))
            last_run = obj.get('last_run')
            for c in obj.get('clips', []): clips[str(c['id'])] = c
        except Exception as ex:
            log('WARN', f'render_state.json unreadable, fresh start: {ex}')
    return dict(project=pid, last_run=last_run, Clips=clips)

def clip_status(state, cid): return state['Clips'].get(cid, {}).get('status', 'pending')

def set_clip_status(state, cid, status, output=None, reason=None):
    e = {'id': cid, 'status': status}
    if output: e['output'] = output
    if reason: e['reason'] = reason
    state['Clips'][cid] = e

def save_state(project_root, state):
    p = os.path.join(project_root, "render_state.json")
    clip_list = [state['Clips'][k] for k in sorted(state['Clips'].keys())]
    doc = {'project': state['project'], 'last_run': now_iso(), 'clips': clip_list}
    tmp = p + ".tmp"
    json.dump(doc, open(tmp, 'w', encoding='utf-8'), indent=2)
    os.replace(tmp, p)

# ---------- layout.ps1 (element-based, real-font rendering) ----------
_FONT_CACHE = {}

def _is_arabic_char(ch):
    o = ord(ch)
    return (0x0600 <= o <= 0x06FF) or (0x0750 <= o <= 0x077F) or (0x08A0 <= o <= 0x08FF) or (0xFB50 <= o <= 0xFDFF) or (0xFE70 <= o <= 0xFEFF)

def _font_for_char(layout, base_font_rel, size, ch):
    if _is_arabic_char(ch) and layout and layout.get('fonts', {}).get('arabic'):
        return _font(layout['fonts']['arabic'], size)
    return _font(base_font_rel, size)

def _font_path(rel):
    """Resolve a font path relative to the Dok root, falling back to a default."""
    if os.path.isabs(rel) and os.path.exists(rel):
        return rel
    cand = os.path.join(ROOT_HINT, rel) if ROOT_HINT else rel
    if os.path.exists(cand):
        return cand
    # fallback to any DejaVu
    for p in ('/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf',
              '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        if os.path.exists(p):
            return p
    return rel

def _font(rel, size):
    key = (rel, int(size))
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype(_font_path(rel), int(size))
    return _FONT_CACHE[key]

def _text_w(draw, text, font, letter_spacing=0, layout=None, base_font_rel=None, size=None):
    text = str(text)
    if layout is not None and base_font_rel is not None and size is not None:
        total = 0
        for ch in text:
            total += draw.textlength(ch, font=_font_for_char(layout, base_font_rel, size, ch))
        if letter_spacing and len(text) > 1:
            total += letter_spacing * (len(text) - 1)
        return total
    if letter_spacing and len(text) > 1:
        return sum(draw.textlength(c, font=font) for c in text) + letter_spacing * (len(text) - 1)
    return draw.textlength(text, font=font)

def _wrap(draw, text, font, max_w, letter_spacing=0, max_chars=None, layout=None, font_rel=None, size=None):
    """Greedy word-wrap to fit max_w and optional max_chars using real-ish glyph metrics."""
    words = [w for w in re.split(r'\s+', text or '') if w]
    lines, cur = [], ''
    for w in words:
        cand = w if not cur else f"{cur} {w}"
        fits_width = _text_w(draw, cand, font, letter_spacing, layout, font_rel, size) <= max_w
        fits_chars = (not max_chars) or (len(cand) <= max_chars)
        if (fits_width and fits_chars) or not cur:
            cur = cand
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    return lines or ['']

def _fit_block(draw, text, el, font_rel, letter_spacing=0):
    """Find the largest font (<= font_max) so wrapped text fits width & max_lines."""
    fmax = int(el.get('font_size', el.get('font_max', 60)))
    fmin = int(el.get('font_min', 30))
    max_lines = int(el.get('max_lines', 99))
    size = fmax
    while size >= fmin:
        f = _font(font_rel, size)
        max_chars = int(el.get('max_chars_per_line', 0)) or None
        lines = _wrap(draw, text, f, el['width'], letter_spacing, max_chars, getattr(draw, '_dok_layout', None), font_rel, size)
        if len(lines) <= max_lines and all(_text_w(draw, ln, f, letter_spacing, getattr(draw, '_dok_layout', None), font_rel, size) <= el['width'] for ln in lines):
            return f, size, lines
        size -= 2
    f = _font(font_rel, fmin)
    max_chars = int(el.get('max_chars_per_line', 0)) or None
    return f, fmin, _wrap(draw, text, f, el['width'], letter_spacing, max_chars, getattr(draw, '_dok_layout', None), font_rel, fmin)

def _draw_text_line(draw, x, y, text, font, fill, letter_spacing=0, outline=0, outline_color='#000000', layout=None, font_rel=None, size=None):
    text = str(text)
    mixed = layout is not None and font_rel is not None and size is not None and any(_is_arabic_char(ch) for ch in text)
    if mixed or (letter_spacing and len(text) > 1):
        cx = x
        for ch in text:
            f = _font_for_char(layout, font_rel, size, ch) if mixed else font
            if outline:
                for dx in (-outline, 0, outline):
                    for dy in (-outline, 0, outline):
                        if dx or dy:
                            draw.text((cx+dx, y+dy), ch, font=f, fill=outline_color)
            draw.text((cx, y), ch, font=f, fill=fill)
            cx += draw.textlength(ch, font=f) + (letter_spacing if len(text) > 1 else 0)
    else:
        if outline:
            for dx in (-outline, 0, outline):
                for dy in (-outline, 0, outline):
                    if dx or dy:
                        draw.text((x+dx, y+dy), text, font=font, fill=outline_color)
        draw.text((x, y), text, font=font, fill=fill)

def _render_block(draw, el, text, font_rel):
    """Render a multi-line, auto-fit, aligned text block within element box."""
    if not text or not str(text).strip():
        return
    text = str(text)
    if el.get('uppercase'):
        text = text.upper()
    ls = int(el.get('letter_spacing', 0))
    font, size, lines = _fit_block(draw, text, el, font_rel, ls)
    line_h = size * float(el.get('line_spacing', 1.15))
    block_h = line_h * len(lines)
    valign = el.get('valign', 'top')
    if valign == 'middle':
        y = el['y'] + (el['height'] - block_h) / 2
    elif valign == 'bottom':
        y = el['y'] + el['height'] - block_h
    else:
        y = el['y']
    align = el.get('align', 'center')
    for ln in lines:
        lw = _text_w(draw, ln, font, ls, getattr(draw, '_dok_layout', None), font_rel, size)
        if align == 'center':
            x = el['x'] + (el['width'] - lw) / 2
        elif align == 'right':
            x = el['x'] + el['width'] - lw
        else:
            x = el['x']
        _draw_text_line(draw, x, y, ln, font, el.get('color', '#FFFFFF'), ls,
                        int(el.get('outline', 0)), el.get('outline_color', '#000000'),
                        getattr(draw, '_dok_layout', None), font_rel, size)
        y += line_h

def render_overlay_png(layout, out_png, headline='', speaker='', clip_id=''):
    """Render the static overlay (ID + headline + sheikh name) to a transparent PNG."""
    w = int(layout['canvas']['width']); h = int(layout['canvas']['height'])
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw._dok_layout = layout
    els = layout['elements']
    # ID number (single line, beside baked banner)
    idel = els.get('id', {})
    if idel.get('enabled') and clip_id:
        idtext = (idel.get('prefix', '') + str(clip_id))
        font = _font(layout['fonts']['id'], idel['font_size'])
        ls = int(idel.get('letter_spacing', 0))
        align = idel.get('align', 'left')
        tw = _text_w(draw, idtext, font, ls)
        if align == 'left': x = idel['x']
        elif align == 'right': x = idel['x'] + idel['width'] - tw
        else: x = idel['x'] + (idel['width'] - tw) / 2
        asc, desc = font.getmetrics()
        y = idel['y'] + (idel['height'] - (asc + desc)) / 2
        _draw_text_line(draw, x, y, idtext, font, idel.get('color', '#E8D7A0'), ls)
    # Headline
    if els.get('title', {}).get('enabled', True):
        _render_block(draw, els['title'], headline, layout['fonts']['title'])
    # Sheikh name
    if els.get('speaker', {}).get('enabled', True):
        _render_block(draw, els['speaker'], speaker, layout['fonts']['speaker'])
    img.save(out_png)
    return os.path.exists(out_png)

# ---------- captions.ps1 ----------
def srt_time(t):
    t = t.strip().replace(',', '.'); parts = t.split(':')
    if len(parts) != 3: return None
    return float(parts[0])*3600 + float(parts[1])*60 + float(parts[2])

def read_transcript(path):
    cues = []
    if not path or not os.path.exists(path): return cues
    ext = os.path.splitext(path)[1].lower()
    raw = open(path, encoding='utf-8').read()
    if ext == '.json':
        try:
            obj = json.loads(raw)
            segs = obj['segments'] if isinstance(obj, dict) and 'segments' in obj else obj
            for s in segs:
                cues.append(dict(start=float(s['start']), end=float(s['end']), text=str(s['text'])))
        except Exception: pass
        return cues
    text = raw.replace('\r', '')
    for block in re.split(r'\n\n+', text):
        lines = [l for l in block.split('\n') if l]
        timing = next((l for l in lines if '-->' in l), None)
        if not timing: continue
        m = re.search(r'([0-9:.,]+)\s*-->\s*([0-9:.,]+)', timing)
        if not m: continue
        st, en = srt_time(m.group(1)), srt_time(m.group(2))
        idx = lines.index(timing)
        txt = ' '.join(lines[idx+1:]).strip()
        if st is not None and en is not None and txt:
            cues.append(dict(start=st, end=en, text=txt))
    return cues

def clip_cues(all_cues, cs, ce, max_words=7):
    out = []
    for c in all_cues:
        if c['end'] <= cs or c['start'] >= ce: continue
        s = max(c['start'], cs) - cs; e = min(c['end'], ce) - cs
        if e - s <= 0: continue
        words = [w for w in re.split(r'\s+', c['text']) if w]
        if not words: continue
        chunks, cur = [], []
        for w in words:
            cur.append(w)
            ends_punct = bool(re.search(r'[.!?,;:]$', w))
            if len(cur) >= max_words or (ends_punct and len(cur) >= max_words//2):
                chunks.append(' '.join(cur)); cur = []
        if cur: chunks.append(' '.join(cur))
        per = (e - s) / max(1, len(chunks))
        for i, ch in enumerate(chunks):
            out.append(dict(start=s+i*per, end=s+(i+1)*per, text=ch))
    return out

def ass_time(sec):
    if sec < 0: sec = 0
    h = int(sec//3600); m = int((sec%3600)//60); s = sec%60
    return f"{h}:{m:02d}:{s:05.2f}"

def _ass_color(hexstr):
    """#RRGGBB -> ASS &HBBGGRR& (opaque)."""
    s = hexstr.lstrip('#')
    r, g, b = s[0:2], s[2:4], s[4:6]
    return f"&H00{b}{g}{r}".upper()

def _ass_font_name(layout):
    """ASS needs the font *family name*, not a path. Map known files -> names."""
    rel = layout['fonts'].get('subtitle', '')
    base = os.path.basename(rel).lower()
    if 'ptserif' in base or 'pt_serif' in base: return 'PT Serif'
    if 'montserrat' in base: return 'Montserrat'
    if 'oswald' in base: return 'Oswald'
    return 'DejaVu Serif'

def write_ass(cues, layout, out_path):
    """Captions are DARK text rendered INSIDE the baked white box (centered there),
    not bottom-anchored white subtitles."""
    w = int(layout['canvas']['width']); h = int(layout['canvas']['height'])
    box = layout['elements']['caption_box']
    font = _ass_font_name(layout)
    size = int(box['font_size'])
    color = _ass_color(box.get('color', '#1A1A1A'))
    # Position centered inside the box using \pos (alignment 5 = middle-center).
    cx = int(box['x'] + box['width'] / 2)
    cy = int(box['y'] + box['height'] / 2)
    wrap_w = int(box['width'])
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{size},{color},{color},&H00FFFFFF,1,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for c in cues:
        txt = str(c['text']).replace('\n', ' ').replace('{', '(').replace('}', ')')
        # \q2 = no auto-wrap then we hard-wrap; simpler: rely on box width via \pos + manual breaks
        wrapped = _ass_wrap(txt, wrap_w, size, int(box.get('max_lines', 2)))
        body = "\\N".join(wrapped)
        lines.append(f"Dialogue: 0,{ass_time(c['start'])},{ass_time(c['end'])},Caption,,0,0,0,,{{\\pos({cx},{cy})}}{body}")
    open(out_path, 'w', encoding='utf-8').write("\n".join(lines) + "\n")

def _ass_wrap(text, max_w, font_px, max_lines):
    """Rough char-based wrap so captions stay within the white box width."""
    approx_char_w = font_px * 0.5
    max_chars = max(8, int(max_w / approx_char_w))
    words = text.split()
    lines, cur = [], ''
    for w in words:
        cand = w if not cur else f"{cur} {w}"
        if len(cand) <= max_chars or not cur:
            cur = cand
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    return lines[:max_lines] if max_lines else lines

# ---------- audio.ps1 ----------
def audio_duration(path):
    try:
        out = subprocess.run([FFPROBE, '-v', 'error', '-show_entries', 'format=duration',
                              '-of', 'default=noprint_wrappers=1:nokey=1', path],
                             capture_output=True, text=True).stdout.strip()
        return float(out) if out else 0.0
    except Exception:
        return 0.0

def working_audio(src, cache_dir):
    ext = os.path.splitext(src)[1].lower()
    if ext in ('.m4a', '.aac', '.mp3'): return src
    os.makedirs(cache_dir, exist_ok=True)
    work = os.path.join(cache_dir, 'audio_work.m4a')
    if not os.path.exists(work):
        subprocess.run([FFMPEG, '-y', '-i', src, '-vn', '-c:a', 'aac', '-b:a', '192k', work],
                       capture_output=True)
    return work if os.path.exists(work) else src

# ---------- ffmpeg.ps1 ----------
def svg_to_png(svg, out_png, w, h):
    tmp = out_png + '.svg'
    open(tmp, 'w', encoding='utf-8').write(svg)
    subprocess.run([MAGICK, '-background', 'none', '-size', f'{w}x{h}', tmp, out_png], capture_output=True)
    if os.path.exists(tmp): os.remove(tmp)
    return os.path.exists(out_png)

def base_frame(bg, overlay, out_png, w, h, color='#0B0B0F'):
    if bg and os.path.exists(bg):
        cmd = [MAGICK, bg, '-resize', f'{w}x{h}^', '-gravity', 'center', '-extent', f'{w}x{h}',
               overlay, '-gravity', 'center', '-composite', out_png]
    else:
        cmd = [MAGICK, '-size', f'{w}x{h}', f'xc:{color}', overlay, '-gravity', 'center', '-composite', out_png]
    subprocess.run(cmd, capture_output=True)
    return os.path.exists(out_png)

def render_clip(base, audio, start, dur, ass, out_mp4, fps=30, fontsdir=None):
    # H.264 needs even width/height; pad up to the next even size if the
    # template canvas is odd (e.g. 1587x2245). Captions burn before the pad.
    even = "pad=ceil(iw/2)*2:ceil(ih/2)*2"
    if ass and os.path.exists(ass):
        esc = ass.replace('\\', '/').replace(':', '\\:')
        if fontsdir:
            fd = fontsdir.replace('\\', '/').replace(':', '\\:')
            vf = f"ass='{esc}':fontsdir='{fd}',{even},format=yuv420p"
        else:
            vf = f"ass='{esc}',{even},format=yuv420p"
    else:
        vf = f"{even},format=yuv420p"
    args = [FFMPEG, '-y', '-loop', '1', '-framerate', str(fps), '-i', base,
            '-ss', f'{start:.3f}', '-t', f'{dur:.3f}', '-i', audio,
            '-vf', vf, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-preset', 'veryfast',
            '-crf', '20', '-c:a', 'aac', '-b:a', '192k', '-r', str(fps),
            '-t', f'{dur:.3f}', '-shortest', '-movflags', '+faststart', out_mp4]
    res = subprocess.run(args, capture_output=True)
    ok = (res.returncode == 0) and os.path.exists(out_mp4) and os.path.getsize(out_mp4) > 1024
    if not ok and os.path.exists(out_mp4):
        try: os.remove(out_mp4)
        except Exception: pass
    return ok

def export_preview(mp4, out_jpg):
    subprocess.run([FFMPEG, '-y', '-ss', '0.5', '-i', mp4, '-frames:v', '1', '-q:v', '3', out_jpg],
                   capture_output=True)
    return os.path.exists(out_jpg)

def qa_clip(mp4, exp_dur, w, h):
    checks = []
    ok = os.path.exists(mp4) and os.path.getsize(mp4) > 1024
    checks.append(dict(check='file_exists', pass_=ok))
    if ok:
        try:
            info = subprocess.run([FFPROBE, '-v', 'error', '-select_streams', 'v:0',
                                   '-show_entries', 'stream=width,height', '-of', 'csv=p=0', mp4],
                                  capture_output=True, text=True).stdout.strip().split(',')
            vw, vh = int(info[0]), int(info[1])
            ew, eh = w + (w % 2), h + (h % 2)  # even-padded target
            checks.append(dict(check='resolution', pass_=(vw==ew and vh==eh), detail=f'{vw}x{vh}'))
            aud = subprocess.run([FFPROBE, '-v', 'error', '-select_streams', 'a:0',
                                  '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', mp4],
                                 capture_output=True, text=True).stdout.strip()
            checks.append(dict(check='has_audio', pass_=bool(aud)))
        except Exception: pass
    score = round(len([c for c in checks if c['pass_']]) / len(checks) * 100) if checks else 0
    clean = []
    for c in checks:
        c = dict(c); c['pass'] = c.pop('pass_'); clean.append(c)
    return dict(score=score, checks=clean)

# ---------- render_project.ps1 (the loop) ----------
def render_project(project_root, root_dir, force=False):
    global ROOT_HINT
    project_root = os.path.abspath(project_root); root_dir = os.path.abspath(root_dir)
    ROOT_HINT = root_dir
    log('STEP', 'DropsofKnowledge Renderer starting')
    log('INFO', f'Project: {project_root}')
    plan_path = os.path.join(project_root, 'clip_plan.json')
    if not os.path.exists(plan_path): log('ERROR', 'clip_plan.json not found.'); return 2
    plan = json.load(open(plan_path, encoding='utf-8'))
    series, episode, speaker = plan.get('series',''), plan.get('episode',''), plan.get('speaker','')
    pid = project_id(series, episode)
    paths = resolve_paths(project_root, root_dir, series)
    src_dur = audio_duration(paths['Audio']) if paths['Audio'] else 0
    issues = validate(project_root, paths, plan, src_dur)
    for i in issues:
        lvl = {'error':'ERROR','warn':'WARN'}.get(i['severity'],'INFO')
        log(lvl, (f"[clip {i['clip']}] " if i['clip'] else '') + i['message'])
    proj_errors = [i for i in issues if i['severity']=='error' and not i['clip']]
    if proj_errors: log('ERROR', 'Validation failed; aborting.'); return 4
    layout = json.load(open(paths['Template'], encoding='utf-8'))
    cw, ch = int(layout['canvas']['width']), int(layout['canvas']['height'])
    work_audio = working_audio(paths['Audio'], paths['CacheDir'])
    all_cues = read_transcript(paths['Transcript'])
    state = get_state(project_root, pid)
    bad = {i['clip']: i['message'] for i in issues if i['severity']=='error' and i['clip']}
    clips = plan['clips']; total = len(clips); done=failed=skipped=0
    log('INFO', f'Planned clips: {total}')
    for clip in clips:
        cid = str(clip['id'])
        clip_out = os.path.join(paths['OutputDir'], cid)
        mp4 = os.path.join(clip_out, 'clip.mp4')
        if clip_status(state, cid)=='completed' and not force and os.path.exists(mp4):
            log('INFO', f'Clip {cid} already completed - skipping.'); skipped+=1; continue
        if cid in bad:
            log('ERROR', f'Clip {cid} invalid: {bad[cid]}')
            set_clip_status(state, cid, 'failed', reason=bad[cid]); save_state(project_root, state)
            failed+=1; continue
        try:
            log('STEP', f"Rendering clip {cid} - {clip.get('headline','')}")
            set_clip_status(state, cid, 'rendering')
            os.makedirs(clip_out, exist_ok=True)
            start = to_seconds(clip['start']); end = to_seconds(clip['end']); dur = end-start
            overlay = os.path.join(clip_out, 'overlay.png'); bframe = os.path.join(clip_out, 'base.png')
            if not render_overlay_png(layout, overlay, clip.get('headline',''), speaker, cid):
                raise RuntimeError('Failed to render overlay.')
            if not base_frame(paths['Background'], overlay, bframe, cw, ch): raise RuntimeError('Failed to build base frame.')
            ass = os.path.join(clip_out, 'captions.ass')
            cues = clip_cues(all_cues, start, end, int(layout['elements']['caption_box'].get('max_words', 7)))
            write_ass(cues, layout, ass)
            fontsdir = os.path.join(root_dir, 'fonts')
            if not render_clip(bframe, work_audio, start, dur, ass, mp4, int(layout['fps']), fontsdir):
                raise RuntimeError('FFmpeg produced no/empty output.')
            export_preview(mp4, os.path.join(clip_out, 'preview.jpg'))
            qa = qa_clip(mp4, dur, cw, ch)
            rel = f'output/{cid}/clip.mp4'
            manifest = dict(id=cid, series=series, episode=episode, speaker=speaker, headline=clip.get('headline'),
                            inputs=dict(audio=os.path.basename(paths['Audio']),
                                        background=os.path.basename(paths['Background']) if paths['Background'] else 'default',
                                        transcript=os.path.basename(paths['Transcript']) if paths['Transcript'] else None,
                                        template=paths['Template']),
                            window=dict(start=clip['start'], end=clip['end'], duration_sec=round(dur,3)),
                            render=dict(width=cw, height=ch, fps=int(layout['fps']), codec='h264/aac'),
                            output=rel, caption_cues=len(cues))
            json.dump(manifest, open(os.path.join(clip_out,'manifest.json'),'w'), indent=2)
            report = dict(id=cid, result='success', qa_score=qa['score'], checks=qa['checks'], rendered_at=now_iso())
            json.dump(report, open(os.path.join(clip_out,'report.json'),'w'), indent=2)
            json.dump(report, open(os.path.join(paths['ReportsDir'], f'{cid}.json'),'w'), indent=2)
            set_clip_status(state, cid, 'completed', output=rel); save_state(project_root, state)
            log('OK', f"Clip {cid} done (QA {qa['score']}).")
            done+=1
        except Exception as ex:
            log('ERROR', f'Clip {cid} FAILED: {ex}')
            set_clip_status(state, cid, 'failed', reason=str(ex)); save_state(project_root, state)
            failed+=1
    save_state(project_root, state)
    summary = dict(project=pid, total=total, completed=done, failed=failed, skipped=skipped, finished_at=now_iso())
    json.dump(summary, open(os.path.join(paths['ReportsDir'],'_summary.json'),'w'), indent=2)
    log('STEP', f'Summary: total={total} completed={done} failed={failed} skipped={skipped}')
    return 1 if failed else 0

# ---------- import + dashboard ----------
def import_lecture(root_dir, series, episode, source_folder):
    pid = project_id(series, episode)
    folder = f"{safe_name(series)}_{safe_name(episode)}"
    pdir = os.path.join(root_dir, 'Projects', folder)
    for d in (pdir, *(os.path.join(pdir, s) for s in ('output','logs','reports','cache'))):
        os.makedirs(d, exist_ok=True)
    accepted = {f'audio{e}' for e in AUDIO_EXT} | {f'transcript{e}' for e in TRANSCRIPT_EXT} | {f'background{e}' for e in BG_EXT} | {'clip_plan.json'}
    copied = []
    for f in os.listdir(source_folder):
        fp = os.path.join(source_folder, f)
        if not os.path.isfile(fp): continue
        low = f.lower(); ext = os.path.splitext(f)[1].lower(); target = None
        if low in accepted: target = low
        elif f'audio{ext}' in accepted: target = f'audio{ext}'
        elif f'transcript{ext}' in accepted: target = f'transcript{ext}'
        elif f'background{ext}' in accepted: target = f'background{ext}'
        if target:
            dest = os.path.join(pdir, target)
            if not os.path.exists(dest): shutil.copy2(fp, dest); copied.append(target)
    pp = os.path.join(pdir, 'clip_plan.json')
    if not os.path.exists(pp):
        json.dump(dict(series=safe_name(series).lower(), episode=safe_name(episode), speaker='', clips=[]), open(pp,'w'), indent=2)
    sp = os.path.join(pdir, 'render_state.json')
    if not os.path.exists(sp):
        json.dump(dict(project=pid, last_run=None, clips=[]), open(sp,'w'), indent=2)
    log('OK', f'Project created: {pdir}')
    log('INFO', f'Copied: {copied}')
    return pdir

def dashboard(root_dir, project_dir=None):
    def show(d):
        pp = os.path.join(d, 'clip_plan.json')
        if not os.path.exists(pp): return
        plan = json.load(open(pp, encoding='utf-8'))
        planned = len(plan.get('clips', []))
        completed=failed=0; last='never'
        sp = os.path.join(d, 'render_state.json')
        if os.path.exists(sp):
            st = json.load(open(sp, encoding='utf-8')); last = st.get('last_run') or 'never'
            completed = len([c for c in st.get('clips',[]) if c.get('status')=='completed'])
            failed = len([c for c in st.get('clips',[]) if c.get('status')=='failed'])
        pending = max(0, planned-completed-failed)
        print('-'*52)
        print(f"Project : {os.path.basename(d)}")
        print(f"Series  : {plan.get('series')}   Episode: {plan.get('episode')}")
        print(f"Planned : {planned:3}   Completed: {completed:3}   Failed: {failed:3}   Pending: {pending:3}")
        print(f"Last run: {last}")
    if project_dir: show(project_dir)
    else:
        pdir = os.path.join(root_dir, 'Projects')
        print("DropsofKnowledge - All Projects")
        if os.path.isdir(pdir):
            for d in sorted(os.listdir(pdir)):
                fd = os.path.join(pdir, d)
                if os.path.isdir(fd): show(fd)
        print('-'*52)

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'render':
        sys.exit(render_project(sys.argv[2], sys.argv[3], '--force' in sys.argv))
    elif cmd == 'import':
        import_lecture(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    elif cmd == 'dashboard':
        dashboard(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    else:
        print(__doc__); sys.exit(64)
