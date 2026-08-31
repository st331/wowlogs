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
ladder, §1.8's per-entry "iup" (the 20-distinct-wearer floor, the wearer
dedupe, the high tie rule, the fail-closed uniqueness gate that must ignore
a legitimate embellishment split and fire on a display-name collision, and
the absence-degradation that leaves every column byte-identical), and
empty-journal absence. fetch_names' offline half: enchant-name
cleaning, grow-only merges that never overwrite manual entries, null =
asked-and-unnamed (never re-asked), and a stubbed end-to-end run whose
network failures change nothing and still exit 0.

EMBELLISHMENT IDENTITY (v3) is pinned hardest, because this suite stayed
GREEN through two production bugs -- it asserted a fiction: the old fake db2
gave a crafting reagent `"LimitCategory": "512"` where live db2 says `0` on
every reagent in the game. That fixture is corrected first; the v2 code
cannot pass the corrected suite. Pinned: the v1 failure (a missive on the
same item must never be named or split identity), the v2 failure (a real
embellishment MUST be named, the reagent's LimitCategory is never read and
the Item hop is gone), full recursion, unbacked trees, the leak guard,
CONFLICT, name-tier disagreement, that NO null is ever stored, migration off
the sticky-null v2 cache, and the [emb] build-health verdict token.
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
    # v3: identity is SET MEMBERSHIP against a db2-derived id set. 12001 is
    # a named embellishment; 16001 is a real identity id db2 could not name
    # (generic bucket, never dropped); 6652 is a stat missive — not in the
    # set at all, so it can never split identity however it co-occurs.
    # (exactly what the fake db2 below derives, written out by hand)
    "emb_identity.json": {"ids": [12001, 13001, 16001],
                          "names": {"12001": "Radiant Hem",
                                    "13001": "Nested Lining"},
                          "run": {"ok": True, "trees": 9, "direct": 5,
                                  "marker_trees": 5, "backed": 4,
                                  "unbacked": [505], "leaked": [15001],
                                  "depth": 3, "bad_children": [],
                                  "by_marker": {"8960": 5}, "fetched": 2,
                                  "failures": 0, "migrated": 0,
                                  "dropped_nulls": 0, "intrinsic": 1}},
    "emb_items.json": {"777": "Intrinsically Embellished Boots"},
    "emb_overrides.json": {"names": {}, "ids": []},
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
        bsd.EMB_IDENTITY = tp / "emb_identity.json"
        bsd.EMB_ITEMS = tp / "emb_items.json"
        bsd.EMB_OVERRIDES = tp / "emb_overrides.json"
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
    # every embellishment branch of emb_of, on one crafted wrist:
    #   i<=6  marker + NAMED identity + a missive -> "Radiant Hem". The
    #         missive rides the same item and must not touch identity (v1).
    #   7-8   marker alone                        -> generic bucket
    #   9-10  marker + an identity id db2 could not name -> generic, kept
    #   11-12 a missive on a NON-embellished item  -> clubs with plain
    #   13    marker + TWO identity ids            -> CONFLICT -> generic
    #   14+   no bonus at all                      -> the same plain entry
    bonus = ([8960, 12001, 6652] if i <= 6 else
             [8960] if i <= 8 else
             [8960, 16001] if i <= 10 else
             [6652] if i <= 12 else
             [8960, 12001, 13001] if i == 13 else None)
    g += [gear_item(222, bonus=bonus)]                      # 8 wrist
    # 777 is INTRINSICALLY embellished (emb_items.json): no marker bonus,
    # no bonus list at all, still embellished
    g += [gear_item(777 if i <= 4 else 409)]                # 9 hands
    g += [gear_item(400 + s) for s in (10, 11, 12, 13, 14)]
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
# plain 9 (i 11-12 missive-only + 14-20) > "Radiant Hem" 6 > generic 5
assert wrist[0] == {"id": 222, "n": None, "ilvl": 720, "cr": 1}, wrist[0]
assert wrist[1] == {"id": 222, "n": None, "ilvl": 720, "cr": 1,
                    "emb": "Radiant Hem"}, wrist[1]
assert wrist[2] == {"id": 222, "n": None, "ilvl": 720, "cr": 1,
                    "emb": "embellished"}, wrist[2]   # generic bucket, no id
