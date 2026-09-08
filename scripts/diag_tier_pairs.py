#!/usr/bin/env python3
"""Diagnostic v2: what partitions the wearers of one tier item?

v1 showed that set items carry NO ItemBonus type-2 stat rows, yet each
tier item shows 600-1,150 distinct bonus tuples -- more than tracks x
sockets x tertiaries. Something in the bonus list partitions wearers, and
this run finds out what:

  * the whole ItemBonus table is fetched once and every id seen on a set
    item is decoded (type + values), with per-id wear counts;
  * for the top 3 tier items: the frequency of every bonus id, and the
    partition induced by the ids NOT explained as ilvl/track/socket/
    tertiary/quality/display -- how many groups, how big;
  * co-occurrence: for the top item, which id families are mutually
    exclusive (one per wear, like a stat template would be).

Read-only. Output stays under ~200 lines.
"""
import collections
import csv
import gzip
import io
import json
import pathlib
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
JOURNAL = ROOT / "data" / "processed" / "gear.jsonl"
EXPORT = ROOT / "data" / "gear.jsonl.gz"
NAMES = ROOT / "data" / "names_items.json"
UA = {"User-Agent": "wowlogs-diagnostic/2.0"}
TYPES = {1: "stat(legacy)", 2: "stat alloc", 3: "quality", 4: "name suffix",
         5: "name desc", 6: "scaling", 7: "socket", 11: "ilvl offset",
         13: "display", 14: "ilvl abs", 35: "limit cat"}


def item_bonus() -> dict[int, list[tuple]]:
    raw = urllib.request.urlopen(
        urllib.request.Request("https://wago.tools/db2/ItemBonus/csv", headers=UA),
        timeout=180).read().decode("utf-8", "replace")
    out: dict[int, list[tuple]] = collections.defaultdict(list)
    for r in csv.DictReader(io.StringIO(raw)):
        out[int(r["ParentItemBonusListID"])].append(
            (int(r["Type"]), r["Value_0"], r["Value_1"], r["Value_2"], r["Value_3"]))
    return out


def describe(bid: int, table) -> str:
    rows = table.get(bid)
    if not rows:
        return "not in ItemBonus"
    return "; ".join(f"T{t}{'('+TYPES[t]+')' if t in TYPES else ''} "
                     f"v={v0},{v1},{v2},{v3}" for t, v0, v1, v2, v3 in rows)


def main() -> None:
    src = JOURNAL if JOURNAL.exists() else EXPORT
    if not src.exists():
        sys.exit(f"no gear journal at {JOURNAL} or {EXPORT}")
    t0 = time.time()
    table = item_bonus()
    print(f"[diag] ItemBonus: {len(table):,} bonus lists ({time.time() - t0:.1f}s)")
    names = json.loads(NAMES.read_text()) if NAMES.exists() else {}
    opener = gzip.open if src.suffix == ".gz" else open
    wears_by_item = collections.Counter()
    id_wears = collections.Counter()                     # bonus id -> set-item wears
    per_item = {}                                        # iid -> {"ids": Counter, "tuples": Counter}
    with opener(src, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            gear = rec.get("gear")
            if not isinstance(gear, list):
                continue
            for it in gear:
                if not isinstance(it, dict) or not it.get("id") or not it.get("set"):
                    continue
                iid = int(it["id"])
                wears_by_item[iid] += 1
                bonus = tuple(int(b) for b in (it.get("bonus") or []))
                d = per_item.setdefault(iid, {"ids": collections.Counter(),
                                              "tuples": collections.Counter(),
                                              "ilvl": collections.Counter()})
                d["tuples"][tuple(sorted(bonus))] += 1
                if it.get("ilvl"):
                    d["ilvl"][int(it["ilvl"])] += 1
                for b in bonus:
                    id_wears[b] += 1
                    d["ids"][b] += 1
    print(f"[diag] {sum(wears_by_item.values()):,} set-item wears over "
          f"{len(wears_by_item)} items in {time.time() - t0:.0f}s")
    print("[diag] every bonus id seen on set items (id | wears | decode):")
    for b, c in id_wears.most_common(60):
        print(f"  {b:>6} | {c:>9,} | {describe(b, table)}")
    top = [iid for iid, _ in wears_by_item.most_common(3)]
    for iid in top:
        d = per_item[iid]
        nm = (names.get(str(iid)) or {}).get("n") or "?"
        n = wears_by_item[iid]
        print(f"\n[diag] {iid} {nm}: {n:,} wears, {len(d['tuples'])} distinct tuples, "
              f"ilvls {sorted(d['ilvl'])[:3]}..{sorted(d['ilvl'])[-3:]}")
        print("  per-id share:", ", ".join(f"{b}:{100 * c / n:.0f}%"
                                          for b, c in d["ids"].most_common(24)))
        # ids of unknown meaning: not in the table, or types outside the
        # explained set; the partition they induce is the candidate identity
        known_types = {3, 6, 7, 11, 13, 14, 35}
        unexplained = [b for b in d["ids"]
                       if not table.get(b) or any(t not in known_types
                                                  for t, *_ in table[b])]
        groups = collections.Counter()
        for tup, c in d["tuples"].items():
            groups[tuple(sorted(b for b in tup if b in unexplained))] += c
        print(f"  unexplained ids: {len(unexplained)} -> {len(groups)} groups by their "
              f"combination; top: "
              + ", ".join(f"{list(g)}={100 * c / n:.0f}%" for g, c in groups.most_common(8)))
        # mutual exclusivity among the most frequent unexplained ids
        freq = [b for b, _ in d["ids"].most_common(40) if b in unexplained][:10]
        excl = []
        for i, a in enumerate(freq):
            for b2 in freq[i + 1:]:
                both = sum(c for tup, c in d["tuples"].items() if a in tup and b2 in tup)
                if both == 0:
                    excl.append(f"{a}x{b2}")
        print(f"  mutually exclusive pairs among top unexplained ids: {excl[:24]}")


if __name__ == "__main__":
    main()
