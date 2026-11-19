import json
import os
import unittest

from tests.helpers import DataDirCase, commit, run_kb, tempdir
from kblib import findings, promotions, rules, store
from tests.test_rules import rule

SRC = 'rule:label-for-points-nowhere'


def f(outcome, i, source='hunter:explore', **kw):
    it = {'kind': 'label-for-mismatch', 'root_cause': 'r', 'path': 'p.html', 'snippet': f's{i}', 'source': source,
          'outcome': outcome}
    if outcome.startswith('dropped:'):
        it['outcome_phase'] = '1'
    it.update(kw)
    return it


class PromotionTest(DataDirCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        commit(self.repo, 'p.html', 'x\n', 'Add p')
        self.conn = store.connect(self.repo)
        self.cfg = store.load_config(self.repo)
        self.scratch = tempdir(self)

    def round(self, items):
        rid = findings.start_round(self.conn, 'main', 'x', 'on', {}, [])
        return findings.add_findings(self.conn, self.repo, rid, items) if items else []

    def add_rule(self):
        path = os.path.join(self.scratch, 'r.json')
        with open(path, 'w') as fh:
            json.dump(rule(), fh)
        rules.add_rule(self.conn, self.repo, path, self.scratch)

    def steps(self):
        return [p['step'] for p in promotions.promotions(self.conn, self.cfg)]

    def test_repo_rule_over_threshold(self):
        self.add_rule()
        self.round([f('merged', i, SRC) for i in range(5)])
        for _ in range(3):
            self.round([])
        [p] = [p for p in promotions.promotions(self.conn, self.cfg) if p['step'] == 'repo-rule']
        self.assertEqual(p['subject'], 'label-for-points-nowhere')
        self.assertIn('5 hits', p['evidence'])

    def test_repo_rule_under_threshold(self):
        self.add_rule()
        self.round([f('merged', i, SRC) for i in range(4)])
        self.assertNotIn('repo-rule', self.steps())

    def test_repo_rule_not_when_merge_share_low(self):
        self.add_rule()
        self.round([f('merged', i, SRC) for i in range(15)] + [f('closed', i + 20, SRC) for i in range(4)])
        self.assertNotIn('repo-rule', self.steps())

    def test_repo_rule_not_with_recent_false_positive(self):
        self.add_rule()
        self.round([f('merged', i, SRC) for i in range(9)] + [f('dropped:false-positive', 9, SRC)])
        self.assertNotIn('repo-rule', self.steps())

    def test_repo_docs_over_and_under(self):
        pat = 'label-for-mismatch/for-on-a-hidden-input'
        self.round([f('dropped:false-positive', i, pattern=pat, reason='hidden inputs need no label')
                    for i in range(2)])
        self.assertNotIn('repo-docs', self.steps())
        self.round([f('dropped:false-positive', 3, pattern=pat, reason='hidden inputs need no label')])
        [p] = [p for p in promotions.promotions(self.conn, self.cfg) if p['step'] == 'repo-docs']
        self.assertEqual(p['subject'], pat)
        self.assertIn('hidden inputs need no label', p['evidence'])

    def test_skill_update_over_and_under(self):
        noisy = [f('dropped:false-positive', i, 'hunter:hot') for i in range(4)] + [f('merged', 9, 'hunter:hot')]
        self.round(noisy)
        self.assertNotIn('skill-update', self.steps())
        self.round([dict(it, snippet=it['snippet'] + 'b') for it in noisy])
        [p] = [p for p in promotions.promotions(self.conn, self.cfg) if p['step'] == 'skill-update']
        self.assertEqual(p['subject'], 'source hunter:hot')

    def test_cli(self):
        r = run_kb('promotions', cwd=self.repo)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, '(nothing to propose)'), r.stderr)


if __name__ == '__main__':
    unittest.main()
