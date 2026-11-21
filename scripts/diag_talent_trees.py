#!/usr/bin/env python3
"""How does the journal record a talent tree, measured against the game's own
trait tables? Read-only; run through diagnose.yml (script: diag_talent_trees).
No Warcraft Logs request is made: the journal is read from the restored cache
and the trait tables from wago.tools (fetch_traits.fetch_geometry, in memory,
nothing is written).

Owner, 2026-11-21: the Devourer talent build is drawn wider than other specs,
and a build should be exportable as the game's import string. Both need facts
the page's payload cannot give, because the builder collapses each record to
one (rank, option) per node:

  A. how many records carry talents the game does not show their labelled
     spec (the source of the foreign nodes in six specs' trees), and whether
     such a record is another spec's whole tree or a mixture;
  B. how a TIERED node (TraitNode.Type 1, three entries) is logged: one entry
     with the total rank, or one entry per tier;
  C. whether the nodes the game grants for free are in the logged tree;
  D. what the purchased points add up to per tree section once granted nodes
     are set aside (a constant per section says the granted table is right);
  E. the hero-tree selector (Type 3): its rank, and whether the option logged
     points at the hero tree whose nodes the record holds;
  F. choice nodes logged with two options, ranks above the node's maximum,
     entry ids the trait tables do not know.
"""
from __future__ import annotations

import collections
import gzip
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fetch_traits as ft                                   # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
JOURNAL = ROOT / "data" / "processed" / "gear.jsonl"
EXPORT = ROOT / "data" / "gear.jsonl.gz"


