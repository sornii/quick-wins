"""Where a repository's findings database lives, its schema, and its settings."""
import datetime
import json
import os
import pathlib
import re
import sqlite3
import subprocess

SCHEMA_VERSION = 1

KINDS = (
    'missing-await', 'promise-as-boolean', 'missing-return-after-error', 'wrong-arg-order',
    'null-check-misses-undefined', 'wrong-i18n-key', 'missing-i18n-key', 'visible-typo',
    'label-for-mismatch', 'wrong-link-or-route', 'wrong-template-param', 'wrong-query-options',
    'unhandled-throw-hangs-request', 'wrong-log-level', 'dropped-log-context',
)

DROP_REASONS = frozenset({'false-positive', 'dead-code', 'duplicate', 'product-decision', 'in-open-pr', 'unsafe'})
OUTCOMES = frozenset({'seed', 'candidate', 'confirmed', 'fixed', 'shipped', 'merged', 'closed', 'reverted'}
                     | {'dropped:' + r for r in DROP_REASONS})
PHASES = frozenset({'1', '2', '3', '3.5', '4', '5'})

DEFAULTS = {
    'hunters': 5,
    'explore_share': 0.25,
    'hot_window_days': 90,
    'sweep_files': 50,
    'seed_limit': 300,
    'seed_max_lines': 10,
    'seed_weight': 0.3,
    'context_lines': 20,
    'brief_max_lines': 40,
    'unknown_kind_min_decided': 3,
    'rule_min_precision': 0.5,
    'rule_min_decided': 4,
    'rule_from_merged': 2,
    'rule_from_seeds': 3,
    'promote_rule_hits': 5,
    'promote_rule_merged': 0.8,
    'promote_rule_clean_rounds': 3,
    'promote_doc_drops': 3,
    'promote_skill_noise': 0.8,
    'promote_skill_rounds': 2,
}

MIGRATIONS = {
    1: """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE rounds (
  id INTEGER PRIMARY KEY,
  started TEXT NOT NULL,
  base TEXT,
  base_sha TEXT,
  memory TEXT NOT NULL DEFAULT 'on',
  settings TEXT,
  areas TEXT,
  areas_explored TEXT
);
CREATE TABLE kinds (slug TEXT PRIMARY KEY, status TEXT NOT NULL);
CREATE TABLE findings (
  id INTEGER PRIMARY KEY,
  round_id INTEGER REFERENCES rounds(id),
  nn TEXT, slug TEXT, in_batch INTEGER NOT NULL DEFAULT 1, verify TEXT,
  kind TEXT NOT NULL, pattern TEXT, root_cause TEXT,
  path TEXT, symbol TEXT, area TEXT,
  snippet TEXT, snippet_hash TEXT, context_hash TEXT, base_sha TEXT,
  fix_diff TEXT, source TEXT,
  outcome TEXT NOT NULL, outcome_phase TEXT, reason TEXT,
  browser_check TEXT, introduced_in TEXT,
  pr_number INTEGER, pr_url TEXT, pr_title TEXT, merge_commit TEXT,
  changed_in_review INTEGER NOT NULL DEFAULT 0,
  superseded_by INTEGER, seed_commit TEXT,
  created TEXT NOT NULL, updated TEXT NOT NULL
);
CREATE INDEX findings_hash ON findings(snippet_hash);
CREATE INDEX findings_round ON findings(round_id, nn);
CREATE VIRTUAL TABLE findings_fts USING fts5(root_cause, reason, snippet, path, content='findings', content_rowid='id');
CREATE TRIGGER findings_ai AFTER INSERT ON findings BEGIN
  INSERT INTO findings_fts(rowid, root_cause, reason, snippet, path)
  VALUES (new.id, new.root_cause, new.reason, new.snippet, new.path);
END;
CREATE TRIGGER findings_ad AFTER DELETE ON findings BEGIN
  INSERT INTO findings_fts(findings_fts, rowid, root_cause, reason, snippet, path)
  VALUES ('delete', old.id, old.root_cause, old.reason, old.snippet, old.path);
END;
CREATE TRIGGER findings_au AFTER UPDATE ON findings BEGIN
  INSERT INTO findings_fts(findings_fts, rowid, root_cause, reason, snippet, path)
  VALUES ('delete', old.id, old.root_cause, old.reason, old.snippet, old.path);
  INSERT INTO findings_fts(rowid, root_cause, reason, snippet, path)
  VALUES (new.id, new.root_cause, new.reason, new.snippet, new.path);
END;
CREATE TABLE seed_seen (commit_sha TEXT PRIMARY KEY, skipped INTEGER NOT NULL, seen TEXT NOT NULL);
CREATE TABLE rules (
  id TEXT PRIMARY KEY,
  engine TEXT NOT NULL, kind TEXT, pattern TEXT, file TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  hits INTEGER NOT NULL DEFAULT 0, decided INTEGER NOT NULL DEFAULT 0,
  merged INTEGER NOT NULL DEFAULT 0, false_positives INTEGER NOT NULL DEFAULT 0,
  created TEXT NOT NULL
);
""",
}


