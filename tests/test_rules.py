import json
import os
import shutil
import unittest

from tests.helpers import DataDirCase, commit, git, tempdir
from kblib import findings, rules, store

BROKEN = '<template name="users">\n<label for="basic-url">Role</label>\n<select id="role"></select>\n</template>\n'
FIXED = BROKEN.replace('for="basic-url"', 'for="role"')
OTHER = '<template name="x">\n<label for="nowhere">X</label>\n</template>\n'
PATTERN = 'label-for-mismatch/for-points-nowhere'


def rule(**kw):
    r = {'id': 'label-for-points-nowhere', 'engine': 'html-regex', 'kind': 'label-for-mismatch', 'pattern': PATTERN,
         'message': '<label for> names an id that is not in the template', 'glob': '**/*.html',
         'regex': r'<label[^>]*\bfor="(?P<ref>[^"]+)"', 'unless': 'id="{ref}"',
         'must_match': '<label for="a">A</label>', 'must_not_match': '<label for="a">A</label><input id="a">',
         'sources': []}
    r.update(kw)
    return r


class RulesCase(DataDirCase):
    def setUp(self):
        super().setUp()
        self.old = commit(self.repo, 'imports/ui/users.html', BROKEN, 'Add users')
        commit(self.repo, 'imports/ui/x.html', OTHER, 'Add x')
        self.conn = store.connect(self.repo)
        self.cfg = store.load_config(self.repo)
        self.scratch = tempdir(self)
        self.rid = findings.start_round(self.conn, 'main', 'x', 'on', {}, [])
        self.sources = findings.add_findings(self.conn, self.repo, self.rid, [
            {'kind': 'label-for-mismatch', 'pattern': PATTERN, 'root_cause': 'label points at a missing id',
             'path': p, 'snippet': s, 'source': 'hunter:explore', 'outcome': 'merged'}
            for p, s in (('imports/ui/users.html', '<label for="basic-url">Role</label>'),
                         ('imports/ui/x.html', '<label for="nowhere">X</label>'))])

    def write(self, r):
        path = os.path.join(self.scratch, 'draft.json')
        with open(path, 'w') as f:
            json.dump(r, f)
        return path


class CandidateTest(RulesCase, unittest.TestCase):
    def test_candidates_need_two_merged(self):
        [c] = rules.candidates(self.conn, self.cfg)
        self.assertEqual((c['kind'], c['pattern'], c['merged']), ('label-for-mismatch', PATTERN, 2))
        self.assertEqual(len(c['sources']), 2)
        rules.add_rule(self.conn, self.repo, self.write(rule(sources=self.sources)), self.scratch)
        self.assertEqual(rules.candidates(self.conn, self.cfg), [])


