#!/usr/bin/env python3
"""Raider.IO season-score collector.

Warcraft Logs gives us the score of each *run*; this gives the score of each
*player* -- their Mythic+ season total, which is the sum of their best run in
each of the eight dungeons and so lands around eight times a single run's
rating. The two are different quantities and the site shows them as such.

Raider.IO has no bulk character endpoint, so this is one request per character
against a population of ~150k. It is therefore incremental and time-boxed:
every run spends at most --budget-s seconds, journals what it learned, and the
next run picks up where this one stopped. Characters are fetched
most-logged-first so the metric is worth reading long before the sweep
finishes -- the players who appear in the most runs are the ones who move a
spec's average.

The journal is the unit of recovery. It is rewritten atomically and also
checkpointed mid-flight, because the job that hosts this can be killed at its
own timeout with no warning.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import os
import re
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RUNS_CSV = ROOT / "data" / "mythic_runs.csv.gz"
# The live journal lives inside data/processed because that is what the
# workflow cache carries. It must NOT be a separate cache path: actions/cache
# versions a cache by its path list, so adding one orphans every cache that
# came before it. RIO_SEED is the committed copy, refreshed once a day, and is
# the recovery path when the cache is evicted -- the same arrangement
# mythic_runs.csv.gz has with the collection journals.
RIO_FILE = ROOT / "data" / "processed" / "rio_scores.csv.gz"
RIO_SEED = ROOT / "data" / "rio_scores.csv.gz"

API = "https://raider.io/api/v1/characters/profile"
FIELDS = "mythic_plus_scores_by_season:current"

# Warcraft Logs' region labels vs Raider.IO's. WCL files the Taiwanese realms
# under KR, so a KR miss is retried against TW before it counts as a miss --
# see REGION_FALLBACK.
REGION = {"US": "us", "EU": "eu", "KR": "kr", "TW": "tw", "CN": "cn"}
REGION_FALLBACK = {"kr": "tw"}

# A score climbs all season, so an entry goes stale rather than wrong. A week
# is short enough to track the curve and long enough that the steady-state
# cost is a few minutes an hour once the first sweep is in.
FRESH_DAYS = 7
# A character that does not resolve usually never will -- renamed, transferred,
# deleted, or a realm Raider.IO does not carry. Re-probing those every week
# would spend the whole budget on known misses, so they are parked much longer.
MISS_DAYS = 30

MISS = -1            # journalled score meaning "asked, no answer"
DAY = 86400
THREADS = 8
# Raider.IO publishes no rate limit. Unpaced, eight threads reach ~965 req/min
# and about 4% of requests start failing, which is the service telling us to
# slow down; 300/min is the rate the community settles on and it runs clean.
# The whole population is a few hours at that rate and minutes an hour once
# the first sweep is done, so there is nothing to buy by going faster.
RATE_PER_MIN = 300
SAVE_EVERY = 2000    # checkpoint the journal mid-sweep


class Pacer:
    """Spaces requests evenly across all threads at RATE_PER_MIN."""

    def __init__(self, per_min: float):
        self._gap = 60.0 / per_min
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            due = max(now, self._next)
            self._next = due + self._gap
        delay = due - now
        if delay > 0:
            time.sleep(delay)


def today() -> int:
    return int(time.time() // DAY)


def slug(realm: str) -> str:
    """Raider.IO realm slug for a Warcraft Logs realm name.

    Lowercasing alone is not safe: 'Zul'jin' resolves and 'zul'jin' does not,
    because the apostrophe has to go before the case does. CamelCase realms
    ('MoonGuard') are split on the case boundary so they reach 'moon-guard'.
    """
    s = realm.replace("'", "").replace("’", "")
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", s)
    return s.replace(" ", "-").lower()


# ---------------------------------------------------------------- journal

def load_journal() -> dict[tuple[str, str, str], tuple[float, int]]:
    """(name, realm, region) -> (score, day fetched).

    Falls back to the committed seed when the cached journal is gone, so a
    cache eviction costs at most a day of re-fetching rather than the lot.
    """
    src = RIO_FILE if RIO_FILE.exists() else RIO_SEED
    if not src.exists():
        return {}
    if src is RIO_SEED:
        print(f"[rio] cached journal missing; seeding from {RIO_SEED}", flush=True)
    out: dict[tuple[str, str, str], tuple[float, int]] = {}
    with gzip.open(src, "rt", encoding="utf-8", newline="") as fh:
        for row in csv.reader(fh):
            if len(row) != 5:
                continue
            name, realm, region, score, day = row
            try:
                out[(name, realm, region)] = (float(score), int(day))
            except ValueError:
                continue
    return out


def save_journal(j: dict[tuple[str, str, str], tuple[float, int]]) -> None:
    """Atomic rewrite -- a half-written journal costs the whole sweep."""
    RIO_FILE.parent.mkdir(parents=True, exist_ok=True)
    # ".csv.gz" + ".tmp", not with_suffix() -- that would replace .gz and give
    # "rio_scores.csv.tmp", which the .gitignore rule for *.csv.gz.tmp misses
    tmp = RIO_FILE.with_name(RIO_FILE.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        for (name, realm, region), (score, day) in j.items():
            w.writerow([name, realm, region, f"{score:g}", day])
    os.replace(tmp, RIO_FILE)


# ---------------------------------------------------------------- population

def population() -> list[tuple[tuple[str, str, str], int]]:
    """Characters in the export, most-logged first."""
    import pandas as pd

    if not RUNS_CSV.exists():
        print(f"[rio] {RUNS_CSV} missing -- nothing to enrich", flush=True)
        return []
    df = pd.read_csv(RUNS_CSV, usecols=["character", "server", "region"])
    df = df.dropna(subset=["character", "server", "region"])
    counts: dict[tuple[str, str, str], int] = defaultdict(int)
    for name, realm, region in zip(df["character"], df["server"], df["region"]):
        counts[(str(name), str(realm), str(region))] += 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def due(pop, journal, now: int):
    """Split the population into never-asked, stale, and settled.

    Never-asked comes first: coverage is what makes the metric readable, and a
    character we have never seen contributes nothing until we do. Within each
    bucket the order is by run count, so the heaviest loggers land first.
    """
    fresh, new, stale = 0, [], []
    for key, n in pop:
        rec = journal.get(key)
        if rec is None:
            new.append((key, n))
            continue
        score, day = rec
        age = now - day
        if age >= (MISS_DAYS if score == MISS else FRESH_DAYS):
            stale.append((key, n))
        else:
            fresh += 1
    return new, stale, fresh


# ---------------------------------------------------------------- fetching

def fetch_one(session: requests.Session, key, pacer, fails) -> float | None:
    """Season score, MISS if the character does not resolve, None on a
    transport failure (which must not be journalled -- it would cache a
    network blip as a dead character for a month)."""
    name, realm, region = key
    rio_region = REGION.get(region.upper(), "us")
    regions = [rio_region]
    if rio_region in REGION_FALLBACK:
        regions.append(REGION_FALLBACK[rio_region])

    transport_error = False
    for reg in regions:
        for r in (realm, slug(realm)):
            pacer.wait()
            try:
                resp = session.get(API, timeout=25, params={
                    "region": reg, "realm": r, "name": name, "fields": FIELDS})
            except requests.RequestException as e:
                fails[type(e).__name__] += 1
                transport_error = True
                continue
            if resp.status_code == 429:
                fails["429"] += 1
                time.sleep(2)
                transport_error = True
                continue
            if resp.status_code == 200:
                try:
                    seasons = resp.json()["mythic_plus_scores_by_season"]
                    return float(seasons[0]["scores"]["all"])
                except (ValueError, KeyError, IndexError):
                    return MISS
            if resp.status_code >= 500:
                fails[str(resp.status_code)] += 1
                transport_error = True
    return None if transport_error else MISS


def sweep(budget_s: float, limit: int | None) -> None:
    now = today()
    journal = load_journal()
    pop = population()
    if not pop:
        return
    new, stale, fresh = due(pop, journal, now)
    queue = new + stale
    if limit:
        queue = queue[:limit]
    print(f"[rio] {len(pop):,} characters | {fresh:,} fresh | "
          f"{len(new):,} never asked | {len(stale):,} stale", flush=True)
    if not queue:
        print("[rio] nothing due", flush=True)
        return

    deadline = time.monotonic() + budget_s
    session = requests.Session()
    pacer = Pacer(RATE_PER_MIN)
    fails: dict[str, int] = defaultdict(int)
    done = ok = miss = fail = 0
    t0 = time.monotonic()

    def work(item):
        key, _ = item
        if time.monotonic() >= deadline:
            return key, None, True
        return key, fetch_one(session, key, pacer, fails), False

    with ThreadPoolExecutor(THREADS) as ex:
        for key, score, expired in ex.map(work, queue):
            if expired:
                continue
            if score is None:
                fail += 1
                continue
            journal[key] = (score, now)
            done += 1
            ok += score != MISS
            miss += score == MISS
            if done % SAVE_EVERY == 0:
                save_journal(journal)
                print(f"[rio] {done:,} fetched ({ok:,} scored, {miss:,} miss) "
                      f"{done / (time.monotonic() - t0) * 60:.0f}/min", flush=True)

    save_journal(journal)
    el = time.monotonic() - t0
    left = max(0, len(queue) - done)
    print(f"[rio] {done:,} fetched in {el:.0f}s ({done / max(el, 1) * 60:.0f}/min) "
          f"| {ok:,} scored, {miss:,} unresolved, {fail:,} transport failures "
          f"| {left:,} still due", flush=True)
    if fails:
        print("[rio] failures by kind: "
              + ", ".join(f"{k}={v}" for k, v in sorted(fails.items())), flush=True)
    if left:
        print(f"[rio] budget spent; the next run continues", flush=True)


def status() -> None:
    now = today()
    journal = load_journal()
    pop = population()
    new, stale, fresh = due(pop, journal, now)
    scored = sum(1 for s, _ in journal.values() if s != MISS)
    print(f"characters in export : {len(pop):,}")
    print(f"journalled           : {len(journal):,} "
          f"({scored:,} scored, {len(journal) - scored:,} unresolved)")
    print(f"fresh                : {fresh:,}")
    print(f"due                  : {len(new):,} never asked + {len(stale):,} stale")
    if pop:
        covered = sum(n for k, n in pop if journal.get(k, (MISS,))[0] != MISS)
        total = sum(n for _, n in pop)
        print(f"parse coverage       : {covered / total * 100:.1f}% of "
              f"{total:,} parses have a rated player")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--budget-s", type=float, default=300,
                    help="seconds to spend fetching before stopping (default 300)")
    ap.add_argument("--limit", type=int, default=None,
                    help="stop after N characters (for testing)")
    ap.add_argument("--status", action="store_true", help="report and exit")
    args = ap.parse_args()
    if args.status:
        status()
        return
    sweep(args.budget_s, args.limit)


if __name__ == "__main__":
    sys.exit(main())
