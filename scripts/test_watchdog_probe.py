#!/usr/bin/env python3
"""The watchdog's probe step (watchdog.yml, id: probe), run for real under
`bash -e` with `set -u`, against stubbed API and site answers:

  * a fresh runs list agreeing with the site: no warning, ok_age is the
    list's, the timer count and the next slot come through;
  * a runs list ten days behind the site (2026-09-30 13:00Z, run #183): the
    warning, ok_age is the site's, the streak is dropped, the next slot is
    counted from the build;
  * an empty runs list (the API call failed): ok_age is the site's, quietly;
  * no site at all: ok_age is the list's.

Run #184 died on an unbound variable the parser and `bash -n` cannot see;
this is the check that catches the next one.

Since 2026-11-28 the step after it ('Raise or clear the alert') is run too, on
the probe's own outputs: healthy at the policy's reset count and at the row
floor, a reason one row under the floor, at another reset count and on a STALE
page, and an open issue closed when all is well. Its reasons had only ever
been read; a row floor above the healthy page raised "Refresh stalled" every
hour for ten days (issue #28) with every test green.
"""
import os
import pathlib
import subprocess
import sys
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import cadence  # noqa: E402
import retention  # noqa: E402

# the period in force, which the probe itself reads from data/cadence.json through
# scripts/cadence.py: the slot arithmetic below follows the file, not a pinned 4 h
PERIOD_S = cadence.read_cadence(ROOT / "data" / "cadence.json", log=lambda *_: None)["period_min"] * 60
NOW_S = 1790780220          # 2026-09-30T14:57:00Z, the clock the stub pins
fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


doc = yaml.safe_load((ROOT / ".github" / "workflows" / "watchdog.yml").read_text())
PROBE = [s for s in doc["jobs"]["watch"]["steps"] if s.get("id") == "probe"][0]["run"]
ENV_BASE = {k: str(v) for k, v in doc["env"].items()}

FRESH_RUNS = ('{"workflow_runs":[{"status":"completed","conclusion":"success","created_at":"2026-09-30T13:00:50Z",'
              '"updated_at":"2026-09-30T13:10:22Z","run_started_at":"2026-09-30T13:00:50Z"}]}')
STALE_RUNS = ('{"workflow_runs":[{"status":"completed","conclusion":"failure","created_at":"2026-09-19T23:00:00Z",'
              '"updated_at":"2026-09-19T23:50:00Z","run_started_at":"2026-09-19T23:00:00Z"},'
              '{"status":"completed","conclusion":"success","created_at":"2026-09-19T22:00:00Z",'
              '"updated_at":"2026-09-19T22:45:51Z","run_started_at":"2026-09-19T22:00:00Z"}]}')
HEALTH = f"built=2026-09-30T13:05:06Z\nrows=520014\nnewest_row=2026-09-30T12:43:07Z\nretention.resets={retention.RETENTION_RESETS}\nretention.stale=0\n"

# The watchdog compares the published reset count with EXPECT_RESETS and opens "Refresh stalled" when they differ.
# Nothing tied that number to scripts/retention.py: a change of policy would have raised the alarm every hour.
check(int(ENV_BASE["EXPECT_RESETS"]) == retention.RETENTION_RESETS,
      f"watchdog.yml EXPECT_RESETS ({ENV_BASE['EXPECT_RESETS']}) is the policy's reset count ({retention.RETENTION_RESETS})")
# The row floor is for an emptied journal, not for a quiet week: at 200000 it was above the healthy page for ten
# days (issue #28, 2026-11-18 to 2026-11-28, 107 comments, nothing wrong). The smallest healthy page is the
# policy's resets less the partial one, at the lowest daily volume seen (about 11,000 rows a day, 2026-11-26).
check(int(ENV_BASE["MIN_ROWS"]) == 50000 and int(ENV_BASE["MIN_ROWS"]) < 7 * 11000,
      f"watchdog.yml MIN_ROWS ({ENV_BASE['MIN_ROWS']}) sits under one quiet week of rows, so only an emptied journal trips it")