assert len(wrist) == 3, wrist          # THREE entries: the missive (6652),
# the unnamed identity id (14001) and the conflict all fall into ONE generic
# bucket. v1 split this column five ways by stat combo.
hands = spec["items"][doc["slots"].index(9)]
assert hands[0] == {"id": 409, "n": None, "ilvl": 720}, hands[0]
assert hands[1] == {"id": 777, "n": None, "ilvl": 720,
                    "emb": "embellished"}, hands[1]   # intrinsic, no bonus
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
# level 9 = what the writer actually emits, which is what the ladder weighs
g1 = len(gzip.compress(json.dumps(full, separators=(",", ":")).encode(), 9))
assert len(full["specs"]["Hunter|Marksmanship"]["items"][0]) == 24  # cap
assert len(full["specs"]["Hunter|Marksmanship"]["builds"]) == 30

# rung 2 drops the enchant block but KEEPS the full item vocabulary: a
# truncated item vocab pools its tail into one "other / none" bucket that can
# outrank every real item on a slot, while a missing enchant block merely
# feature-detects off. Enchants are therefore the first thing traded away.
_, no_en = run_builds(big_rows, big_recs, big_caches, enc="dense",
                      target=g1 - 1)
assert no_en is not None and "en" not in no_en["cols"]    # rung 2
assert no_en["eslots"] == [] and "ench" not in no_en["specs"]["Paladin|Retribution"]
assert len(no_en["specs"]["Hunter|Marksmanship"]["items"][0]) == 24  # cap intact
assert len(no_en["specs"]["Hunter|Marksmanship"]["builds"]) == 30
ref_decode(no_en, len(big_rows))                          # still §1.3-valid

# rung 3 is the intermediate item step (24->18, not straight to 12), so a small
# overshoot costs a little of the tail rather than all of it
g2 = len(gzip.compress(json.dumps(no_en, separators=(",", ":")).encode(), 9))
_, mid = run_builds(big_rows, big_recs, big_caches, enc="dense", target=g2 - 1)
assert mid is not None and "en" not in mid["cols"]        # rung 3
assert len(mid["specs"]["Hunter|Marksmanship"]["items"][0]) == 18

_, halved = run_builds(big_rows, big_recs, big_caches, enc="dense", target=1)
assert halved is not None and "en" not in halved["cols"]  # rung 4, the floor
assert len(halved["specs"]["Hunter|Marksmanship"]["items"][0]) == 12
assert len(halved["specs"]["Hunter|Marksmanship"]["builds"]) == 24
_, refused = run_builds(big_rows, big_recs, big_caches, target=1, cap=1)
assert refused is None                                    # over the hard cap
_, empty = run_builds(rows, [])
assert empty is None                                      # empty journal
print("ladder    : full caps -> en dropped (eslots []) -> items 24->18 -> "
      "12 (builds->24) -> refused over cap; empty journal -> no file")

# --------------------------------------------------------------------------
# §1.8 per-entry upgrade lean ("iup"). The metric is a share of a piece's
# DISTINCT wearers carrying it strictly above that piece's own modal item
# level, emitted at >=20 wearers, suppressed wholesale when the vocabulary's
# (spec, slot, id, emb) partition is ambiguous. Each assertion below is
# written so that the obvious wrong implementation fails it, not merely so
# that the right one passes: parse-weighted instead of wearer-deduped, the
# lower tie instead of the higher, a floor off by one, a gate keyed on the
# bare item id.
def iup_gear(head_ilvl, wrist_bonus="none", wrist_ilvl=720):
    """head (slot 0) at a chosen ilvl; wrist (slot 8) optional + crafted."""
    g = [gear_item(111, ilvl=head_ilvl)] + [{"id": 0}] * 7
    g += [{"id": 0} if wrist_bonus == "none"
          else gear_item(222, ilvl=wrist_ilvl,
                         bonus=(None if wrist_bonus == "plain"
                                else wrist_bonus))]
    return g


def iup_rows(chars, caches=CACHES, **kw):
    """chars = [(character, head ilvl, wrist bonus), ...]; one parse each
    unless the same character name repeats, which is the whole point."""
    r, c = [], []
    for j, (ch, ilvl, wb) in enumerate(chars):
        rw, rc = make_parse(f"IUP{j}", ch, "Priest", "Shadow",
                            gear=iup_gear(ilvl, wb))
        r.append(rw)
        if rc is not None:
            c.append(rc)
    return run_builds(r, c, caches, enc="dense", **kw)


def head_entry(d):
    return d["specs"]["Priest|Shadow"]["items"][0][0]


