"""Private rules: a proven bug pattern, written down so it runs before any hunter.

A rule is rules/<id>.json in the data folder:
  {"id", "engine": "html-regex" | "ast-grep", "kind", "pattern", "message", "sources": [finding ids],
   "must_match": "<code it must flag>", "must_not_match": "<code it must leave alone>",
   html-regex: "glob": "**/*.html", "regex": "<Python regex>", optional "unless": "<text with {group}>"
               (a match counts only when that text, with the regex's named groups filled in, is NOT in the file)
   ast-grep:   "ast_grep": {"language": "javascript", "rule": {...}, ...}   (an ast-grep rule, as JSON)}
A hit is a candidate like any other: it is checked, verified and reviewed before anything is fixed.
"""
import fnmatch
import json
import os
import re
import shutil
import subprocess
import tempfile

from kblib import findings, store
from kblib.stats import NEUTRAL_DROPS

ENGINES = ('html-regex', 'ast-grep')
REQUIRED = ('id', 'engine', 'kind', 'pattern', 'message', 'must_match', 'must_not_match')
EXT = {'javascript': '.js', 'js': '.js', 'typescript': '.ts', 'ts': '.ts', 'tsx': '.tsx', 'jsx': '.jsx',
       'python': '.py', 'html': '.html', 'css': '.css', 'go': '.go', 'ruby': '.rb', 'java': '.java'}


def _validate(rule):
    missing = [k for k in REQUIRED if not rule.get(k)]
    if missing:
        raise store.KbError(f'rule is missing {", ".join(missing)}')
    if not re.fullmatch(r'[a-z0-9]+(-[a-z0-9]+)*', rule['id']):
        raise store.KbError(f'rule id must be a lowercase slug, got {rule["id"]!r}')
    if rule['engine'] not in ENGINES:
        raise store.KbError(f'engine must be one of {", ".join(ENGINES)}')
    if rule['engine'] == 'html-regex':
        if not rule.get('regex') or not rule.get('glob'):
            raise store.KbError('an html-regex rule needs regex and glob')
        try:
            re.compile(rule['regex'])
        except re.error as e:
            raise store.KbError(f'regex: {e}')
    elif not isinstance(rule.get('ast_grep'), dict) or not rule['ast_grep'].get('language'):
        raise store.KbError('an ast-grep rule needs ast_grep with language and rule')


def _glob_match(path, glob):
    return fnmatch.fnmatch(path, glob.replace('**/', '*').replace('/**', '*'))


def _regex_hits(rule, path, text):
    rx = re.compile(rule['regex'], re.MULTILINE)
    lines = text.splitlines()
    out = []
    for m in rx.finditer(text):
        if rule.get('unless'):
            try:
                needle = rule['unless'].format(**{k: v for k, v in m.groupdict().items() if v is not None})
            except (KeyError, IndexError):
                continue
            if needle in text:
                continue
        line = text.count('\n', 0, m.start())
        out.append({'path': path, 'line': line + 1, 'snippet': lines[line].strip() if line < len(lines) else m.group(0)})
    return out


def _ast_grep(rule, target, scratch):
    cfg = dict(rule['ast_grep'])
    cfg.setdefault('id', rule['id'])
    env = dict(os.environ, npm_config_cache=os.path.join(scratch, 'ast-grep', 'npm-cache'), npm_config_yes='true')
    cmd = ['npx', '--yes', '--package', '@ast-grep/cli', 'ast-grep', 'scan', '--inline-rules', json.dumps(cfg),
           '--json=compact', '.']
    try:
        r = subprocess.run(cmd, cwd=target, capture_output=True, text=True, env=env, timeout=600)
    except FileNotFoundError:
        raise store.KbError('ast-grep rules need npx (Node.js)')
    except subprocess.TimeoutExpired:
        raise store.KbError('ast-grep timed out')
    if r.returncode not in (0, 1) or not r.stdout.strip().startswith('['):
        lines = r.stderr.strip().splitlines()
        raise store.KbError('ast-grep: ' + (lines[-1] if lines else f'exited {r.returncode}'))
    return [{'path': os.path.normpath(m['file']), 'line': m['range']['start']['line'] + 1,
             'snippet': (m.get('lines') or m.get('text') or '').strip().splitlines()[0]}
            for m in json.loads(r.stdout)]


def _hits(rule, target, scratch, files=None):
    """Matches in a checkout (files: git ls-files) or in the given {path: text}."""
    if rule['engine'] == 'html-regex':
        out = []
        if files is None:
            listed = subprocess.run(['git', '-c', 'core.quotePath=false', '-C', target, 'ls-files'],
                                    capture_output=True, text=True).stdout
            paths = [p for p in listed.splitlines() if _glob_match(p, rule['glob'])]
            for p in paths:
                try:
                    with open(os.path.join(target, p), encoding='utf-8', errors='replace') as f:
                        out += _regex_hits(rule, p, f.read())
                except OSError:
                    continue
        else:
            for p, text in files.items():
                out += _regex_hits(rule, p, text)
        return out
    if files is None:
        return _ast_grep(rule, target, scratch)
    tmp = tempfile.mkdtemp(dir=_scratch(scratch))
    try:
        for p, text in files.items():
            os.makedirs(os.path.dirname(os.path.join(tmp, p)) or tmp, exist_ok=True)
            with open(os.path.join(tmp, p), 'w') as f:
                f.write(text)
        return _ast_grep(rule, tmp, scratch)
    finally:
        shutil.rmtree(tmp, True)


