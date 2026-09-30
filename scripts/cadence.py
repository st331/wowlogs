#!/usr/bin/env python3
"""The steady-state cadence and its quota cap: data/cadence.json.

Owner, 2026-09-30: "after the backfill is done, move to a cadence of
updating the data every 4 hours or so ... make sure that you coordinate the
quota between the two sets of runs so I never end up using more than 70% of
my API quota."

The file holds the owner's two knobs and nothing else decides them:

    every_hours     the refresh period. Every refresh run's last step arms
                    timer.yml for every_hours after the run's own start
                    (next_fire_at), and the timer sleeps until then
                    (timer_wait_s) and dispatches the next run unless one is
                    running or started less than a period minus half an hour
                    ago (fresh_min). It is a timer and not a cron because
                    GitHub fired this repository's schedules 5-8 times a day
                    with gaps of 2-6.5 h over 12-28 September 2026, whatever
                    the expression asked for; a dispatched run starts within
                    seconds. The watchdog's EVERY_HOURS and its quiet
                    threshold (every_hours + 1 h) are pinned to this file by
                    scripts/test_cadence.py, so changing the period is: edit
                    the file, edit watchdog.yml's EVERY_HOURS and
                    STALE_SUCCESS_MIN, run the test.
    quota_fraction  the share of the hourly Warcraft Logs limit ANY collector
                    process may spend. wcl_client.quota_fraction() reads it
                    whenever WCL_QUOTA_FRACTION is not in the environment,
                    and the workflow passes it into every step that runs a
                    client besides. The governor measures the ceiling against
                    the account's LIVE pointsSpentThisHour, so the sweep, the
                    bundle, the keystone collector and the
                    owner's own lookups from the vetting site together never
                    pass it; a process that starts with the hour already over
                    the ceiling sleeps to the reset within its cap or stops
                    cleanly (QuotaDeadline), never pushes past.

read_cadence() never raises: a missing or unreadable file, or a bad value,
is the default for that knob (DEFAULTS), said once on stdout.

The backfill switch (data/backfill.json, execution.backfill_mode) overrides
both while its `until` is in the future: the governor runs at the switch's
`share` and the Chain step dispatches a successor the moment a run ends, so
the chain covers the switch's span back to back. chain_decision() is that
step's whole decision: in cadence mode (no switch, no explicitly dispatched
drain with a backlog) a run dispatches NOTHING of its own -- the timer is
the cadence, and every run arms it whatever the decision.
"""
from __future__ import annotations

import json
import os
import pathlib
import shlex
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CADENCE_FILE = ROOT / "data" / "cadence.json"
DEFAULTS = {"every_hours": 4, "quota_fraction": 0.70}
MAX_EVERY_HOURS = 24          # a period past a day is not a cadence
# the timer's sleep is capped below a job's 6-hour limit; a period longer
# than the cap is simply armed again by the watchdog when the timer ends
MAX_TIMER_WAIT_S = 5 * 3600 + 1800
# a refresh that started within (period - this) of the timer's slot is
# "fresh": the watchdog or a hand dispatch got there first, the timer stands
# down (and that run armed its own timer)
FRESH_SLACK_MIN = 30
# a refresh publishes about this long after it starts; the timer's second
# witness (the site's build stamp) is judged fresh a run's length earlier
RUN_LENGTH_MIN = 15
# an explicitly dispatched drain keeps alternating fresh/backfill runs only
# while this many runs are still pending (refresh.yml's drain machinery)
DRAIN_BACKLOG_FLOOR = 300


