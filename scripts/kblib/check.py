"""Is a candidate already known? strong (skip), weak (show the earlier record), or miss."""
import re

from kblib import findings, store

STRONG_OUTCOMES = ('shipped', 'dropped:false-positive', 'dropped:dead-code', 'dropped:product-decision')
CAME_BACK = ('merged', 'reverted')


def _miss():
    return {'gate': 'miss', 'match': None, 'note': ''}


def _fts_query(text):
    words = sorted({w.lower() for w in re.findall(r'[A-Za-z0-9_]{4,}', text or '')}, key=len, reverse=True)[:8]
    return ' OR '.join(f'"{w}"' for w in words)


def _one(conn, repo_dir, item, cfg):
    path, snippet = findings.norm_path(repo_dir, item.get('path')), item.get('snippet')
    if not path or not snippet:
        raise ValueError('every candidate needs path and snippet')
    sh = findings.snippet_hash(snippet)
    ch = findings.context_hash(repo_dir, path, snippet, cfg['context_lines'])
    rows = conn.execute('SELECT * FROM findings WHERE snippet_hash = ? ORDER BY id DESC', (sh,)).fetchall()
    here = [r for r in rows if r['path'] == path]
    for r in here:
        if r['outcome'] in CAME_BACK and r['source'] != 'seed':
            return {'gate': 'weak', 'match': r['id'], 'note': f'reintroduced (#{r["id"]}): it was {r["outcome"]}'}
    for r in here:
        if (r['outcome'] in STRONG_OUTCOMES and r['superseded_by'] is None and r['source'] != 'seed'
                and ch is not None and r['context_hash'] == ch):
            why = f': {r["reason"]}' if r['reason'] else ''
            return {'gate': 'strong', 'match': r['id'], 'note': f'known, {r["outcome"]} (#{r["id"]}){why}'}
    if here:
        r = here[0]
        return {'gate': 'weak', 'match': r['id'], 'note': f'seen before (#{r["id"]}, {r["outcome"]})'}
    if rows:
        r = rows[0]
        return {'gate': 'weak', 'match': r['id'], 'note': f'sibling of #{r["id"]} in {r["path"]} ({r["outcome"]})'}
    q = _fts_query(item.get('root_cause'))
    if q:
        r = conn.execute('SELECT f.* FROM findings_fts JOIN findings f ON f.id = findings_fts.rowid '
                         'WHERE findings_fts MATCH ? AND f.path = ? AND f.superseded_by IS NULL '
                         'ORDER BY bm25(findings_fts) LIMIT 1', (q, path)).fetchone()
        if r:
            why = f': {r["reason"]}' if r['reason'] else ''
            return {'gate': 'weak', 'match': r['id'], 'note': f'related #{r["id"]} ({r["outcome"]}){why}'}
    return _miss()


def check_candidates(conn, repo_dir, items, memory_on):
    cfg = store.load_config(repo_dir)
    out, seen = [], {}
    for i, item in enumerate(items):
        if not memory_on:
            out.append(_miss())
            continue
        key = (findings.norm_path(repo_dir, item.get('path')), findings.snippet_hash(item.get('snippet') or ''))
        if key in seen:
            out.append({'gate': 'weak', 'match': None, 'note': f'same as candidate {seen[key]} in this batch'})
            continue
        seen[key] = i
        out.append(_one(conn, repo_dir, item, cfg))
    return out


def summary(results):
    n = {'strong': 0, 'weak': 0, 'miss': 0}
    for r in results:
        n[r['gate']] += 1
    back = sum(1 for r in results if r['note'].startswith('reintroduced'))
    return f'# strong {n["strong"]}, weak {n["weak"]}, reintroduced {back}, miss {n["miss"]}'
