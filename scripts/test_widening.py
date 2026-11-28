#!/usr/bin/env python3
"""The widening of 2026-11-28: journals and a seed that were kept at 16 days meeting a wider window.

Owner: "start keeping 3 weeks of data (+3 days to accomodate resets)". The window had only ever been narrowed
before. Three things have to hold when it is widened, and each is run here against the real function:

  A. builder    apply_retention() on a frame whose oldest row is the old disk edge publishes every row it still
                holds inside the new cut, and says through window_before_data when the window reaches back
                before the data (the oldest reset is then part-filled until the journals have grown into it);
  B. pruner     prune() at the new width drops nothing and rewrites nothing: the rows between the old and the
                new edge do not come back, and nothing more is lost;
  C. collector  load_fights() admits a listed run between the old and the new disk cut again, and the backlog
                counts it only if it was never fetched. A run fetched once and pruned since keeps its marker in
                summaries_done.txt (exempt from the prune), so it is NOT bought again: widening the window costs
                no quota for the days already let go, and those days stay absent.
"""
import copy
import json
import pathlib
import sys
import tempfile

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import build_site_data as B      # noqa: E402
import fetch_data as fd          # noqa: E402
import prune_journals as PJ      # noqa: E402
import retention as R            # noqa: E402

OLD_DISK = 16          # what the disk kept from 2026-09-14 to 2026-11-28
fails = 0
def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1

assert R.RETENTION_DISK_DAYS > OLD_DISK, "this file is about a window WIDER than the journals were kept at"
DAY = R.DAY_MS

