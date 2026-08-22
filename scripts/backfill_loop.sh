#!/usr/bin/env bash
# Continuous collection loop.
#
# Sweeps the leaderboards and fetches whatever summaries are new, back to back,
# sleeping only when there is nothing left -- the API client already parks
# itself when the hourly budget runs out, so there is no pacing to do here.
#
# Everything is journalled per window and per batch, so killing this at any
# point costs at most the batch in flight. It deliberately does NOT commit: a
# build is tens of MB and the site cannot take the row count yet.
set -u
cd "$(dirname "$0")/.."
LOG=${1:-/tmp/backfill.log}
LOCK=/tmp/wowlogs-backfill.lock

exec 9>"$LOCK"
flock -n 9 || { echo "another backfill loop holds $LOCK; exiting"; exit 0; }

# Collection runs in GitHub Actions against the account's API client. If no
# credentials are present locally that is deliberate, not a fault -- exit
# rather than spinning, since fetch_data exits immediately without them and
# the || true below would otherwise turn that into a hot loop.
if [ -z "${WCL_TOKEN:-}${WCL_CLIENT_ID:-}" ] \
   && [ ! -s .secrets/wcl_token ] \
   && [ ! -s .secrets/wcl_client_id ]; then
  echo "no local WCL credentials; collection belongs to the workflow" >>"$LOG"
  exit 0
fi

cycle=0
while true; do
  cycle=$((cycle + 1))
  echo "=== cycle $cycle  $(date -u +%FT%TZ) ===" >>"$LOG"
  python3 -u scripts/fetch_data.py --stage sweep --resweep >>"$LOG" 2>&1 || true
  python3 -u scripts/fetch_data.py --stage summaries         >>"$LOG" 2>&1 || true
  st=$(python3 -u scripts/fetch_data.py --stage status 2>/dev/null)
  echo "$st" >>"$LOG"
  case "$st" in
    *"(0 remaining)"*) echo "caught up; sleeping 20m" >>"$LOG"; sleep 1200 ;;
  esac
done
