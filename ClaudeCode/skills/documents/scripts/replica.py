#!/usr/bin/env python3
"""replica.py -- emit a sample PDF as editable Typst source, and build that source into a PDF.

Both steps run the liteparser litepdf_typst API IN PROCESS (never its CLI):

    PDF --litepdf dump--> pages --emit()--> report.typ --(model edits)--> build() --> report.pdf

The .typ is a strict Typst SUBSET compiled by litepdf_typst itself; NO Typst binary is
involved, and the subset is UNVERIFIED against the real Typst (see typst-subset.md).

Usage:
    replica.py emit SAMPLE.pdf|DUMP.jsonl --font-dir DIR [--font-dir DIR ...] -o WORK/report.typ
                    [--image-dir DIR] [--litepdf-dir DIR]
    replica.py build WORK/report.typ --font-dir DIR [--font-dir DIR ...] -o WORK/report.pdf
                    [--no-text-check] [--litepdf-dir DIR]

    emit    Dumps a .pdf in process (glyph advances included; its images go NEXT TO the
            .typ, because the source names them relative to its own directory), or reads
            a litepdf --dump-json JSONL (images listed in it are copied from --image-dir,
            default the dump's directory).  fonts.json is built from the TTFs found under
            --font-dir (grouped by PostScript family and style).  Writes the .typ and
            prints a short per-page report (flow or absolute, paragraphs, headings).
    build   Compiles the .typ with litepdf_typst.build and writes the PDF, then checks that
            every word written appears in the pdftotext output.  INFO line and exit 0 when
            pdftotext is not on PATH (or with --no-text-check).

    The outputs share a stem: emit writes NAME.typ, build NAME.typ -> NAME.pdf (a different
    stem is refused).  emit_read is deliberately not exposed.

    --font-dir DIR     directory holding the sample's TrueType fonts (repeatable; searched
                       recursively for .ttf).  Required.
    --litepdf-dir DIR  directory of the liteparser package (else $LITEPDF_DIR).  The directory
                       must hold litepdf.py, litepdf_write.py, litepdf_flow.py,
                       litepdf_hyph.py and litepdf_typst.py.  No other lookup, no fallback.

Exit codes:
    0  done
    1  build: a word written is missing from the pdftotext output
    2  bad invocation, missing dependency, a refusal, a TypstSubsetError (line:col and the
       `use:` hint) or TypstEmitError; always ONE stderr line
"""
import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import replica_common as rc  # noqa: E402

_STYLE_RE = re.compile(r'[-,](?:Bold|Italic|Ital|Oblique|Regular|Regu|Roman|BoldItalic|BoldOblique)+'
                       r'(?:MT|PS)?$', re.I)


# ------------------------------------------------------------------ fonts
def ttf_psname(path):
    """PostScript name (name id 6) of a TTF, or None."""
    try:
        with open(path, 'rb') as fh:
            d = fh.read()
        off = struct.unpack('>I', d[12:16])[0] if d[:4] == b'ttcf' else 0
        n = struct.unpack('>H', d[off + 4:off + 6])[0]
        for i in range(n):
            tag, _c, o, ln = struct.unpack('>4sIII', d[off + 12 + 16 * i:off + 28 + 16 * i])
            if tag != b'name':
                continue
            nm = d[o:o + ln]
            cnt, so = struct.unpack('>HH', nm[2:6])
            for k in range(cnt):
                pid, _e, _l, nid, sl, sofs = struct.unpack('>6H', nm[6 + 12 * k:18 + 12 * k])
                if nid == 6:
                    raw = nm[so + sofs:so + sofs + sl]
                    return raw.decode('utf-16-be' if pid in (0, 3) else 'latin-1', 'ignore')
    except (OSError, struct.error, IndexError):
        return None
    return None


def split_name(name):
    """Font name (subset tag stripped) -> (family, variant); variant is regular | bold | italic |
    bolditalic."""
    fam = _STYLE_RE.sub('', name) or name
    low = name.lower()
    bold = 'bold' in low
    ital = 'italic' in low or 'oblique' in low
    return fam, 'bolditalic' if bold and ital else 'bold' if bold else 'italic' if ital else 'regular'


