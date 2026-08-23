"""Gear/talent extraction against synthetic combatantInfo payloads.

The live response shape cannot be checked from here (collection holds the
credentials), so this pins the contract the parser relies on and, just as
importantly, that every field is optional: Warcraft Logs omits combatantInfo
on some uploads and omits individual keys on others, and the parser must
return "unknown" rather than raise or invent a zero.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fetch_data import compact_gear, compact_talents, gear_sets

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

n, sid = gear_sets(full)
assert (n, sid) == (4, TIER), (n, sid)
print(f"set count : {n} pieces of set {sid} (the 9999 single piece loses)")

# --- the absent cases, which must be distinguishable from a real zero
for label, ci in [("no combatantInfo", None), ("empty dict", {}),
                  ("gear key absent", {"talentTree": []}),
                  ("gear empty list", {"gear": []})]:
    assert compact_gear(ci) is None, label
    assert gear_sets(ci) == (None, ""), label
    print(f"absent    : {label:18} -> gear None, pieces None (unknown, not 0)")

# gear present but no set pieces at all is a REAL zero, not unknown
noset = {"gear": [item(7001), item(7002)]}
assert gear_sets(noset) == (0, ""), gear_sets(noset)
assert compact_gear(noset) is not None
print("real zero : gear present, no set items -> pieces 0 (not None)")

# talents absent
assert compact_talents(None) is None and compact_talents({}) is None
assert compact_talents({"gear": []}) is None
print("talents   : absent -> None")
print("\nPASS")
