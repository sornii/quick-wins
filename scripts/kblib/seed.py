"""Seed the database from the repository's own small fix commits, so the first round isn't cold."""
import fnmatch
import glob
import json
import os
import re
import subprocess

from kblib import findings, store

DEFAULT_FIX_REGEX = r'(?i)\b(fix|fixes|fixed|bug|bugfix|typo|hotfix)\b'
BATCH = 25
DIFF_CHARS = 4000
EXCLUDED = ('*.lock', 'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'npm-shrinkwrap.json', '*.min.*',
            'dist/*', 'build/*', 'vendor/*', 'node_modules/*', '*/dist/*', '*/build/*', '*/vendor/*',
            '*/node_modules/*', '.meteor/versions', '.meteor/packages')
LOCALE = ('*.i18n.json', 'i18n/*', '*/i18n/*', 'locales/*', '*/locales/*')

README = """Classify each commit in batch-NNN.json: is it a small bug fix of one of the known kinds?
Write answer-NNN.json (same NNN) as a JSON list with one entry per commit:
  {"commit": "<sha>", "kind": "<kind>", "pattern": "<kind>/<short-root-cause-slug>",
   "root_cause": "<one sentence: why the old code was wrong>", "path": "<file>", "snippet": "<the buggy line(s), as before the fix>"}
or {"commit": "<sha>", "skip": true} when it is not a small bug fix of a known kind (refactors, features,
config, dependency bumps, anything you are unsure of).
Known kinds: %s. A bug of none of these: "new:<slug>".
Never include who wrote a commit.
"""


def _matches(path, patterns):
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def excluded_paths(paths):
    """The paths of one commit that don't count: lockfiles, generated, vendored, and locale bulk edits."""
    out = {p for p in paths if _matches(p, EXCLUDED)}
    locale = [p for p in paths if _matches(p, LOCALE)]
    if len(locale) > 1:
        out.update(locale)
    return out


