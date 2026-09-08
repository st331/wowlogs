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
print("client      : renderBeamTable() after FRAME_A=A; label 'in the light'; 'uptime' only in the definition")

print("\nPASS")
