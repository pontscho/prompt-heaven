"""replica.py (the litepdf_typst wrapper) and the package-wide liteparser lookup of replica_common.

Everything but the last class runs against a STUB liteparser package written under a mkdtemp:
five files named like the real ones, the litepdf_typst stub raising or returning on cue.  The
last class is the integration emit -> build -> compare against the real package; it is
SKIPPED (recorded as INFO) without $LITEPDF_DIR or the Liberation Serif TTFs.
"""
import os
import shutil
import stat
import struct
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import documents_helpers as helpers  # noqa: E402

rc = helpers.load('replica_common')
rp = helpers.load('replica')

STUB_TYPST = '''
class TypstSubsetError(Exception):
    def __init__(self, msg, line=1, col=1, construct=None, hint=None):
        s = '%d:%d: ' % (line, col) + (construct + ': ' if construct else '') + msg
        Exception.__init__(self, s + (' -- use: ' + hint if hint else ''))


class TypstEmitError(Exception):
    pass


MODE = @MODE@


def emit(pages, fonts=None, **kw):
    if MODE == 'emit-error':
        raise TypstEmitError('page 1 has rotation 90')
    if kw.get('report') is not None:
        kw['report'].append({'page': 1, 'decision': 'flow', 'why': '', 'paragraphs': 2, 'headings': 1})
    return '// litepdf-typst 1 stub\\n'


def build(source, fonts_map, out_pdf, base_dir=None):
    if MODE == 'subset-error':
        raise TypstSubsetError('boom', 3, 7, '#let', 'plain text')
    with open(out_pdf, 'wb') as fh:
        fh.write(b'%PDF-stub')
    return [{'runs': [{'text': 'alpha beta', 'x': 10, 'baseline': 20},
                      {'text': ' gamma', 'x': 80, 'baseline': 20}]}]
'''


def fake_ttf(psname):
    """The smallest byte string ttf_psname() reads: an sfnt header and a one-record name table."""
    s = psname.encode('utf-16-be')
    nm = struct.pack('>HHH', 0, 1, 18) + struct.pack('>6H', 3, 1, 0x409, 6, len(s), 0) + s
    return (struct.pack('>IHHHH', 0x10000, 1, 16, 0, 0)
            + struct.pack('>4sIII', b'name', 0, 28, len(nm)) + nm)


def stub_package(test, omit=None, mode='ok'):
    d = helpers.tmpdir(test)
    for n in rc.LITEPDF_PACKAGE:
        if n == omit:
            continue
        with open(os.path.join(d, n), 'w', encoding='utf-8') as fh:
            fh.write(STUB_TYPST.replace('@MODE@', repr(mode)) if n == 'litepdf_typst.py' else '')
    return d


def font_dir(test, *psnames):
    d = helpers.tmpdir(test)
    for ps in psnames or ('FakeSerif-Regular',):
        with open(os.path.join(d, ps + '.ttf'), 'wb') as fh:
            fh.write(fake_ttf(ps))
    return d


def cli(*args, env=None, drop_litepdf_dir=True):
    if env is None:
        env = {k: v for k, v in os.environ.items() if k != 'LITEPDF_DIR' or not drop_litepdf_dir}
    return subprocess.run([sys.executable, '-I', '-B', os.path.join(helpers.SCRIPTS, 'replica.py')]
                          + list(args), capture_output=True, text=True, env=env)


def one_line(test, res):
    test.assertEqual(len(res.stderr.strip().splitlines()), 1, res.stderr)
    test.assertNotIn('Traceback', res.stderr)


def write(path, text):
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)


class FontMapTests(unittest.TestCase):
    def test_family_and_variant_from_the_postscript_name(self):
        self.assertEqual(rp.split_name('LiberationSerif-BoldItalic'), ('LiberationSerif', 'bolditalic'))
        self.assertEqual(rp.split_name('LiberationSerif-Bold'), ('LiberationSerif', 'bold'))
        self.assertEqual(rp.split_name('Foo-Oblique'), ('Foo', 'italic'))
        self.assertEqual(rp.split_name('LiberationSerif'), ('LiberationSerif', 'regular'))
        self.assertEqual(rp.split_name('TimesNewRomanPS-BoldMT'), ('TimesNewRomanPS', 'bold'))

    def test_fonts_json_from_font_dir(self):
        d = font_dir(self, 'FakeSerif-Regular', 'FakeSerif-Bold', 'Other')
        fonts = rp.fonts_from_dirs([d])
        self.assertEqual(sorted(fonts), ['FakeSerif', 'Other'])
        self.assertEqual(sorted(fonts['FakeSerif']), ['bold', 'regular'])
        self.assertTrue(fonts['FakeSerif']['bold'].endswith('FakeSerif-Bold.ttf'))

    def test_dir_without_ttf_refused(self):
        with self.assertRaises(rc.Refusal):
            rp.fonts_from_dirs([helpers.tmpdir(self)])

    def test_used_font_without_its_variant_refused(self):
        fonts = {'FakeSerif': {'regular': '/x/a.ttf'}}
        rp.check_fonts(['FakeSerif'], fonts)
        with self.assertRaises(rc.Refusal) as cm:
            rp.check_fonts(['FakeSerif-Bold'], fonts)
        self.assertIn('FakeSerif-Bold', str(cm.exception))

    def test_expected_words_join_runs_of_one_baseline(self):
        pages = [{'runs': [{'text': 'ab ', 'x': 0, 'baseline': 5}, {'text': 'cd', 'x': 20, 'baseline': 5},
                           {'text': 'ef', 'x': 0, 'baseline': 9}]}]
        self.assertEqual(rp.expected_words(pages), {'ab', 'cd', 'ef'})


