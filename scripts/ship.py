"""Publish the fixes: push each branch and open its PR, exactly as the spec lists them.

Usage:
  python3 -I ship.py <spec.json> create-issues            # only when the repository pairs each PR with an issue
  python3 -I ship.py <spec.json> ship [NN ...]             # rename branch and amend subject where listed, push, open PR
  python3 -I ship.py <spec.json> ship --dry-run [NN ...]   # print what would run; changes nothing

This script has no format of its own and nothing to configure. The spec is the list of what will
be published, written for this run from what phase 0 learned in the repository (its written rules,
and the person's own recent commits and pull requests there; see conventions.sh). The PR body is
the description written in phase 2 with the repository's own PR template,
<pr_desc_dir>/PR-NN-<slug>.md, without its first "# title" line. A key that is left out means
"leave that alone": no label, no reviewer, no rename, no issue.

spec.json:
{
  "repo": "org/name",                          # gh -R target
  "base": "main",                              # PR base branch
  "worktrees": "/abs/path/to/worktrees",
  "pr_desc_dir": "/tmp/quick-wins/<repo>",     # where PR-NN-<slug>.md and evidence/ live
  "assignee": "@me",                           # as the person's own PRs there are assigned, if they are
  "reviewers": ["login", "org/team"],          # who their PRs there ask, if anyone
  "labels": ["bug"],                           # labels their PRs there carry, if any
  "draft": false,
  "fixes": [
    {"n": "01", "slug": "...",
     "issue": null,                            # null or absent: no issue. A number: an existing one
     "new_issue": {"title": "...", "body": "...", "labels": []},   # create-issues opens it and records "issue"
     "branch": "fix/{issue}-<slug>",           # rename the branch to this before pushing; absent: keep it
     "subject": "#{issue} fix: ...",           # amend the commit subject to this; absent: keep it
     "title": "...",                           # PR title; absent: the commit subject as pushed
     "closing": "Closes #{issue}",             # first line of the body, unless the body names the issue already
     "attach": []}                             # extra files. evidence/NN-<slug>-* images named in the body
  ]                                            #   are attached by themselves
}
"{issue}" in branch, subject, title and closing becomes the fix's issue number.
The spec is rewritten after every created issue / PR, so re-running is safe.
"""
import glob
import json
import os
import re
import shlex
import subprocess
import sys

DRY = False
MEDIA = ('.png', '.jpg', '.jpeg', '.gif', '.webm', '.mp4')


def run(args, cwd=None, env=None):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, check=True).stdout.strip()


def act(args, cwd=None, env=None, pretend=''):
    """Something that changes the repo or GitHub: printed instead of run with --dry-run."""
    if DRY:
        print('  would run:', ' '.join(shlex.quote(a if len(a) < 200 else a[:197] + '...') for a in args))
        return pretend
    return run(args, cwd=cwd, env=env)


def save(path, spec):
    if DRY:
        return
    with open(path, 'w') as f:
        json.dump(spec, f, indent=1)


def create_issues(path, spec):
    for fx in spec['fixes']:
        new = fx.get('new_issue')
        if fx.get('issue') or not new:
            continue
        args = ['gh', 'issue', 'create', '-R', spec['repo'], '--title', new['title'], '--body', new.get('body', '')]
        if spec.get('assignee'):
            args += ['--assignee', spec['assignee']]
        if new.get('labels'):
            args += ['--label', ','.join(new['labels'])]
        url = act(args, pretend='https://github.com/%s/issues/0' % spec['repo'])
        fx['issue'] = int(url.rsplit('/', 1)[1])
        save(path, spec)
        print(fx['n'], fx['issue'], url)


def stated(fx, key):
    """A value from the spec, with the issue number put in."""
    value = fx.get(key)
    if not value:
        return None
    if '{issue}' in value and not fx.get('issue'):
        sys.exit(f"{fx['n']} {fx['slug']}: \"{key}\" names {{issue}} but the fix has no issue")
    return value.replace('{issue}', str(fx.get('issue')))


def description(spec, fx):
    """The PR body: the phase 2 description without its "# title" line."""
    local = os.path.join(spec.get('pr_desc_dir', '.'), f"PR-{fx['n']}-{fx['slug']}.md")
    if not os.path.isfile(local):
        sys.exit(f"{fx['n']} {fx['slug']}: {local} is missing; the PR body is read from it")
    return re.sub(r'\A\s*# [^\n]*\n+', '', open(local).read(), count=1).strip() + '\n'


def attachments(spec, fx, body):
    """Files to upload, and the body with each reference turned into the path given to gh."""
    files = list(fx.get('attach', []))
    evidence = os.path.join(spec.get('pr_desc_dir', '.'), 'evidence')
    for path in sorted(glob.glob(os.path.join(evidence, f"{fx['n']}-{fx['slug']}-*"))):
        if os.path.basename(path) in body and path.endswith(MEDIA) and path not in files:
            files.append(path)
    for path in files:
        name = re.escape(os.path.basename(path))
        body = re.sub(r'\]\((?:\./)?' + name + r'\)', lambda _: '](' + path + ')', body)
    return files, body


def ship(path, spec, only):
    for fx in spec['fixes']:
        if only and fx['n'] not in only:
            continue
        if fx.get('pr'):
            print(fx['n'], 'already shipped', fx['pr'])
            continue
        wt = os.path.join(spec['worktrees'], fx['slug'])
        issue = fx.get('issue')

        subject = run(['git', 'log', '-1', '--format=%s'], cwd=wt)
        wanted = stated(fx, 'subject')
        if wanted and wanted != subject:
            subject = wanted
            # Message-only amend: the code in this commit already passed the hooks.
            act(['git', 'commit', '-q', '--amend', '-m', subject], cwd=wt, env=dict(os.environ, LEFTHOOK='0', HUSKY='0'))

        current = run(['git', 'branch', '--show-current'], cwd=wt)
        branch = stated(fx, 'branch') or current
        if current != branch:
            act(['git', 'branch', '-m', current, branch], cwd=wt)

        title = stated(fx, 'title') or subject
        body = description(spec, fx)
        closing = stated(fx, 'closing')
        if closing and not (issue and f'#{issue}' in body):
            body = closing + '\n\n' + body
        files, body = attachments(spec, fx, body)

        act(['git', 'push', '-q', '-u', 'origin', branch], cwd=wt)
        args = ['gh', 'pr', 'create', '-R', spec['repo'], '--base', spec['base'], '--head', branch, '--title', title, '--body', body]
        if spec.get('assignee'):
            args += ['--assignee', spec['assignee']]
        if spec.get('reviewers'):
            args += ['--reviewer', ','.join(spec['reviewers'])]
        if spec.get('labels'):
            args += ['--label', ','.join(spec['labels'])]
        if spec.get('draft'):
            args += ['--draft']
        for each in files:
            args += ['--attach', each]
        url = act(args, cwd=wt, pretend='(not opened)')
        if not DRY:
            fx['pr'] = url
        save(path, spec)
        print(fx['n'], branch, '|', title, '|', url)


if __name__ == '__main__':
    spec_path, cmd, *rest = sys.argv[1:]
    if '--dry-run' in rest:
        DRY = True
        rest.remove('--dry-run')
    data = json.load(open(spec_path))
    if cmd == 'create-issues':
        create_issues(spec_path, data)
    elif cmd == 'ship':
        ship(spec_path, data, set(rest))
    else:
        sys.exit(f'unknown command {cmd}')