# ---- A. the builder ---------------------------------------------------------------------------------------
ANCHOR = pd.Timestamp("2026-11-27T19:35:00Z")            # a Friday, the committed seed's own anchor to the minute
def ms(t):
    return int(pd.Timestamp(t).value // 1_000_000)
edge = ANCHOR - pd.Timedelta(days=OLD_DISK)              # 2026-11-11 19:35: where the 16-day journals begin
rows = [("US", ANCHOR), ("EU", ANCHOR - pd.Timedelta(days=3)), ("US", ANCHOR - pd.Timedelta(days=9)),
        ("US", edge), ("EU", edge), ("KR", edge)]
df = pd.DataFrame({"region": [r for r, _ in rows], "started_at": [ms(t) for _, t in rows],
                   "report_code": [f"W{i}" for i in range(len(rows))], "fight_id": 1})
out = B.apply_retention(df.copy(), "widen", now=ANCHOR + pd.Timedelta(minutes=5))
info = copy.deepcopy(B.RETENTION_INFO)
back = pd.Timedelta(days=7 * (R.RETENTION_RESETS - 1))
cut = {"US": pd.Timestamp("2026-11-24T15:00:00Z") - back, "EU": pd.Timestamp("2026-11-25T04:00:00Z") - back,
       "KR": pd.Timestamp("2026-11-25T22:00:00Z") - back}
check(info["cut"]["US"] == cut["US"].strftime("%Y-%m-%dT%H:%M:%SZ") and info["floored"] == 0 and not info["stale"],
      f"the cut is the reset cut, not the age floor: {info['cut']}")
want = {f"W{i}" for i, (reg, t) in enumerate(rows) if t >= cut[reg]}
check(set(out["report_code"]) == want, f"every row the old journals still hold inside the new cut is published: {sorted(out['report_code'])}")
check(info["window_before_data"] is (edge > min(cut.values())),
      f"window_before_data says the window reaches before the oldest row held: {info['window_before_data']} "
      f"(oldest {info['oldest_seen']}, earliest cut {min(info['cut'].values())})")
gap = {r: round((edge - c).total_seconds() / 86400, 2) for r, c in cut.items()}
print(f"     days of each region's oldest reset with no rows behind them: {gap} (negative = the cut is inside the journals)")

# ---- B. the pruner ----------------------------------------------------------------------------------------
NOW_S = 1_795_000_000.0
NOW = NOW_S * 1000
def prow(code, st):
    return json.dumps({"character": "A", "server": "S", "region": "US", "report_code": code, "fight_id": 1, "started_at": st, "dps": 1.0})
with tempfile.TemporaryDirectory() as tmp:
    d = pathlib.Path(tmp)
    ages = [0, 5, 15, 17, 20, 24, 26, 40]
    (d / "players.jsonl").write_text("".join(prow(f"A{a}", NOW - 3_600_000 - a * DAY) + "\n" for a in ages))
    (d / "gear.jsonl").write_text("".join(json.dumps({"report_code": f"A{a}", "fight_id": 1, "gear": []}) + "\n" for a in ages))
    s1 = PJ.prune(d, now_s=NOW_S, days=OLD_DISK, force=True)
    left = [json.loads(l)["report_code"] for l in (d / "players.jsonl").read_text().splitlines()]
    check(left == ["A0", "A5", "A15"], f"at {OLD_DISK} days the journal holds {left}")
    ino = (d / "players.jsonl").stat().st_ino
    s2 = PJ.prune(d, now_s=NOW_S + 60, force=True)              # the default: RETENTION_DISK_DAYS
    left2 = [json.loads(l)["report_code"] for l in (d / "players.jsonl").read_text().splitlines()]
    check(s2["days"] == R.RETENTION_DISK_DAYS and s2["counts"]["runs_to_drop"] == 0 and left2 == left
          and (d / "players.jsonl").stat().st_ino == ino and s2["refused"] is None,
          f"widened to {s2['days']} days the prune drops nothing and rewrites nothing; the rows between {OLD_DISK} and {s2['days']} days do not come back")
    check(pd.Timestamp(s1["cut"]) - pd.Timestamp(s2["cut"]) == pd.Timedelta(days=R.RETENTION_DISK_DAYS - OLD_DISK),
          f"the cut moved back {R.RETENTION_DISK_DAYS - OLD_DISK} days: {s1['cut']} -> {s2['cut']}")

# ---- C. the collector -------------------------------------------------------------------------------------
import time
NOWC = int(time.time() * 1000)
NEWEST = NOWC - 3_600_000
def entry(code, st):
    return {"report": {"code": code, "fightID": 1}, "score": 300.0, "medal": "gold", "duration": 1_500_000,
            "bracketData": 12, "affixes": [9], "server": {"region": "US"}, "startTime": st}
with tempfile.TemporaryDirectory() as tmp:
    tp = pathlib.Path(tmp)
    fd.ROOT = tp; fd.RAW = tp / "raw"; fd.PROCESSED = tp / "processed"
    fd.RAW.mkdir(); fd.PROCESSED.mkdir()
    fd.RANKINGS_FILE = fd.RAW / "rankings.jsonl"; fd.LEDGER_FILE = fd.PROCESSED / "discovered.jsonl"
    fd.SUMMARIES_DONE = fd.PROCESSED / "summaries_done.txt"; fd.PLAYERS_FILE = fd.PROCESSED / "players.jsonl"
    fd.GEAR_FILE = fd.PROCESSED / "absent_gear.jsonl"; fd.CSV_FILE = tp / "mythic_runs.csv.gz"
    mid = NEWEST - (OLD_DISK + 3) * DAY                         # past the old disk cut, inside the new one
    rk = [entry("NEW", NEWEST), entry("IN", NEWEST - 5 * DAY), entry("BANDDONE", mid), entry("BANDNEVER", mid - 3_600_000),
          entry("OLD", NEWEST - (R.RETENTION_DISK_DAYS + 2) * DAY)]
    fd.RANKINGS_FILE.write_text(json.dumps({"enc": 1, "bracket": 11, "page": 1, "more": False, "rankings": rk}) + "\n")
    fights = fd.load_fights(None)
    check(set(fights) == {"NEW:1", "IN:1", "BANDDONE:1", "BANDNEVER:1"} and fd.load_fights.out_of_window == 1,
          f"a listed run between the old and the new disk cut is admitted again: {sorted(fights)}")
    # NEW, IN and BANDDONE were fetched while they were inside the 16-day window; BANDDONE's rows were then pruned,
    # its marker was not (summaries_done.txt is exempt). BANDNEVER was first listed after it had left the old window.
    fd.SUMMARIES_DONE.write_text("NEW:1\tOK\nIN:1\tOK\nBANDDONE:1\tOK\n")
    fd.load_done.cache = None
    bl = fd.backlog_size(None)
    check(bl == 1, f"backlog after the widening: {bl} run -- the one never fetched; the one fetched and pruned is not bought again and stays absent")

print("FAILED" if fails else "PASS", f"({fails} failures)")
sys.exit(1 if fails else 0)
