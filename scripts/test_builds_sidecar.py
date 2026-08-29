"""Builds sidecar + name caches against synthetic journal records.

Records go through the REAL writer (parse_summary), the real journal reader
(meta_from_gear_journal) and the real emitter (builds_sidecar), with the
fetch_names caches written to disk exactly as fetch_names.py writes them.
What this pins, per blueprint §1 (change-controlled): the exact JSON shape,
row/column alignment with the payload's df order for BOTH encodings via a
reference decoder implementing §1.3, per-spec vocab construction (order,
caps, embellishment splits, cr/emb/ilvl/name annotations incl. the
missing-name null fallback), fl bits for gear-only and talents-only records,
_gear_key normalization (a null server still joins), the full degradation
ladder, and empty-journal absence. fetch_names' offline half: enchant-name
cleaning, grow-only merges that never overwrite manual entries, null =
asked-and-unnamed (never re-asked), and a stubbed end-to-end run whose
network failures change nothing and still exit 0.
"""
import base64
import gzip
import json
import math
import pathlib
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fetch_data import parse_summary
import build_site_data as bsd
import fetch_names as fn


class _Hero:
    def resolve(self, tree):
        return "Hero"


def gear_item(iid, ilvl=720, ench=None, bonus=None):
    d = {"id": iid, "itemLevel": ilvl, "icon": "x.jpg", "quality": 4}
    if ench:
        d["permanentEnchant"] = ench
    if bonus:
        d["bonusIDs"] = bonus
    return d


def make_parse(code, char, cls, spec, *, server="X", gear=None, build=None):
    """One (player row, journal record|None) through the real collector."""
    ci = {"talentTree": [{"id": 1, "rank": 1}], "specID": 1}
    if gear is not None:
        ci["gear"] = gear
    if build:
        ci["talentImportString"] = build
    fight = {"code": code, "fid": 1, "dungeon": "Halls", "key_level": 14,
             "region": "US", "score": 400.0, "medal": "gold",
             "affixes": [9], "start_time": 1_787_000_000_000,
             "rank_duration_ms": 1_500_000}
    player = {"id": 1, "name": char, "server": server, "type": cls,
              "specs": [spec], "icon": f"{cls}-{spec}",
              "maxItemLevel": 720, "combatantInfo": ci}
    table = {"data": {"totalTime": 1_500_000,
                      "playerDetails": {"dps": [player]},
                      "damageDone": [{"id": 1, "total": 9_000_000}],
                      "deathEvents": []}}
    rows, gear_rows = parse_summary(fight, table, _Hero())
    return rows[0], (gear_rows[0] if gear_rows else None)


CACHES = {
    "names_items.json": {"111": {"n": "Crown of Testing", "q": 4},
                         "222": {"n": None}},      # 112/444/555 never asked
    "names_enchants.json": {"7008": "Rune of Tests"},   # 7100 never asked
    "crafted_ids.json": [222],
    "names_bonus_emb.json": {"8960": None, "12001": "Radiant Hem"},
}


def run_builds(rows, recs, caches=CACHES, **kw):
    with tempfile.TemporaryDirectory() as tmp:
        tp = pathlib.Path(tmp)
        with (tp / "gear.jsonl").open("w") as fh:
            for r in recs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        bsd.GEAR_JOURNAL = tp / "gear.jsonl"
        bsd.GEAR_EXPORT = tp / "absent.jsonl.gz"
        bsd.NAMES_ITEMS = tp / "names_items.json"
        bsd.NAMES_ENCHANTS = tp / "names_enchants.json"
        bsd.CRAFTED_IDS = tp / "crafted_ids.json"
        bsd.NAMES_BONUS_EMB = tp / "names_bonus_emb.json"
        for fname, obj in caches.items():
            (tp / fname).write_text(json.dumps(obj))
        df = pd.DataFrame(rows)
        doc = bsd.builds_sidecar(df, bsd.meta_from_gear_journal(),
                                 "test", **kw)
        return df, (json.loads(doc) if doc is not None else None)


