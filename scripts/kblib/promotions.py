"""Lessons proven enough to propose beyond the skill: a repo lint rule, a docs edit, a skill update.

Only proposals: nothing here publishes or commits anything.
"""
from collections import defaultdict

from kblib import rules
from kblib.stats import NEUTRAL_DROPS

MIN_PER_ROUND = 3  # a source or area needs this many candidates in a round for its noise to count


def _recent_rounds(conn, n):
    return [r['id'] for r in conn.execute('SELECT id FROM rounds ORDER BY id DESC LIMIT ?', (n,))]


def _repo_rules(conn, cfg):
    out = []
    recent = _recent_rounds(conn, cfg['promote_rule_clean_rounds'])
    for r in conn.execute('SELECT * FROM rules WHERE active = 1 ORDER BY id').fetchall():
        if r['hits'] < cfg['promote_rule_hits'] or not r['decided']:
            continue
        share = r['merged'] / r['decided']
        if share < cfg['promote_rule_merged']:
            continue
        fp = conn.execute(f"SELECT COUNT(*) FROM findings WHERE source = ? AND outcome = 'dropped:false-positive' "
                          f'AND round_id IN ({", ".join("?" * len(recent))})',
                          (f'rule:{r["id"]}', *recent)).fetchone()[0] if recent else 0
        if fp:
            continue
        out.append({'step': 'repo-rule', 'subject': r['id'],
                    'evidence': f'{r["kind"]}: {r["hits"]} hits, {r["merged"]} of {r["decided"]} decided merged, '
                                f'no false positive in the last {len(recent)} rounds; rule file {r["file"]}'})
    return out


def _repo_docs(conn, cfg):
    have = {r['pattern'] for r in conn.execute('SELECT pattern FROM rules')}
    out = []
    for r in conn.execute("SELECT kind, pattern, COUNT(*) AS n FROM findings WHERE outcome = 'dropped:false-positive' "
                          'AND pattern IS NOT NULL AND superseded_by IS NULL GROUP BY kind, pattern '
                          'ORDER BY n DESC, pattern'):
        if r['n'] < cfg['promote_doc_drops'] or r['pattern'] in have:
            continue
        reasons = [x[0] for x in conn.execute(
            "SELECT DISTINCT reason FROM findings WHERE pattern = ? AND outcome = 'dropped:false-positive' "
            'AND reason IS NOT NULL ORDER BY id DESC LIMIT 3', (r['pattern'],))]
        out.append({'step': 'repo-docs', 'subject': r['pattern'],
                    'evidence': f'{r["n"]} candidates dropped as not bugs: ' + ' | '.join(reasons)})
    return out


def _skill_updates(conn, cfg):
    rounds = _recent_rounds(conn, cfg['promote_skill_rounds'])
    if len(rounds) < cfg['promote_skill_rounds']:
        return []
    noisy_in = defaultdict(int)
    for rid in rounds:
        counts = defaultdict(lambda: [0, 0])
        for f in conn.execute('SELECT source, area, outcome FROM findings WHERE round_id = ?', (rid,)):
            noise = f['outcome'].startswith('dropped:') and f['outcome'] not in NEUTRAL_DROPS
            for key in (f'source {f["source"]}', f'area {f["area"]}' if f['area'] else None):
                if key:
                    counts[key][0] += 1
                    counts[key][1] += noise
        for key, (n, noise) in counts.items():
            if n >= MIN_PER_ROUND and noise / n >= cfg['promote_skill_noise']:
                noisy_in[key] += 1
    return [{'step': 'skill-update', 'subject': key,
             'evidence': f'{int(cfg["promote_skill_noise"] * 100)}%+ of its candidates dropped in each of the last '
                         f'{len(rounds)} rounds'}
            for key, n in sorted(noisy_in.items()) if n == len(rounds)]


def promotions(conn, cfg):
    rules.refresh_health(conn, cfg)
    return _repo_rules(conn, cfg) + _repo_docs(conn, cfg) + _skill_updates(conn, cfg)


def format_promotions(items):
    if not items:
        return '(nothing to propose)\n'
    titles = {'repo-rule': 'Propose a lint rule for the repository',
              'repo-docs': "Propose a note in the repository's conventions doc",
              'skill-update': "Propose a change to this skill's instructions"}
    out = []
    for step in ('repo-rule', 'repo-docs', 'skill-update'):
        group = [p for p in items if p['step'] == step]
        if group:
            out.append(f'## {titles[step]}')
            out += [f'- {p["subject"]}: {p["evidence"]}' for p in group]
            out.append('')
    return '\n'.join(out)
