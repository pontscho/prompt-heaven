#!/usr/bin/env python3
"""replica_common -- shared helpers of the documents skill's scripts (stdlib only).

Not a command: measure.py, replica.py and compare.py import it from their own
directory.  It holds

  * the liteparser lookup (--litepdf-dir, then $LITEPDF_DIR, nothing else): one
    module for measure.py / compare.py, the whole litepdf package (LITEPDF_PACKAGE)
    for replica.py, whose directory is also put on sys.path so that
    litepdf_typst's sibling imports resolve,
  * loading a geometry dump (a litepdf --dump-json JSONL, or a PDF dumped in
    process through litepdf.dump_json),
  * the page analysis both measure.py and compare.py report on: lines, left
    margin, right edge, centre axis, per-style baseline pitch, the page-number
    run, rects (with their stroke geometry) and images.

Conventions of the analysis (top-left origin, y down, pt, as the dump):

  * A LINE is the runs sharing one baseline (within 0.05 pt) with no
    horizontal gap wider than 3 x the font size; lone-space runs are ignored.
  * A line's style is the (font without its ABCDEF+ tag, size, bold, italic)
    of the most non-space characters on it.
  * The line's content right edge excludes trailing spaces: with per-glyph
    advances (--glyphs) it is exact, without them each trailing space counts
    0.25 em (Times / Liberation space width).  The centre of a centred line
    uses the drawn box INCLUDING trailing spaces, because LibreOffice and Word
    centre a paragraph with its trailing spaces.
  * Pitch: consecutive baselines of one style in one x-column; the reported
    pitch is the mean of the modal cluster (the mode +-0.15 pt), so a producer
    that rounds 16.55 pt to 16.5 / 16.6 reports 16.55.
  * PDFium reports a stroked path's bbox grown by the full stroke width on
    every side; `geometry` undoes that.

Exit codes used through die(): 2 (bad invocation, missing dependency, refusal).
"""
import importlib.util
import json
import os
import re
import statistics
import sys
from collections import Counter

LITEPDF_PACKAGE = ('litepdf.py', 'litepdf_write.py', 'litepdf_flow.py', 'litepdf_hyph.py',
                   'litepdf_typst.py')
LOOKUP_HINT = 'Pass --litepdf-dir DIR (or set $LITEPDF_DIR). There is no fallback.'
_TAG_RE = re.compile(r'^[A-Z]{6}\+')
_NUM_RE = re.compile(r'^\d{1,4}$')

LINE_TOL = 0.05        # pt: runs within this of a baseline share the line
GAP_FACTOR = 3.0       # x size: a wider horizontal gap splits a line
SPACE_EM = 0.25        # trailing-space width when no per-glyph advances
PITCH_CLUSTER = 0.15   # pt: diffs within this of the mode form the pitch cluster
SYM_TOL = 3.0          # pt: a centred line's left and right insets agree within this


class Refusal(Exception):
    """An expected failure: reported as one stderr line, exit 2."""


def die(msg, code=2):
    sys.stderr.write(msg.rstrip('\n').replace('\n', ' ') + '\n')
    raise SystemExit(code)


# ---------------------------------------------------------------- liteparser
def _first_missing(d, needs):
    for n in needs:
        if not os.path.isfile(os.path.join(d, n)):
            return n
    return None


def find_litepdf_dir(flag_value, need):
    """Directory holding `need` (a file name, or a tuple of them, all required): the flag, then
    $LITEPDF_DIR.  A miss names the first missing file in one message."""
    needs = (need,) if isinstance(need, str) else tuple(need)
    if flag_value:
        d = os.path.abspath(os.path.expanduser(flag_value))
        miss = _first_missing(d, needs)
        if miss:
            raise Refusal('--litepdf-dir %s has no %s; refused. There is no fallback.' % (d, miss))
        return d
    env = os.environ.get('LITEPDF_DIR')
    if env:
        d = os.path.abspath(os.path.expanduser(env))
        miss = _first_missing(d, needs)
        if miss:
            raise Refusal('$LITEPDF_DIR=%s has no %s; refused. There is no fallback.' % (d, miss))
        return d
    raise Refusal('%s not found: looked at --litepdf-dir (not given) and $LITEPDF_DIR (not set). %s'
                  % (needs[0], LOOKUP_HINT))


