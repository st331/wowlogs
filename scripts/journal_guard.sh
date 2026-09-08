#!/usr/bin/env bash
# Journal guard: never let a run overwrite a good journals cache with an empty one.
#
# Every collection journal (data/raw, data/processed: the 4 GB gear journal, the
# players journal, the trinket journal, the name caches) lives ONLY in the
# Actions cache, saved under a fresh key by every run and restored by prefix.
# actions/cache/restore never fails a job: on a service error or a missing key
# it warns, leaves the tree empty and lets the run continue. A run that then
# fetches, builds and SAVES writes an impoverished cache under the newest key,
# and every later run restores that one -- the newest good cache is gone the
# moment it stops being the newest. That is exactly what run 32625724812 did
# on 2026-08-23 (70,246 runs -> 34,312) and, with gear.jsonl having no copy
# outside the cache, what would end the Character screen and the trinket
# feature for the season.
#
# Verdicts, written to $GITHUB_OUTPUT as journals=<verdict>:
#   ok       the restore matched a key AND the journals are on disk -> run normally
#   fresh    nothing restored, but FRESH_START=true was dispatched by a human ->
#            proceed anyway (first run of a season, or after a deliberate reset)
#   missing  nothing restored (or restored but empty) and no fresh start ->
#            the workflow skips collecting, building, deploying and saving; the
#            chain still dispatches the successor, which retries the restore.
#            A transient miss costs one 20-minute cycle and heals itself. A
#            persistent one stops deploys, which the watchdog already reports
#            as a stalled build; then, and only then, dispatch fresh_start=true.
#
# Inputs (env): MATCHED_KEY (actions/cache/restore's cache-matched-key output),
# FRESH_START ("true" to bypass), JOURNALS_DIR (default data/processed).
set -u
DIR="${JOURNALS_DIR:-data/processed}"
GEAR="$DIR/gear.jsonl"; PLAYERS="$DIR/players.jsonl"
MATCHED="${MATCHED_KEY:-}"
have() { [ -s "$1" ] && echo present || echo absent; }
OK=1
[ -n "$MATCHED" ] || OK=0
[ -s "$GEAR" ] || OK=0
[ -s "$PLAYERS" ] || OK=0
out() { if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "journals=$1" >> "$GITHUB_OUTPUT"; fi; echo "journals=$1"; }
if [ "$OK" = 1 ]; then
  out ok
  echo "restored '$MATCHED': gear.jsonl $(stat -c %s "$GEAR") B, players.jsonl $(stat -c %s "$PLAYERS") B"
elif [ "${FRESH_START:-}" = "true" ]; then
  out fresh
  echo "::warning::fresh_start: proceeding with NO restored journals (matched key '${MATCHED:-none}', gear.jsonl $(have "$GEAR"), players.jsonl $(have "$PLAYERS")); this run seeds from the committed CSV and saves a NEW cache that every later run will restore"
else
  out missing
  echo "::warning::journal guard tripped: matched key '${MATCHED:-none}', gear.jsonl $(have "$GEAR"), players.jsonl $(have "$PLAYERS") -- this run collects nothing, builds nothing, deploys nothing and saves no cache, so the newest good cache stays the newest. The chain continues and the next run retries the restore. If EVERY run trips, the cache is really gone: dispatch refresh.yml with fresh_start=true to start over from the committed CSV (gear and trinket history cannot be recovered)."
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    { echo "### Journal guard tripped"; echo
      echo "- matched key: \`${MATCHED:-none}\`"
      echo "- gear.jsonl: $(have "$GEAR"); players.jsonl: $(have "$PLAYERS")"
      echo "- this run skipped Fetch, Build, Deploy and Save; the chain continues"
      echo "- persistent? dispatch with \`fresh_start=true\` only if the journals are truly gone"
    } >> "$GITHUB_STEP_SUMMARY"
  fi
fi
exit 0
