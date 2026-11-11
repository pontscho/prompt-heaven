#!/usr/bin/env python3
"""compare.py -- compare the layout STRUCTURE of a replica against its sample.

The content differs between sample and replica, so the text is not compared;
the geometry the layout spec controls is.  Both sides go through the same
analysis as measure.py (replica_common.analyse_page).

Checks, per mapped page pair (sample page -> output page):
    page box        width, height and MediaBox, within --box-tol (default exact: 0.001)
    left margin     smallest line-start x column, within --tol
    right edge      rightmost justified line end, within --right-tol (default 0.6:
                    LibreOffice / Word justification leaves lines up to ~0.56 pt
                    short of the margin, measured on the bill sample)
    centre axis     median centre of centred lines, within --tol (INFO when one
                    side has no centred line: that depends on the content)
    body pitch      the pitch of each page's most frequent style, within --tol
    style pitch     the pitch of every style both pages carry, within --tol
                    (each shown with n, its count of baseline diffs; a delta
                    on a side with n < --min-pitch-samples (default 2) is INFO,
                    because one diff of a producer that rounds to 0.1 pt
                    cannot tell 16.55 from 16.6)
    first baseline  the first body baseline, within --tol
    page number     x centre and baseline within --tol, same font and size;
                    present on one side only is a FAIL
    rects / images  each sample object matched to the nearest output object of
                    the same kind; the largest bbox coordinate delta within
                    --tol; a missing or an extra object is a FAIL
Document checks:
    fonts           every output font (subset tag stripped) is a sample font
    --forbid        none of these characters occurs in the output text
    --require       each of these characters occurs in the output text
    page count      INFO only

Usage:
    compare.py SAMPLE.(pdf|jsonl) OUTPUT.(pdf|jsonl) [--tol 0.05] [--right-tol 0.6]
               [--box-tol 0.001] [--min-pitch-samples 2]
               [--pages-map 1:1,2:2,...] [--forbid U+2013,U+2014]
               [--require őűŐŰ] [--litepdf-dir DIR] [--json]

    --pages-map   comma-separated sample:output page pairs (1-based); a sample
                  page may map to several output pages.  Default: 1:1, 2:2, ...
                  up to the shorter document.
    --forbid / --require
                  characters as U+XXXX code points and/or literal characters,
                  comma-separated or run together ("őű" = "U+0151,U+0171").

Environment:
    LITEPDF_DIR   directory of litepdf.py when --litepdf-dir is not given (only
                  needed for a .pdf input).  No other lookup, no fallback.

Output:
    A Markdown PASS / FAIL table on stdout (with --json: {"rows", "fail", "pass"});
    a one-line verdict on stderr.

Exit codes:
    0  every check PASS (INFO rows never fail)
    1  at least one FAIL
    2  bad invocation, unreadable input, missing litepdf, a page with
       rotation != 0 (v1 supports rotation 0 only); one stderr line
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import replica_common as rc  # noqa: E402

fmt = rc.fmt


def parse_pages_map(spec, ns, no):
    if not spec:
        return [(i, i) for i in range(1, min(ns, no) + 1)]
    pairs = []
    for part in spec.split(','):
        part = part.strip()
        if not part:
            continue
        try:
            a, b = part.split(':')
            a, b = int(a), int(b)
        except ValueError:
            raise rc.Refusal('--pages-map: "%s" is not sample:output' % part)
        if not (1 <= a <= ns):
            raise rc.Refusal('--pages-map: sample page %d out of range 1..%d' % (a, ns))
        if not (1 <= b <= no):
            raise rc.Refusal('--pages-map: output page %d out of range 1..%d' % (b, no))
        pairs.append((a, b))
    if not pairs:
        raise rc.Refusal('--pages-map is empty')
    return pairs


def parse_chars(spec, flag):
    out = []
    if not spec:
        return out
    for tok in spec.split(','):
        tok = tok.strip()
        if not tok:
            continue
        if tok[:2] in ('U+', 'u+'):
            try:
                out.append(chr(int(tok[2:], 16)))
            except ValueError:
                raise rc.Refusal('%s: bad code point "%s"' % (flag, tok))
        else:
            out.extend(tok)
    return out


def _row(rows, check, page, s, o, delta, tol, status):
    rows.append({'check': check, 'page': page, 'sample': s, 'output': o,
                 'delta': delta, 'tol': tol, 'status': status})


def _num_row(rows, check, page, s, o, tol, missing='FAIL'):
    if s is None and o is None:
        _row(rows, check, page, '-', '-', None, tol, 'PASS')
    elif s is None or o is None:
        _row(rows, check, page, fmt(s), fmt(o), None, tol, missing)
    else:
        d = o - s
        _row(rows, check, page, fmt(s), fmt(o), d, tol, 'PASS' if abs(d) <= tol + 1e-9 else 'FAIL')


def _pitch_row(rows, check, page, ps, ns, po, no, tol, min_n):
    """A pitch row; INFO when a side has fewer than min_n baseline diffs in its pitch cluster."""
    _num_row(rows, check, page, ps, po, tol, missing='INFO')
    r = rows[-1]
    r['sample'] = '%s (n=%d)' % (r['sample'], ns) if ps is not None else r['sample']
    r['output'] = '%s (n=%d)' % (r['output'], no) if po is not None else r['output']
    if r['status'] == 'FAIL' and min(ns, no) < min_n:
        r['status'] = 'INFO'


def _match(rows, kind, page, sa, oa, tol):
    left = list(range(len(oa)))
    for i, s in enumerate(sa):
        sb = s['bbox']
        if not left:
            _row(rows, '%s %d' % (kind, i + 1), page, str(sb), 'missing', None, tol, 'FAIL')
            continue
        sc = ((sb[0] + sb[2]) / 2.0, (sb[1] + sb[3]) / 2.0)

        def dist(j):
            b = oa[j]['bbox']
            return (((b[0] + b[2]) / 2.0 - sc[0]) ** 2 + ((b[1] + b[3]) / 2.0 - sc[1]) ** 2,
                    max(abs(x - y) for x, y in zip(b, sb)))
        j = min(left, key=dist)
        left.remove(j)
        ob = oa[j]['bbox']
        d = max(abs(x - y) for x, y in zip(ob, sb))
        _row(rows, '%s %d bbox' % (kind, i + 1), page, str(sb), str(ob), d, tol,
             'PASS' if d <= tol + 1e-9 else 'FAIL')
    for j in left:
        _row(rows, '%s extra' % kind, page, 'none', str(oa[j]['bbox']), None, tol, 'FAIL')


def compare_docs(shead, spages, ohead, opages, pairs, tol=0.05, right_tol=0.6, box_tol=0.001,
                 forbid=(), require=(), min_pitch_n=2):
    """-> list of row dicts (check, page, sample, output, delta, tol, status)."""
    rows = []
    sa = {p['page']: rc.analyse_page(p) for p in spages}
    oa = {p['page']: rc.analyse_page(p) for p in opages}
    sp_by_n = {k: p['page'] for k, p in enumerate(spages, 1)}
    op_by_n = {k: p['page'] for k, p in enumerate(opages, 1)}
    _row(rows, 'page count', '', len(spages), len(opages), None, None, 'INFO')
    for sn, on in pairs:
        A, B = sa[sp_by_n[sn]], oa[op_by_n[on]]
        pg = '%d->%d' % (sn, on)
        _num_row(rows, 'page width', pg, A['box']['width'], B['box']['width'], box_tol)
        _num_row(rows, 'page height', pg, A['box']['height'], B['box']['height'], box_tol)
        ms, mo = A['box']['mediabox'] or [], B['box']['mediabox'] or []
        if ms and mo:
            d = max(abs(x - y) for x, y in zip(ms, mo))
            _row(rows, 'MediaBox', pg, str(ms), str(mo), d, box_tol, 'PASS' if d <= box_tol + 1e-9 else 'FAIL')
        _num_row(rows, 'left margin', pg, A['margin'], B['margin'], tol)
        _num_row(rows, 'right edge', pg, A['right_edge'], B['right_edge'], right_tol)
        _num_row(rows, 'centre axis', pg, A['centre_axis'], B['centre_axis'], tol, missing='INFO')
        _pitch_row(rows, 'body pitch (%s | %s)' % (A['body_style'], B['body_style']), pg,
                   A['body_pitch'], A['body_pitch_n'], B['body_pitch'], B['body_pitch_n'], tol, min_pitch_n)
        for st in sorted(set(A['styles']) & set(B['styles'])):
            sa_, so_ = A['styles'][st], B['styles'][st]
            if sa_['pitch'] is not None and so_['pitch'] is not None:
                _pitch_row(rows, 'pitch %s' % st, pg, sa_['pitch'], sa_['pitch_n'], so_['pitch'],
                           so_['pitch_n'], tol, min_pitch_n)
        _num_row(rows, 'first baseline', pg, A['first_baseline'], B['first_baseline'], tol)
        pa, pb = A['page_number'], B['page_number']
        if pa is None and pb is None:
            _row(rows, 'page number', pg, 'none', 'none', None, None, 'PASS')
        elif pa is None or pb is None:
            _row(rows, 'page number', pg, 'none' if pa is None else pa['text'],
                 'none' if pb is None else pb['text'], None, None, 'FAIL')
        else:
            _num_row(rows, 'page number x centre', pg, pa['x_center'], pb['x_center'], tol)
            _num_row(rows, 'page number baseline', pg, pa['baseline'], pb['baseline'], tol)
            _row(rows, 'page number font', pg, pa['style'], pb['style'], None, None,
                 'PASS' if pa['style'] == pb['style'] else 'FAIL')
        _match(rows, 'rect', pg, A['rects'], B['rects'], tol)
        _match(rows, 'image', pg, A['images'], B['images'], tol)
    mapped_o = {on for _s, on in pairs}
    rest = [k for k in range(1, len(opages) + 1) if k not in mapped_o]
    if rest:
        _row(rows, 'output pages not compared', '', '', ','.join(map(str, rest)), None, None, 'INFO')
    fs, fo = rc.doc_fonts(shead, spages), rc.doc_fonts(ohead, opages)
    extra = [f for f in fo if f not in fs]
    _row(rows, 'fonts', '', ', '.join(fs), ', '.join(fo), None, None, 'FAIL' if extra else 'PASS')
    text = rc.doc_text(opages)
    for ch in forbid:
        n = text.count(ch)
        _row(rows, 'forbid U+%04X' % ord(ch), '', '', '%d found' % n, None, None, 'FAIL' if n else 'PASS')
    for ch in require:
        n = text.count(ch)
        _row(rows, 'require U+%04X %s' % (ord(ch), ch), '', '', '%d found' % n, None, None,
             'PASS' if n else 'FAIL')
    return rows


def table_md(rows):
    o = ['| # | check | pages | sample | output | delta | tol | status |',
         '|---|---|---|---|---|---|---|---|']
    for i, r in enumerate(rows, 1):
        o.append('| %d | %s | %s | %s | %s | %s | %s | %s |' % (
            i, r['check'], r['page'], r['sample'], r['output'],
            '-' if r['delta'] is None else '%+.3f' % r['delta'],
            '-' if r['tol'] is None else fmt(r['tol']), r['status']))
    return '\n'.join(o)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='compare.py', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('sample')
    ap.add_argument('output')
    ap.add_argument('--tol', type=float, default=0.05)
    ap.add_argument('--right-tol', type=float, default=0.6)
    ap.add_argument('--box-tol', type=float, default=0.001)
    ap.add_argument('--min-pitch-samples', type=int, default=2)
    ap.add_argument('--pages-map')
    ap.add_argument('--forbid')
    ap.add_argument('--require')
    ap.add_argument('--litepdf-dir')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    try:
        sh, sp, _l = rc.load_dump(a.sample, a.litepdf_dir)
        oh, op, _l = rc.load_dump(a.output, a.litepdf_dir)
        pairs = parse_pages_map(a.pages_map, len(sp), len(op))
        if a.min_pitch_samples < 1:
            raise rc.Refusal('--min-pitch-samples must be at least 1')
        rows = compare_docs(sh, sp, oh, op, pairs, a.tol, a.right_tol, a.box_tol,
                            parse_chars(a.forbid, '--forbid'), parse_chars(a.require, '--require'),
                            a.min_pitch_samples)
    except rc.Refusal as e:
        rc.die('compare: %s' % e)
    nf = sum(1 for r in rows if r['status'] == 'FAIL')
    npass = sum(1 for r in rows if r['status'] == 'PASS')
    if a.json:
        print(json.dumps({'sample': a.sample, 'output': a.output, 'rows': rows, 'fail': nf, 'pass': npass},
                         ensure_ascii=False, indent=1))
    else:
        print('# compare: %s vs %s\n' % (a.sample, a.output))
        print(table_md(rows))
        print('\n**%s**: %d PASS, %d FAIL, %d INFO' % ('FAIL' if nf else 'PASS', npass, nf,
                                                     len(rows) - nf - npass))
    sys.stderr.write('compare: %s (%d PASS, %d FAIL)\n' % ('FAIL' if nf else 'PASS', npass, nf))
    return 1 if nf else 0


if __name__ == '__main__':
    sys.exit(main())
