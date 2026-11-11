"""PASS / FAIL logic of compare.py on synthetic dumps."""
import copy
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import documents_helpers as helpers  # noqa: E402

cmp_ = helpers.load('compare')
rc = helpers.load('replica_common')

RULE = {'bbox': [309.0, 66.15, 517.5, 66.35], 'stroke': [0, 0, 0], 'fill': None, 'width': 0.1, 'segments': 2}
IMAGE = {'bbox': [136.0, 60.45, 168.1, 127.1], 'name': 'img_p1_1'}


def doc(pitch=13.8, n=2, **kw):
    pages = [helpers.body_page(i, pitch=pitch, **kw) for i in range(1, n + 1)]
    pages[0]['rects'] = [dict(RULE)]
    pages[0]['images'] = [dict(IMAGE)]
    return pages


def rows_for(sample, output, **kw):
    pairs = cmp_.parse_pages_map(kw.pop('pages_map', None), len(sample), len(output))
    return cmp_.compare_docs({}, sample, {}, output, pairs, **kw)


def statuses(rows, check):
    return [r['status'] for r in rows if r['check'].startswith(check)]


def fails(rows):
    return [(r['check'], r['page']) for r in rows if r['status'] == 'FAIL']


class CompareLogicTests(unittest.TestCase):
    def test_identical_structure_passes(self):
        rows = rows_for(doc(), doc())
        self.assertEqual(fails(rows), [])
        self.assertIn('PASS', statuses(rows, 'body pitch'))

    def test_pitch_off_by_more_than_tol_fails(self):
        rows = rows_for(doc(13.8), doc(13.5))
        self.assertIn('FAIL', statuses(rows, 'body pitch'))

    def test_pitch_within_tol_passes(self):
        self.assertEqual(fails(rows_for(doc(13.8), doc(13.84))), [])

    def test_pitch_from_one_diff_is_info_not_fail(self):
        s, o = doc(16.6, lines=2), doc(16.5)
        rows = rows_for(s, o)
        self.assertEqual(statuses(rows, 'body pitch'), ['INFO', 'INFO'])
        self.assertEqual(statuses(rows_for(s, o, min_pitch_n=1), 'body pitch'), ['FAIL', 'FAIL'])

    def test_rounded_16_5_16_6_mix_matches_a_true_16_55(self):
        s = doc(16.55)
        for p in s:                      # the sample's producer rounded to 0.1 pt, half-down
            for r in p['runs']:
                if r['baseline'] < 700:
                    r['baseline'] = round(r['baseline'] - 0.0005, 1) + 0.001
        rows = rows_for(s, doc(16.55))
        self.assertIn('PASS', statuses(rows, 'body pitch'))
        self.assertEqual([r for r in fails(rows) if 'pitch' in r[0]], [])

    def test_page_box_must_be_exact(self):
        o = doc()
        o[1]['width'] = 595.304 + 0.002
        o[1]['mediabox'][2] = 595.304 + 0.002
        rows = rows_for(doc(), o)
        self.assertEqual(statuses(rows, 'page width'), ['PASS', 'FAIL'])
        self.assertEqual(statuses(rows, 'MediaBox'), ['PASS', 'FAIL'])

    def test_missing_page_number_fails(self):
        rows = rows_for(doc(), doc(pn=False))
        self.assertEqual(statuses(rows, 'page number'), ['FAIL', 'FAIL'])

    def test_page_number_moved_fails(self):
        o = doc()
        for r in o[0]['runs']:
            if r['text'] == '1':
                r['baseline'] += 0.06
        self.assertIn(('page number baseline', '1->1'), fails(rows_for(doc(), o)))

    def test_rect_and_image_matching(self):
        o = doc()
        o[0]['rects'][0]['bbox'] = [309.04, 66.15, 517.5, 66.35]
        self.assertEqual(fails(rows_for(doc(), o)), [])
        o[0]['rects'][0]['bbox'] = [309.1, 66.15, 517.5, 66.35]
        self.assertIn(('rect 1 bbox', '1->1'), fails(rows_for(doc(), o)))
        o = doc()
        o[0]['rects'] = []
        self.assertIn(('rect 1', '1->1'), fails(rows_for(doc(), o)))
        o = doc()
        o[0]['images'].append({'bbox': [0, 0, 10, 10]})
        self.assertIn(('image extra', '1->1'), fails(rows_for(doc(), o)))

    def test_margin_and_first_baseline(self):
        o = doc(margin=57.0)
        self.assertIn(('left margin', '1->1'), fails(rows_for(doc(), o)))
        o = doc(first=72.0)
        self.assertIn(('first baseline', '1->1'), fails(rows_for(doc(), o)))

    def test_right_edge_uses_its_own_tolerance(self):
        self.assertEqual(fails(rows_for(doc(right=538.5), doc(right=538.0))), [])
        self.assertIn(('right edge', '1->1'), fails(rows_for(doc(right=538.5), doc(right=537.5))))

    def test_extra_output_font_fails(self):
        o = doc()
        o[0]['runs'][1]['font'] = 'ABCDEF+TimesNewRomanPSMT'
        self.assertIn(('fonts', ''), fails(rows_for(doc(), o)))
        o = doc()
        o[0]['runs'][1]['font'] = 'XYZABC+LiberationSerif'     # a subset tag alone is not a new font
        self.assertNotIn(('fonts', ''), fails(rows_for(doc(), o)))

    def test_forbid_and_require(self):
        o = doc()
        o[0]['runs'][1]['text'] = 'a – b ő'
        rows = rows_for(doc(), o, forbid=['–', '—'], require=['ő', 'ű'])
        self.assertEqual(statuses(rows, 'forbid'), ['FAIL', 'PASS'])
        self.assertEqual(statuses(rows, 'require'), ['PASS', 'FAIL'])

    def test_pages_map(self):
        self.assertEqual(cmp_.parse_pages_map(None, 5, 3), [(1, 1), (2, 2), (3, 3)])
        self.assertEqual(cmp_.parse_pages_map('1:1,3:2,3:4', 5, 4), [(1, 1), (3, 2), (3, 4)])
        for bad in ('6:1', '1:9', 'x', '1-2', ','):
            with self.assertRaises(rc.Refusal):
                cmp_.parse_pages_map(bad, 5, 4)

    def test_parse_chars(self):
        self.assertEqual(cmp_.parse_chars('U+2013,U+2014', '--forbid'), ['–', '—'])
        self.assertEqual(cmp_.parse_chars('őű', '--require'), ['ő', 'ű'])
        with self.assertRaises(rc.Refusal):
            cmp_.parse_chars('U+ZZ', '--forbid')


