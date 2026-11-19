import json
import os
import unittest

from tests.helpers import DataDirCase, commit, git, publish, run_kb, tempdir
from kblib import findings, hotspots, store


def many_files(repo, n, subject, prefix):
    for i in range(n):
        full = os.path.join(repo, f'{prefix}/f{i}.js')
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as f:
            f.write(''.join(f'line {j}\n' for j in range(20)))
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', subject)


class HotspotTest(DataDirCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.conn = store.connect(self.repo)
        self.cfg = store.load_config(self.repo)

    def test_formatter_sweep_not_on_top(self):
        many_files(self.repo, 60, 'style: run prettier over everything', 'pretty')
        many_files(self.repo, 51, 'Move the helpers', 'moved')
        commit(self.repo, 'src/hot.js', ''.join(f'x{j}\n' for j in range(15)), 'Change hot')
        commit(self.repo, 'src/warm.js', ''.join(f'y{j}\n' for j in range(15)), 'Change warm')
        commit(self.repo, 'src/hot.js', ''.join(f'z{j}\n' for j in range(15)), 'Change hot again')
        publish(self.repo)
        top = hotspots.hotspots(self.conn, self.repo, 'main', self.cfg)
        self.assertEqual(top[0][0], 'src/hot.js')
        self.assertFalse(any(p.startswith(('pretty/', 'moved/')) for p, _ in top))

    def test_excluded_and_deleted_paths_skipped(self):
        commit(self.repo, 'package-lock.json', '{\n}\n' * 50, 'Bump')
        commit(self.repo, 'src/gone.js', 'a\n' * 30, 'Add gone')
        commit(self.repo, 'src/gone.js', None, 'Remove gone')
        commit(self.repo, 'src/kept.js', 'a\n', 'Add kept')
        commit(self.repo, 'docs/plan.md', 'a\n' * 40, 'Docs')
        commit(self.repo, 'src/kept.test.js', 'a\n' * 40, 'Test')
        publish(self.repo)
        paths = [p for p, _ in hotspots.hotspots(self.conn, self.repo, 'main', self.cfg)]
        self.assertEqual(paths, ['src/kept.js'])
        self.assertNotIn('package-lock.json', paths)
        self.assertNotIn('src/gone.js', paths)

    def test_bug_density_raises_score(self):
        commit(self.repo, 'src/a.js', 'a\n' * 10, 'Add a')
        commit(self.repo, 'src/b.js', 'b\n' * 10, 'Add b')
        publish(self.repo)
        rid = findings.start_round(self.conn, 'main', 'x', 'on', {}, [])
        findings.add_findings(self.conn, self.repo, rid, [
            {'kind': 'visible-typo', 'root_cause': 'r', 'path': 'src/b.js', 'snippet': 'b', 'source': 'hunter:hot',
             'outcome': 'merged'}])
        top = hotspots.hotspots(self.conn, self.repo, 'main', self.cfg)
        self.assertEqual(top[0][0], 'src/b.js')
        self.assertAlmostEqual(top[0][1], 2.0)


class PlanTest(DataDirCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.conn = store.connect(self.repo)
        self.cfg = store.load_config(self.repo)
        self.areas = ['a', 'b', 'c', 'd', 'e']
        self.hot = ['h1.js', 'h2.js', 'h3.js', 'h4.js']

    def plan(self, memory='on', hot=None):
        rid = findings.start_round(self.conn, 'main', 'x', memory, {}, self.areas)
        return hotspots.plan_hunters(self.conn, self.cfg, rid, self.areas, self.hot if hot is None else hot)

    def test_split_counts(self):
        p = self.plan()
        self.assertEqual(len(p['explore']), 2)
        self.assertEqual(len(p['hot']), 3)
        self.assertEqual(p['hot'][0], ['h1.js', 'h4.js'])
        self.assertEqual(p['kind'], [])

    def test_explore_rotates(self):
        self.assertEqual(self.plan()['explore'], ['a', 'b'])
        self.assertEqual(self.plan()['explore'], ['c', 'd'])
        self.assertEqual(self.plan()['explore'], ['e', 'a'])

    def test_no_hot_files_means_more_exploring(self):
        p = self.plan(hot=[])
        self.assertEqual(p['explore'], self.areas)
        self.assertEqual(p['hot'], [])

    def test_memory_off_plan(self):
        p = self.plan(memory='off')
        self.assertEqual(p, {'explore': self.areas, 'hot': [], 'kind': []})

    def test_cli(self):
        commit(self.repo, 'src/a.js', 'a\n', 'Add a')
        publish(self.repo)
        r = run_kb('round', 'start', '--base', 'main', '--areas', 'a,b,c,d,e', cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run_kb('hotspots', '--base', 'main', cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        hot = os.path.join(tempdir(self), 'hot.json')
        with open(hot, 'w') as f:
            f.write(r.stdout)
        r = run_kb('hunters', '--hot-from', hot, cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        plan = json.loads(r.stdout)
        self.assertEqual(plan['hot'], [['src/a.js']])
        self.assertEqual(len(plan['explore']), 4)

    def test_fewer_hot_files_than_hunters(self):
        p = self.plan(hot=['only.js'])
        self.assertEqual(p['hot'], [['only.js']])
        self.assertEqual(len(p['explore']), 4)


if __name__ == '__main__':
    unittest.main()
