#!/usr/bin/env bash
# Add issues to a GitHub Projects (v2) board and set their Status, then read it back.
#
# Usage: project_status.sh <org> <project-number> "<Status option name>" <repo> <issue> [<issue> ...]
#   e.g. project_status.sh acme 1 "In review" acme/app 101 102 103
#
# Needs the `project` token scope: gh auth refresh -h github.com -s project (interactive; the user runs it).
set -euo pipefail
org=$1; num=$2; status=$3; repo=$4; shift 4
pid=$(gh project view "$num" --owner "$org" --format json --jq .id)
read -r fid oid < <(gh project field-list "$num" --owner "$org" --format json \
  --jq ".fields[]|select(.name==\"Status\")|.id+\" \"+(.options[]|select(.name==\"$status\")|.id)")
[ -n "${oid:-}" ] || { echo "Status option '$status' not found"; exit 1; }
for n in "$@"; do
  id=$(gh project item-add "$num" --owner "$org" --url "https://github.com/$repo/issues/$n" --format json --jq .id)
  gh project item-edit --id "$id" --project-id "$pid" --field-id "$fid" --single-select-option-id "$oid" >/dev/null
done
owner=${repo%/*}; name=${repo#*/}
for n in "$@"; do
  gh api graphql -F n="$n" -f o="$owner" -f r="$name" -f query='query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){issue(number:$n){number projectItems(first:5){nodes{project{title} fieldValueByName(name:"Status"){... on ProjectV2ItemFieldSingleSelectValue{name}}}}}}}' \
    --jq '.data.repository.issue | "#\(.number) " + ([.projectItems.nodes[] | "\(.project.title): \(.fieldValueByName.name)"] | join(", "))'
done
