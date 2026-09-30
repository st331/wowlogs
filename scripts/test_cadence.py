#!/usr/bin/env python3
"""The steady-state cadence (scripts/cadence.py, data/cadence.json), pinned:

  * read_cadence: the committed file says every 4 h at 70 %; a missing,
    unreadable or non-object file is the defaults; each knob falls back on
    its own when its value is missing or bad (every_hours must be a whole
    number of hours in 1..24, quota_fraction in (0, 1]); extra keys are
    ignored;
  * wcl_client.quota_fraction: WCL_QUOTA_FRACTION wins when it is valid, an
    unparseable or out-of-range value falls back to the FILE (not to a
    constant), no variable at all is the file, and an unreadable file is
    DEFAULT_QUOTA_FRACTION (0.70); the governor is built from it;
  * chain_decision, the Chain step's whole decision: a cadence run (no
    switch, no drain) dispatches nothing; the switch in force chains a long
    backfill run; a fresh drain run chains a backfill run; a draining run
    with a backlog chains a fresh run, and without one chains nothing; an
    empty backfill_on (Fetch skipped) consults the switch file itself;
  * the CLI the workflow calls: `chain` prints eval-able assignments,
    `github-output` writes every_hours / quota_fraction / cron;
  * the workflows: refresh.yml and watchdog.yml (and diagnose.yml) parse;
    the refresh cron is `0 */<every_hours> * * *`; the refresh job and the
    watchdog job carry no `if:` pause; the Cadence step feeds Fetch and the
    background collectors; the Chain step runs on success() alone and
    decides through cadence.py; the watchdog's STALE_SUCCESS_MIN is
    every_hours x 60 + 60 with the alert and data thresholds above it;
  * the backfill switch is not committed (the cadence stands).
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
os.environ.pop("WCL_QUOTA_FRACTION", None)
import cadence                               # noqa: E402
import wcl_client as W                       # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


quiet = lambda m: None                        # noqa: E731

# --- 1. the file ----------------------------------------------------------------------
real = cadence.read_cadence(log=quiet)
check(real["every_hours"] == 4 and real["quota_fraction"] == 0.70
      and real["source"] == str(cadence.CADENCE_FILE),
      f"committed data/cadence.json: every 4 h at 70 % ({real})")
check(cadence.DEFAULTS == {"every_hours": 4, "quota_fraction": 0.70}, "defaults are the owner's knobs")

with tempfile.TemporaryDirectory() as tmp:
    f = pathlib.Path(tmp) / "cadence.json"
    c = cadence.read_cadence(path=f, log=quiet)
    check(c["every_hours"] == 4 and c["quota_fraction"] == 0.70 and c["source"] == "default",
          "absent file -> defaults, source=default")
    f.write_text("{not json")
    c = cadence.read_cadence(path=f, log=quiet)
    check(c["every_hours"] == 4 and c["quota_fraction"] == 0.70 and c["source"] == "default",
          "unreadable file -> defaults")
    f.write_text("[1, 2]")
    check(cadence.read_cadence(path=f, log=quiet)["source"] == "default", "non-object file -> defaults")
    f.write_text(json.dumps({"every_hours": 6, "quota_fraction": 0.5, "note": "x", "extra": 1}))
    c = cadence.read_cadence(path=f, log=quiet)
    check(c["every_hours"] == 6 and c["quota_fraction"] == 0.5 and c["source"] == str(f),
          "valid values read; extra keys ignored")
    f.write_text(json.dumps({"every_hours": "8", "quota_fraction": "0.25"}))
    c = cadence.read_cadence(path=f, log=quiet)
    check(c["every_hours"] == 8 and c["quota_fraction"] == 0.25, "numeric strings accepted")
    f.write_text(json.dumps({"every_hours": 4.0, "quota_fraction": 1}))
    c = cadence.read_cadence(path=f, log=quiet)
    check(c["every_hours"] == 4 and isinstance(c["every_hours"], int) and c["quota_fraction"] == 1.0,
          "4.0 hours is 4; a fraction of 1 is the whole limit")
    for bad in (0, -4, 4.5, 25, "four", None, True, [4], float("nan")):
        f.write_text(json.dumps({"every_hours": bad, "quota_fraction": 0.6}))
        c = cadence.read_cadence(path=f, log=quiet)
        check(c["every_hours"] == 4 and c["quota_fraction"] == 0.6,
              f"every_hours={bad!r} -> default 4, the other knob kept")
    for bad in (0, -0.5, 1.01, 70, "lots", None, False, [0.7], float("nan")):
        f.write_text(json.dumps({"every_hours": 2, "quota_fraction": bad}))
        c = cadence.read_cadence(path=f, log=quiet)
        check(c["quota_fraction"] == 0.70 and c["every_hours"] == 2,
              f"quota_fraction={bad!r} -> default 0.70, the other knob kept")
    f.write_text(json.dumps({}))
    c = cadence.read_cadence(path=f, log=quiet)
    check(c["every_hours"] == 4 and c["quota_fraction"] == 0.70 and c["source"] == str(f),
          "empty object -> both defaults, source is the file")
    msgs = []
    f.write_text(json.dumps({"every_hours": 0, "quota_fraction": 3}))
    cadence.read_cadence(path=f, log=msgs.append)
    check(len(msgs) == 2 and "every_hours" in msgs[0] and "quota_fraction" in msgs[1],
          "a bad value is said once per knob")

check(cadence.cron_expression(4) == "0 */4 * * *" and cadence.cron_expression(6) == "0 */6 * * *",
      "cron_expression: on the hour, every N hours")
check(cadence.watchdog_stale_min(4) == 300 and cadence.watchdog_stale_min(2) == 180,
      "watchdog_stale_min: every_hours x 60 + 60")

# --- 2. the client's fraction ----------------------------------------------------------
orig_file = cadence.CADENCE_FILE
with tempfile.TemporaryDirectory() as tmp:
    f = pathlib.Path(tmp) / "cadence.json"
    f.write_text(json.dumps({"every_hours": 4, "quota_fraction": 0.55}))
    cadence.CADENCE_FILE = f
    try:
        os.environ.pop("WCL_QUOTA_FRACTION", None)
        check(abs(W.quota_fraction() - 0.55) < 1e-9, "no WCL_QUOTA_FRACTION -> the file's quota_fraction")
        os.environ["WCL_QUOTA_FRACTION"] = "0.9"
        check(abs(W.quota_fraction() - 0.9) < 1e-9, "WCL_QUOTA_FRACTION=0.9 wins over the file")
        os.environ["WCL_QUOTA_FRACTION"] = "lots"
        check(abs(W.quota_fraction() - 0.55) < 1e-9, "unparseable WCL_QUOTA_FRACTION -> the file, not a constant")
        os.environ["WCL_QUOTA_FRACTION"] = "1.5"
        check(abs(W.quota_fraction() - 0.55) < 1e-9, "out-of-range WCL_QUOTA_FRACTION -> the file")
        os.environ["WCL_QUOTA_FRACTION"] = "0"
        check(abs(W.quota_fraction() - 0.55) < 1e-9, "WCL_QUOTA_FRACTION=0 -> the file")
        os.environ.pop("WCL_QUOTA_FRACTION", None)
        f.unlink()
        check(abs(W.quota_fraction() - W.DEFAULT_QUOTA_FRACTION) < 1e-9 and W.DEFAULT_QUOTA_FRACTION == 0.70,
              "no file at all -> DEFAULT_QUOTA_FRACTION, which is 0.70")
        f.write_text(json.dumps({"quota_fraction": 0.4}))
        q = W._Quota()
        check(abs(q.fraction - 0.4) < 1e-9 and abs(q.ceiling - 18000 * 0.4) < 1e-6,
              "a fresh governor takes the file's fraction; ceiling = limit x fraction")
    finally:
        cadence.CADENCE_FILE = orig_file
        os.environ.pop("WCL_QUOTA_FRACTION", None)
check(abs(W.quota_fraction() - 0.70) < 1e-9 and abs(W.QUOTA.fraction - 0.70) < 1e-9,
      "with the committed file the client and the process governor run at 0.70")

# --- 3. the Chain step's decision --------------------------------------------------------
d = cadence.chain_decision(backfill_on="0")
check(d["chain"] is False and d["next_backfill"] == "" and d["next_drain"] == "" and d["next_mode"] == "",
      f"cadence run (switch off, no drain) -> no successor ({d['reason']})")
d = cadence.chain_decision(backfill_on="0", backlog="4000")
check(d["chain"] is False, "a cadence run with a big backlog still dispatches nothing (the cron is the cadence)")
d = cadence.chain_decision(backfill_on="1")
check(d["chain"] is True and d["next_backfill"] == "true" and d["next_drain"] == "" and d["next_mode"] == "",
      "switch in force -> a long backfill successor")
d = cadence.chain_decision(backfill_on="1", draining=True, mode="fresh", backlog="10")
check(d["chain"] is True and d["next_backfill"] == "true" and d["next_mode"] == "backfill",
      "switch + fresh drain -> both carried to the successor")
d = cadence.chain_decision(backfill_on="0", draining=True, mode="fresh", backlog="10")
check(d["chain"] is True and d["next_drain"] == "true" and d["next_mode"] == "backfill" and d["next_backfill"] == "",
      "fresh drain run -> a backfill drain run, whatever the slice's remainder")
d = cadence.chain_decision(backfill_on="0", draining=True, mode="backfill", backlog="301")
check(d["chain"] is True and d["next_drain"] == "true" and d["next_mode"] == "fresh",
      "draining with 301 pending -> a fresh drain run")
d = cadence.chain_decision(backfill_on="0", draining=True, mode="", backlog="5000")
check(d["chain"] is True and d["next_mode"] == "fresh", "a plain drain run with a backlog -> fresh successor")
d = cadence.chain_decision(backfill_on="0", draining=True, mode="backfill", backlog="300")
check(d["chain"] is False and "done" in d["reason"], "draining with 300 pending (the floor) -> done, no successor")
d = cadence.chain_decision(backfill_on="0", draining=True, mode="backfill", backlog="")
check(d["chain"] is False, "draining with an unknown backlog -> done, no successor")
d = cadence.chain_decision(backfill_on="0", draining=True, mode="backfill", backlog="n/a")
check(d["chain"] is False, "draining with a garbage backlog -> done, no successor")

with tempfile.TemporaryDirectory() as tmp:
    sw = pathlib.Path(tmp) / "backfill.json"
    sw.write_text(json.dumps({"until": "2026-10-01T00:00:00Z", "share": 1.0}))
    d = cadence.chain_decision(backfill_on="", switch_path=sw, now_s=1_790_000_000, env={})
    check(d["chain"] is True and d["next_backfill"] == "true",
          "Fetch skipped (empty backfill_on) with the switch in force -> the chain survives")
    d = cadence.chain_decision(backfill_on=None, switch_path=sw, now_s=1_800_000_000, env={})
    check(d["chain"] is False, "Fetch skipped with the switch expired -> no successor")
    d = cadence.chain_decision(backfill_on="", switch_path=pathlib.Path(tmp) / "absent.json", env={})
    check(d["chain"] is False, "Fetch skipped, no switch file -> no successor")
    d = cadence.chain_decision(backfill_on="0", switch_path=sw, now_s=1_790_000_000, env={})
    check(d["chain"] is False, "Fetch said backfill_on=0 -> the switch file is not consulted")

# --- 4. the CLI ---------------------------------------------------------------------------
env = {k: v for k, v in os.environ.items() if k not in ("BACKFILL_ON", "DRAIN", "MODE", "BACKLOG", "GITHUB_OUTPUT")}
r = subprocess.run([sys.executable, "scripts/cadence.py", "chain"], cwd=ROOT, capture_output=True, text=True,
                   env={**env, "BACKFILL_ON": "0"})
lines = dict(l.split("=", 1) for l in r.stdout.strip().splitlines() if "=" in l)
check(r.returncode == 0 and lines.get("CHAIN") == "false" and lines.get("NEXT_BACKFILL") == ""
      and lines.get("NEXT_DRAIN") == "" and lines.get("NEXT_MODE") == "" and lines.get("REASON", "").startswith("'"),
      f"CLI chain (cadence run): {r.stdout.strip().splitlines()[:1]}")
r = subprocess.run([sys.executable, "scripts/cadence.py", "chain"], cwd=ROOT, capture_output=True, text=True,
                   env={**env, "BACKFILL_ON": "1", "DRAIN": "", "MODE": ""})
lines = dict(l.split("=", 1) for l in r.stdout.strip().splitlines() if "=" in l)
check(lines.get("CHAIN") == "true" and lines.get("NEXT_BACKFILL") == "true", "CLI chain (switch in force)")
r = subprocess.run([sys.executable, "scripts/cadence.py", "chain"], cwd=ROOT, capture_output=True, text=True,
                   env={**env, "BACKFILL_ON": "0", "DRAIN": "true", "MODE": "", "BACKLOG": "900"})
lines = dict(l.split("=", 1) for l in r.stdout.strip().splitlines() if "=" in l)
check(lines.get("CHAIN") == "true" and lines.get("NEXT_DRAIN") == "true" and lines.get("NEXT_MODE") == "fresh",
      "CLI chain (drain with a backlog)")
# the assignments are what bash eval sees: shell-safe
r = subprocess.run(["bash", "-c", f'eval "$({sys.executable} scripts/cadence.py chain)"; echo "$CHAIN|$REASON"'],
                   cwd=ROOT, capture_output=True, text=True, env={**env, "BACKFILL_ON": "0"})
check(r.returncode == 0 and r.stdout.startswith("false|cadence mode"), f"bash eval of the CLI output: {r.stdout.strip()!r}")
with tempfile.TemporaryDirectory() as tmp:
    out = pathlib.Path(tmp) / "out"
    r = subprocess.run([sys.executable, "scripts/cadence.py", "github-output"], cwd=ROOT, capture_output=True,
                       text=True, env={**env, "GITHUB_OUTPUT": str(out)})
    got = dict(l.split("=", 1) for l in out.read_text().splitlines() if "=" in l)
    check(r.returncode == 0 and got == {"every_hours": "4", "quota_fraction": "0.7", "cron": "0 */4 * * *"}
          and "::notice::" in r.stdout and not any(l.startswith("::") for l in out.read_text().splitlines()),
          f"CLI github-output writes the knobs and the cron, no workflow command in the file ({got})")

# --- 5. the workflows -----------------------------------------------------------------------
import yaml                                  # noqa: E402

def load(name):
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())

def on_block(doc):
    return doc.get("on") or doc.get(True) or {}

refresh = load("refresh.yml")
watchdog = load("watchdog.yml")
diagnose = load("diagnose.yml")
check(isinstance(refresh, dict) and isinstance(watchdog, dict) and isinstance(diagnose, dict), "the three workflows parse")

crons = [s.get("cron") for s in (on_block(refresh).get("schedule") or [])]
check(crons == [cadence.cron_expression(real["every_hours"])],
      f"refresh.yml cron {crons} is data/cadence.json's every_hours ({real['every_hours']})")
job = refresh["jobs"]["refresh"]
check("if" not in job, "refresh job carries no `if:` (runs on the schedule and on every dispatch)")
inputs = on_block(refresh)["workflow_dispatch"]["inputs"]
check(inputs["quota_fraction"]["default"] == "" and "cadence.json" in inputs["quota_fraction"]["description"],
      "quota_fraction dispatch input: blank = the cadence file")
steps = {s.get("name") or s.get("uses"): s for s in job["steps"]}
check(steps.get("Cadence", {}).get("id") == "cadence" and "cadence.py github-output" in steps["Cadence"]["run"],
      "a Cadence step reads the file")
fetch_frac = steps["Fetch"]["env"]["WCL_QUOTA_FRACTION"]
check(fetch_frac.strip().endswith("|| steps.cadence.outputs.quota_fraction }}") and "'0.85'" not in fetch_frac,
      "Fetch's WCL_QUOTA_FRACTION falls back to the Cadence step, never a literal")
bg = steps["Start background collectors"]["env"]
check(bg.get("WCL_QUOTA_FRACTION") == "${{ steps.cadence.outputs.quota_fraction }}",
      "the background collectors run under the Cadence step's fraction")
check("Drain mode" not in steps, "no Drain mode step: the Lightspire trinket collector and its drain window are gone (2026-09-30)")
chain = steps["Chain the next run"]
check(chain["if"].strip() == "success()" and "cadence.py chain" in chain["run"]
      and 'if [ "$CHAIN" != "true" ]' in chain["run"] and "schedule" not in chain["if"],
      "the Chain step runs on success() alone and lets cadence.py decide")
check(chain["env"]["BACKFILL_ON"] == "${{ steps.fetch.outputs.backfill_on }}"
      and chain["env"]["DRAIN"] == "${{ inputs.drain }}" and chain["env"]["MODE"] == "${{ inputs.mode }}",
      "the Chain step hands Fetch's backfill_on and the drain inputs to the decision")
timeout = str(job["timeout-minutes"])
check(timeout.endswith("|| 80 }}"), f"the plain path has an 80-minute job ({timeout})")
import re                                    # noqa: E402

def paused(name):
    """An `if: false` directive or a commented-out schedule, on a real line
    (a comment that merely explains how to pause does not count)."""
    txt = (ROOT / ".github" / "workflows" / name).read_text()
    return bool(re.search(r"^\s*if:\s*false\b", txt, re.M) or re.search(r"^\s*#\s*schedule:", txt, re.M))

check(not paused("refresh.yml"), "no paused cron or `if: false` line left in refresh.yml")

wjob = watchdog["jobs"]["watch"]
check("if" not in wjob, "watchdog job carries no `if:` pause")
wcrons = [s.get("cron") for s in (on_block(watchdog).get("schedule") or [])]
check(len(wcrons) == 1 and wcrons[0].split()[1] == "*", f"the watchdog runs hourly ({wcrons})")
wenv = watchdog["env"]
stale = int(wenv["STALE_SUCCESS_MIN"]); alert = int(wenv["ALERT_SUCCESS_MIN"]); data = int(wenv["STALE_DATA_MIN"])
check(stale == cadence.watchdog_stale_min(real["every_hours"]),
      f"watchdog STALE_SUCCESS_MIN={stale} is every_hours x 60 + 60 ({cadence.watchdog_stale_min(real['every_hours'])})")
check(alert > stale and data > stale and data >= real["every_hours"] * 60 * 2,
      f"alert ({alert}) and stale-data ({data}) thresholds sit above the quiet threshold and two periods")
check(not paused("watchdog.yml"), "no paused cron or `if: false` line left in watchdog.yml")

# --- 6. the switch is off ------------------------------------------------------------------
check(not (ROOT / "data" / "backfill.json").exists(), "data/backfill.json is not committed: the cadence stands")

print()
if fails:
    print(f"FAIL  {fails} check(s) failed")
    sys.exit(1)
print("PASS  cadence: the file, the client's cap, the chain decision, the workflows")
