#!/usr/bin/env python3
"""The findings database: every finding a sweep makes in a repository, and what became of it.

Usage: python3 -I kb.py [-C <repo-dir>] <command> ...   (run `kb.py <command> --help` for each)

One SQLite file per repository, outside it, kept between runs:
  ~/.local/share/quick-wins/<host_owner_name>/kb.db   (+ config.json, rules/)
KB_DATA_DIR replaces ~/.local/share/quick-wins (tests use it). Settings: config.json, keys as in
kblib/store.py DEFAULTS. Memory is evidence, never a verdict: only an exact `strong` match skips a
candidate; everything else is re-checked against the current code.
"""
import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kblib import brief, check, findings, hotspots, ingest, promotions, rules, seed, stats, store, sync  # noqa: E402


def read_json(path):
    try:
        with (sys.stdin if path == '-' else open(path)) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise store.KbError(f'{path}: {e}')


def need_round(conn):
    r = findings.current_round(conn)
    if not r:
        raise store.KbError('no round yet; run `kb.py round start` first')
    return r


def cmd_init(args):
    store.connect(args.repo).close()
    print(store.data_dir(args.repo))


def cmd_round_start(args):
    conn = store.connect(args.repo)
    areas = [a.strip() for a in args.areas.split(',') if a.strip()] if args.areas else []
    sha = findings.head_sha(args.repo, f'origin/{args.base}') or findings.head_sha(args.repo)
    rid = findings.start_round(conn, args.base, sha, args.memory, store.load_config(args.repo), areas)
    print(f'round {rid} (memory {args.memory})')


def cmd_add(args):
    conn = store.connect(args.repo)
    items = read_json(args.items)
    if not isinstance(items, list):
        raise ValueError('expected a JSON list of findings')
    ids = findings.add_findings(conn, args.repo, need_round(conn)['id'], items)
    print(json.dumps(ids))


def cmd_update(args):
    conn = store.connect(args.repo)
    fields = {k: v for k, v in {
        'outcome': args.outcome, 'outcome_phase': args.phase, 'reason': args.reason,
        'fix_diff_from': args.fix_diff_from, 'introduced_in': args.introduced_in, 'nn': args.nn,
        'slug': args.slug, 'verify': args.verify, 'in_batch': args.in_batch, 'pattern': args.pattern,
        'kind': args.kind,
    }.items() if v is not None}
    findings.update_finding(conn, args.ref, **fields)


def cmd_check(args):
    conn = store.connect(args.repo)
    need_round(conn)
    items = read_json(args.candidates)
    if not isinstance(items, list):
        raise ValueError('expected a JSON list of candidates')
    results = check.check_candidates(conn, args.repo, items, findings.memory_on(conn))
    for i, r in enumerate(results):
        print(json.dumps({'i': i, **r}))
    print(check.summary(results))


def cmd_import_checks(args):
    print(ingest.import_checks(store.connect(args.repo), args.tsv))


def cmd_import_ship(args):
    print(ingest.import_ship(store.connect(args.repo), args.spec))


def cmd_sync(args):
    print(json.dumps(sync.sync(store.connect(args.repo), args.repo, args.base)))


def cmd_seed(args):
    conn = store.connect(args.repo)
    if args.action == 'prepare':
        n = seed.prepare(conn, args.repo, args.base, args.scratch, args.fix_regex)
        print(f'{n} batch file(s) in {os.path.join(args.scratch, "seed")}' + (' (see README.txt there)' if n else ''))
    elif args.action == 'load':
        print(f'{seed.load(conn, args.repo, args.scratch)} seeds added')
    else:
        print(seed.status(conn, args.repo, args.scratch))


def cmd_stats(args):
    print(stats.format_stats(stats.round_stats(store.connect(args.repo), args.rounds)), end='')


def hot_paths(path):
    if not path:
        return None
    data = read_json(path)
    return [p[0] if isinstance(p, list) else p for p in data]


def cmd_brief(args):
    conn = store.connect(args.repo)
    cfg = store.load_config(args.repo)
    kinds = stats.ranked_kinds(conn, cfg)[:5] if args.role == 'hunter' else None
    print(brief.brief(conn, args.repo, cfg, args.role, hot_paths(args.hot_from), kinds), end='')


def cmd_supersede(args):
    brief.supersede(store.connect(args.repo), args.ref, args.by)


def cmd_hotspots(args):
    conn = store.connect(args.repo)
    print(json.dumps(hotspots.hotspots(conn, args.repo, args.base, store.load_config(args.repo), args.top)))


