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
inspected and are skipped.  Region defaults to US+EU; use --regions ALL to
include KR/TW.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import signal
import sys
import time
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wcl_client import WCLClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
RANKINGS_FILE = RAW / "rankings.jsonl"
SUMMARIES_DONE = PROCESSED / "summaries_done.txt"
PLAYERS_FILE = PROCESSED / "players.jsonl"
CSV_FILE = ROOT / "data" / "mythic_runs.csv"
HERO_MAP_FILE = ROOT / "data" / "hero_talent_map.json"

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
SUMMARY_BATCH = 12    # aliased Summary-table sub-queries per HTTP request
EXPORT_EVERY = 100    # export CSV every N summary batches

STOP = False


def _handle_stop(signum, frame):
    global STOP
    STOP = True
    print(f"\n[fetch] signal {signum} received; finishing current batch then exiting",
          flush=True)


def bracket_to_key(bracket: int) -> int:
    return bracket + 1


# --------------------------------------------------------------------------
# Stage 1: rankings sweep
# --------------------------------------------------------------------------

def load_sweep_state() -> dict:
    """Rebuild sweep cursors from the raw rankings journal."""
    state = {}  # (enc, bracket) -> {"last_page": int, "more": bool}
    if RANKINGS_FILE.exists():
        with RANKINGS_FILE.open() as fh:
            for line in fh:
                rec = json.loads(line)
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
    out = RANKINGS_FILE.open("a")
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
                # page beyond what the API serves -> cursor exhausted
                del cursors[(enc, br)]
                continue
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
    """Unique public runs discovered by the sweep, keyed by report:fightID."""
    fights = {}
    anon = 0
    if not RANKINGS_FILE.exists():
        return fights
    with RANKINGS_FILE.open() as fh:
        for line in fh:
            rec = json.loads(line)
            for r in rec["rankings"]:
                code = (r.get("report") or {}).get("code") or ""
                fid = (r.get("report") or {}).get("fightID")
                if not code or fid is None:
                    anon += 1
                    continue
                region = ((r.get("server") or {}).get("region") or "").upper()
                if regions and region not in regions:
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
                done.add(line.strip().split("\t")[0])
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


def fetch_summaries(client: WCLClient, regions: set[str] | None,
                    limit: int | None = None) -> None:
    fights = load_fights(regions)
    done = load_done()
    pending = [f for k, f in fights.items() if k not in done]
    random.Random(42).shuffle(pending)  # balanced partial coverage
    if limit:
        pending = pending[:limit]
    print(f"[summaries] {len(fights)} public runs discovered "
          f"({load_fights.anon_skipped} anonymous entries skipped), "
          f"{len(done)} already fetched, {len(pending)} to go", flush=True)

    PROCESSED.mkdir(parents=True, exist_ok=True)
    done_fh = SUMMARIES_DONE.open("a")
    rows_fh = PLAYERS_FILE.open("a")
    batches = 0
    t0 = time.time()
    while pending and not STOP:
        batch, pending = pending[:SUMMARY_BATCH], pending[SUMMARY_BATCH:]
        parts = []
        for i, f in enumerate(batch):
            parts.append(
                f'a{i}: report(code: "{f["code"]}") '
                f'{{ table(fightIDs: [{f["fid"]}], dataType: Summary) }}'
            )
        q = "{ reportData { " + " ".join(parts) + " } }"
        data = client.query(q, est_cost=1.2 * len(batch))
        rep = data.get("reportData") or {}
        for i, f in enumerate(batch):
            key = f"{f['code']}:{f['fid']}"
            node = rep.get(f"a{i}")
            try:
                if not node or not node.get("table"):
                    raise ValueError("report unavailable")
                rows = parse_summary(f, node["table"], fetch_summaries.hero)
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                done_fh.write(f"{key}\tFAILED\t{e}\n")
                continue
            for row in rows:
                rows_fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            done_fh.write(f"{key}\tOK\n")
        done_fh.flush()
        rows_fh.flush()
        batches += 1
        if batches % 10 == 0:
            rate = batches * SUMMARY_BATCH / max(time.time() - t0, 1)
            print(f"[summaries] {len(pending)} left | {rate:.1f} runs/s | "
                  f"{client.spent:.0f}/{client.limit:.0f} pts", flush=True)
        if batches % EXPORT_EVERY == 0:
            export()
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
    df = pd.read_json(PLAYERS_FILE, lines=True)
    if df.empty:
        print("[export] no player rows yet", flush=True)
        return
    before = len(df)
    df = df.drop_duplicates(subset=["report_code", "fight_id", "character"])
    df.to_csv(CSV_FILE, index=False)
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
    ap.add_argument("--regions", default="US,EU",
                    help='comma-separated regions, or ALL (default: US,EU)')
    ap.add_argument("--brackets", default="11-24",
                    help="bracket range lo-hi (bracket = key level - 1)")
    ap.add_argument("--limit-fights", type=int, default=None,
                    help="stop after N summary fetches (for testing)")
    args = ap.parse_args()

    regions = None if args.regions.upper() == "ALL" else \
        {r.strip().upper() for r in args.regions.split(",")}
    lo, hi = (int(x) for x in args.brackets.split("-"))
    brackets = [b for b in BRACKETS if lo <= b <= hi]

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
        fetch_summaries(client, regions, args.limit_fights)
    export()
    print(f"[done] {client.requests_made} HTTP requests, "
          f"{client.spent:.0f} points spent this window", flush=True)


if __name__ == "__main__":
    main()
