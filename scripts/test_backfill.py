#!/usr/bin/env python3
"""Backfill mode (scripts/backfill.py, execution.backfill_mode, the governor's
deadline), pinned:

  * the switch: on while data/backfill.json's `until` is in the future, off
    once it has passed, off when the file is absent or unreadable, `share`
    read and clamped to (0, 1], BUNDLE_BACKFILL=off forces it off;
  * the gate in admit_all mode admits every rostered run whatever the counts,
    still refuses a run with no roster, and the pause file still wins;
  * the client's sleep cap: the deadline overrides WCL_MAX_SLEEP_S, a reset
    past the deadline raises QuotaDeadline instead of sleeping;
  * selection: the four passes in order, newest first inside each, with the
    counters updated ONLINE as runs are taken (a run that would have been a
    pass-1 run is not, once the runs taken before it filled its cells);
  * runs_from_frame: the trailing 14 days at +10 and up, exec != 1, last copy
    of a row wins, marked runs and runs with any bundled row excluded, the
    counters seeded from the window's bundled rows;
  * row replacement, end to end: a journal of Summary-only rows, run() with a
    fake fetch returning the real fixture's bundle nodes, then the journal
    holds both copies, export() keeps the bundled copy only, the runs journal
    has the run record, the markers file has OK / FAILED / EMPTY, and the
    gate's rebuild counts each player once;
  * the wall-clock deadline stops submissions (stop=wall), a QuotaDeadline from
    the fetch ends the backfill as stop=budget with everything journaled.
"""
import gzip
import json
import os
import pathlib
import sys
import tempfile
import time
from collections import Counter

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
os.environ.setdefault("WCL_MAX_SLEEP_S", "480")
import execution as ex                       # noqa: E402
import fetch_data as fd                      # noqa: E402
import backfill as bf                        # noqa: E402
import wcl_client as W                       # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "scripts" / "fixtures" / "bundle_shape_f.json"
NOW_S = time.time()
NOW_MS = NOW_S * 1000
DAY = ex.DAY_MS
fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


class _Hero:
    def resolve(self, tree):
        return "Hero"


# --- 1. the switch ------------------------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    sw = pathlib.Path(tmp) / "backfill.json"
    sw.write_text(json.dumps({"until": "2026-09-28T10:40:00Z", "share": 1.0, "note": "x"}))
    on = ex.backfill_mode(now_s=1_790_000_000, path=sw, env={})      # 2026-09-21
    check(on is not None and on["share"] == 1.0 and on["until"] == "2026-09-28T10:40:00Z"
          and abs(on["until_s"] - 1_790_592_000) < 1, f"switch on before `until` ({on})")
    check(ex.backfill_mode(now_s=1_790_635_200, path=sw, env={}) is None, "switch off once `until` has passed")
    check(ex.backfill_mode(now_s=1_790_000_000, path=pathlib.Path(tmp) / "absent.json", env={}) is None,
          "switch off when the file is absent")
    sw.write_text("{not json")
    check(ex.backfill_mode(now_s=1_790_000_000, path=sw, env={}) is None, "switch off when the file is unreadable")
    sw.write_text(json.dumps({"until": "2026-09-28T10:40:00+00:00", "share": 0.6}))
    check(ex.backfill_mode(now_s=1_790_000_000, path=sw, env={})["share"] == 0.6, "share read (offset form of until)")
    sw.write_text(json.dumps({"until": "2026-09-28T10:40:00Z", "share": 7}))
    check(ex.backfill_mode(now_s=1_790_000_000, path=sw, env={})["share"] == 1.0, "share out of range -> 1.0")
    sw.write_text(json.dumps({"until": "2026-09-28T10:40:00Z"}))
    check(ex.backfill_mode(now_s=1_790_000_000, path=sw, env={})["share"] == 1.0, "share missing -> 1.0")
    check(ex.backfill_mode(now_s=1_790_000_000, path=sw, env={"BUNDLE_BACKFILL": "off"}) is None,
          "BUNDLE_BACKFILL=off forces it off")
    sw.write_text(json.dumps({"until": "not a date"}))
    check(ex.backfill_mode(now_s=1_790_000_000, path=sw, env={}) is None, "an unparseable `until` is off")
    real = ex.backfill_mode(now_s=1_790_000_000, env={})
    check(real is not None and real["until"] == "2026-09-28T10:40:00Z" and real["share"] == 1.0,
          "the committed data/backfill.json: until 2026-09-28T10:40:00Z, share 1.0")

