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
  data/emb_identity.json    {"ids": [...], "names": {"<bonusid>": "name"},
                            "run": {...}} -- the EMBELLISHMENT IDENTITY map,
                            derived whole from db2 every run (§ below).
                            "names" is grow-only and POSITIVE ONLY: a null is
                            never stored, so an unresolved id is simply absent
                            and retried at one request. Hand entries survive.
  data/emb_items.json       {"<itemid>": "name"} -- the handful of items that
                            are INTRINSICALLY embellished (ItemSparse carries
                            an Embellished LimitCategory directly, 6 today).
  data/emb_overrides.json   {"names": {...}, "ids": [...]} -- HUMAN ONLY,
                            never machine-written, highest precedence.
  data/names_bonus_emb2.json RETIRED (v2). Read once by a migration that
                            copies its truthy values and DROPS its nulls.
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

EMBELLISHMENT IDENTITY (v3, 2026-08-30) -- the one place that contract does
NOT apply. Two versions failed here, both silently:
  v1 named a bonus id by walking bonus -> ItemBonusTreeNode -> MCRI -> Item
     -> ItemSparse. That chain closes for EVERY optional crafting reagent,
     so stat missives and sparks were named as embellishments.
  v2 guarded the same walk on the reagent's Embellished ItemSparse
     LimitCategory. Measured against live db2: that field is 0 on every
     crafting reagent in the game and non-zero on exactly SIX non-reagent
     WORN items -- the guard rejected 100% of candidates BY CONSTRUCTION,
     and each rejection was cached as a permanent null.
v3 never asks a question per bonus id. It derives ONE global identity set
from two whole-table fetches and tests membership:
  IDENTITY = the non-marker bonus lists emitted (full, cycle-guarded
  recursion) by an ItemBonusTreeNode tree that emits a MARKER and has a
  ModifiedCraftingReagentItem row, minus any id also emitted by a
  non-marker tree (leak guard, counted).
Measured 2026-08-30: 4,410 trees -> 53 emit a marker directly (every one
with exactly 2 children) + 1 through a subtree = 54; 49 are reagent-backed;
49 identity ids, 0 leaked, 48 named. Draconic Missive (8791), Spark of
Tides (13751) and Spark of Radiance (12066) are all absent from the set, so
an item carrying an embellishment AND a missive AND a spark still resolves
to exactly one identity. Because the candidate universe is db2-bounded and
tiny, a failure to name is stored as ABSENCE, never as a null -- the bug
class that made v2 unrecoverable cannot recur.
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
NAMES_BONUS_EMB2 = DATA / "names_bonus_emb2.json"   # RETIRED, migration only
EMB_IDENTITY = DATA / "emb_identity.json"
EMB_ITEMS = DATA / "emb_items.json"
EMB_OVERRIDES = DATA / "emb_overrides.json"
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


