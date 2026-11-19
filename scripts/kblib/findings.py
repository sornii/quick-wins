"""Rounds and findings: record, update, list."""
import hashlib
import json
import os
import re
import sqlite3
import subprocess

from kblib import store

REQUIRED = ('kind', 'root_cause', 'path', 'snippet', 'source', 'outcome')
OPTIONAL = ('pattern', 'symbol', 'area', 'verify', 'slug', 'nn', 'in_batch', 'reason', 'outcome_phase', 'base_sha',
            'seed_commit')
UPDATABLE = frozenset({'outcome', 'outcome_phase', 'reason', 'introduced_in', 'nn', 'slug', 'verify', 'in_batch',
                       'pattern', 'kind', 'browser_check', 'pr_number', 'pr_url', 'changed_in_review',
                       'superseded_by', 'symbol', 'area', 'fix_diff'})
VERIFY_TAGS = frozenset({'ui', 'http', 'none'})


def _lines(text):
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    return [line.strip() for line in text.split('\n') if line.strip()]


def normalise(text):
    return '\n'.join(_lines(text or ''))


def snippet_hash(snippet):
    return hashlib.sha1(normalise(snippet).encode()).hexdigest()


def context_hash(repo_dir, path, snippet, lines):
    try:
        with open(os.path.join(repo_dir, path), encoding='utf-8', errors='replace') as f:
            file_lines = _lines(f.read())
    except OSError:
        return None
    want = _lines(snippet or '')
    if not want:
        return None
    n = len(want)
    at = [i for i in range(len(file_lines) - n + 1) if file_lines[i:i + n] == want]
    if not at and n == 1:
        # a one-line snippet may be part of a longer line
        at = [i for i, line in enumerate(file_lines) if want[0] in line]
    if len(at) != 1:
        # missing, or ambiguous: the same code in two places must never count as the same finding
        return None
    i = at[0]
    window = file_lines[max(0, i - lines):i + n + lines]
    return hashlib.sha1('\n'.join(window).encode()).hexdigest()


def norm_path(repo_dir, path):
    """Repository-relative, without a leading ./ (absolute paths inside the checkout are made relative)."""
    p = (path or '').strip()
    if os.path.isabs(p):
        top = subprocess.run(['git', '-C', repo_dir, 'rev-parse', '--show-toplevel'], capture_output=True,
                             text=True).stdout.strip() or repo_dir
        rel = os.path.relpath(p, top)
        p = rel if not rel.startswith('..') else p
    while p.startswith('./'):
        p = p[2:]
    return os.path.normpath(p) if p else p


def validate_outcome(outcome):
    if outcome not in store.OUTCOMES:
        raise ValueError(f'unknown outcome {outcome!r}; one of: {", ".join(sorted(store.OUTCOMES))}')


def _validate_fields(fields):
    if 'outcome' in fields:
        validate_outcome(fields['outcome'])
    if fields.get('outcome_phase') is not None and str(fields['outcome_phase']) not in store.PHASES:
        raise ValueError(f'unknown phase {fields["outcome_phase"]!r}; one of: {", ".join(sorted(store.PHASES))}')
    if fields.get('verify') is not None and fields['verify'] not in VERIFY_TAGS:
        raise ValueError(f'unknown verify tag {fields["verify"]!r}; one of: ui, http, none')
    if fields.get('nn') is not None and not re.fullmatch(r'\d{2}', str(fields['nn'])):
        raise ValueError(f'NN must be two digits, got {fields["nn"]!r}')


def start_round(conn, base, base_sha, memory, settings, areas):
    if memory not in ('on', 'off'):
        raise ValueError('memory must be on or off')
    cur = conn.execute('INSERT INTO rounds (started, base, base_sha, memory, settings, areas) VALUES (?, ?, ?, ?, ?, ?)',
                       (store.now(), base, base_sha, memory, json.dumps(settings), json.dumps(list(areas or []))))
    conn.commit()
    return cur.lastrowid


ROUND = None  # kb.py --round: pin a round when another session may start one in the same repository


def current_round(conn):
    if ROUND is not None:
        r = conn.execute('SELECT * FROM rounds WHERE id = ?', (ROUND,)).fetchone()
        if r is None:
            raise LookupError(f'no round {ROUND}')
        return r
    return conn.execute('SELECT * FROM rounds ORDER BY id DESC LIMIT 1').fetchone()


