import json
import os
import unittest

from tests.helpers import DataDirCase, commit, run_kb, tempdir
from kblib import check, findings, store

LINES = [f'const line{i} = {i};' for i in range(30)]
LINES[15] = 'if (user.roles) { allow(); }'
CODE = '\n'.join(LINES) + '\n'
CAND = {'kind': 'null-check-misses-undefined', 'root_cause': 'roles can be undefined', 'path': 'src/auth.js',
        'snippet': 'if (user.roles) { allow(); }', 'source': 'hunter:explore'}


class CheckCase(DataDirCase):
    def setUp(self):
        super().setUp()
        commit(self.repo, 'src/auth.js', CODE, 'Add auth')
        self.conn = store.connect(self.repo)
        self.old_round = findings.start_round(self.conn, 'main', 'x', 'on', {}, [])

    def record(self, outcome, path='src/auth.js', **kw):
        [fid] = findings.add_findings(self.conn, self.repo, self.old_round,
                                      [dict(CAND, path=path, outcome=outcome, **kw)])
        return fid

    def new_round(self, memory='on'):
        findings.start_round(self.conn, 'main', 'y', memory, {}, [])

    def check(self, **kw):
        return check.check_candidates(self.conn, self.repo, [dict(CAND, **kw)], findings.memory_on(self.conn))[0]


class GateTest(CheckCase, unittest.TestCase):
    def test_strong_for_dropped_false_positive_unchanged_code(self):
        fid = self.record('dropped:false-positive', reason='roles is always an array here')
        self.new_round()
        r = self.check()
        self.assertEqual(r['gate'], 'strong')
        self.assertEqual(r['match'], fid)
        self.assertIn('roles is always an array here', r['note'])

    def test_strong_for_open_pr(self):
        self.record('shipped')
        self.new_round()
        self.assertEqual(self.check()['gate'], 'strong')

    def test_weak_when_context_changed(self):
        self.record('dropped:false-positive', reason='guarded above')
        lines = list(LINES)
        lines[10] = 'const line10 = changed;'
        commit(self.repo, 'src/auth.js', '\n'.join(lines) + '\n', 'Change guard')
        self.new_round()
        self.assertEqual(self.check()['gate'], 'weak')

    def test_weak_reintroduced_after_merge(self):
        fid = self.record('merged')
        self.new_round()
        r = self.check()
        self.assertEqual(r['gate'], 'weak')
        self.assertIn(f'reintroduced (#{fid})', r['note'])

    def test_weak_sibling_other_path(self):
        commit(self.repo, 'src/other.js', CODE, 'Copy')
        fid = self.record('merged', path='src/other.js')
        self.new_round()
        r = self.check()
        self.assertEqual(r['gate'], 'weak')
        self.assertIn(f'sibling of #{fid}', r['note'])

    def test_weak_related_root_cause_same_file(self):
        fid = self.record('dropped:false-positive', snippet='const line3 = 3;', reason='fine',
                          root_cause='roles undefined for guests')
        self.new_round()
        r = self.check()
        self.assertEqual(r['gate'], 'weak')
        self.assertIn(f'related #{fid}', r['note'])

    def test_superseded_never_strong(self):
        fid = self.record('dropped:false-positive', reason='old lesson')
        findings.update_finding(self.conn, f'#{fid}', superseded_by=fid)
        self.new_round()
        self.assertNotEqual(self.check()['gate'], 'strong')

    def test_seed_never_strong(self):
        self.record('seed')
        self.new_round()
        self.assertNotEqual(self.check()['gate'], 'strong')

    def test_miss_when_memory_off(self):
        self.record('dropped:false-positive', reason='x')
        self.new_round(memory='off')
        self.assertEqual(self.check(), {'gate': 'miss', 'match': None, 'note': ''})

    def test_miss_when_nothing_known(self):
        self.new_round()
        self.assertEqual(self.check()['gate'], 'miss')

    def test_no_crash_when_file_gone(self):
        self.record('dropped:false-positive', reason='x')
        commit(self.repo, 'src/auth.js', None, 'Remove auth')
        self.new_round()
        self.assertIn(self.check()['gate'], ('weak', 'miss'))

    def test_duplicate_within_batch(self):
        self.new_round()
        res = check.check_candidates(self.conn, self.repo, [dict(CAND), dict(CAND)], True)
        self.assertEqual(res[0]['gate'], 'miss')
        self.assertEqual(res[1]['gate'], 'weak')
        self.assertIn('same as candidate 0', res[1]['note'])


class CliTest(CheckCase, unittest.TestCase):
    def test_cli_prints_lines_and_summary(self):
        self.record('dropped:false-positive', reason='x')
        self.new_round()
        path = os.path.join(tempdir(self), 'c.json')
        with open(path, 'w') as f:
            json.dump([CAND, dict(CAND, path='src/none.js', snippet='other()', root_cause='zzz')], f)
        r = run_kb('check', path, cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.strip().split('\n')
        self.assertEqual(json.loads(lines[0])['gate'], 'strong')
        self.assertEqual(lines[-1], '# strong 1, weak 0, reintroduced 0, miss 1')


if __name__ == '__main__':
    unittest.main()
