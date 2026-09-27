#!/usr/bin/env python3
"""The execution bundle: five extra per-run tables, their parsers, and the
quota gate that decides which runs get them.

Contract: keylevel_addon/design/baselines-from-wowlogs.md §1 and §4 (the
vetting site reads what this collector stores, so no run is ever pulled from
Warcraft Logs twice). Measured shapes and costs: pug/verify_cost.md.

Per run the Summary table is joined, INSIDE the same aliased request, by
Interrupts, Dispels, DamageTaken filtered to the dungeon's avoidable list,
Casts filtered to the roster's defensive/self-heal/consumable kit, and
Healing (+1.0 point per table per run, 6.0 warm / 6.5-8.5 cold for the six).
The parsers here turn those tables into per-player columns:

    kicks, kicks_by   interrupts landed, and per enemy spell id
    dispels, dispels_by
    avoid_dmg         damage taken from the avoidable list
    def_casts         casts of the kit
    heal_total, heal_over

zero-filled for a player a table does not list (a player with no kicks is
simply absent from Interrupts) and None for every player when a table is
missing from the node. The Summary alone yields pots / hs (potionUse and
healthstoneUse, None when the field is absent) and deaths_chain (own deaths
with another party death in the previous CHAIN_WINDOW_MS).

Run-level, for the population priority and dispellable tables: int_spells
{guid: {name, begun, completed, interrupted}} and dispel_spells {guid: {name,
applied, dispelled, expired}}. A dispelling actor that is not a party member
(a pet, a totem) is attributed to its owner when the node carries
masterData.actors with petOwner, and dropped otherwise.

THE GATE (§4, measured on the CSV). A run gets the bundle iff any of its
five (spec, dungeon, 2-level band) cells holds fewer than QUOTA_ROWS bundled
player-rows over the trailing QUOTA_DAYS. That admits ~4,600 of ~9,000
runs/day at key >= 10, fills every cell the population can fill, and costs
about +960 points/hour. The counter is rebuilt from the players journal at
the start of every run and kept on disk beside the other checkpoints.

The curated spell lists come from ONE file published by the addon repo;
load_lists() fetches it at the start of a refresh run and falls back to the
last fetched copy, then to the vendored data/lists.json. It never raises.

No pandas at import: fetch_data imports this at start-up and must stay light.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import time
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
LISTS_URL = "https://st331.github.io/keylevel_addon/data/lists.json"
LISTS_FILE = ROOT / "data" / "lists.json"                     # vendored fallback
LISTS_CACHE = ROOT / "data" / "processed" / "lists.json"      # last successful fetch

QUOTA_ROWS = 100          # bundled player-rows per cell that count as "full"
QUOTA_DAYS = 14           # trailing window the counter looks back over
# The pause switch: while data/bundle.paused exists (committed, so the
# workflow sees it) or EXEC_BUNDLE=off is in the environment, no run gets
# the bundle -- the Summary sweep, the baselines and the run store go on
# with what is already journaled. Delete the file (or set EXEC_BUNDLE=on)
# to resume; the gate then picks up exactly where the counts left off.
PAUSE_FILE = ROOT / "data" / "bundle.paused"


def bundle_paused(env=None, pause_file=None) -> bool:
    env = os.environ if env is None else env
    flag = str(env.get("EXEC_BUNDLE", "")).strip().lower()
    if flag in ("off", "0", "false", "no", "paused"):
        return True
    if flag in ("on", "1", "true", "yes"):
        return False
    return pathlib.Path(pause_file if pause_file is not None else PAUSE_FILE).exists()
BAND = 2                  # key levels per band: b18 = 18-19
CHAIN_WINDOW_MS = 5_000   # another party death within this many ms before own death
EST_COST_BUNDLE = 7.5     # est_cost per bundled run (measured 6.5-8.5 cold)
DAY_MS = 86_400_000

BUNDLE_TABLES = ("interrupts", "dispels", "dmgTaken", "casts", "healing")
# per-player columns, in the order they follow keystone_s on a journal row
# and in the CSV. `exec` (1/0) says whether the bundle was fetched; the
# Summary-derived three (pots, hs, deaths_chain) are present on every row
# written since the bundle landed; the eight bundle columns are None unless
# exec is 1 and their table came back.
SUMMARY_COLUMNS = ("pots", "hs", "deaths_chain")
BUNDLE_COLUMNS = ("kicks", "kicks_by", "dispels", "dispels_by", "avoid_dmg",
                  "def_casts", "heal_total", "heal_over")
EXEC_COLUMNS = ("exec",) + SUMMARY_COLUMNS + BUNDLE_COLUMNS


# --------------------------------------------------------------------------
# the curated lists
# --------------------------------------------------------------------------

def _valid_lists(doc) -> bool:
    return isinstance(doc, dict) and isinstance(doc.get("specs"), dict)


def load_lists(fetch: bool = True, timeout: float = 20.0, log=print) -> dict:
    """The lists document, with `_source` = fetched | cached | vendored.

    Fetch first (the published file is the single source of truth for both
    projects), fall back to the last fetched copy, then to the vendored one.
    A refresh run must never fail on this: the vendored copy is always there.
    """
    doc = None
    source = None
    if fetch:
        try:
            import requests
            r = requests.get(LISTS_URL, timeout=timeout)
            r.raise_for_status()
            cand = r.json()
            if _valid_lists(cand):
                doc, source = cand, "fetched"
                try:
                    LISTS_CACHE.parent.mkdir(parents=True, exist_ok=True)
                    tmp = LISTS_CACHE.with_suffix(".json.tmp")
                    tmp.write_text(json.dumps(cand, ensure_ascii=False))
                    os.replace(tmp, LISTS_CACHE)
                except OSError:
                    pass
            else:
                log(f"[lists] {LISTS_URL} returned an unexpected document; "
                    f"using the fallback copy")
        except Exception as e:                       # noqa: BLE001 -- never fail the run
            log(f"[lists] fetch failed ({type(e).__name__}: {str(e)[:120]}); "
                f"using the fallback copy")
    if doc is None:
        for path, label in ((LISTS_CACHE, "cached"), (LISTS_FILE, "vendored")):
            try:
                cand = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if _valid_lists(cand):
                doc, source = cand, label
                break
    if doc is None:
        doc, source = {"version": "none", "specs": {}, "dungeons": {},
                       "consumables": {}}, "empty"
    doc["_source"] = source
    log(f"[lists] version {doc.get('version', '?')} ({source}); "
        f"{len(doc.get('specs') or {})} specs, "
        f"{len(doc.get('dungeons') or {})} dungeons")
    return doc


def _id_of(item) -> int | None:
    if isinstance(item, bool):
        return None
    if isinstance(item, (int, float)):
        return int(item)
    if isinstance(item, str) and item.strip().lstrip("-").isdigit():
        return int(item)
    if isinstance(item, dict):
        return _id_of(item.get("id"))
    return None


def _ids(items) -> set[int]:
    if isinstance(items, (int, float, str)):
        items = [items]
    out = set()
    for it in items or []:
        i = _id_of(it)
        if i is not None:
            out.add(i)
    return out


def spec_key(cls, spec) -> str:
    """'Warrior-Arms' as lists.json keys specs; the class alone when the
    spec is unknown (the class-level fallback the gate and the kit use)."""
    cls = (cls or "").strip()
    spec = (spec or "").strip()
    return f"{cls}-{spec}" if cls and spec else cls


def roster_specs(team) -> list[str]:
    """Spec keys from a fightRankings `team` roster ([] when it has none)."""
    out = []
    for m in team or []:
        if not isinstance(m, dict):
            continue
        k = spec_key(m.get("class"), m.get("spec"))
        if k:
            out.append(k)
    return out


def kit_ids(lists: dict, spec_keys) -> list[int]:
    """Union of defensives + selfheals of the specs given (class-level union
    for a key whose spec is unknown or not in the lists) + every consumable."""
    specs = lists.get("specs") or {}
    ids: set[int] = set()
    for sk in spec_keys or []:
        entry = specs.get(sk)
        if isinstance(entry, dict):
            entries = [entry]
        else:
            cls = sk.split("-", 1)[0]
            entries = [v for k, v in specs.items()
                       if isinstance(v, dict) and k.split("-", 1)[0] == cls]
        for e in entries:
            for grp in ("defensives", "selfheals"):
                ids |= _ids(e.get(grp))
    for v in (lists.get("consumables") or {}).values():
        ids |= _ids(v)
    return sorted(ids)


def avoidable_ids(lists: dict, dungeon: str) -> list[int]:
    """The dungeon's curated avoidable-damage ids ([] when the lists have
    none for it -- the provisional file ships an empty dungeons block)."""
    duns = lists.get("dungeons") or {}
    entry = duns.get(dungeon)
    if entry is None:
        return []
    if isinstance(entry, dict):
        return sorted(_ids(entry.get("avoidable")))
    return sorted(_ids(entry))


# --------------------------------------------------------------------------
# the request
# --------------------------------------------------------------------------

def bundle_subquery(alias: str, code: str, fid, avoidable, kit) -> str:
    """One report alias carrying the six tables (§4). The Summary keeps the
    alias `table`, the name the summary stage has always read, so the caller
    path and every existing test see the same node shape. The two filtered
    tables are omitted when their id list is empty -- a filter over nothing
    is not a table -- and parse as "missing" (None) downstream."""
    f = f"fightIDs: [{int(fid)}]"
    parts = [f"table: table({f}, dataType: Summary)",
             f"interrupts: table({f}, dataType: Interrupts)",
             f"dispels: table({f}, dataType: Dispels)"]
    if avoidable:
        ids = ",".join(str(int(i)) for i in avoidable)
        parts.append(f'dmgTaken: table({f}, dataType: DamageTaken, '
                     f'filterExpression: "ability.id in ({ids})")')
    if kit:
        ids = ",".join(str(int(i)) for i in kit)
        parts.append(f'casts: table({f}, dataType: Casts, '
                     f'filterExpression: "ability.id in ({ids})")')
    parts.append(f"healing: table({f}, dataType: Healing)")
    return f'{alias}: report(code: "{code}") {{ {" ".join(parts)} }}'


# --------------------------------------------------------------------------
# the parsers
# --------------------------------------------------------------------------

def _table_data(node, key):
    t = node.get(key) if isinstance(node, dict) else None
    d = t.get("data") if isinstance(t, dict) else None
    return d if isinstance(d, dict) else None


def _spell_rows(data: dict):
    """Interrupts / Dispels: one row per enemy spell, nested one level under
    entries[] (entries[0].entries[]); a flat row is tolerated."""
    for grp in data.get("entries") or []:
        if not isinstance(grp, dict):
            continue
        subs = grp.get("entries")
        if isinstance(subs, list):
            for sub in subs:
                if isinstance(sub, dict):
                    yield sub
        elif grp.get("guid") is not None:
            yield grp


def pet_owners(node) -> dict:
    """{actor id: owner id} from masterData.actors when the node carries it."""
    md = node.get("masterData") if isinstance(node, dict) else None
    actors = md.get("actors") if isinstance(md, dict) else None
    out = {}
    for a in actors or []:
        if isinstance(a, dict) and a.get("petOwner") is not None and a.get("id") is not None:
            out[a["id"]] = a["petOwner"]
    return out


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def parse_tables(node: dict, player_ids, actors: dict | None = None):
    """The five bundle tables -> (per_player, int_spells, dispel_spells, present).

    per_player: {actor id: {kicks, kicks_by, dispels, dispels_by, avoid_dmg,
    def_casts, heal_total, heal_over}} for every id in player_ids, zero-filled
    where a table does not list the player and None where the table is
    missing. present: {table: bool}. `actors` maps a pet/totem actor id to
    its owner (pet_owners()); an unmapped non-party actor is dropped.
    """
    ids = [i for i in player_ids if i is not None]
    idset = set(ids)
    actors = actors or {}
    out = {i: {} for i in ids}
    present = {}

    def owner_of(actor_id):
        if actor_id in idset:
            return actor_id
        o = actors.get(actor_id)
        return o if o in idset else None

    # Interrupts / Dispels share a shape; the run-level sums differ in name
    for key, total_col, by_col, sums in (
            ("interrupts", "kicks", "kicks_by", ("begun", "completed", "interrupted")),
            ("dispels", "dispels", "dispels_by", ("applied", "expired", "dispelled"))):
        d = _table_data(node, key)
        present[key] = d is not None
        spells: dict = {}
        if d is None:
            for i in ids:
                out[i][total_col] = None
                out[i][by_col] = None
            if key == "interrupts":
                int_spells = spells
            else:
                dispel_spells = spells
            continue
        tot: Counter = Counter()
        by = {i: Counter() for i in ids}
        for sub in _spell_rows(d):
            guid = sub.get("guid")
            if guid is None:
                continue
            g = str(guid)
            s = spells.setdefault(g, {"name": sub.get("name"), sums[0]: 0, sums[1]: 0, sums[2]: 0})
            if not s.get("name") and sub.get("name"):
                s["name"] = sub.get("name")
            s[sums[0]] += _int(sub.get("spellsBegun"))
            s[sums[1]] += _int(sub.get("spellsCompleted"))
            s[sums[2]] += _int(sub.get("spellsInterrupted"))
            for det in sub.get("details") or []:
                if not isinstance(det, dict):
                    continue
                o = owner_of(det.get("id"))
                if o is None:
                    continue
                n = _int(det.get("total"))
                tot[o] += n
                by[o][g] += n
        for i in ids:
            out[i][total_col] = int(tot[i])
            out[i][by_col] = {k: int(v) for k, v in by[i].items() if v}
        if key == "interrupts":
            int_spells = spells
        else:
            dispel_spells = spells

    # DamageTaken (filtered) and Casts (filtered): entries[] per player, total
    for key, col in (("dmgTaken", "avoid_dmg"), ("casts", "def_casts")):
        d = _table_data(node, key)
        present[key] = d is not None
        if d is None:
            for i in ids:
                out[i][col] = None
            continue
        tot = Counter()
        for e in d.get("entries") or []:
            if isinstance(e, dict) and e.get("id") in idset:
                tot[e["id"]] += _int(e.get("total"))
        for i in ids:
            out[i][col] = int(tot[i])

    # Healing: total + overheal per player; NPC rows dropped; null overheal = 0
    d = _table_data(node, "healing")
    present["healing"] = d is not None
    if d is None:
        for i in ids:
            out[i]["heal_total"] = None
            out[i]["heal_over"] = None
    else:
        tot, over = Counter(), Counter()
        for e in d.get("entries") or []:
            if not isinstance(e, dict) or e.get("id") not in idset:
                continue
            if str(e.get("type") or "").upper() == "NPC":
                continue
            tot[e["id"]] += _int(e.get("total"))
            over[e["id"]] += _int(e.get("overheal"))
        for i in ids:
            out[i]["heal_total"] = int(tot[i])
            out[i]["heal_over"] = int(over[i])
    return out, int_spells, dispel_spells, present


def deaths_chain(death_events, player_ids, window_ms: int = CHAIN_WINDOW_MS):
    """{id: own deaths with another party death in the previous window_ms}.

    None (for every player) when the Summary carries no deathEvents list at
    all. Events of non-party actors and events without a deathTime are
    ignored. Simultaneous deaths are ordered (time, id): the later one sees
    the earlier at 0 ms and counts, the earlier does not -- one chained death
    per pair, never two for one event.
    """
    if not isinstance(death_events, list):
        return {i: None for i in player_ids}
    idset = set(player_ids)
    ev = []
    for e in death_events:
        if not isinstance(e, dict) or e.get("id") not in idset:
            continue
        t = e.get("deathTime")
        if isinstance(t, bool) or not isinstance(t, (int, float)):
            continue
        ev.append((float(t), e["id"]))
    ev.sort(key=lambda x: (x[0], str(x[1])))
    chain: Counter = Counter()
    for i, (t, pid) in enumerate(ev):
        for j in range(i - 1, -1, -1):
            if t - ev[j][0] > window_ms:
                break
            chain[pid] += 1
            break
    return {i: int(chain.get(i, 0)) for i in player_ids}


def summary_player_fields(p: dict) -> dict:
    """pots / hs off a playerDetails entry: the raw counts, None when absent."""
    out = {}
    for src, dst in (("potionUse", "pots"), ("healthstoneUse", "hs")):
        v = p.get(src) if isinstance(p, dict) else None
        out[dst] = int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    return out


def pack_by(d) -> str | None:
    """{'1294557': 12} -> '1294557:12|1307571:11'; {} -> 'none'; None -> None.
    The same sentinel discipline as fetch_data.pack_sets: an empty dict must
    survive the CSV round trip as "fetched, nothing kicked", never as NaN."""
    if d is None:
        return None
    if not d:
        return "none"
    return "|".join(f"{k}:{int(v)}" for k, v in sorted(d.items(), key=lambda kv: str(kv[0])))


def unpack_by(s) -> dict | None:
    """The inverse of pack_by; tolerates a dict passed straight through."""
    if s is None:
        return None
    if isinstance(s, dict):
        return {str(k): int(v) for k, v in s.items()}
    if isinstance(s, float):                      # only NaN reaches here (the CSV's null)
        return None
    s = str(s).strip()
    if not s or s.lower() in ("nan", "none"):
        return {} if s.lower() == "none" else None
    out = {}
    for part in s.split("|"):
        if ":" not in part:
            continue
        k, v = part.rsplit(":", 1)
        try:
            out[k] = int(float(v))
        except ValueError:
            continue
    return out


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------

_EXEC_HINT = re.compile(rb'"exec":\s*(1|1\.0|true)\b')


def band_of(level) -> int:
    return (int(level) // BAND) * BAND


def cell_key(spec: str, dungeon: str, level) -> str:
    return f"{spec}|{dungeon}|b{band_of(level)}"


class BundleGate:
    """Bundled player-rows per (spec, dungeon, band) over the trailing window.

    Counts are kept per cell per UTC day so the window slides without a
    rebuild; a row is dated by its run's started_at (undated rows count as
    today, the conservative choice: they fill a cell rather than hide from
    it). `admits()` is the rule from the design doc: any of the run's cells
    under the quota admits the bundle. A roster whose specs are unknown is
    looked up at class level (the sum of the class's spec cells); a run with
    no roster at all is not admitted -- there is nothing to build the casts
    filter from either -- and counted under stats["no_roster"].
    """

    def __init__(self, path=None, quota: int = QUOTA_ROWS, days: int = QUOTA_DAYS,
                 now_ms: float | None = None, paused: bool | None = None):
        self.path = pathlib.Path(path) if path else None
        self.quota = int(quota)
        self.days = int(days)
        self.paused = bundle_paused() if paused is None else bool(paused)
        self.now_ms = float(now_ms) if now_ms is not None else time.time() * 1000
        self.today = int(self.now_ms // DAY_MS)
        self.cut_day = self.today - self.days + 1        # trailing `days` days inclusive
        self.cells: dict[str, Counter] = {}
        self.stats: Counter = Counter()
        self.dirty = False

    # -- counting --------------------------------------------------------
    def _day(self, started_ms) -> int:
        if isinstance(started_ms, bool) or not isinstance(started_ms, (int, float)):
            return self.today
        if started_ms != started_ms or started_ms <= 0 or started_ms > self.now_ms + DAY_MS:
            return self.today
        return int(started_ms // DAY_MS)

    def record(self, spec: str, dungeon: str, level, started_ms=None, n: int = 1) -> None:
        if not spec or dungeon is None or level is None:
            return
        try:
            key = cell_key(spec, str(dungeon), level)
        except (TypeError, ValueError):
            return
        self.cells.setdefault(key, Counter())[self._day(started_ms)] += int(n)
        self.dirty = True

    def _cell_count(self, key: str) -> int:
        c = self.cells.get(key)
        if not c:
            return 0
        return sum(n for day, n in c.items() if day >= self.cut_day)

    def count(self, spec: str, dungeon: str, level) -> int:
        """Rows in the cell; for a class-only key, the class's spec cells summed."""
        if "-" in spec:
            return self._cell_count(cell_key(spec, dungeon, level))
        suffix = f"|{dungeon}|b{band_of(level)}"
        prefix = f"{spec}-"
        own = f"{spec}{suffix}"            # rows parsed without a spec land here
        return sum(self._cell_count(k) for k in self.cells
                   if (k.startswith(prefix) and k.endswith(suffix)) or k == own)

    def admits(self, roster, dungeon: str, level) -> bool:
        if self.paused:
            self.stats["paused"] += 1
            return False
        roster = [r for r in (roster or []) if r]
        if not roster or dungeon is None or level is None:
            self.stats["no_roster"] += 1
            return False
        under = any(self.count(sk, dungeon, level) < self.quota for sk in roster)
        self.stats["admitted" if under else "full"] += 1
        return under

    def record_rows(self, rows) -> int:
        """Count the bundled rows a parse produced (exec == 1)."""
        n = 0
        for r in rows:
            if not r.get("exec"):
                continue
            self.record(spec_key(r.get("class"), r.get("spec")), r.get("dungeon"),
                        r.get("key_level"), r.get("started_at"))
            n += 1
        return n

    def cells_full(self) -> int:
        return sum(1 for k in self.cells if self._cell_count(k) >= self.quota)

    def cells_open(self) -> int:
        return sum(1 for k in self.cells if 0 < self._cell_count(k) < self.quota)

    # -- rebuild from the players journal ----------------------------------
    def rebuild(self, players_jsonl) -> int:
        """Recount from the journal (the source of truth); returns rows counted.
        A byte-level prefilter skips every row that never carried the bundle
        (all pre-bundle rows), so the walk costs about a second per million."""
        self.cells = {}
        self.dirty = True
        path = pathlib.Path(players_jsonl)
        if not path.exists():
            return 0
        n = 0
        with path.open("rb") as fh:
            for raw in fh:
                if not _EXEC_HINT.search(raw):
                    continue
                try:
                    r = json.loads(raw)
                except ValueError:
                    continue
                if not r.get("exec"):
                    continue
                self.record(spec_key(r.get("class"), r.get("spec")), r.get("dungeon"),
                            r.get("key_level"), r.get("started_at"))
                n += 1
        return n

    # -- persistence ---------------------------------------------------------
    def save(self, path=None) -> None:
        path = pathlib.Path(path) if path else self.path
        if path is None:
            return
        cells = {}
        for k, c in self.cells.items():
            kept = {str(day): int(n) for day, n in c.items() if day >= self.cut_day and n}
            if kept:
                cells[k] = kept
        doc = {"v": 1, "quota": self.quota, "days": self.days,
               "saved": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.now_ms / 1000)),
               "cells": cells}
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(doc, separators=(",", ":")))
        os.replace(tmp, path)
        self.dirty = False

    @classmethod
    def load(cls, path, quota: int = QUOTA_ROWS, days: int = QUOTA_DAYS,
             now_ms: float | None = None) -> "BundleGate":
        g = cls(path, quota=quota, days=days, now_ms=now_ms)
        try:
            doc = json.loads(pathlib.Path(path).read_text())
        except (OSError, ValueError):
            return g
        for k, c in (doc.get("cells") or {}).items():
            if not isinstance(c, dict):
                continue
            cnt = Counter()
            for day, n in c.items():
                try:
                    cnt[int(day)] += int(n)
                except (TypeError, ValueError):
                    continue
            if cnt:
                g.cells[k] = cnt
        g.dirty = False
        return g
