#!/usr/bin/env bash
#
# Draft one usage note per project for last month, from real Slurm accounting.
#
# Runs on a host that can reach slurmdbd -- not in GitHub Actions, which cannot
# see the cluster and whose artifacts on a public repository are downloadable by
# anyone. Drafts land in a private directory for a person to read and send.
#
# Install: see RUNBOOK.md next to this file.

set -euo pipefail

# cron gives you almost no PATH, and uv is usually not in it. Appended, not
# prepended, so an operator (or a test) can put something earlier and have it win.
PATH="${PATH:-}:/usr/local/bin:/usr/bin:/bin"

# ---------------------------------------------------------------- configuration
# Override any of these in /etc/kowhai/monthly-notes.env (see RUNBOOK.md).
CONFIG="${KOWHAI_CONFIG:-/etc/kowhai/monthly-notes.env}"
# `set -a` so the config's assignments are exported, not merely set. Without it
# the settings the Python side reads -- KOWHAI_ANON_SALT above all -- are
# invisible to it, and anonymisation quietly falls back to the default salt.
if [[ -r "$CONFIG" ]]; then set -a; . "$CONFIG"; set +a; fi

KOWHAI_REPO="${KOWHAI_REPO:-/opt/kowhai/agents_workshop}"
KOWHAI_DRAFTS_ROOT="${KOWHAI_DRAFTS_ROOT:-/var/lib/kowhai/drafts}"
KOWHAI_LOG_DIR="${KOWHAI_LOG_DIR:-/var/log/kowhai}"
KOWHAI_KEY_FILE="${KOWHAI_KEY_FILE:-/etc/kowhai/openrouter.key}"
KOWHAI_TZ="${KOWHAI_TZ:-Pacific/Auckland}"
KOWHAI_SACCT="${KOWHAI_SACCT:-sacct}"        # overridable so the pipeline is testable
KOWHAI_NOTIFY="${KOWHAI_NOTIFY:-}"           # optional: a command taking the summary on stdin
KOWHAI_KEEP_MONTHS="${KOWHAI_KEEP_MONTHS:-13}"

umask 077                                     # drafts name real groups; keep them private

# ------------------------------------------------------------------- plumbing
timestamp() { date +%Y-%m-%dT%H:%M:%S%z; }
log()  { printf '%s  %s\n' "$(timestamp)" "$*"; }
die()  { printf '%s  ERROR: %s\n' "$(timestamp)" "$*" >&2; exit 1; }

WORK=""
cleanup() { [[ -n "$WORK" && -d "$WORK" ]] && rm -rf "$WORK"; }
trap cleanup EXIT

mkdir -p "$KOWHAI_LOG_DIR" "$KOWHAI_DRAFTS_ROOT"
exec > >(tee -a "$KOWHAI_LOG_DIR/monthly-notes.log") 2>&1

# One run at a time. A month's export can outlive the next trigger if slurmdbd
# is slow, and two runs writing the same draft directory is not worth debugging.
LOCK="${KOWHAI_LOG_DIR}/monthly-notes.lock"
exec 9>"$LOCK"
flock -n 9 || die "another run holds $LOCK; exiting"

# ---------------------------------------------------------------- preconditions
[[ -d "$KOWHAI_REPO/kowhai-agent" ]] || die "no package at $KOWHAI_REPO/kowhai-agent (set KOWHAI_REPO)"
command -v uv >/dev/null           || die "uv is not on PATH ($PATH)"
command -v "$KOWHAI_SACCT" >/dev/null || die "$KOWHAI_SACCT is not on PATH; is this a host with slurmdbd access?"
[[ -r "$KOWHAI_KEY_FILE" ]]        || die "cannot read $KOWHAI_KEY_FILE"

perms=$(stat -c '%a' "$KOWHAI_KEY_FILE" 2>/dev/null || stat -f '%OLp' "$KOWHAI_KEY_FILE")
[[ "$perms" == "600" || "$perms" == "400" ]] || die "$KOWHAI_KEY_FILE is mode $perms; want 600"

