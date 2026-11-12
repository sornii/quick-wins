#!/usr/bin/env bash
# Start and stop local app instances for browser verification, under a machine-wide cap.
# A dev server (Meteor, Rails, Next...) is heavy: a few at once can freeze the machine, and
# several repos may be using it at the same time. So every instance is started
# through this script, which refuses to start one more than the cap allows.
#
# Usage:
#   app_slot.sh run <name> <workdir> '<command>'   start in the background; prints pid and log path
#   app_slot.sh wait <url> [<timeout-seconds>=600] block until the URL answers
#   app_slot.sh stop <name> | --all                stop the instance (whole process group), free the slot
#   app_slot.sh status                             list running instances and the cap
#
#   e.g. app_slot.sh run app-verify ~/code/app-worktrees/verify \
#          'PORT=3190 DATABASE_URL=postgres://localhost:5432/verify_app npm run dev'
#        app_slot.sh wait http://localhost:3190 900
#        app_slot.sh stop app-verify
#
# Cap: APP_SLOT_MAX, default 2, never more than 3 whatever you pass. The cap counts instances
# started by this script, in any repo. A dev server the user already has running is not counted
# here: lower APP_SLOT_MAX by one for each (see `status`, which lists look-alike processes).
# <name> must be unique on the machine: prefix it with the repo name.
set -euo pipefail
dir=/tmp/quick-wins/.apps
mkdir -p "$dir"
max=${APP_SLOT_MAX:-2}
case "$max" in ''|*[!0-9]*) max=2 ;; esac
[ "$max" -gt 3 ] && max=3
[ "$max" -lt 1 ] && max=1

alive() { [ -n "${1:-}" ] && kill -0 -- "-$1" 2>/dev/null; }

# Drop slots whose process group is gone, then print the names still running.
running() {
  local f pid
  for f in "$dir"/*.pid; do
    [ -e "$f" ] || continue
    pid=$(cat "$f" 2>/dev/null || true)
    if alive "$pid"; then basename "$f" .pid; else rm -f "$f"; fi
  done
}

stop_one() {
  local name=$1 f="$dir/$1.pid" pid i
  [ -e "$f" ] || { echo "not running: $name"; return 0; }
  pid=$(cat "$f")
  if alive "$pid"; then
    kill -TERM -- "-$pid" 2>/dev/null || true
    for i in $(seq 1 30); do alive "$pid" || break; sleep 1; done
    alive "$pid" && kill -KILL -- "-$pid" 2>/dev/null || true
  fi
  rm -f "$f"
  echo "stopped: $name"
}

cmd=${1:-status}; shift || true
case "$cmd" in
  run)
    name=$1; workdir=$2; command=$3
    case "$name" in */*|'') echo "bad name: $name" >&2; exit 2 ;; esac
    exec 9>"$dir/.lock"; flock 9
    mapfile -t up < <(running)
    for n in "${up[@]:-}"; do
      [ "$n" = "$name" ] && { echo "already running: $name (pid $(cat "$dir/$name.pid"))"; exit 0; }
    done
    count=${#up[@]}
    if [ "$count" -ge "$max" ]; then
      echo "REFUSED: $count app instance(s) already running (${up[*]}), cap is $max." >&2
      echo "Stop one first (app_slot.sh stop <name>) or wait; do not raise the cap." >&2
      exit 1
    fi
    log="$dir/$name.log"
    # The new session leader records its own pid, which is the process group id: killing
    # that group stops every child. (setsid may or may not fork, so $! can't be trusted.)
    tmp="$dir/$name.starting"; rm -f "$tmp"
    ( cd "$workdir" && exec setsid bash -c 'echo $$ >"$1"; exec bash -c "$2"' _ "$tmp" "$command" \
        >"$log" 2>&1 9>&- ) &
    pgid=
    for _ in $(seq 1 25); do
      [ -s "$tmp" ] && { pgid=$(cat "$tmp"); break; }
      sleep 0.2
    done
    rm -f "$tmp"
    sleep 1
    alive "$pgid" || { echo "failed to start: $name (see $log)" >&2; exit 1; }
    echo "$pgid" >"$dir/$name.pid"
    echo "started: $name pgid=$pgid log=$log ($((count + 1))/$max)"
    ;;
  wait)
    url=$1; timeout=${2:-600}; end=$((SECONDS + timeout))
    until curl -s -o /dev/null --max-time 5 "$url"; do
      [ "$SECONDS" -ge "$end" ] && { echo "timeout waiting for $url" >&2; exit 1; }
      sleep 3
    done
    echo "ready: $url"
    ;;
  stop)
    if [ "${1:-}" = "--all" ]; then
      for n in $(running); do stop_one "$n"; done
    else
      stop_one "$1"
    fi
    ;;
  status)
    mapfile -t up < <(running)
    echo "cap: $max, running: ${#up[@]}"
    for n in "${up[@]:-}"; do [ -n "$n" ] && echo "  $n pgid=$(cat "$dir/$n.pid") log=$dir/$n.log"; done
    # Not counted, shown so you can account for them: dev servers started some other way.
    others=
    while read -r pid rest; do
      [ -n "$pid" ] || continue
      pg=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')
      mine=0
      for n in "${up[@]:-}"; do [ -n "$n" ] && [ "$(cat "$dir/$n.pid")" = "$pg" ] && mine=1; done
      [ "$mine" = 0 ] && others+="$pid $rest"$'\n'
    done < <(pgrep -af "${APP_SLOT_PATTERN:-meteor run|tools/index.js run|next dev|rails s|vite( |$)}" 2>/dev/null \
      | grep -v app_slot.sh || true)
    if [ -n "$others" ]; then
      echo "other dev-server-like processes (not counted; lower APP_SLOT_MAX by one per real instance):"
      printf '%s' "$others" | cut -c1-140 | sed 's/^/  /'
    fi
    ;;
  *) echo "unknown command: $cmd" >&2; exit 2 ;;
esac
