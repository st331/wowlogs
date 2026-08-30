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
import hashlib
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


def make_parse(code, char, cls, spec, *, server="X", gear=None, build=None,
               tree=({"id": 1, "rank": 1},), spec_id=1):
    """One (player row, journal record|None) through the real collector.
    tree=None omits the talent tree (no build identity without a string)."""
    ci = {"specID": spec_id}
    if tree is not None:
        ci["talentTree"] = list(tree)
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
    "emb_markers.json": [8960],
    # v2 semantics: 12001 = validated embellishment reagent; 6652 = a stat
    # missive validated NOT-an-embellishment (null) — must never split
    "names_bonus_emb2.json": {"12001": "Radiant Hem", "6652": None},
    # 111 has an icon; 222 was asked and has none (null); 112 never asked
    "names_icons.json": {"111": "inv_helm_test", "222": None},
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
        bsd.NAMES_BONUS_EMB2 = tp / "names_bonus_emb2.json"
        bsd.EMB_MARKERS = tp / "emb_markers.json"
        bsd.NAMES_ICONS = tp / "names_icons.json"
        bsd.TRAIT_GEOMETRY = tp / "trait_geometry.json"
        bsd.NAMES_SPELLS = tp / "names_spells.json"
        for fname, obj in caches.items():
            (tp / fname).write_text(json.dumps(obj))
        df = pd.DataFrame(rows)
        doc = bsd.builds_sidecar(df, bsd.meta_from_gear_journal(),
                                 "test", **kw)
        return df, (json.loads(doc) if doc is not None else None)


def run_talents(rows, recs, caches=CACHES):
    """Journal + caches on disk -> the real talents_doc, parsed or None."""
    with tempfile.TemporaryDirectory() as tmp:
        tp = pathlib.Path(tmp)
        with (tp / "gear.jsonl").open("w") as fh:
            for r in recs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        bsd.GEAR_JOURNAL = tp / "gear.jsonl"
        bsd.GEAR_EXPORT = tp / "absent.jsonl.gz"
        bsd.TRAIT_GEOMETRY = tp / "trait_geometry.json"
        bsd.NAMES_SPELLS = tp / "names_spells.json"
        for fname, obj in caches.items():
            (tp / fname).write_text(json.dumps(obj))
        doc = bsd.talents_doc("test")
        return json.loads(doc) if doc is not None else None


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
    # i<=6: marker + named reagent + missive; 7-10: marker only (generic
    # bucket); 11-13: missive alone on a NON-embellished item (must club
    # with plain); 14+: no bonus at all (same plain entry)
    bonus = ([8960, 12001, 6652] if i <= 6 else [8960] if i <= 10
             else [6652] if i <= 13 else None)
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
        gear=paladin_gear(i), build=build,
        # Ret20 has neither string nor tree: the no-build-identity case
        tree=None if i == 20 else ({"id": 1, "rank": 1},))
add("PT", "TalOnly", "Paladin", "Retribution", gear=None, build="BUILD_X")
# a hash-identified spec: trees only, no import strings anywhere (the
# production shape) — 6 chars on tree A (journal order shuffled per char, it
# must not matter), 4 on tree B (one rank different)
TREE_A = [{"id": 10, "rank": 1}, {"id": 20, "rank": 2}, {"id": 30, "rank": 1}]
TREE_B = [{"id": 10, "rank": 1}, {"id": 20, "rank": 3}, {"id": 30, "rank": 1}]
for i in range(1, 11):
    t = TREE_A if i <= 6 else TREE_B
    add(f"M{i}", f"Arc{i}", "Mage", "Arcane", gear=None,
        tree=[t[(j + i) % 3] for j in range(3)])   # rotated order per char
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
assert head[0] == {"id": 111, "n": "Crown of Testing", "ilvl": 720,
                   "ic": "inv_helm_test"}, head[0]      # §1.6 icon widening
assert head[1] == {"id": 112, "n": None, "ilvl": 720}, head[1]  # never asked
wrist = spec["items"][doc["slots"].index(8)]
assert wrist[0] == {"id": 222, "n": None, "ilvl": 720, "cr": 1}, wrist[0]
assert wrist[1] == {"id": 222, "n": None, "ilvl": 720, "cr": 1,
                    "emb": "Radiant Hem"}, wrist[1]