class PackageLookupTests(unittest.TestCase):
    def test_every_package_module_is_required(self):
        self.assertEqual(rc.LITEPDF_PACKAGE, ('litepdf.py', 'litepdf_write.py', 'litepdf_flow.py',
                                              'litepdf_hyph.py', 'litepdf_typst.py'))

    def test_package_dir_goes_on_sys_path(self):
        d = stub_package(self)
        with mock.patch.object(sys, 'path', list(sys.path)):
            self.assertEqual(rc.use_litepdf_package(d), os.path.abspath(d))
            self.assertEqual(sys.path[0], os.path.abspath(d))

    def test_missing_module_is_named_in_the_refusal(self):
        for n in rc.LITEPDF_PACKAGE:
            with self.subTest(missing=n):
                d = stub_package(self, omit=n)
                with self.assertRaises(rc.Refusal) as cm:
                    rc.use_litepdf_package(d)
                self.assertIn('--litepdf-dir %s has no %s;' % (os.path.abspath(d), n), str(cm.exception))

    def test_single_module_lookup_is_unchanged(self):
        d = helpers.tmpdir(self)
        write(os.path.join(d, 'litepdf.py'), '')
        self.assertEqual(rc.find_litepdf_dir(d, 'litepdf.py'), os.path.abspath(d))

    def test_cli_lookup_refuses_each_missing_module_exit_2_one_line(self):
        fd = font_dir(self)
        work = helpers.tmpdir(self)
        src = os.path.join(work, 'report.typ')
        write(src, 'x\n')
        for n in rc.LITEPDF_PACKAGE:
            with self.subTest(missing=n):
                pkg = stub_package(self, omit=n)
                res = cli('build', src, '-o', os.path.join(work, 'report.pdf'), '--font-dir', fd,
                          '--litepdf-dir', pkg)
                self.assertEqual(res.returncode, 2, res.stderr)
                one_line(self, res)
                self.assertIn('has no %s;' % n, res.stderr)
                self.assertIn('There is no fallback.', res.stderr)
                self.assertFalse(os.path.exists(os.path.join(work, 'report.pdf')))

    def test_cli_without_litepdf_dir_exit_2_with_the_hint(self):
        fd = font_dir(self)
        work = helpers.tmpdir(self)
        write(os.path.join(work, 'report.typ'), 'x\n')
        res = cli('build', os.path.join(work, 'report.typ'), '-o', os.path.join(work, 'report.pdf'),
                  '--font-dir', fd)
        self.assertEqual(res.returncode, 2)
        one_line(self, res)
        self.assertIn('--litepdf-dir (not given)', res.stderr)
        self.assertIn('$LITEPDF_DIR (not set)', res.stderr)

    def test_cli_env_dir_is_used_and_checked(self):
        fd = font_dir(self)
        work = helpers.tmpdir(self)
        write(os.path.join(work, 'report.typ'), 'x\n')
        pkg = stub_package(self, omit='litepdf_hyph.py')
        res = cli('build', os.path.join(work, 'report.typ'), '-o', os.path.join(work, 'report.pdf'),
                  '--font-dir', fd, env=dict(os.environ, LITEPDF_DIR=pkg))
        self.assertEqual(res.returncode, 2)
        self.assertIn('$LITEPDF_DIR=%s has no litepdf_hyph.py' % os.path.abspath(pkg), res.stderr)