def ref_decode(doc, N):
    """Reference decoder implementing blueprint §1.3 verbatim."""
    assert doc["v"] == 1 and doc["n"] == N, (doc["v"], doc["n"], N)

    def dec(b, dt):
        return np.frombuffer(base64.b64decode(b), dtype=dt)
    if doc["enc"] == "sparse":
        idx = dec(doc["idx"], "<u4")
        mp = np.full(N, -1, np.int64)
        mp[idx] = np.arange(len(idx))
        m = len(idx)
    else:
        mp = np.arange(N)
        m = N
    fl = dec(doc["cols"]["fl"], "u1")
    it = [dec(b, "u1") for b in doc["cols"]["it"]]
    en = [dec(b, "u1") for b in doc["cols"].get("en", [])]
    bld = dec(doc["cols"]["bld"], "u1")
    # declared lengths (§1.2 reject rules, checked here as the pipeline pin)
    assert len(it) == len(doc["slots"]) == 16
    assert len(en) == math.ceil(len(doc["eslots"]) / 2)
    assert len(fl) == len(bld) == m
    assert all(len(c) == m for c in it + en)

    def itV(k, i):
        j = mp[i]
        return 0 if j < 0 else int(it[k][j])

    def enV(jj, i):
        j = mp[i]
        if j < 0:
            return 0
        b = int(en[jj >> 1][j])
        return (b >> 4) if jj % 2 else (b & 15)
    return {"mp": mp, "fl": lambda i: 0 if mp[i] < 0 else int(fl[mp[i]]),
            "itV": itV, "enV": enV,
            "bldV": lambda i: 0 if mp[i] < 0 else int(bld[mp[i]])}


# --------------------------------------------------------------------------
# fixture: one spec, every annotation and fl case, one uncovered row
rows, recs = [], []


def add(*a, **kw):
    rw, rc = make_parse(*a, **kw)
    rows.append(rw)
    if rc is not None:
        recs.append(rc)


def paladin_gear(i):
    g = [gear_item(111 if i <= 15 else 112,
                   ilvl=730 if i == 1 else 720,
                   ench=7100 if i <= 2 else None)]          # 0 head
    g += [gear_item(300 + s) for s in (1, 2)]               # 1 neck 2 shoulder
    g += [{"id": 0}]                                        # 3 shirt (empty)
    g += [gear_item(400 + s) for s in (4, 5, 6, 7)]         # chest..feet
    bonus = ([8960, 12001] if i <= 6 else [8960] if i <= 10 else None)
    g += [gear_item(222, bonus=bonus)]                      # 8 wrist
    g += [gear_item(400 + s) for s in (9, 10, 11, 12, 13, 14)]
    g += [gear_item(555, ench=7008)]                        # 15 mainhand
    g += [{"id": 0}]                                        # 16 offhand empty
    return g


for i in range(1, 21):
    build = ("BUILD_X" if i <= 12 else "BUILD_Y" if i <= 17
             else "BUILD_Z" if i <= 19 else None)
    add(f"P{i}", f"Ret{i}", "Paladin", "Retribution",
        server=(None if i == 5 else "X"),          # (f) null server joins
        gear=paladin_gear(i), build=build)
add("PT", "TalOnly", "Paladin", "Retribution", gear=None, build="BUILD_X")
rw, _ = make_parse("PN", "NoJournal", "Paladin", "Retribution",
                   gear=paladin_gear(3), build="BUILD_X")
rows.append(rw)                                    # row present, record not

df, doc = run_builds(rows, recs, enc="dense")
N = len(df)
assert doc["enc"] == "dense" and doc["n"] == N
assert doc["slots"] == list(bsd.BUILDS_SLOTS)
assert doc["eslots"] == [0, 15], doc["eslots"]     # measured, >=1% rule
spec = doc["specs"]["Paladin|Retribution"]

# vocab construction: order by count, names from cache, null fallback,
# median ilvl, crafted flag, embellishment splits
head = spec["items"][0]
assert head[0] == {"id": 111, "n": "Crown of Testing", "ilvl": 720}, head[0]
assert head[1] == {"id": 112, "n": None, "ilvl": 720}, head[1]
wrist = spec["items"][doc["slots"].index(8)]
assert wrist[0] == {"id": 222, "n": None, "ilvl": 720, "cr": 1}, wrist[0]
assert wrist[1] == {"id": 222, "n": None, "ilvl": 720, "cr": 1,
                    "emb": "Radiant Hem"}, wrist[1]
assert wrist[2] == {"id": 222, "n": None, "ilvl": 720, "cr": 1,
                    "emb": "#8960"}, wrist[2]
assert spec["ench"][0] == [{"id": 7100, "n": None}], spec["ench"]
assert spec["ench"][1] == [{"id": 7008, "n": "Rune of Tests"}], spec["ench"]
assert spec["builds"] == [{"s": "BUILD_X", "n": 13}, {"s": "BUILD_Y", "n": 5},
                          {"s": "BUILD_Z", "n": 2}], spec["builds"]
assert len(spec["items"]) == 16 and len(spec["ench"]) == 2
print("vocab     : count-ordered entries; null-name fallback; median ilvl; "
      "cr from crafted_ids; emb split plain/named/#marker")