# (a) arithmetic. 20 distinct wearers: 12 at 720, 5 at 723, 3 at 717.
#     mode = 720; strictly above = 5; iup = round(100*5/20) = 25.
_ilvls = [720] * 12 + [723] * 5 + [717] * 3
_plain = [(f"W{i}", v, "none") for i, v in enumerate(_ilvls)]
_df, _d = iup_rows(_plain)
_e = head_entry(_d)
assert _e["iup"] == 25, _e
assert _e["ilvl"] == 720 and _e["id"] == 111, _e     # ilvl untouched in shape
assert isinstance(_e["iup"], int) and 0 <= _e["iup"] <= 100

# (b) the floor is a floor, not a suggestion: 19 wearers carry nothing,
#     20 carry the field. Absent means unknown, never a zero.
_, _d19 = iup_rows([(f"W{i}", 720 if i else 730, "none") for i in range(19)])
assert "iup" not in head_entry(_d19), head_entry(_d19)
_, _d20 = iup_rows([(f"W{i}", 720 if i else 730, "none") for i in range(20)])
assert head_entry(_d20)["iup"] == 5, head_entry(_d20)   # round(100*1/20)

# (c) mode ties resolve to the HIGHER item level. 10 at 720, 10 at 723:
#     the higher tie gives mode 723 and iup 0; the lower tie would give
#     mode 720 and iup 50, so this single number separates the two rules.
_, _dt = iup_rows([(f"W{i}", 720 if i < 10 else 723, "none")
                   for i in range(20)])
assert head_entry(_dt)["iup"] == 0, head_entry(_dt)

# (d) DEDUPE to distinct (character, server). One grinder logs 40 parses at
#     730 while 20 other characters each log one at 720.
#       deduped : 21 wearers, mode 720, one above -> round(100/21) = 5
#       parses  : 60 observations, mode 730 (40 of them), none above -> 0
#     and the entry's median ilvl is 720 deduped, 730 parse-weighted. Both
#     numbers are asserted, so a parse-weighted implementation fails twice.
_grind = [("Grinder", 730, "none")] * 40 + \
         [(f"W{i}", 720, "none") for i in range(20)]
_, _dg = iup_rows(_grind)
_eg = head_entry(_dg)
assert _eg["iup"] == 5, _eg
assert _eg["ilvl"] == 720, _eg      # per-character median, §1.8's stated cost

# (e) the fail-closed uniqueness gate does NOT fire on a legitimate
#     embellishment split. One crafted wrist held plain, named and generic is
#     three vocab entries sharing id 222 -- 392 such groups exist in the live
#     document and every one is correct. A gate keyed on (spec, slot, id)
#     would suppress iup here, silently and forever.
_split = ([(f"A{i}", 720, [8960, 12001]) for i in range(8)] +
          [(f"B{i}", 720, [8960]) for i in range(7)] +
          [(f"C{i}", 723, "plain") for i in range(9)])
_, _ds = iup_rows(_split)
_wrist = _ds["specs"]["Priest|Shadow"]["items"][_ds["slots"].index(8)]
assert len(_wrist) == 3 and {w["id"] for w in _wrist} == {222}, _wrist
assert sorted(str(w.get("emb")) for w in _wrist) == \
    ["None", "Radiant Hem", "embellished"], _wrist
assert "iup" in head_entry(_ds), head_entry(_ds)   # 24 wearers -> field ships

# (f) ...and it DOES fire when two identity ids collapse onto one emitted
#     display name in one slot, which is the ambiguity the gate exists for:
#     the two entries are indistinguishable to the client, so each share
#     would be taken over an arbitrary part of one piece's wearers. Then NO
#     entry anywhere in the document carries iup -- and the document still
#     ships, because a suppressed statistic must never cost a pane.
_dupc = json.loads(json.dumps(CACHES))
_dupc["emb_identity.json"]["names"]["13001"] = "Radiant Hem"   # same name
_collide = ([(f"A{i}", 720, [8960, 12001]) for i in range(8)] +
            [(f"B{i}", 720, [8960, 13001]) for i in range(8)] +
            [(f"C{i}", 723, "plain") for i in range(8)])
_, _dc = iup_rows(_collide, caches=_dupc)
assert _dc is not None
_wc = _dc["specs"]["Priest|Shadow"]["items"][_dc["slots"].index(8)]
assert [w.get("emb") for w in _wc].count("Radiant Hem") == 2, _wc
assert not [e for col in _dc["specs"]["Priest|Shadow"]["items"] for e in col
            if "iup" in e], "the gate must suppress iup on EVERY entry"
assert head_entry(_dc)["ilvl"] == 720          # the rest of the entry stands

