"""test_talent_string: the game's talent import string, written from a logged
tree (owner, 2026-11-21: "add a button to export talents for a selected talent
build").

A wrong string does not fail loudly: it imports a different build, or the game
refuses it with no reason given. So the encoder is held against strings the
GAME produced, not against itself:

  * the bit layout, by hand: a string spelled out bit by bit in this file;
  * 160 armory loadouts, four per spec over all 40 specs
    (fixtures/talent_strings.json, collected by collect_talent_vectors.py):
    every string is reproduced CHARACTER FOR CHARACTER from the list of
    (entry id, rank) pairs alone, which is all a Warcraft Logs talent tree
    carries -- the free nodes, the option positions and the tiered node's
    total all have to come out of the game tables;
  * every one of them also decodes to exactly the nodes the build takes;
  * what is NOT a loadout of the spec gets no string, with the reason: an
    unknown entry, another spec's talent, two hero trees, a hero tree the spec
    cannot take, a rank out of range, two options of one choice, hero talents
    without their selector, a selector pointing at the other tree;
  * a geometry cache without the game tables (v1) gives no tree at all.

The vectors are only valid against the class tree they were taken from. A
talent patch changes the trees (fetch_traits.py re-reads them by itself), and
from then on a recorded string is a string for the OLD tree. The fixture
carries a fingerprint of each tree; a vector whose tree has changed is not
run, and the last line says how many were -- it never passes silently on
nothing. Collect new ones with collect_talent_vectors.py.
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import talent_string as ts                     # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


# --- 1. the bit layout, spelled out -------------------------------------------
# version 2, spec 259, zero hash, then five nodes:
#   10  not selected                      0
#   11  granted                           1 0
#   12  purchased, full, single           1 1 0 0
#   13  purchased, 2 of 3 ranks, single   1 1 1 010000 0
#   14  purchased, full, choice option 2  1 1 0 1 01
bits = []
bits += [0, 1, 0, 0, 0, 0, 0, 0]                              # 2, low bit first
bits += [1, 1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0]      # 259 = 0x0103
bits += [0] * 128
bits += [0]
bits += [1, 0]
bits += [1, 1, 0, 0]
bits += [1, 1, 1, 0, 1, 0, 0, 0, 0, 0]
bits += [1, 1, 0, 1, 0, 1]
want = "".join(ts.ALPHABET[sum(b << j for j, b in enumerate(bits[i:i + 6]))]
               for i in range(0, len(bits), 6))
ORDER = [10, 11, 12, 13, 14]
PICKED = {11: {"granted": True},
          12: {"granted": False, "ranks": 1, "max": 1, "choice": None},
          13: {"granted": False, "ranks": 2, "max": 3, "choice": None},
          14: {"granted": False, "ranks": 1, "max": 1, "choice": 2}}
got = ts.encode(259, ORDER, PICKED)
check(got == want and got.startswith("CMQA") and len(got) == 30,
      f"the layout written by hand, bit by bit, is what encode writes: {got}")
back = ts.decode(got, ORDER)
check(back["version"] == 2 and back["spec"] == 259 and not any(back["hash"])
      and back["clean"]
      and back["nodes"] == {11: {"granted": True, "ranks": None, "choice": None},
                            12: {"granted": False, "ranks": None, "choice": None},
                            13: {"granted": False, "ranks": 2, "choice": None},
                            14: {"granted": False, "ranks": None, "choice": 2}},
      "decode reads it back: header, a free node, a full one, a partial one, "
      "a choice")
check(ts.encode(259, ORDER, PICKED, tree_hash=bytes(range(16))) != got
      and ts.decode(ts.encode(259, ORDER, PICKED, tree_hash=bytes(range(16))),
                    ORDER)["hash"] == bytes(range(16)),
      "a tree hash is carried when one is given (the page never gives one)")
check(not ts.decode(got, ORDER[:4])["clean"],
      "a string read against a shorter node list is reported as not clean")
for label, fn in (
        ("a node order that is not ascending",
         lambda: ts.encode(259, [11, 10], {})),
        ("a picked node that is not in the tree",
         lambda: ts.encode(259, ORDER, {99: {"granted": True}})),
        ("more ranks than the node has",
         lambda: ts.encode(259, ORDER, {12: {"granted": False, "ranks": 2,
                                             "max": 1, "choice": None}})),
        ("a fifth choice option",
         lambda: ts.encode(259, ORDER, {14: {"granted": False, "ranks": 1,
                                             "max": 1, "choice": 4}})),
        ("a character outside the alphabet", lambda: ts.decode("CMQ*", ORDER)),
        ("a string that ends inside a value", lambda: ts.decode("CMQA", ORDER))):
    try:
        fn()
        check(False, f"{label} is refused")
    except ValueError:
        check(True, f"{label} is refused")

# --- 2. a small tree, and everything that is not a loadout of the spec ---------
# one class tree, two specs (70 and 65), three hero trees: 70 takes 48 and 49,
# 65 takes 49 and 50
GEO = {
    "v": 2, "build": "9.9.9.1",
    "specs": {"70": 900, "65": 900},
    "spec_keys": {"70": "Paladin|Retribution", "65": "Paladin|Holy"},
    "subtrees": {"48": "Templar", "49": "Herald", "50": "Lightsmith"},
    "trees": {"900": {
        "nodes": {"1001": [0, 0, 0, 0],        # class, free for everyone
                  "1002": [600, 0, 0, 0],      # class, 2 ranks
                  "1003": [1200, 0, 2, 0],     # class choice
                  "1004": [1800, 0, 0, 0],     # class, shown to 65 only
                  "2001": [9000, 0, 0, 0],     # spec 70, free for 70
                  "2002": [9600, 0, 1, 0],     # spec 70, tiered 1+2+1
                  "2101": [9000, 600, 0, 0],   # spec 65
                  "2500": [5000, 0, 3, 0],     # hero selector of 70
                  "2501": [5000, 600, 3, 0],   # hero selector of 65
                  "3001": [5000, 1200, 0, 48], "3002": [5000, 1800, 0, 48],
                  "3101": [5600, 1200, 0, 49], "3102": [5600, 1800, 0, 49],
                  "3201": [6200, 1200, 0, 50], "3202": [6200, 1800, 0, 50]},
        "edges": [],
        "vis": {"1004": [65], "2001": [70], "2002": [70], "2101": [65],
                "2500": [70], "2501": [65],
                # hero nodes are judged by the selector, not by these
                "3001": [70], "3002": [70], "3201": [65], "3202": [65]},
        "grant": {"1001": [], "2001": [70],
                  "3001": [], "3101": [], "3201": []}}},
    "entries": {
        "50001": [1001, 1, 1, None, 0, 0], "50002": [1002, 2, 2, None, 0, 0],
        # the choice's options: _Index order is 50004 then 50003
        "50003": [1003, 1, 3, None, 200, 0], "50004": [1003, 1, 4, None, 100, 0],
        "50005": [1004, 1, 5, None, 0, 0],
        "60001": [2001, 1, 6, None, 0, 0],
        "60002": [2002, 1, 7, None, 0, 0], "60003": [2002, 2, 8, None, 1, 0],
        "60004": [2002, 1, 9, None, 2, 0],
        "60101": [2101, 1, 10, None, 0, 0],
        "65001": [2500, 1, 0, None, 0, 48], "65002": [2500, 1, 0, None, 1, 49],
        "65101": [2501, 1, 0, None, 0, 49], "65102": [2501, 1, 0, None, 1, 50],
        "70001": [3001, 1, 11, None, 0, 48], "70002": [3002, 1, 12, None, 0, 48],
        "70101": [3101, 1, 13, None, 0, 49], "70102": [3102, 1, 14, None, 0, 49],
        "70201": [3201, 1, 15, None, 0, 50], "70202": [3202, 1, 16, None, 0, 50]},
}
T70 = ts.SpecTree.of(GEO, 70)
check(T70 is not None and T70.order == sorted(int(n) for n in GEO["trees"]["900"]["nodes"])
      and T70.hidden == {1004, 2101, 2501} and set(T70.hero) == {48, 49}
      and T70.options[1003] == [50004, 50003]
      and T70.granted == {1001, 2001, 3001, 3101},
      "the spec's tree: every node of the class tree in the order, the other "
      "spec's talents hidden, its two hero trees from its own selector, "
      "choice options in the game's order, free nodes of its own hero trees "
      "only")
BUILD = [(50002, 1), (50003, 1), (60001, 1), (60002, 1), (60003, 2),
         (65001, 1), (70001, 1), (70002, 1)]
picked, why = ts.loadout(T70, BUILD)
check(picked is not None and picked[1002] == {"granted": False, "ranks": 1,
                                              "max": 2, "choice": None}
      and picked[1003]["choice"] == 1                 # 50003 is the SECOND option
      and picked[2002] == {"granted": False, "ranks": 3, "max": 4, "choice": None}
      and picked[2500]["choice"] == 0
      and picked[1001]["granted"] and picked[2001]["granted"]
      and picked[3001]["granted"] and picked[3101]["granted"]
      and 3201 not in picked,
      "a logged tree becomes a loadout: a partial rank, the choice by its "
      "position in the game's order, the tiered node as one node with the "
      "sum, the free nodes marked -- the unpicked hero tree's too, never a "
      "hero tree the spec cannot take")
text = ts.loadout_string(T70, BUILD)
dec = ts.decode(text, T70.order)
check(dec["clean"] and dec["spec"] == 70
      and {n for n, p in dec["nodes"].items() if p["granted"]} == T70.granted
      and dec["nodes"][2002] == {"granted": False, "ranks": 3, "choice": None}
      and dec["nodes"][1003]["choice"] == 1 and dec["nodes"][2500]["choice"] == 0
      and dec["nodes"][3002] == {"granted": False, "ranks": None, "choice": None},
      f"and its string reads back as that build: {text}")
check(ts.loadout_string(T70, list(reversed(BUILD))) == text,
      "the order a log lists the talents in does not change the string")
for label, pairs, reason in (
        ("an entry the tables do not know", BUILD + [(99999, 1)],
         "an entry the trait tables do not know"),
        ("a talent of the other spec", BUILD + [(60101, 1)],
         "a talent the game does not show this spec"),
        ("a class talent only the other spec sees", BUILD + [(50005, 1)],
         "a talent the game does not show this spec"),
        ("talents of two hero trees", BUILD + [(70102, 1)],
         "talents of two hero trees"),
        ("a hero tree the spec cannot take",
         [p for p in BUILD if p[0] < 65000] + [(70202, 1)],
         "a hero tree this spec cannot take"),
        ("a rank above the talent's maximum",
         [(50002, 3)] + [p for p in BUILD if p[0] != 50002],
         "a rank outside the talent's range"),
        ("a rank of zero", [(50002, 0)] + [p for p in BUILD if p[0] != 50002],
         "a rank outside the talent's range"),
        ("two options of one choice", BUILD + [(50004, 1)],
         "two options of one choice"),
        ("the same entry twice", BUILD + [(50002, 1)], "an entry logged twice"),
        ("hero talents without the hero selector",
         [p for p in BUILD if p[0] != 65001],
         "hero talents without the hero selector"),
        ("a selector that points at the other hero tree",
         [p for p in BUILD if p[0] != 65001] + [(65002, 1)],
         "the hero selector points at another tree")):
    p, w = ts.loadout(T70, pairs)
    check(p is None and w == reason and ts.loadout_string(T70, pairs) is None,
          f"no string for {label}: {w!r}")
T65 = ts.SpecTree.of(GEO, 65)
check(T65.granted == {1001, 3101, 3201} and set(T65.hero) == {49, 50}
      and ts.loadout(T65, [(60001, 1)])[0] is None
      and ts.loadout(T65, [(50005, 1), (60101, 1), (65102, 1), (70201, 1)])[0]
      is not None,
      "the other spec of the same tree has its own hidden, free and hero sets")
V1 = json.loads(json.dumps(GEO))
for t in V1["trees"].values():
    t.pop("vis"), t.pop("grant")
check(ts.SpecTree.of(V1, 70) is None and ts.loadout_string(None, BUILD) is None
      and ts.SpecTree.of(GEO, 71) is None,
      "a cache without the game tables, or a spec it does not hold, gives no "
      "tree and so no string")
check(ts.tree_stamp(GEO, 900) == ts.tree_stamp(json.loads(json.dumps(GEO)), "900")
      and ts.tree_stamp(GEO, 900) != ts.tree_stamp(
          dict(GEO, entries=dict(GEO["entries"], **{"50003": [1003, 1, 3, None, 50, 0]})), 900)
      and ts.tree_stamp(GEO, 900) == ts.tree_stamp(
          dict(GEO, trees={"900": dict(GEO["trees"]["900"], nodes=dict(
              GEO["trees"]["900"]["nodes"], **{"1002": [777, 5, 0, 0]}))}), 900),
      "the tree fingerprint moves with an option's order and not with a "
      "node's position on the page")

# --- 3. the game's own strings -------------------------------------------------
FIX = json.loads((HERE / "fixtures" / "talent_strings.json").read_text())
REAL = json.loads((HERE.parent / "data" / "trait_geometry.json").read_text())
check(REAL.get("v") == 2 and bool(REAL.get("build")),
      f"the committed geometry cache holds the game tables, read at game "
      f"build {REAL.get('build')}")
vec = FIX["vectors"]
specs = {v["k"] for v in vec}
check(len(vec) >= 120 and len(specs) == 40
      and all(set(v) == {"k", "sid", "s", "t"} for v in vec),
      f"the fixture: {len(vec)} strings the game exported, over {len(specs)} "
      f"of 40 specs, talents only -- no character identity")
changed = sorted(t for t, stamp in FIX["trees"].items()
                 if t not in REAL["trees"] or ts.tree_stamp(REAL, t) != stamp)
ran = same = reads = 0
miss = []
for v in vec:
    tid = str(REAL["specs"].get(str(v["sid"])))
    if tid in changed or tid not in FIX["trees"]:
        continue
    ran += 1
    tree = ts.SpecTree.of(REAL, v["sid"])
    got = ts.loadout_string(tree, [tuple(p) for p in v["t"]])
    if got == v["s"]:
        same += 1
    elif len(miss) < 5:
        miss.append((v["k"], ts.loadout(tree, v["t"])[1] or "differs"))
    d = ts.decode(v["s"], tree.order)
    taken = {tree.node_of[e] for e, _r in v["t"]}
    if (d["version"] == 2 and d["spec"] == v["sid"] and d["clean"]
            and not any(d["hash"])
            and {n for n, p in d["nodes"].items() if not p["granted"]}
            == taken - tree.granted):
        reads += 1
check(ran > 0 and same == ran,
      f"{same} of {ran} game strings reproduced character for character from "
      f"the (entry, rank) pairs alone" + (f"; first misses {miss}" if miss else ""))
check(ran > 0 and reads == ran,
      f"{reads} of {ran} game strings read back as exactly the talents the "
      f"build takes, ending where the node list ends")
if changed:
    held = sum(1 for v in vec
               if str(REAL["specs"].get(str(v["sid"]))) in changed)
    print(f"NOT RUN: {held} of {len(vec)} vectors. The class trees {changed} "
          f"have changed since they were collected (fixture: game build "
          f"{FIX.get('build')}, cache: {REAL.get('build')}); a recorded "
          f"string is a string for the old tree. Collect new ones: "
          f"python3 scripts/collect_talent_vectors.py")
check(ran >= len(vec) // 2,
      f"at least half of the vectors still stand on the tree they were taken "
      f"from ({ran} of {len(vec)})")

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print(f"PASS  talent import strings: the layout by hand, {same} game strings "
      f"reproduced exactly"
      + (f", {len(vec) - ran} not run (their trees changed)" if ran < len(vec)
         else ""))
