#!/usr/bin/env bash
# Create one worktree + branch per fix.
#
# Usage: make_worktrees.sh <repo-dir> <base-ref> <worktrees-dir> <slug> [<slug> ...]
#   e.g. make_worktrees.sh ~/code/app origin/staging ~/code/app-worktrees fix-a fix-b
#
# Each slug gets <worktrees-dir>/<slug> on branch fix/<slug> (provisional; the issue
# number is added at publish time). node_modules is symlinked from the main checkout
# so pre-commit hooks (eslint/prettier) work without reinstalling.
set -euo pipefail
repo=$1; base=$2; wtdir=$3; shift 3
git -C "$repo" fetch -q origin "${base#origin/}"
mkdir -p "$wtdir"
for slug in "$@"; do
  if [ -d "$wtdir/$slug" ]; then echo "exists: $slug"; continue; fi
  git -C "$repo" worktree add -q --no-track -b "fix/$slug" "$wtdir/$slug" "$base"
  [ -d "$repo/node_modules" ] && ln -s "$repo/node_modules" "$wtdir/$slug/node_modules"
  echo "created: $wtdir/$slug (fix/$slug)"
done