# (g) absence-degradation and the §1.2 alignment guarantee. iup is pure
#     per-entry vocab text: raising the floor above the fixture's wearer
#     count removes every key and must change NOTHING else -- same n, same
#     slots/eslots, byte-identical columns, same "v", every other entry key
#     untouched. That is what lets an older client ignore it and lets a
#     ladder rung carry it without a length check.
_floor = bsd.BUILDS_IUP_MIN_WEARERS
try:
    bsd.BUILDS_IUP_MIN_WEARERS = 10_000
    _, _doff = iup_rows(_plain)
finally:
    bsd.BUILDS_IUP_MIN_WEARERS = _floor
assert not [e for col in _doff["specs"]["Priest|Shadow"]["items"] for e in col
            if "iup" in e]
assert _doff["v"] == 1 == _d["v"]                     # no version bump
assert _doff["n"] == _d["n"] == len(_df)
assert _doff["slots"] == _d["slots"] and _doff["eslots"] == _d["eslots"]
assert _doff["cols"] == _d["cols"], "iup must not touch a single column byte"
for _ca, _cb in zip((e for col in _d["specs"]["Priest|Shadow"]["items"]
                     for e in col),
                    (e for col in _doff["specs"]["Priest|Shadow"]["items"]
                     for e in col)):
    assert {k: v for k, v in _ca.items() if k != "iup"} == _cb, (_ca, _cb)
ref_decode(_d, len(_df))          # the iup-carrying doc still decodes §1.3
ref_decode(_doff, len(_df))

# (h) the proof block. Both earlier widenings in this pipeline failed
#     silently, so build_health.txt must answer "did it ship" without
#     reading between lines, and must look obviously wrong when the field is
#     present but degenerate.
bsd._HEALTH.clear()
# the split fixture: head clears the floor (24 wearers, 9 of them above the
# 720 mode -> 38), the three wrist entries do not -- so the coverage line
# has a real numerator AND a real denominator, and a build that emitted
# nothing would read "0/4" rather than merely omitting a line.
_, _dh = iup_rows(_split)
assert head_entry(_dh)["iup"] == 38, head_entry(_dh)
_H = "\n".join(bsd._HEALTH)
assert "[iup] gate: 0 (spec,slot,id,emb) collisions" in _H and "WRITTEN" in _H
assert "eslots [], iup on" in _H              # the one-line sidecar summary
_lines = [ln for ln in bsd._HEALTH if "[iup] emitted on" in ln]
assert _lines, "build_health.txt must always say whether iup shipped"
_line = _lines[-1]
assert "emitted on 1/4 shipped vocab entries (25.0%)" in _line, _line
assert "3 entries below the floor" in _line, _line
assert "floor >=20 distinct (character,server) wearers" in _line, _line
assert [ln for ln in bsd._HEALTH
        if "[iup] distribution: p10/p50/p90 = 38/38/38" in ln], bsd._HEALTH
assert [ln for ln in bsd._HEALTH if "[iup] dedupe:" in ln]
assert [ln for ln in bsd._HEALTH if "[iup] size: shipped rung" in ln]
bsd._HEALTH.clear()
iup_rows(_collide, caches=_dupc)
_H2 = "\n".join(bsd._HEALTH)
assert "-> iup SUPPRESSED, no entry carries it" in _H2, _H2
assert "iup OFF" in _H2 and "[iup] emitted on 0 entries" in _H2, _H2
bsd._HEALTH.clear()
print("iup       : 20-wearer floor; wearer dedupe (not parses); mode ties "
      "resolve HIGH; gate ignores legitimate emb splits and fires on a name "
      "collision; absence leaves columns byte-identical; health proves it")

# --------------------------------------------------------------------------
# the [emb] proof block (§3). Both previous embellishment bugs shipped
# because build_health.txt said NOTHING about embellishments; the verdict is
# the first line and a single greppable token, so it is what is pinned here.
def emb_verdict(caches, bonus, n=12):
    v_rows, v_recs = [], []
    for i in range(1, n + 1):
        rw, rc = make_parse(f"V{i}", f"Vx{i}", "Priest", "Shadow",
                            gear=[gear_item(222, bonus=bonus)])
        v_rows.append(rw)
        v_recs.append(rc)
    mark = len(bsd._HEALTH)
    run_builds(v_rows, v_recs, caches, enc="dense")
    lines = [ln for ln in bsd._HEALTH[mark:] if "[emb] verdict:" in ln]
    assert len(lines) == 1, lines
    return lines[0]