def _int(v) -> int:
    """A db2 CSV cell as a positive int; 0 for empty/absent/garbage."""
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def derive_emb_identity(marker_ids: set[int]):
    """The whole embellishment identity map, from two whole-table fetches.

    -> ({bonus id: owning reagent-backed tree}, {tree: MCRI id}, stats)
    or _FAILED (network) -- on _FAILED the caller writes NOTHING, so the
    previous map survives and the site degrades to the generic bucket at
    worst, never to a wrong name.

    An embellishment reagent's ItemBonusTree emits the Embellished MARKER
    bonus list as one of its children; nothing else in the game does. That
    is the entire discriminator, and it is structural rather than
    statistical: no field, flag or threshold anywhere says "embellishment".
    Two guards, each other's insurance (both measured 0-cost today, and
    direct-children-only vs. backed+recursion produce the IDENTICAL set):
      * MCRI-BACKING -- only a tree that is literally some craftable
        reagent's tree may contribute identity. It excludes ids whose
        marker is inherited up an ancestor chain (8809 via unbacked tree
        3902), which is exactly the shape of reasoning that produced v1.
      * the LEAK GUARD -- an id ALSO emitted by a tree unrelated to any
        marker tree is dropped and COUNTED, never silently absorbed.
    Recursion is full and cycle-guarded, not one level: nested chains
    exist (5287 -> 5102 -> 5974).

    The leak guard compares against marker trees AND THEIR DESCENDANTS. A
    marker tree's own subtree emits its identity id too, so comparing
    against the marker trees alone would drop the identity of any nested
    reagent-backed tree -- the exact case the recursion exists to catch.
    Re-derived live 2026-08-30: both readings give the identical 49 ids
    with zero drops today, so this costs nothing and is the only one that
    survives Blizzard nesting a backed tree.
    """
    nodes = _get_csv("ItemBonusTreeNode")
    if nodes is _FAILED:
        return _FAILED
    mcri = _get_csv("ModifiedCraftingReagentItem")
    if mcri is _FAILED:
        return _FAILED

    by_parent: dict[int, list] = {}
    for r in nodes:
        p = _int(_field(r, "ParentItemBonusTreeID"))
        if p:
            by_parent.setdefault(p, []).append(r)
    depth = [0]

    def emits(t: int, seen: frozenset = frozenset()) -> set:
        if t in seen:
            return set()                       # cycle guard: never recurse in
        depth[0] = max(depth[0], len(seen) + 1)
        out = set()
        for n in by_parent.get(t, []):
            c = _int(_field(n, "ChildItemBonusListID"))
            if c:
                out.add(c)
            s = _int(_field(n, "ChildItemBonusTreeID"))
            if s:
                out |= emits(s, seen | {t})
        return out

    tree_lists = {t: emits(t) for t in sorted(by_parent)}
    emitted_by: dict[int, set] = {}
    for t, s in tree_lists.items():
        for b in s:
            emitted_by.setdefault(b, set()).add(t)
    marker_trees = {t for t, s in tree_lists.items() if s & marker_ids}

    def descend(t: int, seen: set) -> None:
        for n in by_parent.get(t, []):
            s = _int(_field(n, "ChildItemBonusTreeID"))
            if s and s not in seen:
                seen.add(s)
                descend(s, seen)

    adjacent = set(marker_trees)
    for t in sorted(marker_trees):
        descend(t, adjacent)
    direct = {t for t in by_parent
              if {_int(_field(n, "ChildItemBonusListID"))
                  for n in by_parent[t]} & marker_ids}
    bad_children = sorted(t for t in direct if len(by_parent[t]) != 2)

    tree2mcri: dict[int, int] = {}
    for r in sorted(mcri, key=lambda r: _int(_field(r, "ID"))):
        t, mid = _int(_field(r, "ItemBonusTreeID")), _int(_field(r, "ID"))
        if t and mid:
            tree2mcri.setdefault(t, mid)       # first row per tree, sorted
    backed = {t for t in marker_trees if t in tree2mcri}

    ident: dict[int, int] = {}
    leaked: list[int] = []
    for t in sorted(backed):                   # sorted => deterministic
        for b in sorted(tree_lists[t] - marker_ids):
            if emitted_by.get(b, set()) - adjacent:
                leaked.append(b)               # LEAK GUARD (0/49 today)
                continue
            ident.setdefault(b, t)             # first backed tree wins
    stats = {"trees": len(by_parent), "direct": len(direct),
             "marker_trees": len(marker_trees), "backed": len(backed),
             "unbacked": sorted(marker_trees - backed),
             "leaked": sorted(set(leaked)), "depth": depth[0],
             "bad_children": bad_children,
             "by_marker": {str(m): sum(1 for t in direct
                                       if m in tree_lists[t])
                           for m in sorted(marker_ids)}}
    return ident, tree2mcri, stats


