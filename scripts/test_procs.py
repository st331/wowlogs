#!/usr/bin/env python3
"""Trinket beam benefit: the model, the collector and the sidecar, end to end
on synthetic journals and a scripted API.

Pinned:
  * benefit(): overlapping beams merge in the denominator, bands clip to the
    fight, a band can never exceed its beam window in the numerator, zero
    beams is None (no evidence), and the two reference fights' arithmetic.
  * bands_from_events(): apply/refresh/remove -> bands (any source), the
    wearer's own spawns (source = wearer, a refresh is a new beam), the
    foreign count, an open band closing at the fight end.
  * candidates(): byte prefilter + exact id check over a gear journal (a gem
    or bonus id that merely CONTAINS the digits must not match), last record
    wins, actor id carried when present.
  * load_done()/order_pending(): journaled and failed keys are both done;
    newest fight first.
  * run(): the whole loop against a scripted client -- masterData resolution
    for actor-less records, one v2 journal line per wearer-fight with bands,
    own spawns, the foreign count and the derived numbers, alias errors go
    to the failed file, a v1 (table-era, always empty) record is redone, the
    point budget stops the loop, and a second run re-does nothing.
  * procs_sidecar(): row alignment with df via _gear_key (null server joins),
    the u8/u16 columns, 255 = no beam, coverage health line, None on an
    empty journal.
Run: python3 scripts/test_procs.py
"""
import base64
import gzip
import json
import pathlib
import sys
import tempfile

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_procs as fp                                    # noqa: E402
import build_site_data as bsd                               # noqa: E402
from procs_spec import TRACKED                              # noqa: E402

T = TRACKED[0]
W = T["window_ms"]

# --- the model -----------------------------------------------------------------
r = fp.benefit([[1000, 4000]], W, 100_000)
assert r == {"n": 1, "a": W, "b": 3000, "i": 3000, "r": 0.25}, r
# two beams 5 s apart: windows [0,12] and [5,17] merge to 17 s available
r = fp.benefit([[0, 2000], [5000, 17000]], W, 100_000)
assert r["a"] == 17_000 and r["b"] == 14_000 and r["i"] == 14_000, r
assert r["r"] == round(14 / 17, 4), r
# a band that outlives its window (should not happen; the numerator must
# still be capped by availability, never exceed it)
r = fp.benefit([[0, 20_000]], W, 100_000)
assert r["a"] == W and r["i"] == W and r["r"] == 1.0, r
# the fight ends 4 s into the beam: the window clips, the ratio is of 4 s
r = fp.benefit([[96_000, 100_000]], W, 100_000)
assert r["a"] == 4000 and r["i"] == 4000 and r["r"] == 1.0, r
# no beam at all: no evidence, not 0 %
r = fp.benefit([], W, 100_000)
assert r == {"n": 0, "a": 0, "b": 0, "i": 0, "r": None}, r
# explicit spawns: a refresh at 8 s while standing in beam 1 is a second beam,
# so availability runs to 20 s; the buff band is one 15 s band
r = fp.benefit([[0, 15_000]], W, 100_000, spawns=[0, 8000])
assert r == {"n": 2, "a": 20_000, "b": 15_000, "i": 15_000, "r": 0.75}, r
# a teammate's beam blessing the wearer: a band with NO own spawn is buff time
# but no availability -- it counts only where it overlaps the wearer's own windows
r = fp.benefit([[0, 3000], [50_000, 53_000]], W, 100_000, spawns=[0])
assert r == {"n": 1, "a": W, "b": 6000, "i": 3000, "r": 0.25}, r
# the reference fights, re-derived: 23 beams / 211.0 s available / 87.4 s in
# -> 41.4 % is what diag_lightspire2 printed; here the same arithmetic on a
# hand-made band set with the same totals
bands = [[i * 30_000, i * 30_000 + 3800] for i in range(23)]
r = fp.benefit(bands, W, 1_060_000)
assert r["n"] == 23 and r["a"] == 23 * W and r["b"] == 23 * 3800, r
assert abs(r["r"] - 3800 / W) < 1e-4, r
print("benefit     : overlap merges, fight clip, cap, zero beams = None, reference arithmetic")

# --- the buff events the API returns ---------------------------------------------
T0, T1 = 3_223_104, 4_283_125
ev = [{"timestamp": T0 + 10_000, "type": "applybuff", "sourceID": 9, "targetID": 9},
      {"timestamp": T0 + 16_000, "type": "removebuff", "sourceID": 9, "targetID": 9},
      {"timestamp": T0 + 20_000, "type": "applybuff", "sourceID": 9, "targetID": 9},
      {"timestamp": T0 + 28_000, "type": "refreshbuff", "sourceID": 9, "targetID": 9},
      {"timestamp": T0 + 33_000, "type": "removebuff", "sourceID": 9, "targetID": 9},
      {"timestamp": T0 + 40_000, "type": "applybuff", "sourceID": 77, "targetID": 9},   # a teammate's beam
      {"timestamp": T0 + 41_000, "type": "removebuff", "sourceID": 77, "targetID": 9},
      {"timestamp": T1 - 2000, "type": "applybuff", "sourceID": 9, "targetID": 9}]       # open at the end
bands, spawns, foreign = fp.bands_from_events(ev, 9, T0, T1)
assert bands == [[10_000, 16_000], [20_000, 33_000], [40_000, 41_000], [T1 - T0 - 2000, T1 - T0]], bands
assert spawns == [10_000, 20_000, 28_000, T1 - T0 - 2000], spawns
assert foreign == 1, foreign
assert fp.bands_from_events([], 9, T0, T1) == ([], [], 0)
print("events      : bands any-source, own spawns incl. refresh, foreign count, open band closes at fight end")

