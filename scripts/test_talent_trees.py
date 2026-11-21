"""test_talent_trees: a spec's talent panes hold that spec's talents and no
other's, and a listed build carries the game's import string.

The owner's report (2026-11-21): "the talent build for devourer ... seems
wider than other specs like rogue". The panes are drawn from the union of
talents the spec's players ever took, and a record is labelled with the spec
Warcraft Logs gives the player. A handful of records carry another spec's
talents under the label (15 of 274,007 that day), and ONE is enough to put
that spec's whole tree, and a third hero tree, into the union. Six specs were
drawn that way.

Pinned here, on a journal written by the real collector (parse_summary), in
the production shape (a talent tree and nothing else, no spec id), holding
ONE Retribution record that carries Holy's talents:

  * with the game tables in the geometry cache: the foreign talents, the
    other spec's hero selector and the third hero tree are left out of the
    panes and COUNTED (talents_doc.foreign -- build_health.txt prints it);
    each spec's class pane holds what the game shows that spec, including a
    talent none of its own players took; the other spec's panes are untouched;
  * without them (a v1 cache): the document is built exactly as before,
    polluted panes and all, and says it could not judge -- the absence of the
    table is never read as "nothing is hidden";
  * the test would have failed before the fix: the v1 result IS the bug;
  * a tiered talent is one node whose ranks add up, in the pane and in "sel";
  * every listed build carries "imp", a string that reads back as exactly
    that build; the mislabelled build is listed and carries none, with the
    reason counted; the spec block names the game build the strings were
    written for; no string without the game tables.
"""
import contextlib
import io
import json
import pathlib
import sys
import tempfile

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fetch_data import parse_summary          # noqa: E402
import build_site_data as bsd                  # noqa: E402
import talent_string as ts                     # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


class _Hero:
    def resolve(self, tree):
        return "Hero"


def make_parse(code, char, cls, spec, tree):
    fight = {"code": code, "fid": 1, "dungeon": "Halls", "key_level": 14,
             "region": "US", "score": 400.0, "medal": "gold",
             "affixes": [9], "start_time": 1_787_000_000_000,
             "rank_duration_ms": 1_500_000}
    player = {"id": 1, "name": char, "server": "X", "type": cls,
              "specs": [spec], "icon": f"{cls}-{spec}", "maxItemLevel": 720,
              "combatantInfo": {"talentTree": [{"id": e, "rank": r}
                                               for e, r in tree]}}
    table = {"data": {"totalTime": 1_500_000,
                      "playerDetails": {"dps": [player]},
                      "damageDone": [{"id": 1, "total": 9_000_000}],
                      "deathEvents": []}}
    rows, gear_rows = parse_summary(fight, table, _Hero())
    assert "specID" not in gear_rows[0]["talents"], "the production shape"
    return rows[0], gear_rows[0]