def fetch_emb_name(mcri_id: int):
    """The reagent's display name for an MCRI id, straight off ItemSparse
    (which carries ModifiedCraftingReagentItemID itself -- the Item hop v1
    and v2 walked is deleted). Quality tiers return several rows; take the
    name only when they AGREE on one string, else refuse rather than pick.
    None = no published name (an absence, retried next run -- never a
    stored null); _FAILED = network."""
    rows = _get_csv("ItemSparse",
                    {"filter[ModifiedCraftingReagentItemID]":
                     f"exact:{mcri_id}"})
    if rows is _FAILED:
        return _FAILED
    disp = {(_field(r, "Display_lang") or "").strip() for r in rows}
    disp.discard("")
    return disp.pop() if len(disp) == 1 else None


def fetch_emb_items(emb_cats: set[str]) -> dict | object:
    """The items that are INTRINSICALLY embellished -- ItemSparse carries an
    Embellished LimitCategory directly. Exactly 6 today (512 -> 6 rows, 697
    -> 0), every one a finished worn item, none a reagent. This is the ONLY
    legitimate reading of ItemSparse.LimitCategory."""
    out: dict[str, str] = {}
    for cat in sorted(emb_cats, key=lambda c: int(c)):
        rows = _get_csv("ItemSparse", {"filter[LimitCategory]": f"exact:{cat}"})
        if rows is _FAILED:
            return _FAILED
        for r in rows:
            iid, nm = _int(_field(r, "ID")), _field(r, "Display_lang")
            if iid and nm:
                out[str(iid)] = nm
    return out


def migrate_emb2(names: dict) -> tuple[int, int]:
    """Read the RETIRED v2 cache exactly once: copy its truthy values in
    (hand-typed names survive) and DROP every null. v2 wrote one null per
    candidate bonus id and `unseen()` never re-asks a present key, so those
    nulls are permanent and unfalsifiable -- the dropped count is the only
    evidence of how large the sticky block was, so it is reported."""
    old = load_json(NAMES_BONUS_EMB2, None)
    if not isinstance(old, dict):
        return 0, 0
    kept = dropped = 0
    for k, v in old.items():
        if not str(k).isdigit():
            continue                        # only bonus-id keys
        if not v:
            dropped += 1                    # the sticky nulls, counted
        elif isinstance(v, str) and k not in names:
            names[k] = v
            kept += 1
    return kept, dropped