# --- 2. the gate in admit_all mode ------------------------------------------------------
g = ex.BundleGate(None, now_ms=NOW_MS, paused=False, admit_all=True)
for i in range(300):
    g.record("Mage-Arcane", "Altar of Fangs", 18, NOW_MS)
check(g.admits(["Mage-Arcane"] * 5, "Altar of Fangs", 18) is True and g.stats["admitted"] == 1,
      "admit_all: a run whose every cell is far over the quota is admitted")
check(g.admits([], "Altar of Fangs", 18) is False and g.stats["no_roster"] == 1, "admit_all: no roster -> still refused")
gp = ex.BundleGate(None, now_ms=NOW_MS, paused=True, admit_all=True)
check(gp.admits(["Mage-Arcane"], "Altar of Fangs", 18) is False, "the pause file wins over admit_all")
gn = ex.BundleGate(None, now_ms=NOW_MS, paused=False, admit_all=False)
for i in range(100):
    gn.record("Mage-Arcane", "Altar of Fangs", 18, NOW_MS)
check(gn.admits(["Mage-Arcane"], "Altar of Fangs", 18) is False, "without admit_all the quota still gates")

# --- 3. the governor's deadline --------------------------------------------------------
saved_deadline = W.QUOTA.deadline
W.QUOTA.deadline = None
os.environ["WCL_MAX_SLEEP_S"] = "480"
check(W.WCLClient._sleep_cap() == 480.0, "sleep cap: the environment's when no deadline is set")
W.QUOTA.deadline = time.time() + 1000
check(990 < W.WCLClient._sleep_cap() <= 1000, "sleep cap: seconds to the deadline when one is set (env ignored)")
W.QUOTA.deadline = time.time() - 50
check(W.WCLClient._sleep_cap() == 1.0, "sleep cap: a passed deadline reads as 1 s, never 0 (= unlimited)")


class _Fake:
    status_code, headers = 200, {}

    def json(self):
        return {"data": {"worldData": {}, "rateLimitData": {"limitPerHour": 18000, "pointsSpentThisHour": 0,
                                                              "pointsResetIn": 1800}}}

    def raise_for_status(self):
        pass


_post, _tok = W.requests.Session.post, W.get_token
W.requests.Session.post = staticmethod(lambda *a, **k: _Fake())
W.get_token = lambda s: ("fake", "env")
try:
    c = W.WCLClient(verbose=False)
    W.QUOTA.reset_in = 1800
    W.QUOTA.deadline = time.time() + 60
    try:
        c._sleep_for_reset()
        raised = False
    except W.QuotaDeadline:
        raised = True
    check(raised, "a reset 30 min away with the deadline 1 min away -> QuotaDeadline, no sleep")
finally:
    W.requests.Session.post, W.get_token = _post, _tok
    W.QUOTA.deadline = saved_deadline

# --- 4. selection order and the online counters ----------------------------------------
ALTAR, MURDER = "Altar of Fangs", "Murder Row"


def mk(key, start, level, dungeon, specs):
    return {"key": key, "code": key.split(":")[0], "fid": 1, "start": start, "level": level,
            "dungeon": dungeon, "specs": specs, "region": "US", "score": 1.0, "medal": "gold",
            "affixes": [9], "rank_duration_ms": 1_000_000}


