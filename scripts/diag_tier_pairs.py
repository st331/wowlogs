#!/usr/bin/env python3
"""Diagnostic: can set (tier) items in the gear journal be split by their
inherited secondary-stat PAIR?

12.1's Catalyst keeps the source piece's secondary stats. The game encodes
secondary stats as ItemBonus TYPE-2 rows (stat id, allocation) hung off bonus
ids, and the collector keeps every item's bonus ids (compact_gear -> "bonus").
This script streams the journal, decodes each SET item's bonus list against
the live wago.tools ItemBonus table, and prints a compact report:

  * coverage: share of set-item wears whose bonus list decodes to exactly
    two secondaries (a pair), one, none, or three+;
  * per item id (top 40 by wears): name, wears, distinct bonus tuples,
    pair histogram;
  * a few raw bonus tuples for the top three items, so the encoding is
    visible rather than inferred;
  * a sanity count of crafted items (no `set`), which must not be touched.

Read-only; no journal is written. Output stays short on purpose: the job-log
API returns at most 5,000 lines and the interesting part is the last 150.
"""
import collections
import csv
import gzip
import io
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
JOURNAL = ROOT / "data" / "processed" / "gear.jsonl"
EXPORT = ROOT / "data" / "gear.jsonl.gz"
NAMES = ROOT / "data" / "names_items.json"
CRAFTED = ROOT / "data" / "crafted_ids.json"
SEC = {32: "Crit", 36: "Haste", 40: "Vers", 49: "Mastery"}
TER = {61: "Speed", 62: "Leech", 63: "Avoid"}
UA = {"User-Agent": "wowlogs-diagnostic/1.0"}


def stat_table() -> dict[int, list[tuple[int, int]]]:
    """bonus list id -> [(stat id, allocation)] from ItemBonus type-2 rows."""
    url = ("https://wago.tools/db2/ItemBonus/csv?"
           + urllib.parse.urlencode({"filter[Type]": "2"}))
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                 timeout=120).read().decode("utf-8", "replace")
    out: dict[int, list[tuple[int, int]]] = collections.defaultdict(list)
    for r in csv.DictReader(io.StringIO(raw)):
        if r.get("Type") != "2":
            continue
        out[int(r["ParentItemBonusListID"])].append(
            (int(r["Value_0"]), int(r["Value_1"])))
    return out


def decode(bonus, table) -> tuple[str, ...] | None:
    """Sorted secondary-stat names a bonus list confers, or None if none."""
    stats: dict[int, int] = {}
    for b in bonus or []:
        for sid, alloc in table.get(int(b), ()):
            if sid in SEC:
                stats[sid] = stats.get(sid, 0) + alloc
    return tuple(sorted(SEC[s] for s in stats)) if stats else None


def main() -> None:
    src = JOURNAL if JOURNAL.exists() else EXPORT
    if not src.exists():
        sys.exit(f"no gear journal at {JOURNAL} or {EXPORT}")
    t0 = time.time()
    table = stat_table()
    print(f"[diag] ItemBonus type-2: {len(table):,} bonus lists "
          f"({time.time() - t0:.1f}s)")
    names = json.loads(NAMES.read_text()) if NAMES.exists() else {}
    crafted = set(json.loads(CRAFTED.read_text())) if CRAFTED.exists() else set()
    opener = gzip.open if src.suffix == ".gz" else open
    recs = items = set_items = crafted_items = crafted_with_set = 0
    by_set_item: dict[int, dict] = {}
    cov = collections.Counter()          # 0/1/2/3+ decoded secondaries
    by_setid = collections.Counter()
    with opener(src, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            recs += 1
            gear = rec.get("gear")
            if not isinstance(gear, list):
                continue
            for it in gear:
                if not isinstance(it, dict) or not it.get("id"):
                    continue
                items += 1
                iid = int(it["id"])
                if iid in crafted:
                    crafted_items += 1
                    if it.get("set"):
                        crafted_with_set += 1
                if not it.get("set"):
                    continue
                set_items += 1
                by_setid[str(it.get("set"))] += 1
                pair = decode(it.get("bonus"), table)
                cov[len(pair) if pair else 0] += 1
                d = by_set_item.setdefault(iid, {"n": 0, "tuples": collections.Counter(),
                                                  "pairs": collections.Counter(),
                                                  "ilvl": collections.Counter()})
                d["n"] += 1
                d["tuples"][tuple(it.get("bonus") or [])] += 1
                d["pairs"]["/".join(pair) if pair else "?"] += 1
                if it.get("ilvl"):
                    d["ilvl"][int(it["ilvl"])] += 1
    print(f"[diag] {recs:,} records, {items:,} items, {set_items:,} set-item "
          f"wears, {crafted_items:,} crafted wears ({crafted_with_set} crafted "
          f"items carrying a set id) in {time.time() - t0:.0f}s")
    tot = max(set_items, 1)
    print("[diag] decoded secondaries per set-item wear: "
          + ", ".join(f"{k}: {v:,} ({100 * v / tot:.1f}%)"
                      for k, v in sorted(cov.items())))
    print("[diag] set ids by wears: "
          + ", ".join(f"{s}={c:,}" for s, c in by_setid.most_common(8)))
    top = sorted(by_set_item.items(), key=lambda kv: -kv[1]["n"])[:40]
    print("[diag] top set items -- id | name | wears | distinct bonus tuples | pairs")
    for iid, d in top:
        nm = (names.get(str(iid)) or {}).get("n") or "?"
        pairs = ", ".join(f"{p} {100 * c / d['n']:.0f}%"
                          for p, c in d["pairs"].most_common(6))
        print(f"  {iid} | {nm[:40]:<40} | {d['n']:>6,} | {len(d['tuples']):>4} | {pairs}")
    print("[diag] raw bonus tuples for the top three items (tuple -> wears, decoded):")
    for iid, d in top[:3]:
        nm = (names.get(str(iid)) or {}).get("n") or "?"
        print(f"  {iid} {nm[:40]}")
        for tup, c in d["tuples"].most_common(6):
            pair = decode(list(tup), table)
            print(f"     {list(tup)} -> {c:,} wears, decoded {'/'.join(pair) if pair else '?'}")


if __name__ == "__main__":
    main()
