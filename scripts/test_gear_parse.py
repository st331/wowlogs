"""Gear/talent extraction against synthetic combatantInfo payloads.

The live response shape cannot be checked from here (collection holds the
credentials), so this pins the contract the parser relies on and, just as
importantly, that every field is optional: Warcraft Logs omits combatantInfo
on some uploads and omits individual keys on others, and the parser must
return "unknown" rather than raise or invent a zero.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fetch_data import (batch_query, compact_flask, compact_gear,
                        compact_talents, gear_sets, pack_sets, parse_summary)

TIER = "1729"
def item(i, set_id=None, **kw):
    d = {"id": i, "itemLevel": 720, "icon": "x.jpg", "quality": 4}
    if set_id: d["setID"] = set_id
    d.update(kw); return d

# a normal player: 4 tier pieces, a trinket, an enchant, gems
full = {"gear": [
    item(1001, TIER), item(2001), item(1002, TIER),
    item(1003, TIER), item(1004, TIER),
    item(3001, gems=[2001, 2002], bonusIDs=[41, 42]),
    item(4001, permanentEnchant=7008), {"id": 0}, item(5001, "9999"),
], "talentTree": [{"id": 11, "rank": 1}, {"id": 12, "rank": 2}],
   "talentImportString": "ABC", "specID": 260,
   "stats": {"Haste": {"min": 3100}, "Crit": {"min": 2400}},
   "auras": [{"source": 1, "ability": 462854, "stacks": 1,
              "name": "Skyfury", "icon": "x.jpg"},
             {"source": 1, "ability": 431972, "stacks": 1,
              "name": "Flask of Tempered Swiftness", "icon": "y.jpg"}]}

g = compact_gear(full)
assert len(g) == 9 and g[7] is None, g
assert g[0] == {"id": 1001, "ilvl": 720, "set": TIER}, g[0]
assert g[5]["gems"] == [2001, 2002] and g[5]["bonus"] == [41, 42], g[5]
assert g[6]["ench"] == 7008, g[6]
print(f"gear      : {len(g)} slots, 1 empty, trinket gems+bonus kept, enchant kept")

t = compact_talents(full)
assert t["tree"] == [{"id": 11, "rank": 1}, {"id": 12, "rank": 2}]
assert t["talentImportString"] == "ABC" and t["specID"] == 260
assert t["stats"] == {"Haste": 3100, "Crit": 2400}, t["stats"]
print(f"talents   : {len(t['tree'])} nodes, loadout string, specID, stats flattened")

f = compact_flask(full)
assert f == {"id": 431972, "name": "Flask of Tempered Swiftness"}, f
print(f"flask     : {f['name']!r} picked out of {len(full['auras'])} auras")
# aura list present but no flask among the buffs is a REAL "no flask" ({}),
# while a missing/empty aura list is unknown (None) -- same split as gear_sets
assert compact_flask({"auras": [{"ability": 1, "name": "Skyfury"}]}) == {}
assert compact_flask({"auras": [{"ability": 2,
                                 "name": "Phial of Truesight"}]})["id"] == 2
for label, ci in [("no combatantInfo", None), ("empty dict", {}),
                  ("auras key absent", {"gear": []}),
                  ("auras empty list", {"auras": []})]:
    assert compact_flask(ci) is None, label
print("flask     : no flask aura -> {}; auras missing -> None (unknown)")

counts = gear_sets(full)
assert counts == {TIER: 4, "9999": 1}, counts
print(f"set count : {counts} -- every set counted, not just the largest")

# The case that made counting only the dominant set wrong: last season's
# four-piece worn alongside this season's two-piece. Keeping only the biggest
# reported last season's set and the current two-piece disappeared, which put
# the player in the no-set bucket while they had the 2-set bonus active.
OLD = "1600"
mixed = {"gear": [item(1, OLD), item(2, OLD), item(3, OLD), item(4, OLD),
                  item(5, TIER), item(6, TIER)]}
mc = gear_sets(mixed)
assert mc == {OLD: 4, TIER: 2}, mc
assert mc.get(TIER) == 2, "this season's 2-set must survive an older 4-set"
assert pack_sets(mc) == "1600:4|1729:2", pack_sets(mc)
print(f"mixed sets: {pack_sets(mc)} -- old 4-set does not hide the current 2-set")

# --- the absent cases, which must be distinguishable from a real zero
for label, ci in [("no combatantInfo", None), ("empty dict", {}),
                  ("gear key absent", {"talentTree": []}),
                  ("gear empty list", {"gear": []})]:
    assert compact_gear(ci) is None, label
    assert gear_sets(ci) is None, label
    print(f"absent    : {label:18} -> gear None, sets None (unknown, not 0)")

# gear present but no set pieces at all is a REAL zero, not unknown
noset = {"gear": [item(7001), item(7002)]}
assert gear_sets(noset) == {}, gear_sets(noset)
# "none", never "" -- an empty string dies in the CSV round trip (pandas
# writes it as an empty field and reads it back as NaN, i.e. "no gear"),
# which silently reclassified every real zero as unknown on journal-less
# rebuilds. The sentinel must survive being written and re-read.
assert pack_sets(gear_sets(noset)) == "none", pack_sets(gear_sets(noset))
import io
import pandas as _pd
_rt = _pd.read_csv(io.StringIO(_pd.DataFrame(
    {"sc": [pack_sets(gear_sets(noset)), pack_sets(gear_sets(None))]}).to_csv()))
assert _rt["sc"][0] == "none" and _pd.isna(_rt["sc"][1]), _rt["sc"].tolist()
print("csv trip  : real zero -> 'none' survives; no gear -> NaN stays distinct")
assert compact_gear(noset) is not None
print("real zero : gear present, no set items -> {} (not None)")

# talents absent
assert compact_talents(None) is None and compact_talents({}) is None
assert compact_talents({"gear": []}) is None
print("talents   : absent -> None")
# --- parse_summary end to end.
# The helpers above all passed while parse_summary itself raised
# UnboundLocalError on the first real report, because the gear record was built
# before the variable it reads. Helper-level tests cannot see that; this can.
class _Hero:
    def resolve(self, tree): return "Hero"

fight = {"code": "aBc", "fid": 7, "dungeon": "Halls", "key_level": 14,
         "region": "US", "score": 400.0, "medal": "gold", "affixes": [9, 10],
         "start_time": 1_700_000_000_000, "rank_duration_ms": 1_500_000}
player = {"id": 1, "name": "Tester", "server": "Illidan", "type": "Rogue",
          "specs": ["Assassination"], "icon": "Rogue-Assassination",
          "maxItemLevel": 720, "combatantInfo": full}
table = {"data": {"totalTime": 1_500_000,
                  "playerDetails": {"dps": [player]},
                  "damageDone": [{"id": 1, "total": 9_000_000}],
                  "deathEvents": []}}

rows, gear_rows = parse_summary(fight, table, _Hero())
assert len(rows) == 1 and len(gear_rows) == 1, (len(rows), len(gear_rows))
r, g = rows[0], gear_rows[0]
assert r["spec"] == "Assassination" and r["set_counts"] == "1729:4|9999:1", r["set_counts"]
assert g["spec"] == "Assassination" and g["report_code"] == "aBc" and g["fight_id"] == 7
assert len(g["gear"]) == 9 and g["talents"]["specID"] == 260
assert g["flask"] == {"id": 431972, "name": "Flask of Tempered Swiftness"}, g
print(f"parse_summary: 1 row + 1 gear row; spec={r['spec']!r} "
      f"set_counts={r['set_counts']!r} gear_slots={len(g['gear'])} "
      f"flask={g['flask']['name']!r}")

bare = dict(player); bare.pop("combatantInfo")
rows2, gear2 = parse_summary(fight, {"data": {"totalTime": 1_500_000,
    "playerDetails": {"dps": [bare]},
    "damageDone": [{"id": 1, "total": 9_000_000}], "deathEvents": []}}, _Hero())
assert len(rows2) == 1 and gear2 == [], (rows2, gear2)
assert rows2[0]["set_counts"] is None, rows2[0]["set_counts"]
print("parse_summary: no combatantInfo -> player row kept, no gear row, "
      "set_counts None")

# a summary WITHOUT auras -- the production shape -- keeps the journal field
# at None (unknown): the flask feature is removed and the summary is the only
# source read, so this is what every record captures today
noaura = {k: v for k, v in full.items() if k != "auras"}
p2 = dict(player, combatantInfo=noaura)
t2 = {"data": {"totalTime": 1_500_000, "playerDetails": {"dps": [p2]},
               "damageDone": [{"id": 1, "total": 9_000_000}],
               "deathEvents": []}}
_, gear3 = parse_summary(fight, t2, _Hero())
assert gear3[0]["flask"] is None, gear3[0]
print("parse_summary: no summary auras (production shape) -> flask None; "
      "field kept for a later re-enable")

# --- THE CALLER'S PATH. Everything above calls parse_summary directly and
# passed for five days while production produced nothing: the flask removal
# changed parse_summary's arity and the call inside fetch_summaries kept
# passing the old fourth argument, so every report raised TypeError and was
# journaled as a permanent failure. parse_node is the seam the summary stage
# now goes through; feed it a node of the exact shape the batch query returns.
from fetch_data import parse_node, release_failed, POISON_RE
rows4, gear4 = parse_node(fight, {"table": table}, _Hero())
assert len(rows4) == 1 and len(gear4) == 1, (rows4, gear4)
assert rows4[0]["character"] == "Tester"
print("parse_node   : caller-shaped node -> 1 row + 1 gear row (arity under test)")

# the poisoned markers that bug wrote are released, nothing else is touched,
# and a second pass is a no-op
import pathlib, tempfile
with tempfile.TemporaryDirectory() as td:
    p = pathlib.Path(td) / "summaries_done.txt"
    p.write_text(
        "aaa:1\tOK\n"
        "bbb:2\tFAILED\tparse_summary() takes 3 positional arguments but 4 were given\n"
        "ccc:3\tFAILED\tThis report does not exist.\n"
        "ddd:4\tFAILED\tTypeError: parse_summary() takes 3 positional arguments but 4 were given\n"
        "eee:5\tOK\n")
    assert release_failed(p, POISON_RE) == 2
    left = p.read_text().splitlines()
    assert [l.split("\t")[0] for l in left] == ["aaa:1", "ccc:3", "eee:5"], left
    assert release_failed(p, POISON_RE) == 0
    assert release_failed(pathlib.Path(td) / "missing.txt", POISON_RE) == 0
print("release_failed: 2 poisoned markers dropped, OK and genuine failures kept, idempotent")

# --- fetch order: newest run first, regardless of region tag. The owner's
# stated priority; a week-old region-tagged run must never be fetched ahead of
# this morning's untagged one.
from fetch_data import order_pending
pend = [
    {"code": "old-tagged", "fid": 1, "region": "US", "start_time": 1_000},
    {"code": "new-untagged", "fid": 2, "region": None, "start_time": 9_000},
    {"code": "mid-untagged", "fid": 3, "region": "", "start_time": 5_000},
    {"code": "new-tagged", "fid": 4, "region": "EU", "start_time": 9_000},
    {"code": "no-time", "fid": 5, "region": "US", "start_time": None},
]
got = [f["code"] for f in order_pending(pend)]
assert got == ["new-tagged", "new-untagged", "mid-untagged", "old-tagged", "no-time"], got
assert order_pending(pend) == order_pending(list(reversed(pend))), "order must be deterministic"
print("order_pending: strictly newest-first, tags ignored, unknown time last, deterministic")

# --- the batch request is the Summary table alone: the flask feature's
# CombatantInfo events sub-query is removed and must NOT be requested
q = batch_query([{"code": "aBc", "fid": 7}, {"code": "dEf", "fid": 9}])
for needle in ('a0: report(code: "aBc")', 'a1: report(code: "dEf")',
               "table(fightIDs: [7], dataType: Summary)",
               "table(fightIDs: [9], dataType: Summary)"):
    assert needle in q, (needle, q)
assert "events(" not in q and "CombatantInfo" not in q, q
print("batch_query : Summary table only; no events sub-query")

print("\nPASS")