# --- journals ----------------------------------------------------------------------
tmp = pathlib.Path(tempfile.mkdtemp())
gear = tmp / "gear.jsonl"
procs = tmp / "procs.jsonl"
failed = tmp / "procs_failed.txt"
item = T["item"]
def grec(code, fid, ch, sv, items, actor=None, **kw):
    d = {"report_code": code, "fight_id": fid, "character": ch, "server": sv,
         "class": "Mage", "spec": "Arcane",
         "gear": [{"id": i, "ilvl": 300} for i in items], "talents": None}
    if actor is not None:
        d["actor"] = actor
    d.update(kw)
    return json.dumps(d, ensure_ascii=False)
lines = [
    grec("AAA", 1, "Wearer", "Realm", [111, item], actor=9),          # actor known
    grec("BBB", 2, "Old", None, [item, 222]),                         # pre-2026-09-08 record: no actor, null server
    grec("CCC", 3, "Decoy", "Realm", [111], gems=[item]),             # digits present, item NOT worn
    json.dumps({"report_code": "DDD", "fight_id": 4, "character": "Gem", "server": "R",
                "gear": [{"id": 5, "gems": [{"id": item}]}]}),        # a gem carrying the id
    grec("AAA", 1, "Wearer", "Realm", [item], actor=9),               # duplicate -> last wins
    grec("EEE", 5, "Failed", "Realm", [item], actor=3),
    grec("FFF", 6, "Later", "Realm", [item], actor=4),
]
gear.write_text("\n".join(lines) + "\n")
c = fp.candidates(TRACKED, gear)[T["key"]]
assert set(c) == {("AAA", 1, "Wearer", "Realm"), ("BBB", 2, "Old", ""),
                  ("EEE", 5, "Failed", "Realm"), ("FFF", 6, "Later", "Realm")}, set(c)
assert c[("AAA", 1, "Wearer", "Realm")]["actor"] == 9
assert c[("BBB", 2, "Old", "")]["actor"] is None
print("candidates  : exact item match (gem/decoy digits ignored), last record wins, actor carried")

failed.write_text("EEE:5:Failed\tlscore\tprivate report\n")
done = fp.load_done(procs, failed)
assert fp._done_has(done["lscore"], ("EEE", 5, "Failed", "Realm"))
assert not fp._done_has(done["lscore"], ("AAA", 1, "Wearer", "Realm"))
fights = {"AAA:1": {"start_time": 100}, "BBB:2": {"start_time": 300}, "FFF:6": {"start_time": 200}}
order = fp.order_pending([k for k in c if not fp._done_has(done["lscore"], k)], fights)
assert [k[0] for k in order] == ["BBB", "FFF", "AAA"], order
print("done/order  : failed file counts as done; newest fight first")

# --- the loop, against a scripted client ----------------------------------------
class FakeClient:
    def __init__(self):
        self.spent = 0.0
        self.queries = []
    def query(self, gql, est_cost=1.0):
        self.queries.append(gql)
        self.spent += est_cost
        rd = {}
        if "masterData" in gql:
            # BBB's roster: the name resolves to actor 42
            rd["a0"] = {"masterData": {"actors": [{"id": 42, "name": "Old", "server": "X"},
                                                   {"id": 43, "name": "Someone", "server": "X"}]}}
            return {"reportData": rd, "_errors": []}
        # buff events: one alias per wearer-fight, in query order
        import re
        errs = []
        assert "table(" not in gql, "the Buffs table with source+target returns nothing (run 827)"
        for m in re.finditer(r'(a\d+): report\(code: "(\w+)"\) \{ fights\(fightIDs: \[(\d+)\]\).*?targetID: (\d+)', gql):
            alias, code, fid, aid = m.groups(); aid = int(aid)
            if code == "FFF":
                errs.append({"path": ["reportData", alias], "message": "This report does not exist."})
                rd[alias] = None
                continue
            t0 = 1_000_000
            rd[alias] = {"fights": [{"startTime": t0, "endTime": t0 + 600_000}],
                "ev": {"nextPageTimestamp": None, "data": [
                    {"timestamp": t0 + 10_000, "type": "applybuff", "sourceID": aid, "targetID": aid},
                    {"timestamp": t0 + 16_000, "type": "removebuff", "sourceID": aid, "targetID": aid},
                    {"timestamp": t0 + 20_000, "type": "applybuff", "sourceID": aid, "targetID": aid},
                    {"timestamp": t0 + 20_500, "type": "removebuff", "sourceID": aid, "targetID": aid},
                    {"timestamp": t0 + 30_000, "type": "applybuff", "sourceID": 999, "targetID": aid},
                    {"timestamp": t0 + 31_000, "type": "removebuff", "sourceID": 999, "targetID": aid}]}}
        return {"reportData": rd, "_errors": errs}

# a table-era (v1) record for AAA: empty by construction, must NOT count as done
procs.write_text(json.dumps({"report_code": "AAA", "fight_id": 1, "character": "Wearer", "server": "Realm",
    "key": "lscore", "actor": 9, "f": 600_000, "bands": [], "n": 0, "a": 0, "b": 0, "i": 0, "r": None}) + "\n")