def _git(repo_dir, *args):
    r = subprocess.run(['git', '-C', repo_dir, *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise store.KbError(f'git {args[0]}: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "failed"}')
    return r.stdout


def commits_with_numstat(repo_dir, rev, extra=()):
    """[(sha, subject, [(added, deleted, path)])], newest first. Binary files count as 0 lines."""
    out = _git(repo_dir, 'log', rev, '--no-merges', '--format=%x1e%H%x00%s', '--numstat', *extra)
    result = []
    for entry in out.split('\x1e')[1:]:
        head, _, rest = entry.partition('\n')
        sha, _, subject = head.partition('\x00')
        files = []
        for line in rest.splitlines():
            bits = line.split('\t')
            if len(bits) == 3:
                a, d, p = bits
                files.append((int(a) if a.isdigit() else 0, int(d) if d.isdigit() else 0, p))
        result.append((sha, subject, files))
    return result


def _seen(conn):
    return {r[0] for r in conn.execute('SELECT commit_sha FROM seed_seen')}


def prepare(conn, repo_dir, base, scratch, fix_regex):
    cfg = store.load_config(repo_dir)
    rx = re.compile(fix_regex or DEFAULT_FIX_REGEX)
    seen = _seen(conn)
    room = max(0, cfg['seed_limit'] - len(seen))
    picked = []
    for sha, subject, files in commits_with_numstat(repo_dir, f'origin/{base}'):
        if len(picked) >= room:
            break
        if sha in seen or subject.startswith('Revert "') or not rx.search(subject):
            continue
        drop = excluded_paths([p for _, _, p in files])
        kept = [(a, d, p) for a, d, p in files if p not in drop]
        if not kept or sum(a + d for a, d, _ in kept) > cfg['seed_max_lines']:
            continue
        diff = _git(repo_dir, 'show', '--format=', '--patch', sha, '--', *[p for _, _, p in kept])
        picked.append({'commit': sha, 'subject': subject, 'diff': diff[:DIFF_CHARS]})
    folder = os.path.join(scratch, 'seed')
    os.makedirs(folder, exist_ok=True)
    for old in glob.glob(os.path.join(folder, 'batch-*.json')) + glob.glob(os.path.join(folder, 'answer-*.json')):
        os.remove(old)
    with open(os.path.join(folder, 'README.txt'), 'w') as f:
        f.write(README % ', '.join(store.KINDS))
    n = 0
    for n, i in enumerate(range(0, len(picked), BATCH), 1):
        with open(os.path.join(folder, f'batch-{n:03d}.json'), 'w') as f:
            json.dump(picked[i:i + BATCH], f, indent=1)
    return n


def load(conn, repo_dir, scratch):
    folder = os.path.join(scratch, 'seed')
    asked = set()
    for b in glob.glob(os.path.join(folder, 'batch-*.json')):
        asked.update(e['commit'] for e in json.load(open(b)))
    added = 0
    for path in sorted(glob.glob(os.path.join(folder, 'answer-*.json'))):
        try:
            entries = json.load(open(path))
        except json.JSONDecodeError as e:
            raise store.KbError(f'{path}: {e}')
        for e in entries:
            if e.get('commit') not in asked:
                raise store.KbError(f'{path}: {e.get("commit")!r} is not in any batch')
        items = [{'kind': e['kind'], 'pattern': e.get('pattern'), 'root_cause': e['root_cause'], 'path': e['path'],
                  'snippet': e['snippet'], 'source': 'seed', 'outcome': 'seed', 'seed_commit': e['commit'],
                  'base_sha': e['commit']} for e in entries if not e.get('skip')]
        findings.add_findings(conn, repo_dir, None, items)
        conn.executemany('INSERT OR IGNORE INTO seed_seen (commit_sha, skipped, seen) VALUES (?, ?, ?)',
                         [(e['commit'], 1 if e.get('skip') else 0, store.now()) for e in entries])
        conn.commit()
        added += len(items)
    mark_reverted(conn, repo_dir)
    return added


def mark_reverted(conn, repo_dir):
    seeds = {r['seed_commit']: r['id'] for r in conn.execute(
        "SELECT id, seed_commit FROM findings WHERE outcome = 'seed' AND seed_commit IS NOT NULL")}
    if not seeds:
        return 0
    base = conn.execute('SELECT base FROM rounds ORDER BY id DESC LIMIT 1').fetchone()
    rev = f'origin/{base[0]}' if base and base[0] else 'HEAD'
    r = subprocess.run(['git', '-C', repo_dir, 'log', rev, '--format=%b%x1e'], capture_output=True, text=True)
    if r.returncode != 0:
        r = subprocess.run(['git', '-C', repo_dir, 'log', '--all', '--format=%b%x1e'], capture_output=True, text=True)
    n = 0
    for sha in re.findall(r'This reverts commit ([0-9a-f]{7,40})', r.stdout):
        for seed_sha, fid in seeds.items():
            if seed_sha.startswith(sha):
                conn.execute("UPDATE findings SET outcome = 'reverted', updated = ? WHERE id = ?", (store.now(), fid))
                n += 1
    conn.commit()
    return n


def status(conn, repo_dir, scratch):
    cfg = store.load_config(repo_dir)
    seen = len(_seen(conn))
    seeds = conn.execute("SELECT COUNT(*) FROM findings WHERE source = 'seed'").fetchone()[0]
    waiting = 0
    if scratch:
        folder = os.path.join(scratch, 'seed')
        for b in glob.glob(os.path.join(folder, 'batch-*.json')):
            if not os.path.exists(b.replace('batch-', 'answer-')):
                waiting += 1
    room = max(0, cfg['seed_limit'] - seen)
    return (f'{seen} of {cfg["seed_limit"]} commits read, {seeds} seeds; '
            f'{waiting} batch{"es" if waiting != 1 else ""} waiting for an answer'
            + ('; room for more' if room else '; limit reached'))
