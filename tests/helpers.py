"""Test helpers: throwaway git repos, a fake `gh`, and a temp data folder."""
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts')
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
KB = os.path.join(SCRIPTS, 'kb.py')


def git(repo, *args):
    return subprocess.run(['git', '-C', repo, *args], check=True, capture_output=True, text=True).stdout.strip()


def tempdir(test):
    d = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, d, True)
    return d


def make_repo(test, origin='git@github.com:org/name.git'):
    repo = os.path.join(tempdir(test), 'repo')
    os.makedirs(repo)
    subprocess.run(['git', 'init', '-q', '-b', 'main', repo], check=True)
    git(repo, 'config', 'user.email', 'test@example.com')
    git(repo, 'config', 'user.name', 'Test')
    git(repo, 'config', 'commit.gpgsign', 'false')
    if origin:
        git(repo, 'remote', 'add', 'origin', origin)
    commit(repo, 'README.md', 'hello\n', 'Initial commit')
    return repo


def commit(repo, path, text, subject, body=None):
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    if text is None:
        os.remove(full)
        git(repo, 'add', '-A', path)
    else:
        with open(full, 'w') as f:
            f.write(text)
        git(repo, 'add', path)
    msg = subject if body is None else subject + '\n\n' + body
    git(repo, 'commit', '-q', '--allow-empty', '-m', msg)
    return git(repo, 'rev-parse', 'HEAD')


def publish(repo, branch='main'):
    """Point origin/<branch> at HEAD, as if it had been pushed."""
    git(repo, 'update-ref', 'refs/remotes/origin/' + branch, 'HEAD')


FAKE_GH = '''#!/usr/bin/env python3
import json, os, sys
path = os.environ["FAKE_GH_FILE"]
data = json.load(open(path))
if data is None:
    sys.stderr.write("To get started with GitHub CLI, please run:  gh auth login\\n")
    sys.exit(4)
args = sys.argv[1:]
with open(path + ".calls", "a") as log:
    log.write(json.dumps(args) + "\\n")
key = "graphql" if args[:2] == ["api", "graphql"] else " ".join(args)
if key not in data:
    sys.stderr.write("fake gh: no response for " + key + "\\n")
    sys.exit(1)
out = data[key]
code = 0
if isinstance(out, dict) and "__exit" in out:
    code, out = out["__exit"], out["__stdout"]
    sys.stderr.write("gh: GraphQL error\\n")
sys.stdout.write(out if isinstance(out, str) else json.dumps(out))
sys.exit(code)
'''


@contextlib.contextmanager
def fake_gh(test, responses):
    d = tempdir(test)
    exe = os.path.join(d, 'gh')
    with open(exe, 'w') as f:
        f.write(FAKE_GH)
    os.chmod(exe, 0o755)
    data_file = os.path.join(d, 'responses.json')
    with open(data_file, 'w') as f:
        json.dump(responses, f)
    old_path, old_file = os.environ.get('PATH', ''), os.environ.get('FAKE_GH_FILE')
    os.environ['PATH'] = d + os.pathsep + old_path
    os.environ['FAKE_GH_FILE'] = data_file
    try:
        yield data_file + '.calls'
    finally:
        os.environ['PATH'] = old_path
        if old_file is None:
            os.environ.pop('FAKE_GH_FILE', None)
        else:
            os.environ['FAKE_GH_FILE'] = old_file


@contextlib.contextmanager
def temp_data_dir(test):
    d = tempdir(test)
    old = os.environ.get('KB_DATA_DIR')
    os.environ['KB_DATA_DIR'] = d
    try:
        yield d
    finally:
        if old is None:
            os.environ.pop('KB_DATA_DIR', None)
        else:
            os.environ['KB_DATA_DIR'] = old


def run_kb(*args, cwd=None):
    """Run the CLI as the skill does; returns the CompletedProcess."""
    return subprocess.run([sys.executable, '-I', KB, *args], cwd=cwd, capture_output=True, text=True,
                          env=os.environ.copy())


class DataDirCase:
    """Mixin: a temp KB_DATA_DIR and a repo for every test."""

    def setUp(self):
        cm = temp_data_dir(self)
        self.data = cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        self.repo = make_repo(self)