assert wrist[2] == {"id": 222, "n": None, "ilvl": 720, "cr": 1,
                    "emb": "embellished"}, wrist[2]   # generic bucket, no id
assert spec["ench"][0] == [{"id": 7100, "n": None}], spec["ench"]
assert spec["ench"][1] == [{"id": 7008, "n": "Rune of Tests"}], spec["ench"]
assert spec["builds"] == [{"s": "BUILD_X", "n": 13}, {"s": "BUILD_Y", "n": 5},
                          {"s": "BUILD_Z", "n": 2}], spec["builds"]
assert spec["bkind"] == "string", spec.get("bkind")
assert len(spec["items"]) == 16 and len(spec["ench"]) == 2
print("vocab     : count-ordered entries; null-name fallback; median ilvl; "
      "cr from crafted_ids; emb split plain/named/generic, missives club")

# --- build identity by tree hash (§1.5 addendum): canonical over node
# order, sensitive to rank, "t:"-prefixed, never derived from junk nodes
HASH_A = "t:" + hashlib.md5(b"10:1|20:2|30:1").hexdigest()[:12]
HASH_B = "t:" + hashlib.md5(b"10:1|20:3|30:1").hexdigest()[:12]
assert bsd._tree_build_id(TREE_A) == HASH_A
assert bsd._tree_build_id(TREE_A[::-1]) == HASH_A          # order-invariant
assert bsd._tree_build_id(TREE_B) == HASH_B != HASH_A      # rank matters
assert bsd._tree_build_id([{"id": 10}]) == \
    "t:" + hashlib.md5(b"10:0").hexdigest()[:12]           # null rank = 0
assert bsd._tree_build_id(None) is None
assert bsd._tree_build_id([]) is None
assert bsd._tree_build_id([{"rank": 1}, "junk"]) is None   # no usable node
mage = doc["specs"]["Mage|Arcane"]
assert mage["builds"] == [{"s": HASH_A, "n": 6},
                          {"s": HASH_B, "n": 4}], mage["builds"]
assert mage["bkind"] == "hash", mage.get("bkind")
# precedence: a record carrying BOTH keeps the verbatim string; tree-only
# hashes; the journal reader is where the choice is made
with tempfile.TemporaryDirectory() as _tmp:
    _tp = pathlib.Path(_tmp)
    _, both = make_parse("PB", "Both", "Paladin", "Retribution",
                         build="STR_WINS", tree=TREE_A)
    _, only = make_parse("PO", "Only", "Paladin", "Retribution",
                         tree=TREE_A)
    (_tp / "gear.jsonl").write_text(json.dumps(both) + "\n"
                                    + json.dumps(only) + "\n")
    bsd.GEAR_JOURNAL = _tp / "gear.jsonl"
    bsd.GEAR_EXPORT = _tp / "absent.jsonl.gz"
    _j = bsd.meta_from_gear_journal()
    _by = {k[0]: v["build"] for k, v in _j.items()}
    assert _by == {"PB": "STR_WINS", "PO": HASH_A}, _by
print("tree hash : canonical (order-free, rank-sensitive, 't:'-prefixed); "
      "import string wins when both exist; bkind string/hash per spec")

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
    m1 = int(df.index[df["character"] == "Arc1"][0])
    assert R["fl"](m1) == 2 and R["bldV"](m1) == 1          # tree-hash build
    m7 = int(df.index[df["character"] == "Arc7"][0])
    assert R["bldV"](m7) == 2                               # the rank variant
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
assert "ic" not in b_spec["items"][0][0]
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
            return [{"Display_lang": "Radiant Hem", "LimitCategory": "512"}]
        if p.get("filter[ID]") == "exact:31338":   # a missive: no emb cat
            return [{"Display_lang": "Draconic Missive of Nope",
                     "LimitCategory": "0"}]
        return []
    if path == "SpellItemEnchantment":
        if p.get("filter[ID]") == "exact:7008":
            return [{"Name_lang": "Enchant Helm - Rune of Tests |A:x|a"}]
        return []
    if path == "ItemBonusTreeNode":
        if p.get("filter[ChildItemBonusListID]") == "exact:12001":
            return [{"ParentItemBonusTreeID": "500"}]
        if p.get("filter[ChildItemBonusListID]") == "exact:13001":
            return [{"ParentItemBonusTreeID": "501"}]
        return []
    if path == "ModifiedCraftingReagentItem":
        if p.get("filter[ItemBonusTreeID]") == "exact:500":
            return [{"ID": "600"}]
        if p.get("filter[ItemBonusTreeID]") == "exact:501":
            return [{"ID": "601"}]
        return []
    if path == "Item":
        if p.get("filter[ModifiedCraftingReagentItemID]") == "exact:600":
            return [{"ID": "31337"}]
        if p.get("filter[ModifiedCraftingReagentItemID]") == "exact:601":
            return [{"ID": "31338"}]
        return []
    return []


