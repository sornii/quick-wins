import glob
import json
import os
import unittest

from tests.helpers import DataDirCase, commit, publish, run_kb, tempdir
from kblib import seed, store


class SeedCase(DataDirCase):
    def setUp(self):
        super().setUp()
        commit(self.repo, 'src/a.js', 'one\ntwo\nthree\n', 'Add a')
        self.big = commit(self.repo, 'src/b.js', ''.join(f'line {i}\n' for i in range(40)), 'fix: rewrite b')
        commit(self.repo, 'src/c.js', 'c\n', 'chore: add c')
        commit(self.repo, 'package-lock.json', '{}\n', 'fix: bump lockfile')
        self.small = commit(self.repo, 'src/a.js', 'one\n2\nthree\n', '#12 fix: show the right number')
        publish(self.repo)
        self.conn = store.connect(self.repo)
        self.scratch = tempdir(self)

    def batches(self):
        return sorted(glob.glob(os.path.join(self.scratch, 'seed', 'batch-*.json')))

    def answer(self, entries, n='001'):
        with open(os.path.join(self.scratch, 'seed', f'answer-{n}.json'), 'w') as f:
            json.dump(entries, f)


class PrepareTest(SeedCase, unittest.TestCase):
    def test_prepare_filters_and_limits(self):
        self.assertEqual(seed.prepare(self.conn, self.repo, 'main', self.scratch, None), 1)
        [b] = self.batches()
        entries = json.load(open(b))
        self.assertEqual([e['commit'] for e in entries], [self.small])
        self.assertIn('+2', entries[0]['diff'])
        self.assertTrue(os.path.exists(os.path.join(self.scratch, 'seed', 'README.txt')))

    def test_prepare_respects_limit(self):
        commit(self.repo, 'src/d.js', 'd\n', 'fix: d')
        publish(self.repo)
        with open(store.data_dir(self.repo) / 'config.json', 'w') as f:
            json.dump({'seed_limit': 1}, f)
        seed.prepare(self.conn, self.repo, 'main', self.scratch, None)
        self.assertEqual(len(json.load(open(self.batches()[0]))), 1)

    def test_prepare_skips_already_seen(self):
        seed.prepare(self.conn, self.repo, 'main', self.scratch, None)
        self.answer([{'commit': self.small, 'skip': True}])
        seed.load(self.conn, self.repo, self.scratch)
        self.assertEqual(seed.prepare(self.conn, self.repo, 'main', self.scratch, None), 0)
        self.assertEqual(self.batches(), [])

    def test_custom_regex(self):
        self.assertEqual(seed.prepare(self.conn, self.repo, 'main', self.scratch, r'^chore'), 1)
        self.assertEqual(json.load(open(self.batches()[0]))[0]['subject'], 'chore: add c')


class LoadTest(SeedCase, unittest.TestCase):
    def test_load_and_revert(self):
        seed.prepare(self.conn, self.repo, 'main', self.scratch, None)
        self.answer([{'commit': self.small, 'kind': 'visible-typo', 'pattern': 'visible-typo/wrong-number',
                      'root_cause': 'shows two as a word', 'path': 'src/a.js', 'snippet': 'two'}])
        self.assertEqual(seed.load(self.conn, self.repo, self.scratch), 1)
        row = self.conn.execute('SELECT * FROM findings').fetchone()
        self.assertEqual((row['outcome'], row['source'], row['seed_commit'], row['round_id']),
                         ('seed', 'seed', self.small, None))
        commit(self.repo, 'src/a.js', 'one\ntwo\nthree\n', 'Revert "#12 fix: show the right number"',
               body=f'This reverts commit {self.small}.')
        publish(self.repo)
        seed.prepare(self.conn, self.repo, 'main', self.scratch, None)
        seed.load(self.conn, self.repo, self.scratch)
        self.assertEqual(self.conn.execute('SELECT outcome FROM findings').fetchone()[0], 'reverted')

    def test_load_twice_adds_once(self):
        seed.prepare(self.conn, self.repo, 'main', self.scratch, None)
        self.answer([{'commit': self.small, 'kind': 'visible-typo', 'root_cause': 'r', 'path': 'src/a.js',
                      'snippet': 'two'}])
        self.assertEqual(seed.load(self.conn, self.repo, self.scratch), 1)
        self.assertEqual(seed.load(self.conn, self.repo, self.scratch), 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM findings').fetchone()[0], 1)

    def test_load_names_missing_key(self):
        seed.prepare(self.conn, self.repo, 'main', self.scratch, None)
        self.answer([{'commit': self.small, 'kind': 'visible-typo'}])
        with self.assertRaises(store.KbError) as e:
            seed.load(self.conn, self.repo, self.scratch)
        self.assertIn("'root_cause'", str(e.exception))

    def test_missing_base_says_fetch(self):
        with self.assertRaises(store.KbError) as e:
            seed.prepare(self.conn, self.repo, 'nope', self.scratch, None)
        self.assertIn('origin/nope not found', str(e.exception))

    def test_non_ascii_path(self):
        commit(self.repo, 'src/página.js', 'a\nb\n', 'fix: accent')
        publish(self.repo)
        seed.prepare(self.conn, self.repo, 'main', self.scratch, None)
        entries = json.load(open(self.batches()[0]))
        self.assertIn('+++ b/src/página.js', entries[0]['diff'])

    def test_load_rejects_unknown_commit(self):
        seed.prepare(self.conn, self.repo, 'main', self.scratch, None)
        self.answer([{'commit': 'f' * 40, 'skip': True}])
        with self.assertRaises(store.KbError):
            seed.load(self.conn, self.repo, self.scratch)

    def test_cli_status(self):
        r = run_kb('seed', 'prepare', '--base', 'main', '--scratch', self.scratch, cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run_kb('seed', 'status', '--scratch', self.scratch, cwd=self.repo)
        self.assertIn('0 of 300 commits read', r.stdout)
        self.assertIn('1 batch waiting for an answer', r.stdout)


if __name__ == '__main__':
    unittest.main()