COMMON = ["Mage-Arcane", "Warrior-Arms", "Paladin-Holy", "DeathKnight-Blood", "Hunter-Marksmanship"]
RARE = ["Mage-Arcane", "Warrior-Arms", "Paladin-Holy", "DeathKnight-Blood", "Druid-Feral"]
band_counts, exact_counts = Counter(), Counter()
for sk in COMMON:
    band_counts[(sk, ALTAR, 18)] = 150            # band full
    exact_counts[(sk, ALTAR, 18)] = 150           # exact full at 18 ...
    exact_counts[(sk, ALTAR, 19)] = 50            # ... open under 100 at 19
runs = [mk("R1:1", NOW_MS - 1 * DAY, 18, ALTAR, COMMON),      # everything full -> rest
        mk("R2:1", NOW_MS - 2 * DAY, 19, ALTAR, COMMON),      # exact 19 under 100 -> pass exact_lt100
        mk("R3:1", NOW_MS - 3 * DAY, 18, ALTAR, RARE),        # Druid-Feral band empty -> pass 1
        mk("R4:1", NOW_MS - 4 * DAY, 18, MURDER, COMMON),     # Murder Row band cells empty -> pass 1
        mk("R5:1", NOW_MS - 5 * DAY, 18, ALTAR, RARE),        # pass 1 (Feral at 1)
        mk("R6:1", NOW_MS - 0.5 * DAY, 18, ALTAR, COMMON)]    # newest, everything full -> rest, first of the rest
# 25 older RARE runs (6..30 days old, all older than R5): with R3 and R5 the Druid-Feral
# band cell reaches 20 after 18 of them in pass 1; the other 7 go to pass 2 (< 100)
for i in range(25):
    runs.append(mk(f"Q{i:02d}:1", NOW_MS - (6 + i) * DAY, 18, ALTAR, RARE))
order, taken = bf.select_runs(list(runs), Counter(band_counts), Counter(exact_counts))
keys = [r["key"] for r in order]
check(keys[:2] == ["R3:1", "R4:1"], f"pass 1 newest first: R3 (rare spec), R4 (empty dungeon) ({keys[:4]})")
p1 = keys[:taken["band_lt20"]]
check(taken["band_lt20"] == 21 and p1[2] == "R5:1" and p1[3] == "Q00:1" and p1[-1] == "Q17:1"
      and all(k.startswith("Q") for k in p1[3:]),
      f"pass 1: R3, R4, R5 and exactly 18 Q runs -- the Feral cell reaches 20 and closes (online counter), {taken}")
p2 = keys[taken["band_lt20"]:taken["band_lt20"] + taken["band_lt100"]]
check(taken["band_lt100"] == 7 and p2 == [f"Q{i:02d}:1" for i in range(18, 25)],
      f"pass 2: the remaining 7 Feral runs (band now 20..26 < 100), newest first ({p2[:2]}..)")
check(taken["exact_lt20"] == 0 and taken["exact_lt100"] == 1
      and keys[taken["band_lt20"] + taken["band_lt100"]] == "R2:1",
      "pass 3: R2 alone (exact +19 cells at 50 < 100; +18 exact cells full)")
check(keys[-2:] == ["R6:1", "R1:1"] and taken["rest"] == 2, "pass 4: the rest, newest first (R6 then R1)")
check(len(keys) == len(runs) == len(set(keys)), "every run selected exactly once")
bc2 = Counter(band_counts)
bf.select_runs([runs[2]], bc2, Counter(exact_counts))
check(bc2[("Druid-Feral", ALTAR, 18)] == 1 and bc2[("Mage-Arcane", ALTAR, 18)] == 151,
      "taking a run adds one row to each of its five band cells")

# --- 5. runs_from_frame --------------------------------------------------------------
rd = json.loads(FIXTURE.read_text())["data"]["reportData"]