RAW_CALLS = []


def fake_raw(url):
    RAW_CALLS.append(url)
    if "wowhead.com/item=111&" in url:      # icon present (case-normalized)
        return (b'<wowhead><item id="111">'
                b'<icon displayId="7">INV_Helm_Test</icon></item></wowhead>')
    if "wowhead.com/item=333&" in url:      # junk icon name -> sanitized out
        return b"<wowhead><item><icon>../evil</icon></item></wowhead>"
    if "zamimg.com" in url and url.endswith("inv_helm_test.jpg"):
        return b"JPEGDATA"
    return b"<wowhead></wowhead>"           # no icon tag -> null


with tempfile.TemporaryDirectory() as tmp:
    tp = pathlib.Path(tmp)
    _, rec = make_parse("F1", "Fx", "Mage", "Arcane",
                        gear=[gear_item(111, ench=7008,
                                        bonus=[8960, 12001, 13001])])
    (tp / "gear.jsonl").write_text(json.dumps(rec) + "\n")
    fn.GEAR_FILE = tp / "gear.jsonl"
    fn.GEAR_CSV = tp / "absent.jsonl.gz"
    fn.NAMES_ITEMS = tp / "names_items.json"
    fn.NAMES_ENCHANTS = tp / "names_enchants.json"
    fn.CRAFTED_IDS = tp / "crafted_ids.json"
    fn.NAMES_BONUS_EMB2 = tp / "names_bonus_emb2.json"
    fn.EMB_MARKERS = tp / "emb_markers.json"
    fn.NAMES_ICONS = tp / "names_icons.json"
    fn.ICONS_DIR = tp / "icons"
    fn.NAMES_BONUS_EMB2.write_text(json.dumps({"9999": "Manual Name"}))
    # 333 already known to the item cache: the icon phase must still ask
    # wowhead for it (icons trail names) and cache the sanitized-out answer
    fn.NAMES_ITEMS.write_text(json.dumps({"333": {"n": None}}))
    fn._get_csv = fake_get
    fn._get_raw = fake_raw
    assert fn.main([]) == 0
    items = json.loads(fn.NAMES_ITEMS.read_text())
    assert items == {"111": {"n": "Crown of Testing", "q": 4},
                     "333": {"n": None}}, items
    assert json.loads(fn.NAMES_ENCHANTS.read_text()) == \
        {"7008": "Rune of Tests"}
    assert json.loads(fn.CRAFTED_IDS.read_text()) == [222]
    emb = json.loads(fn.NAMES_BONUS_EMB2.read_text())
    # markers live in their own file, never in the reagent cache; the
    # missive-shaped candidate validates to null (reagent lacks an
    # Embellished limit category), the true reagent keeps its name
    assert emb == {"9999": "Manual Name", "12001": "Radiant Hem",
                   "13001": None}, emb
    assert json.loads(fn.EMB_MARKERS.read_text()) == [8960]
    icons = json.loads(fn.NAMES_ICONS.read_text())
    assert icons == {"111": "inv_helm_test", "333": None}, icons
    assert (tp / "icons" / "inv_helm_test.jpg").read_bytes() == b"JPEGDATA"
    # second run: nothing unseen -> byte-identical caches (idempotent), the
    # stored image is never re-fetched and the null icon never re-asked
    before = {p.name: p.read_text() for p in tp.glob("*.json")}
    n_raw = len(RAW_CALLS)
    assert fn.main([]) == 0
    assert {p.name: p.read_text() for p in tp.glob("*.json")} == before
    assert len(RAW_CALLS) == n_raw, RAW_CALLS[n_raw:]
    # total network failure: caches unchanged, still exit 0
    fn._get_csv = lambda path, params=None: fn._FAILED
    fn._get_raw = lambda url: fn._FAILED
    assert fn.main([]) == 0
    assert {p.name: p.read_text() for p in tp.glob("*.json")} == before
