#!/usr/bin/env python3
"""The steady-state cadence and its quota cap: data/cadence.json.

Owner, 2026-09-30: "after the backfill is done, move to a cadence of
updating the data every 4 hours or so ... make sure that you coordinate the
quota between the two sets of runs so I never end up using more than 70% of
my API quota."

The file holds the owner's two knobs and nothing else decides them:

    every_hours     the refresh cron's period. refresh.yml's cron line is
                    `0 */<every_hours> * * *` (UTC; the hourly quota window
                    resets at :00 UTC, so every scheduled run starts on a
                    fresh window) and the watchdog's quiet threshold is
                    every_hours + 1 h -- both are pinned to this file by
                    scripts/test_cadence.py, so changing the period is: edit
                    the file, edit the cron, edit STALE_SUCCESS_MIN, run the
                    test.
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
drain with a backlog) a run dispatches NOTHING -- the cron is the cadence.
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
MAX_EVERY_HOURS = 24          # a cron `0 */N * * *` past a day is not a cadence
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


def cron_expression(every_hours: int) -> str:
    """The refresh cron line for a period: every N hours on the hour, UTC."""
    return f"0 */{int(every_hours)} * * *"


def watchdog_stale_min(every_hours: int) -> int:
    """The watchdog's quiet threshold (STALE_SUCCESS_MIN): one period plus an
    hour of slack for GitHub's cron delays, so a healthy cadence is never
    re-dispatched and a dropped tick is revived within the hour."""
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
        reasons.append("cadence mode: no successor is dispatched; the cron is the cadence")
    out["reason"] = "; ".join(reasons)
    return out


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "show"
    if cmd == "show":
        c = read_cadence()
        print(f"every_hours={c['every_hours']} quota_fraction={c['quota_fraction']} "
              f"cron={cron_expression(c['every_hours'])!r} "
              f"watchdog_stale_min={watchdog_stale_min(c['every_hours'])} "
              f"source={c['source']}")
        return 0
    if cmd == "github-output":
        # key=value lines ONLY (a workflow command in $GITHUB_OUTPUT fails
        # the step); the notice goes to stdout
        c = read_cadence()
        lines = {"every_hours": c["every_hours"], "quota_fraction": c["quota_fraction"],
                 "cron": cron_expression(c["every_hours"])}
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
    print(f"usage: {sys.argv[0]} [show|github-output|chain]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