# ------------------------------------------------------------------- the window
# Anchored to the first of this month, then stepped back. `date -d 'last month'`
# is wrong on a 31st when the previous month is shorter: on 2026-03-31 it yields
# 2026-03-03, silently exporting the wrong month.
MONTH_END=$(date +%Y-%m-01)                                   # exclusive
MONTH_START=$(date -d "${MONTH_END} -1 month" +%Y-%m-%d)      # inclusive
LABEL=$(date -d "$MONTH_START" +%Y-%m)
log "drafting notes for ${LABEL} (${MONTH_START} .. ${MONTH_END}, ${KOWHAI_TZ})"

WORK=$(mktemp -d)
DUMP="$WORK/sacct.txt"
OUT="$KOWHAI_DRAFTS_ROOT/$LABEL"

# ------------------------------------------------------------------ 1. export
log "exporting from ${KOWHAI_SACCT}"
SLURM_TIME_FORMAT='%Y-%m-%dT%H:%M:%S' "$KOWHAI_SACCT" \
  -a -S "$MONTH_START" -E "$MONTH_END" --parsable2 --noconvert \
  --format=JobID,JobIDRaw,JobName,User,Account,Partition,QOS,State,ExitCode,\
Submit,Eligible,Start,End,Timelimit,Elapsed,Planned,NNodes,NCPUS,ReqMem,ReqTRES,\
TotalCPU,MaxRSS,Reason > "$DUMP"

lines=$(wc -l < "$DUMP")
(( lines > 1 )) || die "sacct returned no rows for ${LABEL}; refusing to draft from an empty month"
log "exported $((lines - 1)) rows"

# ----------------------------------------------------------------- 2. convert
cd "$KOWHAI_REPO/kowhai-agent"
log "converting to Parquet"
# --anonymise pseudonymises usernames only. Account, project name, job name, job
# id and exact timestamps are kept, which is enough to re-identify -- so the
# output stays inside this host either way. KOWHAI_ANON_SALT keeps pseudonyms
# stable between months; set it in the config file.
uv run --no-sync scripts/sacct_to_parquet.py "$DUMP" \
  --tz "$KOWHAI_TZ" --out "$WORK/data" --derive-sched --anonymise

# ------------------------------------------------------------------- 3. draft
mkdir -p "$OUT"
log "drafting into $OUT"
set +e
OPENROUTER_API_KEY="$(<"$KOWHAI_KEY_FILE")" \
KOWHAI_DATA_DIR="$WORK/data" \
KOWHAI_LOG="$KOWHAI_LOG_DIR/runs.jsonl" \
  uv run --no-sync kowhai advisory --out "$OUT"
status=$?
set -e

# A failed account still gets a file, saying why. Counting *.md would report it
# as a draft somebody could send.
# `|| true` on both: grep exits 1 when it matches nothing, and under
# `set -o pipefail` that status survives the pipe and kills the script -- right
# after a run in which every account succeeded.
written=$(grep -L 'FAILED, nothing to send' "$OUT"/*.md 2>/dev/null | wc -l | tr -d ' ') || true
failed=$(grep -l  'FAILED, nothing to send' "$OUT"/*.md 2>/dev/null | wc -l | tr -d ' ') || true
SUMMARY="kowhai ${LABEL}: ${written} draft(s) to read, ${failed} failed, in ${OUT}"
if (( status != 0 )); then
  SUMMARY="${SUMMARY}
advisory exited ${status}. See ${KOWHAI_LOG_DIR}/monthly-notes.log"
fi
log "$SUMMARY"

# ------------------------------------------------------------------ 4. notify
if [[ -n "$KOWHAI_NOTIFY" ]]; then
  printf '%s\n\nNothing has been sent to anyone. Read the drafts before you do.\n' \
    "$SUMMARY" | $KOWHAI_NOTIFY || log "notify command failed; drafts are still in $OUT"
fi

# ------------------------------------------------------------------ 5. retain
find "$KOWHAI_DRAFTS_ROOT" -mindepth 1 -maxdepth 1 -type d \
  | sort -r | tail -n "+$((KOWHAI_KEEP_MONTHS + 1))" \
  | while read -r old; do log "pruning $old"; rm -rf "$old"; done

exit "$status"