print("fetch_names: stubbed run seeds all five caches + the icon image "
      "(junk icon name sanitized to null); idempotent -- second run makes "
      "zero raw fetches; total failure changes nothing")

# --- the site-copy step: new/changed only, never deletes
with tempfile.TemporaryDirectory() as tmp:
    tp = pathlib.Path(tmp)
    for d in ("src", "site", "docs"):
        (tp / d).mkdir()
    (tp / "src" / "a.jpg").write_bytes(b"AAA")
    (tp / "src" / "b.jpg").write_bytes(b"BB")
    _old = bsd.ICONS_SRC, bsd.SITE_DIRS
    bsd.ICONS_SRC = tp / "src"
    bsd.SITE_DIRS = [tp / "site", tp / "docs"]
    bsd.sync_icons("test")
    assert (tp / "site" / "icons" / "a.jpg").read_bytes() == b"AAA"
    assert (tp / "docs" / "icons" / "b.jpg").read_bytes() == b"BB"
    (tp / "site" / "icons" / "stray.jpg").write_bytes(b"S")
    m0 = (tp / "site" / "icons" / "a.jpg").stat().st_mtime_ns
    bsd.sync_icons("test")                     # no-op: nothing changed
    assert (tp / "site" / "icons" / "a.jpg").stat().st_mtime_ns == m0
    (tp / "src" / "b.jpg").write_bytes(b"BBBB")
    bsd.sync_icons("test")                     # changed size -> recopied
    assert (tp / "site" / "icons" / "b.jpg").read_bytes() == b"BBBB"
    assert (tp / "site" / "icons" / "stray.jpg").exists()   # never deleted
    bsd.ICONS_SRC, bsd.SITE_DIRS = _old
print("sync_icons : publishes new/changed only; unchanged untouched; "
      "stray published files never deleted")

# ===========================================================================
# talent trees (§1.7): geometry cache -> talents doc; sel on build vocab
# ===========================================================================
GEO = {
    "specs": {"70": 900, "62": 901},
    "subtrees": {"48": "Templar"},
    "trees": {
        "900": {"nodes": {"1001": [3000, 1200, 0, 0],    # class page
                          "1002": [3600, 1200, 0, 0],
                          "2001": [9900, 1200, 0, 0],    # spec page
                          "2002": [10500, 1800, 2, 0],   # choice node
                          "3001": [7800, 9000, 0, 48]},  # hero (Templar)
                "edges": [[1001, 1002], [1001, 2001],
                          [2001, 2002], [3001, 1001]]},
        "901": {"nodes": {"1101": [3000, 1200, 0, 0],
                          "2101": [9900, 1200, 0, 0]}, "edges": []}},
    "entries": {"50001": [1001, 1, 111111, None],
                "50002": [1002, 2, 222222, None],
                "60001": [2001, 1, 333333, "Override Name"],
                "60002": [2002, 1, 444444, None],
                "60003": [2002, 1, 555555, None],       # choice, same node
                "70001": [3001, 1, 666666, None],
                "51101": [1101, 1, 777777, None]},
}
SPELLS = {"111111": {"n": "Spell One", "ic": "spell_one_icon"},
          "222222": {"n": "Spell Two", "ic": None},
          "333333": {"n": "Shadowed By Override", "ic": "spell_three_icon"},
          "444444": {"n": "Choice A", "ic": "choice_a_icon"},
          "666666": {"n": "Hero Spell", "ic": "hero_icon"}}
TCACHES = dict(CACHES, **{"trait_geometry.json": GEO,
                          "names_spells.json": SPELLS})

TREE_P1 = [{"id": 50001, "rank": 1}, {"id": 50002, "rank": 2},
           {"id": 60001, "rank": 1}, {"id": 70001, "rank": 1}]
TREE_P2 = [{"id": 50001, "rank": 1}, {"id": 50002, "rank": 1},
           {"id": 60002, "rank": 1}, {"id": 70001, "rank": 1}]
trows, trecs = [], []
for i in range(1, 13):
    t = TREE_P1 if i <= 8 else TREE_P2
    rw, rc = make_parse(f"T{i}", f"Pal{i}", "Paladin", "Retribution",
                        spec_id=70, tree=[t[(j + i) % 4] for j in range(4)])
    trows.append(rw)
    trecs.append(rc)
