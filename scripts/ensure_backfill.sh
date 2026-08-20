#!/usr/bin/env bash
# Idempotent starter for the collection loop.
#
# The container is reclaimed after a period of session inactivity and comes
# back as a fresh boot: the filesystem survives, every process does not. So
# "is it still running" has to be re-checked rather than assumed, and the
# answer after a reboot is always no.
#
# backfill_loop.sh holds a flock, so running this while the loop is healthy is
# a no-op. Safe to call as often as you like.
set -u
cd "$(dirname "$0")/.."
LOG=/tmp/backfill.log
DONE=data/processed/summaries_done.txt

fetched=$(wc -l < "$DONE" 2>/dev/null || echo 0)
if pgrep -f 'backfill_loop[.]sh' >/dev/null; then
  age=$(( $(date +%s) - $(stat -c %Y "$LOG" 2>/dev/null || date +%s) ))
  echo "OK: loop running | fetched=$fetched | log idle ${age}s | up $(cut -d. -f1 /proc/uptime)s"
  exit 0
fi

# Deliberately NOT removing the lock file. flock releases on process death, so
# a leftover file is harmless -- but deleting it and letting the new loop
# create a fresh one points the two processes at different inodes, and the
# mutual exclusion silently stops working. A stale path is not a stale lock.
nohup "$(dirname "$0")/backfill_loop.sh" "$LOG" >/dev/null 2>&1 &
sleep 3
if pgrep -f 'backfill_loop[.]sh' >/dev/null; then
  echo "RESTARTED: loop was not running | fetched=$fetched | up $(cut -d. -f1 /proc/uptime)s"
else
  echo "FAILED to start the loop | fetched=$fetched"
  exit 1
fi