class RuleTest(RulesCase, unittest.TestCase):
    def test_html_regex_rule_finds_source_on_old_commit(self):
        rules.add_rule(self.conn, self.repo, self.write(rule(sources=self.sources)), self.scratch)
        self.assertTrue((store.data_dir(self.repo) / 'rules' / 'label-for-points-nowhere.json').exists())
        commit(self.repo, 'imports/ui/users.html', FIXED, 'Fix users label')
        commit(self.repo, 'imports/ui/x.html', OTHER.replace('nowhere', 'x') + '<input id="x">\n', 'Fix x')
        self.assertEqual(rules.run_rules(self.conn, self.repo, self.repo, self.scratch), [])
        old = os.path.join(tempdir(self), 'old')
        git(self.repo, 'worktree', 'add', '-q', '--detach', old, self.old)
        hits = rules.run_rules(self.conn, self.repo, old, self.scratch)
        self.assertEqual([h['path'] for h in hits], ['imports/ui/users.html'])
        self.assertIn('for="basic-url"', hits[0]['snippet'])

    def test_non_ascii_path(self):
        commit(self.repo, 'imports/ui/my dir/página.html', OTHER, 'Add página')
        rules.add_rule(self.conn, self.repo, self.write(rule(sources=self.sources)), self.scratch)
        paths = [h['path'] for h in rules.run_rules(self.conn, self.repo, self.repo, self.scratch)]
        self.assertIn('imports/ui/my dir/página.html', paths)

    def test_rule_refused_when_examples_fail(self):
        bad = rule(must_not_match='<label for="a">A</label>', sources=self.sources)
        with self.assertRaises(store.KbError) as e:
            rules.add_rule(self.conn, self.repo, self.write(bad), self.scratch)
        self.assertIn('must_not_match', str(e.exception))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM rules').fetchone()[0], 0)

    def test_rule_refused_when_source_not_matched(self):
        bad = rule(regex=r'<label[^>]*\bfor="(?P<ref>a)"', sources=self.sources)
        with self.assertRaises(store.KbError) as e:
            rules.add_rule(self.conn, self.repo, self.write(bad), self.scratch)
        self.assertIn(f'#{self.sources[0]}', str(e.exception))

    def test_rule_refused_when_fields_missing(self):
        with self.assertRaises(store.KbError):
            rules.add_rule(self.conn, self.repo, self.write({'id': 'x'}), self.scratch)

    def test_run_rules_hits_become_candidates(self):
        rules.add_rule(self.conn, self.repo, self.write(rule(sources=self.sources)), self.scratch)
        hits = rules.run_rules(self.conn, self.repo, self.repo, self.scratch)
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0]['source'], 'rule:label-for-points-nowhere')
        ids = findings.add_findings(self.conn, self.repo, self.rid, hits)
        self.assertEqual(len(ids), 2)

    def test_rule_auto_off(self):
        rules.add_rule(self.conn, self.repo, self.write(rule(sources=self.sources)), self.scratch)
        src = 'rule:label-for-points-nowhere'
        findings.add_findings(self.conn, self.repo, self.rid, [
            {'kind': 'label-for-mismatch', 'root_cause': 'r', 'path': 'p', 'snippet': f's{i}', 'source': src,
             'outcome': o, 'outcome_phase': '1' if o.startswith('dropped') else None}
            for i, o in enumerate(['merged', 'dropped:false-positive', 'dropped:false-positive', 'closed'])])
        self.assertEqual(rules.refresh_health(self.conn, self.cfg), ['label-for-points-nowhere'])
        row = self.conn.execute('SELECT * FROM rules').fetchone()
        self.assertEqual((row['active'], row['hits'], row['decided'], row['merged'], row['false_positives']),
                         (0, 4, 4, 1, 2))
        self.assertEqual(rules.run_rules(self.conn, self.repo, self.repo, self.scratch), [])

    def test_rules_skip_when_memory_off(self):
        rules.add_rule(self.conn, self.repo, self.write(rule(sources=self.sources)), self.scratch)
        findings.start_round(self.conn, 'main', 'y', 'off', {}, [])
        self.assertEqual(rules.run_rules(self.conn, self.repo, self.repo, self.scratch), [])

    @unittest.skipUnless(os.environ.get('KB_TEST_AST_GREP') and shutil.which('npx'), 'needs npx and network')
    def test_ast_grep_rule(self):
        commit(self.repo, 'src/a.js', 'async function f() {\n  const x = load();\n  if (x) save();\n}\n', 'Add a')
        r = {'id': 'promise-in-if', 'engine': 'ast-grep', 'kind': 'promise-as-boolean', 'pattern': 'p/q',
             'message': 'a Promise used as a boolean', 'sources': [],
             'ast_grep': {'language': 'javascript', 'rule': {'pattern': 'const $X = load()'}},
             'must_match': 'const y = load();', 'must_not_match': 'const y = await load();'}
        rules.add_rule(self.conn, self.repo, self.write(r), self.scratch)
        hits = rules.run_rules(self.conn, self.repo, self.repo, self.scratch)
        self.assertEqual([h['path'] for h in hits], ['src/a.js'])


if __name__ == '__main__':
    unittest.main()