clean_c = dict(CACHES)
clean_c["emb_identity.json"] = {
    "ids": [12001], "names": {"12001": "Radiant Hem"},
    "run": {"ok": True, "trees": 1, "direct": 1, "marker_trees": 1,
            "backed": 1, "unbacked": [], "leaked": [], "depth": 1,
            "bad_children": [], "by_marker": {"8960": 1}, "fetched": 0,
            "failures": 0, "migrated": 0, "dropped_nulls": 0}}
assert "verdict: ok" in emb_verdict(clean_c, [8960, 12001])
# the EXACT shape of the v2 regression: markers detected, name map empty
dead_c = dict(clean_c)
dead_c["emb_identity.json"] = dict(clean_c["emb_identity.json"], names={})
dead_line = emb_verdict(dead_c, [8960, 12001])
assert "verdict: DEAD" in dead_line and "0 named" in dead_line, dead_line
# db2 silent this run: the cached map still ships, but the block says so
stale_c = dict(clean_c)
stale_c["emb_identity.json"] = dict(
    clean_c["emb_identity.json"],
    run=dict(clean_c["emb_identity.json"]["run"], ok=False))
assert "db2 did not answer" in emb_verdict(stale_c, [8960, 12001])
print("emb health: verdict ok on a named fixture; DEAD on markers-with-no-"
      "names (the v2 signature); DEGRADED when db2 was silent")

# The AUDIT line is the tripwire for the v1 failure, so it is exercised in
# both directions rather than left to fire for the first time in production.
# Counted per DISTINCT crafted configuration, support>=12 over >=3 item ids.
def emb_audit(withMissive):
    a_rows, a_recs = [], []
    for i in range(12):                     # 12 configs, ALL embellished
        bonus = [8960, 12001] + ([6652] if withMissive else []) + [7000 + i]
        rw, rc = make_parse(f"A{i}", f"Ax{i}", "Priest", "Discipline",
                            gear=[gear_item(900 + i % 4, bonus=bonus)])
        a_rows.append(rw)
        a_recs.append(rc)
    for i in range(12):                     # 12 configs, NONE embellished
        rw, rc = make_parse(f"B{i}", f"Bx{i}", "Priest", "Discipline",
                            gear=[gear_item(900 + i % 4,
                                            bonus=[6653, 7100 + i])])
        a_rows.append(rw)
        a_recs.append(rc)
    c = dict(clean_c)
    c["crafted_ids.json"] = [222, 900, 901, 902, 903]
    mark = len(bsd._HEALTH)
    run_builds(a_rows, a_recs, c, enc="dense")
    return [ln for ln in bsd._HEALTH[mark:] if "AUDIT" in ln][0]


ok_line = emb_audit(False)
assert "identity ids min withMarker/seen 1.000 (1 ids" in ok_line, ok_line
assert "non-identity max 0.000 (1 ids) -> CLEAN" in ok_line, ok_line
# 6652 is a missive: NOT in the identity set, yet it rides only embellished
# items here. That is exactly the shape v1 mistook for an embellishment, and
# the audit must say so out loud rather than let it pass.
warn_line = emb_audit(True)
assert "-> WARN" in warn_line and "[6652]" in warn_line, warn_line
assert "1.000 (2 ids)" in warn_line, warn_line
print("emb audit : co-occurrence validator fires BOTH ways -- CLEAN when the "
      "db2 set matches the journal, WARN naming the bonus id when a "
      "non-identity id rides only embellished items (the v1 shape)")

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


"""A fake db2 with REAL semantics.

The suite passed through both embellishment bugs because the old fake gave
the reagent `"LimitCategory": "512"` — live db2 says `0` on every crafting
reagent in the game, and non-zero on exactly six non-reagent WORN items.
Every reagent row below therefore carries `LimitCategory: "0"`, so any code
that validates a reagent on that field names NOTHING and fails this suite.

The ItemBonusTreeNode table is now a WHOLE table (v3 fetches it once, not
per bonus id) and models every shape that matters:
  500  {8960 marker, 12001}                 backed by MCRI 600 -> named
  501  {6652}                               a missive tree: NO marker
  502  {8960, ->503}, 503 {->504}, 504 {13001}   nested identity, backed 602
  505  {8960, 14001}                        marker tree with NO MCRI row
  506  {8960, 15001}                        backed by 606, but...
  507  {15001}                              ...15001 also rides a NON-marker
                                            tree -> the leak guard drops it
  508  {8960, 16001}                        backed by 608, whose ItemSparse
                                            rows DISAGREE on Display_lang
"""
IBTN = [
    ("500", "", "8960"), ("500", "", "12001"),
    ("501", "", "6652"),
    ("502", "", "8960"), ("502", "503", ""),
    ("503", "504", ""), ("504", "", "13001"),
    ("505", "", "8960"), ("505", "", "14001"),
    ("506", "", "8960"), ("506", "", "15001"),
    ("507", "", "15001"),
    ("508", "", "8960"), ("508", "", "16001"),
]
QUERIED = []


