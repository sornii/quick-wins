import unittest

from tests.helpers import DataDirCase, commit, run_kb
from kblib import findings, stats, store


def item(outcome, kind='missing-await', phase=None, source='hunter:explore', **kw):
    it = {'kind': kind, 'root_cause': 'r', 'path': 'a.js', 'snippet': 'x', 'source': source, 'outcome': outcome}
    if phase:
        it['outcome_phase'] = phase
    it.update(kw)
    return it


class StatsTest(DataDirCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        commit(self.repo, 'a.js', 'x\n', 'Add a')
        self.conn = store.connect(self.repo)
        self.cfg = store.load_config(self.repo)

    def add(self, items, memory='on'):
        rid = findings.start_round(self.conn, 'main', 'x', memory, {}, [])
        findings.add_findings(self.conn, self.repo, rid, items)
        return rid

    def test_round_stats_counts_phases(self):
        self.add([item('dropped:false-positive', phase='1'), item('dropped:dead-code', phase='1'),
                  item('dropped:false-positive', phase='3'), item('merged', source='hunter:hot'), item('merged'),
                  item('shipped')])
        [r] = stats.round_stats(self.conn, 5)
        self.assertEqual((r['candidates'], r['after_1'], r['after_3'], r['merged'], r['pending']), (6, 4, 3, 2, 1))
        self.assertEqual(r['by_source']['hunter:hot'], {'candidates': 1, 'merged': 1})
        self.assertEqual(r['by_kind']['missing-await'], {'candidates': 6, 'merged': 2})

    def test_kind_rates_formula(self):
        self.add([item('merged'), item('merged'), item('reverted'), item('dropped:false-positive', phase='1'),
                  item('dropped:false-positive', phase='3'), item('dropped:duplicate', phase='1'),
                  item('shipped')])
        rate, decided = stats.kind_rates(self.conn, self.cfg)['missing-await']
        self.assertEqual(decided, 6)
        self.assertAlmostEqual(rate, 3 / 8)

    def test_seed_and_review_weights(self):
        self.add([item('merged')])
        self.conn.execute('UPDATE findings SET changed_in_review = 1')
        findings.add_findings(self.conn, self.repo, None, [item('seed', source='seed', kind='visible-typo')])
        rates = stats.kind_rates(self.conn, self.cfg)
        self.assertAlmostEqual(rates['missing-await'][0], (0.5 + 1) / (1 + 2))
        rate, decided = rates['visible-typo']
        self.assertAlmostEqual(decided, 0.3)
        self.assertAlmostEqual(rate, 1.3 / 2.3)

    def test_off_rounds_labelled(self):
        self.add([item('merged')], memory='off')
        self.add([item('merged')])
        text = stats.format_stats(stats.round_stats(self.conn, 5))
        self.assertIn('memory off', text)
        self.assertIn('memory on', text)

    def test_empty_round_not_shown(self):
        self.add([item('merged')])
        findings.start_round(self.conn, 'main', 'x', 'on', {}, [])
        rows = stats.round_stats(self.conn, 5)
        self.assertEqual([r['candidates'] for r in rows], [1])

    def test_cli(self):
        self.add([item('merged')])
        r = run_kb('stats', '--rounds', '3', cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('round', r.stdout)


if __name__ == '__main__':
    unittest.main()
