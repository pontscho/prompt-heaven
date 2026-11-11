#!/usr/bin/env python3
"""measure.py -- measure a sample PDF's layout.

Reads a sample PDF (dumped in process through litepdf, per-glyph advances and
images included) or an existing litepdf --dump-json JSONL, and reports per page:

  * the page box (MediaBox / CropBox, exact);
  * text left edges (histogram of line-start x: the margin and the indents),
    the right edge of justified lines, the centre axis of centred lines;
  * per style (font without its subset tag, size, bold, italic): line count,
    the baseline pitch histogram (consecutive baselines of one style in one
    x-column), the pitch (mean of the modal cluster) and the typical
    paragraph gap;
  * the first and last body baseline;
  * the page-number run (a short numeric line near the bottom or top);
  * rects (bbox, stroke width, drawn geometry) and images (bbox, file).

The report is for the human (and the model): it explains the sample's roles
and rhythm before replica.py emits the Typst source.  It writes no layout file.

Usage:
    measure.py SAMPLE.pdf --out-dir DIR [--json]
    measure.py DUMP.jsonl [--json]

    --out-dir DIR      required for a .pdf: the dump goes to DIR/<stem>.dump.jsonl
                       and its images to DIR/<stem>_images/
    --litepdf-dir DIR  directory of litepdf.py (else $LITEPDF_DIR)
    --json             print {"dump", "fonts", "pages": [...]} instead of Markdown

Environment:
    LITEPDF_DIR        directory of litepdf.py when --litepdf-dir is not given
                       (only needed for a .pdf input).  No other lookup, no fallback.

Output:
    Markdown on stdout (one section per page); a one-line summary on stderr.

Exit codes:
    0  measured
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


# ------------------------------------------------------------------ report
def page_md(a):
    o = ['## Page %s' % a['page'], '']
    b = a['box']
    o.append('- page box: %s x %s pt, MediaBox %s, CropBox %s, rotation %s'
             % (fmt(b['width']), fmt(b['height']), b['mediabox'], b['cropbox'], b['rotation']))
    o.append('- first / last body baseline: %s / %s' % (fmt(a['first_baseline']), fmt(a['last_baseline'])))
    o.append('- left margin: %s; right edge of justified lines: %s; centre axis: %s (%d centred lines)'
             % (fmt(a['margin']), fmt(a['right_edge']), fmt(a['centre_axis']), a['centre_lines']))
    pn = a['page_number']
    if pn:
        o.append('- page number "%s": x %s, x centre %s, baseline %s, %s'
                 % (pn['text'], fmt(pn['x']), fmt(pn['x_center']), fmt(pn['baseline']), pn['style']))
    else:
        o.append('- page number: none')
    o += ['', '| line-start x | lines |', '|---|---|']
    o += ['| %s | %d |' % (fmt(x, 1), c) for x, c in a['left_hist']]
    if a['right_hist']:
        o += ['', 'Right edges of full lines: %s' % ', '.join('%s (%d)' % (fmt(x, 1), c) for x, c in a['right_hist'])]
    o += ['', '| style | lines | pitch | mode | pitch histogram | paragraph gap | gap - pitch |',
          '|---|---|---|---|---|---|---|']
    for k, s in sorted(a['styles'].items(), key=lambda kv: -kv[1]['lines']):
        hist = ', '.join('%s x%d' % (fmt(d, 2), c) for d, c in s['pitch_hist'][:8])
        o.append('| %s%s | %d | %s | %s | %s | %s | %s |' % (
            k, ' (body)' if k == a['body_style'] else '', s['lines'], fmt(s['pitch']), fmt(s['pitch_mode']),
            hist or '-', fmt(s['gap']), fmt(s['gap_extra'])))
    if a['rects']:
        o += ['', '| rect bbox | stroke width | segments | geometry |', '|---|---|---|---|']
        o += ['| %s | %s | %s | %s |' % (r['bbox'], fmt(r['width']), r['segments'], r['geometry'])
              for r in a['rects']]
    if a['images']:
        o += ['', '| image bbox | file |', '|---|---|']
        o += ['| %s | %s |' % (im['bbox'], im.get('file') or '-') for im in a['images']]
    o.append('')
    return '\n'.join(o)


def _jsonable(a):
    b = dict(a)
    b['lines'] = [{k: v for k, v in ln.items()} for ln in a['lines']]
    return b


def main(argv=None):
    ap = argparse.ArgumentParser(prog='measure.py', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input')
    ap.add_argument('--out-dir')
    ap.add_argument('--litepdf-dir')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    try:
        if a.input.lower().endswith('.pdf'):
            if not a.out_dir:
                raise rc.Refusal('a .pdf input needs --out-dir DIR (where the dump and its images go)')
            stem = os.path.splitext(os.path.basename(a.input))[0]
            img_dir = os.path.abspath(os.path.join(a.out_dir, stem + '_images'))
            head, pages, lines = rc.load_dump(a.input, a.litepdf_dir, img_dir)
            dump_path = os.path.join(a.out_dir, stem + '.dump.jsonl')
            with open(dump_path, 'w', encoding='utf-8') as fh:
                fh.write('\n'.join(lines) + '\n')
        else:
            head, pages, _l = rc.load_dump(a.input)
            dump_path = a.input
        analyses = [rc.analyse_page(p) for p in pages]
    except rc.Refusal as e:
        rc.die('measure: %s' % e)
    except OSError as e:
        rc.die('measure: %s' % e)
    if a.json:
        print(json.dumps({'dump': dump_path, 'fonts': rc.doc_fonts(head, pages),
                          'pages': [_jsonable(x) for x in analyses]},
                         ensure_ascii=False, indent=1))
    else:
        print('# measure: %s\n' % a.input)
        print('- dump: %s' % dump_path)
        print('- fonts (tags stripped): %s\n' % ', '.join(rc.doc_fonts(head, pages)))
        for x in analyses:
            print(page_md(x))
    sys.stderr.write('measure: %d pages\n' % len(pages))
    return 0


if __name__ == '__main__':
    sys.exit(main())