class KbError(Exception):
    """An expected failure: printed as one line, exit 1."""


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


def _git(repo_dir, *args):
    r = subprocess.run(['git', '-C', repo_dir, *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def parse_remote(url):
    """(host, owner, name) from an SSH or HTTPS remote URL, lowercased; None if unrecognised."""
    url = url.strip()
    m = (re.match(r'^[\w.-]+@([^:/]+):(.+)$', url)
         or re.match(r'^(?:ssh|https?|git)://(?:[^@/]+@)?([^/:]+)(?::\d+)?/(.+)$', url))
    if not m:
        return None
    host, path = m.group(1), m.group(2)
    path = re.sub(r'\.git/?$', '', path.rstrip('/'))
    parts = path.split('/')
    if len(parts) < 2:
        return None
    return host.lower(), parts[-2].lower(), parts[-1].lower()


def repo_key(repo_dir):
    url = _git(repo_dir, 'remote', 'get-url', 'origin')
    parsed = parse_remote(url) if url else None
    if parsed:
        return '_'.join(parsed)
    common = _git(repo_dir, 'rev-parse', '--path-format=absolute', '--git-common-dir')
    if not common:
        raise KbError(f'not a git repository: {repo_dir}')
    common = pathlib.Path(common)
    return (common.parent if common.name == '.git' else common).name


def data_root():
    env = os.environ.get('KB_DATA_DIR')
    if env:
        return pathlib.Path(env)
    base = os.environ.get('XDG_DATA_HOME') or os.path.join(os.path.expanduser('~'), '.local', 'share')
    return pathlib.Path(base) / 'quick-wins'


def data_dir(repo_dir):
    d = data_root() / repo_key(repo_dir)
    (d / 'rules').mkdir(parents=True, exist_ok=True)
    return d


def load_config(repo_dir):
    cfg = dict(DEFAULTS)
    path = data_dir(repo_dir) / 'config.json'
    if path.exists():
        try:
            override = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise KbError(f'{path}: {e}')
        unknown = set(override) - set(DEFAULTS)
        if unknown:
            raise KbError(f'{path}: unknown settings {", ".join(sorted(unknown))}')
        cfg.update(override)
    return cfg


def _version(conn):
    try:
        row = conn.execute('SELECT version FROM schema_version').fetchone()
    except sqlite3.OperationalError:
        return 0
    return row[0] if row else 0


def connect(repo_dir):
    path = data_dir(repo_dir) / 'kb.db'
    existed = path.exists() and path.stat().st_size > 0
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout = 5000')
    if conn.execute('PRAGMA journal_mode').fetchone()[0] != 'wal':
        conn.execute('PRAGMA journal_mode = WAL')
    version = _version(conn)
    if version < SCHEMA_VERSION:
        if existed:
            bak = sqlite3.connect(path.with_name('kb.db.bak'))
            conn.backup(bak)
            bak.close()
        for target in range(version + 1, SCHEMA_VERSION + 1):
            conn.executescript('BEGIN;' + MIGRATIONS[target]
                               + 'DELETE FROM schema_version;'
                               + f'INSERT INTO schema_version VALUES ({target});'
                               + 'COMMIT;')
        conn.executemany("INSERT OR IGNORE INTO kinds (slug, status) VALUES (?, 'approved')",
                         [(k,) for k in KINDS])
        conn.commit()
    elif version > SCHEMA_VERSION:
        raise KbError(f'{path} is schema {version}; this kb.py knows {SCHEMA_VERSION}. Update the skill.')
    return conn
