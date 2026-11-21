#!/usr/bin/env python3
"""Collect the test vectors of test_talent_string.py: talent loadouts the GAME
exported, with the same build as a Warcraft Logs talent tree would log it.

Raider.IO's public profile API (fields=talents) returns, for a character, the
import string the game produced (talentLoadout.loadout_text) beside the
decoded nodes. This asks for a few recently logged characters per spec, taken
from the committed run export, and writes

  scripts/fixtures/talent_strings.json
      {"about", "build": the game build of the geometry cache,
       "trees":   {"<treeId>": talent_string.tree_stamp at collection time},
       "vectors": [{"k": "Rogue|Assassination", "sid": 259,
                    "s": "<the game's string>",
                    "t": [[entryId, rank], ...]}]}

NO CHARACTER IDENTITY IS KEPT: a name is used for one request and nothing of
it is written. Run by hand after a talent patch, once data/trait_geometry.json
holds the new trees; not part of the collector, never run by a workflow.

  python3 scripts/collect_talent_vectors.py [--per-spec 4] [--max-requests 600]
"""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import json
import pathlib
import sys
import time

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fetch_names as fn                       # noqa: E402
import fetch_rio as fr                         # noqa: E402
import talent_string as ts                     # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent / "fixtures" / "talent_strings.json"
GEOMETRY = fn.DATA / "trait_geometry.json"


def as_logged(tree: ts.SpecTree, loadout: list) -> list:
    """The armory's decoded nodes as [entry id, rank] pairs. The armory gives
    a tiered node as one node with a total; a log gives one entry per tier."""
    pairs = []
    for x in loadout:
        node = x["node"]
        if node["type"] == 1:
            left = x["rank"]
            for eid in tree.options[node["id"]]:
                take = min(left, tree.max_of[eid])
                if take > 0:
                    pairs.append([eid, take])
                    left -= take
        else:
            pairs.append([node["entries"][x["entryIndex"]]["id"], x["rank"]])
    return sorted(pairs)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--per-spec", type=int, default=4)
    ap.add_argument("--max-requests", type=int, default=600)
    args = ap.parse_args(argv)
    geo = json.loads(GEOMETRY.read_text())
    if geo.get("v") != 2 or not geo.get("build"):
        print("the geometry cache has no game tables; run fetch_traits.py --refresh")
        return 1

    seen: dict[tuple, str] = {}
    with gzip.open(fr.RUNS_CSV, "rt", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            k = (r["class"], r["spec"], r["region"], r["server"], r["character"])
            if r["started_at"] > seen.get(k, ""):
                seen[k] = r["started_at"]
    by_spec = collections.defaultdict(list)
    for k, newest in seen.items():
        by_spec[(k[0], k[1])].append((newest, k))

    session = requests.Session()
    vectors, tried, short = [], 0, {}
    for sk in sorted(by_spec):
        got, per_region = 0, collections.Counter()
        # the most recently logged first: their armory build is the likeliest
        # to be a current one; no more than half from one region
        for _newest, k in sorted(by_spec[sk], reverse=True):
            if got >= args.per_spec or tried >= args.max_requests:
                break
            region = fr.REGION.get(k[2])
            if (not region or not k[3] or not k[4]
                    or per_region[region] >= max(2, args.per_spec // 2)):
                continue
            for realm in (k[3], fr.slug(k[3])):
                tried += 1
                try:
                    r = session.get(fr.API, timeout=30, headers=fn.HEADERS,
                                    params={"region": region, "realm": realm,
                                            "name": k[4], "fields": "talents"})
                except requests.RequestException:
                    time.sleep(1)
                    continue
                time.sleep(0.35)
                if r.status_code != 200:
                    continue
                d = r.json()
                tl = d.get("talentLoadout") or {}
                if not tl.get("loadout_text") or not tl.get("loadout"):
                    continue
                now = ((d.get("class") or "").replace(" ", ""),
                       (d.get("active_spec_name") or "").replace(" ", ""))
                if now != sk:
                    break                  # the armory holds another spec now
                tree = ts.SpecTree.of(geo, tl["loadout_spec_id"])
                if tree is None:
                    break
                vectors.append({"k": f"{sk[0]}|{sk[1]}",
                                "sid": tl["loadout_spec_id"],
                                "s": tl["loadout_text"],
                                "t": as_logged(tree, tl["loadout"])})
                got += 1
                per_region[region] += 1
                break
        if got < args.per_spec:
            short[f"{sk[0]}|{sk[1]}"] = got

    trees = {str(geo["specs"][str(v["sid"])]) for v in vectors}
    doc = {"about": "Armory talent loadouts (Raider.IO public profile API, "
                    "talentLoadout): s = the import string the game produced, "
                    "t = the same build as [entry id, rank] pairs, the shape "
                    "of a Warcraft Logs talent tree. No character identity is "
                    "kept. trees = talent_string.tree_stamp of each class "
                    "tree when the vectors were collected; "
                    "test_talent_string.py holds a vector only against the "
                    "tree it was taken from.",
           "collected": time.strftime("%Y-%m-%d", time.gmtime()),
           "build": geo["build"],
           "trees": {t: ts.tree_stamp(geo, t) for t in sorted(trees, key=int)},
           "vectors": vectors}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, separators=(",", ":"),
                              ensure_ascii=False) + "\n")
    same = sum(1 for v in vectors
               if ts.loadout_string(ts.SpecTree.of(geo, v["sid"]), v["t"]) == v["s"])
    print(f"{tried} requests | {len(vectors)} vectors over "
          f"{len({v['k'] for v in vectors})} specs | {same} reproduced "
          f"character for character | short of {args.per_spec}: {short or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
