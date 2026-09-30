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
# discarded and no permission beyond an ordinary push is needed.
#
# The cost is that the branch grows by roughly the archive size each run, about
# 0.7 MB, so a year of weekdays is on the order of 175 MB. Collapsing it back
# to a single commit means rewriting the branch, which needs a force-push, and
# force-pushing is deliberately not automated here: it is the one operation
# that can destroy the only copy of the state. Do it by hand when it matters.
#
# The archive is built in a staging directory that is NOT the scratch repo, and
# copied in only after the branch is checked out. The first version of this
# script built it inside the repo, where `git reset --hard FETCH_HEAD` promptly
# overwrote it with the previous commit's copy: from 2026-09-20 to 2026-09-24
# every push silently re-committed the first archive, so the branch carried a
# pre-004 database under labels "issue 4" and "issue 5", and the day the state
# was restored from it two days of dedup were lost. Hence also the read-back
# below: a push is not reported as done until the archive that came back out of
# the commit holds the same rows as the database that went in.
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

# Row counts alone, for comparing what went in against what came back out.
counts_db() {
    python3 - "$1" <<'PY'
import sqlite3, sys
db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
print(" ".join(str(db.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
                for t in ("seen_items", "nct_state", "page_state",
                          "weekly_pool", "contributors", "runs")))
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
    # A pull overwrites the only local copy, so keep the outgoing one until the
    # next pull. The script above cost two days of dedup by moving over a
    # newer database without leaving anything behind.
    if [ -f "$DB" ]; then
        cp "$DB" "$DB.replaced"
        echo "state_sync: previous local database kept at data/state.sqlite3.replaced"
    fi
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
    before="$(counts_db "$DB")"
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    # Staged here, OUTSIDE the scratch repo, so the checkout below cannot
    # overwrite it. This separation is the whole fix; do not collapse it.
    stage="$tmp/stage"; work="$tmp/repo"
    mkdir -p "$stage" "$work"
    gzip -c "$DB" > "$stage/$ARCHIVE"
    remote="$(git remote get-url origin)"
    # Built in a scratch repo so the code checkout is never touched: this must
    # not be able to stage, stash or switch anything in the working tree.
    cd "$work"
    git init -q .
    git remote add origin "$remote"
    # Chain onto whatever the branch already holds, so the push fast-forwards.
    if git fetch -q origin "$BRANCH" 2>/dev/null; then
        git reset -q --hard FETCH_HEAD
    fi
    cp "$stage/$ARCHIVE" "$work/$ARCHIVE"
    # The issue this state was published under, taken from the day's own run
    # file rather than from an argument, so it cannot disagree with the report.
    newest="$(ls -1 "$HERE"/out/liver_daily_*_run.json 2>/dev/null | sort | tail -1 || true)"
    if [ -n "$newest" ]; then
        python3 - "$newest" > "$stage/$ISSUE" <<'PY' || rm -f "$stage/$ISSUE"
import json, re, sys
issue = str((json.load(open(sys.argv[1], encoding="utf-8")) or {}).get("issue") or "")
digits = re.sub(r"\D", "", issue)
if not digits:
    sys.exit(1)
print(int(digits))
PY
    fi
    if [ -f "$stage/$ISSUE" ]; then
        cp "$stage/$ISSUE" "$work/$ISSUE"
        git add "$ISSUE"
        echo "state_sync: recording issue 第$(printf '%03d' "$(cat "$stage/$ISSUE")")期"
    fi
    git add "$ARCHIVE"
    git -c user.name=Claude -c user.email=noreply@anthropic.com \
        commit -q -m "liver-intel state $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    # Read the archive back out of the commit before claiming anything. An
    # archive that is stale, truncated or corrupt looks exactly like a good one
    # from the outside, and only the row counts tell them apart.
    git show "HEAD:$ARCHIVE" | gunzip > "$stage/verify.sqlite3"
    check_db "$stage/verify.sqlite3" "committed"
    after="$(counts_db "$stage/verify.sqlite3")"
    if [ "$before" != "$after" ]; then
        echo "state_sync: the commit does not hold the local state -- NOT pushing." >&2
        echo "state_sync:   local     $before" >&2
        echo "state_sync:   committed $after" >&2
        echo "state_sync: (counts are seen_items nct_state page_state weekly_pool contributors runs)" >&2
        exit 1
    fi
    git push -q origin "HEAD:refs/heads/$BRANCH"
    echo "state_sync: pushed data/state.sqlite3 -> '$BRANCH' ($(du -h "$work/$ARCHIVE" | cut -f1), $before)"
    ;;
size)
    git fetch -q origin "$BRANCH" 2>/dev/null || { echo "state_sync: no '$BRANCH' branch"; exit 0; }
    commits="$(git rev-list --count FETCH_HEAD)"
    echo "state_sync: '$BRANCH' holds $commits commit(s), roughly $(( commits * 7 / 10 )) MB of archives"
    echo "state_sync: collapsing it to one commit is a manual force-push -- see the header"
    ;;
*)
    echo "usage: state_sync.sh {pull|push|size}" >&2; exit 2 ;;
esac