class WrapperCliTests(unittest.TestCase):
    def setUp(self):
        self.pkg = stub_package(self)
        self.fonts = font_dir(self)
        self.work = helpers.tmpdir(self)

    def build(self, *extra, name='report', pkg=None):
        src = os.path.join(self.work, name + '.typ')
        if not os.path.exists(src):
            write(src, 'hello\n')
        return cli('build', src, '-o', os.path.join(self.work, name + '.pdf'), '--font-dir', self.fonts,
                   '--litepdf-dir', pkg or self.pkg, *extra)

    def test_no_subcommand_exit_2(self):
        res = cli()
        self.assertEqual(res.returncode, 2)

    def test_emit_output_must_end_in_typ(self):
        d = os.path.join(self.work, 'd.jsonl')
        helpers.write_jsonl(d, [helpers.page(1, [helpers.run('hi', 10, 20, font='FakeSerif')])])
        res = cli('emit', d, '-o', os.path.join(self.work, 'report.txt'), '--font-dir', self.fonts,
                  '--litepdf-dir', self.pkg)
        self.assertEqual(res.returncode, 2)
        one_line(self, res)
        self.assertIn('.typ', res.stderr)

    def test_build_outputs_must_share_a_stem(self):
        src = os.path.join(self.work, 'report.typ')
        write(src, 'x\n')
        res = cli('build', src, '-o', os.path.join(self.work, 'other.pdf'), '--font-dir', self.fonts,
                  '--litepdf-dir', self.pkg)
        self.assertEqual(res.returncode, 2)
        one_line(self, res)
        self.assertIn('share a stem', res.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.work, 'other.pdf')))

    def test_font_dir_is_required_and_must_exist(self):
        src = os.path.join(self.work, 'report.typ')
        write(src, 'x\n')
        base = ['build', src, '-o', os.path.join(self.work, 'report.pdf'), '--litepdf-dir', self.pkg]
        res = cli(*base)
        self.assertEqual(res.returncode, 2)
        one_line(self, res)
        self.assertIn('--font-dir', res.stderr)
        res = cli(*(base + ['--font-dir', os.path.join(self.work, 'nope')]))
        self.assertEqual(res.returncode, 2)
        one_line(self, res)
        self.assertIn('is not a directory', res.stderr)

    def test_missing_source_exit_2(self):
        res = cli('build', os.path.join(self.work, 'nope.typ'), '-o', os.path.join(self.work, 'nope.pdf'),
                  '--font-dir', self.fonts, '--litepdf-dir', self.pkg)
        self.assertEqual(res.returncode, 2)
        one_line(self, res)
        self.assertIn('no such file', res.stderr)

    def test_subset_error_is_one_line_with_line_col_and_hint(self):
        pkg = stub_package(self, mode='subset-error')
        res = self.build('--no-text-check', pkg=pkg)
        self.assertEqual(res.returncode, 2, res.stdout)
        one_line(self, res)
        self.assertIn('report.typ:3:7: #let: boom -- use: plain text', res.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.work, 'report.pdf')))

    def test_emit_error_is_one_line_exit_2(self):
        pkg = stub_package(self, mode='emit-error')
        d = os.path.join(self.work, 'd.jsonl')
        helpers.write_jsonl(d, [helpers.page(1, [helpers.run('hi', 10, 20, font='FakeSerif')])])
        res = cli('emit', d, '-o', os.path.join(self.work, 'report.typ'), '--font-dir', self.fonts,
                  '--litepdf-dir', pkg)
        self.assertEqual(res.returncode, 2)
        one_line(self, res)
        self.assertIn('emit refused: page 1 has rotation 90', res.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.work, 'report.typ')))

    def test_emit_dump_font_without_a_ttf_is_refused(self):
        d = os.path.join(self.work, 'd.jsonl')
        helpers.write_jsonl(d, [helpers.page(1, [helpers.run('hi', 10, 20, font='Ghost')])])
        res = cli('emit', d, '-o', os.path.join(self.work, 'report.typ'), '--font-dir', self.fonts,
                  '--litepdf-dir', self.pkg)
        self.assertEqual(res.returncode, 2)
        one_line(self, res)
        self.assertIn('no TTF for dump font Ghost', res.stderr)

    def test_emit_then_build_share_the_stem(self):
        d = os.path.join(self.work, 'd.jsonl')
        helpers.write_jsonl(d, [helpers.page(1, [helpers.run('hi', 10, 20, font='FakeSerif')])])
        out = os.path.join(self.work, 'report.typ')
        res = cli('emit', d, '-o', out, '--font-dir', self.fonts, '--litepdf-dir', self.pkg)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn('| 1 | flow | 2 | 1 |', res.stdout)
        self.assertTrue(os.path.isfile(out))
        res = self.build('--no-text-check')
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.work, 'report.pdf')))
        self.assertIn('INFO: text check skipped (--no-text-check)', res.stdout)

    def test_text_check_is_info_when_pdftotext_is_absent(self):
        write(os.path.join(self.work, 'report.typ'), 'x\n')
        res = cli('build', os.path.join(self.work, 'report.typ'), '-o', os.path.join(self.work, 'report.pdf'),
                  '--font-dir', self.fonts, '--litepdf-dir', self.pkg,
                  env={'PATH': os.path.join(self.work, 'no-such-bin')})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn('INFO: text check skipped (pdftotext not on PATH)', res.stdout)

    def _fake_pdftotext(self, output):
        bindir = os.path.join(self.work, 'bin')
        exe = os.path.join(bindir, 'pdftotext')
        os.makedirs(bindir)
        write(exe, '#!/bin/sh\necho "%s"\n' % output)
        os.chmod(exe, os.stat(exe).st_mode | stat.S_IXUSR)
        write(os.path.join(self.work, 'report.typ'), 'x\n')
        return cli('build', os.path.join(self.work, 'report.typ'), '-o', os.path.join(self.work, 'report.pdf'),
                   '--font-dir', self.fonts, '--litepdf-dir', self.pkg, env={'PATH': bindir})

    def test_text_check_pass(self):
        res = self._fake_pdftotext('alpha beta gamma')
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn('text check: PASS (3 words)', res.stdout)

    def test_text_check_failure_exits_1(self):
        res = self._fake_pdftotext('alpha beta')
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn('missing from pdftotext output: gamma', res.stdout)
        self.assertEqual(len(res.stderr.strip().splitlines()), 1)


