import json
import os
import unittest

from tests.helpers import DataDirCase, commit, run_kb, tempdir
from kblib import brief, findings, store


def drop(path, reason, outcome='dropped:false-positive'):
    return {'kind': 'missing-await', 'root_cause': 'r', 'path': path, 'snippet': 'x', 'source': 'hunter:explore',
            'outcome': outcome, 'outcome_phase': '1', 'reason': reason}


class BriefTest(DataDirCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        commit(self.repo, 'a.js', 'x\n', 'Add a')
        self.conn = store.connect(self.repo)
        self.cfg = store.load_config(self.repo)
        self.r1 = findings.start_round(self.conn, 'main', 'x', 'on', {}, [])

    def text(self, role='hunter', hot=None):
        return brief.brief(self.conn, self.repo, self.cfg, role, hot)

    def test_brief_lists_round_n_false_positive_in_round_n_plus_1(self):
        findings.add_findings(self.conn, self.repo, self.r1, [drop('a.js', 'logger already wraps bare Errors')])
        findings.start_round(self.conn, 'main', 'y', 'on', {}, [])
        self.assertIn('a.js: logger already wraps bare Errors', self.text())
        self.assertIn('a.js: logger already wraps bare Errors', self.text('prosecutor'))

    def test_closed_pr_reason_included(self):
        findings.add_findings(self.conn, self.repo, self.r1, [dict(drop('a.js', 'We want this.'), outcome='closed')])
        self.assertIn('a.js: a PR for this was closed: We want this.', self.text())

    def test_brief_excludes_superseded(self):
        [fid] = findings.add_findings(self.conn, self.repo, self.r1, [drop('a.js', 'old lesson')])
        brief.supersede(self.conn, f'#{fid}', None)
        self.assertNotIn('old lesson', self.text())

    def test_brief_dedupes(self):
        findings.add_findings(self.conn, self.repo, self.r1, [drop('a.js', 'same')] * 3)
        self.assertEqual(self.text().count('a.js: same'), 1)

    def test_brief_caps_lines_newest_first(self):
        findings.add_findings(self.conn, self.repo, self.r1, [drop(f'f{i}.js', f'lesson {i}') for i in range(60)])
        out = self.text(hot=['a.js', 'b.js'])
        self.assertLessEqual(len(out.splitlines()), 40)
        self.assertIn('lesson 59', out)
        self.assertNotIn('lesson 0\n', out)
        self.assertIn('b.js', out)

    def test_brief_shows_hot_files(self):
        out = self.text(hot=['src/hot.js'])
        self.assertIn('src/hot.js', out)

    def test_brief_empty_when_memory_off(self):
        findings.add_findings(self.conn, self.repo, self.r1, [drop('a.js', 'x')])
        findings.start_round(self.conn, 'main', 'y', 'off', {}, [])
        self.assertEqual(self.text(hot=['a.js']), '')

    def test_brief_has_no_ids(self):
        findings.add_findings(self.conn, self.repo, self.r1, [drop('a.js', 'guarded by getRoles()')])
        self.assertNotIn('#', self.text())

    def test_cli(self):
        findings.add_findings(self.conn, self.repo, self.r1, [drop('a.js', 'lesson')])
        hot = os.path.join(tempdir(self), 'hot.json')
        with open(hot, 'w') as f:
            json.dump([['src/hot.js', 1.0]], f)
        r = run_kb('brief', '--role', 'hunter', '--hot-from', hot, cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('a.js: lesson', r.stdout)
        self.assertIn('src/hot.js', r.stdout)
        r = run_kb('supersede', '#1', cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == '__main__':
    unittest.main()