assert not fp._done_has(fp.load_done(procs, failed).get("lscore", set()), ("AAA", 1, "Wearer", "Realm"))
fc = FakeClient()
s = fp.run(budget_pts=100, budget_s=60, limit=None, tracked=TRACKED, gear_path=gear,
           procs_path=procs, failed_path=failed, client=fc)["lscore"]
assert s["ok"] == 2 and s["failed"] == 1 and s["transient"] == 0, s
recs = [json.loads(l) for l in procs.read_text().splitlines()]
v2 = [r for r in recs if r.get("v") == 2]
assert len(recs) == 3 and {(r["report_code"], r["actor"]) for r in v2} == {("AAA", 9), ("BBB", 42)}, recs
rb = next(r for r in v2 if r["report_code"] == "BBB")
assert rb["server"] is None and rb["key"] == "lscore" and rb["f"] == 600_000
assert rb["bands"] == [[10_000, 16_000], [20_000, 20_500], [30_000, 31_000]], rb["bands"]
assert rb["sp"] == [10_000, 20_000] and rb["x"] == 1, rb
# own windows [10,22]∪[20,32] s = 22 s available; the teammate's band at 30-31 s
# lies inside the wearer's own second window, so it counts as buff time
assert rb["n"] == 2 and rb["a"] == 22_000 and rb["b"] == 7500 and rb["i"] == 7500
assert rb["r"] == round(7500 / 22_000, 4), rb
assert "FFF:6:Later\tlscore\tThis report does not exist." in failed.read_text()
assert sum("masterData" in q for q in fc.queries) == 1, "one masterData request for the actor-less report"
# a second run: everything is done, nothing is asked
fc2 = FakeClient()
s2 = fp.run(budget_pts=100, budget_s=60, limit=None, tracked=TRACKED, gear_path=gear,
            procs_path=procs, failed_path=failed, client=fc2)["lscore"]
assert s2["pending"] == 0 and not fc2.queries, (s2, fc2.queries)
# the point budget stops the loop before the first batch when already spent
procs.unlink(); failed.write_text("")
fc3 = FakeClient(); fc3.spent = 0
s3 = fp.run(budget_pts=0, budget_s=60, limit=None, tracked=TRACKED, gear_path=gear,
            procs_path=procs, failed_path=failed, client=fc3)["lscore"]
assert s3["ok"] == 0 and s3["stopped"].startswith("point budget"), s3
print("run         : actor resolution, journal lines, alias error -> failed, budget stop, idempotent")

# --- rollover-safe budget and the systemic stop -------------------------------------
# 30 wearers with actor ids, all pending
big = tmp / "gear_big.jsonl"; bigp = tmp / "procs_big.jsonl"; bigf = tmp / "failed_big.txt"
big.write_text("\n".join(grec(f"R{i:03d}", 1, f"W{i}", "Realm", [item], actor=100 + i) for i in range(30)) + "\n")
class RolloverClient(FakeClient):
    """the hour rolls over after the 2nd request: spent drops to 0.3"""
    def query(self, gql, est_cost=1.0):
        out = super().query(gql, est_cost)
        if len(self.queries) == 2: self.spent = 0.3
        return out
rc = RolloverClient()
s5 = fp.run(budget_pts=2.5, budget_s=60, limit=None, tracked=TRACKED, gear_path=big,
            procs_path=bigp, failed_path=bigf, client=rc, workers=1)["lscore"]
# 12 aliases x 0.5 = 6 pts per request; the first request alone exceeds 2.5 ->
# stops after ONE batch even though spent was reset to 0.3 by the rollover
assert s5["ok"] == 12 and s5["stopped"].startswith("point budget"), s5
assert len(bigp.read_text().splitlines()) == 12
print("rollover    : only positive spend deltas count; the window rollover cannot unbound the budget")
class BrokenClient(FakeClient):
    """events come back, but never with the wearer as source: a broken actor id path"""
    def query(self, gql, est_cost=1.0):
        out = super().query(gql, est_cost)
        for k, v in (out.get("reportData") or {}).items():
            if v and "ev" in v:
                for e in v["ev"]["data"]: e["sourceID"] = 555
        return out
bigp.unlink(); bigf.write_text("")
bc = BrokenClient()
s6 = fp.run(budget_pts=1000, budget_s=60, limit=None, tracked=TRACKED, gear_path=big,
            procs_path=bigp, failed_path=bigf, client=bc, workers=1)["lscore"]
assert s6["ok"] == 0 and s6["stopped"].startswith("systemic"), s6
assert not bigp.exists() or bigp.read_text() == "", "a systemic run must journal nothing"
assert len(bc.queries) == 2, "held back 24 results, stopped at the first check past 20"
hl = (tmp / "fetch_health.txt").read_text()
assert "procs.lscore.stopped=systemic" in hl and "procs.lscore.ok=0" in hl, hl
print("systemic    : 24 of 24 without an own spawn -> nothing journaled, warning, stop, fetch_health says so")
# concurrency: 3 workers over the same 30 wearers, all journaled exactly once, v2
import threading
class SafeClient(FakeClient):
    lock = threading.Lock()
    def query(self, gql, est_cost=1.0):
        with self.lock: return super().query(gql, est_cost)
bigp.unlink(missing_ok=True); bigf.write_text("")
sc = SafeClient()
s7 = fp.run(budget_pts=1000, budget_s=60, limit=None, tracked=TRACKED, gear_path=big,
            procs_path=bigp, failed_path=bigf, client=sc, workers=3)["lscore"]