def _ident(fam):
    return re.sub(r'(mt|ps)+$', '', re.sub(r'[^a-z0-9]', '', fam.lower()))


def fonts_from_dirs(dirs):
    """{family: {variant: ttf path}} from every .ttf under dirs (the first file of a variant wins)."""
    out = {}
    for d in dirs:
        for root, subdirs, files in os.walk(d):
            subdirs.sort()
            for f in sorted(files):
                if not f.lower().endswith('.ttf'):
                    continue
                p = os.path.join(root, f)
                ps = ttf_psname(p)
                if not ps:
                    continue
                fam, var = split_name(ps)
                out.setdefault(fam, {}).setdefault(var, p)
    if not out:
        raise rc.Refusal('--font-dir %s holds no readable .ttf' % ', '.join(dirs))
    return out


def check_fonts(used, fonts):
    """Every dump font that carries text needs its TTF variant in `fonts`."""
    byid = {_ident(f): v for f, v in fonts.items()}
    for name in used:
        fam, var = split_name(name)
        if var not in byid.get(_ident(fam), {}):
            raise rc.Refusal('no TTF for dump font %s (family %s, %s) under --font-dir; '
                             'the metrics would be wrong, so this is refused' % (name, fam, var))


# ------------------------------------------------------------------ helpers
def _check_dirs(dirs):
    if not dirs:
        raise rc.Refusal('--font-dir DIR is required (the sample\'s TrueType fonts)')
    for d in dirs:
        if not os.path.isdir(d):
            raise rc.Refusal('--font-dir %s is not a directory' % d)


def _load_typst(flag):
    d = rc.use_litepdf_package(flag)
    return rc.import_by_path('litepdf_typst', os.path.join(d, 'litepdf_typst.py'))


def expected_words(pages):
    words = set()
    for p in pages:
        lines = {}
        for r in p['runs']:
            lines.setdefault(r['baseline'], []).append(r)
        for rs in lines.values():
            rs.sort(key=lambda r: r['x'])
            words.update(unicodedata.normalize('NFC', ''.join(r['text'] for r in rs)).split())
    return words


