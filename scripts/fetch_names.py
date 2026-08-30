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
  data/emb_markers.json     sorted int list -- the Embellished limit-category
                            marker bonus ids (whole-table probe, grow-only)
  data/names_bonus_emb2.json {"<bonusid>": "name" | null} -- VALIDATED
                            embellishment reagent names for bonus ids seen
                            alongside a marker; null = validated not-an-
                            embellishment (missives, sparks...) or chain
                            open -- never re-asked, nameable manually.
                            (v2: the v1 cache conflated markers, reagents
                            and every co-occurring bonus id -- retired.)
  data/names_icons.json     {"<itemid>": "inv_..." | null} -- icon NAME per
                            item id (wowhead XML), resolved only for ids the
                            item cache already knows; null = asked, absent

Icon IMAGES are downloaded once per unseen icon name (zamimg medium JPGs,
~2-6 KB each) into data/processed/icons/<icon>.jpg; the build step copies
them into site/icons/ for self-hosting -- the page never hotlinks and never
runs third-party scripts.

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
EMB_MARKERS = DATA / "emb_markers.json"
NAMES_BONUS_EMB2 = DATA / "names_bonus_emb2.json"
NAMES_ICONS = DATA / "names_icons.json"
ICONS_DIR = DATA / "processed" / "icons"

WAGO = "https://wago.tools/db2"
WOWHEAD_XML = "https://www.wowhead.com/item={iid}&xml"
ZAM_ICON = "https://wow.zamimg.com/images/wow/icons/medium/{icon}.jpg"
HEADERS = {"User-Agent": "wowlogs-collector/1.0"}
SLEEP_S = 0.15
TIMEOUT_S = 30

# <icon displayId="...">inv_helm_...</icon> in wowhead's item XML
ICON_TAG_RE = re.compile(r"<icon[^>]*>([^<]+)</icon>")
# icon names become filenames and URL segments: anything outside this strict
# alphabet is discarded as junk rather than written to disk
ICON_NAME_RE = re.compile(r"[a-z0-9_\-]+", re.IGNORECASE)

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