def fake_get(path, params=None):
    p = (params or {})
    QUERIED.append((path, tuple(sorted(p.items()))))
    if path == "ItemBonusTreeNode":
        return [{"ParentItemBonusTreeID": t, "ChildItemBonusTreeID": st,
                 "ChildItemBonusListID": bl} for t, st, bl in IBTN]
    if path == "ModifiedCraftingReagentItem":
        return [{"ID": "600", "ItemBonusTreeID": "500"},
                {"ID": "601", "ItemBonusTreeID": "501"},
                {"ID": "602", "ItemBonusTreeID": "502"},
                {"ID": "606", "ItemBonusTreeID": "506"},
                {"ID": "608", "ItemBonusTreeID": "508"}]
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
        # reagents, reached by MCRI id — the Item hop is gone. Quality tiers
        # return several rows; LimitCategory is 0 on every one, as in life.
        mc = p.get("filter[ModifiedCraftingReagentItemID]")
        if mc == "exact:600":
            return [{"ID": "31337", "Display_lang": "Radiant Hem",
                     "LimitCategory": "0"},
                    {"ID": "31338", "Display_lang": "Radiant Hem",
                     "LimitCategory": "0"}]
        if mc == "exact:602":
            return [{"ID": "31340", "Display_lang": "Nested Lining",
                     "LimitCategory": "0"}]
        if mc == "exact:608":                      # tiers DISAGREE -> refuse
            return [{"ID": "31341", "Display_lang": "Ambiguous A",
                     "LimitCategory": "0"},
                    {"ID": "31342", "Display_lang": "Ambiguous B",
                     "LimitCategory": "0"}]
        if mc == "exact:601":                      # the missive's reagent
            return [{"ID": "31339",
                     "Display_lang": "Draconic Missive of Nope",
                     "LimitCategory": "0"}]
        if p.get("filter[LimitCategory]") == "exact:512":
            return [{"ID": "777",
                     "Display_lang": "Intrinsically Embellished Boots",
                     "LimitCategory": "512"}]
        return []
    if path == "SpellItemEnchantment":
        if p.get("filter[ID]") == "exact:7008":
            return [{"Name_lang": "Enchant Helm - Rune of Tests |A:x|a"}]
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
    fn.EMB_IDENTITY = tp / "emb_identity.json"
    fn.EMB_ITEMS = tp / "emb_items.json"
    fn.EMB_OVERRIDES = tp / "emb_overrides.json"
    fn.EMB_MARKERS = tp / "emb_markers.json"
    fn.NAMES_ICONS = tp / "names_icons.json"
    fn.ICONS_DIR = tp / "icons"
    # THE STICKY-NULL TRAP, end to end. This is the RETIRED v2 cache as CI
    # actually holds it: one hand-typed name and a null for every candidate
    # bonus id v2 ever asked about — including 12001, the identity id this
    # run must name. v2's unseen() would skip all of them forever, so a
    # corrected resolver against this file resolves NOTHING. If the fix ever
    # regresses to reading it, 12001 comes back unnamed and this fails.
    fn.NAMES_BONUS_EMB2.write_text(json.dumps(
        {"9999": "Manual Name", "12001": None, "13001": None,
         "6652": None, "16001": None}))
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
    assert json.loads(fn.EMB_MARKERS.read_text()) == [8960]
    icons = json.loads(fn.NAMES_ICONS.read_text())
    assert icons == {"111": "inv_helm_test", "333": None}, icons
    assert (tp / "icons" / "inv_helm_test.jpg").read_bytes() == b"JPEGDATA"

    # ---- the v3 identity map, every branch of the derivation
    ident = json.loads(fn.EMB_IDENTITY.read_text())
    # (1) v1 REGRESSION: the missive 6652 rides tree 501, which emits no
    #     marker. It is absent from the identity set, so it can never be
    #     named and can never split a crafted item — even when it sits on
    #     the same item as a marker and an identity id (it does, above).
    assert 6652 not in ident["ids"] and "6652" not in ident["names"]
    # (2) NESTED: 13001 hides two levels down 502 -> 503 -> 504. A
    #     one-level walk finds nothing there and fails this line.
    # (3) UNBACKED: 14001's tree 505 emits the marker but has no MCRI row —
    #     not a craftable reagent, so it contributes no identity and
    #     nothing raises.
    # (4) LEAK GUARD: 15001 is emitted by backed marker tree 506 AND by the
    #     unrelated tree 507, so it is dropped and counted.
    # (5) DISAGREEMENT: 16001 is a real identity id whose ItemSparse tiers
    #     disagree; it stays in ids and is REFUSED a name, never picked.
    assert ident["ids"] == [12001, 13001, 16001], ident["ids"]
    assert ident["run"]["leaked"] == [15001], ident["run"]
    assert ident["run"]["unbacked"] == [505], ident["run"]
    assert ident["run"]["direct"] == 5 and ident["run"]["marker_trees"] == 5
    assert ident["run"]["depth"] == 3, ident["run"]["depth"]
    assert ident["run"]["bad_children"] == []
    # (6) v2 REGRESSION: every fake reagent row carries LimitCategory "0",
    #     as live db2 does. v2's guard names nothing against this fixture.
    assert ident["names"] == {"9999": "Manual Name",       # migrated, truthy
                              "12001": "Radiant Hem",
                              "13001": "Nested Lining"}, ident["names"]
    # (7) POSITIVE ONLY: not one null anywhere. 16001 is simply absent and
    #     will be re-asked at one request — the bug class is deleted.
    assert all(v for v in ident["names"].values()), ident["names"]
    # (8) the migration read the retired file, kept the name, dropped the
    #     four nulls, and left the old file alone
    assert ident["run"]["migrated"] == 1 and ident["run"]["dropped_nulls"] == 4
    assert len(json.loads(fn.NAMES_BONUS_EMB2.read_text())) == 5
    # (9) the deleted Item hop is never queried, and nothing reads a
    #     reagent's LimitCategory (the whole v2 dead end)
    assert not [q for q in QUERIED if q[0] == "Item"], "the Item hop is gone"
    assert json.loads(fn.EMB_ITEMS.read_text()) == \
        {"777": "Intrinsically Embellished Boots"}
    # second run: nothing unseen -> byte-identical caches (idempotent), the
    # stored image is never re-fetched and the null icon never re-asked.
    # emb_identity.json legitimately re-records its per-run counters, so it
    # is compared on content instead of bytes.
    def _stable():
        return {p.name: p.read_text() for p in tp.glob("*.json")
                if p.name != "emb_identity.json"}
    before, n_raw = _stable(), len(RAW_CALLS)
    assert fn.main([]) == 0
    assert _stable() == before
    again = json.loads(fn.EMB_IDENTITY.read_text())
    assert again["ids"] == ident["ids"] and again["names"] == ident["names"]
    assert again["run"]["fetched"] == 0, again["run"]   # names never re-asked
    assert len(RAW_CALLS) == n_raw, RAW_CALLS[n_raw:]
    # total network failure: caches unchanged, still exit 0, and the identity
    # map SURVIVES verbatim while saying out loud that db2 did not answer
    fn._get_csv = lambda path, params=None: fn._FAILED
    fn._get_raw = lambda url: fn._FAILED
    assert fn.main([]) == 0
    assert _stable() == before
    dead = json.loads(fn.EMB_IDENTITY.read_text())
    assert dead["ids"] == ident["ids"] and dead["names"] == ident["names"]
    assert dead["run"]["ok"] is False and dead["run"]["failures"] >= 1
    # THE EMPTY-DERIVATION FLOOR. _FAILED only covers the network dying.
    # Here db2 answers 200 and the two structural tables parse to ZERO rows
    # -- a renamed column, a schema change, or a stub run pointed at the
    # real data dir. That was "success" to the first cut of v3: it rewrote
    # ids to [] and shipped a cache that could not name anything, which is
    # exactly the artifact that went out and reproduced the owner's bug.
    # The map must survive and the run must say so out loud.
    fn._get_csv = lambda path, params=None: (
        [] if path in ("ItemBonusTreeNode", "ModifiedCraftingReagentItem")
        else fake_get(path, params))
    fn._get_raw = fake_raw
    assert fn.main([]) == 0
    assert _stable() == before
    starved = json.loads(fn.EMB_IDENTITY.read_text())
    assert starved["ids"] == ident["ids"], starved["ids"]
    assert starved["names"] == ident["names"], starved["names"]
    assert starved["run"]["empty_derivation"] is True, starved["run"]
    assert starved["run"]["ok"] is False, starved["run"]
