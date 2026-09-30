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
"""
import os
import pathlib
import subprocess
import sys
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
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
HEALTH = "built=2026-09-30T13:05:06Z\nrows=520014\nnewest_row=2026-09-30T12:43:07Z\nretention.resets=2\nretention.stale=0\n"


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
        r = subprocess.run(["bash", "-e", "-c", stub + PROBE], capture_output=True, text=True, env=env)
        outputs = dict(l.split("=", 1) for l in out.read_text().splitlines() if "=" in l) if out.exists() else {}
        return r, outputs


r, o = run_probe(FRESH_RUNS)
check(r.returncode == 0, f"fresh list: the step exits 0 (stderr: {r.stderr.strip()[:120]!r})")
check(o.get("ok_age") == "106" and o.get("streak") == "0" and o.get("timer_pending") == "1"
      and "stale" not in r.stdout and o.get("last_conclusion") == "success",
      f"fresh list: ok_age from the list (106), streak 0, one timer sleeping, no stale warning ({o.get('ok_age')}, {o.get('streak')})")
check(o.get("next_due") == str(1790773250 + 4 * 3600), f"fresh list: next slot = the run's start + 4 h ({o.get('next_due')})")

r, o = run_probe(STALE_RUNS)
check(r.returncode == 0, f"stale list: the step exits 0 (stderr: {r.stderr.strip()[:120]!r})")
check("::warning::the runs list is stale" in r.stdout and o.get("ok_age") == "111" and o.get("streak") == "0"
      and "per the site" in o.get("last_conclusion", ""),
      f"stale list: the warning, ok_age from the site's build (111), the list's failure streak dropped ({o.get('ok_age')}, {o.get('streak')})")
check(o.get("next_due") == str(1790773506 - 600 + 4 * 3600), f"stale list: next slot = build - 10 min + 4 h ({o.get('next_due')})")
check("15371 min ago per the runs list; 111 min counting" in r.stdout, "stale list: both witnesses are printed")

r, o = run_probe(None)
check(r.returncode == 0 and o.get("ok_age") == "111" and "::warning::" not in r.stdout,
      f"empty list (API failed): ok_age from the site, no warning ({o.get('ok_age')})")

r, o = run_probe(FRESH_RUNS, health=None)
check(r.returncode == 0 and o.get("ok_age") == "106" and o.get("built") == "unknown",
      f"no site: ok_age from the list, built unknown ({o.get('ok_age')}, {o.get('built')})")

r, o = run_probe(None, health=None)
check(r.returncode == 0 and o.get("ok_age") == "99999" and o.get("next_due") == "1790780220",
      f"nothing answers: ok_age 99999 (never), next slot now ({o.get('ok_age')}, {o.get('next_due')})")

print()
if fails:
    print(f"FAIL  {fails} check(s) failed")
    sys.exit(1)
print("PASS  watchdog probe: fresh, stale, empty and absent witnesses under set -u")
