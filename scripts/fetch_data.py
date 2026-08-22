#!/usr/bin/env python3
"""Midnight Season 2 Mythic+ data collection pipeline.

Stages (all checkpointed; safe to kill and re-run at any point):

  1. sweep      - paginate fightRankings for every dungeon x keystone bracket
                  (keys 10-25+) until the API stops serving pages (20-page cap
                  per bracket).  One rankings entry == one unique dungeon run,
                  so this is 5x cheaper than characterRankings which repeats
                  each run once per player.
  2. summaries  - for every ranked run with a public report, fetch the report
                  Summary table (ONE ~1-point query per run) which contains
                  per-player damage totals, the raw death events and the full
                  combatant talent trees for all 5 players at once.
  3. export     - flatten everything into data/mythic_runs.csv.gz (one row
                  per player per run), which the site build packs for the
                  static dashboard.

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
# stored gzipped: the live CSV crossed GitHub's hard 100 MB blob limit,
# and pandas reads/writes .csv.gz transparently
CSV_FILE = ROOT / "data" / "mythic_runs.csv.gz"
HERO_MAP_FILE = ROOT / "data" / "hero_talent_map.json"

# GraphQL error messages that mean a report can never be fetched (vs. a
# transient server problem, which must NOT poison the checkpoint journal)
PERMANENT_ERROR = re.compile(
    r"do(es)? not exist|not found|permission|private|deleted|invalid report",
    re.IGNORECASE)

# Midnight Mythic+ Season 2 (WCL zone 55). Encounter ids are the live zone's,
# not the beta/PTR zone's - they differ even though the dungeons match.
ZONE_ID = 55
ZONE_LABEL = "Midnight — Mythic+ Season 2"
ENCOUNTERS = {
    12993: "Altar of Fangs",
    12825: "Den of Nalorakk",
    61762: "Kings' Rest",
    12813: "Murder Row",
    112521: "Ruby Life Pools",
    61877: "Temple of Sethraliss",
    12859: "The Blinding Vale",
    12923: "Voidscar Arena",
}
# WCL bracket N == keystone level N+1 (bracket 9 -> +10). Zone 55 advertises
# brackets for keys 2..30, and every one of them is swept: the dashboard opens
# on +10 and up, but the whole range stays selectable.
BRACKETS = list(range(1, 30))     # keys 2 .. 30
MAX_PAGE = 20  # the API 404s past page 20 (hasMorePages stays true)
# Keys below +10 are levelling/gearing content with effectively bottomless
# leaderboards - a full 20-page sweep there would be 5x the whole dataset for
# a range the default view does not even show. They get a shallower slice of
# the same leaderboard instead.
LOW_KEY_MAX_PAGE = 4              # 200 runs per dungeon x key below +10


def page_cap(bracket: int) -> int:
    return MAX_PAGE if bracket_to_key(bracket) >= 10 else LOW_KEY_MAX_PAGE

# Two uploads of one run agree on dungeon, key level and keystone clock, and
# their absolute start times land within a couple of seconds of each other
# (measured: median 0.0s, p90 2.1s). Runs that merely share the first three
# are a real 5% of matches, so the start time has to break them apart.
DEDUPE_START_GAP_MS = 120_000

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
    seed_from_csv()


def seed_from_csv() -> None:
    """Rebuild the player journal from the committed CSV export.

    data/raw and data/processed are gitignored, so a fresh clone - or an
    hourly CI run whose journal cache was evicted - starts with no memory of
    which summaries were already fetched, and would re-fetch every run in the
    season. The committed CSV is a faithful copy of the journal (identical
    columns, plus the derived keystone clock), so it can seed both the player
    rows and the fetched-set and the next sweep only pays for what is new.
    """
    if PLAYERS_FILE.exists() or not CSV_FILE.exists():
        return
    import pandas as pd
    df = pd.read_csv(CSV_FILE)
    cols = [c for c in df.columns if c != "keystone_s"]
    PLAYERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PLAYERS_FILE.open("w") as fh:
        for rec in df[cols].to_dict("records"):
            fh.write(json.dumps({k: (None if pd.isna(v) else v)
                                 for k, v in rec.items()},
                                ensure_ascii=False) + "\n")
    if not SUMMARIES_DONE.exists():
        pairs = df[["report_code", "fight_id"]].drop_duplicates()
        with SUMMARIES_DONE.open("w") as fh:
            for c, f in zip(pairs["report_code"], pairs["fight_id"]):
                fh.write(f"{c}:{f}\tOK\n")
        print(f"[restore] {len(pairs)} fetched runs seeded from {CSV_FILE.name}",
              flush=True)
    print(f"[restore] {len(df)} player rows seeded from {CSV_FILE.name}",
          flush=True)


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
            if more and page < page_cap(br):
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
            elif cur["more"] and cur["last_page"] < page_cap(br):
                cursors[(enc, br)] = cur["last_page"] + 1
    total = len(ENCOUNTERS) * sum(page_cap(b) for b in brackets)
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


def dedupe_fights(fights: dict) -> dict:
    """Drop re-uploads of the same run BEFORE paying for their summaries.

    Every member of a group can upload the same key, and each upload is a
    separate report the summary stage would fetch in full. Collapsing them
    here rather than at export time is worth ~20% of the entire summary spend.
    """
    groups = {}
    for k, f in fights.items():
        ks = f.get("rank_duration_ms") or 0
        if not ks:                      # no clock -> weak signature, never merge
            groups[("solo", k)] = [(k, f)]
            continue
        groups.setdefault((f["enc"], f["key_level"], round(ks / 100)),
                          []).append((k, f))
    keep = {}
    dropped = 0
    for members in groups.values():
        if len(members) == 1:
            keep[members[0][0]] = members[0][1]
            continue
        # same dungeon/key/clock is not proof on its own; split on start time
        members.sort(key=lambda kv: kv[1].get("start_time") or 0)
        cluster = [members[0]]
        for k, f in members[1:]:
            prev = cluster[-1][1].get("start_time") or 0
            if (f.get("start_time") or 0) - prev <= DEDUPE_START_GAP_MS:
                cluster.append((k, f))
            else:
                dropped += len(cluster) - 1
                keep.update([_pick(cluster)])
                cluster = [(k, f)]
        dropped += len(cluster) - 1
        keep.update([_pick(cluster)])
    dedupe_fights.dropped = dropped
    return keep


def _pick(cluster):
    """One representative per run: prefer a copy already fetched, then the
    lowest code so the choice is stable across runs."""
    done = load_done.cache if hasattr(load_done, "cache") else set()
    return min(cluster, key=lambda kv: (kv[0] not in done, kv[0]))


## --------------------------------------------------------------------------
# Stage 2: report Summary tables
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
            prev = fights.get(key)
            if prev is not None:
                continue                      # first ranking entry wins
            fights[key] = {
                "code": code, "fid": fid, "enc": rec["enc"],
                "dungeon": ENCOUNTERS.get(rec["enc"], str(rec["enc"])),
                "key_level": r.get("bracketData", bracket_to_key(rec["bracket"])),
                "rank_duration_ms": r.get("duration"),
                "medal": r.get("medal"),
                "affixes": r.get("affixes") or [],
                "region": region or (prev or {}).get("region", ""),
                "start_time": r.get("startTime") or (prev or {}).get("start_time"),
            }
    load_fights.anon_skipped = anon
    if regions:
        fights = {k: f for k, f in fights.items()
                  if not f["region"] or f["region"] in regions}
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

    if not damage:
        # the dashboard measures damage and nothing else, so a summary table
        # without a damageDone section has nothing we can use
        raise ValueError("no damage data")

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
    load_done.cache = done          # lets _pick() prefer an already-fetched copy
    raw_n = len(fights)
    fights = dedupe_fights(fights)
    if raw_n != len(fights):
        print(f"[summaries] {raw_n} uploads -> {len(fights)} distinct runs "
              f"({dedupe_fights.dropped} re-uploads skipped before fetching, "
              f"{100 * dedupe_fights.dropped / raw_n:.0f}% of the spend saved)",
              flush=True)
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
    # medals live in the rankings journal, which can be re-swept much more
    # cheaply than the summaries; overlay so a late-arriving medal reaches rows
    # fetched before the journal carried one
    jmap = {(f["code"], f["fid"]): f for f in load_fights(None).values()}
    # The keystone clock (wall-time against the dungeon timer) differs from the
    # combat duration already stored, and is what "% under timer" needs. WCL's
    # zone report list only goes back so far, so keep a persistent map that
    # accumulates across sweeps instead of losing older runs on every resweep.
    ks_file = ROOT / "data" / "keystone_times.json"
    ks = json.loads(ks_file.read_text()) if ks_file.exists() else {}
    for (c, f), fight in jmap.items():
        ms = fight.get("rank_duration_ms")
        if ms:
            ks[f"{c}:{f}"] = round(ms / 1000, 1)
    tmp = ks_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ks, separators=(",", ":")))
    os.replace(tmp, ks_file)
    df["keystone_s"] = [ks.get(f"{c}:{f}", "")
                        for c, f in zip(df["report_code"], df["fight_id"])]

    # Several members of a group often upload the same fight, so one real run
    # appears under multiple report codes and gets counted repeatedly. Identify
    # a run by dungeon + key + keystone clock + its exact roster: the copies
    # agree on all four, while two genuinely different runs that happen to
    # share a clock do not (their rosters differ). Start timestamps are NOT
    # usable here — each uploader's report begins at a different moment, so
    # the same fight can be tens of seconds apart between copies.
    per_run = df.groupby(["report_code", "fight_id"]).agg(
        _dun=("dungeon", "first"), _key=("key_level", "first"),
        _ks=("keystone_s", "first"),
        _roster=("character", lambda s: "|".join(sorted(map(str, s)))),
        _n=("character", "size")).reset_index()
    per_run["_sig"] = (per_run["_dun"].astype(str) + "/"
                       + per_run["_key"].astype(str) + "/"
                       + per_run["_ks"].astype(str) + "/" + per_run["_roster"])
    # a run with no keystone clock has a weak signature, so never merge those
    weak = per_run["_ks"].astype(str).isin(["", "nan", "None"])
    canon = pd.concat([
        per_run[weak],
        (per_run[~weak].sort_values(["_sig", "_n", "report_code"],
                                    ascending=[True, False, True])
         .drop_duplicates("_sig")),
    ])
    if len(canon) < len(per_run):
        keep = set(zip(canon["report_code"], canon["fight_id"]))
        df = df[[(c, f) in keep
                 for c, f in zip(df["report_code"], df["fight_id"])]]
        print(f"[export] collapsed {len(per_run) - len(canon)} duplicate "
              f"uploads of the same fight", flush=True)
    if jmap:
        for col in ("medal",):
            df[col] = [
                (jmap.get((c, f), {}).get(col) if jmap.get((c, f), {}).get(col)
                 is not None else v)
                for c, f, v in zip(df["report_code"], df["fight_id"], df[col])]
    df = df.drop(columns=[c for c in ("score",) if c in df.columns])
    tmp = CSV_FILE.with_name(CSV_FILE.name + ".tmp")
    df.to_csv(tmp, index=False, compression="gzip")
    os.replace(tmp, CSV_FILE)  # atomic: the live dashboard never sees a torn file
    print(f"[export] {len(df)} player-rows ({before - len(df)} dupes dropped) "
          f"across {df[['report_code', 'fight_id']].drop_duplicates().shape[0]} runs "
          f"-> {CSV_FILE}", flush=True)


def status(regions: set[str] | None) -> None:
    fights = load_fights(regions)
    done = load_done()
    # _pick() prefers a copy we already fetched, but only if it can see the
    # fetched set. Without this, status picks different representatives than
    # the summary stage does and reports runs as outstanding whose data we
    # already hold -- a phantom backlog that never drains.
    load_done.cache = done
    state = load_sweep_state()
    open_cursors = sum(1 for (_, br), v in state.items()
                       if v["more"] and v["last_page"] < page_cap(br))
    print(f"sweep:     {len(state)} cursors touched, "
          f"{len(ENCOUNTERS) * len(BRACKETS) - len(state)} untouched, "
          f"{open_cursors} open")
    print(f"fights:    {len(fights)} unique public runs "
          f"({getattr(load_fights, 'anon_skipped', '?')} anonymous skipped)")
    ded = dedupe_fights(dict(fights))
    print(f"deduped:   {len(ded)} distinct runs "
          f"({getattr(dedupe_fights, 'dropped', 0)} re-uploads collapsed)")
    # against the DEDUPED set, which is what the summary stage will actually
    # fetch. Counting raw uploads never reaches zero -- the re-uploads are
    # skipped rather than fetched, so they are never marked done -- and any
    # caller watching for "0 remaining" would wait forever.
    print(f"summaries: {len(done)} fetched "
          f"({len(set(ded) - done)} remaining)")
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
    ap.add_argument("--brackets", default="1-29",
                    help="bracket range lo-hi (bracket = key level - 1)")
    ap.add_argument("--limit-fights", type=int, default=None,
                    help="stop after N summary fetches (for testing)")
    ap.add_argument("--resweep", action="store_true",
                    help="discard the rankings journal (and its checkpoint "
                         "snapshot) to re-scan the leaderboards; already-"
                         "fetched summaries are kept and deduped")
    args = ap.parse_args()

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
        sweep(client, brackets)
    if args.stage in ("all", "summaries") and not STOP:
        fetch_summaries(regions, args.limit_fights)
    export()
    print(f"[done] {client.requests_made} HTTP requests, "
          f"{client.spent:.0f} points spent this window", flush=True)


if __name__ == "__main__":
    main()
