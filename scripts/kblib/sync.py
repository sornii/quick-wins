"""What the team did with each PR: merged, closed, still open, or reverted later."""
import json
import re
import subprocess

from kblib import store

FIELDS = ('state title mergeCommit { oid } commits { totalCount } '
          'reviews(last: 3) { nodes { body } } comments(last: 3) { nodes { body } }')


class SyncError(store.KbError):
    pass


def _gh_graphql(query):
    try:
        r = subprocess.run(['gh', 'api', 'graphql', '-f', 'query=' + query], capture_output=True, text=True)
    except FileNotFoundError:
        raise SyncError('sync skipped: gh is not installed')
    try:
        data = json.loads(r.stdout) if r.stdout.strip() else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict) or (r.returncode != 0 and not data.get('data')):
        lines = r.stderr.strip().splitlines()
        raise SyncError('sync skipped: ' + (lines[-1] if lines else f'gh exited {r.returncode}'))
    if not data.get('data'):
        err = (data.get('errors') or [{}])[0]
        raise SyncError('sync skipped: ' + str(err.get('message', err) or 'no data'))
    # partial errors (a deleted repository, a PR that no longer exists) leave those nodes null
    return data['data']


def _owner_name(row, origin):
    m = re.search(r'github\.com/([^/]+)/([^/]+)/pull/', row['pr_url'] or '')
    if m:
        return m.group(1), m.group(2)
    if origin:
        return origin[1], origin[2]
    raise SyncError(f'sync skipped: finding #{row["id"]} has a PR number but no URL, and origin is not GitHub')


def _reason(node):
    bodies = [n['body'].strip() for part in ('reviews', 'comments') for n in (node.get(part) or {}).get('nodes', [])
              if n.get('body') and n['body'].strip()]
    text = re.sub(r'(?<![\w.])@[A-Za-z0-9][\w-]*', '@someone', ' | '.join(bodies))
    return (text[:497] + '...') if len(text) > 500 else (text or 'closed without comment')


def _fetch(rows, origin):
    repos = {}
    for row in rows:
        repos.setdefault(_owner_name(row, origin), []).append(row)
    parts, where = [], {}
    for i, ((owner, name), group) in enumerate(repos.items()):
        prs = ' '.join(f'p{r["pr_number"]}: pullRequest(number: {int(r["pr_number"])}) {{ {FIELDS} }}' for r in group)
        parts.append(f'r{i}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ {prs} }}')
        for r in group:
            where[r['id']] = (f'r{i}', f'p{r["pr_number"]}')
    data = _gh_graphql('query { ' + ' '.join(parts) + ' }')
    out = {}
    for fid, (repo_alias, pr_alias) in where.items():
        node = (data.get(repo_alias) or {}).get(pr_alias)
        if node is not None:
            out[fid] = node
    return out


def _base_log(repo_dir, base):
    r = subprocess.run(['git', '-C', repo_dir, 'log', f'origin/{base}', '--grep=This reverts commit', '--grep=^Revert "',
                        '--format=%H%x00%s%x00%b%x1e'], capture_output=True, text=True)
    if r.returncode != 0:
        return []
    out = []
    for entry in r.stdout.split('\x1e'):
        bits = entry.strip('\n').split('\x00')
        if len(bits) == 3:
            out.append(bits)
    return out


def _reverted(log, merge_commit, title):
    by_title = re.compile(r'^Revert "' + re.escape(title) + r'( \(#\d+\))?"( \(#\d+\))?$') if title else None
    for _sha, subject, body in log:
        if merge_commit and f'This reverts commit {merge_commit}' in body:
            return True
        if by_title and by_title.match(subject):
            return True
    return False


def sync(conn, repo_dir, base):
    url = subprocess.run(['git', '-C', repo_dir, 'remote', 'get-url', 'origin'], capture_output=True, text=True)
    origin = store.parse_remote(url.stdout) if url.returncode == 0 else None
    shipped = conn.execute("SELECT * FROM findings WHERE outcome = 'shipped' AND pr_number IS NOT NULL").fetchall()
    nodes = _fetch(shipped, origin) if shipped else {}
    counts = {'merged': 0, 'closed': 0, 'reverted': 0, 'pending': 0, 'unresolved': 0}
    now = store.now()
    for row in shipped:
        node = nodes.get(row['id'])
        if node is None:
            counts['unresolved'] += 1
            continue
        state = node.get('state')
        if state == 'MERGED':
            oid = (node.get('mergeCommit') or {}).get('oid')
            changed = 1 if (node.get('commits') or {}).get('totalCount', 1) > 1 else 0
            conn.execute("UPDATE findings SET outcome = 'merged', merge_commit = ?, pr_title = ?, "
                         'changed_in_review = ?, updated = ? WHERE id = ?',
                         (oid, node.get('title'), changed, now, row['id']))
            counts['merged'] += 1
        elif state == 'CLOSED':
            conn.execute("UPDATE findings SET outcome = 'closed', reason = ?, pr_title = ?, updated = ? WHERE id = ?",
                         (_reason(node), node.get('title'), now, row['id']))
            counts['closed'] += 1
        else:
            counts['pending'] += 1
    log = _base_log(repo_dir, base)
    for row in conn.execute("SELECT * FROM findings WHERE outcome = 'merged'").fetchall():
        if _reverted(log, row['merge_commit'], row['pr_title']):
            conn.execute("UPDATE findings SET outcome = 'reverted', updated = ? WHERE id = ?", (now, row['id']))
            counts['reverted'] += 1
    conn.commit()
    return counts
