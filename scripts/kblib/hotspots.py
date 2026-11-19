"""Where code changes most, and how this round's hunters are split between those files and exploring."""
import json
import math
import re
import subprocess
from collections import defaultdict

from kblib import findings, seed, stats

SWEEP = re.compile(r'(?i)\b(prettier|eslint\s+--fix|format(ting)?|reformat|lint(ing)?\s+fix(es)?|codemod)\b')
REAL_BUGS = ('seed', 'confirmed', 'fixed', 'shipped', 'merged', 'reverted', 'closed')
# Not where a sweep fixes bugs: docs, and tests (which repositories often only let you touch when asked).
NOT_HUNTED = ('*.md', 'docs/*', 'doc/*', 'tests/*', 'test/*', '*/tests/*', '*/test/*', '*/__tests__/*',
              '*.test.*', '*.spec.*', '*.tests.*', '*.test', 'e2e/*', '*/e2e/*')


def _tracked(repo_dir, base):
    r = subprocess.run(['git', '-c', 'core.quotePath=false', '-C', repo_dir, 'ls-tree', '-r', '--name-only',
                        f'origin/{base}'],
                       capture_output=True, text=True)
    return set(r.stdout.splitlines())


def hotspots(conn, repo_dir, base, cfg, top=20):
    lines = defaultdict(int)
    for _sha, subject, files in seed.commits_with_numstat(repo_dir, f'origin/{base}',
                                                         (f'--since={cfg["hot_window_days"]} days ago',)):
        if len(files) > cfg['sweep_files'] or SWEEP.search(subject):
            continue
        drop = seed.excluded_paths([p for _, _, p in files])
        for a, d, p in files:
            if p not in drop and '=>' not in p:
                lines[p] += a + d
    tracked = _tracked(repo_dir, base)
    lines = {p: n for p, n in lines.items() if p in tracked and n > 0 and not seed._matches(p, NOT_HUNTED)}
    if not lines:
        return []
    bugs = defaultdict(int)
    for r in conn.execute(f'SELECT path, COUNT(*) AS n FROM findings WHERE outcome IN '
                          f'({", ".join("?" * len(REAL_BUGS))}) GROUP BY path', REAL_BUGS):
        bugs[r['path']] = r['n']
    max_lines = max(lines.values())
    max_bugs = max([bugs[p] for p in lines] + [1])
    scored = [(p, round((n / max_lines) * (1 + bugs[p] / max_bugs), 4)) for p, n in lines.items()]
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored[:top]


def _last_explored(conn, round_id, areas):
    last = {a: -1 for a in areas}
    for r in conn.execute('SELECT id, areas_explored FROM rounds WHERE id != ? AND areas_explored IS NOT NULL',
                          (round_id,)):
        for a in json.loads(r['areas_explored']):
            if a in last:
                last[a] = max(last[a], r['id'])
    return last


def plan_hunters(conn, cfg, round_id, areas, hot, kinds=None):
    """{"explore": [area], "hot": [[path]], "kind": [[kind]]}; records the explored areas on the round."""
    n = cfg['hunters']
    rd = conn.execute('SELECT memory FROM rounds WHERE id = ?', (round_id,)).fetchone()
    if rd is None:
        raise LookupError(f'no round {round_id}')
    if rd['memory'] == 'off':
        plan = {'explore': list(areas)[:max(n, 1)], 'hot': [], 'kind': []}
    else:
        explore_n = max(1, math.ceil(n * cfg['explore_share']))
        rest = n - explore_n
        kinds = list(kinds or [])
        kind_n = min(len(kinds), rest // 2 if hot else rest) if kinds else 0
        hot_n = min(len(hot), rest - kind_n) if hot else 0
        explore_n = n - kind_n - hot_n
        last = _last_explored(conn, round_id, areas)
        order = sorted(areas, key=lambda a: (last[a], areas.index(a)))
        hot_lists = [hot[i::hot_n] for i in range(hot_n)] if hot_n else []
        top = kinds[:kind_n * 2]
        kind_lists = [top[i::kind_n] for i in range(kind_n)] if kind_n else []
        plan = {'explore': order[:explore_n], 'hot': hot_lists, 'kind': kind_lists}
    conn.execute('UPDATE rounds SET areas_explored = ? WHERE id = ?', (json.dumps(plan['explore']), round_id))
    conn.commit()
    return plan


def plan_for_round(conn, cfg, round_id, areas, hot):
    """The plan with kind-focused hunters steered by merge rate (stats.ranked_kinds)."""
    return plan_hunters(conn, cfg, round_id, areas, hot, stats.ranked_kinds(conn, cfg))


def round_areas(conn):
    r = findings.current_round(conn)
    return json.loads(r['areas']) if r and r['areas'] else []