def use_litepdf_package(flag_value):
    """The liteparser directory holding every module of LITEPDF_PACKAGE, put on sys.path (the
    modules import each other by plain name).  Refusal naming the first missing module."""
    d = find_litepdf_dir(flag_value, LITEPDF_PACKAGE)
    if d not in sys.path:
        sys.path.insert(0, d)
    return d


def import_by_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise Refusal('cannot import %s from %s' % (name, path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- dumps
def parse_jsonl(lines, origin='dump'):
    head, pages = None, []
    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except ValueError as e:
            raise Refusal('%s line %d is not JSON (%s)' % (origin, i, e))
        if not isinstance(o, dict):
            raise Refusal('%s line %d is not a JSON object' % (origin, i))
        if 'convention' in o and 'runs' not in o:
            head = o
        elif 'runs' in o:
            pages.append(o)
    if not pages:
        raise Refusal('%s holds no page lines' % origin)
    for p in pages:
        rot = p.get('rotation') or 0
        if rot:
            raise Refusal('%s page %s has rotation %s; only rotation 0 is supported (v1)'
                          % (origin, p.get('page'), rot))
    return head or {}, pages


def dump_pdf(pdf_path, litepdf_dir, img_dir=None):
    """Dump a PDF in process (with --glyphs) -> list of JSONL lines."""
    lp = import_by_path('litepdf', os.path.join(litepdf_dir, 'litepdf.py'))
    if img_dir:
        os.makedirs(img_dir, exist_ok=True)
    try:
        return lp.dump_json(pdf_path, None, None, True, img_dir)
    except (SystemExit, Exception) as e:   # PDFium load failure, missing lib, ...
        raise Refusal('litepdf could not dump %s (%s: %s)' % (pdf_path, type(e).__name__, e))


def load_dump(path, litepdf_flag=None, img_dir=None):
    """(header, pages, source_lines) for a .pdf or a dump .jsonl."""
    if not os.path.isfile(path):
        raise Refusal('no such file: %s' % path)
    if path.lower().endswith('.pdf'):
        d = find_litepdf_dir(litepdf_flag, 'litepdf.py')
        lines = dump_pdf(path, d, img_dir)
    else:
        try:
            with open(path, encoding='utf-8') as fh:
                lines = fh.read().splitlines()
        except (OSError, UnicodeDecodeError) as e:
            raise Refusal('cannot read %s (%s)' % (path, e))
    head, pages = parse_jsonl(lines, path)
    return head, pages, lines


# ---------------------------------------------------------------- analysis
def strip_tag(name):
    return _TAG_RE.sub('', name or '')


def style_key(run):
    key = '%s %spt' % (strip_tag(run.get('font')), fmt(run.get('size', 0)))
    if run.get('bold'):
        key += ' bold'
    if run.get('italic'):
        key += ' italic'
    return key


def fmt(v, nd=3):
    if v is None:
        return '-'
    s = ('%.*f' % (nd, float(v))).rstrip('0').rstrip('.')
    return '0' if s in ('', '-0') else s


def _content_right(run):
    t = run.get('text', '')
    core = t.rstrip(' ')
    if len(core) == len(t):
        return run['bbox'][2]
    adv = run.get('advance')
    if adv and len(core) < len(adv):
        return run['x'] + adv[len(core)]
    return run['bbox'][2] - (len(t) - len(core)) * SPACE_EM * float(run.get('size') or 0)


def build_lines(runs):
    rs = [r for r in runs if r.get('text', '').strip() and r.get('bbox')]
    rs.sort(key=lambda r: (r['baseline'], r['x']))
    groups, cur = [], []
    for r in rs:
        if cur and abs(r['baseline'] - cur[0]['baseline']) <= LINE_TOL:
            cur.append(r)
        else:
            if cur:
                groups.append(cur)
            cur = [r]
    if cur:
        groups.append(cur)
    lines = []
    for g in groups:
        g.sort(key=lambda r: r['x'])
        part = [g[0]]
        for r in g[1:]:
            gap = r['x'] - max(p['bbox'][2] for p in part)
            if gap > GAP_FACTOR * float(r.get('size') or 12):
                lines.append(_mk_line(part))
                part = [r]
            else:
                part.append(r)
        lines.append(_mk_line(part))
    lines.sort(key=lambda ln: (ln['baseline'], ln['left']))
    return lines


def _mk_line(part):
    cnt = Counter()
    for r in part:
        cnt[style_key(r)] += len(r['text'].replace(' ', '')) or 1
    style = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    last = part[-1]
    first = part[0]
    return {
        'baseline': first['baseline'],
        'left': first['x'],
        'right': _content_right(last),
        'right_full': max(r['bbox'][2] for r in part),
        'text': ''.join(r['text'] for r in part),
        'style': style,
        'size': float(first.get('size') or 0),
        'font': strip_tag(first.get('font')),
        'runs': len(part),
    }


def _hist(values, nd=1):
    c = Counter(round(v, nd) for v in values)
    return sorted(c.items())


def modal_cluster(diffs):
    """(mode, cluster mean, histogram, cluster size) of baseline diffs; (None, None, [], 0) if none."""
    if not diffs:
        return None, None, [], 0
    hist = Counter(round(d, 2) for d in diffs)
    mode = sorted(hist.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    cl = [d for d in diffs if abs(d - mode) <= PITCH_CLUSTER]
    return mode, statistics.mean(cl), sorted(hist.items()), len(cl)


def find_page_number(lines, height):
    cands = [ln for ln in lines if _NUM_RE.match(ln['text'].strip())
             and (ln['baseline'] >= 0.85 * height or ln['baseline'] <= 0.15 * height)]
    if not cands:
        return None
    ln = max(cands, key=lambda l: l['baseline'])
    return {'text': ln['text'].strip(), 'x': ln['left'], 'right': ln['right'],
            'x_center': (ln['left'] + ln['right']) / 2.0, 'baseline': ln['baseline'],
            'font': ln['font'], 'size': ln['size'], 'style': ln['style'], '_line': ln}


def rect_geometry(r):
    """Undo PDFium's stroke inflation: the drawn geometry of a dumped rect/path."""
    x0, y0, x1, y1 = r['bbox']
    w = float(r.get('width') or 0) if r.get('stroke') else 0.0
    g = [x0 + w, y0 + w, x1 - w, y1 - w]
    if g[0] > g[2]:
        g[0] = g[2] = (x0 + x1) / 2.0
    if g[1] > g[3]:
        g[1] = g[3] = (y0 + y1) / 2.0
    return [round(v, 3) for v in g]


def is_centred(ln, margin, right_edge):
    """Starts right of the margin, ends left of the edge, equal insets (within SYM_TOL)."""
    return (ln['left'] > margin + 1.0 and ln['right_full'] < right_edge - 1.0
            and abs((ln['left'] - margin) - (right_edge - ln['right_full'])) <= SYM_TOL)


def _style_size(key):
    try:
        return float(key.split(' ')[1][:-2])
    except (IndexError, ValueError):
        return None


def analyse_page(page):
    lines = build_lines(page.get('runs') or [])
    h = float(page.get('height') or 0)
    pn = find_page_number(lines, h)
    body = [ln for ln in lines if pn is None or ln is not pn['_line']]
    res = {
        'page': page.get('page'),
        'box': {'width': page.get('width'), 'height': page.get('height'),
                'mediabox': page.get('mediabox'), 'cropbox': page.get('cropbox'),
                'rotation': page.get('rotation') or 0},
        'lines': body,
        'page_number': None if pn is None else {k: v for k, v in pn.items() if k != '_line'},
        'left_hist': [], 'margin': None, 'right_edge': None, 'right_hist': [],
        'centre_axis': None, 'centre_lines': 0, 'centre_hist': [],
        'styles': {}, 'body_style': None, 'body_pitch': None, 'body_pitch_n': 0,
        'first_baseline': None, 'last_baseline': None,
        'rects': [], 'images': [],
    }
    for r in page.get('rects') or []:
        res['rects'].append({'bbox': r.get('bbox'), 'width': r.get('width'), 'stroke': r.get('stroke'),
                             'fill': r.get('fill'), 'segments': r.get('segments'),
                             'geometry': rect_geometry(r)})
    for im in page.get('images') or []:
        res['images'].append({'bbox': im.get('bbox'), 'file': im.get('file'), 'name': im.get('name')})
    if not body:
        return res
    res['first_baseline'] = min(ln['baseline'] for ln in body)
    res['last_baseline'] = max(ln['baseline'] for ln in body)
    lefts = [ln['left'] for ln in body]
    lh = _hist(lefts)
    res['left_hist'] = lh
    multi = [x for x, c in lh if c >= 2]
    margin = min(multi) if multi else min(lefts)
    res['margin'] = min((x for x in lefts if round(x, 1) == margin), default=margin)
    m = res['margin']
    at_margin = [ln for ln in body if abs(ln['left'] - m) <= 0.5]
    if at_margin:
        maxr = max(ln['right'] for ln in at_margin)
        full = [ln for ln in at_margin if ln['right'] >= m + 0.9 * (maxr - m)]
        res['right_edge'] = max(ln['right'] for ln in full)
        res['right_hist'] = _hist([ln['right'] for ln in full])
    re_ = res['right_edge']
    if re_ is not None:
        for ln in body:
            ln['centred'] = is_centred(ln, m, re_)
        cents = [(ln['left'] + ln['right_full']) / 2.0 for ln in body if ln['centred']]
        if cents:
            res['centre_axis'] = statistics.median(cents)
            res['centre_lines'] = len(cents)
            res['centre_hist'] = _hist(cents, 2)
    # styles and pitch: one column per line-start x, centred lines per centre x
    by_style = {}
    for ln in body:
        by_style.setdefault(ln['style'], []).append(ln)
    for st, lns in by_style.items():
        cols = {}
        for ln in lns:
            col = ('c', round((ln['left'] + ln['right_full']) / 2.0)) if ln.get('centred') \
                else ('l', round(ln['left']))
            cols.setdefault(col, []).append(ln['baseline'])
        diffs = []
        for bls in cols.values():
            bls.sort()
            diffs += [b - a for a, b in zip(bls, bls[1:]) if b - a > 0.5]
        mode, pitch, hist, n = modal_cluster(diffs)
        size = _style_size(st)
        if mode is not None and size and mode > 2.0 * size:
            mode = pitch = None     # no two lines of this style were adjacent: no pitch
            n = 0
        gap = None
        if pitch:
            big = [d for d in diffs if pitch * 1.05 < d <= pitch * 3]
            if big:
                gap = sorted(Counter(round(d, 1) for d in big).items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        res['styles'][st] = {'lines': len(lns), 'pitch': pitch, 'pitch_mode': mode, 'pitch_n': n,
                             'pitch_hist': hist, 'gap': gap,
                             'gap_extra': None if gap is None or pitch is None else gap - pitch}
    bs = sorted(res['styles'].items(), key=lambda kv: (-kv[1]['lines'], kv[0]))[0][0]
    res['body_style'] = bs
    res['body_pitch'] = res['styles'][bs]['pitch']
    res['body_pitch_n'] = res['styles'][bs]['pitch_n']
    return res


def doc_fonts(head, pages):
    names = set()
    for f in (head or {}).get('fonts') or []:
        if f.get('name'):
            names.add(strip_tag(f['name']))
    for p in pages:
        for r in p.get('runs') or []:
            if r.get('font') and r.get('text', '').strip():
                names.add(strip_tag(r['font']))
    return sorted(names)


def doc_text(pages):
    return '\n'.join(''.join(r.get('text', '') for r in p.get('runs') or []) for p in pages)
