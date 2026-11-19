#!/usr/bin/env python3
"""Refuse to call a fix "verified" without its pictures.

Usage:
  python3 -I check_evidence.py <scratch-dir>
      End of phase 3.5, and again before the phase 5 stop.
  python3 -I check_evidence.py <scratch-dir> --published [-R org/name] [NN=<pr number or url> ...]
      Before phase 6 deletes the scratch directory, which holds the only copies.

Reads <scratch-dir>/evidence/checks.tsv, one line per fix that survived phase 3:
  NN  slug  tag  outcome  reason...
  tag:      ui | http | none          the finding's `verify` tag (kb.py show NN)
  outcome:  verified                  bug seen on the base and gone with the fix, as a user
            not-verified <reason>     anything else: not reproduced, can't be reached as a user,
                                      app not run, a capture that failed
            skipped                   only for tag none
  Blank lines and lines starting with # are ignored.

Before publishing, for every fix:
  - it has a row, and every row has its PR-NN-<slug>.md in <scratch-dir>
  - verified ui:    evidence/NN-<slug>-before.png and -after.png exist, differ, and were
                    written by annotate.py (it marks the file)
  - verified http:  evidence/NN-<slug>-before.txt and -after.txt exist and are not empty
  - verified:       PR-NN-<slug>.md names both files, so the upload puts them in the PR
  - anything else:  PR-NN-<slug>.md does not say it was verified, checked or tested in a browser

With --published, each verified ui fix's PR body is read from GitHub and must hold two uploaded
images. PR urls come from <scratch-dir>/ship.json; pass NN=<pr> for PRs opened another way
(a bare number needs -R).

Exits 1 listing every problem, or 0 with one line per fix.
"""
import glob
import json
import os
import re
import subprocess
import sys

TAGS = ('ui', 'http', 'none')
# "checked in a browser" and the like, unless it is being denied ("not checked in a browser").
CLAIM = re.compile(r"(?<!not )(?<!n't )(?<!never )\b(verified|checked|confirmed|tested|reproduced)\b[^.\n]{0,60}?\bin (a|the) (real |headless )?browser\b", re.I)
IMAGE = re.compile(r'!\[[^\]]*\]\(https://[^)\s]+\)|<img[^>]+src="https://')