for i in range(1, 4):
    rw, rc = make_parse(f"TM{i}", f"Mg{i}", "Mage", "Arcane", spec_id=62,
                        tree=[{"id": 51101, "rank": 1}])
    trows.append(rw)
    trecs.append(rc)

tdoc = run_talents(trows, trecs, TCACHES)
assert tdoc["v"] == 1 and set(tdoc["trees"]) == \
    {"Paladin|Retribution", "Mage|Arcane"}, tdoc.keys()


def class_pane(doc, sk):
    e = doc["trees"][sk]
    return e["class"] if "class" in e else doc["classes"][e["classRef"]]


cp = class_pane(tdoc, "Paladin|Retribution")
pr = tdoc["trees"]["Paladin|Retribution"]
assert [n["id"] for n in cp["nodes"]] == [1001, 1002]
assert [n["id"] for n in pr["spec"]["nodes"]] == [2001, 2002]
assert list(pr["hero"]) == ["Templar"]
assert [n["id"] for n in pr["hero"]["Templar"]["nodes"]] == [3001]
n1002 = cp["nodes"][1]
assert n1002 == {"id": 1002, "x": 3600, "y": 1200, "r": 2,
                 "n": "Spell Two", "ic": None, "t": 0, "s": 222222}, n1002
n2001 = pr["spec"]["nodes"][0]
assert n2001["n"] == "Override Name", n2001       # def override beats spell
assert n2001["ic"] == "spell_three_icon", n2001   # shared icon-store name
assert n2001["s"] == 333333 and "es" not in n2001, n2001
n2002 = pr["spec"]["nodes"][1]
assert n2002["t"] == 2 and n2002["n"] == "Choice A", n2002
# choice node: every option in _node_entries order (sel's index space)
assert n2002["s"] == 444444, n2002
assert n2002["es"] == [{"s": 444444, "n": "Choice A", "ic": "choice_a_icon"},
                       {"s": 555555, "n": None, "ic": None}], n2002
assert cp["edges"] == [[1001, 1002]]              # cross-pane edges dropped
assert pr["spec"]["edges"] == [[2001, 2002]]
assert pr["hero"]["Templar"]["edges"] == []
mg = tdoc["trees"]["Mage|Arcane"]
assert [n["id"] for n in class_pane(tdoc, "Mage|Arcane")["nodes"]] == [1101]
assert mg["spec"]["nodes"] == [] and mg["hero"] == {}, mg
print("talents doc : panes split hero/class/spec (subtree + X-gap), ranks, "
      "override names, shared-store icons, per-pane edges")

# sel on the sidecar's build vocab: entry ids -> node ids, modal blob
HP1 = "t:" + hashlib.md5(b"50001:1|50002:2|60001:1|70001:1").hexdigest()[:12]
HP2 = "t:" + hashlib.md5(b"50001:1|50002:1|60002:1|70001:1").hexdigest()[:12]
_, sdoc2 = run_builds(trows, trecs, TCACHES, enc="dense")
pb = sdoc2["specs"]["Paladin|Retribution"]
assert pb["bkind"] == "hash"
assert pb["builds"][0] == {"s": HP1, "n": 8, "sel": [[1001, 1], [1002, 2],
                                                     [2001, 1], [3001, 1]]}
assert pb["builds"][1] == {"s": HP2, "n": 4, "sel": [[1001, 1], [1002, 1],
                                                     [2002, 1, 0],
                                                     [3001, 1]]}
# string-identified build with two tree variants: modal selection wins
vrows, vrecs = [], []
for i, t in enumerate((TREE_P1, TREE_P1, TREE_P2), 1):
    rw, rc = make_parse(f"V{i}", f"Var{i}", "Paladin", "Retribution",
                        spec_id=70, build="STR_BUILD", tree=t)
    vrows.append(rw)
    vrecs.append(rc)
_, vdoc = run_builds(vrows, vrecs, TCACHES, enc="dense")
vb = vdoc["specs"]["Paladin|Retribution"]["builds"][0]
assert vb["s"] == "STR_BUILD" and vb["n"] == 3
assert vb["sel"] == [[1001, 1], [1002, 2], [2001, 1], [3001, 1]], vb
# absence: no geometry cache -> no sel keys anywhere, no talents doc
_, nodoc = run_builds(trows, trecs, enc="dense")
assert all("sel" not in b for s in nodoc["specs"].values()
           for b in s["builds"])