recs7 = [json.loads(l) for l in bigp.read_text().splitlines()]
assert s7["ok"] == 30 and len(recs7) == 30 and all(r["v"] == 2 for r in recs7), (s7, len(recs7))
assert len({(r["report_code"], r["fight_id"], r["character"]) for r in recs7}) == 30
assert fp.load_done(bigp, bigf)["lscore"] and s7["stopped"] == "", s7
print("workers     : 3 concurrent workers journal each of 30 wearer-fights exactly once")
# since_days: only fights the sweep dates inside the window are fetched
import time as _time
now_ms = _time.time() * 1000
fx = {f"R{i:03d}:1": {"start_time": now_ms - (2 if i < 5 else 20) * 86400_000} for i in range(30)}
bigp.unlink(missing_ok=True); bigf.write_text("")
s8 = fp.run(budget_pts=1000, budget_s=60, limit=None, tracked=TRACKED, gear_path=big,
            procs_path=bigp, failed_path=bigf, client=SafeClient(), workers=2,
            since_days=8, fights=fx)["lscore"]
assert s8["ok"] == 5 and s8["older"] == 25 and s8["pending"] == 5, s8
assert "procs.lscore.older=25" in (tmp / "fetch_health.txt").read_text()
print("since_days  : 5 fights inside the window fetched, 25 older left alone and reported")
# since_reset: per-region instants (injected); grace admits the hour before the reset;
# a regionless fight takes the EARLIEST instant; an undated fight is old
US, EU = now_ms - 10 * 3600_000, now_ms - 2 * 3600_000          # US reset 10 h ago, EU 2 h ago
fr = {}
for i in range(30):
    reg = "US" if i < 10 else ("EU" if i < 20 else "")
    st = {0: US + 3600_000, 1: US - 3 * 3600_000, 2: US - 12 * 3600_000,     # US: in, in (grace), out
          10: EU + 60_000, 11: EU - 5 * 3600_000, 12: EU - 7 * 3600_000,   # EU: in, in (grace), out
          20: US - 1 * 3600_000, 21: US - 9 * 3600_000}.get(i, US - 48 * 3600_000)   # no region: earliest (US) rule
    fr[f"R{i:03d}:1"] = {"start_time": st, "region": reg}
cut = fp.reset_cutoffs(fr, [(f"R{i:03d}", 1, f"W{i}", "Realm") for i in range(30)], now_ms=now_ms, instants={"US": US, "EU": EU})
assert cut[("R000", 1, "W0", "Realm")] == US - 6 * 3600_000 and cut[("R010", 1, "W10", "Realm")] == EU - 6 * 3600_000 and cut[("R020", 1, "W20", "Realm")] == US - 6 * 3600_000
bigp.unlink(missing_ok=True); bigf.write_text("")
s9 = fp.run(budget_pts=1000, budget_s=60, limit=None, tracked=TRACKED, gear_path=big,
            procs_path=bigp, failed_path=bigf, client=SafeClient(), workers=2,
            since_reset=True, fights=fr, instants={"US": US, "EU": EU})["lscore"]
got = sorted(json.loads(l)["report_code"] for l in bigp.read_text().splitlines())
assert got == ["R000", "R001", "R010", "R011", "R020"] and s9["older"] == 25, (got, s9)
print("since_reset : per-region instants with a 6 h grace; regionless -> earliest instant; 5 in, 25 left alone")
# fight_times: fights the sweep forgot are dated (and regioned) by the players journal
pl = tmp / "players.jsonl"
rows_pl = []
for i in range(30):
    rows_pl.append(json.dumps({"character": f"W{i}", "server": "Realm", "region": "EU" if i % 2 else "US",
        "class": "Mage", "spec": "Arcane", "dps": 1, "report_code": f"R{i:03d}", "fight_id": 1,
        "started_at": int(EU + 60_000) if i < 6 else int(US - 20 * 3600_000)}))   # old rows predate BOTH windows (US cutoff = US - 6 h)
rows_pl.append(json.dumps({"character": "Dup", "server": "Realm", "region": "US", "report_code": "R000", "fight_id": 1, "started_at": int(EU + 120_000)}))
pl.write_text("\n".join(rows_pl) + "\n")
ft = fp.fight_times(pl)
assert len(ft) == 30 and ft["R000:1"] == {"start_time": int(EU + 120_000), "region": "US"} and ft["R001:1"]["region"] == "EU", ft["R000:1"]
bigp.unlink(missing_ok=True); bigf.write_text("")
s10 = fp.run(budget_pts=1000, budget_s=60, limit=None, tracked=TRACKED, gear_path=big,
             procs_path=bigp, failed_path=bigf, client=SafeClient(), workers=2,
             since_reset=True, fights={}, players_path=pl, instants={"US": US, "EU": EU})["lscore"]
got = sorted(json.loads(l)["report_code"] for l in bigp.read_text().splitlines())
assert got == [f"R{i:03d}" for i in range(6)] and s10["older"] == 24, (got, s10)
print("fight_times : the players journal dates fights the sweep no longer lists; 6 in, 24 before the reset")