def _scratch(scratch):
    os.makedirs(scratch, exist_ok=True)
    return scratch


def _example_name(rule, n):
    if rule['engine'] == 'html-regex':
        ext = os.path.splitext(rule['glob'])[1] or '.html'
    else:
        ext = EXT.get(rule['ast_grep']['language'].lower(), '.txt')
    return f'example{n}{ext}'


def test_rule(conn, rule, scratch):
    _validate(rule)
    errors = []
    if not _hits(rule, None, scratch, {_example_name(rule, 1): rule['must_match']}):
        errors.append('must_match is not flagged')
    if _hits(rule, None, scratch, {_example_name(rule, 2): rule['must_not_match']}):
        errors.append('must_not_match is flagged')
    for fid in rule.get('sources') or []:
        row = conn.execute('SELECT snippet FROM findings WHERE id = ?', (fid,)).fetchone()
        if not row:
            errors.append(f'source #{fid} does not exist')
        elif not _hits(rule, None, scratch, {_example_name(rule, 3): row['snippet']}):
            errors.append(f'source #{fid} is not flagged')
    return errors


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise store.KbError(f'{path}: {e}')


def add_rule(conn, repo_dir, path, scratch):
    rule = load(path)
    errors = test_rule(conn, rule, scratch)
    if errors:
        raise store.KbError(f'rule {rule.get("id")} refused: ' + '; '.join(errors))
    dest = store.data_dir(repo_dir) / 'rules' / f'{rule["id"]}.json'
    with open(dest, 'w') as f:
        json.dump(rule, f, indent=1)
    conn.execute('INSERT INTO rules (id, engine, kind, pattern, file, created) VALUES (?, ?, ?, ?, ?, ?) '
                 'ON CONFLICT(id) DO UPDATE SET engine = excluded.engine, kind = excluded.kind, '
                 'pattern = excluded.pattern, file = excluded.file, active = 1',
                 (rule['id'], rule['engine'], rule['kind'], rule['pattern'], str(dest), store.now()))
    conn.commit()
    return rule['id']


def run_rules(conn, repo_dir, checkout, scratch):
    if not findings.memory_on(conn):
        return []
    items = []
    for row in conn.execute('SELECT * FROM rules WHERE active = 1 ORDER BY id').fetchall():
        rule = load(row['file'])
        for h in _hits(rule, checkout, scratch):
            items.append({'kind': rule['kind'], 'pattern': rule['pattern'], 'root_cause': rule['message'],
                          'path': h['path'], 'snippet': h['snippet'], 'source': f'rule:{rule["id"]}',
                          'outcome': 'candidate'})
    return items


def refresh_health(conn, cfg):
    turned_off = []
    for row in conn.execute('SELECT * FROM rules').fetchall():
        rows = conn.execute('SELECT outcome FROM findings WHERE source = ?', (f'rule:{row["id"]}',)).fetchall()
        outcomes = [r['outcome'] for r in rows]
        decided = sum(1 for o in outcomes if o in ('merged', 'closed', 'reverted')
                      or (o.startswith('dropped:') and o not in NEUTRAL_DROPS))
        merged = outcomes.count('merged')
        fps = outcomes.count('dropped:false-positive')
        off = (row['active'] and decided >= cfg['rule_min_decided']
               and merged / decided < cfg['rule_min_precision'])
        conn.execute('UPDATE rules SET hits = ?, decided = ?, merged = ?, false_positives = ?, active = ? WHERE id = ?',
                     (len(outcomes), decided, merged, fps, 0 if off else row['active'], row['id']))
        if off:
            turned_off.append(row['id'])
    conn.commit()
    return turned_off


def candidates(conn, cfg):
    have = {r['pattern'] for r in conn.execute('SELECT pattern FROM rules')}
    out = []
    for r in conn.execute("SELECT kind, pattern, SUM(outcome = 'merged' AND source != 'seed') AS merged, "
                          "SUM(source = 'seed' AND outcome = 'seed') AS seeds FROM findings "
                          'WHERE pattern IS NOT NULL GROUP BY kind, pattern ORDER BY merged DESC, seeds DESC'):
        if r['pattern'] in have:
            continue
        if r['merged'] < cfg['rule_from_merged'] and r['seeds'] < cfg['rule_from_seeds']:
            continue
        src = conn.execute("SELECT id, path, snippet, root_cause FROM findings WHERE pattern = ? AND "
                           "outcome IN ('merged', 'seed') ORDER BY id", (r['pattern'],)).fetchall()
        out.append({'kind': r['kind'], 'pattern': r['pattern'], 'merged': r['merged'], 'seeds': r['seeds'],
                    'sources': [dict(s) for s in src]})
    return out


def listing(conn):
    return conn.execute('SELECT * FROM rules ORDER BY active DESC, id').fetchall()