print("fetch_names: stubbed run seeds every cache + the icon image (junk "
      "icon name sanitized to null); idempotent -- second run makes zero "
      "raw fetches; total failure keeps the identity map and says so")
print("emb v3    : missive absent from identity (v1); reagent LimitCategory "
      "never read and the Item hop deleted (v2); nested id found; unbacked "
      "tree skipped; leak dropped; tier disagreement refused; NO nulls; "
      "sticky-null v2 cache migrated (1 name, 4 nulls dropped)")

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

# THE PRODUCTION SHAPE: WCL summaries carry talents.tree and nothing else --
# no specID, no import string (430,507/430,507 records checked). Keying the
# tree lookup off specID silently omitted the whole document in production
# while this suite passed, because every fixture above passes spec_id. The
# tree must be identified by ENTRY MEMBERSHIP when specID is absent.
norows, norecs = [], []
for i in range(1, 13):
    t = TREE_P1 if i <= 8 else TREE_P2
    rw, rc = make_parse(f"N{i}", f"NoSid{i}", "Paladin", "Retribution",
                        spec_id=None, tree=[t[(j + i) % 4] for j in range(4)])
    assert "specID" not in rc["talents"], rc["talents"]   # fixture is honest
    norows.append(rw)
    norecs.append(rc)