def _get_raw(url: str):
    """One raw GET -> response bytes, or _FAILED on any error. Same spacing
    and UA discipline as the CSV fetches; never raises."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT_S)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        return r.content
    except Exception as e:                       # noqa: BLE001 -- never block
        print(f"[names] fetch failed: {url} ({e})", flush=True)
        return _FAILED
    finally:
        time.sleep(SLEEP_S)


def fetch_icon_name(iid: int):
    """The icon name for an item id off wowhead's item XML, None when the
    page carries no usable <icon> (asked-and-absent: cached as null)."""
    raw = _get_raw(WOWHEAD_XML.format(iid=iid))
    if raw is _FAILED:
        return _FAILED
    m = ICON_TAG_RE.search(raw.decode("utf-8", "replace"))
    if not m:
        return None
    name = m.group(1).strip().lower()
    return name if ICON_NAME_RE.fullmatch(name) else None


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


def fetch_emb_cats() -> set[str] | object:
    """ItemLimitCategory ids whose name contains "Embellished"
    (512 Embellished, 697 Outdoor Embellished today)."""
    cats = _get_csv("ItemLimitCategory")
    if cats is _FAILED:
        return _FAILED
    return {str(r.get("ID")) for r in cats
            if "embellished" in (_field(r, "Name_lang") or "").lower()}


def fetch_markers(emb_cats: set[str]) -> dict[int, None] | object:
    """Embellishment marker bonus ids: ItemBonus rows of Type=35 whose
    ItemLimitCategory is an Embellished category. The marker is generic;
    identity comes from the reagent bonus resolved separately."""
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


def resolve_emb_name(bid: int, emb_cats: set[str]):
    """VALIDATED reagent name for an embellishment-candidate bonus id,
    walking ItemBonusTreeNode -> ModifiedCraftingReagentItem -> Item ->
    ItemSparse. The chain closes for EVERY optional crafting reagent --
    stat missives, sparks, embellishments alike (owner bug reports
    2026-08-30: "Draconic Missive of the Peerless", "Spark of Tides",
    "Spark of Radiance" surfaced as embellishments) -- so the reagent item
    itself must carry an Embellished ItemLimitCategory to count. None =
    validated not-an-embellishment or chain open (stored null, never
    re-asked, manually nameable); _FAILED = network, retried next run."""
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
    lim = _field(sparse[0], "LimitCategory", "ItemLimitCategory")
    if str(lim) not in emb_cats:
        return None      # a plain optional reagent, not an embellishment
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
    emb_c = load_json(NAMES_BONUS_EMB2, {})
    icons_c = load_json(NAMES_ICONS, {})

    item_ids, ench_ids, bonus_tuples = scan_journal()
    budget = [args.limit if args.limit > 0 else float("inf")]

    def spend() -> bool:
        if budget[0] <= 0:
            return False
        budget[0] -= 1
        return True

    got = {"items": 0, "enchants": 0, "emb": 0, "icons": 0, "images": 0,
           "failed": 0}

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
    emb_cats = fetch_emb_cats()
    if emb_cats is _FAILED:
        got["failed"] += 1
        emb_cats = set()
    markers = fetch_markers(emb_cats)
    if markers is _FAILED:
        got["failed"] += 1
        markers = {}
    # the marker set lives in its OWN committed file (grow-only union) --
    # markers must never mix with reagent-name candidates: treating every
    # cached candidate as a marker is exactly the bug that let stat missives
    # and sparks split item identity and pose as embellishments
    marker_ids = {int(v) for v in load_json(EMB_MARKERS, [])
                  if str(v).isdigit()} | set(markers)
    save_cache(EMB_MARKERS, sorted(marker_ids))

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
        if not emb_cats:     # cannot validate without the category table
            break
        if not spend():
            break
        nm = resolve_emb_name(bid, emb_cats)
        if nm is _FAILED:
            got["failed"] += 1
            continue
        emb_c[str(bid)] = nm
        got["emb"] += 1

    # icon names for item ids the item cache already knows (icons resolve
    # off a different source, so they trail the name fetch by design), then
    # each unseen icon IMAGE exactly once into the local store -- the build
    # step publishes copies under site/icons/, nothing ever hotlinks
    for iid in unseen(sorted(int(k) for k in items_c), icons_c):
        if not spend():
            break
        ic = fetch_icon_name(iid)
        if ic is _FAILED:
            got["failed"] += 1
            continue
        icons_c[str(iid)] = ic
        got["icons"] += 1
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    for ic in sorted({v for v in icons_c.values() if v}):
        dest = ICONS_DIR / f"{ic}.jpg"
        if dest.exists():
            continue                    # downloaded once, never re-fetched
        if not spend():
            break
        raw = _get_raw(ZAM_ICON.format(icon=ic))
        if raw is _FAILED or not raw:
            got["failed"] += 1
            continue
        dest.write_bytes(raw)
        got["images"] += 1

    save_cache(NAMES_ITEMS, items_c)
    save_cache(NAMES_ENCHANTS, enchs_c)
    save_cache(CRAFTED_IDS, crafted_c)
    save_cache(NAMES_BONUS_EMB2, emb_c)
    save_cache(NAMES_ICONS, icons_c)
    n_img = sum(1 for p in ICONS_DIR.glob("*.jpg"))
    print(f"[names] items {got['items']} fetched "
          f"({len(items_c)} cached) | enchants {got['enchants']} "
          f"({len(enchs_c)}) | emb bonuses {got['emb']} ({len(emb_c)}) | "
          f"crafted {len(crafted_c)} | icons {got['icons']} "
          f"({len(icons_c)} cached, {got['images']} images fetched, "
          f"{n_img} stored) | {got['failed']} fetch failures "
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