# per-row decode pins, both encodings, incl. fl bits and the uncovered row
_, sdoc = run_builds(rows, recs, enc="sparse")
for d in (doc, sdoc):
    R = ref_decode(d, N)
    r1 = int(df.index[df["character"] == "Ret1"][0])
    assert R["fl"](r1) == 3                        # gear + build
    assert R["itV"](0, r1) == 1                    # head 111
    assert R["itV"](d["slots"].index(8), r1) == 2  # wrist emb "Radiant Hem"
    assert R["enV"](0, r1) == 1 and R["enV"](1, r1) == 1   # 7100 low, 7008 high
    assert R["bldV"](r1) == 1                      # BUILD_X
    r7 = int(df.index[df["character"] == "Ret7"][0])
    assert R["itV"](d["slots"].index(8), r7) == 3  # wrist "#8960" variant
    assert R["enV"](0, r7) == 0 and R["enV"](1, r7) == 1
    r11 = int(df.index[df["character"] == "Ret11"][0])
    assert R["itV"](d["slots"].index(8), r11) == 1  # plain 222
    r16 = int(df.index[df["character"] == "Ret16"][0])
    assert R["itV"](0, r16) == 2                   # head 112
    assert R["itV"](d["slots"].index(16), r1) == 0  # empty offhand -> 0
    r20 = int(df.index[df["character"] == "Ret20"][0])
    assert R["fl"](r20) == 1 and R["bldV"](r20) == 0        # gear-only
    rt = int(df.index[df["character"] == "TalOnly"][0])
    assert R["fl"](rt) == 2 and R["bldV"](rt) == 1          # talents-only
    assert R["itV"](0, rt) == 0
    rn = int(df.index[df["character"] == "NoJournal"][0])
    assert R["fl"](rn) == 0 and R["bldV"](rn) == 0 and R["itV"](0, rn) == 0
    r5 = int(df.index[df["character"] == "Ret5"][0])
    assert R["fl"](r5) == 3, "null-server record must join its row"
assert ref_decode(sdoc, N)["mp"][rn] == -1         # sparse: truly uncovered
print("decode    : dense + sparse round-trip the same values through the "
      "§1.3 reference decoder; fl bits 1/2/3; nibble pair (7100,7008)")

# alignment is df order, not journal order: reverse the frame, values follow
df_r, doc_r = run_builds(rows[::-1], recs, enc="dense")
Rr = ref_decode(doc_r, N)
r1r = int(df_r.index[df_r["character"] == "Ret1"][0])
assert Rr["itV"](0, r1r) == 1 and Rr["fl"](r1r) == 3
print("alignment : reversed df -> values move with their rows; n == len(df)")

# --------------------------------------------------------------------------
# degradation ladder on a vocab-heavy second spec: 30 distinct named heads
big_rows, big_recs = list(rows), list(recs)
for i in range(1, 31):
    rw, rc = make_parse(f"H{i}", f"Hunt{i}", "Hunter", "Marksmanship",
                        gear=[gear_item(1000 + i)] + paladin_gear(i)[1:],
                        build=f"HBUILD_{i:02d}")
    big_rows.append(rw)
    big_recs.append(rc)
big_caches = dict(CACHES)
big_caches["names_items.json"] = dict(
    CACHES["names_items.json"],
    **{str(1000 + i): {"n": f"Very Distinguished Headpiece Number {i:02d}"}
       for i in range(1, 31)})

_, full = run_builds(big_rows, big_recs, big_caches, enc="dense")
g1 = len(gzip.compress(json.dumps(full, separators=(",", ":")).encode(), 6))
assert len(full["specs"]["Hunter|Marksmanship"]["items"][0]) == 24  # cap
assert len(full["specs"]["Hunter|Marksmanship"]["builds"]) == 30

_, halved = run_builds(big_rows, big_recs, big_caches, enc="dense",
                       target=g1 - 1)
assert halved is not None and "en" in halved["cols"]      # rung 2 sufficed
assert len(halved["specs"]["Hunter|Marksmanship"]["items"][0]) == 12
assert len(halved["specs"]["Hunter|Marksmanship"]["builds"]) == 24
_, no_en = run_builds(big_rows, big_recs, big_caches, enc="dense", target=1)
assert no_en is not None and "en" not in no_en["cols"]    # rung 3
assert no_en["eslots"] == [] and "ench" not in no_en["specs"]["Paladin|Retribution"]
ref_decode(no_en, len(big_rows))                          # still §1.3-valid
_, refused = run_builds(big_rows, big_recs, big_caches, target=1, cap=1)
assert refused is None                                    # over the hard cap
_, empty = run_builds(rows, [])
assert empty is None                                      # empty journal
print("ladder    : full caps -> halved (24->12, builds->24) -> en dropped "
      "(eslots []) -> refused over cap; empty journal -> no file")

# missing caches degrade to null names, never an error
_, bare = run_builds(rows, recs, caches={}, enc="dense")
b_spec = bare["specs"]["Paladin|Retribution"]
assert b_spec["items"][0][0]["n"] is None
assert "cr" not in b_spec["items"][0][0]
assert "emb" not in b_spec["items"][doc["slots"].index(8)][0]
print("caches    : absent cache files -> all-null names, no crafted/emb "
      "annotations, build still ships")

