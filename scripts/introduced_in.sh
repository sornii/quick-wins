#!/usr/bin/env bash
# First guess at the commit that introduced each fixed bug.
#
# Usage: introduced_in.sh <worktrees-dir> [<slug> ...]
#   With slugs, only those worktrees are checked (skips earlier rounds' worktrees).
#
# For every worktree whose HEAD is a single fix commit, blames the lines that the fix
# removed/changed on HEAD^ and prints the blamed commits with counts. Blame often lands on
# formatter/lint sweeps, file moves or later edits: confirm with
#   git log <base> --reverse -S'<buggy snippet>' -- <path>
# and read the diff before trusting a result.
set -uo pipefail
cd "$1"; shift
if [ $# -gt 0 ]; then dirs=("$@"); else dirs=(*/); fi
for d in "${dirs[@]}"; do
  d=${d%/}
  echo "=== $d"
  git -C "$d" diff -U0 HEAD^ HEAD |
    awk '/^\+\+\+ b\//{f=substr($0,7)} /^@@/{split($2,a,","); s=substr(a[1],2); c=(a[2]==""?1:a[2]); if(c==0)c=1; print f" "s" "c}' |
    while read -r f s c; do
      git -C "$d" blame -w -s -l -L "$s,$((s + c - 1))" HEAD^ -- "$f" | awk '{print $1}'
    done | sort | uniq -c |
    while read -r n h; do
      echo "  $n $(git -C "$d" log -1 --format='%h %ad %an | %s' --date=short "$h")"
    done
done