_refresh = (ROOT / ".github" / "workflows" / "refresh.yml").read_text()
check("SEED_FLOOR=50000\n" in _refresh, "refresh.yml SEED_FLOOR is the same catastrophe floor, so a healthy export always reaches the seed")


def run_probe(runs_json, timers_json='{"workflow_runs":[{"status":"in_progress"}]}', health=HEALTH, now="2026-09-30T14:57:00Z"):
    """Run the step with curl stubbed (the step's api() calls curl) and the
    clock pinned through a `date` wrapper, so the ages are deterministic."""
    stub = f'''
curl() {{
  for a in "$@"; do case "$a" in
    *refresh.yml/runs*) {"echo " + repr(runs_json) + "; return 0" if runs_json is not None else "return 22"};;
    *timer.yml/runs*) echo {repr(timers_json)}; return 0;;
    *build_health.txt*) {"printf %b " + repr(health) + "; return 0" if health is not None else "return 22"};;
  esac; done; return 22; }}
date() {{ if [ "$1" = "-u" ] && [ "$2" = "+%s" ]; then command date -u -d {repr(now)} +%s; else command date "$@"; fi; }}
'''
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "out"
        env = {**os.environ, **ENV_BASE, "GH_TOKEN": "x", "REPO": "st331/wowlogs", "GITHUB_OUTPUT": str(out)}
        # from the repository root, as on the runner: the probe reads scripts/cadence.py by that path
        r = subprocess.run(["bash", "-e", "-c", stub + PROBE], capture_output=True, text=True, env=env, cwd=str(ROOT))
        outputs = dict(l.split("=", 1) for l in out.read_text().splitlines() if "=" in l) if out.exists() else {}
        return r, outputs


r, o = run_probe(FRESH_RUNS)
check(r.returncode == 0, f"fresh list: the step exits 0 (stderr: {r.stderr.strip()[:120]!r})")
check(o.get("ok_age") == "106" and o.get("streak") == "0" and o.get("timer_pending") == "1"
      and "stale" not in r.stdout and o.get("last_conclusion") == "success",
      f"fresh list: ok_age from the list (106), streak 0, one timer sleeping, no stale warning ({o.get('ok_age')}, {o.get('streak')})")
check(o.get("next_due") == str(max(1790773250 + PERIOD_S, NOW_S)),
      f"fresh list: next slot = the run's start + one period of {PERIOD_S // 60} min, never before now ({o.get('next_due')})")

r, o = run_probe(STALE_RUNS)
check(r.returncode == 0, f"stale list: the step exits 0 (stderr: {r.stderr.strip()[:120]!r})")
check("::warning::the runs list is stale" in r.stdout and o.get("ok_age") == "111" and o.get("streak") == "0"
      and "per the site" in o.get("last_conclusion", ""),
      f"stale list: the warning, ok_age from the site's build (111), the list's failure streak dropped ({o.get('ok_age')}, {o.get('streak')})")
check(o.get("next_due") == str(max(1790773506 - 600 + PERIOD_S, NOW_S)),
      f"stale list: next slot = build - 10 min + one period, never before now ({o.get('next_due')})")
check("15371 min ago per the runs list; 111 min counting" in r.stdout, "stale list: both witnesses are printed")

# The two next-slot checks above run with the clock at 14:57, when "start + one period" is already past at
# an hourly cadence, so they only prove the clamp. Here the clock sits ten minutes after the run, inside
# the period: the slot must be the SUM, from the run's own start and from the site's build stamp.
EARLY, EARLY_S = "2026-09-30T13:11:00Z", 1790773860
if PERIOD_S > 15 * 60:
    r, o = run_probe(FRESH_RUNS, now=EARLY)
    check(o.get("next_due") == str(1790773250 + PERIOD_S) and 1790773250 + PERIOD_S > EARLY_S,
          f"fresh list, clock inside the period: next slot IS the run's start + one period ({o.get('next_due')})")
    r, o = run_probe(STALE_RUNS, now=EARLY)
    check(o.get("next_due") == str(1790773506 - 600 + PERIOD_S) and 1790773506 - 600 + PERIOD_S > EARLY_S,
          f"stale list, clock inside the period: next slot IS the build - 10 min + one period ({o.get('next_due')})")
