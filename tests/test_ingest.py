import json
import os
import unittest

from tests.helpers import DataDirCase, commit, run_kb, tempdir
from kblib import findings, ingest, store


def item(nn):
    return {'kind': 'visible-typo', 'root_cause': 'typo', 'path': 'a.html', 'snippet': 'Lease', 'nn': nn,
            'slug': f's{nn}', 'source': 'hunter:explore', 'outcome': 'fixed'}


class IngestTest(DataDirCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        commit(self.repo, 'a.html', 'Lease Recent\n', 'Add a')
        self.conn = store.connect(self.repo)
        rid = findings.start_round(self.conn, 'main', 'x', 'on', {}, [])
        findings.add_findings(self.conn, self.repo, rid, [item('01'), item('02'), item('03')])
        self.dir = tempdir(self)

    def value(self, nn, col):
        return self.conn.execute(f'SELECT {col} FROM findings WHERE nn = ?', (nn,)).fetchone()[0]

    def test_import_checks_sets_browser_check(self):
        path = os.path.join(self.dir, 'checks.tsv')
        with open(path, 'w') as f:
            f.write('# comment\n\n01 s01 ui verified\n02 s02 ui not-verified not run: no safe database\n'
                    '03 s03 none skipped\n')
        self.assertEqual(ingest.import_checks(self.conn, path), 3)
        self.assertEqual(self.value('01', 'browser_check'), 'verified')
        self.assertEqual(self.value('02', 'browser_check'), 'not-verified')
        self.assertEqual(self.value('03', 'browser_check'), 'skipped')

    def test_import_ship_marks_shipped(self):
        path = os.path.join(self.dir, 'ship.json')
        with open(path, 'w') as f:
            json.dump({'repo': 'o/n', 'fixes': [{'n': '01', 'slug': 's01', 'pr': 'https://github.com/o/n/pull/42'},
                                                {'n': '02', 'slug': 's02'}]}, f)
        self.assertEqual(ingest.import_ship(self.conn, path), 1)
        self.assertEqual(self.value('01', 'outcome'), 'shipped')
        self.assertEqual(self.value('01', 'pr_number'), 42)
        self.assertEqual(self.value('01', 'pr_url'), 'https://github.com/o/n/pull/42')
        self.assertEqual(self.value('02', 'outcome'), 'fixed')

    def test_import_ship_ignores_dry_run_placeholder(self):
        path = os.path.join(self.dir, 'ship.json')
        with open(path, 'w') as f:
            json.dump({'repo': 'o/n', 'fixes': [{'n': '01', 'slug': 's01', 'pr': '(not opened)'}]}, f)
        self.assertEqual(ingest.import_ship(self.conn, path), 0)

    def test_cli(self):
        path = os.path.join(self.dir, 'checks.tsv')
        with open(path, 'w') as f:
            f.write('01 s01 ui verified\n')
        r = run_kb('import-checks', path, cwd=self.repo)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, '1'), r.stderr)
        with open(path, 'w') as f:
            f.write('broken\n')
        r = run_kb('import-checks', path, cwd=self.repo)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn('Traceback', r.stderr)


if __name__ == '__main__':
    unittest.main()