def cmd_hunters(args):
    conn = store.connect(args.repo)
    rd = need_round(conn)
    areas = [a.strip() for a in args.areas.split(',') if a.strip()] if args.areas else hotspots.round_areas(conn)
    if not areas:
        raise ValueError('no areas: pass --areas, or give them to `round start`')
    plan = hotspots.plan_for_round(conn, store.load_config(args.repo), rd['id'], areas, hot_paths(args.hot_from) or [])
    print(json.dumps(plan, indent=1))


def cmd_rules(args):
    conn = store.connect(args.repo)
    cfg = store.load_config(args.repo)
    if args.action in ('add', 'test', 'run') and (not args.target or not args.scratch):
        raise ValueError(f'rules {args.action} needs a {"checkout" if args.action == "run" else "file"} and --scratch')
    if args.action == 'candidates':
        print(json.dumps(rules.candidates(conn, cfg), indent=1))
    elif args.action == 'test':
        errors = rules.test_rule(conn, rules.load(args.target), args.scratch)
        print('\n'.join(errors) or 'ok')
        return 1 if errors else 0
    elif args.action == 'add':
        print(rules.add_rule(conn, args.repo, args.target, args.scratch))
    elif args.action == 'run':
        items = rules.run_rules(conn, args.repo, os.path.abspath(args.target), args.scratch)
        out = os.path.join(args.scratch, 'rule-candidates.json')
        with open(out, 'w') as f:
            json.dump(items, f, indent=1)
        print(f'{len(items)} rule hit(s) in {out}')
    elif args.action == 'health':
        off = rules.refresh_health(conn, cfg)
        print('turned off: ' + ', '.join(off) if off else 'no rule turned off')
    else:
        for r in rules.listing(conn):
            state = 'on' if r['active'] else 'off'
            print(f'{r["id"]} [{state}] {r["kind"]}: hits {r["hits"]}, merged {r["merged"]}/{r["decided"]} decided')


def cmd_promotions(args):
    conn = store.connect(args.repo)
    print(promotions.format_promotions(promotions.promotions(conn, store.load_config(args.repo))), end='')


def cmd_list(args):
    conn = store.connect(args.repo)
    verify = set(args.verify.split(',')) if args.verify else None
    print(findings.format_list(findings.list_findings(conn, need_round(conn)['id'], args.runners_up, verify)), end='')


def cmd_show(args):
    print(findings.show(store.connect(args.repo), args.ref), end='')


def cmd_patterns(args):
    rows = findings.patterns(store.connect(args.repo), args.kind)
    print('\n'.join(f'{r["pattern"]} ({r["n"]})' for r in rows) or '(none yet)')


def cmd_kinds(args):
    conn = store.connect(args.repo)
    if args.action and not args.slug or args.action == 'map' and not args.existing:
        raise ValueError('usage: kinds approve <slug> | kinds map <slug> <existing kind>')
    if args.action == 'approve':
        findings.approve_kind(conn, args.slug)
    elif args.action == 'map':
        findings.map_kind(conn, args.slug, args.existing)
    elif args.pending:
        print('\n'.join(findings.kinds_pending(conn)) or '(none pending)')
    else:
        print('\n'.join(f'{r["slug"]}' + (' (pending)' if r['status'] == 'pending' else '') for r in findings.kinds(conn)))


