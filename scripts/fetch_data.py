#!/usr/bin/env python3
"""Midnight Season 1 Mythic+ data collection pipeline.

Stages (all checkpointed; safe to kill and re-run at any point):

  1. sweep      - paginate fightRankings for every dungeon x keystone bracket
                  (keys 12-25+) until the API stops serving pages (20-page cap
                  per bracket).  One rankings entry == one unique dungeon run,
                  so this is 5x cheaper than characterRankings which repeats
                  each run once per player.
  2. summaries  - for every ranked run with a public report, fetch the report
                  Summary table (ONE ~1-point query per run) which contains
                  per-player damage totals, the raw death events and the full
                  combatant talent trees for all 5 players at once.
  3. export     - flatten everything into data/mythic_runs.csv (one row per
                  player per run) for the Streamlit dashboard.

Quota strategy: queries are batched via GraphQL aliases (~12 sub-queries per
HTTP request), the live point spend is tracked on every response, and the
process sleeps until the hourly window resets when the budget is nearly gone.

Anonymous rankings entries (report code hidden by the logger) cannot be
inspected and are skipped.  All regions are collected by default; pass e.g.
--regions US,EU to restrict.

--source ptr switches to the PTR zone (next season's dungeons).  WCL computes
no rankings for PTR zones, so stage 1 becomes a report-enumeration sweep
(reportData.reports) instead; everything downstream is identical and lands in
per-source files (mythic_runs_ptr.csv etc.).
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import pathlib
import random
import re
import shutil
import signal
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wcl_client import WCLClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
CHECKPOINTS = ROOT / "data" / "checkpoints"
RANKINGS_FILE = RAW / "rankings.jsonl"
SUMMARIES_DONE = PROCESSED / "summaries_done.txt"
PLAYERS_FILE = PROCESSED / "players.jsonl"
CSV_FILE = ROOT / "data" / "mythic_runs.csv"
HERO_MAP_FILE = ROOT / "data" / "hero_talent_map.json"

# GraphQL error messages that mean a report can never be fetched (vs. a
# transient server problem, which must NOT poison the checkpoint journal)
PERMANENT_ERROR = re.compile(
    r"do(es)? not exist|not found|permission|private|deleted|invalid report",
    re.IGNORECASE)

# Selectable data sources. "live" is the current season; "ptr" is the next
# season's PTR zone, which only has runs once Blizzard opens PTR log uploads.
ZONES = {
    "live": {
        "zone_id": 47, "label": "Midnight — Mythic+ Season 1",
        "brackets": list(range(11, 25)),   # keys 12 .. 25 (24 catches 25+)
        "encounters": {
            112526: "Algeth'ar Academy",
            12811: "Magisters' Terrace",
            12874: "Maisara Caverns",
            12915: "Nexus-Point Xenas",
            10658: "Pit of Saron",
            361753: "Seat of the Triumvirate",
            61209: "Skyreach",
            12805: "Windrunner Spire",
        },
    },
    "ptr": {
        "zone_id": 56, "label": "Midnight — Mythic+ Season 2 (PTR)",
        # WCL computes no rankings for PTR zones, so the sweep enumerates
        # reports directly (see sweep_reports) and brackets are irrelevant
        "brackets": [],
        "encounters": {
            62993: "Altar of Fangs",
            62825: "Den of Nalorakk",
            111762: "Kings' Rest",
            62813: "Murder Row",
            162521: "Ruby Life Pools",
            111877: "Temple of Sethraliss",
            62859: "The Blinding Vale",
            62923: "Voidscar Arena",
        },
    },
}
SOURCE = "live"          # set by --source; rebinds the globals below
ZONE_ID = ZONES["live"]["zone_id"]
ENCOUNTERS = ZONES["live"]["encounters"]
# WCL bracket N == keystone level N+1 for this zone (bracket 11 -> +12).
BRACKETS = ZONES["live"]["brackets"]
MAX_PAGE = 20  # the API 404s past page 20 (hasMorePages stays true)

RANK_BATCH = 10       # aliased fightRankings sub-queries per HTTP request
RANK_WORKERS = 8      # concurrent sweep workers, each walking its own cursors
SUMMARY_BATCH = 8     # aliased Summary-table sub-queries per HTTP request
SUMMARY_WORKERS = 14  # concurrent HTTP requests (server latency, not quota,
                      # is the per-request bottleneck: ~2s per table sub-query)
EXPORT_EVERY = 800    # export CSV every N summary batches

STOP = False


def _handle_stop(signum, frame):
    global STOP
    STOP = True
    print(f"\n[fetch] signal {signum} received; finishing current batch then exiting",
          flush=True)


def use_source(name: str) -> None:
    """Point the module's zone constants and file paths at one data source.
    'live' keeps the historical unsuffixed paths; other sources get their own
    journals, checkpoints and CSV so datasets never mix."""
    global SOURCE, ZONE_ID, ENCOUNTERS, BRACKETS
    global RANKINGS_FILE, SUMMARIES_DONE, PLAYERS_FILE, CSV_FILE
    cfg = ZONES[name]
    SOURCE, ZONE_ID = name, cfg["zone_id"]
    ENCOUNTERS, BRACKETS = cfg["encounters"], cfg["brackets"]
    sfx = "" if name == "live" else f"_{name}"
    RANKINGS_FILE = RAW / f"rankings{sfx}.jsonl"
    SUMMARIES_DONE = PROCESSED / f"summaries_done{sfx}.txt"
    PLAYERS_FILE = PROCESSED / f"players{sfx}.jsonl"
    CSV_FILE = ROOT / "data" / f"mythic_runs{sfx}.csv"


def bracket_to_key(bracket: int) -> int:
    return bracket + 1


def _iter_journal(path: pathlib.Path):
    """Yield parsed JSON lines, tolerating a torn trailing line (kill -9/OOM
    mid-write). Corrupt lines are skipped with a warning, never fatal."""
    if not path.exists():
        return
    bad = 0
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                bad += 1
    if bad:
        print(f"[journal] skipped {bad} corrupt line(s) in {path.name}", flush=True)


def _repair_tail(path: pathlib.Path) -> None:
    """Ensure an append journal ends with a newline so a torn last line can't
    merge with the next record."""
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open("rb") as fh:
        fh.seek(-1, os.SEEK_END)
        if fh.read(1) != b"\n":
            with path.open("ab") as afh:
                afh.write(b"\n")
            print(f"[journal] repaired torn tail in {path.name}", flush=True)


def restore_checkpoints() -> None:
    """Rehydrate journals from committed gzip snapshots (fresh clone case)."""
    sfx = "" if SOURCE == "live" else f"_{SOURCE}"
    pairs = [
        (CHECKPOINTS / f"rankings{sfx}.jsonl.gz", RANKINGS_FILE),
        (CHECKPOINTS / f"summaries_done{sfx}.txt.gz", SUMMARIES_DONE),
        (CHECKPOINTS / f"players{sfx}.jsonl.gz", PLAYERS_FILE),
    ]
    for gz, dst in pairs:
        if gz.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(gz, "rb") as src, dst.open("wb") as out:
                shutil.copyfileobj(src, out)
            print(f"[restore] {dst.name} restored from {gz}", flush=True)


def alias_error_map(errors: list) -> dict:
    """Map alias name ('a3') -> error message from a GraphQL errors array."""
    out = {}
    for e in errors or []:
        path = e.get("path") or []
        for part in path:
            if isinstance(part, str) and re.fullmatch(r"a\d+", part):
                out[part] = e.get("message", "")
                break
    return out


# --------------------------------------------------------------------------
# Stage 1: rankings sweep
# --------------------------------------------------------------------------

def load_sweep_state() -> dict:
    """Rebuild sweep cursors from the raw rankings journal."""
    state = {}  # (enc, bracket) -> {"last_page": int, "more": bool}
    for rec in _iter_journal(RANKINGS_FILE):
        k = (rec["enc"], rec["bracket"])
        cur = state.setdefault(k, {"last_page": 0, "more": True})
        if rec["page"] > cur["last_page"]:
            cur["last_page"] = rec["page"]
            cur["more"] = rec["more"]
    return state


def _sweep_shard(cursors: dict, out, out_lock, label: str) -> None:
    """Worker: run the wave loop over ONE shard of cursors, alias-batching
    within the shard. Journal writes are serialized via out_lock."""
    client = _worker_client()
    alias_fails: dict = {}
    while cursors and not STOP:
        batch = list(cursors.items())[:RANK_BATCH]
        parts = []
        for i, ((enc, br), page) in enumerate(batch):
            parts.append(
                f'a{i}: encounter(id: {enc}) '
                f'{{ fightRankings(metric: score, bracket: {br}, page: {page}) }}'
            )
        q = "{ worldData { " + " ".join(parts) + " } }"
        data = client.query(q, est_cost=1.5 * len(batch))
        world = (data.get("worldData") or {})
        lines = []
        for i, ((enc, br), page) in enumerate(batch):
            node = world.get(f"a{i}")
            fr = (node or {}).get("fightRankings")
            if not fr or fr.get("rankings") is None:
                alias_fails[(enc, br)] = alias_fails.get((enc, br), 0) + 1
                if alias_fails[(enc, br)] >= 3:
                    del cursors[(enc, br)]
                continue
            alias_fails.pop((enc, br), None)
            more = bool(fr.get("hasMorePages"))
            lines.append(json.dumps({
                "enc": enc, "bracket": br, "page": page, "more": more,
                "rankings": fr["rankings"],
            }))
            if more and page < MAX_PAGE:
                cursors[(enc, br)] = page + 1
            else:
                del cursors[(enc, br)]
        with out_lock:
            for ln in lines:
                out.write(ln + "\n")
            out.flush()
        print(f"[sweep {label}] {len(cursors)} cursors left | "
              f"{client.spent:.0f} pts this window", flush=True)


def sweep(client: WCLClient, brackets: list[int]) -> None:
    state = load_sweep_state()
    cursors = {}  # (enc, bracket) -> next page to fetch
    for enc in ENCOUNTERS:
        for br in brackets:
            cur = state.get((enc, br))
            if cur is None:
                cursors[(enc, br)] = 1
            elif cur["more"] and cur["last_page"] < MAX_PAGE:
                cursors[(enc, br)] = cur["last_page"] + 1
    total = len(ENCOUNTERS) * len(brackets) * MAX_PAGE
    print(f"[sweep] {len(cursors)} open cursors (max {total} pages total), "
          f"{RANK_WORKERS} parallel shards", flush=True)
    if not cursors:
        return

    RAW.mkdir(parents=True, exist_ok=True)
    _repair_tail(RANKINGS_FILE)
    out = RANKINGS_FILE.open("a")
    out_lock = threading.Lock()
    # interleave cursors across shards so deep brackets spread out
    items = list(cursors.items())
    shards = [dict(items[w::RANK_WORKERS]) for w in range(RANK_WORKERS)]
    shards = [s for s in shards if s]
    with ThreadPoolExecutor(max_workers=len(shards)) as pool:
        futs = [pool.submit(_sweep_shard, s, out, out_lock, f"w{wi}")
                for wi, s in enumerate(shards)]
        for f in futs:
            f.result()
    out.close()


# --------------------------------------------------------------------------
# Stage 1b: report-enumeration sweep (PTR)
# --------------------------------------------------------------------------
# WCL never computes rankings for PTR zones, so fightRankings comes back empty
# even though thousands of PTR reports exist and are browsable on the website.
# Instead: paginate the zone's report list, pull each report's fight list, and
# journal the completed keystone fights in the same record shape the rankings
# sweep produces — the summaries and export stages then run unchanged.

REPORT_PAGE_BATCH = 8    # aliased reports() pages per HTTP request
REPORT_FIGHT_BATCH = 15  # aliased report fight-list sub-queries per request


def _swept_codes() -> set[str]:
    return {rec["code"] for rec in _iter_journal(RANKINGS_FILE) if "code" in rec}


def _list_zone_reports(client: WCLClient) -> list[str]:
    """Every report code uploaded to ZONE_ID, newest first."""
    codes, page, more = [], 1, True
    while more and not STOP:
        parts = []
        for i in range(REPORT_PAGE_BATCH):
            parts.append(
                f'a{i}: reports(zoneID: {ZONE_ID}, limit: 100, page: {page + i}) '
                f'{{ has_more_pages data {{ code }} }}')
        q = "{ reportData { " + " ".join(parts) + " } }"
        data = client.query(q, est_cost=float(REPORT_PAGE_BATCH))
        rd = data.get("reportData") or {}
        for i in range(REPORT_PAGE_BATCH):
            node = rd.get(f"a{i}")
            if not node:
                more = False
                break
            codes.extend(r["code"] for r in node["data"])
            if not node["has_more_pages"]:
                more = False
                break
        page += REPORT_PAGE_BATCH
    seen: set[str] = set()
    return [c for c in codes if not (c in seen or seen.add(c))]


def _report_fights_shard(codes: list[str], total: int, out, out_lock,
                         counters: Counter, label: str) -> None:
    """Worker: fetch fight lists for ONE shard of report codes and journal
    their completed keystone fights as synthesized rankings entries."""
    client = _worker_client()
    for i in range(0, len(codes), REPORT_FIGHT_BATCH):
        if STOP:
            break
        batch = codes[i:i + REPORT_FIGHT_BATCH]
        parts = []
        for j, code in enumerate(batch):
            parts.append(
                f'a{j}: report(code: "{code}") {{ code startTime '
                f'region {{ compactName }} '
                f'fights(killType: All) {{ id encounterID keystoneLevel '
                f'keystoneAffixes keystoneTime kill rating startTime endTime }} }}')
        q = "{ reportData { " + " ".join(parts) + " } }"
        try:
            data = client.query(q, est_cost=float(len(batch)))
        except RuntimeError as e:
            print(f"[reports {label}] batch failed (re-run to retry): {e}",
                  flush=True)
            continue
        rd = data.get("reportData") or {}
        lines = []
        for j, code in enumerate(batch):
            rep = rd.get(f"a{j}")
            if not rep:  # no journal record -> retried on the next run
                continue
            region = ((rep.get("region") or {}).get("compactName") or "").upper()
            base = rep.get("startTime") or 0
            by_enc: dict[int, list] = {}
            for f in rep.get("fights") or []:
                kl = f.get("keystoneLevel")
                if not kl or not f.get("kill"):
                    continue  # wipe / abandoned key
                if f["encounterID"] not in ENCOUNTERS:
                    with out_lock:
                        counters[f"enc:{f['encounterID']}"] += 1
                    continue
                dur = f.get("keystoneTime") or \
                    ((f.get("endTime") or 0) - (f.get("startTime") or 0))
                rating = f.get("rating")  # in-game M+ rating from the log
                by_enc.setdefault(f["encounterID"], []).append({
                    "report": {"code": code, "fightID": f["id"]},
                    "server": {"region": region},
                    "bracketData": kl,
                    "duration": dur,
                    "score": round(rating, 2) if rating else None, "medal": None,
                    "affixes": f.get("keystoneAffixes") or [],
                    "startTime": base + (f.get("startTime") or 0),
                })
            recs = [{"code": code, "enc": enc, "bracket": 0, "page": 0,
                     "more": False, "rankings": entries}
                    for enc, entries in by_enc.items()]
            if not recs:  # marker so a fight-less report isn't re-fetched
                recs = [{"code": code, "enc": -1, "bracket": -1, "page": 0,
                         "more": False, "rankings": []}]
            lines.extend(json.dumps(r) for r in recs)
            with out_lock:
                counters["reports"] += 1
                counters["runs"] += sum(len(e) for e in by_enc.values())
        with out_lock:
            for ln in lines:
                out.write(ln + "\n")
            out.flush()
            done, runs = counters["reports"], counters["runs"]
        print(f"[reports {label}] {done}/{total} reports | {runs} completed "
              f"keys | {client.spent:.0f} pts this window", flush=True)


def sweep_reports(client: WCLClient) -> None:
    done = _swept_codes()
    print(f"[reports] listing zone {ZONE_ID} reports "
          f"({len(done)} already swept)...", flush=True)
    all_codes = _list_zone_reports(client)
    todo = [c for c in all_codes if c not in done]
    print(f"[reports] {len(all_codes)} reports in zone, {len(todo)} new to "
          f"scan, {RANK_WORKERS} parallel shards", flush=True)
    if not todo:
        return
    RAW.mkdir(parents=True, exist_ok=True)
    _repair_tail(RANKINGS_FILE)
    out = RANKINGS_FILE.open("a")
    out_lock = threading.Lock()
    counters: Counter = Counter()
    shards = [todo[w::RANK_WORKERS] for w in range(RANK_WORKERS)]
    shards = [s for s in shards if s]
    with ThreadPoolExecutor(max_workers=len(shards)) as pool:
        futs = [pool.submit(_report_fights_shard, s, len(todo), out, out_lock,
                            counters, f"w{wi}")
                for wi, s in enumerate(shards)]
        for f in futs:
            f.result()
    out.close()
    other = {k: v for k, v in counters.items() if k.startswith("enc:")}
    if other:
        print(f"[reports] skipped fights on encounters outside the "
              f"{len(ENCOUNTERS)}-dungeon pool: {other}", flush=True)


# --------------------------------------------------------------------------
# Stage 2: report summaries
# --------------------------------------------------------------------------

def load_fights(regions: set[str] | None) -> dict:
    """Unique public runs discovered by the sweep, keyed by report:fightID.

    Entries with a *known* region outside `regions` are dropped.  Entries with
    an empty region (very common: ~2/3 of public runs have no server tag on
    the ranking) are KEPT — their true per-player regions are only knowable
    from the report itself and end up in the CSV's `region` column.
    """
    fights = {}
    anon = 0
    for rec in _iter_journal(RANKINGS_FILE):
        for r in rec["rankings"]:
            code = (r.get("report") or {}).get("code") or ""
            fid = (r.get("report") or {}).get("fightID")
            if not code or fid is None:
                anon += 1
                continue
            region = ((r.get("server") or {}).get("region") or "").upper()
            if regions and region and region not in regions:
                continue
            key = f"{code}:{fid}"
            if key in fights:
                continue
            fights[key] = {
                "code": code, "fid": fid, "enc": rec["enc"],
                "dungeon": ENCOUNTERS.get(rec["enc"], str(rec["enc"])),
                "key_level": r.get("bracketData", bracket_to_key(rec["bracket"])),
                "rank_duration_ms": r.get("duration"),
                "score": r.get("score"), "medal": r.get("medal"),
                "affixes": r.get("affixes") or [],
                "region": region,
                "start_time": r.get("startTime"),
            }
    load_fights.anon_skipped = anon
    return fights


def load_done() -> set[str]:
    done = set()
    if SUMMARIES_DONE.exists():
        with SUMMARIES_DONE.open() as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                # a torn line has no status column: treat as not-done
                if len(parts) >= 2 and ":" in parts[0]:
                    done.add(parts[0])
    return done


class HeroResolver:
    def __init__(self):
        m = json.loads(HERO_MAP_FILE.read_text())
        self.names = {int(k): v for k, v in m["subtree_names"].items()}
        self.node_to_subtree = {int(k): v for k, v in m["node_to_subtree"].items()}
        self.entry_to_subtree = {int(k): v for k, v in m["entry_to_subtree"].items()}

    def resolve(self, talent_tree) -> str:
        if not isinstance(talent_tree, list) or not talent_tree:
            return "Unknown"
        votes = Counter()
        for t in talent_tree:
            sub = self.node_to_subtree.get(t.get("nodeID")) \
                or self.entry_to_subtree.get(t.get("id"))
            if sub:
                votes[sub] += 1
        if not votes:
            return "Unknown"
        return self.names[votes.most_common(1)[0][0]]


def parse_summary(fight: dict, table: dict, hero: HeroResolver) -> list[dict]:
    data = table.get("data") if isinstance(table, dict) else None
    if not isinstance(data, dict):
        raise ValueError("no summary data")
    details = data.get("playerDetails")
    if not isinstance(details, dict):
        raise ValueError("no playerDetails")

    total_time_ms = data.get("totalTime") or fight.get("rank_duration_ms") or 0
    if total_time_ms <= 0:
        raise ValueError("no usable duration")
    seconds = total_time_ms / 1000.0

    damage = {d.get("id"): d.get("total", 0)
              for d in data.get("damageDone") or [] if isinstance(d, dict)}
    deaths = Counter(e.get("id")
                     for e in data.get("deathEvents") or [] if isinstance(e, dict))

    rows = []
    for role_key, role in (("tanks", "Tank"), ("healers", "Healer"), ("dps", "DPS")):
        for p in details.get(role_key) or []:
            ci = p.get("combatantInfo")
            tree = ci.get("talentTree") if isinstance(ci, dict) else None
            specs = p.get("specs") or []
            icon = p.get("icon") or ""
            spec = specs[0] if specs else (icon.split("-", 1)[1] if "-" in icon else "")
            rows.append({
                "character": p.get("name"),
                "server": p.get("server"),
                "region": p.get("region") or fight["region"],
                "class": p.get("type"),
                "spec": spec,
                "hero_talent": hero.resolve(tree),
                "role": role,
                "dungeon": fight["dungeon"],
                "key_level": fight["key_level"],
                "duration_s": round(seconds, 1),
                "damage_done": damage.get(p.get("id"), 0),
                "dps": round(damage.get(p.get("id"), 0) / seconds, 1),
                "deaths": int(deaths.get(p.get("id"), 0)),
                "item_level": p.get("maxItemLevel"),
                "score": fight["score"],
                "medal": fight["medal"],
                "affixes": "|".join(str(a) for a in fight["affixes"]),
                "report_code": fight["code"],
                "fight_id": fight["fid"],
                "started_at": fight["start_time"],
            })
    if not rows:
        raise ValueError("no players parsed")
    return rows


_tls = threading.local()


def _worker_client() -> WCLClient:
    if not hasattr(_tls, "client"):
        _tls.client = WCLClient(verbose=True)
    return _tls.client


def _fetch_batch(batch: list[dict]):
    """Worker: fetch one aliased batch of Summary tables.

    Returns (batch, reportData|None, alias->error map, points).  reportData
    None means the whole request failed after retries -> requeue, don't
    journal anything.
    """
    client = _worker_client()
    parts = []
    for i, f in enumerate(batch):
        parts.append(
            f'a{i}: report(code: "{f["code"]}") '
            f'{{ table(fightIDs: [{f["fid"]}], dataType: Summary) }}'
        )
    q = "{ reportData { " + " ".join(parts) + " } }"
    try:
        data = client.query(q, est_cost=2.6 * len(batch))
    except RuntimeError as e:
        print(f"[summaries] batch failed, will requeue: {e}", flush=True)
        return batch, None, {}, client.spent
    return (batch, data.get("reportData") or {},
            alias_error_map(data.get("_errors")), client.spent)


def fetch_summaries(regions: set[str] | None, limit: int | None = None) -> None:
    fights = load_fights(regions)
    done = load_done()
    pending = [f for k, f in fights.items() if k not in done]
    # US/EU-tagged runs first, unknown-region after; shuffled within each
    # group so partial datasets stay balanced across dungeons/brackets.
    rnd = random.Random(42)
    known = [f for f in pending if f["region"]]
    unknown = [f for f in pending if not f["region"]]
    rnd.shuffle(known)
    rnd.shuffle(unknown)
    pending = known + unknown
    if limit:
        pending = pending[:limit]
    print(f"[summaries] {len(fights)} public runs discovered "
          f"({load_fights.anon_skipped} anonymous entries skipped), "
          f"{len(done)} already fetched, {len(pending)} to go "
          f"({len(known)} region-tagged, {len(unknown)} untagged)", flush=True)

    PROCESSED.mkdir(parents=True, exist_ok=True)
    _repair_tail(SUMMARIES_DONE)
    _repair_tail(PLAYERS_FILE)
    done_fh = SUMMARIES_DONE.open("a")
    rows_fh = PLAYERS_FILE.open("a")
    n_done, t0 = 0, time.time()
    retry_round = 0
    while pending and not STOP and retry_round <= 2:
        if retry_round:
            print(f"[summaries] retry round {retry_round}: "
                  f"{len(pending)} transient failures", flush=True)
        batches = [pending[i:i + SUMMARY_BATCH]
                   for i in range(0, len(pending), SUMMARY_BATCH)]
        transient: list[dict] = []

        # Workers only do HTTP; all journal writes happen on this thread.
        with ThreadPoolExecutor(max_workers=SUMMARY_WORKERS) as pool:
            it = iter(batches)
            futures = {pool.submit(_fetch_batch, b) for b in
                       (next(it, None) for _ in range(SUMMARY_WORKERS * 2)) if b}
            while futures:
                fut = next(as_completed(futures))
                futures.remove(fut)
                batch, rep, errmap, spent = fut.result()
                if rep is None:
                    transient.extend(batch)  # whole request failed
                else:
                    for i, f in enumerate(batch):
                        key = f"{f['code']}:{f['fid']}"
                        node = rep.get(f"a{i}")
                        if not node or not node.get("table"):
                            # only a *permanent* GraphQL error may poison the
                            # journal; anything ambiguous is retried later
                            msg = errmap.get(f"a{i}", "")
                            if msg and PERMANENT_ERROR.search(msg):
                                done_fh.write(f"{key}\tFAILED\t{msg[:100]}\n")
                            else:
                                transient.append(f)
                            continue
                        try:
                            rows = parse_summary(f, node["table"],
                                                 fetch_summaries.hero)
                        except (ValueError, KeyError, TypeError,
                                AttributeError) as e:
                            done_fh.write(f"{key}\tFAILED\t{e}\n")
                            continue
                        for row in rows:
                            rows_fh.write(
                                json.dumps(row, ensure_ascii=False) + "\n")
                        done_fh.write(f"{key}\tOK\n")
                    # rows must hit disk before their OK markers: a kill
                    # between the flushes then costs a refetch (deduped at
                    # export), never silent row loss
                    rows_fh.flush()
                    done_fh.flush()
                n_done += 1
                if n_done % 20 == 0:
                    rate = n_done * SUMMARY_BATCH / max(time.time() - t0, 1)
                    left = (len(batches) - n_done) * SUMMARY_BATCH
                    print(f"[summaries] ~{max(left, 0)} left | "
                          f"{rate:.1f} runs/s | "
                          f"{spent:.0f} pts this window", flush=True)
                if n_done % EXPORT_EVERY == 0:
                    export()
                if not STOP:
                    nxt = next(it, None)
                    if nxt:
                        futures.add(pool.submit(_fetch_batch, nxt))
        pending = transient
        retry_round += 1
    if pending:
        print(f"[summaries] {len(pending)} runs still unfetched "
              f"(transient failures; re-run to retry)", flush=True)
    done_fh.close()
    rows_fh.close()


fetch_summaries.hero = None


# --------------------------------------------------------------------------
# Stage 3: export
# --------------------------------------------------------------------------

def export() -> None:
    if not PLAYERS_FILE.exists():
        print("[export] no player rows yet", flush=True)
        return
    import pandas as pd
    rows = list(_iter_journal(PLAYERS_FILE))  # tolerates a torn trailing line
    if not rows:
        print("[export] no player rows yet", flush=True)
        return
    df = pd.DataFrame(rows)
    before = len(df)
    df = df.drop_duplicates(subset=["report_code", "fight_id", "character", "server"])
    # scores live in the rankings journal, which can be re-swept much more
    # cheaply than the summaries; overlay so late-arriving scores (e.g. PTR
    # fight ratings) reach rows fetched before the score existed
    smap = {(f["code"], f["fid"]): f["score"]
            for f in load_fights(None).values() if f.get("score") is not None}
    if smap:
        df["score"] = [smap.get((c, f), s) for c, f, s in
                       zip(df["report_code"], df["fight_id"], df["score"])]
    tmp = CSV_FILE.with_suffix(".csv.tmp")
    df.to_csv(tmp, index=False)
    os.replace(tmp, CSV_FILE)  # atomic: the live dashboard never sees a torn file
    print(f"[export] {len(df)} player-rows ({before - len(df)} dupes dropped) "
          f"across {df[['report_code', 'fight_id']].drop_duplicates().shape[0]} runs "
          f"-> {CSV_FILE}", flush=True)


def status(regions: set[str] | None) -> None:
    fights = load_fights(regions)
    done = load_done()
    if SOURCE == "live":
        state = load_sweep_state()
        open_cursors = sum(1 for v in state.values()
                           if v["more"] and v["last_page"] < MAX_PAGE)
        print(f"sweep:     {len(state)} cursors touched, "
              f"{len(ENCOUNTERS) * len(BRACKETS) - len(state)} untouched, "
              f"{open_cursors} open")
    else:
        print(f"sweep:     {len(_swept_codes())} reports scanned")
    print(f"fights:    {len(fights)} unique public runs "
          f"({getattr(load_fights, 'anon_skipped', '?')} anonymous skipped)")
    print(f"summaries: {len(done)} fetched ({len(fights) - len(done & set(fights))} remaining)")
    if PLAYERS_FILE.exists():
        n = sum(1 for _ in PLAYERS_FILE.open())
        print(f"rows:      {n} player rows in {PLAYERS_FILE}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage", choices=["all", "sweep", "summaries", "export", "status"],
                    default="all")
    ap.add_argument("--source", choices=sorted(ZONES), default="live",
                    help="which zone to collect: live season rankings or the "
                         "PTR zone (report enumeration; separate journals/CSV)")
    ap.add_argument("--regions", default="ALL",
                    help='ALL (default) or a comma-separated allow-list, '
                         'e.g. US,EU')
    ap.add_argument("--brackets", default="11-24",
                    help="bracket range lo-hi (bracket = key level - 1)")
    ap.add_argument("--limit-fights", type=int, default=None,
                    help="stop after N summary fetches (for testing)")
    ap.add_argument("--resweep", action="store_true",
                    help="discard the rankings journal (and its checkpoint "
                         "snapshot) to re-scan the leaderboards; already-"
                         "fetched summaries are kept and deduped")
    args = ap.parse_args()
    use_source(args.source)

    regions = None if args.regions.upper() == "ALL" else \
        {r.strip().upper() for r in args.regions.split(",")}
    lo, hi = (int(x) for x in args.brackets.split("-"))
    brackets = [b for b in BRACKETS if lo <= b <= hi]

    if args.resweep:
        RANKINGS_FILE.unlink(missing_ok=True)
        (CHECKPOINTS / f"{RANKINGS_FILE.name}.gz").unlink(missing_ok=True)
        print("[resweep] rankings journal cleared; leaderboards will be re-scanned",
              flush=True)
    restore_checkpoints()
    if args.stage == "status":
        status(regions)
        return
    if args.stage == "export":
        export()
        return

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)
    fetch_summaries.hero = HeroResolver()
    client = WCLClient()

    if args.stage in ("all", "sweep"):
        if SOURCE == "live":
            sweep(client, brackets)
        else:
            sweep_reports(client)
    if args.stage in ("all", "summaries") and not STOP:
        fetch_summaries(regions, args.limit_fights)
    export()
    print(f"[done] {client.requests_made} HTTP requests, "
          f"{client.spent:.0f} points spent this window", flush=True)


if __name__ == "__main__":
    main()