def save_emb_identity(ids, names: dict, run: dict) -> None:
    """emb_identity.json: sorted ids, name map sorted by int key, and the
    run's own diagnostics (build_site_data republishes them into
    site/build_health.txt, the only place a human ever reads them).

    THE FLOOR: a run that PARSED but derived nothing never empties the map.
    _FAILED covers the network dying; it does not cover db2 answering 200
    with a table that suddenly yields zero marker trees (a renamed column,
    a schema change, a stub build pointed at the real data dir -- all three
    have happened). Under v3-as-first-written those were "success" and
    rewrote ids to [], which is unrecoverable-by-retry: emb_of then has no
    path to a name, every carry falls into the generic bucket, and the next
    run inherits the wipe. So the previous ids survive and the run is
    marked NOT ok, which puts it on the build_health verdict line where a
    human sees it, instead of shipping a silent regression."""
    new_ids = sorted(ids)
    prev = [i for i in ((load_json(EMB_IDENTITY, {}) or {}).get("ids") or [])
            if isinstance(i, int)]
    if prev and not new_ids:
        new_ids = sorted(prev)
        run["ok"] = False
        run["empty_derivation"] = True
        print(f"[emb] derivation returned 0 identity ids; keeping the "
              f"cached {len(new_ids)} -- a parseable-but-empty table never "
              f"empties the map", flush=True)
    obj = {"ids": new_ids,
           "names": {k: names[k] for k in sorted(names, key=int)},
           "run": run}
    tmp = EMB_IDENTITY.with_name(EMB_IDENTITY.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n")
    tmp.replace(EMB_IDENTITY)


def emb_pass(marker_ids: set[int], emb_cats: set[str], got: dict) -> None:
    """The whole embellishment identity pass. Runs FIRST in main() and is
    EXEMPT from --limit: it costs 4 requests plus one per newly-published
    reagent, it is journal-independent, and under v2 it ran last and died
    on the item loop's spent budget -- a second live failure that would
    have survived a naming-only fix."""
    cached = load_json(EMB_IDENTITY, {})
    if not isinstance(cached, dict):
        cached = {}
    names = {str(k): v for k, v in (cached.get("names") or {}).items()
             if str(k).isdigit() and isinstance(v, str) and v}
    ids = [i for i in (cached.get("ids") or []) if isinstance(i, int)]
    mig_kept, mig_dropped = migrate_emb2(names)
    if mig_kept or mig_dropped:
        print(f"[emb] migrated {mig_kept} names from names_bonus_emb2 "
              f"(dropped {mig_dropped} nulls)", flush=True)

    run: dict = {"ok": False, "fetched": 0, "failures": 0,
                 "migrated": mig_kept, "dropped_nulls": mig_dropped,
                 "markers": sorted(marker_ids)}
    derived = derive_emb_identity(marker_ids) if marker_ids else _FAILED
    if derived is _FAILED:
        got["failed"] += 1
        run["failures"] += 1
        run.update({k: cached.get("run", {}).get(k)
                    for k in ("trees", "direct", "marker_trees", "backed",
                              "unbacked", "leaked", "depth", "bad_children",
                              "by_marker")})
        print("[emb] db2 unavailable; identity map unchanged "
              f"({len(ids)} ids, {len(names)} names cached)", flush=True)
        save_emb_identity(ids, names, run)     # names/migration still land
        return
    ident, tree2mcri, stats = derived
    run.update(stats)
    for b in sorted(ident):
        if names.get(str(b)):
            continue                           # a name is never re-asked
        nm = fetch_emb_name(tree2mcri[ident[b]])
        if nm is _FAILED:
            got["failed"] += 1
            run["failures"] += 1
            continue                           # ABSENT, not null: retried
        if nm:
            names[str(b)] = nm
            run["fetched"] += 1
            got["emb"] += 1
    items_c = load_json(EMB_ITEMS, {})
    intrinsic = fetch_emb_items(emb_cats) if emb_cats else _FAILED
    if intrinsic is _FAILED:
        got["failed"] += 1
        run["failures"] += 1
    else:
        merge_grow_only(items_c, intrinsic)
        save_cache(EMB_ITEMS, items_c)
    run["ok"] = True
    run["intrinsic"] = len(items_c)
    save_emb_identity(ident, names, run)       # ids rewritten only on success
    gaps = sorted(b for b in ident if not names.get(str(b)))
    if gaps:
        print("[emb] unnamed identity ids " + ",".join(str(b) for b in gaps)
              + " -- paste into data/emb_overrides.json: "
              + json.dumps({"names": {str(b): "" for b in gaps}}), flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0,
                    help="max per-id fetches this run (0 = unlimited); the "
                         "rest are absent from the caches and retried later")
    args = ap.parse_args(argv)

    items_c = load_json(NAMES_ITEMS, {})
    enchs_c = load_json(NAMES_ENCHANTS, {})
    crafted_c = set(load_json(CRAFTED_IDS, []))
    icons_c = load_json(NAMES_ICONS, {})

    item_ids, ench_ids, _bonus_tuples = scan_journal()
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

    # embellishment identity FIRST and exempt from the budget (§ module
    # docstring): it is journal-independent, costs 4 requests steady state,
    # and running it last behind --limit 2000 is how v2's emb loop could
    # silently never execute at all
    emb_pass(marker_ids, emb_cats if isinstance(emb_cats, set) else set(), got)

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
    save_cache(NAMES_ICONS, icons_c)
    _ec = load_json(EMB_IDENTITY, {})
    n_img = sum(1 for p in ICONS_DIR.glob("*.jpg"))
    print(f"[names] items {got['items']} fetched "
          f"({len(items_c)} cached) | enchants {got['enchants']} "
          f"({len(enchs_c)}) | emb identity: {len(_ec.get('ids') or [])} ids, "
          f"{len(_ec.get('names') or {})} named ({got['emb']} fetched, "
          f"{(_ec.get('run') or {}).get('failures', 0)} failures) | "
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
