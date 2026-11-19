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
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kblib import findings, store  # noqa: E402


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
    sha = findings.head_sha(args.repo)
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
    try:
        return args.func(args) or 0
    except (store.KbError, LookupError, ValueError) as e:
        msg = e.args[0] if isinstance(e, LookupError) and e.args else e
        print(f'kb.py {args.command}: {msg}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
