#!/usr/bin/env bash
# Show how this repository, and the person running the sweep, actually do things, so the
# batch's commits, branches, issues and pull requests look like theirs. Nothing is configured
# anywhere: phase 0 reads this output and the files it lists, and derives the rules from them.
#
# Usage: conventions.sh <repo-dir> [<how-many>=12]
#   e.g. conventions.sh ~/code/app > /tmp/quick-wins/app/conventions.raw.txt
#
# Read-only: git log and gh list/view calls. A section that can't be read (no gh, no access,
# no history) says so and the rest still prints.
set -uo pipefail
repo=$1; n=${2:-12}
cd "$repo" || exit 1
say() { printf '\n## %s\n' "$1"; }
none() { sed 's/^/  /' | { grep . || echo "  (none)"; }; }

me=$(gh api user --jq .login 2>/dev/null || true)
email=$(git config user.email || true)
slug=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || true)
default=$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name 2>/dev/null || git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||')
echo "repository: ${slug:-unknown}   default branch: ${default:-unknown}   person: ${me:-unknown} (git: $(git config user.name || echo unknown))"

say "Written rules to read (files that exist)"
for f in AGENTS.md CLAUDE.md .claude/CLAUDE.md CONTRIBUTING.md .github/CONTRIBUTING.md docs/CONTRIBUTING.md \
         .github/pull_request_template.md .github/PULL_REQUEST_TEMPLATE.md docs/pull_request_template.md pull_request_template.md \
         .github/ISSUE_TEMPLATE .github/PULL_REQUEST_TEMPLATE CODEOWNERS .github/CODEOWNERS docs/CODEOWNERS \
         commitlint.config.js commitlint.config.cjs commitlint.config.ts .commitlintrc .commitlintrc.json .commitlintrc.yml \
         lefthook.yml .husky .pre-commit-config.yaml .gitmessage; do
  [ -e "$f" ] && echo "$f"
done | none

say "The person's last $n commits here (subjects, newest first)"
if [ -n "$email" ]; then git log --no-merges --author="$email" -n "$n" --format='%s' --all 2>/dev/null | none; else echo "  (no git user.email)"; fi

say "Everyone's last $n commits on ${default:-the default branch} (subjects; shows squash or merge, and the usual format)"
git log -n "$n" --format='%s' "origin/${default:-HEAD}" 2>/dev/null | none

fields=number,title,state,headRefName,baseRefName,labels,assignees,reviewRequests,latestReviews,isDraft,closingIssuesReferences,body
line='.[] | "#\(.number) [\(.state)\(if .isDraft then " draft" else "" end)] \(.title)\n    branch: \(.headRefName) -> \(.baseRefName)\n    labels: \([.labels[].name] | join(", ") | if . == "" then "-" else . end)   assignees: \([.assignees[].login] | join(", ") | if . == "" then "-" else . end)\n    review asked of: \([.reviewRequests[] | (.login // .slug // .name)] | join(", ") | if . == "" then "-" else . end)   reviewed by: \([.latestReviews[].author.login] | unique | join(", ") | if . == "" then "-" else . end)\n    closes: \([.closingIssuesReferences[].number | "#\(.)"] | join(", ") | if . == "" then "-" else . end)   body: \(.body | split(" ") | length) words; headings: \([.body | split("\n")[] | select(test("^#{1,6} ")) | gsub("\r"; "")] | join(" / ") | if . == "" then "-" else . end)"'

say "The person's last $n pull requests here"
gh pr list --author "@me" --state all --limit "$n" --json "$fields" --jq "$line" 2>/dev/null | none

say "Other people's last $n merged pull requests here (use only where the person has no history of their own)"
gh pr list --state merged --limit $((n * 3)) --json "$fields,author" --jq "[.[] | select(.author.login != \"$me\")][:$n] | $line" 2>/dev/null | none

say "The person's last 6 issues here"
gh issue list --author "@me" --state all --limit 6 --json number,title,state,labels,assignees \
  --jq '.[] | "#\(.number) [\(.state)] \(.title)\n    labels: \([.labels[].name] | join(", ") | if . == "" then "-" else . end)   assignees: \([.assignees[].login] | join(", ") | if . == "" then "-" else . end)"' 2>/dev/null | none

say "Labels the repository has"
gh label list --limit 100 --json name --jq '[.[].name] | join(", ")' 2>/dev/null | none

say "Branches pull requests are merged into (last 40 merged)"
gh pr list --state merged --limit 40 --json baseRefName --jq '[.[].baseRefName] | group_by(.) | map("\(.[0]): \(length)") | join(", ")' 2>/dev/null | none