def read_cadence(path=None, log=print) -> dict:
    """{every_hours, quota_fraction, source}. Each knob is the file's value
    when it is present and valid (every_hours a whole number of hours in
    1..24, quota_fraction in (0, 1]) and its default otherwise; `source` is
    the file's path, or "default" when the file could not be read at all."""
    p = pathlib.Path(path if path is not None else CADENCE_FILE)
    out = dict(DEFAULTS)
    out["source"] = "default"
    try:
        doc = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        log(f"[cadence] {p} unreadable ({type(e).__name__}); using the defaults "
            f"every_hours={DEFAULTS['every_hours']} quota_fraction={DEFAULTS['quota_fraction']}")
        return out
    if not isinstance(doc, dict):
        log(f"[cadence] {p} is not a JSON object; using the defaults")
        return out
    out["source"] = str(p)

    eh = doc.get("every_hours", DEFAULTS["every_hours"])
    ok = False
    if not isinstance(eh, bool):
        try:
            f = float(eh)
            ok = f == f and 0 < f <= MAX_EVERY_HOURS and f == int(f)
        except (TypeError, ValueError):
            ok = False
    if ok:
        out["every_hours"] = int(float(eh))
    else:
        log(f"[cadence] every_hours={eh!r} is not a whole number of hours in "
            f"1..{MAX_EVERY_HOURS}; using {DEFAULTS['every_hours']}")

    qf = doc.get("quota_fraction", DEFAULTS["quota_fraction"])
    ok = False
    if not isinstance(qf, bool):
        try:
            f = float(qf)
            ok = f == f and 0 < f <= 1
        except (TypeError, ValueError):
            ok = False
    if ok:
        out["quota_fraction"] = float(qf)
    else:
        log(f"[cadence] quota_fraction={qf!r} is not in (0, 1]; using "
            f"{DEFAULTS['quota_fraction']}")
    return out


def next_fire_at(start_s, every_hours: int) -> int:
    """The timer's slot for the run after one that started at start_s: one
    period later, start to start, so the cadence is every_hours whatever a
    run's own length."""
    return int(start_s) + int(every_hours) * 3600


def timer_wait_s(fire_at, now_s, cap_s: int = MAX_TIMER_WAIT_S) -> int:
    """How long the timer sleeps: until fire_at, never negative (a slot
    already past fires at once; a blank or bad fire_at is "now"), never past
    the cap (a job may run 6 h)."""
    try:
        f = int(float(fire_at))
    except (TypeError, ValueError):
        return 0
    return max(0, min(int(cap_s), f - int(now_s)))


def fresh_min(every_hours: int, slack_min: int = FRESH_SLACK_MIN) -> int:
    """A refresh younger than this many minutes when the timer fires makes
    the timer stand down: a period minus the slack, so a run the watchdog or
    a hand started shortly before the slot is not doubled, while one that
    started a period ago is due."""
    return max(1, int(every_hours) * 60 - int(slack_min))


def built_fresh_min(every_hours: int, slack_min: int = FRESH_SLACK_MIN,
                    run_min: int = RUN_LENGTH_MIN) -> int:
    """The site's build stamp younger than this many minutes when the timer
    fires makes the timer stand down: fresh_min less a run's length, since a
    run publishes that long after it starts. The runs list can come back
    stale (2026-09-30: ten days behind), so the timer asks both witnesses."""
    return max(1, fresh_min(every_hours, slack_min) - int(run_min))


def watchdog_stale_min(every_hours: int) -> int:
    """The watchdog's quiet threshold (STALE_SUCCESS_MIN): one period plus an
    hour of slack for a run's length and a timer's own delay, so a healthy
    cadence is never re-dispatched and a lost timer is revived within the
    hour after its slot."""
    return int(every_hours) * 60 + 60


def _flag(v) -> bool:
    return str(v if v is not None else "").strip().lower() in ("1", "true", "yes", "on")