def rows_of(path):
    rows = []
    for number, line in enumerate(open(path), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split(None, 4)
        if len(parts) < 4:
            sys.exit(f'{path}:{number}: expected "NN slug tag outcome [reason]", got: {line}')
        n, slug, tag, outcome = parts[:4]
        rows.append({'n': n, 'slug': slug, 'tag': tag, 'outcome': outcome, 'reason': parts[4] if len(parts) > 4 else ''})
    return rows


def annotated(path):
    # annotate.py writes a PNG text chunk named "annotation"; a raw screenshot has none.
    with open(path, 'rb') as f:
        return b'annotation\x00' in f.read()


def before_publishing(scratch, rows, spec):
    problems = []
    evidence = os.path.join(scratch, 'evidence')
    described = {}
    for path in glob.glob(os.path.join(scratch, 'PR-*-*.md')):
        found = re.fullmatch(r'PR-(\d+)-(.+)\.md', os.path.basename(path))
        if found:
            described[found.group(1)] = (found.group(2), path)
    listed = {row['n'] for row in rows}
    for n, (slug, _) in sorted(described.items()):
        if n not in listed:
            problems.append(f'{n} {slug}: has a PR description but no row in evidence/checks.tsv')

    for row in rows:
        name = f"{row['n']} {row['slug']}"
        if row['tag'] not in TAGS:
            problems.append(f"{name}: tag {row['tag']!r} is not one of {', '.join(TAGS)}")
            continue
        allowed = ('skipped', 'not-verified') if row['tag'] == 'none' else ('verified', 'not-verified')
        if row['outcome'] not in allowed:
            problems.append(f"{name}: outcome {row['outcome']!r} is not one of {', '.join(allowed)} for tag {row['tag']}")
            continue
        if row['outcome'] == 'not-verified' and not row['reason']:
            problems.append(f'{name}: not-verified needs its reason on the same line')
        entry = described.get(row['n'])
        if not entry or entry[0] != row['slug']:
            problems.append(f"{name}: no PR-{row['n']}-{row['slug']}.md in {scratch}")
            continue
        text = open(entry[1]).read()

        if row['outcome'] != 'verified':
            claim = CLAIM.search(text)
            if claim:
                problems.append(f'{name}: its PR text says "{claim.group(0)}" but the fix is {row["outcome"]}; say it was not checked in a browser')
            continue

        ext = 'png' if row['tag'] == 'ui' else 'txt'
        files = [f"{row['n']}-{row['slug']}-{when}.{ext}" for when in ('before', 'after')]
        paths = [os.path.join(evidence, each) for each in files]
        missing = [each for each, path in zip(files, paths) if not os.path.isfile(path) or os.path.getsize(path) == 0]
        if missing:
            problems.append(f"{name}: marked verified, but evidence/{' and evidence/'.join(missing)} {'is' if len(missing) == 1 else 'are'} missing or empty; take it again, or mark the fix not-verified")
            continue
        if row['tag'] == 'ui':
            for each, path in zip(files, paths):
                if not annotated(path):
                    problems.append(f'{name}: evidence/{each} was not written by annotate.py (no box, arrow and label)')
            if open(paths[0], 'rb').read() == open(paths[1], 'rb').read():
                problems.append(f'{name}: the before and after pictures are the same file')
        for each in files:
            if each not in text:
                problems.append(f"{name}: PR-{row['n']}-{row['slug']}.md does not show {each} (a Before/After table with both)")
    return problems


def published(rows, spec, repo, given):
    problems = []
    prs = {str(fx.get('n')): fx.get('pr') for fx in (spec or {}).get('fixes', []) if fx.get('pr')}
    prs.update(given)
    for row in rows:
        if row['outcome'] != 'verified' or row['tag'] != 'ui':
            continue
        name = f"{row['n']} {row['slug']}"
        pr = prs.get(row['n'])
        if not pr:
            problems.append(f"{name}: no PR known for it; pass {row['n']}=<pr number or url>")
            continue
        args = ['gh', 'pr', 'view', str(pr), '--json', 'body', '--jq', '.body']
        if repo and not str(pr).startswith('http'):
            args += ['-R', repo]
        done = subprocess.run(args, capture_output=True, text=True)
        if done.returncode:
            problems.append(f'{name}: could not read PR {pr}: {done.stderr.strip().splitlines()[-1] if done.stderr.strip() else "gh failed"}')
            continue
        count = len(IMAGE.findall(done.stdout))
        if count < 2:
            problems.append(f'{name}: PR {pr} shows {count} uploaded image(s), not its before and after; add them with `gh pr edit --attach` before deleting the evidence')
    return problems


def main():
    args = sys.argv[1:]
    if not args or args[0] in ('-h', '--help'):
        sys.exit(__doc__)
    scratch = args.pop(0)
    after = '--published' in args
    if after:
        args.remove('--published')
    repo = None
    if '-R' in args:
        at = args.index('-R')
        if at + 1 >= len(args):
            sys.exit('-R needs org/name')
        repo = args[at + 1]
        del args[at:at + 2]
    given = dict(each.split('=', 1) for each in args if '=' in each)

    manifest = os.path.join(scratch, 'evidence', 'checks.tsv')
    if not os.path.isfile(manifest):
        sys.exit(f'{manifest} is missing: write one line per fix (NN slug tag outcome reason); see the header of this script')
    rows = rows_of(manifest)
    spec_path = os.path.join(scratch, 'ship.json')
    spec = json.load(open(spec_path)) if os.path.isfile(spec_path) else None

    problems = published(rows, spec, repo or (spec or {}).get('repo'), given) if after else before_publishing(scratch, rows, spec)
    if problems:
        print(f"{len(problems)} problem(s) with the evidence:", file=sys.stderr)
        for each in problems:
            print(f'  - {each}', file=sys.stderr)
        sys.exit(1)
    for row in rows:
        print(f"{row['n']} {row['slug']}: {row['tag']}, {row['outcome']}{' (' + row['reason'] + ')' if row['reason'] else ''}")
    print('evidence is in the PRs' if after else 'evidence is complete')


if __name__ == '__main__':
    main()