ndoc = run_talents(norows, norecs, TCACHES)
assert ndoc is not None, "talents doc omitted without specID (the prod bug)"
assert set(ndoc["trees"]) == {"Paladin|Retribution"}, ndoc["trees"].keys()
npr = ndoc["trees"]["Paladin|Retribution"]
assert [n["id"] for n in class_pane(ndoc, "Paladin|Retribution")["nodes"]] \
    == [1001, 1002]
assert [n["id"] for n in npr["spec"]["nodes"]] == [2001, 2002]
assert list(npr["hero"]) == ["Templar"]
# a spec whose entries match NO tree still drops out rather than mismatching
offrows, offrecs = [], []
for i in range(1, 13):
    rw, rc = make_parse(f"O{i}", f"Off{i}", "Warrior", "Arms",
                        spec_id=None, tree=[{"id": 999001, "rank": 1}])
    offrows.append(rw)
    offrecs.append(rc)
odoc = run_talents(norows + offrows, norecs + offrecs, TCACHES)
assert set(odoc["trees"]) == {"Paladin|Retribution"}, odoc["trees"].keys()
print("no-specID   : trees identified by entry overlap (the production "
      "journal shape); unmatched specs drop out cleanly")

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

# ---- the COMMITTED SEED itself, offline. v3's code shipped correct and its
# ARTIFACT shipped broken: data/emb_identity.json went out with "ids": [], a
# fixture name key, and a run block from a stub run -- so the first build named
# nothing, the documented db2-outage floor ("the previous map survives") was
# void because the previous map was empty, and a test string rode into a data
# file. emb_of reaches a name only through this id set, so an empty one makes
# naming structurally impossible however many names sit beside it. Nothing else
# in the suite reads data/; these four lines are what would have caught it.
SEED_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
SEED = json.loads((SEED_DIR / "emb_identity.json").read_text())
SEED_MARKERS = json.loads((SEED_DIR / "emb_markers.json").read_text())
assert SEED["ids"], "the committed identity seed is EMPTY -- the first build " \
                    "after a merge, and any build db2 cannot reach, names " \
                    "nothing"
assert SEED["ids"] == sorted(set(SEED["ids"])) and \
    all(isinstance(i, int) for i in SEED["ids"]), SEED["ids"][:8]
_stale = sorted(set(SEED["names"]) - {str(i) for i in SEED["ids"]})
assert not _stale, f"name keys outside the identity set (fixtures?): {_stale}"
assert all(isinstance(v, str) and v for v in SEED["names"].values()), \
    "a null or empty name is stored -- the v2 sticky-null class is deleted"
assert not (set(SEED["ids"]) & set(SEED_MARKERS)), "identity intersects markers"
assert SEED["run"].get("markers") == sorted(SEED_MARKERS), \
    f"run block is not from a real run against {SEED_MARKERS}"
assert SEED["run"].get("marker_trees") and SEED["run"].get("backed"), SEED["run"]
print(f"emb seed  : data/emb_identity.json carries {len(SEED['ids'])} identity "
      f"ids, {len(SEED['names'])} names, none outside the set, none null; run "
      f"block from a real derivation over markers {sorted(SEED_MARKERS)}")

print("\nPASS")
