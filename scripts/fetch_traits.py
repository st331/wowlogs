#!/usr/bin/env python3
"""Trait-tree geometry and spell names for the in-game-style talent renderer.

Companion to fetch_names.py, run in the same collector slot (between
fetch_data and build_site_data; order relative to fetch_names is free).
Two caches, both committed:

  data/trait_geometry.json  {"v": 2,
                             "specs":    {"<chrSpecId>": treeId},
                             "spec_keys": {"<chrSpecId>": "DemonHunter|Devourer"},
                             "subtrees": {"<subtreeId>": "Templar", ...},
                             "trees":    {"<treeId>": {
                                 "nodes": {"<nodeId>": [x, y, type, subtree]},
                                 "edges": [[a, b], ...],
                                 "vis":   {"<nodeId>": [chrSpecId, ...]},
                                 "grant": {"<nodeId>": [chrSpecId, ...]}}},
                             "entries":  {"<entryId>":
                                 [nodeId, maxRanks, spellId, overrideName,
                                  orderIndex, subtreeId]}}

"vis" lists a node ONLY when the game shows it to some of the tree's specs and
not to others (a spec talent, or a class-tree variant); a node that is absent
is seen by every spec of the class. "grant" lists the nodes the game gives for
free, with the specs that get them ([] = every spec). A v1 cache has neither,
and every reader treats their absence as "not known", never as "none".
  data/names_spells.json    {"<spellId>": {"n": "...", "ic": "inv_..."}
                             | {"n": null, "ic": null}}   -- grow-only

Verified live on wago.tools (2026-08-30): TraitTreeLoadout maps
ChrSpecializationID -> TraitTreeID (one CLASS tree carries every spec of the
class, hero subtrees marked by TraitNode.TraitSubTreeID); TraitNode is
filterable per tree and carries PosX/PosY/Type; TraitEdge has NO tree column
(fetched whole, filtered by node membership); the journal's talents.tree ids
are TraitNodeENTRY ids (confirmed against data/hero_talent_map.json), mapped
to nodes through TraitNodeXTraitNodeEntry; TraitNodeEntry carries MaxRanks
and TraitDefinitionID; TraitDefinition carries SpellID + name overrides.
Spell name AND icon come from wowhead's nether tooltip endpoint in one
request (the db2 fdid path dead-ends: zamimg 404s legacy fdids like 132123);
icon images join the SAME store fetch_names uses (data/processed/icons/).

Verified live on wago.tools (2026-11-21), for v2: which spec sees a node is
TraitCond (CondType 0 Available / 1 Visible, SpecSetID -> SpecSetMember),
attached to the node directly (TraitNodeXTraitCond) or through one of its
groups (TraitNodeGroupXTraitNode -> TraitNodeGroupXTraitCond); a node with no
spec-set condition is seen by the whole class. Checked against the site's own
data that day: all 1,600 listed builds (40 per spec) take only nodes this rule
shows their spec, and 34 of 40 specs' observed spec trees equal it exactly --
the other six were the bug (see build_site_data.talents_doc). CondType 2 is
Granted. TraitTreeLoadout is NOT a spec -> class-tree map (it holds starter
loadouts of several trait systems; "newest row wins" sent Fury to tree 880
while Arms and Protection sat in 850): the class tree of a spec is the tree
whose spec-set conditions name it. A choice node's options are ordered by
TraitNodeXTraitNodeEntry._Index, which is NOT entry-id order (214 of 470
choice nodes differ), and that order is what the game's import string indexes.

Same contract as fetch_names: grow-only merges, per-id fetches budgeted by
--limit, 150 ms spacing, any failure logs and exits 0 -- never blocks.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fetch_names as fn

TRAIT_GEOMETRY = fn.DATA / "trait_geometry.json"
NAMES_SPELLS = fn.DATA / "names_spells.json"
NETHER_SPELL = "https://nether.wowhead.com/tooltip/spell/{sid}"


def fetch_geometry():
    """The whole geometry snapshot off wago.tools, or None on any failure.

    All-or-nothing on purpose: a half-refreshed geometry (nodes from one
    patch, entries from another) would draw wrong trees, so any failed
    component keeps the previous cache untouched for this run.
    """
    lo = fn._get_csv("TraitTreeLoadout")
    if lo is fn._FAILED:
        return None
    specs: dict[str, int] = {}
    for r in sorted(lo, key=lambda r: int(r.get("ID") or 0)):
        sid, tid = r.get("ChrSpecializationID"), r.get("TraitTreeID")
        if sid and tid and str(tid).isdigit():
            specs[str(sid)] = int(tid)         # newest loadout row wins
    sub = fn._get_csv("TraitSubTree")
    if sub is fn._FAILED:
        return None
    subtrees = {str(r["ID"]): nm for r in sub
                if (nm := fn._field(r, "Name_lang")) and "DNT" not in nm}

    trees: dict[str, dict] = {}
    node_tree: dict[int, str] = {}
    for tid in sorted(set(specs.values())):
        rows = fn._get_csv("TraitNode", {"filter[TraitTreeID]": f"exact:{tid}"})
        if rows is fn._FAILED:
            return None
        nodes = {}
        for r in rows:
            nid = int(r["ID"])
            nodes[str(nid)] = [int(float(r.get("PosX") or 0)),
                               int(float(r.get("PosY") or 0)),
                               int(r.get("Type") or 0),
                               int(r.get("TraitSubTreeID") or 0)]
            node_tree[nid] = str(tid)
        trees[str(tid)] = {"nodes": nodes, "edges": []}

    edges = fn._get_csv("TraitEdge")           # no tree column: filter here
    if edges is fn._FAILED:
        return None
    for r in edges:
        try:
            a, b = int(r["LeftTraitNodeID"]), int(r["RightTraitNodeID"])
        except (KeyError, ValueError):
            continue
        t = node_tree.get(a)
        if t is not None and node_tree.get(b) == t:
            trees[t]["edges"].append([a, b])
    for t in trees.values():
        t["edges"].sort()

    xref = fn._get_csv("TraitNodeXTraitNodeEntry")
    entry_rows = fn._get_csv("TraitNodeEntry")
    def_rows = fn._get_csv("TraitDefinition")
    if fn._FAILED in (xref, entry_rows, def_rows):
        return None
    by_entry = {r["ID"]: r for r in entry_rows if r.get("ID")}
    by_def = {r["ID"]: r for r in def_rows if r.get("ID")}
    entries: dict[str, list] = {}
    # first xref per entry wins (an entry belongs to one node in practice)
    for r in sorted(xref, key=lambda r: int(fn._field(r, "_Index", "Index")
                                            or 0)):
        eid, nid = r.get("TraitNodeEntryID"), r.get("TraitNodeID")
        if not eid or not nid or int(nid) not in node_tree:
            continue
        if eid in entries:
            continue
        e = by_entry.get(eid) or {}
        d = by_def.get(e.get("TraitDefinitionID") or "") or {}
        override = fn._field(d, "OverrideName_lang") or None
        spell = d.get("SpellID")
        idx = fn._field(r, "_Index", "Index")
        st = e.get("TraitSubTreeID")
        entries[eid] = [int(nid), int(e.get("MaxRanks") or 1),
                        int(spell) if spell and str(spell).isdigit() else 0,
                        override,
                        int(idx) if idx and str(idx).lstrip("-").isdigit() else 0,
                        int(st) if st and str(st).isdigit() else 0]
    vis = fetch_visibility(trees, node_tree)
    if vis is None:
        return None
    spec_tree, spec_keys = vis
    # the class tree of a spec is the one whose conditions name it; the
    # loadout table's answer stays only for a spec no condition names
    for sid, tid in spec_tree.items():
        specs[sid] = tid
    return {"v": 2, "specs": specs, "spec_keys": spec_keys,
            "subtrees": subtrees, "trees": trees, "entries": entries}


def fetch_visibility(trees: dict, node_tree: dict):
    """Who sees which node and which nodes are free, written INTO `trees`
    ("vis", "grant"); returns ({chrSpecId: treeId}, {chrSpecId: "Class|Spec"})
    or None on any failed table (the caller then keeps the previous cache).

    The spec key is the class and spec names with spaces removed, which is how
    Warcraft Logs spells them ("DemonHunter|Devourer", "Hunter|BeastMastery").
    """
    tables = {}
    for t in ("TraitCond", "TraitNodeXTraitCond", "TraitNodeGroupXTraitCond",
              "TraitNodeGroupXTraitNode", "SpecSetMember",
              "ChrSpecialization", "ChrClasses"):
        rows = fn._get_csv(t)
        if rows is fn._FAILED:
            return None
        tables[t] = rows
    cond = {r["ID"]: r for r in tables["TraitCond"] if r.get("ID")}
    node_conds: dict[int, list] = {}
    for r in tables["TraitNodeXTraitCond"]:
        try:
            nid = int(r["TraitNodeID"])
        except (KeyError, ValueError):
            continue
        if nid in node_tree and r.get("TraitCondID") in cond:
            node_conds.setdefault(nid, []).append(cond[r["TraitCondID"]])
    group_conds: dict[str, list] = {}
    for r in tables["TraitNodeGroupXTraitCond"]:
        if r.get("TraitCondID") in cond:
            group_conds.setdefault(r.get("TraitNodeGroupID") or "", []
                                   ).append(cond[r["TraitCondID"]])
    for r in tables["TraitNodeGroupXTraitNode"]:
        try:
            nid = int(r["TraitNodeID"])
        except (KeyError, ValueError):
            continue
        if nid in node_tree:
            node_conds.setdefault(nid, []).extend(
                group_conds.get(r.get("TraitNodeGroupID") or "", []))
    spec_set: dict[str, set] = {}
    for r in tables["SpecSetMember"]:
        try:
            spec_set.setdefault(r["SpecSet"], set()).add(
                int(r["ChrSpecializationID"]))
        except (KeyError, ValueError):
            continue
    class_name = {r["ID"]: (fn._field(r, "Name_lang") or "").replace(" ", "")
                  for r in tables["ChrClasses"] if r.get("ID")}
    spec_class: dict[int, str] = {}
    spec_keys: dict[str, str] = {}
    for r in tables["ChrSpecialization"]:
        cid, nm = r.get("ClassID") or "0", fn._field(r, "Name_lang") or ""
        if not r.get("ID") or cid == "0" or not class_name.get(cid) or not nm:
            continue                       # pet specs, initial "no spec" rows
        spec_class[int(r["ID"])] = cid
        spec_keys[r["ID"]] = f"{class_name[cid]}|{nm.replace(' ', '')}"

    def named(c) -> set:
        return spec_set.get(c.get("SpecSetID") or "", set())

    # The class tree of a spec = the tree whose spec-set conditions name it,
    # and, where two do, the one that holds the spec's HERO-TREE SELECTOR
    # (TraitNode.Type 3). Counting conditions alone is not enough: the game
    # data still carries a second, older Warrior tree (880: 260 nodes, every
    # one of them spec-gated, no hero subtree at all) that out-votes the live
    # one (850). A tree's specs = every spec of the class it serves (a spec
    # no node singles out still sees the whole class side of the tree).
    votes: dict[str, dict] = {}
    for nid, cs in node_conds.items():
        tid = node_tree[nid]
        v = votes.setdefault(tid, {})
        selector = trees[tid]["nodes"][str(nid)][2] == 3
        for c in cs:
            if c.get("CondType") in ("0", "1"):
                for sid in named(c):
                    if sid in spec_class:
                        n, has = v.get(sid, (0, False))
                        v[sid] = (n + 1, has or selector)
    spec_tree: dict[str, int] = {}
    best: dict[int, tuple] = {}
    for tid, v in sorted(votes.items(), key=lambda kv: int(kv[0])):
        for sid, (n, has) in v.items():
            if (has, n) > best.get(sid, ((False, 0), ""))[0]:
                best[sid] = ((has, n), tid)
    tree_specs: dict[str, set] = {}
    for sid, (_n, tid) in best.items():
        spec_tree[str(sid)] = int(tid)
        tree_specs.setdefault(tid, set())
    for tid in tree_specs:
        # the specs conditions actually name: the level-1 "Initial" spec of
        # a class is a ChrSpecialization row too, and is not a talent spec
        tree_specs[tid] = {s for s, (_n, t) in best.items() if t == tid}

    for tid, t in trees.items():
        mine = tree_specs.get(tid, set())
        vis: dict[str, list] = {}
        grant: dict[str, list] = {}
        for n in t["nodes"]:
            cs = node_conds.get(int(n), [])
            gate = [c for c in cs if c.get("CondType") in ("0", "1")
                    and (c.get("SpecSetID") or "0") != "0"]
            if gate and mine:
                seen = set()
                for c in gate:
                    seen |= named(c) & mine
                if seen != mine:
                    vis[n] = sorted(seen)
            free = [c for c in cs if c.get("CondType") == "2"]
            if free:
                who: set = set()
                everyone = False
                for c in free:
                    if (c.get("SpecSetID") or "0") == "0":
                        everyone = True
                    else:
                        who |= named(c) & mine if mine else named(c)
                grant[n] = [] if everyone else sorted(who)
        t["vis"], t["grant"] = vis, grant
    return spec_tree, spec_keys


def fetch_spell(sid: int):
    """(name, icon) for a spell off the nether tooltip, or _FAILED."""
    raw = fn._get_raw(NETHER_SPELL.format(sid=sid))
    if raw is fn._FAILED:
        return fn._FAILED
    try:
        obj = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return None, None
    name = obj.get("name")
    icon = obj.get("icon")
    if isinstance(icon, str):
        icon = icon.strip().lower()
        if not fn.ICON_NAME_RE.fullmatch(icon):
            icon = None
    else:
        icon = None
    return (name if isinstance(name, str) and name else None), icon


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0,
                    help="max per-id fetches this run (0 = unlimited)")
    ap.add_argument("--refresh", action="store_true",
                    help="re-fetch the geometry tables even when cached")
    args = ap.parse_args(argv)

    geo = fn.load_json(TRAIT_GEOMETRY, {})
    spells_c = fn.load_json(NAMES_SPELLS, {})
    budget = [args.limit if args.limit > 0 else float("inf")]

    def spend() -> bool:
        if budget[0] <= 0:
            return False
        budget[0] -= 1
        return True

    got = {"spells": 0, "images": 0, "failed": 0}
    if args.refresh or not geo.get("trees"):
        fresh = fetch_geometry()
        if fresh is None:
            got["failed"] += 1
            print("[traits] geometry refresh failed; keeping the cached copy")
        else:
            # replace refreshed keys, never drop ones a partial cache holds
            for k, v in fresh.items():
                if isinstance(v, dict) and isinstance(geo.get(k), dict):
                    geo[k].update(v)
                else:
                    geo[k] = v
            print(f"[traits] geometry: {len(geo.get('trees', {}))} trees, "
                  f"{sum(len(t['nodes']) for t in geo['trees'].values())} "
                  f"nodes, {len(geo.get('entries', {}))} entries")

    want = sorted({e[2] for e in geo.get("entries", {}).values()
                   if isinstance(e, list) and len(e) > 2 and e[2]})
    for sid in fn.unseen(want, spells_c):
        if not spend():
            break
        res = fetch_spell(sid)
        if res is fn._FAILED:
            got["failed"] += 1
            continue
        name, icon = res
        spells_c[str(sid)] = {"n": name, "ic": icon}
        got["spells"] += 1
    fn.ICONS_DIR.mkdir(parents=True, exist_ok=True)
    for ic in sorted({v.get("ic") for v in spells_c.values()
                      if isinstance(v, dict) and v.get("ic")}):
        dest = fn.ICONS_DIR / f"{ic}.jpg"
        if dest.exists():
            continue                    # shared store, downloaded once ever
        if not spend():
            break
        raw = fn._get_raw(fn.ZAM_ICON.format(icon=ic))
        if raw is fn._FAILED or not raw:
            got["failed"] += 1
            continue
        dest.write_bytes(raw)
        got["images"] += 1

    fn.save_cache(NAMES_SPELLS, spells_c)
    TRAIT_GEOMETRY.parent.mkdir(parents=True, exist_ok=True)
    tmp = TRAIT_GEOMETRY.with_name(TRAIT_GEOMETRY.name + ".tmp")
    tmp.write_text(json.dumps(geo, ensure_ascii=False, sort_keys=True,
                              indent=1) + "\n")
    tmp.replace(TRAIT_GEOMETRY)
    print(f"[traits] spells {got['spells']} fetched ({len(spells_c)} "
          f"cached) | icon images {got['images']} fetched | "
          f"{got['failed']} failures (retried next run)", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                       # noqa: BLE001
        print(f"[traits] aborted without harm: {e}", flush=True)
        sys.exit(0)
