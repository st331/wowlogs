#!/usr/bin/env python3
"""Trinket beam benefit: the model, the collector and the sidecar, end to end
on synthetic journals and a scripted API.

Pinned:
  * benefit(): overlapping beams merge in the denominator, bands clip to the
    fight, a band can never exceed its beam window in the numerator, zero
    beams is None (no evidence), and the two reference fights' arithmetic.
  * bands_from_events(): apply/refresh/remove -> bands (any source), the
    wearer's own spawns (source = wearer, a refresh is a new beam), the
    foreign count, an open band closing at the fight end; and the legacy
    bands_from_table() shape for completeness.
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

# --- the table shape the API returns --------------------------------------------
table = {"data": {"startTime": 3_223_104, "endTime": 4_283_125, "totalTime": 1_060_021,
                  "auras": [{"guid": 999, "name": "Other", "bands": [{"startTime": 3_300_000, "endTime": 3_310_000}]},
                            {"guid": T["buff"], "name": T["buff_name"], "totalUptime": 87_400,
                             "bands": [{"startTime": 3_340_789, "endTime": 3_341_015},
                                       {"startTime": 4_280_000, "endTime": 4_290_000}]}]}}
bands, fight_ms = fp.bands_from_table(table, T["buff"])
assert fight_ms == 1_060_021, fight_ms
assert bands == [[117_685, 117_911], [1_056_896, 1_060_021]], bands   # 2nd clipped to the fight
assert fp.bands_from_table({"data": {"startTime": 0, "endTime": 5, "auras": []}}, T["buff"]) == ([], 5)
for bad in (None, {}, {"data": None}, {"data": {"auras": []}}):
    try:
        fp.bands_from_table(bad, T["buff"])
        raise AssertionError(f"accepted {bad!r}")
    except ValueError:
        pass
print("table       : aura filter, ms from fight start, clip, non-table rejected")

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

# --- the sidecar -------------------------------------------------------------------
procs.write_text("\n".join(json.dumps(x) for x in [
    {"v": 2, "report_code": "AAA", "fight_id": 1, "character": "Wearer", "server": "Realm", "key": "lscore",
     "actor": 9, "f": 600_000, "bands": [[0, 6000]], "sp": [0], "x": 0, "n": 1, "a": 12_000, "b": 6000, "i": 6000, "r": 0.5},
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
assert b64(col["a"], "<u2").tolist() == [0, 12] and b64(col["b"], "<u2").tolist() == [0, 6]
assert b64(col["p"], "u1").tolist() == [0, 1]
line = [h for h in bsd._HEALTH if "procs sidecar" in h]
assert line and "2 of 2 wearer rows" in line[0] and "1 with no beam" in line[0], line
assert bsd.procs_sidecar(df, tmp / "missing.jsonl", meta, "t") is None
print("sidecar     : _gear_key join (NaN server), delta idx, r/a/b/p columns, 255 = no beam, v1 skipped, health")

print("\nPASS")
