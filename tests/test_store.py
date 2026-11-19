import json
import os
import sqlite3
import threading
import time
import unittest

from tests.helpers import DataDirCase, git, make_repo, run_kb, tempdir
from kblib import store


class RepoKeyTest(DataDirCase, unittest.TestCase):
    def test_repo_key_same_for_ssh_and_https(self):
        ssh = make_repo(self, 'git@github.com:Org/Name.git')
        https = make_repo(self, 'https://github.com/org/name')
        self.assertEqual(store.repo_key(ssh), 'github.com_org_name')
        self.assertEqual(store.repo_key(https), 'github.com_org_name')

    def test_repo_key_same_for_worktree(self):
        wt = os.path.join(tempdir(self), 'wt')
        git(self.repo, 'worktree', 'add', '-q', '--detach', wt)
        self.assertEqual(store.repo_key(wt), store.repo_key(self.repo))

    def test_repo_key_without_origin(self):
        repo = make_repo(self, origin=None)
        self.assertEqual(store.repo_key(repo), 'repo')
        wt = os.path.join(tempdir(self), 'other')
        git(repo, 'worktree', 'add', '-q', '--detach', wt)
        self.assertEqual(store.repo_key(wt), 'repo')


class ConfigTest(DataDirCase, unittest.TestCase):
    def test_config_defaults_and_override(self):
        self.assertEqual(store.load_config(self.repo)['seed_limit'], 300)
        with open(store.data_dir(self.repo) / 'config.json', 'w') as f:
            json.dump({'seed_limit': 50}, f)
        cfg = store.load_config(self.repo)
        self.assertEqual(cfg['seed_limit'], 50)
        self.assertEqual(cfg['hunters'], 5)


class SchemaTest(DataDirCase, unittest.TestCase):
    def test_connect_creates_schema_and_kinds(self):
        conn = store.connect(self.repo)
        self.assertEqual(conn.execute('SELECT version FROM schema_version').fetchone()[0], 1)
        kinds = conn.execute("SELECT slug FROM kinds WHERE status = 'approved' ORDER BY rowid").fetchall()
        self.assertEqual([k[0] for k in kinds], list(store.KINDS))
        self.assertEqual(len(store.KINDS), 15)
        conn.execute('SELECT * FROM findings_fts').fetchall()

    def test_migration_writes_backup_first(self):
        path = store.data_dir(self.repo) / 'kb.db'
        old = sqlite3.connect(path)
        old.execute('CREATE TABLE schema_version (version INTEGER)')
        old.execute('INSERT INTO schema_version VALUES (0)')
        old.commit()
        old.close()
        conn = store.connect(self.repo)
        self.assertTrue((store.data_dir(self.repo) / 'kb.db.bak').exists())
        bak = sqlite3.connect(store.data_dir(self.repo) / 'kb.db.bak')
        self.assertEqual(bak.execute('SELECT version FROM schema_version').fetchone()[0], 0)
        self.assertEqual(conn.execute('SELECT version FROM schema_version').fetchone()[0], 1)

    def test_concurrent_writer_waits(self):
        a = store.connect(self.repo)
        a.execute('BEGIN IMMEDIATE')
        a.execute("INSERT INTO rounds (started, memory) VALUES ('t', 'on')")
        errors = []

        def write():
            try:
                b = store.connect(self.repo)
                b.execute("INSERT INTO rounds (started, memory) VALUES ('t', 'on')")
                b.commit()
            except sqlite3.OperationalError as e:
                errors.append(e)

        t = threading.Thread(target=write)
        t.start()
        time.sleep(0.5)
        a.commit()
        t.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(a.execute('SELECT COUNT(*) FROM rounds').fetchone()[0], 2)


class CliTest(DataDirCase, unittest.TestCase):
    def test_init_prints_data_dir(self):
        r = run_kb('init', cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), str(store.data_dir(self.repo)))


if __name__ == '__main__':
    unittest.main()
