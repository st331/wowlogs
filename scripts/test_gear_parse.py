"""Gear/talent extraction against synthetic combatantInfo payloads.

The live response shape cannot be checked from here (collection holds the
credentials), so this pins the contract the parser relies on and, just as
importantly, that every field is optional: Warcraft Logs omits combatantInfo
on some uploads and omits individual keys on others, and the parser must
return "unknown" rather than raise or invent a zero.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fetch_data import compact_gear, compact_talents, gear_sets, pack_sets, parse_summary

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
   "stats": {"Haste": {"min": 3100}, "Crit": {"min": 2400}}}

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
print(f"parse_summary: 1 row + 1 gear row; spec={r['spec']!r} "
      f"set_counts={r['set_counts']!r} gear_slots={len(g['gear'])}")

bare = dict(player); bare.pop("combatantInfo")
rows2, gear2 = parse_summary(fight, {"data": {"totalTime": 1_500_000,
    "playerDetails": {"dps": [bare]},
    "damageDone": [{"id": 1, "total": 9_000_000}], "deathEvents": []}}, _Hero())
assert len(rows2) == 1 and gear2 == [], (rows2, gear2)
assert rows2[0]["set_counts"] is None, rows2[0]["set_counts"]
print("parse_summary: no combatantInfo -> player row kept, no gear row, "
      "set_counts None")

print("\nPASS")