# --- the sidecar -------------------------------------------------------------------
procs.write_text("\n".join(json.dumps(x) for x in [
    # b (any-source buff) 9 s, i (inside own windows) 6 s: the sidecar ships i
    {"v": 2, "report_code": "AAA", "fight_id": 1, "character": "Wearer", "server": "Realm", "key": "lscore",
     "actor": 9, "f": 600_000, "bands": [[0, 6000], [50_000, 53_000]], "sp": [0], "x": 1, "n": 1, "a": 12_000, "b": 9000, "i": 6000, "r": 0.5},
    {"v": 2, "report_code": "BBB", "fight_id": 2, "character": "Old", "server": None, "key": "lscore",
     "actor": 42, "f": 600_000, "bands": [], "sp": [], "x": 0, "n": 0, "a": 0, "b": 0, "i": 0, "r": None},
    {"report_code": "ZZZ", "fight_id": 9, "character": "NotInPayload", "server": "R", "key": "lscore",
     "actor": 1, "f": 1, "bands": [], "n": 0, "a": 0, "b": 0, "i": 0, "r": None},
    # a v1 record for a payload row: skipped, so the row stays UNCOVERED
    {"report_code": "AAA", "fight_id": 1, "character": "Bystander", "server": "Realm", "key": "lscore",
     "actor": 3, "f": 600_000, "bands": [], "n": 0, "a": 0, "b": 0, "i": 0, "r": None},
]) + "\n")
df = pd.DataFrame([
    {"report_code": "BBB", "fight_id": 2, "character": "Old", "server": float("nan")},   # NaN server joins null
    {"report_code": "AAA", "fight_id": 1, "character": "Wearer", "server": "Realm"},
    {"report_code": "AAA", "fight_id": 1, "character": "Bystander", "server": "Realm"},
])
meta = {bsd._gear_key("AAA", 1, "Wearer", "Realm"): {"gear": [{"id": item}]},
        bsd._gear_key("BBB", 2, "Old", None): {"gear": [{"id": item}]},
        bsd._gear_key("AAA", 1, "Bystander", "Realm"): {"gear": [{"id": 5}]}}
bsd._HEALTH.clear()
doc = json.loads(bsd.procs_sidecar(df, procs, meta, "t"))
assert doc["n"] == 3 and doc["kind"] == "procs" and doc["trk"][0]["key"] == "lscore", doc
col = doc["cols"]["lscore"]
b64 = lambda s, dt: np.frombuffer(base64.b64decode(s), dtype=dt)
idx = np.cumsum(b64(col["idx"], "<u4"))
assert idx.tolist() == [0, 1], idx            # payload rows 0 (BBB) and 1 (AAA); row 2 uncovered
assert b64(col["r"], "u1").tolist() == [255, 50], b64(col["r"], "u1")
assert b64(col["a"], "<u2").tolist() == [0, 12] and b64(col["b"], "<u2").tolist() == [0, 6], "b must be the inside seconds (i), never the any-source buff"
assert doc["trk"][0]["cov"] == {"wearers": 2, "measured": 1, "nobeam": 1}, doc["trk"][0]
assert b64(col["p"], "u1").tolist() == [0, 1]
line = [h for h in bsd._HEALTH if "procs sidecar" in h]
assert line and "2 of 2 wearer rows" in line[0] and "1 with no beam" in line[0] and "median 50%" in line[0] and "time-weighted 50%" in line[0], line
assert bsd.procs_sidecar(df, tmp / "missing.jsonl", meta, "t") is None
print("sidecar     : _gear_key join (NaN server), delta idx, r/a/b/p columns, 255 = no beam, v1 skipped, health")

