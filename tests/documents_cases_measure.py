"""Page analysis, the measure report and the liteparser lookup (measure.py, replica_common.py)."""
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import documents_helpers as helpers  # noqa: E402

rc = helpers.load('replica_common')
ms = helpers.load('measure')


class AnalyseTests(unittest.TestCase):
    def test_body_page_measurements(self):
        a = rc.analyse_page(helpers.body_page(1))
        self.assertEqual(a['margin'], 56.8)
        self.assertAlmostEqual(a['right_edge'], 538.5, places=6)
        self.assertAlmostEqual(a['centre_axis'], 297.65, places=6)
        self.assertAlmostEqual(a['body_pitch'], 13.8, places=6)
        self.assertEqual(a['first_baseline'], 71.801)
        pn = a['page_number']
        self.assertEqual((pn['text'], pn['x'], pn['baseline']), ('1', 291.8, 782.601))
        self.assertAlmostEqual(pn['x_center'], 294.8, places=6)

    def test_pitch_is_the_modal_cluster_mean(self):
        mode, pitch, _hist, n = rc.modal_cluster([16.5, 16.6, 16.5, 16.6, 33.1, 16.5, 16.6])
        self.assertEqual(mode, 16.5)
        self.assertAlmostEqual(pitch, 16.55, places=6)
        self.assertEqual(n, 6)
        self.assertEqual(rc.modal_cluster([]), (None, None, [], 0))

    def test_trailing_spaces_excluded_from_the_right_edge(self):
        r = helpers.run('ab  ', 10.0, 100.0, cw=6.0)
        self.assertEqual(rc._content_right(r), 34.0 - 2 * 3.0)       # 0.25 em per space without advances
        r['advance'] = [0.0, 6.0, 12.0, 15.0]
        self.assertEqual(rc._content_right(r), 22.0)                 # exact with advances

    def test_rect_geometry_undoes_the_stroke_inflation(self):
        self.assertEqual(rc.rect_geometry({'bbox': [309.0, 66.15, 517.5, 66.35], 'stroke': [0, 0, 0],
                                           'width': 0.1}), [309.1, 66.25, 517.4, 66.25])
        self.assertEqual(rc.rect_geometry({'bbox': [1, 2, 3, 4], 'fill': [0, 0, 0], 'width': 1}), [1, 2, 3, 4])

    def test_rotation_refused(self):
        lines = [json.dumps({'convention': 'x'}), json.dumps(helpers.page(3, [], rotation=180))]
        with self.assertRaises(rc.Refusal) as cm:
            rc.parse_jsonl(lines, 'd.jsonl')
        self.assertIn('page 3 has rotation 180', str(cm.exception))

    def test_subset_tag_stripped(self):
        self.assertEqual(rc.strip_tag('ABCDEF+LiberationSerif-Bold'), 'LiberationSerif-Bold')
        self.assertEqual(rc.strip_tag('LiberationSerif'), 'LiberationSerif')


class LookupTests(unittest.TestCase):
    def test_flag_dir_without_module_is_refused(self):
        d = helpers.tmpdir(self)
        with mock.patch.dict(os.environ, {'LITEPDF_DIR': d}):
            with self.assertRaises(rc.Refusal) as cm:
                rc.find_litepdf_dir(d, 'litepdf.py')
        self.assertIn('no fallback', str(cm.exception))

    def test_env_dir_without_module_is_refused(self):
        d = helpers.tmpdir(self)
        with mock.patch.dict(os.environ, {'LITEPDF_DIR': d}):
            with self.assertRaises(rc.Refusal) as cm:
                rc.find_litepdf_dir(None, 'litepdf.py')
        self.assertIn('$LITEPDF_DIR=%s has no litepdf.py' % d, str(cm.exception))

    def test_flag_wins_over_env(self):
        d = helpers.tmpdir(self)
        open(os.path.join(d, 'litepdf.py'), 'w').close()
        with mock.patch.dict(os.environ, {'LITEPDF_DIR': '/nonexistent'}):
            self.assertEqual(rc.find_litepdf_dir(d, 'litepdf.py'), os.path.abspath(d))

    def test_several_modules_name_the_first_missing_one(self):
        d = helpers.tmpdir(self)
        open(os.path.join(d, 'litepdf.py'), 'w').close()
        with self.assertRaises(rc.Refusal) as cm:
            rc.find_litepdf_dir(d, rc.LITEPDF_PACKAGE)
        self.assertIn('--litepdf-dir %s has no litepdf_write.py; refused.' % os.path.abspath(d),
                      str(cm.exception))

    def test_nothing_given(self):
        env = {k: v for k, v in os.environ.items() if k != 'LITEPDF_DIR'}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(rc.Refusal) as cm:
                rc.find_litepdf_dir(None, 'litepdf.py')
        self.assertTrue(str(cm.exception).endswith(
            'Pass --litepdf-dir DIR (or set $LITEPDF_DIR). There is no fallback.'))


class MeasureCliTests(unittest.TestCase):
    def cli(self, *args, env=None):
        return subprocess.run([sys.executable, '-I', '-B', os.path.join(helpers.SCRIPTS, 'measure.py')] + list(args),
                              capture_output=True, text=True, env=env)

    def test_pdf_without_out_dir_exit_2(self):
        d = helpers.tmpdir(self)
        pdf = os.path.join(d, 's.pdf')
        with open(pdf, 'wb') as fh:
            fh.write(b'%PDF-1.4\n')
        res = self.cli(pdf)
        self.assertEqual(res.returncode, 2)
        self.assertEqual(len(res.stderr.strip().splitlines()), 1)
        self.assertIn('--out-dir', res.stderr)

    def test_jsonl_report_has_no_skeleton(self):
        d = helpers.tmpdir(self)
        p = os.path.join(d, 'd.jsonl')
        helpers.write_jsonl(p, [helpers.body_page(1), helpers.body_page(2)])
        res = self.cli(p)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn('## Page 2', res.stdout)
        self.assertIn('| 56.8 |', res.stdout)
        self.assertNotIn('skeleton', res.stdout.lower())
        out = json.loads(self.cli(p, '--json').stdout)
        self.assertEqual(sorted(out), ['dump', 'fonts', 'pages'])
        self.assertEqual(out['pages'][0]['margin'], 56.8)
        self.assertEqual(os.listdir(d), ['d.jsonl'])       # nothing else was written

    def test_skeleton_out_flag_is_gone(self):
        d = helpers.tmpdir(self)
        p = os.path.join(d, 'd.jsonl')
        helpers.write_jsonl(p, [helpers.body_page(1)])
        res = self.cli(p, '--skeleton-out', os.path.join(d, 'sk.json'))
        self.assertEqual(res.returncode, 2)
        self.assertFalse(os.path.exists(os.path.join(d, 'sk.json')))

    def test_rotated_dump_exit_2(self):
        d = helpers.tmpdir(self)
        p = os.path.join(d, 'd.jsonl')
        helpers.write_jsonl(p, [helpers.page(1, [], rotation=90)])
        res = self.cli(p)
        self.assertEqual(res.returncode, 2)
        self.assertIn('page 1 has rotation 90', res.stderr)
        self.assertNotIn('Traceback', res.stderr)


if __name__ == '__main__':
    unittest.main()