# The Liberation TTFs of a LibreOffice install (macOS example); override with the env var.
FONT_DIR = os.environ.get('DOCUMENTS_FONT_DIR',
                          '/Applications/LibreOffice.app/Contents/Resources/fonts/truetype')
LIBERATION = ('LiberationSerif-Regular.ttf', 'LiberationSerif-Bold.ttf',
              'LiberationSerif-Italic.ttf', 'LiberationSerif-BoldItalic.ttf')

SAMPLE_TYP = '''#set page(width: 595.304pt, height: 841.89pt, margin: 56.8pt)
#set text(font: "Liberation Serif", size: 12pt, lang: "hu", hyphenate: false)
#set par(leading: 4.5pt, spacing: 10pt, justify: true)

Az ingatlan-nyilvántartásról szóló törvény módosítása a jelen szerződés tárgyát nem érinti, \
ezért az alábbi bekezdés csak a hely kitöltésére szolgál, hogy a sorok kijöjjenek.

Második bekezdés, rövidebb.
'''


def _have_liteparser():
    d = os.environ.get('LITEPDF_DIR', '')
    return bool(d) and all(os.path.isfile(os.path.join(d, n)) for n in rc.LITEPDF_PACKAGE)


@unittest.skipUnless(_have_liteparser() and all(os.path.isfile(os.path.join(FONT_DIR, n)) for n in LIBERATION),
                     'needs $LITEPDF_DIR (the whole liteparser package) and the Liberation Serif TTFs')
class IntegrationTests(unittest.TestCase):
    """Build a hand-written sample, emit it, build the emitted source, compare the two PDFs."""

    def test_emit_build_compare(self):
        fonts = helpers.tmpdir(self)
        for n in LIBERATION:
            shutil.copy2(os.path.join(FONT_DIR, n), fonts)
        a, b = helpers.tmpdir(self), helpers.tmpdir(self)
        write(os.path.join(a, 'sample.typ'), SAMPLE_TYP)
        res = cli('build', os.path.join(a, 'sample.typ'), '-o', os.path.join(a, 'sample.pdf'),
                  '--font-dir', fonts, env=dict(os.environ))
        self.assertEqual(res.returncode, 0, res.stderr)
        res = cli('emit', os.path.join(a, 'sample.pdf'), '-o', os.path.join(b, 'report.typ'),
                  '--font-dir', fonts, env=dict(os.environ))
        if res.returncode == 2 and 'could not dump' in res.stderr:
            self.skipTest('litepdf cannot dump a PDF on this host: %s' % res.stderr.strip())
        self.assertEqual(res.returncode, 0, res.stderr)
        res = cli('build', os.path.join(b, 'report.typ'), '-o', os.path.join(b, 'report.pdf'),
                  '--font-dir', fonts, env=dict(os.environ))
        self.assertEqual(res.returncode, 0, res.stderr)
        res = subprocess.run([sys.executable, '-I', '-B', os.path.join(helpers.SCRIPTS, 'compare.py'),
                              os.path.join(a, 'sample.pdf'), os.path.join(b, 'report.pdf')],
                             capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)


if __name__ == '__main__':
    unittest.main()