def memory_on(conn):
    r = current_round(conn)
    return r is None or r['memory'] == 'on'


def _kind(conn, kind):
    if kind.startswith('new:'):
        slug = kind[4:]
        if not re.fullmatch(r'[a-z0-9]+(-[a-z0-9]+)*', slug):
            raise ValueError(f'new kind must be a lowercase slug, got {slug!r}')
        conn.execute("INSERT OR IGNORE INTO kinds (slug, status) VALUES (?, 'pending')", (slug,))
        return slug
    if not conn.execute('SELECT 1 FROM kinds WHERE slug = ?', (kind,)).fetchone():
        raise ValueError(f'unknown kind {kind!r}; use one of `kb.py kinds`, or new:<slug>')
    return kind


def head_sha(repo_dir, rev='HEAD'):
    r = subprocess.run(['git', '-C', repo_dir, 'rev-parse', '--verify', '--quiet', rev + '^{commit}'],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def add_findings(conn, repo_dir, round_id, items):
    cfg = store.load_config(repo_dir)
    for i, it in enumerate(items):
        missing = [k for k in REQUIRED if not it.get(k)]
        if missing:
            raise ValueError(f'item {i}: missing {", ".join(missing)}')
        unknown = set(it) - set(REQUIRED) - set(OPTIONAL)
        if unknown:
            raise ValueError(f'item {i}: unknown keys {", ".join(sorted(unknown))}')
        _validate_fields(it)
    try:
        ids = _insert(conn, repo_dir, round_id, items, cfg)
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise ValueError(f'an NN is already used in this round ({e})')
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return ids


def _insert(conn, repo_dir, round_id, items, cfg):
    head = None
    if round_id is not None:
        r = conn.execute('SELECT base_sha FROM rounds WHERE id = ?', (round_id,)).fetchone()
        head = r['base_sha'] if r else None
    ids = []
    for it in items:
        row = {k: it.get(k) for k in REQUIRED + OPTIONAL}
        row['path'] = it['path'] = norm_path(repo_dir, it['path'])
        row['kind'] = _kind(conn, it['kind'])
        row['in_batch'] = 1 if it.get('in_batch') is None else int(it['in_batch'])
        if row['outcome_phase'] is not None:
            row['outcome_phase'] = str(row['outcome_phase'])
        if not row['base_sha']:
            head = head or head_sha(repo_dir)
            row['base_sha'] = head
        row['snippet_hash'] = snippet_hash(it['snippet'])
        row['context_hash'] = context_hash(repo_dir, it['path'], it['snippet'], cfg['context_lines'])
        row['round_id'] = round_id
        row['created'] = row['updated'] = store.now()
        cols = ', '.join(row)
        cur = conn.execute(f'INSERT INTO findings ({cols}) VALUES ({", ".join("?" * len(row))})', list(row.values()))
        ids.append(cur.lastrowid)
    return ids


def resolve(conn, ref):
    ref = str(ref).strip()
    if re.fullmatch(r'\d{2}', ref):
        r = current_round(conn)
        row = r and conn.execute('SELECT id FROM findings WHERE round_id = ? AND nn = ?', (r['id'], ref)).fetchone()
        if not row:
            raise LookupError(f'no finding {ref} in the current round')
        return row['id']
    m = re.fullmatch(r'#?(\d+)', ref)
    if not m:
        raise LookupError(f'not a finding reference: {ref!r} (NN of this round, or #id)')
    row = conn.execute('SELECT id FROM findings WHERE id = ?', (int(m.group(1)),)).fetchone()
    if not row:
        raise LookupError(f'no finding #{m.group(1)}')
    return row['id']


def update_finding(conn, ref, **fields):
    fid = resolve(conn, ref)
    wt = fields.pop('fix_diff_from', None)
    if wt:
        r = subprocess.run(['git', '-C', wt, 'show', 'HEAD', '--format=', '--patch'], capture_output=True, text=True)
        if r.returncode != 0:
            raise ValueError(f'cannot read the fix commit in {wt}: {r.stderr.strip()}')
        fields['fix_diff'] = r.stdout
    unknown = set(fields) - UPDATABLE
    if unknown:
        raise ValueError(f'cannot update {", ".join(sorted(unknown))}')
    _validate_fields(fields)
    if 'kind' in fields:
        fields['kind'] = _kind(conn, fields['kind'])
    if fields.get('outcome_phase') is not None:
        fields['outcome_phase'] = str(fields['outcome_phase'])
    if not fields:
        return
    fields['updated'] = store.now()
    sets = ', '.join(f'{k} = ?' for k in fields)
    conn.execute(f'UPDATE findings SET {sets} WHERE id = ?', [*fields.values(), fid])
    conn.commit()


def list_findings(conn, round_id, runners_up, verify):
    sql = "SELECT * FROM findings WHERE round_id = ? AND nn IS NOT NULL AND outcome NOT LIKE 'dropped:%'"
    if not runners_up:
        sql += ' AND in_batch = 1'
    rows = conn.execute(sql + ' ORDER BY nn', (round_id,)).fetchall()
    if verify:
        rows = [r for r in rows if r['verify'] in verify]
    return rows


def format_list(rows):
    out = []
    batch = [r for r in rows if r['in_batch']]
    rest = [r for r in rows if not r['in_batch']]
    for kind in sorted({r['kind'] for r in batch}, key=lambda k: min(r['nn'] for r in batch if r['kind'] == k)):
        out.append(f'## {kind}')
        out += [_line(r) for r in batch if r['kind'] == kind]
        out.append('')
    if rest:
        out.append('## Runners-up')
        out += [_line(r) for r in rest]
        out.append('')
    return '\n'.join(out).rstrip() + '\n' if out else '(no findings in this round)\n'


def _line(r):
    return f'{r["nn"]} {r["slug"] or "-"}: {r["path"]} - {r["root_cause"]}. verify: {r["verify"] or "?"} [{r["outcome"]}]'


def show(conn, ref):
    r = conn.execute('SELECT * FROM findings WHERE id = ?', (resolve(conn, ref),)).fetchone()
    parts = [
        f'{r["nn"] or "#" + str(r["id"])} {r["slug"] or ""}'.strip(),
        f'kind: {r["kind"]}' + (f' ({r["pattern"]})' if r['pattern'] else ''),
        f'where: {r["path"]}' + (f' in {r["symbol"]}' if r['symbol'] else ''),
        f'why it is a bug: {r["root_cause"]}',
        f'verify: {r["verify"] or "?"}',
        f'outcome: {r["outcome"]}' + (f' ({r["reason"]})' if r['reason'] else ''),
        'snippet:',
        r['snippet'],
    ]
    return '\n'.join(parts) + '\n'


def patterns(conn, kind):
    return conn.execute('SELECT pattern, COUNT(*) AS n FROM findings WHERE kind = ? AND pattern IS NOT NULL '
                        'GROUP BY pattern ORDER BY n DESC, pattern', (kind,)).fetchall()


def kinds(conn):
    return conn.execute('SELECT slug, status FROM kinds ORDER BY status, rowid').fetchall()


def kinds_pending(conn):
    return [r[0] for r in conn.execute("SELECT slug FROM kinds WHERE status = 'pending' ORDER BY rowid")]


def approve_kind(conn, slug):
    if not conn.execute("UPDATE kinds SET status = 'approved' WHERE slug = ? AND status = 'pending'", (slug,)).rowcount:
        raise LookupError(f'no pending kind {slug!r}')
    conn.commit()


def map_kind(conn, new_slug, existing):
    if not conn.execute("SELECT 1 FROM kinds WHERE slug = ? AND status = 'pending'", (new_slug,)).fetchone():
        raise LookupError(f'no pending kind {new_slug!r}')
    if not conn.execute("SELECT 1 FROM kinds WHERE slug = ? AND status = 'approved'", (existing,)).fetchone():
        raise LookupError(f'no approved kind {existing!r}')
    conn.execute('UPDATE findings SET kind = ?, updated = ? WHERE kind = ?', (existing, store.now(), new_slug))
    conn.execute('DELETE FROM kinds WHERE slug = ?', (new_slug,))
    conn.commit()
