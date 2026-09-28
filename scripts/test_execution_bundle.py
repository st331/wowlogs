#!/usr/bin/env python3
"""The execution bundle's parsers (scripts/execution.py), on the REAL
six-table response: scripts/fixtures/bundle_shape_f.json is the eight-run
bundle the 2026-09-27 probe pulled (pug/verify_cost.md shape f) with the
combatantInfo/gear/talents bulk stripped -- every table's entries are
untouched.

Pinned here, against sums recomputed independently from the fixture:

  * per-player kicks / dispels equal the sum of that player's `details`
    totals across every spell row; kicks_by / dispels_by hold the same
    numbers per enemy spell id;
  * a player absent from a table's entries is ZERO-filled (Casts lists only
    players with a matching cast: 4 of 5 in a0), and an NPC row in Healing
    (a1 carries one) is dropped, never attributed;
  * int_spells / dispel_spells carry the run-level begun/completed/
    interrupted (applied/expired/dispelled) sums per spell;
  * a table missing from the node yields None for that column and touches
    nothing else; a pet dispeller is attributed to its owner only when the
    node carries masterData.actors with petOwner, else dropped;
  * deaths_chain: another party death within the previous 5 s, one per own
    death, non-party and untimed events ignored, None when deathEvents is
    absent; pots / hs are None when the field is absent, never 0;
  * pack_by / unpack_by survive the CSV round trip with {} distinct from
    "not fetched"; bundle_subquery emits the six tables (the filtered two
    omitted on an empty list) with the Summary under the `table` alias;
  * kit_ids / avoidable_ids read the vendored lists.json; load_lists never
    raises and falls back to the vendored copy.
"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import execution as ex                       # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "scripts" / "fixtures" / "bundle_shape_f.json"
fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


rd = json.loads(FIXTURE.read_text())["data"]["reportData"]
check(len(rd) == 8 and all(set(ex.BUNDLE_TABLES) <= set(n) and "table" in n
                           for n in rd.values()),
      "fixture: 8 aliases, each with the Summary under `table` and the five bundle tables")


def party(node):
    return [c["id"] for c in node["table"]["data"]["composition"]]


def independent_sums(node, key):
    """{player id: total} and {guid: (begun, completed, interrupted)} straight
    off the fixture, without the parser."""
    per, spells = {}, {}
    for grp in node[key]["data"]["entries"]:
        for sub in grp["entries"]:
            spells[str(sub["guid"])] = (sub["spellsBegun"], sub["spellsCompleted"],
                                        sub["spellsInterrupted"])
            for det in sub["details"]:
                per[det["id"]] = per.get(det["id"], 0) + det["total"]
    return per, spells


# --- 1. every alias: totals, by-spell tables, zero-fill, NPC drop -------------
zero_filled = npc_dropped = 0
for alias, node in rd.items():
    ids = party(node)
    per, ints, disp, present = ex.parse_tables(node, ids)
    check(all(present.values()) and set(per) == set(ids), f"{alias}: five tables present, one row per party member")
    for key, col, by_col, spells_out in (("interrupts", "kicks", "kicks_by", ints),
                                         ("dispels", "dispels", "dispels_by", disp)):
        want, spells = independent_sums(node, key)
        ok_tot = all(per[i][col] == want.get(i, 0) for i in ids)
        ok_by = all(sum(per[i][by_col].values()) == per[i][col] for i in ids)
        ok_sp = set(spells_out) == set(spells)
        if key == "interrupts":
            ok_sp = ok_sp and all((s["begun"], s["completed"], s["interrupted"]) == spells[g]
                                  for g, s in spells_out.items())
        else:
            ok_sp = ok_sp and all((s["applied"], s["expired"], s["dispelled"]) == spells[g]
                                  for g, s in spells_out.items())
        check(ok_tot and ok_by and ok_sp,
              f"{alias}: {col} = Σ details ({sum(want.values())}), {by_col} sums to it, "
              f"{len(spells)} spells with run-level sums")
    # zero-fill: casts lists only players with a matching cast
    listed = {e["id"] for e in node["casts"]["data"]["entries"]}
    for i in ids:
        if i not in listed:
            zero_filled += 1
            check(per[i]["def_casts"] == 0, f"{alias}: player {i} absent from Casts -> def_casts 0")
        else:
            check(per[i]["def_casts"] == next(e["total"] for e in node["casts"]["data"]["entries"] if e["id"] == i),
                  f"{alias}: player {i} def_casts = Casts total")
    # damage taken: every player listed
    dt = {e["id"]: e["total"] for e in node["dmgTaken"]["data"]["entries"]}
    check(all(per[i]["avoid_dmg"] == dt[i] for i in ids), f"{alias}: avoid_dmg = filtered DamageTaken total")
    # healing: NPC rows dropped, overheal carried
    hl = [e for e in node["healing"]["data"]["entries"]]
    npc = [e for e in hl if e.get("type") == "NPC"]
    npc_dropped += len(npc)
    # `overheal` is absent on some real rows (verify_cost: "may be null") -> 0
    check(all(per[i]["heal_total"] == next(e["total"] for e in hl if e["id"] == i) and
              per[i]["heal_over"] == (next(e.get("overheal") for e in hl if e["id"] == i) or 0) for i in ids)
          and not any(e["id"] in per for e in npc),
          f"{alias}: heal_total/heal_over per player{' (NPC row dropped)' if npc else ''}")
check(zero_filled >= 5, f"zero-fill exercised on {zero_filled} absent Casts rows across the fixture")
check(npc_dropped >= 1, f"NPC healing rows dropped: {npc_dropped}")

# --- 2. a missing table -> None for its column only ----------------------------
node = rd["a1"]
ids = party(node)
partial = {k: v for k, v in node.items() if k != "dispels"}
per, ints, disp, present = ex.parse_tables(partial, ids)
check(present["dispels"] is False and all(per[i]["dispels"] is None and per[i]["dispels_by"] is None for i in ids)
      and disp == {} and all(isinstance(per[i]["kicks"], int) for i in ids),
      "a1 without Dispels: dispels/dispels_by None, dispel_spells empty, the rest intact")
per, _, _, present = ex.parse_tables({"interrupts": None, "casts": {"data": None}}, ids)
check(not any(present.values()) and all(v is None for i in ids for v in per[i].values()),
      "null tables (an alias that errored) read as missing: every column None")

# --- 3. pet dispeller: owner when masterData says so, else dropped ------------
pet_node = json.loads(json.dumps({"dispels": node["dispels"]}))
row = pet_node["dispels"]["data"]["entries"][0]["entries"][0]
row["details"].append({"name": "Fire Elemental", "id": 999, "type": "Pet", "total": 3})
owner = ids[0]
before = independent_sums(node, "dispels")[0].get(owner, 0)
per, _, _, _ = ex.parse_tables(pet_node, ids)
check(per[owner]["dispels"] == before and all(999 not in per for _ in [0]),
      "pet dispeller without masterData: dropped")
per, _, _, _ = ex.parse_tables(pet_node, ids, actors={999: owner})
check(per[owner]["dispels"] == before + 3 and per[owner]["dispels_by"][str(row["guid"])] >= 3,
      "pet dispeller with petOwner: attributed to the owner")
md = {"masterData": {"actors": [{"id": 999, "petOwner": owner}, {"id": 5, "petOwner": None}]}}
check(ex.pet_owners(md) == {999: owner}, "pet_owners: only actors with a petOwner")

# --- 4. deaths_chain ---------------------------------------------------------------
P = [1, 2, 3, 4, 5]
ev = [{"id": 1, "deathTime": 10_000}, {"id": 2, "deathTime": 13_000},     # 3 s later: chained
      {"id": 3, "deathTime": 19_000}, {"id": 4, "deathTime": 19_000},     # 6 s after 2: not; simultaneous pair
      {"id": 5, "deathTime": 24_001},                                     # 5.001 s after: not
      {"id": 77, "deathTime": 24_500},                                    # non-party: ignored
      {"id": 1}]                                                          # no deathTime: ignored
ch = ex.deaths_chain(ev, P)
check(ch == {1: 0, 2: 1, 3: 0, 4: 1, 5: 0},
      f"deaths_chain: 3 s -> chained, 6 s -> not, 5.001 s -> not, simultaneous pair counts once, "
      f"non-party and untimed ignored ({ch})")
check(ex.deaths_chain(None, P) == {i: None for i in P}, "deaths_chain: no deathEvents list -> None")
check(ex.deaths_chain([], P) == {i: 0 for i in P}, "deaths_chain: no deaths -> 0")
check(ex.deaths_chain([{"id": 1, "deathTime": 100}, {"id": 2, "deathTime": 5_100}], P)[2] == 1,
      "deaths_chain: exactly 5 s counts")
# the fixture's real deaths: a1 has two, 159 s apart -> none chained
a1 = rd["a1"]["table"]["data"]["deathEvents"]
check(len(a1) == 2 and sum(ex.deaths_chain(a1, party(rd["a1"])).values()) == 0,
      "fixture a1: two deaths 159 s apart -> no chain")

# --- 5. pots / hs -------------------------------------------------------------------
check(ex.summary_player_fields({"potionUse": 3, "healthstoneUse": 0}) == {"pots": 3, "hs": 0},
      "pots/hs: raw counts, 0 kept as 0")
check(ex.summary_player_fields({"name": "x"}) == {"pots": None, "hs": None},
      "pots/hs: absent field -> None, never 0")
check(ex.summary_player_fields({"potionUse": None}) == {"pots": None, "hs": None},
      "pots/hs: null field -> None")

# --- 6. pack / unpack ---------------------------------------------------------------
check(ex.pack_by({"1294557": 12, "1307571": 11}) == "1294557:12|1307571:11", "pack_by: sorted guid:n pairs")
check(ex.pack_by({}) == "none" and ex.pack_by(None) is None, "pack_by: {} -> 'none', None -> None")
check(ex.unpack_by("1294557:12|1307571:11") == {"1294557": 12, "1307571": 11}, "unpack_by: inverse")
check(ex.unpack_by("none") == {} and ex.unpack_by(None) is None and ex.unpack_by(float("nan")) is None
      and ex.unpack_by("") is None, "unpack_by: 'none' -> {}, None/NaN/'' -> None")
check(ex.unpack_by({"5": 2}) == {"5": 2}, "unpack_by: a dict passes through")

# --- 7. the sub-query ---------------------------------------------------------------
q = ex.bundle_subquery("a0", "gDZJMwmzvykQprcB", 8, [1306338, 1308518], [23920, 6262])
check(q.startswith('a0: report(code: "gDZJMwmzvykQprcB") {') and "table: table(fightIDs: [8], dataType: Summary)" in q
      and "interrupts: table(fightIDs: [8], dataType: Interrupts)" in q
      and "dispels: table(fightIDs: [8], dataType: Dispels)" in q
      and 'dmgTaken: table(fightIDs: [8], dataType: DamageTaken, filterExpression: "ability.id in (1306338,1308518)")' in q
      and 'casts: table(fightIDs: [8], dataType: Casts, filterExpression: "ability.id in (23920,6262)")' in q
      and "healing: table(fightIDs: [8], dataType: Healing)" in q and q.endswith("}"),
      "bundle_subquery: six tables, Summary aliased `table`, both filters")
q2 = ex.bundle_subquery("a3", "x", 2, [], [23920])
check("dmgTaken" not in q2 and "casts:" in q2, "bundle_subquery: no avoidable list -> no DamageTaken table")
q3 = ex.bundle_subquery("a3", "x", 2, [1], [])
check("dmgTaken" in q3 and "casts:" not in q3, "bundle_subquery: no kit -> no Casts table")

# --- 8. the lists --------------------------------------------------------------------
lists = ex.load_lists(fetch=False, log=lambda *_: None)
check(lists["_source"] in ("vendored", "cached") and "Warrior-Arms" in lists["specs"],
      f"load_lists(fetch=False): {lists['_source']} copy, version {lists.get('version')}")
arms = ex.kit_ids(lists, ["Warrior-Arms"])
want = {118038, 23920, 97462, 386208, 202168, 6262, 1295247, 1236994, 1236616}
check(set(arms) == want, "kit_ids(Warrior-Arms) = defensives + selfheals + consumables")
check(ex.kit_ids(lists, ["Warrior-Fury"]) == arms and ex.kit_ids(lists, ["Warrior"]) == arms,
      "kit_ids: unknown spec / class alone -> the class-level union")
check(set(ex.kit_ids(lists, ["Warrior-Arms", "Rogue-Assassination"])) == want | {5277, 31224, 1966, 185311},
      "kit_ids: union over the roster")
check(ex.avoidable_ids(lists, "Altar of Fangs") == [], "avoidable_ids: provisional lists -> []")
full = {"specs": {}, "dungeons": {"Altar of Fangs": {"avoidable": [1306338, {"id": 1308518}, "7"]}}}
check(ex.avoidable_ids(full, "Altar of Fangs") == [7, 1306338, 1308518], "avoidable_ids: ints, {id}, strings")
check(ex.roster_specs([{"class": "Warrior", "spec": "Arms"}, {"class": "Mage", "spec": ""}, "junk"])
      == ["Warrior-Arms", "Mage"], "roster_specs: Class-Spec, class alone without a spec")


class _Boom:
    def get(self, *a, **k):
        raise OSError("no network")


import execution as ex_mod                                  # noqa: E402
saved = sys.modules.get("requests")
sys.modules["requests"] = _Boom()                            # the import inside load_lists sees this
try:
    doc = ex.load_lists(fetch=True, log=lambda *_: None)
finally:
    if saved is not None:
        sys.modules["requests"] = saved
    else:
        del sys.modules["requests"]
check(doc["_source"] in ("vendored", "cached") and doc["specs"], "load_lists: a failing fetch falls back, never raises")


class _Resp:
    def __init__(self, doc):
        self._d = doc

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


class _Fake:
    def __init__(self, doc):
        self.doc = doc

    def get(self, url, timeout=None):
        return _Resp(self.doc)


with tempfile.TemporaryDirectory() as tmp:
    ex_mod.LISTS_CACHE = pathlib.Path(tmp) / "lists.json"
    sys.modules["requests"] = _Fake({"version": "99", "specs": {"X-Y": {}}, "dungeons": {}})
    try:
        doc = ex.load_lists(fetch=True, log=lambda *_: None)
        check(doc["_source"] == "fetched" and doc["version"] == "99" and ex_mod.LISTS_CACHE.exists(),
              "load_lists: a good fetch wins and is cached")
        sys.modules["requests"] = _Fake({"garbage": True})
        doc = ex.load_lists(fetch=True, log=lambda *_: None)
        check(doc["_source"] == "cached" and doc["version"] == "99",
              "load_lists: an unexpected document -> the cached copy, then the vendored one")
    finally:
        if saved is not None:
            sys.modules["requests"] = saved
        else:
            sys.modules.pop("requests", None)


# --- lean bundle (the backfill): no Casts, no Healing --------------------------
_lean = ex.bundle_subquery("a0", "ABC", 3, [1, 2], [7, 8], lean=True)
_full = ex.bundle_subquery("a0", "ABC", 3, [1, 2], [7, 8])
check("dataType: Summary" in _lean and "dataType: Interrupts" in _lean and "dataType: Dispels" in _lean
      and "dataType: DamageTaken" in _lean and "casts:" not in _lean and "healing:" not in _lean,
      "lean bundle: Summary, Interrupts, Dispels, filtered DamageTaken; no Casts, no Healing")
check("casts:" in _full and "healing:" in _full, "the full bundle still carries Casts and Healing")
check(ex.EST_COST_BUNDLE_LEAN < ex.EST_COST_BUNDLE, "the lean bundle reserves less")

print()
if fails:
    sys.exit(f"FAILED ({fails} failures)")
print("PASS")
