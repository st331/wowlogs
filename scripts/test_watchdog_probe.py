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

Since 2026-11-28 the step after it ('Raise or clear the alert') is run too,
with the environment its own `env:` block gives it, resolved against what the
probe wrote: healthy at the policy's reset count and at the row floor, and each
of its seven reasons raised once (no success, a failure streak, a stuck run,
dead collection, too few rows, another reset count, a STALE page). What it
POSTs is read: the label a later tick finds the issue by, the comment, the
close. Its reasons had only ever been read. A row floor above the healthy page
put a false reason into all 107 comments of issue #28 (2026-11-18 to 11-28),
and for six of those days the same comments also carried a true one, 2 to 6
consecutive failed runs at the Pages deploy, which nobody read for the noise.
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
# days and put a false reason into every one of the 107 comments of issue #28 (2026-11-18 to 2026-11-28). The
# number is a pin. What it has to stay under is the smallest healthy page on record: 95,755 rows, the lowest
# count in those comments (2026-11-26).
LOWEST_HEALTHY_PAGE = 95755
check(int(ENV_BASE["MIN_ROWS"]) == 50000, f"watchdog.yml MIN_ROWS is pinned at 50000 ({ENV_BASE['MIN_ROWS']})")
check(int(ENV_BASE["MIN_ROWS"]) < LOWEST_HEALTHY_PAGE,
      f"and it sits under the smallest healthy page on record ({LOWEST_HEALTHY_PAGE:,} rows), so a quiet week cannot trip it")
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
STEPS = doc["jobs"]["watch"]["steps"]
ALERT_STEP = [s for s in STEPS if s.get("name") == "Raise or clear the alert"][0]
ALERT = ALERT_STEP["run"]
MIN_ROWS, N_RESETS = int(ENV_BASE["MIN_ROWS"]), retention.RETENTION_RESETS
check("if" not in ALERT_STEP, f"the alert step runs on every tick, with no condition ({ALERT_STEP.get('if')!r})")

# The probe reads five facts out of build_health.txt by name. The builder must write each under that name: with
# a line renamed the probe hands the alert step an empty value and the step, by design, says nothing about it.
import re  # noqa: E402
READ = sorted(set(re.findall(r"s/\^([a-z_.]+)=//p", "".join(re.findall(r'echo "\$HEALTH" \| sed -n \'([^\']+)\'', PROBE)))))
_builder = (ROOT / "scripts" / "build_site_data.py").read_text()
missing = [n for n in READ if f'health(f"{n}=' not in _builder]
check(READ == ["built", "newest_row", "retention.resets", "retention.stale", "rows"] and not missing,
      f"build_site_data.py writes every build_health.txt line the probe reads: {READ} (not written: {missing})")


def health_of(rows, resets=N_RESETS, stale=0):
    return f"built=2026-09-30T13:05:06Z\nrows={rows}\nnewest_row=2026-09-30T12:43:07Z\nretention.resets={resets}\nretention.stale={stale}\n"


def alert_env(outputs):
    """The step's own env: block, each ${{ }} resolved the way the runner does. An output the probe did not
    write, or an expression this does not know, fails here: a misspelt output name reaches the step as an empty
    string on the runner, where '[ "" -gt 300 ]' is an error the step shrugs off and the reason never fires."""
    env = {}
    for k, v in ALERT_STEP["env"].items():
        m = re.fullmatch(r"\$\{\{\s*(.+?)\s*\}\}", str(v))
        assert m, f"the alert step's env {k} is not a single expression: {v!r}"
        expr = m.group(1)
        if expr == "github.token":
            env[k] = "x"
        elif expr == "github.repository":
            env[k] = "st331/wowlogs"
        else:
            mo = re.fullmatch(r"steps\.probe\.outputs\.([a-z_]+)", expr)
            assert mo, f"the alert step's env {k} reads something this test cannot resolve: {expr}"
            assert mo.group(1) in outputs, f"the alert step's env {k} reads steps.probe.outputs.{mo.group(1)}, which the probe did not write"
            env[k] = outputs[mo.group(1)]
    return env