# --- static client contract -------------------------------------------------------
html = (ROOT / "site" / "index.html").read_text()
rb = html.index("function render(){")
body = html[rb:html.index("\n}\n", rb)]
assert body.index("FRAME_A=A") < body.index("renderBeamTable()"), "the beam table must run after FRAME_A is set"
assert "in the light" in html and "beam benefit" not in html.replace("beam benefit (owner", ""), "relabel: 'in the light', never 'beam benefit'"
beam_code = html[html.index("const BEAM_KEY"):html.index("/* ---- builds sidecar")]
assert beam_code.lower().count("uptime") == beam_code.count("Not classic uptime"), "the word uptime survives only inside the definition"
# 2026-09-08: the table counts EVERY wearer-parse in the filters (the lens band is a
# column and may be empty); the frame/character surfaces fall back to every parse when
# the band holds none -- a thin band must never hide a measured spec
tbl = html[html.index("function renderBeamTable"):html.index("function renderBeamTable") + 6000]
assert "const st=beamStats(idx);" in tbl and "const ln=beamStats(lensWindow(idx).inWin);" in tbl, "table must read every parse in the filters, lens as a column"
assert '["lens","p"+state.pctl+" lens"' in tbl, "lens column"
assert html.count("beamStatsWin(") >= 5 and "function beamStatsWin(win,all)" in html, "frame + character surfaces fall back to every parse"
assert "Sampled payload:" in html and '"sample": dict(SAMPLE_INFO)' in (ROOT / "scripts" / "build_site_data.py").read_text(), "never a silent sample"
assert 'id="coverage-note"' in html and "function renderCoverageNote" in html and "renderCoverageNote();" in html, "coverage note"
# 2026-09-08 fleet S1-S5: what a sidecar rung withheld is printed, never silent
assert 'id="frame-dead"' in html and "function windowNote(win,idx)" in html and "enchants not published" in html, "sidecar notices"
# 2026-09-09: the chart selects with the sort and then cuts; bucket labels come from the
# rows the bucket holds; the pooled fold names its ranked-11+ tail; every cap says N of M
assert "let view=pool.slice(0,CHART_MAX)" in html and "CHART_CUT=cutWord" in html, "chart cuts AFTER the sort"
assert "function bucketLo(w)" in html and "usB0-7*w" not in html.split("function bucketLo")[1][:4000].split("function renderTrend")[0].replace("return s?s.lo:usB0-7*w","").replace("return s?s.hi:usB0-7*w+7",""), "week labels read bucketSpan, not the US bound"
assert "function resetNote()" in html and "no runs there in this reset yet" in html, "per-region reset straddle note"
assert "more listed '+lc" in html and "const E=new Set(M.ent.map(x=>x.k))" in html, "pooled fold names its tail"
assert "showing the top \"+COMPS_MAX+\" on this sort" in html and "Showing \"+shown+\" of \"+ofN+\" groups that pass the gate" in html, "comps + trajectory caps state N of M"
assert "ratings rounded to steps of" in html and "not published in this build (size ladder)" in html, "stats scale / withheld labels"
_b = (ROOT / "scripts" / "build_site_data.py").read_text()
assert '"caps": {"items": item_cap' in _b and '"stats_all": list(SIDECAR_STATS)' in _b and _b.count("_retention_header(df, ") >= 2, "sidecar headers carry caps/window/stats_all, the window from retention"
# 2026-09-14 retention: the build drops rows; the payload ships what it dropped and where it cut,
# every sidecar window is BOUND to the retention constant, and the sentence leads with the policy
assert '"retention": dict(RETENTION_INFO)' in _b and "SIDECAR_WINDOW_RESETS = RETENTION_RESETS" in _b and "BUILDS_WINDOW_RESETS = RETENTION_RESETS" in _b, "retention facts ship; sidecar windows bind to retention"
assert '"coverage": coverage' in (ROOT / "scripts" / "build_site_data.py").read_text() and 'persist_sweep_stats({"sweep.public_runs"' in (ROOT / "scripts" / "fetch_data.py").read_text(), "coverage facts flow fetch -> build -> page"
# 2026-09-14 retention (owner): the page STATES the window on every build from
# payload.retention, buckets on the builder's anchor, and has no control that
# reaches past the newest two resets
assert "D.retention" in html and 'id="retention-note"' in html and "function renderRetentionNote" in html and "renderRetentionNote();" in html, "the retention note is rendered every build"
_rn = html[html.index("function renderRetentionNote"):]; _rn = _rn[:_rn.index("\nfunction renderCoverageNote")]
assert "weekly reset" in _rn and _rn.index("weekly reset") < _rn.index("dropped"), "the window sentence leads; dropped counts trail"
assert '["Everything kept", ()=>[]]' in html and '["This reset", ()=>[0]]' in html and '["Last reset", ()=>[1]]' in html, "the three period presets"
for gone in ('"All season"', '"Last month"', '"Prev month"', '"Last 2 months"', "Custom weeks", 'id="f-weeksA"', "whole season", "pulseSpark(", "wkBag", "Last vs prev month"):
    assert gone not in html, f"{gone!r} must not survive the two-week window"
assert "if(w===OUTW) return false;" in html and "KEEP_BUCKETS=(Number.isFinite(+r)&&+r>0)?+r:2" in html, "rows past the window never pass, and the clamp fails closed on an old payload"
assert "const now=(aD&&!isNaN(+aD))?aD:wallNow;" in html and "Math.floor((wallNow-epoch)/36e5)" in html, "buckets on the builder's anchor (NaN-guarded); reset-age on the wall clock"
assert 'replace("{window}",retPhrase())' in html and "function retPhrase()" in html, "captions read the window from the payload"
assert "Compare needs two resets" in html, "compare refuses without a baseline inside the window"
assert "Rating is a Raider.IO season total" in html and "Avg Player Rating (season)" in html, "the one season-wide figure says so where it prints"
print("client      : renderBeamTable() after FRAME_A=A; label 'in the light'; 'uptime' only in the definition; every-parse table + lens column; sample banner; coverage note")
# 2026-09-17 item-level range filter (owner: "add a filter for ilevel as well"): the same
# shape as the key range, applied in rowPass through baseMasks (so every surface that
# filters rows inherits it), printed in the scope line, the frame scope and a scope chip
# whenever narrowed, parked and restored by the Archon replica, hidden on a payload
# without rows.ilvl, and honest about the unknown rows a narrowed range excludes.
assert 'id="ilo"' in html and 'id="ihi"' in html and 'id="ilvl-fill"' in html and 'id="ilvl-v"' in html, "item-level dual slider"
assert "il:il?true:(ilvlNarrowed()||gearOn())" in html and "if(m.il){const v=R.ilvl[i]; if(v<m.ilo||v>m.ihi) return false;}" in html, "rowPass applies the range only when narrowed or under the Gear axis; unknown (0) fails a narrowed range"
assert html.count("else if(ilvlNarrowed()) p.push(ilvlText());") == 2 and 'if(gearOn()) p.push(ilvlAText()+" (cohort A)");' in html and 'if(gearOn()) p.push(withB?ilvlAText()+" vs "+ilvlBText()+" (ghost)":ilvlAText()+" (cohort A)");' in html and '$("frame-scope").textContent=frameScope(true);' in html, "printed in the scope line (cohort A) and the frame scope (A vs B) under the Gear axis"
assert 'scopeChip(box,ilvlText()' in html and '" parses in this build with no item level cannot match)"' in html and "parses in this build carry no item level; " in html and '(gear?"they sit in neither cohort":"a narrowed range leaves them out")' in html, "scope chip + the excluded-unknowns hint (cohort wording under the Gear axis)"
assert "ilo:ILVL.min, ihi:ILVL.max," in html and "&& !ilvlNarrowed()" in html and "ilo:state.ilo, ihi:state.ihi," in html, "Archon parks, matches and snapshots the range"
assert "ibox.hidden=true;" in html, "hidden on a payload without rows.ilvl"
assert html.count('(ILVL.has?') >= 3 and "item level, dungeon, region" in html and "'the key, '+(ILVL.has?'item-level, ':'')" in html, "the bypassed/narrowing/fixed-cohort prose names item level (the disclaimer only when the page has it)"
assert "if(ILVL.has&&(DEF.ilo>ILVL.min||DEF.ihi<ILVL.max))" in html, "the trust-gate reference pool mirrors the item-level default"
assert '"ilvl": ilvl_arr' in _b, "the builder ships rows.ilvl"
print("client      : item-level range: dual slider under Key Level; rowPass gate; scope line + frame scope; chip + hint name the unknown count; Archon park/match/snap; hidden without rows.ilvl")