def chain_decision(backfill_on=None, draining=False, mode: str = "",
                   backlog=None, switch_path=None, now_s=None, env=None) -> dict:
    """The Chain step's decision at the end of a successful run.

    backfill_on   Fetch's `backfill_on` output: "1" while data/backfill.json
                  was in force for this run's collector, "0" when it was not,
                  "" / None when Fetch did not run (a tripped journal guard,
                  a paused Fetch) -- then the switch file itself is consulted,
                  so a backfill chain survives a run that could not fetch.
    draining      this run was an explicitly dispatched drain (inputs.drain
                  or inputs.mode set); mode is its "fresh" / "backfill" / "".
    backlog       Fetch's `backlog` output (runs still pending), None unknown.

    Returns {chain, next_backfill, next_drain, next_mode, reason}: `chain`
    False means dispatch nothing (cadence mode: the cron is the cadence);
    the next_* strings are the successor's dispatch inputs.
    """
    out = {"chain": False, "next_backfill": "", "next_drain": "",
           "next_mode": "", "reason": ""}
    reasons = []
    raw = "" if backfill_on is None else str(backfill_on).strip()
    if raw == "":
        try:
            sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
            import execution as ex
            on = ex.backfill_mode(now_s=now_s, path=switch_path, env=env) is not None
        except Exception as e:                 # noqa: BLE001 -- a broken switch is off
            on = False
            reasons.append(f"switch unreadable ({type(e).__name__})")
        if on:
            reasons.append("Fetch did not run; data/backfill.json is in force")
    else:
        on = _flag(raw)
    if on:
        out["chain"] = True
        out["next_backfill"] = "true"
        reasons.append("bundle backfill: the switch is in force -> successor is a long backfill run")

    try:
        pending = int(float(backlog)) if backlog not in (None, "") else 0
    except (TypeError, ValueError):
        pending = 0
    mode = (mode or "").strip().lower()
    if draining and mode == "fresh":
        # a fresh run cannot judge the backlog (its pending set is the
        # 12-hour slice): it always hands over to a backfill run, which can
        out["chain"] = True
        out["next_drain"], out["next_mode"] = "true", "backfill"
        reasons.append(f"drain: fresh slice done ({pending} left in it) -> successor is a "
                       f"backfill run, which judges the real backlog")
    elif draining and pending > DRAIN_BACKLOG_FLOOR:
        out["chain"] = True
        out["next_drain"], out["next_mode"] = "true", "fresh"
        reasons.append(f"drain: {pending} runs still pending -> successor is a fresh run")
    elif draining:
        reasons.append(f"drain: backlog {pending} -> done")

    if not out["chain"]:
        reasons.append("cadence mode: no successor is dispatched; the timer is the cadence")
    out["reason"] = "; ".join(reasons)
    return out


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "show"
    if cmd == "show":
        c = read_cadence()
        print(f"every_hours={c['every_hours']} quota_fraction={c['quota_fraction']} "
              f"fresh_min={fresh_min(c['every_hours'])} "
              f"watchdog_stale_min={watchdog_stale_min(c['every_hours'])} "
              f"source={c['source']}")
        return 0
    if cmd == "github-output":
        # key=value lines ONLY (a workflow command in $GITHUB_OUTPUT fails
        # the step); the notice goes to stdout
        c = read_cadence()
        lines = {"every_hours": c["every_hours"], "quota_fraction": c["quota_fraction"]}
        path = os.environ.get("GITHUB_OUTPUT")
        if path:
            with open(path, "a") as fh:
                for k, v in lines.items():
                    fh.write(f"{k}={v}\n")
        print(" ".join(f"{k}={v}" for k, v in lines.items()))
        print(f"::notice::cadence: a refresh every {c['every_hours']} h, every collector "
              f"capped at {c['quota_fraction']:.0%} of the hourly Warcraft Logs limit "
              f"({c['source']})")
        return 0
    if cmd == "timer":
        # shell assignments for timer.yml's sleep step to eval: WAIT_S,
        # FIRE_AT (resolved: a blank input is now), EVERY_HOURS, FRESH_MIN,
        # BUILT_FRESH_MIN
        import time
        c = read_cadence()
        now = int(time.time())
        raw = (os.environ.get("FIRE_AT") or "").strip()
        wait = timer_wait_s(raw, now)
        print(f"WAIT_S={wait}")
        print(f"FIRE_AT={now + wait}")
        print(f"EVERY_HOURS={c['every_hours']}")
        print(f"FRESH_MIN={fresh_min(c['every_hours'])}")
        print(f"BUILT_FRESH_MIN={built_fresh_min(c['every_hours'])}")
        return 0
    if cmd == "chain":
        # shell assignments for the Chain step to eval: CHAIN, NEXT_BACKFILL,
        # NEXT_DRAIN, NEXT_MODE, REASON (quoted)
        e = os.environ
        d = chain_decision(backfill_on=e.get("BACKFILL_ON"),
                           draining=_flag(e.get("DRAIN")) or bool((e.get("MODE") or "").strip()),
                           mode=e.get("MODE", ""), backlog=e.get("BACKLOG"))
        print(f"CHAIN={'true' if d['chain'] else 'false'}")
        print(f"NEXT_BACKFILL={d['next_backfill']}")
        print(f"NEXT_DRAIN={d['next_drain']}")
        print(f"NEXT_MODE={d['next_mode']}")
        print(f"REASON={shlex.quote(d['reason'])}")
        return 0
    print(f"usage: {sys.argv[0]} [show|github-output|timer|chain]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
