# quick-wins

An agent skill that runs a bug sweep end to end: hunt a batch of small, low-risk bugs, fix each in its own worktree, review the fixes for false positives, prove the visible ones in a running app, find the commit that introduced each, and open one pull request per fix after a single confirmation.

`SKILL.md` is the skill. The repository root is the skill folder, laid out as the [Agent Skills](https://agentskills.io/specification) format asks: `SKILL.md`, `references/` (read on demand) and `scripts/` (run, not read).

## Install

Clone this repository into a skills folder, keeping the folder name `quick-wins` (it must match the `name` in `SKILL.md`):

```sh
git clone <this repository's URL> ~/.claude/skills/quick-wins
```

Claude Code reads `~/.claude/skills/` for every project, and OpenCode reads the same folder. For one project only, clone it into that project's `.claude/skills/` instead. The folder may also be a symlink to a clone kept elsewhere.

## Whose format it uses

Yours, in the repository you run it on. There is nothing to configure. Before it hunts, the skill reads the repository's written rules and your own recent commits and pull requests there (`scripts/conventions.sh`), and derives from them how a commit subject, a branch, a PR title and body read, which labels and reviewers a PR gets, and whether a fix gets an issue. Where you have no history in a repository, it follows what other contributors' merged pull requests look like. It shows what it inferred, and from which commits or PRs, before anything is published.

## Needs

- three places it may write without asking, if your agent asks for permission per path: `/tmp/quick-wins/` (scratch files), `<repo>-worktrees/` beside the repository (one worktree per fix), and `~/.local/share/quick-wins/` (the findings database, one folder per repository, kept between runs)

- `git` and `gh`, signed in to the repository being swept
- `python3` with Pillow, for `scripts/annotate.py`
- Node.js, for the Playwright checks in phase 3.5 (Playwright itself is installed per run, in the scratch directory)

## What it remembers

Every finding and what became of it (merged, closed, reverted, or dropped and why), in one SQLite file per repository under `~/.local/share/quick-wins/<host_owner_name>/`, read and written by `scripts/kb.py`. It is seeded from the repository's own small fix commits on the first run. It stores code snippets, diffs, commit hashes and PR numbers, never who wrote what. Settings are in `config.json` next to it; delete the folder to start a repository over.

## Changing it

Run the tests with `python3 -I -m unittest discover -s tests -t .` (standard library only; they use throwaway git repositories and a fake `gh`). Edit the files in place and commit. `SKILL.md` text is picked up by a running session; scripts are read each time they run.