else:
    print(f"note the period in force ({PERIOD_S // 60} min) is too short for the inside-the-period cases; the clamp cases above stand")

r, o = run_probe(None)
check(r.returncode == 0 and o.get("ok_age") == "111" and "::warning::" not in r.stdout,
      f"empty list (API failed): ok_age from the site, no warning ({o.get('ok_age')})")

# 2026-10-22: 25 listed runs, every one a failure, while the site is recent. That is a
# streak, not a stale list: the site must NOT be believed over it and the streak must stand.
ALL_FAILED = ('{"workflow_runs":[' + ",".join(
    '{"status":"completed","conclusion":"failure","created_at":"2026-09-30T14:%02d:00Z",'
    '"updated_at":"2026-09-30T14:%02d:30Z","run_started_at":"2026-09-30T14:%02d:00Z"}' % (m, m, m)
    for m in range(30, 55)) + ']}')
r, o = run_probe(ALL_FAILED)
check(r.returncode == 0 and o.get("ok_age") == "99999" and o.get("streak") == "25" and o.get("list_stale") == "0"
      and "::warning::the runs list is stale" not in r.stdout,
      f"25 failures listed: a streak, not a stale list ({o.get('ok_age')}, streak {o.get('streak')}, list_stale {o.get('list_stale')})")

r, o = run_probe(FRESH_RUNS, health=None)
check(r.returncode == 0 and o.get("ok_age") == "106" and o.get("built") == "unknown",
      f"no site: ok_age from the list, built unknown ({o.get('ok_age')}, {o.get('built')})")

r, o = run_probe(None, health=None)
check(r.returncode == 0 and o.get("ok_age") == "99999" and o.get("next_due") == "1790780220",
      f"nothing answers: ok_age 99999 (never), next slot now ({o.get('ok_age')}, {o.get('next_due')})")

# ---- the alert step, run for real on what the probe wrote ------------------------------------------------
ALERT = [s for s in doc["jobs"]["watch"]["steps"] if s.get("name") == "Raise or clear the alert"][0]["run"]
MIN_ROWS, N_RESETS = int(ENV_BASE["MIN_ROWS"]), retention.RETENTION_RESETS


def health_of(rows, resets=N_RESETS, stale=0):
    return f"built=2026-09-30T13:05:06Z\nrows={rows}\nnewest_row=2026-09-30T12:43:07Z\nretention.resets={resets}\nretention.stale={stale}\n"


def run_alert(health, open_issue=None, now="2026-09-30T14:57:00Z"):
    """The probe on this build_health.txt, then the alert step on the probe's outputs, with the API stubbed:
    the open-issue lookup answers from here, every other call is logged and answered with {}."""
    r, o = run_probe(FRESH_RUNS, health=health, now=now)
    assert r.returncode == 0, "the probe failed under the alert fixture: " + r.stderr[-300:]
    issues = "[]" if open_issue is None else '[{"number":%d,"title":"Refresh stalled"}]' % open_issue
    stub = f'''
curl() {{
  local method=GET url=""
  while [ $# -gt 0 ]; do case "$1" in
    -X) method="$2"; shift;;
    http*) url="$1";;
  esac; shift; done
  case "$method $url" in
    "GET "*"issues?state=open"*) echo {issues!r};;
    *) echo "$method $url" >> "$CALLS"; echo '{{}}';;
  esac; return 0; }}
date() {{ if [ "$1" = "-u" ] && [ "$2" = "+%s" ]; then command date -u -d {now!r} +%s; else command date "$@"; fi; }}
'''
    with tempfile.TemporaryDirectory() as tmp:
        calls = pathlib.Path(tmp) / "calls"
        env = {**os.environ, **ENV_BASE, "GH_TOKEN": "x", "REPO": "st331/wowlogs", "CALLS": str(calls),
               "OK_AGE": o.get("ok_age", "0"), "STREAK": o.get("streak", "0"), "STUCK_ID": o.get("stuck_id", ""),
               "STUCK_AGE": o.get("stuck_age", ""), "DATA_AGE": o.get("data_age", "0"), "BUILT": o.get("built", ""),
               "NEWEST": o.get("newest", ""), "ROWS": o.get("rows", ""), "ROWS_N": o.get("rows_n", ""),
               "RESETS": o.get("resets", ""), "STALE_WIN": o.get("stale_win", ""), "LAST_CONCLUSION": o.get("last_conclusion", "")}
        a = subprocess.run(["bash", "-e", "-c", stub + ALERT], capture_output=True, text=True, env=env, cwd=str(ROOT))
        return a, (calls.read_text().splitlines() if calls.exists() else []), o


