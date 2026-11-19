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
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kblib import store  # noqa: E402


def cmd_init(args):
    store.connect(args.repo).close()
    print(store.data_dir(args.repo))


def build_parser():
    p = argparse.ArgumentParser(prog='kb.py', description=__doc__.split('\n')[0])
    p.add_argument('-C', dest='repo', default='.', help='repository (any worktree of it); default: .')
    sub = p.add_subparsers(dest='command', required=True)

    s = sub.add_parser('init', help='create or migrate the database; print its folder')
    s.set_defaults(func=cmd_init)
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
