#!/usr/bin/env python3
"""Trait-tree geometry and spell names for the in-game-style talent renderer.

Companion to fetch_names.py, run in the same collector slot (between
fetch_data and build_site_data; order relative to fetch_names is free).
Two caches, both committed:

  data/trait_geometry.json  {"specs":    {"<chrSpecId>": treeId},
                             "subtrees": {"<subtreeId>": "Templar", ...},
                             "trees":    {"<treeId>": {
                                 "nodes": {"<nodeId>": [x, y, type, subtree]},
                                 "edges": [[a, b], ...]}},
                             "entries":  {"<entryId>":
                                 [nodeId, maxRanks, spellId, overrideName]}}
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
        entries[eid] = [int(nid), int(e.get("MaxRanks") or 1),
                        int(spell) if spell and str(spell).isdigit() else 0,
                        override]
    return {"specs": specs, "subtrees": subtrees,
            "trees": trees, "entries": entries}


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