# --------------------------------------------------------------------------
# fetch_names offline: cleaning, grow-only merge, stubbed end-to-end run
assert fn.clean_enchant_name(
    "Enchant Helm - Empowered Rune of Avoidance |A:foo|a") == \
    "Empowered Rune of Avoidance"
assert fn.clean_enchant_name("Lively Growth") == "Lively Growth"
assert fn.clean_enchant_name("") is None and fn.clean_enchant_name(None) is None
cache = {"1": "keep me"}
assert fn.merge_grow_only(cache, {"1": "clobber", "2": None}) == 1
assert cache == {"1": "keep me", "2": None}
assert fn.unseen([1, 2, 3], {"2": None}) == [1, 3]   # null = asked, not again
print("fn helpers: enchant cleaning; grow-only merge keeps manual entries; "
      "null never re-asked")


def fake_get(path, params=None):
    p = (params or {})
    if path == "CraftingData":
        return [{"CraftedItemID": "222"}, {"CraftedItemID": "0"}]
    if path == "ItemLimitCategory":
        return [{"ID": "512", "Name_lang": "Embellished"},
                {"ID": "9", "Name_lang": "Other"}]
    if path == "ItemBonus":
        return [{"ParentItemBonusListID": "8960", "Value_0": "512"},
                {"ParentItemBonusListID": "77", "Value_0": "9"}]
    if path == "ItemSparse":
        if p.get("filter[ID]") == "exact:111":
            return [{"Display_lang": "Crown of Testing",
                     "OverallQualityID": "4"}]
        if p.get("filter[ID]") == "exact:31337":
            return [{"Display_lang": "Radiant Hem"}]
        return []
    if path == "SpellItemEnchantment":
        if p.get("filter[ID]") == "exact:7008":
            return [{"Name_lang": "Enchant Helm - Rune of Tests |A:x|a"}]
        return []
    if path == "ItemBonusTreeNode":
        if p.get("filter[ChildItemBonusListID]") == "exact:12001":
            return [{"ParentItemBonusTreeID": "500"}]
        return []
    if path == "ModifiedCraftingReagentItem":
        if p.get("filter[ItemBonusTreeID]") == "exact:500":
            return [{"ID": "600"}]
        return []
    if path == "Item":
        if p.get("filter[ModifiedCraftingReagentItemID]") == "exact:600":
            return [{"ID": "31337"}]
        return []
    return []


with tempfile.TemporaryDirectory() as tmp:
    tp = pathlib.Path(tmp)
    _, rec = make_parse("F1", "Fx", "Mage", "Arcane",
                        gear=[gear_item(111, ench=7008,
                                        bonus=[8960, 12001])])
    (tp / "gear.jsonl").write_text(json.dumps(rec) + "\n")
    fn.GEAR_FILE = tp / "gear.jsonl"
    fn.GEAR_CSV = tp / "absent.jsonl.gz"
    fn.NAMES_ITEMS = tp / "names_items.json"
    fn.NAMES_ENCHANTS = tp / "names_enchants.json"
    fn.CRAFTED_IDS = tp / "crafted_ids.json"
    fn.NAMES_BONUS_EMB = tp / "names_bonus_emb.json"
    fn.NAMES_BONUS_EMB.write_text(json.dumps({"9999": "Manual Name"}))
    fn._get_csv = fake_get
    assert fn.main([]) == 0
    items = json.loads(fn.NAMES_ITEMS.read_text())
    assert items == {"111": {"n": "Crown of Testing", "q": 4}}, items
    assert json.loads(fn.NAMES_ENCHANTS.read_text()) == \
        {"7008": "Rune of Tests"}
    assert json.loads(fn.CRAFTED_IDS.read_text()) == [222]
    emb = json.loads(fn.NAMES_BONUS_EMB.read_text())
    assert emb == {"8960": None, "9999": "Manual Name",
                   "12001": "Radiant Hem"}, emb
    # second run: nothing unseen -> byte-identical caches (idempotent)
    before = {p.name: p.read_text() for p in tp.glob("*.json")}
    assert fn.main([]) == 0
    assert {p.name: p.read_text() for p in tp.glob("*.json")} == before
    # total network failure: caches unchanged, still exit 0
    fn._get_csv = lambda path, params=None: fn._FAILED
    assert fn.main([]) == 0
    assert {p.name: p.read_text() for p in tp.glob("*.json")} == before
print("fetch_names: stubbed run seeds all four caches (chain resolves "
      "12001 -> 'Radiant Hem'); idempotent; total failure changes nothing")

print("\nPASS")