def text_check(pdf, words):
    exe = shutil.which('pdftotext')
    if not exe:
        return {'status': 'skipped', 'reason': 'pdftotext not on PATH'}
    try:
        res = subprocess.run([exe, '-enc', 'UTF-8', pdf, '-'], capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return {'status': 'skipped', 'reason': 'pdftotext failed (%s)' % e}
    if res.returncode != 0:
        return {'status': 'skipped', 'reason': 'pdftotext exit %d' % res.returncode}
    got = set(unicodedata.normalize('NFC', res.stdout.decode('utf-8', 'replace')).split())
    missing = sorted(w for w in words if w not in got)
    return {'status': 'FAIL' if missing else 'PASS', 'words': len(words), 'missing': missing}


# ------------------------------------------------------------------ emit
def cmd_emit(a):
    out = a.output
    if not out.lower().endswith('.typ'):
        raise rc.Refusal('-o %s must end in .typ' % out)
    _check_dirs(a.font_dir)
    lt = _load_typst(a.litepdf_dir)
    out_dir = os.path.dirname(os.path.abspath(out))
    os.makedirs(out_dir, exist_ok=True)
    is_pdf = a.input.lower().endswith('.pdf')
    head, pages, _lines = rc.load_dump(a.input, a.litepdf_dir, out_dir if is_pdf else None)
    fonts = fonts_from_dirs(a.font_dir)
    check_fonts(rc.doc_fonts(head, pages), fonts)
    notes = []
    if not is_pdf:
        src_dir = os.path.abspath(a.image_dir or os.path.dirname(os.path.abspath(a.input)))
        for p in pages:
            for im in p.get('images') or []:
                f = im.get('file')
                if not f or os.path.basename(f) != f or os.path.exists(os.path.join(out_dir, f)):
                    continue
                if os.path.isfile(os.path.join(src_dir, f)):
                    shutil.copy2(os.path.join(src_dir, f), os.path.join(out_dir, f))
                else:
                    notes.append('image %s not found in %s: build will refuse it' % (f, src_dir))
    report = []
    try:
        text = lt.emit(pages, fonts, source_name=os.path.basename(a.input), report=report)
    except lt.TypstEmitError as e:
        raise rc.Refusal('emit refused: %s' % e)
    with open(out, 'w', encoding='utf-8') as fh:
        fh.write(text)
    print('# replica emit: %s -> %s\n' % (a.input, out))
    print('| page | decision | paragraphs | headings | why |')
    print('|---|---|---|---|---|')
    for r in report:
        print('| %s | %s | %s | %s | %s |' % (r.get('page'), r.get('decision'), r.get('paragraphs', '-'),
                                             r.get('headings', '-'), r.get('why') or '-'))
    for n in notes:
        print('\nNOTE: %s' % n)
    sys.stderr.write('replica: emit: %d pages -> %s\n' % (len(report), out))
    return 0


# ------------------------------------------------------------------ build
def cmd_build(a):
    src, out = a.source, a.output
    if not src.lower().endswith('.typ'):
        raise rc.Refusal('%s must be a .typ source' % src)
    if not out.lower().endswith('.pdf'):
        raise rc.Refusal('-o %s must end in .pdf' % out)
    if os.path.splitext(os.path.basename(src))[0] != os.path.splitext(os.path.basename(out))[0]:
        raise rc.Refusal('the outputs share a stem: %s must be %s.pdf'
                         % (out, os.path.splitext(os.path.basename(src))[0]))
    if not os.path.isfile(src):
        raise rc.Refusal('no such file: %s' % src)
    _check_dirs(a.font_dir)
    lt = _load_typst(a.litepdf_dir)
    try:
        with open(src, encoding='utf-8') as fh:
            source = fh.read()
    except (OSError, UnicodeDecodeError) as e:
        raise rc.Refusal('cannot read %s (%s)' % (src, e))
    fonts = fonts_from_dirs(a.font_dir)
    try:
        pages = lt.build(source, fonts, out, base_dir=os.path.dirname(os.path.abspath(src)))
    except lt.TypstSubsetError as e:
        raise rc.Refusal('build: %s:%s' % (src, e))
    except (OSError, ValueError) as e:
        raise rc.Refusal('litepdf_write failed: %s' % e)
    tc = {'status': 'skipped', 'reason': '--no-text-check'} if a.no_text_check \
        else text_check(out, expected_words(pages))
    print('# replica build: %s -> %s\n' % (src, out))
    print('- pages: %d' % len(pages))
    if tc['status'] == 'skipped':
        print('INFO: text check skipped (%s)' % tc['reason'])
    else:
        print('- text check: %s (%d words)' % (tc['status'], tc['words']))
        if tc['missing']:
            print('- missing from pdftotext output: %s' % ', '.join(tc['missing'][:20]))
    sys.stderr.write('replica: build: %d pages -> %s; text check %s\n' % (len(pages), out, tc['status']))
    return 1 if tc['status'] == 'FAIL' else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog='replica.py', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd')
    sub.required = True
    e = sub.add_parser('emit')
    e.add_argument('input')
    e.add_argument('-o', '--output', required=True)
    e.add_argument('--font-dir', action='append', default=[])
    e.add_argument('--image-dir')
    e.add_argument('--litepdf-dir')
    b = sub.add_parser('build')
    b.add_argument('source')
    b.add_argument('-o', '--output', required=True)
    b.add_argument('--font-dir', action='append', default=[])
    b.add_argument('--no-text-check', action='store_true')
    b.add_argument('--litepdf-dir')
    a = ap.parse_args(argv)
    try:
        return cmd_emit(a) if a.cmd == 'emit' else cmd_build(a)
    except rc.Refusal as ex:
        rc.die('replica: %s' % ex)
    except OSError as ex:
        rc.die('replica: %s' % ex)


if __name__ == '__main__':
    sys.exit(main())