def run_alert(health, open_issue=None, now="2026-09-30T14:57:00Z", runs=FRESH_RUNS):
    """The probe on this build_health.txt and runs list, then the alert step on the probe's outputs. The API is
    stubbed: the open-issue lookup answers from here only when it asks for open issues carrying the watchdog
    label; every call is logged with its method, URL and body."""
    r, o = run_probe(runs, health=health, now=now)
    assert r.returncode == 0, "the probe failed under the alert fixture: " + r.stderr[-300:]
    issues = "[]" if open_issue is None else '[{"number":%d,"title":"Refresh stalled"}]' % open_issue
    stub = f"""
curl() {{
  local method=GET url="" body=""
  while [ $# -gt 0 ]; do case "$1" in
    -X) method="$2"; shift;;
    -d) body="$2"; shift;;
    http*) url="$1";;
  esac; shift; done
  jq -cn --arg m "$method" --arg u "$url" --arg b "$body" '{{m:$m,u:$u,b:$b}}' >> "$CALLS"
  case "$method $url" in
    "GET "*"/issues?"*"state=open"*"labels=watchdog"*|"GET "*"/issues?"*"labels=watchdog"*"state=open"*) echo {issues!r};;
    "GET "*) echo '[]';;
    *) echo '{{}}';;
  esac; return 0; }}
date() {{ if [ "$1" = "-u" ] && [ "$2" = "+%s" ]; then command date -u -d {now!r} +%s; else command date "$@"; fi; }}
"""
    with tempfile.TemporaryDirectory() as tmp:
        calls = pathlib.Path(tmp) / "calls"
        env = {**os.environ, **ENV_BASE, **alert_env(o), "CALLS": str(calls)}
        a = subprocess.run(["bash", "-e", "-c", stub + ALERT], capture_output=True, text=True, env=env, cwd=str(ROOT))
        log = [json.loads(l) for l in calls.read_text().splitlines()] if calls.exists() else []
        for c in log:
            c["b"] = json.loads(c["b"]) if c["b"] else None
        return a, log, o


API = "https://api.github.com/repos/st331/wowlogs"


def lookup_ok(log):
    g = [c for c in log if c["m"] == "GET"]
    return len(g) == 1 and g[0]["u"].startswith(API + "/issues?") and "state=open" in g[0]["u"] and "labels=watchdog" in g[0]["u"]


def writes(log):
    return [(c["m"], c["u"]) for c in log if c["m"] != "GET"]


def healthy(a, log):
    return a.returncode == 0 and a.stdout.startswith("healthy:") and "::error::" not in a.stdout and lookup_ok(log) and not writes(log)


def raised(a, log, *reasons, only=True):
    """The step reported exactly these reasons (one '- ' line each) and opened one labelled issue carrying them."""
    lines = [l for l in a.stdout.splitlines() if l.startswith("- ")]
    w = [c for c in log if c["m"] != "GET"]
    ok = a.returncode == 0 and "::error::Refresh stalled" in a.stdout and all(any(r in l for l in lines) for r in reasons)
    ok = ok and (not only or len(lines) == len(reasons)) and lookup_ok(log)
    ok = ok and len(w) == 1 and (w[0]["m"], w[0]["u"]) == ("POST", API + "/issues") and w[0]["b"]["title"] == "Refresh stalled" \
        and w[0]["b"]["labels"] == ["watchdog"] and all(r in w[0]["b"]["body"] for r in reasons)
    return ok


import json  # noqa: E402

a, log, o = run_alert(health_of(223399))
check(healthy(a, log) and o.get("resets") == str(N_RESETS) and o.get("rows_n") == "223399",
      f"alert: the first three-reset build (223,399 rows, {N_RESETS} resets) is healthy; one labelled lookup, nothing posted ({a.stdout.strip()[:70]!r}, stderr {a.stderr.strip()[:80]!r})")
a, log, o = run_alert(health_of(LOWEST_HEALTHY_PAGE))
check(healthy(a, log), f"alert: the smallest healthy page on record ({LOWEST_HEALTHY_PAGE:,} rows), which the old floor of 200,000 called emptied journals, is healthy ({a.stdout.strip()[:60]!r})")
a, log, o = run_alert(health_of(MIN_ROWS))
check(healthy(a, log), f"alert: exactly {MIN_ROWS} rows is healthy, the floor is the first number that is not below it")
a, log, o = run_alert(None)
check(healthy(a, log), f"alert: with the site unreachable and a fresh success on the runs list nothing is raised ({a.stdout.strip()[:70]!r})")