def main() -> int:
    geo = ft.fetch_geometry(ft.live_build())   # the live build, never the tables' newest
    if geo is None:
        print("[diag] the trait tables could not be fetched; nothing measured")
        return 0
    entries = geo["entries"]
    node_tree = {int(n): t for t, tg in geo["trees"].items() for n in tg["nodes"]}
    key_sid = {k: int(s) for s, k in geo["spec_keys"].items() if s in geo["specs"]}
    node_entries: dict[int, list] = collections.defaultdict(list)
    for eid, e in entries.items():
        node_entries[int(e[0])].append((int(e[4]), int(eid)))
    for v in node_entries.values():
        v.sort()
    print(f"[diag] trait tables: {len(geo['trees'])} trees, {len(node_tree)} nodes, "
          f"{len(entries)} entries, {len(key_sid)} talent specs")

    def info(nid):
        return geo["trees"][node_tree[nid]]["nodes"][str(nid)]   # [x, y, type, subtree]

    def sees(nid, sid):
        v = geo["trees"][node_tree[nid]].get("vis", {}).get(str(nid))
        return v is None or sid in v

    def granted(nid, sid):
        g = geo["trees"][node_tree[nid]].get("grant", {}).get(str(nid))
        return g is not None and (not g or sid in g)

    # class side / spec side of each tree: the largest x gap between plain nodes
    cut = {}
    for t, tg in geo["trees"].items():
        xs = sorted({g[0] for g in tg["nodes"].values() if not g[3] and g[2] != 3})
        gaps = [(xs[i + 1] - xs[i], xs[i + 1]) for i in range(len(xs) - 1)]
        cut[t] = max(gaps)[1] if gaps and max(gaps)[0] >= 900 else None

    src = JOURNAL if JOURNAL.exists() else EXPORT
    if not src.exists():
        print("[diag] no gear journal restored; nothing measured")
        return 0
    opener = gzip.open if src.suffix == ".gz" else open
    n_rec = n_tree = 0
    per_spec = collections.Counter()
    unknown_entries = collections.Counter()
    foreign_recs = collections.Counter()          # label -> records with a foreign node
    foreign_pair = collections.Counter()          # (label, whose nodes) -> records
    foreign_kind = collections.Counter()          # (label, "whole"|"mixture")
    foreign_share = collections.defaultdict(list) # label -> foreign node counts
    samples = collections.defaultdict(list)
    tier_pat = collections.defaultdict(collections.Counter)
    tier_shape = collections.Counter()
    grant_seen = collections.defaultdict(collections.Counter)   # key -> node -> records holding it
    grant_rank = collections.Counter()
    pts = collections.defaultdict(collections.Counter)
    sel3 = collections.Counter()
    sel3_match = collections.Counter()
    two_options = collections.Counter()
    over_rank = collections.Counter()
    dup_plain = collections.Counter()
    with opener(src, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            n_rec += 1
            tal = rec.get("talents")
            tree = tal.get("tree") if isinstance(tal, dict) else None
            if not isinstance(tree, list) or not tree:
                continue
            key = f"{rec.get('class')}|{rec.get('spec')}"
            sid = key_sid.get(key)
            if sid is None:
                per_spec[key + " (no such talent spec)"] += 1
                continue
            n_tree += 1
            per_spec[key] += 1
            by_node: dict[int, list] = collections.defaultdict(list)
            for x in tree:
                eid = str(x.get("id"))
                e = entries.get(eid)
                if not e:
                    unknown_entries[eid] += 1
                    continue
                by_node[int(e[0])].append((int(eid), int(x.get("rank") or 0)))
            tid = geo["specs"].get(str(sid))
            # --- A. foreign nodes
            plain = [n for n in by_node if not info(n)[3] and info(n)[2] != 3]
            foreign = [n for n in plain if not sees(n, sid)]
            if foreign:
                foreign_recs[key] += 1
                foreign_share[key].append(len(foreign))
                own = [n for n in plain
                       if geo["trees"][node_tree[n]].get("vis", {}).get(str(n)) == [sid]]
                foreign_kind[(key, "mixture" if own else "whole")] += 1
                whose = collections.Counter()
                for n in foreign:
                    for s in geo["trees"][node_tree[n]].get("vis", {}).get(str(n), []):
                        whose[geo["spec_keys"].get(str(s), s)] += 1
                foreign_pair[(key, whose.most_common(1)[0][0] if whose else "?")] += 1
                if len(samples[key]) < 4:
                    samples[key].append((rec.get("report_code"), rec.get("fight_id"),
                                         rec.get("character"), len(foreign), len(plain)))
                continue                     # the rest is measured on clean records
            sec = collections.Counter()
            hero_sub = collections.Counter()
            for n, es in by_node.items():
                x, _y, typ, sub = info(n)
                total = sum(r for _e, r in es)
                mx = {eid: int(entries[str(eid)][1]) for _i, eid in node_entries[n]}
                # --- F. sanity
                for eid, r in es:
                    if r > mx.get(eid, 99):
                        over_rank[typ] += 1
                if typ == 1:                 # --- B. tiered
                    order = {eid: i for i, (_ix, eid) in enumerate(node_entries[n])}
                    pat = tuple(sorted((order.get(eid, -1), r) for eid, r in es))
                    tier_pat[n][pat] += 1
                    tier_shape[(len(es), total)] += 1
                elif typ == 2 and len(es) > 1:
                    two_options[n] += 1
                elif typ == 0 and len(es) > 1:
                    dup_plain[n] += 1
                if typ == 3:                 # --- E. the hero selector
                    for eid, r in es:
                        sel3[(len(es), r)] += 1
                    hero_sub["sel:" + str(entries[str(es[0][0])][5])] += 1
                    continue
                if sub:
                    hero_sub[str(sub)] += 1
                g = granted(n, sid)
                if g:                        # --- C. granted nodes present?
                    grant_seen[key][n] += 1
                    grant_rank[total] += 1
                    continue
                side = "hero" if sub else ("class" if cut.get(node_tree[n]) is None
                                           or x < cut[node_tree[n]] else "spec")
                sec[side] += total
            pts[key][(sec["class"], sec["spec"], sec["hero"])] += 1
            subs = [k for k in hero_sub if not k.startswith("sel:")]
            picked = [k[4:] for k in hero_sub if k.startswith("sel:")]
            if picked:
                sel3_match[("one hero tree" if len(subs) == 1 else f"{len(subs)} hero trees",
                            "selector points at it" if subs and picked[0] == subs[0]
                            else "selector points elsewhere")] += 1
            else:
                sel3_match[("no selector logged", f"{len(subs)} hero trees")] += 1

    print(f"[diag] journal {src.name}: {n_rec:,} records, {n_tree:,} with a talent tree "
          f"and a known talent spec")
    odd = {k: v for k, v in per_spec.items() if k.endswith(")")}
    if odd:
        print(f"[diag] labels that are not a talent spec: {dict(odd)}")
    print(f"[diag] entry ids the trait tables do not know: {sum(unknown_entries.values()):,} "
          f"occurrences of {len(unknown_entries)} ids {unknown_entries.most_common(5)}")

    print("\n== A. records holding talents the game does not show their labelled spec")
    tot = sum(foreign_recs.values())
    print(f"   {tot:,} of {n_tree:,} records ({100 * tot / max(1, n_tree):.3f}%)")
    for key, n in foreign_recs.most_common(40):
        fs = sorted(foreign_share[key])
        pairs = [f"{w} x{c}" for (k, w), c in foreign_pair.most_common() if k == key][:3]
        print(f"   {key:26s} {n:5d} of {per_spec[key]:6d} ({100 * n / per_spec[key]:.2f}%) | "
              f"whole foreign tree {foreign_kind[(key, 'whole')]}, mixture {foreign_kind[(key, 'mixture')]} | "
              f"foreign nodes per record min {fs[0]} median {fs[len(fs) // 2]} max {fs[-1]} | {', '.join(pairs)}")
    for key in ("DemonHunter|Devourer", "Hunter|BeastMastery"):
        for s in samples.get(key, []):
            print(f"   sample {key}: report {s[0]} fight {s[1]} {s[2]}: {s[3]} of {s[4]} class+spec nodes foreign")

    print("\n== B. tiered nodes (Type 1): (option position, rank) logged per record")
    print(f"   (entries logged, total rank) over all tiered nodes: {dict(tier_shape.most_common(12))}")
    for n in sorted(tier_pat, key=lambda n: -sum(tier_pat[n].values()))[:8]:
        mr = [(i, int(entries[str(eid)][1])) for i, (_ix, eid) in enumerate(node_entries[n])]
        print(f"   node {n} options (position, max ranks) {mr}: {tier_pat[n].most_common(5)}")

    print("\n== C. nodes the game grants for free: are they in the logged tree?")
    print(f"   total rank logged on a granted node: {dict(grant_rank.most_common(6))}")
    low = []
    for key in sorted(grant_seen):
        sid = key_sid[key]; tid = str(geo["specs"][str(sid)]); tg = geo["trees"][tid]
        mine = [int(n) for n in tg.get("grant", {}) if granted(int(n), sid) and sees(int(n), sid)]
        clean = per_spec[key] - foreign_recs[key]
        shares = {n: grant_seen[key][n] / max(1, clean) for n in mine}
        if shares:
            lo = min(shares.values())
            low.append((lo, key, len(mine), {n: round(s, 3) for n, s in shares.items() if s < 0.98},
                        {n: [info(n)[2], info(n)[3]] for n in mine if shares[n] < 0.98}))
    for lo, key, n, bad, typ in sorted(low)[:45]:
        print(f"   {key:26s} {n} granted nodes, lowest presence {lo:.3f}" + (f" | under 98%: {bad} [type, subtree] {typ}" if bad else ""))

    print("\n== D. purchased points per section (granted nodes set aside): (class, spec, hero) -> records")
    glob = collections.Counter()
    for key in sorted(pts):
        top = pts[key].most_common(3)
        allc = sum(pts[key].values())
        glob.update(pts[key])
        print(f"   {key:26s} {', '.join(f'{k} {100 * c / allc:.1f}%' for k, c in top)}")
    print(f"   all specs: {[(k, c) for k, c in glob.most_common(8)]}")

    print("\n== E. the hero-tree selector (Type 3)")
    print(f"   (options logged, rank): {dict(sel3.most_common(6))}")
    print(f"   {dict(sel3_match.most_common(8))}")

    print("\n== F. sanity")
    print(f"   choice nodes logged with two options: {sum(two_options.values()):,} records over {len(two_options)} nodes")
    print(f"   plain nodes logged with two entries: {sum(dup_plain.values()):,} records over {len(dup_plain)} nodes {dup_plain.most_common(4)}")
    print(f"   entries logged above their maximum rank, by node type: {dict(over_rank)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