# Gear compare axis (owner, 2026-09-18: "compare on ... ilevel. I want to be able to see how
# classes scale with gear"): a third XOR axis next to Time and Skill. The same period and
# filters are aggregated twice, once per item-level cohort (A = the Item Level slider, B = a
# ghost twin in the sidebar), and joined on A's groups exactly like the Time axis. Turning it
# on splits an open slider at the median item level of the current selection (A at or above,
# B below) or keeps a narrowed A and gives B the complement; every surface that prints
# "period A/B" under Time prints the two item-level ranges under Gear; parses with no item
# level sit in neither cohort; Archon parks it and restores it; hidden without rows.ilvl.
assert 'data-a="gear" data-l="Gear" id="axis-gear-btn"' in html and 'id="axis-gear"' in html and 'id="axis-gear-chip"' in html, "Gear segment button + reserved axis sub-slot"
assert 'id="blockG"' in html and 'id="ilob"' in html and 'id="ihib"' in html and 'id="ilvlb-v"' in html and 'id="ilvlb-fill"' in html and 'id="gearquick"' in html, "B cohort panel: dual slider + quick chips"
assert "const gearOn=()=>state.gear&&!state.compare&&!state.skill&&!state.elite&&ILVL.has;" in html and "const twoSided=()=>state.compare||gearOn();" in html, "gearOn and twoSided predicates"
assert "const skillOn=()=>state.skill&&!state.compare&&!state.gear&&!state.elite;" in html and "if(on){state.skill=false; if(state.gear) gearOff();}" in html and "function gearOff()" in html and 'else if(a==="gear"){ if(!state.gear) setGear(true); }' in html, "strict XOR across the three axes"
assert "function gearIlvls()" in html and "function gearMedian()" in html and "const gearQuantile=f=>" in html and "function gearSplitApply(announce)" in html and "function setGear(on)" in html and "function gearOverlapNote()" in html and "function gearCensus()" in html, "gear engine"
assert "const m=baseMasks(); m.il=false;" in html and "const x=R.ilvl[i]; if(x>0) v.push(x);" in html and "state.ilo=med; state.ihi=ILVL.max; state.ilob=ILVL.min; state.ihib=med-1;" in html and "if(below){ state.ilob=ILVL.min; state.ihib=state.ilo-1; }" in html and "else { state.ilob=state.ihi+1; state.ihib=ILVL.max; }" in html and "if(gearPrevA&&state.ilo===gearPrevA.setLo&&state.ihi===gearPrevA.setHi){" in html and "ilo:il?il.lo:state.ilo, ihi:il?il.hi:state.ihi};" in html, "the arithmetic: median over the un-narrowed selection, known item levels only, lower median; split and complement cohorts adjacent and disjoint; B's bounds ride the mask; leaving restores A only while the axis's own range still stands"
assert "gearPrevA=null;   // a fresh activation" in html, "a fresh activation never inherits a restore memory"
assert "at or above the selection's median item level at the split" in html and '"), B = "+ilvlBText()+" (below it)' in html and 'A kept at "+ilvlAText()+" (solid), B = "+ilvlBText()' in html and '" A, grey ghost)."' in html and "Gear compare needs item levels — this build carries none." in html and "cannot be split at its median item level" in html and "no parse in it sits below that" in html, "the notices say what was set, kept or refused"
assert "function aggregate(weeks,il){" in html and "const m=baseMasks(il), cut=periodCut(weeks);" in html and "aggregate(state.weeksA,{lo:state.ilob,hi:state.ihib})" in html, "B is period A aggregated over the B cohort"
assert "const cmpA=twoSided()||skillTab;" in html and "if(twoSided()) b=B?B.groups.get(key)||null:null;" in html and "b:(twoSided()&&FRAME_B)?(FRAME_B.groups.get(key)??null):null," in html and "const compare=twoSided()||skill;" in html and html.count("if(twoSided())") == 4, "every two-aggregation surface reads twoSided(), not state.compare: the join, the caption gate, the frame ctx and its three identity rows, the table"
assert '<b>Gear compare:</b> solid = item level A (' in html and "parses with no item level sit in neither cohort." in html and "Item level climbs with key level, so keep the key range narrow to read gear alone" in html and "A and B overlap on ilvl" in html, "period note: cohorts, unknowns, the key confound, overlap"
assert 'cap="Solid bar: item level A ("' in html and 'scopeChip(box,"compare: gear vs "+ilvlBText(),"blockG")' in html and "'<b>A</b><br><span style=\"white-space:nowrap\">'+esc(ilvlAText())+'</span>'" in html and '" at item level A ("+state.ilo+"–"+state.ihi+") vs B ("' in html, "caption, scope chip, tooltip header and table sub-line name the ranges"
assert "(gearOn()?'no B':'new')" in html and "gearOn()?' · item-level cohort A':''" in html and 'gearOn()?" · item-level cohort A":""' in html, "no time words under the Gear axis"
assert 'state.gear=("gear" in st)?!!st.gear:false;' in html and 'if("ilob" in st){state.ilob=st.ilob; state.ihib=st.ihib;}' in html and "gear:state.gear, ilob:state.ilob, ihib:state.ihib," in html and "&& state.merge && !state.compare && !state.gear" in html and "function setGear(on){\n  if(state.elite) return;" in html, "Archon parks the Gear axis and restores it with both cohorts; the axis cannot be turned on inside the replica"
assert '$("axis-gear-btn").style.display=ILVL.has?"":"none";' in html and "state.gear=false; state.ilob=0; state.ihib=0;" in html and "const any=state.compare||state.skill||state.gear;" in html and 'gear?"Item Level A (solid)":"Item Level"' in html and '$("axis-gear-chip").textContent="B: "+ilvlBText();' in html, "hidden without rows.ilvl; opens off; gain/loss sorts, the A label and the B chip follow the axis"
# review follow-up (2026-09-18): the split is tried before the other axis is switched off; the
# degenerate case is "nothing below the median"; the restore memory rides the Archon snapshot;
# leaving the axis always says where A stands; the selection-scoped unknown count; shared
# characters; Pulse names the cohort; compare columns say Parses; the B slider is grey.
assert "const open=!ilvlNarrowed();" in html and "if(open&&!gearSplitApply(true)){ syncAxisUI(); return; }" in html, "a refused split leaves Time/Skill on"
assert "return {med:v[Math.floor((v.length-1)/2)], lo:v[0]};" in html and "if(r==null||r.lo>=med){" in html, "refused when no parse sits below the median"
assert "gearPrev:gearPrevA," in html and 'gearPrevA=("gearPrev" in st)?st.gearPrev:null;' in html, "the restore memory travels with the Archon snapshot"
assert 'the item-level range stays at "+(ilvlNarrowed()?ilvlAText():"any")' in html and '+(wasGear?" "+$("notice").textContent:""));' in html, "leaving the axis always speaks; the quick-compare chip keeps both lines"
assert "const gc=gearCensus(), aHigh=state.ilo>state.ihib, aLow=state.ihi<state.ilob;" in html and '" In this selection "+fmtInt(gc.unk)+" parses with no item level sit in neither cohort."' in html and '" fall between the two ranges and sit in neither cohort.</b>"' in html and '" <b>Median item level "+gc.ilA+" in A vs "+gc.ilB+" in B · median key +"+gc.kA+" in A vs +"+gc.kB+" in B.</b>"' in html and '" — the key gap above is inside every badge"' in html, "the census: selection-scoped unknowns, the gap between the ranges, each cohort's median item level and key"
assert '["⚡ Median split",' in html and '["⚡ Quartile split",' in html and '["⇄ Swap A and B",' in html and "gearTrack(hi,ILVL.max); state.ilo=hi; state.ihi=ILVL.max; state.ilob=ILVL.min; state.ihib=lo;" in html, "quick chips: median split, quartile split, swap"
assert 'mil:(g.il.sort((a,b)=>a-b),g.il[Math.floor((g.il.length-1)/2)]),' in html and 'h+=row("Median item level", f(r.a,"mil",' in html and 'h+=row("Median key", f(r.a,"mkey",' in html, "the tooltip prints the spec's own cohort centres under Gear"
assert "thinB:!!(b&&b.chars<effMinB)});" in html and '">thin B</span>' in html, "a B side under the gate is badged, never hidden"
assert 'scopeChip(box,"compare: gear vs "+ilvlBText(),"blockG")' in html, "the gear scope chip jumps to the B slider"
assert "function gearSharedCharsNote(A,B)" in html and "charSet:charSeen," in html and "a character with parses in both item-level ranges counts in both columns" in html, "shared characters are said in the note and the tooltip"
assert '"sort by Gained most to rank specs by what they gain from gear."' in html and "sort by Lost most" in html and "the two item-level ranges stay put when other filters move" in html, "the sort advice follows which side is the higher gear; the split is a snapshot"
assert 'notes.push("item-level cohort A only ("+ilvlAText()+") in both windows — B is not on this board");' in html, "Pulse names the cohort"
assert 'cols.push(["a_n","Parses A"],["b_n","Parses B"],' in html and 'cols.push(["a_n","Parses"],' in html and '"Runs A"' not in html, "compare columns carry parses and say so"
assert "#blockG .dual .fill{background:#8E8C86}" in html and '<b id="ilvlb-v" style="color:#D8D6CF">' in html, "B slider is the ghost side"
assert "@media(max-width:955px){" in html and "@media(min-width:956px){aside{position:sticky;" in html, "the header wraps before the fourth button squeezes it"
print("client      : Gear compare axis: Off|Time|Skill|Gear XOR; B = period A over the B item-level cohort; median split / complement / swap; period note, caption, chip, tooltip, table, frame; Archon park+restore; hidden without rows.ilvl")
print("client      : retention note every build; presets Everything kept / This reset / Last reset; anchor-bucketed; no month presets, custom weeks, season sparkline or 'whole season' text")

print("\nPASS")