a, log, o = run_alert(health_of(MIN_ROWS - 1))
check(raised(a, log, f"the build published **{MIN_ROWS - 1} rows** (floor {MIN_ROWS})"),
      f"alert: one row under the floor opens a labelled issue with that reason and no other ({writes(log)})")
a, log, o = run_alert(health_of(223399, resets=N_RESETS - 1))
check(raised(a, log, f"the build ships **{N_RESETS - 1} resets**, policy is {N_RESETS} (scripts/retention.py)"),
      f"alert: a build with {N_RESETS - 1} resets under a policy of {N_RESETS} is reported as that and nothing else")
a, log, o = run_alert(health_of(223399, resets=N_RESETS + 1))
check(raised(a, log, f"the build ships **{N_RESETS + 1} resets**, policy is {N_RESETS} (scripts/retention.py)"),
      f"alert: so is a build with {N_RESETS + 1}")
a, log, o = run_alert(health_of(223399, stale=1))
check(raised(a, log, "the page is **STALE**"), "alert: a page flagged STALE is reported")
# nine hours after the last success and the newest run: the two reasons the watchdog exists for
a, log, o = run_alert(health_of(223399), now="2026-09-30T23:57:00Z")
check(int(o["ok_age"]) > int(ENV_BASE["ALERT_SUCCESS_MIN"]) and int(o["data_age"]) > int(ENV_BASE["STALE_DATA_MIN"])
      and raised(a, log, f"no successful refresh for **{o['ok_age']} min**", f"the **newest collected run is {o['data_age']} min old**"),
      f"alert: nine hours on, no success for {o['ok_age']} min and data {o['data_age']} min old are both reported")
a, log, o = run_alert(health_of(223399), runs=ALL_FAILED)
check(o.get("streak") == "25" and raised(a, log, "**25 consecutive failed runs**", only=False),
      f"alert: 25 failed runs in a row are reported as a streak (as 37 comments of issue #28 did, under a false reason)")
STUCK = ('{"workflow_runs":[{"id":777,"status":"queued","conclusion":null,"created_at":"2026-09-30T13:50:00Z",'
         '"updated_at":"2026-09-30T13:50:00Z","run_started_at":"2026-09-30T13:50:00Z"},' + FRESH_RUNS[len('{"workflow_runs":['):])
a, log, o = run_alert(health_of(223399), runs=STUCK)
check(o.get("stuck_id") == "777" and raised(a, log, "run [#777](https://github.com/st331/wowlogs/actions/runs/777) sat **67 min** waiting for a runner"),
      f"alert: a run queued 67 min with nothing running is reported by its number (stuck_id {o.get('stuck_id')!r}, age {o.get('stuck_age')!r})")

a, log, o = run_alert(health_of(223399), open_issue=28)
w = [c for c in log if c["m"] != "GET"]
check(a.returncode == 0 and "closed issue #28" in a.stdout and lookup_ok(log) and writes(log) == [("POST", API + "/issues/28/comments"), ("PATCH", API + "/issues/28")]
      and w[0]["b"]["body"].startswith("Healthy again at ") and "223399 rows" in w[0]["b"]["body"] and w[1]["b"] == {"state": "closed", "state_reason": "completed"},
      f"alert: an open issue is answered 'Healthy again' and closed when the build is healthy, as #28 was on 2026-11-28 04:57 UTC ({writes(log)})")
a, log, o = run_alert(health_of(MIN_ROWS - 1), open_issue=28)
w = [c for c in log if c["m"] != "GET"]
check("updated issue #28" in a.stdout and writes(log) == [("POST", API + "/issues/28/comments")] and f"**{MIN_ROWS - 1} rows**" in w[0]["b"]["body"],
      f"alert: an open issue gets a comment with the reason, not a second issue, while the fault stands ({writes(log)})")

print()
if fails:
    print(f"FAIL  {fails} check(s) failed")
    sys.exit(1)
print("PASS  watchdog probe: fresh, stale, empty and absent witnesses under set -u; the alert step with its own wiring, each of its seven reasons, and what it posts")
