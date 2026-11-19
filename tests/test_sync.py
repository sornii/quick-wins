import json
import unittest

from tests.helpers import DataDirCase, commit, fake_gh, publish, run_kb
from kblib import findings, store, sync


def pr(state, title='Fix a', oid=None, commits=1, reviews=(), comments=()):
    return {'state': state, 'title': title, 'mergeCommit': {'oid': oid} if oid else None,
            'commits': {'totalCount': commits},
            'reviews': {'nodes': [{'body': b} for b in reviews]},
            'comments': {'nodes': [{'body': b} for b in comments]}}


class SyncTest(DataDirCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        commit(self.repo, 'a.js', 'x\n', 'Add a')
        publish(self.repo)
        self.conn = store.connect(self.repo)
        rid = findings.start_round(self.conn, 'main', 'x', 'on', {}, [])
        items = [{'kind': 'visible-typo', 'root_cause': 'typo', 'path': 'a.js', 'snippet': 'x', 'nn': f'0{n}',
                  'source': 'hunter:explore', 'outcome': 'fixed'} for n in (1, 2, 3)]
        findings.add_findings(self.conn, self.repo, rid, items)
        for n in (1, 2, 3):
            findings.update_finding(self.conn, f'0{n}', outcome='shipped', pr_number=40 + n,
                                    pr_url=f'https://github.com/org/name/pull/{40 + n}')

    def row(self, nn):
        return self.conn.execute('SELECT * FROM findings WHERE nn = ?', (nn,)).fetchone()

    def respond(self, prs):
        return {'graphql': {'data': {'r0': {f'p{k}': v for k, v in prs.items()}}}}

    def test_merged_closed_open(self):
        with fake_gh(self, self.respond({41: pr('MERGED', oid='a' * 40), 42: pr('CLOSED', reviews=['We want this.']),
                                         43: pr('OPEN')})) as calls:
            counts = sync.sync(self.conn, self.repo, 'main')
        self.assertEqual(counts, {'merged': 1, 'closed': 1, 'reverted': 0, 'pending': 1, 'unresolved': 0})
        self.assertEqual(self.row('01')['outcome'], 'merged')
        self.assertEqual(self.row('01')['merge_commit'], 'a' * 40)
        self.assertEqual(self.row('02')['outcome'], 'closed')
        self.assertIn('We want this.', self.row('02')['reason'])
        self.assertEqual(self.row('03')['outcome'], 'shipped')
        with open(calls) as f:
            self.assertEqual(len(f.readlines()), 1)

    def test_changed_in_review(self):
        with fake_gh(self, self.respond({41: pr('MERGED', oid='a' * 40, commits=3), 42: pr('OPEN'), 43: pr('OPEN')})):
            sync.sync(self.conn, self.repo, 'main')
        self.assertEqual(self.row('01')['changed_in_review'], 1)

    def test_revert_detected(self):
        oid = commit(self.repo, 'a.js', 'y\n', 'Fix a (#41)')
        commit(self.repo, 'a.js', 'x\n', 'Revert "Fix a (#41)"', body=f'This reverts commit {oid}.')
        publish(self.repo)
        with fake_gh(self, self.respond({41: pr('MERGED', title='Fix a', oid=oid), 42: pr('OPEN'), 43: pr('OPEN')})):
            counts = sync.sync(self.conn, self.repo, 'main')
        self.assertEqual(self.row('01')['outcome'], 'reverted')
        self.assertEqual(counts['reverted'], 1)

    def test_revert_by_title(self):
        commit(self.repo, 'a.js', 'x\n', 'Revert "Fix the label"')
        publish(self.repo)
        with fake_gh(self, self.respond({41: pr('MERGED', title='Fix the label', oid='b' * 40), 42: pr('OPEN'),
                                         43: pr('OPEN')})):
            sync.sync(self.conn, self.repo, 'main')
        self.assertEqual(self.row('01')['outcome'], 'reverted')

    def test_revert_title_needs_exact_match(self):
        commit(self.repo, 'a.js', 'x\n', 'Revert "Fix label for the signup form"')
        publish(self.repo)
        with fake_gh(self, self.respond({41: pr('MERGED', title='Fix label', oid='c' * 40), 42: pr('OPEN'),
                                         43: pr('OPEN')})):
            sync.sync(self.conn, self.repo, 'main')
        self.assertEqual(self.row('01')['outcome'], 'merged')

    def test_revert_title_of_squash_merge(self):
        commit(self.repo, 'a.js', 'x\n', 'Revert "Fix label (#41)"')
        publish(self.repo)
        with fake_gh(self, self.respond({41: pr('MERGED', title='Fix label', oid='c' * 40), 42: pr('OPEN'),
                                         43: pr('OPEN')})):
            sync.sync(self.conn, self.repo, 'main')
        self.assertEqual(self.row('01')['outcome'], 'reverted')

    def test_partial_errors_apply_the_rest(self):
        body = {'data': {'r0': {'p41': pr('MERGED', oid='a' * 40), 'p42': None, 'p43': pr('OPEN')}},
                'errors': [{'message': 'Could not resolve to a PullRequest with the number of 42.'}]}
        with fake_gh(self, {'graphql': {'__exit': 1, '__stdout': body}}):
            counts = sync.sync(self.conn, self.repo, 'main')
        self.assertEqual(counts, {'merged': 1, 'closed': 0, 'reverted': 0, 'pending': 1, 'unresolved': 1})
        self.assertEqual(self.row('02')['outcome'], 'shipped')

    def test_closed_reason_hides_handles(self):
        with fake_gh(self, self.respond({41: pr('CLOSED', reviews=['@alice-b we want this, cc @bob']),
                                         42: pr('OPEN'), 43: pr('OPEN')})):
            sync.sync(self.conn, self.repo, 'main')
        self.assertEqual(self.row('01')['reason'], '@someone we want this, cc @someone')

    def test_gh_failure_changes_nothing(self):
        with fake_gh(self, None):
            with self.assertRaises(sync.SyncError):
                sync.sync(self.conn, self.repo, 'main')
            r = run_kb('sync', '--base', 'main', cwd=self.repo)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn('Traceback', r.stderr)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)
        self.assertIn('sync skipped', r.stderr)
        for nn in ('01', '02', '03'):
            self.assertEqual(self.row(nn)['outcome'], 'shipped')

    def test_gh_missing(self):
        import os
        import shutil
        from tests.helpers import tempdir
        only_git = tempdir(self)
        os.symlink(shutil.which('git'), os.path.join(only_git, 'git'))
        old = os.environ['PATH']
        os.environ['PATH'] = only_git
        try:
            with self.assertRaises(sync.SyncError):
                sync.sync(self.conn, self.repo, 'main')
        finally:
            os.environ['PATH'] = old

    def test_cli_prints_counts(self):
        with fake_gh(self, self.respond({41: pr('OPEN'), 42: pr('OPEN'), 43: pr('OPEN')})):
            r = run_kb('sync', '--base', 'main', cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {'merged': 0, 'closed': 0, 'reverted': 0, 'pending': 3, 'unresolved': 0})


if __name__ == '__main__':
    unittest.main()
