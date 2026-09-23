#!/usr/bin/env bash
# Carry the run state across containers.
#
# The engine's continuity lives in data/state.sqlite3 -- what has already been
# seen, what each registry row last said, what each watched page last hashed
# to. It is deliberately not on the code branch (it is runtime state, and
# 4.6 MB of it), and a scheduled run gets a fresh container with a fresh
# clone. Without this script that run starts blank: every paper a first
# sighting, every trial a first sighting, every watched page changed. The
# issue would be junk, and nothing in the output would say so.
#
# So the state rides on its own branch, gzipped to ~0.7 MB, one commit per
# run. The commits chain normally -- no force-push, so no history is ever
# discarded and no permission beyond an ordinary push is needed. The cost is
# that the branch grows by roughly the archive size each run; `prune` rewrites
# it back to a single commit when that matters, and needs a force-push.
#
#   state_sync.sh pull    restore data/state.sqlite3 from the branch
#   state_sync.sh push    save data/state.sqlite3 onto the branch
#   state_sync.sh size    report what the branch currently costs
set -euo pipefail

BRANCH="${LIVER_INTEL_STATE_BRANCH:-liver-intel-state}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="$HERE/data/state.sqlite3"
ARCHIVE="state.sqlite3.gz"
#: The issue number is the one piece of continuity that lives outside the
#: database -- ``runs`` records what a run collected, never what it was
#: published as -- so it rides on the state branch beside the archive. Without
#: it a scheduled run has no way to know that yesterday was 第004期.
ISSUE="issue.txt"

cd "$HERE"

# A restored database that is truncated or corrupt must never be installed over
# good state, and a corrupt local one must never be published as the good copy.
check_db() {
    python3 - "$1" "$2" <<'PY'
import sqlite3, sys
path, where = sys.argv[1], sys.argv[2]
try:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        sys.exit(f"state_sync: {where} database fails integrity_check -- stopping")
    seen = db.execute("SELECT count(*) FROM seen_items").fetchone()[0]
    ncts = db.execute("SELECT count(*) FROM nct_state").fetchone()[0]
except sqlite3.DatabaseError as exc:
    sys.exit(f"state_sync: {where} database is not readable ({exc}) -- stopping")
print(f"state_sync: {where} holds {seen} seen items, {ncts} registry rows")
PY
}

case "${1:-}" in
pull)
    if ! git fetch -q origin "$BRANCH" 2>/dev/null; then
        echo "state_sync: no '$BRANCH' branch yet -- starting from empty state."
        echo "state_sync: EVERY item will look new. Correct only on the very first run;"
        echo "state_sync: on any later run this means the state was lost -- say so and stop."
        exit 0
    fi
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    git show "FETCH_HEAD:$ARCHIVE" > "$tmp/$ARCHIVE"
    gunzip -c "$tmp/$ARCHIVE" > "$tmp/state.sqlite3"
    check_db "$tmp/state.sqlite3" "restored"
    mkdir -p "$HERE/data"
    mv "$tmp/state.sqlite3" "$DB"
    echo "state_sync: pulled '$BRANCH' -> data/state.sqlite3"
    if git show "FETCH_HEAD:$ISSUE" > "$tmp/$ISSUE" 2>/dev/null; then
        last="$(tr -dc '0-9' < "$tmp/$ISSUE")"
        [ -n "$last" ] && echo "state_sync: last published issue 第$(printf '%03d' "$last")期 -- next is 第$(printf '%03d' $((last + 1)))期"
    else
        echo "state_sync: no issue number on the branch -- ask the operator which issue this is"
    fi
    ;;
push)
    [ -f "$DB" ] || { echo "state_sync: no $DB to push" >&2; exit 1; }
    check_db "$DB" "local"
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    gzip -c "$DB" > "$tmp/$ARCHIVE"
    remote="$(git remote get-url origin)"
    # Built in a scratch repo so the code checkout is never touched: this must
    # not be able to stage, stash or switch anything in the working tree.
    cd "$tmp"
    git init -q .
    git remote add origin "$remote"
    # Chain onto whatever the branch already holds, so the push fast-forwards.
    if git fetch -q origin "$BRANCH" 2>/dev/null; then
        git reset -q --hard FETCH_HEAD
        cp "$tmp/$ARCHIVE" ./"$ARCHIVE" 2>/dev/null || true
    fi
    # The issue this state was published under, taken from the day's own run
    # file rather than from an argument, so it cannot disagree with the report.
    newest="$(ls -1 "$HERE"/out/liver_daily_*_run.json 2>/dev/null | sort | tail -1 || true)"
    if [ -n "$newest" ]; then
        python3 - "$newest" > "$tmp/$ISSUE" <<'PY' || rm -f "$tmp/$ISSUE"
import json, re, sys
issue = str((json.load(open(sys.argv[1], encoding="utf-8")) or {}).get("issue") or "")
digits = re.sub(r"\D", "", issue)
if not digits:
    sys.exit(1)
print(int(digits))
PY
    fi
    [ -f "$tmp/$ISSUE" ] && { git add "$ISSUE"; echo "state_sync: recording issue 第$(printf '%03d' "$(cat "$tmp/$ISSUE")")期"; }
    git add "$ARCHIVE"
    git -c user.name=Claude -c user.email=noreply@anthropic.com \
        commit -q -m "liver-intel state $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    git push -q origin "HEAD:refs/heads/$BRANCH"
    echo "state_sync: pushed data/state.sqlite3 -> '$BRANCH' ($(du -h "$ARCHIVE" | cut -f1))"
    ;;
size)
    git fetch -q origin "$BRANCH" 2>/dev/null || { echo "state_sync: no '$BRANCH' branch"; exit 0; }
    commits="$(git rev-list --count FETCH_HEAD)"
    echo "state_sync: '$BRANCH' holds $commits commit(s)"
    echo "state_sync: roughly $(( commits * 7 / 10 )) MB of archives; run 'prune' to collapse to one"
    ;;
*)
    echo "usage: state_sync.sh {pull|push|size}" >&2; exit 2 ;;
esac
