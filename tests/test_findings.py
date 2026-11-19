import json
import os
import unittest

from tests.helpers import DataDirCase, commit, git, run_kb, tempdir
from kblib import findings, store

CODE = 'function a() {\n  const x = load();\n  if (x) {\n    save(x);\n  }\n}\n'


def item(**kw):
    base = {'kind': 'missing-await', 'root_cause': 'result used before it resolves', 'path': 'src/a.js',
            'snippet': 'const x = load();', 'source': 'hunter:explore', 'outcome': 'confirmed'}
    base.update(kw)
    return base


class FindingsCase(DataDirCase):
    def setUp(self):
        super().setUp()
        commit(self.repo, 'src/a.js', CODE, 'Add a')
        self.conn = store.connect(self.repo)
        self.round = findings.start_round(self.conn, 'main', 'abc', 'on', {}, ['server', 'ui'])


class HashTest(FindingsCase, unittest.TestCase):
    def test_hash_ignores_whitespace(self):
        self.assertEqual(findings.snippet_hash('a = 1;\r\n\tb()'), findings.snippet_hash('  a = 1;  \n\n    b()'))
        before = findings.context_hash(self.repo, 'src/a.js', 'const x = load();', 20)
        with open(os.path.join(self.repo, 'src/a.js'), 'w') as f:
            f.write(CODE.replace('  ', '\t').replace('\n', '\r\n'))
        self.assertIsNotNone(before)
        self.assertEqual(findings.context_hash(self.repo, 'src/a.js', 'const x = load();', 20), before)

    def test_context_hash_none_when_missing(self):
        self.assertIsNone(findings.context_hash(self.repo, 'src/a.js', 'not there', 20))
        os.remove(os.path.join(self.repo, 'src/a.js'))
        self.assertIsNone(findings.context_hash(self.repo, 'src/a.js', 'const x = load();', 20))


class AddTest(FindingsCase, unittest.TestCase):
    def test_add_validates_kind_and_outcome(self):
        with self.assertRaises(ValueError):
            findings.add_findings(self.conn, self.repo, self.round, [item(kind='foo')])
        findings.add_findings(self.conn, self.repo, self.round, [item(kind='new:double-submit')])
        self.assertEqual(findings.kinds_pending(self.conn), ['double-submit'])
        with self.assertRaises(ValueError):
            findings.add_findings(self.conn, self.repo, self.round, [item(outcome='dropped:bored')])
        with self.assertRaises(ValueError):
            findings.add_findings(self.conn, self.repo, self.round, [{'kind': 'missing-await'}])

    def test_add_stores_hashes_and_base(self):
        [fid] = findings.add_findings(self.conn, self.repo, self.round, [item()])
        row = self.conn.execute('SELECT * FROM findings WHERE id = ?', (fid,)).fetchone()
        self.assertEqual(row['snippet_hash'], findings.snippet_hash('const x = load();'))
        self.assertIsNotNone(row['context_hash'])
        self.assertEqual(row['base_sha'], git(self.repo, 'rev-parse', 'HEAD'))

    def test_resolve_nn_and_id(self):
        a, b = findings.add_findings(self.conn, self.repo, self.round, [item(nn='01'), item(nn='02')])
        self.assertEqual(findings.resolve(self.conn, '02'), b)
        self.assertEqual(findings.resolve(self.conn, '#1'), a)
        with self.assertRaises(LookupError):
            findings.resolve(self.conn, '09')
        with self.assertRaises(LookupError):
            findings.resolve(self.conn, '#99')

    def test_update_fix_diff_from_worktree(self):
        findings.add_findings(self.conn, self.repo, self.round, [item(nn='01')])
        wt = os.path.join(tempdir(self), 'wt')
        git(self.repo, 'worktree', 'add', '-q', '-b', 'fix/a', wt)
        commit(wt, 'src/a.js', CODE.replace('load()', 'await load()'), 'Fix a')
        findings.update_finding(self.conn, '01', outcome='fixed', outcome_phase='2', fix_diff_from=wt)
        row = self.conn.execute('SELECT * FROM findings').fetchone()
        self.assertIn('+  const x = await load();', row['fix_diff'])
        self.assertEqual(row['outcome'], 'fixed')
        with self.assertRaises(ValueError):
            findings.update_finding(self.conn, '01', outcome_phase='9')

    def test_list_groups_and_filters(self):
        findings.add_findings(self.conn, self.repo, self.round, [
            item(nn='01', slug='a-await', verify='ui'),
            item(nn='02', slug='b-typo', kind='visible-typo', verify='none'),
            item(nn='03', slug='c-runner', in_batch=0, verify='ui'),
            item(outcome='dropped:false-positive', reason='guarded'),
        ])
        rows = findings.list_findings(self.conn, self.round, runners_up=False, verify=None)
        self.assertEqual([r['nn'] for r in rows], ['01', '02'])
        rows = findings.list_findings(self.conn, self.round, runners_up=True, verify={'ui'})
        self.assertEqual([r['nn'] for r in rows], ['01', '03'])
        text = findings.format_list(findings.list_findings(self.conn, self.round, True, None))
        self.assertIn('## missing-await', text)
        self.assertIn('## visible-typo', text)
        self.assertIn('## Runners-up', text)
        self.assertIn('01 a-await: src/a.js', text)

    def test_map_kind(self):
        findings.add_findings(self.conn, self.repo, self.round, [item(kind='new:double-submit')])
        findings.map_kind(self.conn, 'double-submit', 'visible-typo')
        self.assertEqual(self.conn.execute('SELECT kind FROM findings').fetchone()[0], 'visible-typo')
        self.assertEqual(findings.kinds_pending(self.conn), [])

    def test_approve_kind(self):
        findings.add_findings(self.conn, self.repo, self.round, [item(kind='new:double-submit')])
        findings.approve_kind(self.conn, 'double-submit')
        self.assertEqual(self.conn.execute('SELECT kind FROM findings').fetchone()[0], 'double-submit')
        self.assertEqual(findings.kinds_pending(self.conn), [])
        findings.add_findings(self.conn, self.repo, self.round, [item(kind='double-submit')])


class CliTest(FindingsCase, unittest.TestCase):
    def test_round_add_list_show(self):
        r = run_kb('round', 'start', '--base', 'main', '--memory', 'on', '--areas', 'server,ui', cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        path = os.path.join(tempdir(self), 'items.json')
        with open(path, 'w') as f:
            json.dump([item(nn='01', slug='a-await', verify='ui')], f)
        r = run_kb('add', path, cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run_kb('list', cwd=self.repo)
        self.assertIn('01 a-await', r.stdout)
        r = run_kb('show', '01', cwd=self.repo)
        self.assertIn('const x = load();', r.stdout)
        r = run_kb('show', '42', cwd=self.repo)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn('Traceback', r.stderr)
        r = run_kb('update', '01', '--outcome', 'dropped:dead-code', '--phase', '3', '--reason', 'no route', cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run_kb('patterns', 'missing-await', cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == '__main__':
    unittest.main()