# One class tree, two specs. Class talents left (x < 3000), spec talents
# right (x > 9000), hero selectors between, three hero trees:
# Retribution (70) takes Templar and Herald, Holy (65) Herald and Lightsmith.
GEO = {
    "v": 2, "build": "9.9.9.1",
    "specs": {"70": 900, "65": 900},
    "spec_keys": {"70": "Paladin|Retribution", "65": "Paladin|Holy"},
    "subtrees": {"48": "Templar", "49": "Herald", "50": "Lightsmith"},
    "trees": {"900": {
        "nodes": {"1001": [0, 0, 0, 0],        # class, free for everyone
                  "1002": [600, 0, 0, 0],      # class, 2 ranks
                  "1003": [1200, 0, 2, 0],     # class choice
                  "1004": [1800, 0, 0, 0],     # class variant, Holy only
                  "1005": [2400, 0, 0, 0],     # class, only Holy players take it
                  "2001": [9000, 0, 0, 0],     # Retribution, free
                  "2002": [9600, 0, 1, 0],     # Retribution, tiered 1+2+1
                  "2101": [9000, 600, 0, 0],   # Holy
                  "2500": [5000, 0, 3, 0],     # Retribution's hero selector
                  "2501": [5600, 0, 3, 0],     # Holy's hero selector
                  "3001": [0, 9000, 0, 48], "3002": [600, 9000, 0, 48],
                  "3101": [0, 9600, 0, 49], "3102": [600, 9600, 0, 49],
                  "3201": [0, 10200, 0, 50], "3202": [600, 10200, 0, 50]},
        "edges": [[1001, 1002], [1002, 1004], [2001, 2002], [2001, 2101],
                  [3001, 3002], [3201, 3202]],
        "vis": {"1004": [65], "2001": [70], "2002": [70], "2101": [65],
                "2500": [70], "2501": [65]},
        "grant": {"1001": [], "2001": [70],
                  "3001": [], "3101": [], "3201": []}}},
    "entries": {
        "50001": [1001, 1, 1, None, 0, 0], "50002": [1002, 2, 2, None, 0, 0],
        # the game lists 50004 first; entry-id order is the other way round
        "50003": [1003, 1, 3, None, 200, 0], "50004": [1003, 1, 4, None, 100, 0],
        "50005": [1004, 1, 5, None, 0, 0], "50006": [1005, 1, 17, None, 0, 0],
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
V1 = json.loads(json.dumps(GEO))
V1.pop("v"), V1.pop("build"), V1.pop("spec_keys")
for _t in V1["trees"].values():
    _t.pop("vis"), _t.pop("grant")
V1["entries"] = {k: v[:4] for k, v in V1["entries"].items()}

# the journal logs a free node at rank 1 like any other
RET_A = [(50001, 1), (50002, 1), (50003, 1), (60001, 1), (60002, 1),
         (60003, 2), (65001, 1), (70001, 1), (70002, 1)]           # Templar
RET_B = [(50001, 1), (50002, 2), (50004, 1), (60001, 1), (60002, 1),
         (60003, 2), (60004, 1), (65002, 1), (70101, 1), (70102, 1)]  # Herald
HOLY = [(50001, 1), (50002, 1), (50005, 1), (50006, 1), (60101, 1),
        (65101, 1), (70101, 1), (70102, 1)]                        # Herald
# Holy's talents under a Retribution label: the record behind the bug
WRONG = [(50001, 1), (50005, 1), (60101, 1), (65102, 1), (70201, 1),
         (70202, 1)]                                               # Lightsmith
rows, recs = [], []
for i in range(8):
    rw, rc = make_parse(f"A{i}", f"RetA{i}", "Paladin", "Retribution", RET_A)
    rows.append(rw), recs.append(rc)
for i in range(4):
    rw, rc = make_parse(f"B{i}", f"RetB{i}", "Paladin", "Retribution", RET_B)
    rows.append(rw), recs.append(rc)
rw, rc = make_parse("W0", "Wrong0", "Paladin", "Retribution", WRONG)
rows.append(rw), recs.append(rc)
for i in range(5):
    rw, rc = make_parse(f"H{i}", f"Holy{i}", "Paladin", "Holy", HOLY)
    rows.append(rw), recs.append(rc)


def run(geo, what):
    """(parsed doc, printed text) of the real talents_doc or builds_sidecar."""
    with tempfile.TemporaryDirectory() as tmp:
        tp = pathlib.Path(tmp)
        with (tp / "gear.jsonl").open("w") as fh:
            for r in recs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        bsd.GEAR_JOURNAL = tp / "gear.jsonl"
        bsd.GEAR_EXPORT = tp / "absent.jsonl.gz"
        for attr, fname, obj in (
                ("NAMES_ITEMS", "names_items.json", {}),
                ("NAMES_ENCHANTS", "names_enchants.json", {}),
                ("CRAFTED_IDS", "crafted_ids.json", []),
                ("EMB_IDENTITY", "emb_identity.json", {"ids": [], "names": {}}),
                ("EMB_ITEMS", "emb_items.json", {}),
                ("EMB_OVERRIDES", "emb_overrides.json", {"names": {}, "ids": []}),
                ("EMB_MARKERS", "emb_markers.json", []),
                ("NAMES_ICONS", "names_icons.json", {}),
                ("TRAIT_GEOMETRY", "trait_geometry.json", geo),
                ("NAMES_SPELLS", "names_spells.json", {})):
            setattr(bsd, attr, tp / fname)
            (tp / fname).write_text(json.dumps(obj))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            if what == "talents":
                doc = bsd.talents_doc("test")
            else:
                doc = bsd.builds_sidecar(pd.DataFrame(rows),
                                         bsd.meta_from_gear_journal(),
                                         "test", enc="dense")
        return (json.loads(doc) if doc is not None else None), buf.getvalue()


def panes(doc, sk):
    """(class ids as the PAGE draws them, spec ids, hero tree names)."""
    e = doc["trees"][sk]
    if "class" in e:
        cls = [n["id"] for n in e["class"]["nodes"]]
    else:                       # shared pane minus the spec's hide list
        hide = set(e.get("cx", []))
        cls = [n["id"] for n in doc["classes"][e["classRef"]]["nodes"]
               if n["id"] not in hide]
    return cls, [n["id"] for n in e["spec"]["nodes"]], sorted(e["hero"])


# --- the bug, as it was: no game tables, the union alone ------------------------
old, old_out = run(V1, "talents")
oc, osp, oh = panes(old, "Paladin|Retribution")
check(2101 in osp and oh == ["Herald", "Lightsmith", "Templar"]
      and 1004 in oc and 2501 in oc,
      "without the game tables one mislabelled record puts the other spec's "
      f"talents and a third hero tree into Retribution's panes: spec {osp}, "
      f"hero {oh}")
check(bsd.talents_doc.blind == 2 and bsd.talents_doc.foreign == {}
      and "does not say who sees a node" in old_out,
      "and the build says so: 2 specs drawn from the union alone, none judged")
check(all("cx" not in t for t in old["trees"].values()),
      "a v1 cache hides nothing from a class pane (not knowing is not "
      "'none hidden')")

# --- with the game tables ------------------------------------------------------
new, new_out = run(GEO, "talents")
nc, nsp, nh = panes(new, "Paladin|Retribution")
check(nsp == [2001, 2002] and nh == ["Herald", "Templar"],
      f"with them Retribution's spec pane is Retribution's ({nsp}) and its "
      f"hero trees are its own two ({nh})")
check(nc == [1001, 1002, 1003, 1005, 2500],
      "its class pane: the class talents the game shows it and its own hero "
      f"selector -- not Holy's variant (1004), not Holy's selector (2501): {nc}")
check(1005 in nc and all(e != 50006 for e, _ in RET_A + RET_B),
      "a class talent the game shows Retribution stays on its tree although "
      "no Retribution player took it")
check(bsd.talents_doc.foreign == {"Paladin|Retribution": 5}
      and bsd.talents_doc.blind == 0
      and "Paladin|Retribution 5" in new_out,
      "the 5 nodes left out are counted and printed, not dropped in silence "
      "(1004, 2101, 2501 and the two Lightsmith nodes)")
hc, hsp, hh = panes(new, "Paladin|Holy")
check(hc == [1001, 1002, 1003, 1004, 1005, 2501] and hsp == [2101]
      and hh == ["Herald"],
      f"Holy's panes are Holy's: class {hc}, spec {hsp}, hero {hh}")
ret = new["trees"]["Paladin|Retribution"]
n2002 = [n for n in ret["spec"]["nodes"] if n["id"] == 2002][0]
check(n2002["t"] == 1 and n2002["r"] == 4 and "es" not in n2002,
      "the tiered talent is one node of 4 ranks, not a choice of three")
check(all(a in (n["id"] for n in ret["spec"]["nodes"])
          and b in (n["id"] for n in ret["spec"]["nodes"])
          for a, b in ret["spec"]["edges"]) and [2001, 2101] not in ret["spec"]["edges"],
      "no edge runs to a node that was left out")

# --- the import strings ----------------------------------------------------------
side, side_out = run(GEO, "builds")
pb = side["specs"]["Paladin|Retribution"]
by_n = {b["n"]: b for b in pb["builds"]}
T70 = ts.SpecTree.of(GEO, 70)
check(pb["bkind"] == "hash" and set(by_n) == {8, 4, 1}
      and "imp" in by_n[8] and "imp" in by_n[4],
      "both real Retribution builds carry an import string")
for n, logged, hero in ((8, RET_A, 48), (4, RET_B, 49)):
    d = ts.decode(by_n[n]["imp"], T70.order)
    took = {T70.node_of[e] for e, _r in logged}
    check(d["clean"] and d["version"] == 2 and d["spec"] == 70
          and not any(d["hash"])
          and {k for k, p in d["nodes"].items() if not p["granted"]}
          == took - T70.granted
          and {k for k, p in d["nodes"].items() if p["granted"]} == T70.granted
          and by_n[n]["imp"] == ts.loadout_string(T70, logged),
          f"the string of the build played {n} times reads back as exactly "
          f"that build ({GEO['subtrees'][str(hero)]}): {by_n[n]['imp']}")
dA = ts.decode(by_n[8]["imp"], T70.order)["nodes"]
dB = ts.decode(by_n[4]["imp"], T70.order)["nodes"]
check(dA[1003]["choice"] == 1 and dB[1003]["choice"] == 0
      and dA[2002]["ranks"] == 3 and dB[2002]["ranks"] is None
      and dA[1002]["ranks"] == 1 and dB[1002]["ranks"] is None,
      "option positions follow the game's order, the tiered talent is "
      "written as 3 of 4 and as full, a partial rank as 1 of 2")
check("imp" not in by_n[1] and "sel" in by_n[1],
      "the mislabelled build is still listed, and carries no string")
check(bsd.builds_sidecar.imports
      == (3, {"a hero tree this spec cannot take": 1})
      and "import strings: 3 of 4 listed builds; none for: "
          "a hero tree this spec cannot take x1" in side_out,
      "3 of 4 listed builds get a string (two Retribution, one Holy); the "
      "fourth is the mislabelled one, counted and printed with its reason")
check(pb.get("game") == "9.9.9.1"
      and side["specs"]["Paladin|Holy"].get("game") == "9.9.9.1"
      and bsd.builds_sidecar.game == "9.9.9.1",
      "the spec block names the game build the strings were written for")
selA = {s[0]: s[1:] for s in by_n[8]["sel"]}
check(selA[2002] == [3] and selA[1003] == [1, 0] and selA[2500] == [1, 0],
      f"sel: the tiered talent is [node, 3] with no option index: {by_n[8]['sel']}")
old_side, old_side_out = run(V1, "builds")
check(all("imp" not in b and "sel" in b for s in old_side["specs"].values()
          for b in s["builds"])
      and all("game" not in s for s in old_side["specs"].values())
      and bsd.builds_sidecar.imports[0] == 0,
      "without the game tables no build carries a string, and every build "
      "still carries its selection")

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("PASS  talent trees: a spec's panes are its own; listed builds carry "
      "the game's import string")
