#!/usr/bin/env bash
# scripts/build_step.sh -- the refresh's Build step (partitioned_payload.md §6).
#
# Runs BOTH builders in parallel during dual-emit: the partition builder in the
# background under `timeout` with PARTS_DEADLINE_S (SIGTERM at the deadline,
# SIGKILL 30 s later), the legacy builder in the foreground. Two rules bound
# it so the deploy can never wait on the new builder longer than on the old
# one: the partition builder opens no network socket, and it runs under this
# hard deadline. The LEGACY exit code alone decides the job -- a partition
# failure or a deadline hit is a ::warning:: plus parts.status/parts.rc lines,
# never a red run (a red run would stop the self-chain until the watchdog's
# revival, the opposite of the intent). After both exit, the partition
# builder's own health file is appended to site/build_health.txt (legacy
# write_health() truncates it when it finishes, so the append must come
# after), plus build.step_wall_s.
#
# Deadline: PARTS_DEADLINE_S if set, else the builder's --deadline-default
# (the legacy builder's rolling 7-run median wall minus 60 s, floor 120 s,
# 360 s when unknown). The builder stops cleanly at the first day/stage
# boundary past deadline - 30 s and writes parts.deadline_hit=1 itself; when
# it had to be killed instead (rc 124/137) this step writes the line, so the
# watchdog sees three consecutive hits either way.
#
# Overridable for tests/test_build_step_exit.py: LEGACY_CMD, PARTS_CMD,
# PARTS_DEADLINE_S, HEALTH, PARTS_HEALTH, PARTS_LOG, PYTHON. REBUILD_ALL=true
# (the workflow_dispatch input) passes --rebuild-all to the partition builder.
set +e
T0=$(date +%s)
PY=${PYTHON:-python}
HEALTH=${HEALTH:-site/build_health.txt}
PARTS_HEALTH=${PARTS_HEALTH:-data/processed/parts/health.txt}
PARTS_LOG=${PARTS_LOG:-parts.log}
if [ -z "$PARTS_DEADLINE_S" ]; then
  PARTS_DEADLINE_S=$($PY scripts/partition_build.py --deadline-default 2>/dev/null) || PARTS_DEADLINE_S=""
  case "$PARTS_DEADLINE_S" in ''|*[!0-9]*) PARTS_DEADLINE_S=360 ;; esac
fi
EXTRA=""
[ "${REBUILD_ALL:-}" = "true" ] && EXTRA="--rebuild-all"
: "${LEGACY_CMD:=$PY -u scripts/build_site_data.py}"
: "${PARTS_CMD:=$PY -u scripts/partition_build.py --deadline $PARTS_DEADLINE_S $EXTRA}"
# a killed builder leaves no health file behind, so a stale one from the
# previous run can never be appended as if it were this run's
rm -f "$PARTS_HEALTH"
echo "[build_step] PARTS_DEADLINE_S=$PARTS_DEADLINE_S"
timeout -s TERM -k 30 "$PARTS_DEADLINE_S" bash -c "exec $PARTS_CMD" > "$PARTS_LOG" 2>&1 &
PARTS_PID=$!
bash -c "$LEGACY_CMD"; rc=$?
wait "$PARTS_PID"; prc=$?
if [ "$prc" -ne 0 ]; then
  echo "::warning::partition build failed or hit its deadline (rc $prc, see $PARTS_LOG)"
fi
mkdir -p "$(dirname "$HEALTH")"
touch "$HEALTH"
if [ -f "$PARTS_HEALTH" ]; then
  cat "$PARTS_HEALTH" >> "$HEALTH" 2>/dev/null || true
else
  echo "parts.status=killed" >> "$HEALTH"
fi
if { [ "$prc" -eq 124 ] || [ "$prc" -eq 137 ]; } && ! grep -q '^parts.deadline_hit=1' "$HEALTH"; then
  printf 'parts.deadline_hit=1\nparts.deadline_s=%s\n' "$PARTS_DEADLINE_S" >> "$HEALTH"
fi
echo "parts.rc=$prc" >> "$HEALTH"
echo "build.step_wall_s=$(( $(date +%s) - T0 ))" >> "$HEALTH"
exit $rc
