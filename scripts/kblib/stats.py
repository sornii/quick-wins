"""How each round went, and how often each bug kind ends up merged."""
from collections import defaultdict

SHIPPED = ('shipped', 'merged', 'closed', 'reverted')
NEUTRAL_DROPS = ('dropped:duplicate', 'dropped:in-open-pr')


def _dropped_at(rows, phase):
    return sum(1 for r in rows if r['outcome'].startswith('dropped:') and r['outcome_phase'] == phase)


def round_stats(conn, rounds):
    out = []
    for rd in conn.execute('SELECT * FROM rounds ORDER BY id DESC LIMIT ?', (rounds,)).fetchall()[::-1]:
        rows = conn.execute('SELECT * FROM findings WHERE round_id = ?', (rd['id'],)).fetchall()
        n = len(rows)
        unverified = sum(1 for r in rows if r['outcome'] == 'candidate')
        after_1 = n - unverified - _dropped_at(rows, '1')
        after_2 = after_1 - _dropped_at(rows, '2')
        after_3 = after_2 - _dropped_at(rows, '3')
        after_35 = after_3 - _dropped_at(rows, '3.5')
        by_kind, by_source = defaultdict(lambda: {'candidates': 0, 'merged': 0}), \
            defaultdict(lambda: {'candidates': 0, 'merged': 0})
        for r in rows:
            for bucket, key in ((by_kind, r['kind']), (by_source, r['source'] or '?')):
                bucket[key]['candidates'] += 1
                bucket[key]['merged'] += r['outcome'] == 'merged'
        out.append({
            'round': rd['id'], 'started': rd['started'], 'memory': rd['memory'], 'candidates': n,
            'after_1': after_1, 'after_2': after_2, 'after_3': after_3, 'after_3.5': after_35,
            'shipped': sum(1 for r in rows if r['outcome'] in SHIPPED),
            'merged': sum(1 for r in rows if r['outcome'] == 'merged'),
            'closed': sum(1 for r in rows if r['outcome'] == 'closed'),
            'reverted': sum(1 for r in rows if r['outcome'] == 'reverted'),
            'pending': sum(1 for r in rows if r['outcome'] == 'shipped'),
            'by_kind': dict(by_kind), 'by_source': dict(by_source),
        })
    return out


def format_stats(rows):
    if not rows:
        return '(no rounds yet)\n'
    head = ('round', 'memory', 'cand', 'after 1', 'after 3', 'after 3.5', 'shipped', 'merged', 'closed', 'reverted',
            'pending')
    lines = [' | '.join(head)]
    for r in rows:
        lines.append(' | '.join(str(v) for v in (r['round'], 'memory ' + r['memory'], r['candidates'], r['after_1'],
                                                  r['after_3'], r['after_3.5'], r['shipped'], r['merged'], r['closed'],
                                                  r['reverted'], r['pending'])))
    last = rows[-1]
    for title, key in (('by kind', 'by_kind'), ('by source', 'by_source')):
        parts = [f'{k} {v["merged"]}/{v["candidates"]}' for k, v in sorted(last[key].items())]
        if parts:
            lines.append(f'round {last["round"]} {title} (merged/candidates): ' + ', '.join(parts))
    return '\n'.join(lines) + '\n'


def kind_rates(conn, cfg):
    """{kind: (rate, decided)} with rate = (merged + 1) / (decided + 2); see the spec's Steering section."""
    w = cfg['seed_weight']
    merged, decided = defaultdict(float), defaultdict(float)
    for r in conn.execute('SELECT kind, outcome, source, changed_in_review FROM findings'):
        weight = w if r['source'] == 'seed' else 1.0
        o = r['outcome']
        if o == 'seed':
            merged[r['kind']] += weight
            decided[r['kind']] += weight
        elif o == 'merged':
            merged[r['kind']] += weight * (0.5 if r['changed_in_review'] else 1.0)
            decided[r['kind']] += weight
        elif o == 'reverted':
            decided[r['kind']] += 2 * weight
        elif o == 'closed' or (o.startswith('dropped:') and o not in NEUTRAL_DROPS):
            decided[r['kind']] += weight
    return {k: ((merged[k] + 1) / (decided[k] + 2), decided[k]) for k in decided}
