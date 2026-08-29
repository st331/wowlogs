#!/usr/bin/env python3
"""Resolve item / enchant / embellishment names into committed caches.

The gear journal stores ids only (names are presentation noise at collection
time), but the Builds screen wants "Luminant Verdict's Unwavering Gaze", not
#249961. This script runs BETWEEN fetch_data.py and build_site_data.py: it
scans the journal for ids the caches have never seen and resolves just those
against wago.tools' db2 CSV exports (per-id fetches -- the endpoint's `any:`
multi-id filter verifiably returns nothing), then rewrites the caches sorted
so their diffs stay stable. build_site_data.py never fetches: it reads these
caches only, and a missing cache degrades to all-null names.

Cache files (committed, GROW-ONLY -- merged, never overwritten, so manual
entries survive every run):
  data/names_items.json     {"<itemid>": {"n": "...", "q": 4} | {"n": null}}
  data/names_enchants.json  {"<enchid>": "cleaned name" | null}
  data/crafted_ids.json     sorted int list (CraftingData's CraftedItemIDs)
  data/names_bonus_emb.json {"<bonusid>": "name" | null} -- embellishment
                            marker bonus ids plus the reagent-identity bonus
                            ids seen alongside them; null = asked, unnamed
                            (rendered "#<id>"; nameable manually, once)

Failure contract: a value of null means "asked, the source had no name" and
is never re-asked; an ABSENT key means "not asked yet / fetch failed" and is
retried next run. Any network failure logs one line and the script still
exits 0 -- name resolution must never block a build.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import pathlib
import re
import sys
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
GEAR_FILE = DATA / "processed" / "gear.jsonl"
GEAR_CSV = DATA / "gear.jsonl.gz"
NAMES_ITEMS = DATA / "names_items.json"
NAMES_ENCHANTS = DATA / "names_enchants.json"
CRAFTED_IDS = DATA / "crafted_ids.json"
NAMES_BONUS_EMB = DATA / "names_bonus_emb.json"

WAGO = "https://wago.tools/db2"
HEADERS = {"User-Agent": "wowlogs-collector/1.0"}
SLEEP_S = 0.15
TIMEOUT_S = 30

# "Enchant Helm - Empowered Rune of Avoidance |A:...|a" -> the rune name:
# strip the atlas markup and the "Enchant <slot> - " prefix
ATLAS_RE = re.compile(r"\|A:[^|]*\|a")
ENCH_PREFIX_RE = re.compile(r"^Enchant .*? - ")

_FAILED = object()   # network failure sentinel: retry next run, store nothing


def clean_enchant_name(raw: str | None) -> str | None:
    """Human name from SpellItemEnchantment.Name_lang, or None when empty."""
    if not isinstance(raw, str) or not raw:
        return None
    s = ENCH_PREFIX_RE.sub("", ATLAS_RE.sub("", raw)).strip()
    return s or None


def merge_grow_only(cache: dict, new: dict) -> int:
    """Add keys the cache lacks; NEVER overwrite (manual entries survive)."""
    added = 0
    for k, v in new.items():
        if k not in cache:
            cache[k] = v
            added += 1
    return added


def unseen(ids, cache: dict) -> list[int]:
    """Ids never asked about. A cached null was asked -- not retried."""
    return sorted(i for i in ids if str(i) not in cache)


def _field(row: dict, *names: str) -> str | None:
    """First present column among spelling variants (wago flattens arrays
    as either Value_0 or Value[0] depending on table/export vintage)."""
    for nm in names:
        if nm in row:
            return row[nm]
    return None


def _get_csv(path: str, params: dict | None = None):
    """One wago.tools CSV fetch -> list of row dicts, or _FAILED on any
    error. An empty list is a REAL answer (no such row); _FAILED is not."""
    try:
        r = requests.get(f"{WAGO}/{path}/csv", params=params,
                         headers=HEADERS, timeout=TIMEOUT_S)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        return list(csv.DictReader(io.StringIO(r.text)))
    except Exception as e:                       # noqa: BLE001 -- never block
        print(f"[names] fetch failed: {path} {params or ''} ({e})",
              flush=True)
        return _FAILED
    finally:
        time.sleep(SLEEP_S)


def load_json(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save_cache(path: pathlib.Path, obj) -> None:
    """Sorted, one entry per line -- stable diffs for the weekly commit."""
    if isinstance(obj, dict):
        obj = {k: obj[k] for k in sorted(obj, key=int)}
    else:
        obj = sorted(set(obj))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n")
    tmp.replace(path)


def scan_journal():
    """(item ids, enchant ids, distinct bonus-id tuples) from the journal."""
    src = GEAR_FILE if GEAR_FILE.exists() else GEAR_CSV
    items: set[int] = set()
    enchs: set[int] = set()
    bonus: set[tuple] = set()
    if not src.exists():
        print("[names] no gear journal found; table refreshes only")
        return items, enchs, bonus
    opener = gzip.open if src.suffix == ".gz" else open
    with opener(src, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue                       # tolerate a torn trailing line
            gear = rec.get("gear")
            if not isinstance(gear, list):
                continue
            for it in gear:
                if not isinstance(it, dict):
                    continue
                if isinstance(it.get("id"), int) and it["id"]:
                    items.add(it["id"])
                if isinstance(it.get("ench"), int) and it["ench"]:
                    enchs.add(it["ench"])
                b = it.get("bonus")
                if isinstance(b, list) and b:
                    bonus.add(tuple(x for x in b if isinstance(x, int)))
    return items, enchs, bonus


def fetch_item(iid: int):
    rows = _get_csv("ItemSparse", {"filter[ID]": f"exact:{iid}"})
    if rows is _FAILED:
        return _FAILED
    if not rows:
        return {"n": None}
    nm = _field(rows[0], "Display_lang") or None
    out: dict = {"n": nm}
    q = _field(rows[0], "OverallQualityID", "Quality")
    if q is not None and str(q).isdigit():
        out["q"] = int(q)
    return out


def fetch_enchant(eid: int):
    rows = _get_csv("SpellItemEnchantment", {"filter[ID]": f"exact:{eid}"})
    if rows is _FAILED:
        return _FAILED
    if not rows:
        return None
    return clean_enchant_name(_field(rows[0], "Name_lang"))


def fetch_crafted() -> set[int] | object:
    """Every CraftedItemID in CraftingData -- the crafted-item universe."""
    rows = _get_csv("CraftingData")
    if rows is _FAILED:
        return _FAILED
    out = set()
    for r in rows:
        v = _field(r, "CraftedItemID")
        if v and str(v).isdigit() and int(v) > 0:
            out.add(int(v))
    return out


def fetch_markers() -> dict[int, None] | object:
    """Embellishment marker bonus ids: ItemBonus rows of Type=35 whose
    ItemLimitCategory's name contains "Embellished" (512 Embellished,
    697 Outdoor Embellished today). Stored with null names -- the marker is
    generic; identity comes from the reagent bonus resolved separately."""
    cats = _get_csv("ItemLimitCategory")
    if cats is _FAILED:
        return _FAILED
    emb_cats = {str(r.get("ID")) for r in cats
                if "embellished" in (_field(r, "Name_lang") or "").lower()}
    if not emb_cats:
        return {}
    rows = _get_csv("ItemBonus", {"filter[Type]": "exact:35"})
    if rows is _FAILED:
        return _FAILED
    out: dict[int, None] = {}
    for r in rows:
        if str(_field(r, "Value_0", "Value[0]")) in emb_cats:
            pid = _field(r, "ParentItemBonusListID")
            if pid and str(pid).isdigit():
                out[int(pid)] = None
    return out


def resolve_emb_name(bid: int):
    """Reagent name for an embellishment-candidate bonus id, walking
    ItemBonusTreeNode -> ModifiedCraftingReagentItem -> Item -> ItemSparse.
    None = the chain does not close (a real answer: stored null, displayed
    "#<id>", manually nameable); _FAILED = network, retried next run."""
    nodes = _get_csv("ItemBonusTreeNode",
                     {"filter[ChildItemBonusListID]": f"exact:{bid}"})
    if nodes is _FAILED:
        return _FAILED
    parent = next((_field(r, "ParentItemBonusTreeID") for r in nodes
                   if _field(r, "ParentItemBonusTreeID")), None)
    if not parent or not str(parent).isdigit():
        return None
    mcri = _get_csv("ModifiedCraftingReagentItem",
                    {"filter[ItemBonusTreeID]": f"exact:{parent}"})
    if mcri is _FAILED:
        return _FAILED
    mid = next((r.get("ID") for r in mcri if r.get("ID")), None)
    if not mid:
        return None
    items = _get_csv("Item",
                     {"filter[ModifiedCraftingReagentItemID]": f"exact:{mid}"})
    if items is _FAILED:
        return _FAILED
    iid = next((r.get("ID") for r in items if r.get("ID")), None)
    if not iid:
        return None
    sparse = _get_csv("ItemSparse", {"filter[ID]": f"exact:{iid}"})
    if sparse is _FAILED:
        return _FAILED
    if not sparse:
        return None
    return _field(sparse[0], "Display_lang") or None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0,
                    help="max per-id fetches this run (0 = unlimited); the "
                         "rest are absent from the caches and retried later")
    args = ap.parse_args(argv)

    items_c = load_json(NAMES_ITEMS, {})
    enchs_c = load_json(NAMES_ENCHANTS, {})
    crafted_c = set(load_json(CRAFTED_IDS, []))
    emb_c = load_json(NAMES_BONUS_EMB, {})

    item_ids, ench_ids, bonus_tuples = scan_journal()
    budget = [args.limit if args.limit > 0 else float("inf")]

    def spend() -> bool:
        if budget[0] <= 0:
            return False
        budget[0] -= 1
        return True

    got = {"items": 0, "enchants": 0, "emb": 0, "failed": 0}

    # whole-table refreshes: crafted set + embellishment markers (grow-only)
    crafted = fetch_crafted()
    if crafted is _FAILED:
        got["failed"] += 1
    else:
        before = len(crafted_c)
        crafted_c |= crafted
        if len(crafted_c) != before:
            print(f"[names] crafted set: +{len(crafted_c) - before} "
                  f"({len(crafted_c)} total)")
    markers = fetch_markers()
    if markers is _FAILED:
        got["failed"] += 1
        markers = {}
    merge_grow_only(emb_c, {str(k): v for k, v in markers.items()})
    marker_ids = {int(k) for k in emb_c} | set(markers)

    # per-id item / enchant names, unseen only
    for iid in unseen(item_ids, items_c):
        if not spend():
            break
        got_it = fetch_item(iid)
        if got_it is _FAILED:
            got["failed"] += 1
            continue
        items_c[str(iid)] = got_it
        got["items"] += 1
    for eid in unseen(ench_ids, enchs_c):
        if not spend():
            break
        nm = fetch_enchant(eid)
        if nm is _FAILED:
            got["failed"] += 1
            continue
        enchs_c[str(eid)] = nm
        got["enchants"] += 1

    # embellishment identity: bonus ids co-occurring with a marker on a
    # journaled item, resolved to their reagent's name where the chain closes
    candidates: set[int] = set()
    for t in bonus_tuples:
        if any(b in marker_ids for b in t):
            candidates |= {b for b in t if b not in marker_ids}
    for bid in unseen(candidates, emb_c):
        if not spend():
            break
        nm = resolve_emb_name(bid)
        if nm is _FAILED:
            got["failed"] += 1
            continue
        emb_c[str(bid)] = nm
        got["emb"] += 1

    save_cache(NAMES_ITEMS, items_c)
    save_cache(NAMES_ENCHANTS, enchs_c)
    save_cache(CRAFTED_IDS, crafted_c)
    save_cache(NAMES_BONUS_EMB, emb_c)
    print(f"[names] items {got['items']} fetched "
          f"({len(items_c)} cached) | enchants {got['enchants']} "
          f"({len(enchs_c)}) | emb bonuses {got['emb']} ({len(emb_c)}) | "
          f"crafted {len(crafted_c)} | {got['failed']} fetch failures "
          f"(retried next run)", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                       # noqa: BLE001
        # names are a nicety: whatever went wrong, the pipeline continues
        # and this run's unfinished ids stay absent (= retried next run)
        print(f"[names] aborted without harm: {e}", flush=True)
        sys.exit(0)
