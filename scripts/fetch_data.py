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

ZONE_ID = 47  # Midnight -> Mythic+ Season 1
ENCOUNTERS = {
    112526: "Algeth'ar Academy",
    12811: "Magisters' Terrace",
    12874: "Maisara Caverns",
    12915: "Nexus-Point Xenas",
    10658: "Pit of Saron",
    361753: "Seat of the Triumvirate",
    61209: "Skyreach",
    12805: "Windrunner Spire",
}
# WCL bracket N == keystone level N+1 for this zone (bracket 11 -> +12).
BRACKETS = list(range(11, 25))  # keys 12 .. 25 (bracket 24 catches 25+)
MAX_PAGE = 20  # the API 404s past page 20 (hasMorePages stays true)

RANK_BATCH = 10       # aliased fightRankings sub-queries per HTTP request
SUMMARY_BATCH = 8     # aliased Summary-table sub-queries per HTTP request
SUMMARY_WORKERS = 8   # concurrent HTTP requests (server latency, not quota,
                      # is the per-request bottleneck: ~2s per table sub-query)
EXPORT_EVERY = 800    # export CSV every N summary batches

STOP = False


def _handle_stop(signum, frame):
    global STOP
    STOP = True
    print(f"\n[fetch] signal {signum} received; finishing current batch then exiting",
          flush=True)


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
    pairs = [
        (CHECKPOINTS / "rankings.jsonl.gz", RANKINGS_FILE),
        (CHECKPOINTS / "summaries_done.txt.gz", SUMMARIES_DONE),
        (CHECKPOINTS / "players.jsonl.gz", PLAYERS_FILE),
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
    print(f"[sweep] {len(cursors)} open cursors (max {total} pages total)", flush=True)

    RAW.mkdir(parents=True, exist_ok=True)
    _repair_tail(RANKINGS_FILE)
    out = RANKINGS_FILE.open("a")
    alias_fails: dict = {}  # (enc, bracket) -> consecutive null-alias count
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
        for i, ((enc, br), page) in enumerate(batch):
            node = world.get(f"a{i}")
            fr = (node or {}).get("fightRankings")
            if not fr or fr.get("rankings") is None:
                # could be end-of-pages OR a transient per-alias error:
                # retry a couple of times before declaring the cursor done
                alias_fails[(enc, br)] = alias_fails.get((enc, br), 0) + 1
                if alias_fails[(enc, br)] >= 3:
                    del cursors[(enc, br)]
                continue
            alias_fails.pop((enc, br), None)
            more = bool(fr.get("hasMorePages"))
            out.write(json.dumps({
                "enc": enc, "bracket": br, "page": page, "more": more,
                "rankings": fr["rankings"],
            }) + "\n")
            if more and page < MAX_PAGE:
                cursors[(enc, br)] = page + 1
            else:
                del cursors[(enc, br)]
        out.flush()
        print(f"[sweep] {len(cursors)} cursors left | "
              f"{client.spent:.0f}/{client.limit:.0f} pts", flush=True)
    out.close()


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
    tmp = CSV_FILE.with_suffix(".csv.tmp")
    df.to_csv(tmp, index=False)
    os.replace(tmp, CSV_FILE)  # atomic: the live dashboard never sees a torn file
    print(f"[export] {len(df)} player-rows ({before - len(df)} dupes dropped) "
          f"across {df[['report_code', 'fight_id']].drop_duplicates().shape[0]} runs "
          f"-> {CSV_FILE}", flush=True)


def status(regions: set[str] | None) -> None:
    state = load_sweep_state()
    open_cursors = sum(1 for v in state.values() if v["more"] and v["last_page"] < MAX_PAGE)
    fights = load_fights(regions)
    done = load_done()
    print(f"sweep:     {len(state)} cursors touched, "
          f"{len(ENCOUNTERS) * len(BRACKETS) - len(state)} untouched, {open_cursors} open")
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
    ap.add_argument("--regions", default="ALL",
                    help='ALL (default) or a comma-separated allow-list, '
                         'e.g. US,EU')
    ap.add_argument("--brackets", default="11-24",
                    help="bracket range lo-hi (bracket = key level - 1)")
    ap.add_argument("--limit-fights", type=int, default=None,
                    help="stop after N summary fetches (for testing)")
    args = ap.parse_args()

    regions = None if args.regions.upper() == "ALL" else \
        {r.strip().upper() for r in args.regions.split(",")}
    lo, hi = (int(x) for x in args.brackets.split("-"))
    brackets = [b for b in BRACKETS if lo <= b <= hi]

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
        sweep(client, brackets)
    if args.stage in ("all", "summaries") and not STOP:
        fetch_summaries(regions, args.limit_fights)
    export()
    print(f"[done] {client.requests_made} HTTP requests, "
          f"{client.spent:.0f} points spent this window", flush=True)


if __name__ == "__main__":
    main()