assert run_talents(trows, trecs) is None
print("sel         : entry->node conversion, hash-identical sets, modal "
      "for string variants; absent without the geometry cache")

# --- fetch_traits offline: stubbed geometry + spell run, idempotent
DB2 = {
    "TraitTreeLoadout": [{"ID": "1", "ChrSpecializationID": "70",
                          "TraitTreeID": "900"}],
    "TraitSubTree": [{"ID": "48", "Name_lang": "Templar"},
                     {"ID": "9", "Name_lang": "Yellow [DNT]"}],
    "TraitNode": [{"ID": "1001", "TraitTreeID": "900", "PosX": "3000",
                   "PosY": "1200", "Type": "0", "TraitSubTreeID": "0"},
                  {"ID": "3001", "TraitTreeID": "900", "PosX": "7800",
                   "PosY": "9000", "Type": "0", "TraitSubTreeID": "48"}],
    "TraitEdge": [{"ID": "5", "LeftTraitNodeID": "1001",
                   "RightTraitNodeID": "3001", "Type": "0"},
                  {"ID": "6", "LeftTraitNodeID": "1001",
                   "RightTraitNodeID": "999", "Type": "0"}],   # foreign end
    "TraitNodeXTraitNodeEntry": [
        {"ID": "1", "TraitNodeID": "1001", "TraitNodeEntryID": "50001",
         "_Index": "100"},
        {"ID": "2", "TraitNodeID": "999", "TraitNodeEntryID": "9999",
         "_Index": "100"}],                                    # foreign node
    "TraitNodeEntry": [{"ID": "50001", "TraitDefinitionID": "80001",
                        "MaxRanks": "2", "NodeEntryType": "0"}],
    "TraitDefinition": [{"ID": "80001", "SpellID": "111111",
                         "OverrideName_lang": ""}],
}


def fake_db2(path, params=None):
    return [dict(r) for r in DB2.get(path, [])]


def fake_traw(url):
    RAW_CALLS.append(url)
    if "nether.wowhead.com/tooltip/spell/111111" in url:
        return b'{"name":"Spell One","icon":"Spell_One_Icon"}'
    if "zamimg.com" in url and url.endswith("spell_one_icon.jpg"):
        return b"SPELLJPEG"
    return b"{}"


import fetch_traits as ft
with tempfile.TemporaryDirectory() as tmp:
    tp = pathlib.Path(tmp)
    ft.TRAIT_GEOMETRY = tp / "trait_geometry.json"
    ft.NAMES_SPELLS = tp / "names_spells.json"
    fn.ICONS_DIR = tp / "icons"
    fn._get_csv = fake_db2
    fn._get_raw = fake_traw
    assert ft.main([]) == 0
    g = json.loads(ft.TRAIT_GEOMETRY.read_text())
    assert g["specs"] == {"70": 900}
    assert g["subtrees"] == {"48": "Templar"}       # DNT rows dropped
    assert g["trees"]["900"]["nodes"] == {"1001": [3000, 1200, 0, 0],
                                          "3001": [7800, 9000, 0, 48]}
    assert g["trees"]["900"]["edges"] == [[1001, 3001]]   # foreign end cut
    assert g["entries"] == {"50001": [1001, 2, 111111, None]}
    sp = json.loads(ft.NAMES_SPELLS.read_text())
    assert sp == {"111111": {"n": "Spell One", "ic": "spell_one_icon"}}
    assert (tp / "icons" / "spell_one_icon.jpg").read_bytes() == b"SPELLJPEG"
    # second run: geometry cached, spells seen, image present -> no fetches
    n_raw = len(RAW_CALLS)
    before = ft.TRAIT_GEOMETRY.read_text()
    assert ft.main([]) == 0
    assert len(RAW_CALLS) == n_raw and ft.TRAIT_GEOMETRY.read_text() == before
    # total failure: nothing lost, still exit 0
    fn._get_csv = lambda path, params=None: fn._FAILED
    fn._get_raw = lambda url: fn._FAILED
    assert ft.main(["--refresh"]) == 0
    assert ft.TRAIT_GEOMETRY.read_text() == before
print("fetch_traits: stubbed run seeds geometry + spell cache + shared "
      "icon store; idempotent; failed refresh keeps the cached copy")

print("\nPASS")