class CompareCliTests(unittest.TestCase):
    def cli(self, sample, output, *extra):
        d = helpers.tmpdir(self)
        sp, op = os.path.join(d, 's.jsonl'), os.path.join(d, 'o.jsonl')
        helpers.write_jsonl(sp, sample)
        helpers.write_jsonl(op, output)
        return subprocess.run([sys.executable, '-I', '-B', os.path.join(helpers.SCRIPTS, 'compare.py'), sp, op]
                              + list(extra), capture_output=True, text=True)

    def test_exit_0_on_pass(self):
        res = self.cli(doc(), doc())
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn('| PASS |', res.stdout)

    def test_exit_1_on_fail(self):
        res = self.cli(doc(13.8), doc(13.5))
        self.assertEqual(res.returncode, 1)
        self.assertIn('| FAIL |', res.stdout)
        self.assertEqual(res.stderr.count('\n'), 1)

    def test_rotation_is_refused(self):
        o = copy.deepcopy(doc())
        o[1]['rotation'] = 90
        res = self.cli(doc(), o)
        self.assertEqual(res.returncode, 2)
        self.assertEqual(len(res.stderr.strip().splitlines()), 1)
        self.assertIn('page 2 has rotation 90', res.stderr)
        self.assertNotIn('Traceback', res.stderr)

    def test_json_output(self):
        import json
        res = self.cli(doc(), doc(), '--json')
        out = json.loads(res.stdout)
        self.assertEqual(out['fail'], 0)
        self.assertTrue(out['rows'])

    def test_bad_pages_map_exit_2(self):
        res = self.cli(doc(), doc(), '--pages-map', '9:1')
        self.assertEqual(res.returncode, 2)
        self.assertNotIn('Traceback', res.stderr)


if __name__ == '__main__':
    unittest.main()