def fight(alias, code, level, start, region="EU"):
    comp = rd[alias]["table"]["data"]["composition"]
    return {"code": code, "fid": 8, "dungeon": ALTAR, "key_level": level, "region": region, "score": 400.0,
            "medal": "gold", "affixes": [9, 10], "start_time": int(start),
            "rank_duration_ms": rd[alias]["table"]["data"]["totalTime"] + 20_000,
            "specs": [ex.spec_key(c["type"], c["specs"][0]["spec"]) for c in comp]}


def plain_rows(alias, code, level, start):
    rows, gear = fd.parse_node(fight(alias, code, level, start), {"table": rd[alias]["table"]}, _Hero())
    return rows


journal = []
journal += plain_rows("a1", "NewRunCode0001xx", 16, NOW_MS - 1 * DAY)        # candidate
journal += plain_rows("a2", "OldRunCode0002xx", 16, NOW_MS - 15 * DAY)       # past the 14-day window (inside export's 16-day one)
journal += plain_rows("a3", "LowKeyCode0003xx", 7, NOW_MS - 1 * DAY)         # below +10
journal += plain_rows("a4", "MarkedCode0004xx", 14, NOW_MS - 2 * DAY)        # marked FAILED earlier
journal += plain_rows("a5", "DoneRunCode005xx", 15, NOW_MS - 3 * DAY)        # replaced below by bundled rows
brows, _ = fd.parse_node({**fight("a5", "DoneRunCode005xx", 15, NOW_MS - 3 * DAY), "_bundle": True}, rd["a5"], _Hero())
journal += brows                                                              # the later copy: exec 1
journal += plain_rows("a6", "Undated0006xxxxx", 15, NOW_MS - 1 * DAY)
for r in journal[-5:]:
    r["started_at"] = None                                                    # undated: not in the window
journal += plain_rows("a7", "Newest0007xxxxxx", 12, NOW_MS - 0.2 * DAY)      # candidate, newest
frame = pd.DataFrame(journal)
runs, bcount, ecount, facts = bf.runs_from_frame(frame, NOW_MS, skip={"MarkedCode0004xx:8"})
keys = sorted(r["key"] for r in runs)
check(keys == ["NewRunCode0001xx:8", "Newest0007xxxxxx:8"],
      f"candidates: in the window, +10 and up, no bundled row, not marked, dated ({keys})")
r0 = next(r for r in runs if r["code"] == "NewRunCode0001xx")
check(r0["level"] == 16 and r0["dungeon"] == ALTAR and sorted(r0["specs"]) == sorted(fight("a1", "x", 16, 0)["specs"])
      and r0["affixes"] == [9, 10]
      and r0["rank_duration_ms"] == int(round((rd["a1"]["table"]["data"]["totalTime"] + 20_000) / 1000, 1) * 1000)
      and r0["region"] == "EU" and r0["start"] == int(NOW_MS - 1 * DAY),
      "a candidate carries level, dungeon, the five specs, affixes, the keystone clock and its start")
a5_specs = fight("a5", "x", 15, 0)["specs"]
check(facts["window_runs_bundled"] == 1 and sum(bcount.values()) == 5 and sum(ecount.values()) == 5
      and all(bcount[(sk, ALTAR, 14)] == 1 and ecount[(sk, ALTAR, 15)] == 1 for sk in a5_specs),
      "counters seeded from the window's bundled rows (DoneRun's last copy, exec 1: five band-14 / exact-15 cells)")
f0 = bf.fight_of(r0, {"code": "NewRunCode0001xx", "fid": 8, "score": 555.0, "medal": "silver", "affixes": [1]})
check(f0["_bundle"] is True and f0["score"] == 555.0 and f0["medal"] == "silver" and f0["affixes"] == [1]
      and f0["specs"] == r0["specs"] and f0["key_level"] == 16 and f0["rank_duration_ms"] == r0["rank_duration_ms"],
      "fight_of: the batch machinery's fight dict, the leaderboard's fresher fields winning")

# --- 6. row replacement end to end, with a fake fetch -----------------------------------
NODE_FOR = {"NewRunCode0001xx": "a1", "Newest0007xxxxxx": "a7", "GoneRunCode008xx": "a3", "EmptyRunCode09xx": "a4"}