def build_parser():
    p = argparse.ArgumentParser(prog='kb.py', description=__doc__.split('\n')[0])
    p.add_argument('-C', dest='repo', default='.', help='repository (any worktree of it); default: .')
    p.add_argument('--round', type=int, help='use this round instead of the latest (when another session may '
                                             'start a round in the same repository)')
    sub = p.add_subparsers(dest='command', required=True)

    s = sub.add_parser('init', help='create or migrate the database; print its folder')
    s.set_defaults(func=cmd_init)

    s = sub.add_parser('round', help='start a round (phase 0)')
    rs = s.add_subparsers(dest='action', required=True)
    s = rs.add_parser('start')
    s.add_argument('--base', required=True, help='the PR base branch')
    s.add_argument('--memory', choices=('on', 'off'), default='on')
    s.add_argument('--areas', help='comma-separated hunting areas of this repository')
    s.set_defaults(func=cmd_round_start)

    s = sub.add_parser('add', help='record findings (JSON list) in the current round; prints their ids')
    s.add_argument('items', help='JSON file, or - for stdin')
    s.set_defaults(func=cmd_add)

    s = sub.add_parser('update', help='change one finding (NN of this round, or #id)')
    s.add_argument('ref')
    s.add_argument('--outcome')
    s.add_argument('--phase')
    s.add_argument('--reason')
    s.add_argument('--fix-diff-from', metavar='WORKTREE')
    s.add_argument('--introduced-in', metavar='SHA')
    s.add_argument('--nn')
    s.add_argument('--slug')
    s.add_argument('--verify', choices=sorted(findings.VERIFY_TAGS))
    s.add_argument('--in-batch', type=int, choices=(0, 1))
    s.add_argument('--pattern')
    s.add_argument('--kind')
    s.set_defaults(func=cmd_update)

    s = sub.add_parser('check', help='which candidates are already known (strong = skip; weak = look at it)')
    s.add_argument('candidates', help='JSON list with path, snippet, root_cause; run with -C <the checkout hunted>')
    s.set_defaults(func=cmd_check)

    s = sub.add_parser('import-checks', help="record phase 3.5's evidence/checks.tsv; prints the count")
    s.add_argument('tsv')
    s.set_defaults(func=cmd_import_checks)

    s = sub.add_parser('import-ship', help='record the PRs ship.py opened (ship.json); prints the count')
    s.add_argument('spec')
    s.set_defaults(func=cmd_import_ship)

    s = sub.add_parser('sync', help="read every open PR's fate from GitHub (phase 0); prints counts")
    s.add_argument('--base', required=True, help='the PR base branch, to look for reverts on origin/<base>')
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser('seed', help="seed from the repository's small fix commits (phase 0)")
    s.add_argument('action', choices=('prepare', 'load', 'status'))
    s.add_argument('--base', help='prepare: the PR base branch')
    s.add_argument('--scratch', help='the run scratch directory; batches go in <scratch>/seed/')
    s.add_argument('--fix-regex', help="prepare: subjects that mark a fix (default: fix|bug|typo|hotfix words)")
    s.set_defaults(func=cmd_seed)

    s = sub.add_parser('stats', help='how the last rounds went (phase 0 update)')
    s.add_argument('--rounds', type=int, default=5)
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser('brief', help='memory lines to paste into a hunter or prosecutor prompt (empty when off)')
    s.add_argument('--role', choices=('hunter', 'prosecutor'), required=True)
    s.add_argument('--hot-from', metavar='FILE', help='JSON from `kb.py hotspots`, or a list of paths')
    s.set_defaults(func=cmd_brief)

    s = sub.add_parser('supersede', help='retire a lesson that stopped being true')
    s.add_argument('ref', help='#id (or NN of this round)')
    s.add_argument('--by', help='the finding that replaces it')
    s.set_defaults(func=cmd_supersede)

    s = sub.add_parser('hotspots', help='files that changed most lately, weighted by past bugs (JSON)')
    s.add_argument('--base', required=True)
    s.add_argument('--top', type=int, default=20)
    s.set_defaults(func=cmd_hotspots)

    s = sub.add_parser('hunters', help="this round's hunter plan: exploring areas, hot files, kinds (JSON)")
    s.add_argument('--areas', help="default: the areas given to `round start`")
    s.add_argument('--hot-from', metavar='FILE', help='JSON from `kb.py hotspots`')
    s.set_defaults(func=cmd_hunters)

    s = sub.add_parser('rules', help='private rules: candidates, test, add, run, health, list')
    s.add_argument('action', choices=('candidates', 'test', 'add', 'run', 'health', 'list'))
    s.add_argument('target', nargs='?', help='test/add: the rule file; run: the checkout to scan')
    s.add_argument('--scratch', help='the run scratch directory (rule hits and ast-grep cache go there)')
    s.set_defaults(func=cmd_rules)

    s = sub.add_parser('promotions', help='lessons proven enough to propose: repo rule, docs note, skill change')
    s.set_defaults(func=cmd_promotions)

    s = sub.add_parser('list', help="this round's findings, grouped by kind")
    s.add_argument('--runners-up', action='store_true')
    s.add_argument('--verify', help='only these tags, e.g. ui,http')
    s.set_defaults(func=cmd_list)

    s = sub.add_parser('show', help='one finding, as a fix agent needs it')
    s.add_argument('ref')
    s.set_defaults(func=cmd_show)

    s = sub.add_parser('patterns', help='root-cause patterns already used for a kind')
    s.add_argument('kind')
    s.set_defaults(func=cmd_patterns)

    s = sub.add_parser('kinds', help='the kind list; approve or map a pending one')
    s.add_argument('action', nargs='?', choices=('approve', 'map'))
    s.add_argument('slug', nargs='?')
    s.add_argument('existing', nargs='?')
    s.add_argument('--pending', action='store_true')
    s.set_defaults(func=cmd_kinds)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.repo = os.path.abspath(args.repo)
    findings.ROUND = args.round
    try:
        return args.func(args) or 0
    except (store.KbError, LookupError, ValueError, sqlite3.Error) as e:
        msg = e.args[0] if isinstance(e, LookupError) and e.args else e
        print(f'kb.py {args.command}: {msg}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
