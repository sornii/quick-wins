#!/usr/bin/env bash
# Build a throwaway "preview" worktree: the base branch with every recent open PR
# merged in, so hunters don't rediscover bugs that a pending PR already fixes.
# Hunt here; never branch fixes off it (they would carry other people's commits).
#
# Usage: make_preview.sh <repo-dir> <base-branch> <preview-dir> [<max-age-days>=30]
#   e.g. make_preview.sh ~/code/app staging /tmp/quick-wins/app/preview 30
#
# Merges PRs targeting <base-branch> one at a time, oldest first. A PR that conflicts
# is aborted and skipped. Prints a summary and writes it to <preview-dir>.merged.txt:
#   merged   #123 <title>
#   conflict #456 <title>
# Hooks are disabled for the merges (throwaway tree). Remove afterwards with:
#   git -C <repo-dir> worktree remove --force <preview-dir>
set -euo pipefail
repo=$1; base=$2; preview=$3; max_age=${4:-30}
summary="$preview.merged.txt"

git -C "$repo" fetch -q origin "$base"
if [ -d "$preview" ]; then
  echo "preview exists: $preview (remove it first to rebuild)" >&2; exit 1
fi
mkdir -p "$(dirname "$preview")"
git -C "$repo" worktree add -q --detach "$preview" "origin/$base"

cutoff=$(date -u -d "-$max_age days" +%Y-%m-%dT%H:%M:%SZ)
prs=$(cd "$repo" && gh pr list --base "$base" --state open --limit 200 \
  --json number,title,updatedAt \
  --jq "[.[] | select(.updatedAt >= \"$cutoff\")] | sort_by(.number) | .[] | \"\(.number)\t\(.title)\"")

: > "$summary"
g() { git -C "$preview" -c core.hooksPath=/dev/null -c user.name=preview -c user.email=preview@localhost "$@"; }
while IFS=$'\t' read -r num title; do
  [ -z "$num" ] && continue
  if ! g fetch -q origin "pull/$num/head"; then
    echo "nofetch  #$num $title" >> "$summary"; continue
  fi
  if g merge -q --no-ff --no-edit -m "preview: #$num" FETCH_HEAD >/dev/null 2>&1; then
    echo "merged   #$num $title" >> "$summary"
  else
    g merge --abort >/dev/null 2>&1 || g reset -q --hard HEAD
    echo "conflict #$num $title" >> "$summary"
  fi
done <<< "$prs"

[ -d "$repo/node_modules" ] && ln -s "$repo/node_modules" "$preview/node_modules"
cat "$summary"
echo "preview ready: $preview ($(grep -c '^merged' "$summary" || true) PRs merged)"