def fake_fetch(batch):
    rep, errs = {}, {}
    for i, f in enumerate(batch):
        alias = NODE_FOR[f["code"]]
        if f["code"].startswith("Gone"):
            errs[f"a{i}"] = "This report does not exist."
            rep[f"a{i}"] = None
        elif f["code"].startswith("Empty"):
            rep[f"a{i}"] = {"table": rd[alias]["table"], **{k: None for k in ex.BUNDLE_TABLES}}
        else:
            assert f.get("_bundle") is True and "table" in fd.batch_query([f])
            rep[f"a{i}"] = rd[alias]
    return batch, rep, errs, 0.0


with tempfile.TemporaryDirectory() as tmp:
    tp = pathlib.Path(tmp)
    (tp / "data").mkdir()
    fd.ROOT = tp
    fd.PROCESSED = tp / "processed"
    fd.PROCESSED.mkdir()
    fd.PLAYERS_FILE = fd.PROCESSED / "players.jsonl"
    fd.GEAR_FILE = fd.PROCESSED / "gear.jsonl"
    fd.RUNS_FILE = fd.PROCESSED / "runs.jsonl"
    fd.EXEC_GATE_FILE = fd.PROCESSED / "exec_quota.json"
    fd.SUMMARIES_DONE = fd.PROCESSED / "summaries_done.txt"
    fd.RANKINGS_FILE = tp / "absent_rankings.jsonl"
    fd.GEAR_CSV = tp / "gear.jsonl.gz"
    fd.CSV_FILE = tp / "data" / "mythic_runs.csv.gz"
    fd.fetch_summaries.hero = _Hero()
    os.environ["EXPORT_GEAR"] = "0"
    j2 = list(journal) + plain_rows("a3", "GoneRunCode008xx", 13, NOW_MS - 2 * DAY) \
        + plain_rows("a4", "EmptyRunCode09xx", 13, NOW_MS - 2.5 * DAY)
    with fd.PLAYERS_FILE.open("w") as fh:
        for r in j2:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    (fd.PROCESSED / bf.MARKERS_NAME).write_text("MarkedCode0004xx:8\tFAILED\tearlier\n")
    _real_fetch = fd._fetch_batch
    fd._fetch_batch = fake_fetch
    fd._OUTPUTS.clear()
    try:
        res = bf.run(None, deadline_s=time.time() + 600, now_ms=NOW_MS, listed={})
    finally:
        fd._fetch_batch = _real_fetch
    check(res["backfill.selected"] == 4 and res["backfill.bundled_runs"] == 2 and res["backfill.rows"] == 10
          and res["backfill.failed"] == 1 and res["backfill.empty"] == 1 and res["backfill.left"] == 0
          and res["backfill.stop"] == "done",
          f"run(): 4 selected, 2 bundled (10 rows), 1 gone, 1 empty, stop=done ({ {k: v for k, v in res.items() if k != 'stats'} })")
    marks = (fd.PROCESSED / bf.MARKERS_NAME).read_text().splitlines()
    check(any(m.startswith("GoneRunCode008xx:8\tFAILED") for m in marks) and "EmptyRunCode09xx:8\tEMPTY" in marks
          and "NewRunCode0001xx:8\tOK" in marks and "Newest0007xxxxxx:8\tOK" in marks,
          "markers: FAILED for the gone report, EMPTY for the tableless bundle, OK for the bundled")
    rows_all = list(fd._iter_journal(fd.PLAYERS_FILE))
    new = [r for r in rows_all if r["report_code"] == "NewRunCode0001xx"]
    check(len(new) == 10 and sum(1 for r in new if r["exec"] == 1) == 5 and new[-1]["exec"] == 1
          and isinstance(new[-1]["kicks"], int) and new[-1]["kicks_by"] is not None,
          "the journal holds the old five rows AND the five bundled rows appended after them")
    recs = list(fd._iter_journal(fd.RUNS_FILE))
    check([r["report_code"] for r in recs] == ["Newest0007xxxxxx", "NewRunCode0001xx"] and all(r["exec"] for r in recs)
          and recs[0]["int_spells"], "runs.jsonl: one run record per bundled run (newest first), with its spell tables")
    fd._OUTPUTS.clear()
    fd.export()
    csv = pd.read_csv(fd.CSV_FILE)
    per = csv.groupby("report_code")["exec"].agg(["size", "min", "max"])
    check(per.loc["NewRunCode0001xx"].tolist() == [5, 1.0, 1.0] and per.loc["Newest0007xxxxxx"].tolist() == [5, 1.0, 1.0]
          and per.loc["DoneRunCode005xx"].tolist() == [5, 1.0, 1.0] and per.loc["OldRunCode0002xx"].tolist() == [5, 0.0, 0.0]
          and per.loc["EmptyRunCode09xx"].tolist() == [5, 0.0, 0.0],
          "export(): one row per player, the bundled copy wins, untouched runs keep exec 0")
    check(csv[csv.report_code == "NewRunCode0001xx"]["kicks"].notna().all(), "the CSV carries the bundle columns for the replaced rows")
    g2 = ex.BundleGate(fd.EXEC_GATE_FILE, now_ms=NOW_MS, paused=False)
    n = g2.rebuild(fd.PLAYERS_FILE)
    check(n == 15 and g2.count("DeathKnight-Blood", ALTAR, 16) == 1,
          f"BundleGate.rebuild: {n} bundled rows -- each player counted once")
    saved = json.loads(fd.EXEC_GATE_FILE.read_text())
    check(sum(sum(c.values()) for c in saved["cells"].values()) == 10, "the gate's counter file gained the 10 backfilled rows")
    # a second run selects nothing: the two are bundled, one FAILED, one EMPTY
    fd._fetch_batch = fake_fetch
    fd._OUTPUTS.clear()
    try:
        res2 = bf.run(None, deadline_s=time.time() + 600, now_ms=NOW_MS, listed={})
    finally:
        fd._fetch_batch = _real_fetch
    check(res2["backfill.selected"] == 0 and res2["backfill.stop"] == "done", "a second run finds nothing left to backfill")
    # --- 7. stopping: the wall clock, and a QuotaDeadline from the fetch
    with fd.PLAYERS_FILE.open("a") as fh:
        for r in plain_rows("a1", "NewRunCode0001xx", 16, NOW_MS - 1 * DAY):
            fh.write(json.dumps({**r, "report_code": "AnotherRun0010xx"}, ensure_ascii=False) + "\n")
    NODE_FOR["AnotherRun0010xx"] = "a1"
    fd._OUTPUTS.clear()
    res3 = bf.run(None, deadline_s=time.time() - 1, now_ms=NOW_MS, listed={})
    check(res3["backfill.stop"] == "wall" and res3["backfill.bundled_runs"] == 0,
          "a passed deadline: nothing submitted, stop=wall")

    def budget_fetch(batch):
        raise W.QuotaDeadline("quota reset 3000s away, cap 60s")

    fd._fetch_batch = budget_fetch
    fd._OUTPUTS.clear()
    try:
        res4 = bf.run(None, deadline_s=time.time() + 600, now_ms=NOW_MS, listed={})
    finally:
        fd._fetch_batch = _real_fetch
    check(res4["backfill.stop"] == "budget" and res4["backfill.selected"] == 1 and res4["backfill.left"] == 1,
          "a QuotaDeadline from the fetch: stop=budget, the run stays selectable for the next run")
    health = (fd.PROCESSED / "fetch_health.txt").read_text()
    check("backfill.stop=budget" in health and "backfill.selected=1" in health and "backfill.pass_rest=" in health,
          "backfill.* health lines land in fetch_health.txt")

print()
if fails:
    sys.exit(f"FAILED ({fails} failures)")
print("PASS")