def healthy(a, calls):
    return a.returncode == 0 and a.stdout.startswith("healthy:") and "::error::" not in a.stdout and not calls


a, calls, o = run_alert(health_of(223399))
check(healthy(a, calls) and o.get("resets") == str(N_RESETS) and o.get("rows_n") == "223399",
      f"alert: the first three-reset build (223,399 rows, {N_RESETS} resets) is healthy and nothing is posted ({a.stdout.strip()[:70]!r}, stderr {a.stderr.strip()[:80]!r})")
a, calls, o = run_alert(health_of(118815))
check(healthy(a, calls), f"alert: the two-reset page of 2026-11-28 03:55 (118,815 rows), which the old floor of 200,000 called a stall, is healthy on rows ({a.stdout.strip()[:60]!r})")
a, calls, o = run_alert(health_of(MIN_ROWS))
check(healthy(a, calls), f"alert: exactly {MIN_ROWS} rows is healthy, the floor is the first number that is not below it")
a, calls, o = run_alert(health_of(MIN_ROWS - 1))
check("::error::Refresh stalled" in a.stdout and f"the build published **{MIN_ROWS - 1} rows** (floor {MIN_ROWS})" in a.stdout
      and "resets**, policy is" not in a.stdout and calls == ["POST https://api.github.com/repos/st331/wowlogs/issues"],
      f"alert: one row under the floor opens the issue with that reason and no other ({calls})")
a, calls, o = run_alert(health_of(223399, resets=N_RESETS - 1))
check("::error::Refresh stalled" in a.stdout and f"the build ships **{N_RESETS - 1} resets**, policy is {N_RESETS} (scripts/retention.py)" in a.stdout
      and "rows** (floor" not in a.stdout,
      f"alert: a build with {N_RESETS - 1} resets under a policy of {N_RESETS} is reported as that ({a.stdout.strip()[:90]!r})")
a, calls, o = run_alert(health_of(223399, stale=1))
check("::error::Refresh stalled" in a.stdout and "the page is **STALE**" in a.stdout, "alert: a page flagged STALE is reported")
a, calls, o = run_alert(health_of(223399), open_issue=28)
check(a.returncode == 0 and "closed issue #28" in a.stdout
      and calls == ["POST https://api.github.com/repos/st331/wowlogs/issues/28/comments", "PATCH https://api.github.com/repos/st331/wowlogs/issues/28"],
      f"alert: an open issue is answered and closed when the build is healthy, as #28 was on 2026-11-28 04:57 UTC ({calls})")
a, calls, o = run_alert(health_of(MIN_ROWS - 1), open_issue=28)
check("updated issue #28" in a.stdout and calls == ["POST https://api.github.com/repos/st331/wowlogs/issues/28/comments"],
      f"alert: an open issue gets a comment, not a second issue, while the fault stands ({calls})")

print()
if fails:
    print(f"FAIL  {fails} check(s) failed")
    sys.exit(1)
print("PASS  watchdog probe: fresh, stale, empty and absent witnesses under set -u; the alert step at the floor and at the policy's reset count")
