#!/usr/bin/env python3
"""Pack data/mythic_runs*.csv into compact columnar JSON for the static site.

The static dashboard (site/index.html) filters and aggregates client-side, so
it needs per-parse rows, not pre-aggregates (medians can't be merged). Columns
are dictionary-encoded ints; the whole file compresses to a few MB over the
wire and parses in ~100 ms.

Emits data.json for the current season, self-describing via its "season"
label.
"""
import argparse
import base64
import csv
import gzip
from collections import Counter
import hashlib
import json
import pathlib
import re
import shutil

import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

ROOT = pathlib.Path(__file__).resolve().parent.parent
# site/ is canonical; docs/ mirrors it because GitHub Pages can only serve
# from the repo root or /docs on branch-based deploys
SITE_DIRS = [ROOT / "site", ROOT / "docs"]

# Build-health notes, published as site/build_health.txt. A pipeline finding
# that only exists in a CI log is a finding nobody reads: the Actions log API
# returns just the tail, so the build's own diagnostics were unreachable
# exactly when they mattered (the Enchants pane hunt). These lines ride out
# with the site instead - a few hundred bytes, curl-able, and they say what
# the last build actually decided rather than what it was supposed to.
_HEALTH: list[str] = []


def health(line: str) -> None:
    """Record a build-health line AND print it."""
    _HEALTH.append(line)
    print(line, flush=True)


def write_health() -> None:
    body = "\n".join(_HEALTH) + "\n"
    for d in SITE_DIRS:
        try:
            d.mkdir(parents=True, exist_ok=True)
            (d / "build_health.txt").write_text(body, encoding="utf-8")
        except OSError:
            pass      # a health note must never be able to fail a build

SEASON = {"csv": "mythic_runs.csv.gz", "out": "data.json",
          "season": "Midnight Season 2"}

EPOCH = pd.Timestamp("2026-01-01")
# 1 = beat the timer (any chest count), 0 = over timer, anything else unknown.
# Both the site payload and the LLM export read this, so they cannot drift.
MEDAL_TIMED = {"gold": 1, "silver": 1, "bronze": 1, "timed": 1, "none": 0}
TUNING_FILE = ROOT / "data" / "tuning_patches.json"


def latest_tuning():
    """The newest class-tuning pass, or None if none is recorded."""
    if not TUNING_FILE.exists():
        return None
    patches = json.loads(TUNING_FILE.read_text()).get("patches") or []
    return patches[0] if patches else None


def post_tuning_flag(started, regions, patch):
    """1 where a run started at/after tuning went live in ITS region, else 0.

    Compared as exact UTC instants (the source timestamps are epoch ms), not
    calendar dates, because the cutoff lands mid-day and differs per region.
    """
    if patch is None:
        return pd.Series(-1, index=started.index, dtype=int)
    cutoffs = patch.get("regions", {})
    default = cutoffs.get("default")
    utc = started.dt.tz_localize("UTC") if started.dt.tz is None else started
    cut = regions.map(lambda r: cutoffs.get(r, default))
    cut = pd.to_datetime(cut, utc=True, errors="coerce")
    flag = (utc >= cut)
    # unknown cutoff (no default) -> -1 rather than a false negative
    return flag.astype(int).where(cut.notna(), -1).astype(int)


def derive_pars(df, dungeons):
    """Each dungeon's keystone timer, inferred from the data.

    WCL exposes no par time, but the timer is exactly the line separating
    timed from depleted runs on the keystone clock. Pick the threshold that
    misclassifies fewest runs and snap it to the nearest 30s, since Blizzard's
    timers are round values — on live this separates every dungeon perfectly.
    """
    if "keystone_s" not in df.columns:
        return [0] * len(dungeons)
    ks = pd.to_numeric(df["keystone_s"], errors="coerce")
    if ks.notna().sum() == 0:
        return [0] * len(dungeons)
    ok = df["medal"].isin(["timed", "gold", "silver", "bronze"])
    runs = pd.DataFrame({"dun": df["dungeon"], "ks": ks, "ok": ok}) \
        .dropna(subset=["ks"]).drop_duplicates()
    out = []
    for d in dungeons:
        g = runs[runs["dun"] == d]
        if len(g) < 20 or g["ok"].nunique() < 2:
            out.append(0)
            continue
        cands = np.arange(g["ks"].min(), g["ks"].max() + 30, 15)
        errs = [int(((g["ks"] <= c) != g["ok"]).sum()) for c in cands]
        out.append(int(round(cands[int(np.argmin(errs))] / 30) * 30))
    return out


_ABIL_CACHE = {}
HERO_FILLED = [0]


def ability_records():
    """The per-ability damage journal, loaded once per process."""
    if "rows" not in _ABIL_CACHE:
        try:
            import project_tuning as pt
        except ImportError:
            _ABIL_CACHE["rows"] = []
            return []
        _ABIL_CACHE["rows"] = ([json.loads(l) for l in pt.ABIL.open()
                                if l.strip()] if pt.ABIL.exists() else [])
    return _ABIL_CACHE["rows"]


def resolve_hero_talents(df):
    """Fill in hero_talent="Unknown" from the abilities the parse actually cast.

    Some logs carry no combatantInfo, so WCL can report no talent tree and the
    parse arrives labelled Unknown. Those rows are not random: they cluster by
    report, and any hero-gated view silently reads them as "not that hero".
    Hero trees grant abilities no sibling tree has, so the tree can be read off
    the damage breakdown instead. Markers are learned from the parses whose
    hero IS known, and ambiguous parses are left Unknown rather than guessed.

    Returns the number of rows filled in. Only parses with a breakdown can be
    recovered, so this covers whatever window fetch_abilities.py has collected.
    """
    rows = ability_records()
    if not rows or "Unknown" not in set(df["hero_talent"]):
        return 0
    from hero_from_abilities import HeroResolver
    abil = {(r["report_code"], r["fight_id"], r["name"]):
            frozenset(a["name"] for a in r["abilities"])
            for r in rows if r["abilities"]}
    spec = df["spec"] + " " + df["class"]
    keys = list(zip(df["report_code"], df["fight_id"], df["character"]))
    hr = HeroResolver.learn(
        (sp, h, abil[k]) for sp, h, k in zip(spec, df["hero_talent"], keys)
        if k in abil)
    filled, out = 0, list(df["hero_talent"])
    for i, (sp, h, k) in enumerate(zip(spec, df["hero_talent"], keys)):
        if h != "Unknown":
            continue
        hero, _ = hr.classify(sp, abil.get(k))
        if hero:
            out[i] = hero
            filled += 1
    df["hero_talent"] = out
    HERO_FILLED[0] = max(HERO_FILLED[0], filled)
    return filled


def tuning_multipliers(df, post):
    """Per-parse projected/current damage ratio for an upcoming tuning pass.

    Each parse's own ability breakdown (data/raw/abilities.jsonl) is
    re-scored line by line against the announced changes, so the number
    shipped here is that specific player's projected damage in that specific
    run — never a spec-level average. The client can therefore apply it row by
    row and any aggregate, under any filter combination, stays exact.

    Returns (per-10k ints, metadata) or (None, None) when the projection
    source is absent — the dashboard simply hides the toggle in that case.
    """
    try:
        import project_tuning as pt
    except ImportError:
        return None, None
    if not pt.ABIL.exists():
        return None, None
    rows = ability_records()
    # A rule that names an ability no parse ever reports is inert. That is not
    # always a bug (Disc's Entropic Rift genuinely has no line here) but it
    # must never be presented as an exact projection, so surface it loudly.
    seen_names = {a["name"] for r in rows for a in r["abilities"]}
    for sname, rule in pt.RULES.items():
        named = set(rule.get("abilities", {}))
        for e in rule.get("set_bonus", []) + rule.get("share_scale", []):
            named |= set(e[1])
        missing = sorted(n for n in named if n not in seen_names)
        if missing:
            print(f"[build] WARNING {sname}: rule names {missing} in "
                  f"no parse - that part of the rule does nothing", flush=True)
    work = df.copy()
    work["specname"] = work["spec"] + " " + work["class"]
    mult = pt.project(work[post == 1], rows, pt.B_CENTRAL)["mult"]
    mult = mult.reindex(df.index)
    # A tuned-spec parse with no ability record cannot be projected: a few WCL
    # reports have been deleted since collection, so the breakdown is gone for
    # good. Mark those 0 rather than 1.0 — claiming "unchanged" would be a
    # fabricated result. The client drops them from BOTH sides of the
    # comparison, keeping the populations identical.
    tuned = work["specname"].isin(pt.RULES) & (post == 1)
    for sname, rule in pt.RULES.items():
        if rule.get("hero_only"):                      # e.g. San'layn only
            tuned &= ~(work["specname"].eq(sname)
                       & work["hero_talent"].ne(rule["hero_only"]))
    unprojectable = tuned & mult.isna()
    mult = mult.fillna(1.0).mask(unprojectable, 0.0)
    covered = int(((mult != 1.0) & (mult != 0.0)).sum())
    if not covered:
        # nothing to project: no rules configured, or no run predates the
        # pending pass. Returning None hides the toggle rather than shipping a
        # column of 1.0s that pretends a projection exists.
        return None, None
    return (mult.mul(10000).round().astype(int).tolist(),
            {"label": pt.PROJECTION_LABEL, "url": pt.PROJECTION_URL,
             "date": pt.PROJECTION_DATE, "parses": covered,
             "unprojectable": int(unprojectable.sum()),
             "hero_recovered": int(HERO_FILLED[0]),
             "specs": sorted(pt.RULES),
             "exact": sorted(s for s, r in pt.RULES.items()
                             if not r.get("set_bonus")
                             and not r.get("share_scale")
                             and not r.get("caveats")),
             "caveats": {s: r["caveats"] for s, r in pt.RULES.items()
                         if r.get("caveats")}})


# The browser gets every parse as a row, so the payload scales with the run
# count. Collecting the whole population (~10x a leaderboard sweep, and still
# growing) would put data.json past 70 MB, which no page should ask for.
MAX_RUNS = 150_000


def use_keystone_clock(df: pd.DataFrame, name: str) -> pd.DataFrame:
    """Recompute DPS against the keystone timer rather than the fight duration.

    The collector stores `duration_s` from the Summary table's totalTime, which
    runs a median 27s shorter than the run's actual keystone clock -- it drops
    some of the time between pulls. Warcraft Logs defines overall M+ DPS as
    total damage over the ENTIRE dungeon time, non-combat included, so the
    keystone clock is the denominator that matches the definition, and using
    the shorter one inflated every figure on the page by ~2.3%.

    Rows with no usable clock keep their original value rather than being
    dropped; there are very few and losing them would bias the sample.
    """
    ks = pd.to_numeric(df.get("keystone_s"), errors="coerce")
    if ks is None or not ks.notna().any():
        return df
    ok = ks.notna() & (ks > 0) & df["damage_done"].notna()
    if not ok.any():
        return df
    before = df.loc[ok, "dps"].mean()
    df.loc[ok, "dps"] = (df.loc[ok, "damage_done"] / ks[ok]).round(1)
    df.loc[ok, "duration_s"] = ks[ok].round(1)
    after = df.loc[ok, "dps"].mean()
    print(f"[{name}] DPS recomputed on the keystone clock for {int(ok.sum()):,} "
          f"of {len(df):,} rows ({100 * (after / before - 1):+.1f}% mean)",
          flush=True)
    return df


def sample_runs(df: pd.DataFrame, name: str) -> pd.DataFrame:
    """Cap the published payload at MAX_RUNS whole runs.

    Whole runs, not rows, so a sampled run keeps all five of its players and
    the comps table still sees complete rosters. Selection is by a hash of the
    run id rather than a shuffle, which makes it deterministic across rebuilds
    -- the same runs are published every time, so the numbers do not jitter
    when nothing changed. Sampling a population uniformly leaves it unbiased,
    which is the entire reason for collecting it in full: the local dataset
    stays complete for analysis, only the payload is thinned.
    """
    if MAX_RUNS <= 0:
        return df
    ids = df["report_code"].astype(str) + ":" + df["fight_id"].astype(str)
    total = ids.nunique()
    if total <= MAX_RUNS:
        return df
    cut = int((MAX_RUNS / total) * (1 << 32))
    keep = ids.map(lambda r: int(hashlib.md5(r.encode()).hexdigest()[:8], 16) < cut)
    out = df[keep]
    # ids[keep], not out.report_code: one report can hold several keys, so
    # counting report codes understates the runs published by about half
    print(f"[{name}] {total:,} runs collected -> {ids[keep].nunique():,} "
          f"published ({len(out):,} of {len(df):,} rows); uniform sample, "
          f"full data kept locally", flush=True)
    return out


# live journal first, committed seed second -- see scripts/fetch_rio.py
RIO_FILE = ROOT / "data" / "processed" / "rio_scores.csv.gz"
RIO_SEED = ROOT / "data" / "rio_scores.csv.gz"


def player_scores() -> dict[str, float]:
    """Raider.IO season score per character, keyed name@server@region.

    This is the player's season total -- the sum of their best run in each of
    the eight dungeons -- and so is roughly eight times the per-run score that
    rides on each parse. The two are separate metrics on the site and must not
    be confused for one another.

    Absent journal, or a character missing from it, simply means no rating; the
    client drops those rather than counting them as zero.
    """
    src = RIO_FILE if RIO_FILE.exists() else RIO_SEED
    if not src.exists():
        print("[build] no Raider.IO journal; player rating omitted")
        return {}
    out: dict[str, float] = {}
    with gzip.open(src, "rt", encoding="utf-8", newline="") as fh:
        for row in csv.reader(fh):
            if len(row) != 5:
                continue
            name, realm, region, score, _day = row
            try:
                v = float(score)
            except ValueError:
                continue
            if v > 0:    # -1 = journalled "no answer"; 0 = profile with no
                         # current-season rating, which cannot be a real
                         # figure for a character we have M+ parses for
                out[f"{name}@{realm}@{region}"] = v
    return out


# Share of a class's equipped set pieces a set must reach to be considered its
# tier set. Keeps a stray non-tier set that a few players wear from winning on
# id alone, while staying far below the ~40%+ that a real tier set reaches.
SEASON_SET_MIN_SHARE = 0.05

def _gear_key(code, fid, character, server) -> tuple:
    """Join key for gear rows, identical from either source.

    The journal stores a missing server as JSON null (None); the CSV stores it
    as NaN. None != NaN, and str() of them differ too, so without normalising
    both to "" every such row silently missed the join and fell back to the
    packed column. fight_id goes through int() because the journal carries a
    JSON int while a CSV column can arrive as int64 or float64.
    """
    def norm(v):
        return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)
    return (norm(code), int(fid), norm(character), norm(server))


GEAR_JOURNAL = ROOT / "data" / "processed" / "gear.jsonl"
GEAR_EXPORT = ROOT / "data" / "gear.jsonl.gz"


def sets_from_gear_journal() -> dict[tuple, dict[str, int]]:
    """(report, fight, character, server) -> {set id: pieces}, from raw gear.

    Authoritative, because it counts every set off the equipped items rather
    than trusting a summary written at collection time. Parses collected before
    the collector counted more than the dominant set are only correct through
    this path, which is why it is preferred over the packed column.
    """
    src = GEAR_JOURNAL if GEAR_JOURNAL.exists() else GEAR_EXPORT
    if not src.exists():
        return {}
    opener = gzip.open if src.suffix == ".gz" else open
    out: dict[tuple, dict[str, int]] = {}
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
                continue                       # talents only: gear unknown
            counts: dict[str, int] = {}
            for item in gear:
                if not isinstance(item, dict):
                    continue
                sid = item.get("set")
                if sid in (None, 0, "0", ""):
                    continue
                counts[str(sid)] = counts.get(str(sid), 0) + 1
            out[_gear_key(rec.get("report_code"), rec.get("fight_id"),
                          rec.get("character"), rec.get("server"))] = counts
    return out


def unpack_sets(v) -> dict[str, int] | None:
    """'1729:4|1600:2' -> {'1729': 4, '1600': 2}; '' -> {}; missing -> None."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    v = str(v)
    # "none" = gear visible, no set items (see pack_sets); "" kept for safety
    if v in ("none", "", "nan"):
        return {}
    out: dict[str, int] = {}
    for part in v.split("|"):
        sid, _, n = part.partition(":")
        if sid and n.isdigit():
            out[sid] = int(n)
    return out


def tier_pieces(df: "pd.DataFrame", name: str) -> "pd.Series":
    """Season tier pieces per parse: -1 unknown, else 0-5.

    The collector records how many items a player wears from their commonest
    item set, and which set that is. It does not know which set is *this*
    season's tier -- that would mean hard-coding item-set ids that change every
    patch. Instead the season's set is read off the data: for each class, the
    set id worn by the most of that class's parses is the current tier, since
    that is what the set bonus makes everyone wear.

    Pieces from any other set (an old tier kept for transmog, a crafted set)
    count as zero rather than being credited to this season's bonus.

    -1 means the report carried no gear at all, which stays distinct from a
    real zero all the way to the client so the filter can exclude it rather
    than treat it as "no set".
    """
    journal = sets_from_gear_journal()
    packed = (df["set_counts"] if "set_counts" in df.columns
              else pd.Series(None, index=df.index, dtype=object))
    keys = [_gear_key(c, f, ch, sv) for c, f, ch, sv in
            zip(df["report_code"], df["fight_id"],
                df["character"], df["server"])]

    # per parse: {set id: pieces}, or None when the report carried no gear
    per: list[dict | None] = []
    for k, pv in zip(keys, packed):
        c = journal.get(k)
        per.append(c if c is not None else unpack_sets(pv))
    if not any(c is not None for c in per):
        print(f"[{name}] no gear captured yet; tier filter unavailable")
        return pd.Series(-1, index=df.index, dtype=int)

    # This season's tier set per class. Hard-coding item-set ids would mean
    # editing this every patch, so it is read off the data -- but "the set most
    # of the class wears" is the wrong rule, and was wrong in production: two
    # tier sets are in circulation at once, and plenty of players still had
    # last season's on. Measured on 21,362 parses, that rule picked the OLDER
    # set for Druid, Monk, Paladin and Priest -- Paladin by 0.7% (5,141 vs
    # 5,104) -- so four classes counted last season's pieces as this season's.
    #
    # Item-set ids are issued in content order, so the current tier is the
    # highest id, and the two seasons land in tidy blocks (1978-1990 and
    # 2055-2067). Taking the highest id alone would catch stray non-tier sets
    # a handful of players wear, so a set has to clear a share of the class's
    # equipped set pieces before it is eligible.
    tally: dict[str, dict[str, int]] = {}
    for cls, c in zip(df["class"], per):
        if not c:
            continue
        for sid, n in c.items():
            tally.setdefault(cls, {})
            tally[cls][sid] = tally[cls].get(sid, 0) + n

    def newest(counts: dict[str, int]) -> str:
        total = sum(counts.values())
        qual = [s for s, n in counts.items()
                if n >= SEASON_SET_MIN_SHARE * total and s.isdigit()]
        if not qual:                       # nothing clears the bar: fall back
            return max(counts, key=counts.get)
        return max(qual, key=int)

    seasonal = {cls: newest(v) for cls, v in tally.items() if v}

    # Pieces of THIS season's set specifically. A player wearing last season's
    # four-piece and nothing current is a true zero, which is the point: the
    # no-set cohort is "no Season 2 set", verified against visible gear, not
    # "no set at all" and not "gear unknown".
    out = []
    for cls, c in zip(df["class"], per):
        if c is None:
            out.append(-1)                       # report carried no gear
            continue
        sid = seasonal.get(cls)
        out.append(min(c.get(sid, 0), 5) if sid else 0)
    res = pd.Series(out, index=df.index, dtype=int)

    n_known = int((res >= 0).sum())
    print(f"[{name}] gear on {n_known:,} of {len(df):,} parses "
          f"({n_known / max(len(df), 1):.1%}); {int((res >= 2).sum()):,} with "
          f"2-piece, {int((res >= 4).sum()):,} with 4-piece; tier set "
          f"identified for {len(seasonal)} classes "
          f"({len(journal):,} parses read from the gear journal)")
    if seasonal:
        picked = ", ".join(f"{c}={seasonal[c]}" for c in sorted(seasonal))
        print(f"[{name}] season tier sets: {picked}")
    return res


# --- per-spec character stats for the spec frame ---------------------------
# Cohort knobs. The window is anchored on the newest run in the dataset, not
# the wall clock, so rebuilding stale data cannot silently empty the block.
# +12 is where the gear backfill concentrated, so that is where stats coverage
# is dense enough to quote; timed-only matches the dashboard's default flavor.
SPECSTATS_WINDOW_DAYS = 14
SPECSTATS_MIN_KEY = 12
SPECSTATS_MIN_CHARS = 10        # a spec below this is omitted, never guessed
# The four ratings every real combatantInfo carries; a record missing any of
# them is a torn capture and is skipped rather than mixed into the quantiles.
SPECSTATS_CORE = ("Crit", "Haste", "Mastery", "Versatility")
# The rest of the owner-approved list rides along only where the journal
# reliably carries it: the spec's primary attribute plus the tertiaries.
# This list is a CAP — "but that's it" — nothing else ships (no Stamina,
# no armor), whatever else combatantInfo happens to include.
SPECSTATS_EXTRA = ("Intellect", "Agility", "Strength",
                   "Leech", "Speed", "Avoidance")
SPECSTATS_EXTRA_MIN_SHARE = 0.9


def stats_from_gear_journal() -> dict[tuple, dict]:
    """(report, fight, character, server) -> {"stats": {...}}.

    stats: the secondary-stat RATINGS the collector captured off
    combatantInfo (compact_talents in scripts/fetch_data.py), numeric values
    only. Records missing any core secondary are dropped as torn captures --
    quantiles over a mixed population would be quietly wrong. Duplicate keys
    keep the last copy, matching the journal's append-and-supersede contract
    (see export_gear in fetch_data.py). A record's "flask" field, where one
    was ever captured, is deliberately NOT surfaced: the flask feature was
    removed (see fleet/feature_specframe.md) and nothing downstream reads it.
    """
    src = GEAR_JOURNAL if GEAR_JOURNAL.exists() else GEAR_EXPORT
    if not src.exists():
        return {}
    opener = gzip.open if src.suffix == ".gz" else open
    out: dict[tuple, dict] = {}
    with opener(src, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue                       # tolerate a torn trailing line
            tal = rec.get("talents")
            stats = tal.get("stats") if isinstance(tal, dict) else None
            if not isinstance(stats, dict):
                continue                       # gear-only record: no stats
            vals = {k: v for k, v in stats.items()
                    if isinstance(v, (int, float))
                    and not isinstance(v, bool) and not pd.isna(v)}
            if any(s not in vals for s in SPECSTATS_CORE):
                continue                       # torn capture: skip, don't mix
            out[_gear_key(rec.get("report_code"), rec.get("fight_id"),
                          rec.get("character"), rec.get("server"))] = {
                "stats": vals}
    return out


def _stat_quantiles(stat_rows: list[dict], stats) -> dict[str, list[int]]:
    """{stat: [p25, p50, p75]} over the rows carrying that stat, as ints."""
    out = {}
    for s in stats:
        vals = [r[s] for r in stat_rows if s in r]
        if vals:
            out[s] = [int(round(v)) for v in np.percentile(vals, (25, 50, 75))]
    return out


def _specstats_cohort(df, started, timed) -> "pd.Series":
    """The shared journal-block cohort mask: timed runs at the key floor and
    up, inside the newest-anchored window. specstats and specmeta both build
    on this one mask so their cohort statements can never drift apart."""
    key_ok = pd.to_numeric(df["key_level"], errors="coerce") \
        >= SPECSTATS_MIN_KEY
    ok = started.notna() & (timed == 1) & key_ok
    if ok.any():
        cutoff = started[ok].max() \
            - pd.Timedelta(days=SPECSTATS_WINDOW_DAYS)
        ok &= started >= cutoff
    return ok


def spec_stats_block(df, started, timed, name: str, journal=None):
    """Per-spec secondary-stat distributions for the spec frame's stats block.

    Small by construction: per class+spec (hero talents merged, matching the
    dashboard's default grouping -- the journal carries no hero identity
    anyway) the p25/p50/p75 of each secondary-stat rating, over ONE record
    per character: their latest stats-known parse in the cohort, so a
    grinder's fifty parses cannot drag a distribution. The cohort is timed
    +SPECSTATS_MIN_KEY-and-higher keys from the newest SPECSTATS_WINDOW_DAYS
    days of data, stated verbatim in the "cohort" string for the client to
    print. Values ship as RATINGS, never converted to percentages: the
    conversion is level- and stat-dependent and belongs to whoever knows the
    formula, not this file.

    Returns None when the journal carries no usable stats: the payload key is
    then absent and the client feature-detects it exactly like tier/rating.
    """
    if journal is None:
        journal = stats_from_gear_journal()
    if not journal:
        print(f"[{name}] no combatant stats in the gear journal; "
              f"specstats block omitted")
        return None
    ok = _specstats_cohort(df, started, timed)
    n_cohort = int(ok.sum())
    if not n_cohort:
        print(f"[{name}] no parses in the specstats cohort; block omitted")
        return None

    # one record per character: their latest stats-known parse in the cohort
    latest: dict[tuple, tuple] = {}
    n_hit = 0
    for sel, t, cls, spec, ch, sv, rg, code, fid in zip(
            ok, started, df["class"], df["spec"], df["character"],
            df["server"], df["region"], df["report_code"], df["fight_id"]):
        if not sel:
            continue
        rec = journal.get(_gear_key(code, fid, ch, sv))
        if rec is None:
            continue
        n_hit += 1
        ck = (cls, spec, str(ch), str(sv), str(rg))
        prev = latest.get(ck)
        if prev is None or t > prev[0]:
            latest[ck] = (t, rec)
    if not n_hit:
        print(f"[{name}] no stats-known parses in the specstats cohort; "
              f"block omitted")
        return None

    groups: dict[tuple, list[dict]] = {}
    for (cls, spec, *_), (_, rec) in latest.items():
        groups.setdefault((cls, spec), []).append(rec)

    spec_out: dict[str, dict] = {}
    n_chars = 0
    for (cls, spec), recs in sorted(groups.items()):
        n = len(recs)
        n_chars += n
        if n < SPECSTATS_MIN_CHARS:
            continue                           # thin spec: omit, never guess
        stat_rows = [r["stats"] for r in recs]
        stats = list(SPECSTATS_CORE) + [
            s for s in SPECSTATS_EXTRA
            if sum(1 for r in stat_rows if s in r)
            >= SPECSTATS_EXTRA_MIN_SHARE * n]
        spec_out[f"{cls}|{spec}"] = {"n": n,
                                     "q": _stat_quantiles(stat_rows, stats)}
    if not spec_out:
        print(f"[{name}] every spec below {SPECSTATS_MIN_CHARS} stats-known "
              f"characters; specstats block omitted")
        return None

    # printed verbatim by the client -- window, key floor, n and coverage in
    # one line, per the feature contract
    cohort = (f"timed +{SPECSTATS_MIN_KEY}s and higher from the last "
              f"{SPECSTATS_WINDOW_DAYS} days of data; one record per "
              f"character (their latest parse); stats known for {n_hit:,} of "
              f"{n_cohort:,} parses ({n_hit / n_cohort:.0%}); values are "
              f"stat ratings as the character sheet read at the pull — "
              f"active consumables (flask, food) included; not percentages")
    block = {"cohort": cohort, "keyMin": SPECSTATS_MIN_KEY,
             "windowDays": SPECSTATS_WINDOW_DAYS, "specs": spec_out}
    size = len(json.dumps(block, separators=(",", ":")))
    print(f"[{name}] specstats: {len(spec_out)} specs from {n_chars:,} "
          f"characters ({n_hit:,} stats-known parses; "
          f"{size / 1024:.1f} KB)")
    return block


def spec_stats_frame(block: dict) -> "pd.DataFrame":
    """Flatten a specstats block to long-format rows for the llms export."""
    rows = []
    for key, e in block["specs"].items():
        cls, spec = key.split("|", 1)
        for stat, (p25, p50, p75) in e["q"].items():
            rows.append({"class": cls, "spec": spec,
                         "characters": e["n"], "stat": stat,
                         "p25": p25, "p50": p50, "p75": p75})
    return pd.DataFrame(rows)


# --- per-parse stats sidecar (site/stats.json.gz) ---------------------------
# The specstats block above is a fixed cohort; the client's filter-responsive
# distributions need the per-parse values themselves. They ship as a SIDECAR
# file rather than inside data.json.gz so the main payload does not grow: a
# packed Uint16 matrix, row-aligned with the payload's rows arrays (same df,
# same order -- emitted from one iteration over that df, which is the
# structural guarantee, and pinned by test).
SIDECAR_STATS = ("Intellect", "Agility", "Strength", "Crit", "Haste",
                 "Mastery", "Versatility", "Leech", "Speed", "Avoidance")
SIDECAR_CORE = 7               # first 7 of SIDECAR_STATS; the tertiaries
                               # (Leech/Speed/Avoidance) are the droppable tail
SIDECAR_GZ_TARGET = 2_500_000  # aim under this
SIDECAR_GZ_CAP = 4_000_000     # never ship over this


def _sidecar_json(names, enc, n, vals, idx=None) -> str:
    """The sidecar document, exactly as published.

    data decodes to a little-endian Uint16Array: per covered row, one rating
    per stat in `stats` order (0 = unknown/absent) and nothing else --
    "flaskcol": false says so in-band, because an earlier layout carried a
    trailing flask column and the client tolerates both. Dense covers every
    payload row in order; sparse covers only stats-known rows, with `idx`
    decoding to a Uint32Array of their payload row indices.
    """
    obj = {"stats": list(names), "flaskcol": False, "enc": enc, "n": n,
           "data": base64.b64encode(vals.astype("<u2").tobytes()).decode()}
    if idx is not None:
        obj["idx"] = base64.b64encode(idx.astype("<u4").tobytes()).decode()
    return json.dumps(obj, separators=(",", ":"))


def stats_sidecar(df, journal, name: str, enc: str | None = None,
                  cap: int = SIDECAR_GZ_CAP) -> str | None:
    """Per-parse stat ratings, packed for the client's typed arrays.

    Emitted by walking df in row order, so index i in the decoded matrix IS
    row i of the payload's rows arrays -- never a separate join that could
    drift. Both encodings are built and the one that gzips smaller ships
    (enc forces one, for tests); if even that breaches the cap the
    tertiaries are dropped first, loudly, and shipping nothing beats
    shipping an oversized or misaligned file.

    Returns the JSON document as a string (the caller gzips it), or None
    when the journal offers no stats at all -- the client feature-detects
    the file exactly like the payload blocks.
    """
    per_row: list[tuple[int, dict]] = []
    for i, (code, fid, ch, sv) in enumerate(zip(
            df["report_code"], df["fight_id"],
            df["character"], df["server"])):
        rec = journal.get(_gear_key(code, fid, ch, sv))
        if rec is not None:
            per_row.append((i, rec))
    if not per_row:
        print(f"[{name}] no stats in the gear journal; sidecar omitted")
        return None
    n = len(df)

    def build_doc(names) -> str:
        cols = len(names)
        packed = np.zeros((len(per_row), cols), dtype="<u2")
        idx = np.zeros(len(per_row), dtype="<u4")
        for j, (i, rec) in enumerate(per_row):
            st = rec["stats"]
            for k, nm in enumerate(names):
                packed[j, k] = min(max(int(round(st.get(nm, 0))), 0), 0xFFFF)
            idx[j] = i
        sparse = _sidecar_json(names, "sparse", n, packed, idx)
        if enc == "sparse":
            return sparse
        dense_m = np.zeros((n, cols), dtype="<u2")
        dense_m[idx] = packed
        dense = _sidecar_json(names, "dense", n, dense_m)
        if enc == "dense":
            return dense
        gz_d = len(gzip.compress(dense.encode(), 6))
        gz_s = len(gzip.compress(sparse.encode(), 6))
        print(f"[{name}] sidecar {len(per_row):,}/{n:,} rows known: "
              f"dense {gz_d / 1e6:.2f} MB gz vs sparse {gz_s / 1e6:.2f} MB "
              f"gz -> {'dense' if gz_d <= gz_s else 'sparse'}")
        return dense if gz_d <= gz_s else sparse

    doc = build_doc(SIDECAR_STATS)
    if len(gzip.compress(doc.encode(), 6)) > cap:
        print(f"[{name}] sidecar over the {cap / 1e6:.1f} MB gz cap; "
              f"dropping tertiaries ({', '.join(SIDECAR_STATS[SIDECAR_CORE:])})")
        doc = build_doc(SIDECAR_STATS[:SIDECAR_CORE])
        if len(gzip.compress(doc.encode(), 6)) > cap:
            print(f"[{name}] sidecar still over the cap without tertiaries; "
                  f"NOT shipped (client falls back to the specstats block)")
            return None
    return doc


# --- per-spec "best players" meta aggregates (builds, trinkets, ...) --------
# One generic engine, per the builds-research vision in the feature contract:
# an aggregate is a DIMENSION -- a name, an extractor turning a journal
# record into the string values a character exhibits, and how many top
# entries to keep. Adding a future aggregate (per-slot items, bonus rolls,
# hero nodes, ...) is one line in SPECMETA_DIMS, not a subsystem. Every
# dimension is split into the same two skill bands automatically and shares
# the specstats cohort discipline (window, key floor, timed, per-character
# dedup) via _specstats_cohort.
SPECMETA_ENTRY_MIN = 3        # characters behind an entry before it ships
# "top" band: characters at/above this quantile of the spec's per-character
# best cohort DPS -- a quantile over CHARACTERS, so the band is a true
# quartile of who is shown (ranking by parse percentile instead lets
# best-of-many-parses push half the characters over the line)
SPECMETA_TOP_QUANTILE = 0.75
# Retail equipment order; 12/13 are the trinket slots. Positions are the one
# thing the journal keeps positionally (see compact_gear), and gear_sets'
# warning about trusting indices stands -- a wrong index here fails soft as
# implausible item shares, which the first real run's output shows at once.
TRINKET_SLOTS = (12, 13)


def _dim_builds(rec):
    b = rec.get("build")
    return [b] if b else None


def _dim_trinkets(rec):
    g = rec.get("gear")
    if not isinstance(g, list):
        return None
    return sorted({str(it["id"]) for i in TRINKET_SLOTS if i < len(g)
                   for it in [g[i]] if isinstance(it, dict) and it.get("id")})


def _dim_enchants(rec):
    g = rec.get("gear")
    if not isinstance(g, list):
        return None
    # slot-qualified ("15:7008"), so the same mechanism serves per-slot
    # research later without re-journaling anything
    return [f"{i}:{it['ench']}" for i, it in enumerate(g)
            if isinstance(it, dict) and it.get("ench")]


def _dim_gems(rec):
    g = rec.get("gear")
    if not isinstance(g, list):
        return None
    return sorted({str(gm) for it in g if isinstance(it, dict)
                   for gm in (it.get("gems") or []) if gm})


SPECMETA_DIMS = (("builds", _dim_builds, 3),
                 ("trinkets", _dim_trinkets, 5),
                 ("enchants", _dim_enchants, 5),
                 ("gems", _dim_gems, 5))


def _tree_blob(tree) -> str | None:
    """The canonical serialization a tree hashes over: nodes with a truthy
    id as "id:rank" (null rank = 0), sorted by id, joined "|". Also the
    canonical selection-set serialization for §1.7 "sel" emission — one
    definition, so identity and selections can never disagree."""
    if not isinstance(tree, list) or not tree:
        return None
    nodes = sorted((int(n["id"]), int(n.get("rank") or 0))
                   for n in tree if isinstance(n, dict) and n.get("id"))
    if not nodes:
        return None
    return "|".join(f"{i}:{r}" for i, r in nodes)


def _tree_build_id(tree) -> str | None:
    """Canonical build identity for a talent tree, as a short visible hash.

    Production journals carry talents.tree only — talentImportString never
    appears in real WCL summaries (430,507/430,507 records checked) — so
    builds are identified by the tree itself: nodes sorted by id, serialized
    "id:rank" (null rank = 0) joined with "|", md5 hex truncated to 12
    chars, prefixed "t:" so the value can never be mistaken for a pasteable
    import string. Node order in the journal is presentation order and must
    not matter; a rank change is a different build.
    """
    blob = _tree_blob(tree)
    if blob is None:
        return None
    return "t:" + hashlib.md5(blob.encode()).hexdigest()[:12]


def _record_build_id(tal) -> str | None:
    """One record's build identity: the verbatim import string when the
    journal carries one, else the tree hash. The single definition the meta
    reader and the trait pass share -- they must never disagree."""
    build = tal.get("talentImportString") if isinstance(tal, dict) else None
    if isinstance(build, str) and build:
        return build
    return _tree_build_id(tal.get("tree") if isinstance(tal, dict) else None)


def meta_from_gear_journal() -> dict[tuple, dict]:
    """(report, fight, character, server) -> {"build": ..., "gear": ...}.

    The raw material for the meta dimensions: the build identity and the
    compact per-slot gear list exactly as journaled (items keep
    id/ilvl/set/ench/gems/bonus, so a future dimension slices this same
    record without a new reader or a re-collection). build is the verbatim
    talentImportString where one exists, else the _tree_build_id hash of
    talents.tree (hashed here at read time — trees are never retained), else
    None. Records carrying neither build nor gear are skipped; duplicate
    keys keep the last copy, matching the journal's append-and-supersede
    contract.
    """
    shapes = {"string": 0, "tree": 0, "neither": 0}
    src = GEAR_JOURNAL if GEAR_JOURNAL.exists() else GEAR_EXPORT
    if not src.exists():
        return {}
    opener = gzip.open if src.suffix == ".gz" else open
    out: dict[tuple, dict] = {}
    with opener(src, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue                       # tolerate a torn trailing line
            build = _record_build_id(rec.get("talents"))
            # ":" is outside the import-string alphabet, so the prefix test
            # can never misclassify a real string as a hash
            shapes["neither" if build is None
                   else "tree" if build.startswith("t:")
                   else "string"] += 1
            gear = rec.get("gear")
            if not isinstance(gear, list):
                gear = None
            if build is None and gear is None:
                continue
            out[_gear_key(rec.get("report_code"), rec.get("fight_id"),
                          rec.get("character"), rec.get("server"))] = {
                "build": build, "gear": gear}
    print(f"[journal] build identities: {shapes['string']:,} import "
          f"strings, {shapes['tree']:,} tree hashes, {shapes['neither']:,} "
          f"without either")
    return out


def spec_meta_block(df, started, timed, name: str, journal=None):
    """Per-spec top builds / trinkets / enchants / gems for the spec frame.

    Same cohort and per-character dedup as specstats. Each dimension is
    reported per skill band -- "all" characters, and "top": the top quartile
    of the spec's characters ranked by their best cohort parse's DPS
    (omitted below SPECSTATS_MIN_CHARS characters, like a thin spec). Per
    band: d = characters the dimension is observable for
    (its share denominator) and e = the top entries, each {v, n, dps}: v
    indexes the spec's "vals" string pool (talent loadout strings, item and
    gem ids, "slot:enchantId"), n = characters exhibiting the value, dps =
    the median DPS of those characters' latest parses. The journal stores no
    item names, so ids ship and the client names them. Entries backed by
    fewer than SPECMETA_ENTRY_MIN characters are dropped, never shown thin.

    Returns None when the journal carries no builds or gear at all -- the
    payload key is then absent, feature-detected like tier/rating.
    """
    if journal is None:
        journal = meta_from_gear_journal()
    if not journal:
        print(f"[{name}] no builds/gear in the gear journal; "
              f"specmeta block omitted")
        return None
    ok = _specstats_cohort(df, started, timed)
    n_cohort = int(ok.sum())
    if not n_cohort:
        print(f"[{name}] no parses in the specmeta cohort; block omitted")
        return None

    dps_col = pd.to_numeric(df["dps"], errors="coerce")
    best: dict[tuple, float] = {}       # a character's best cohort DPS
    latest: dict[tuple, tuple] = {}     # their latest journal-known parse
    n_hit = 0
    for sel, t, dps, cls, spec, ch, sv, rg, code, fid in zip(
            ok, started, dps_col, df["class"], df["spec"], df["character"],
            df["server"], df["region"], df["report_code"], df["fight_id"]):
        if not sel or pd.isna(dps):
            continue
        ck = (cls, spec, str(ch), str(sv), str(rg))
        if float(dps) > best.get(ck, -1.0):
            best[ck] = float(dps)
        rec = journal.get(_gear_key(code, fid, ch, sv))
        if rec is None:
            continue
        n_hit += 1
        prev = latest.get(ck)
        if prev is None or t > prev[0]:
            latest[ck] = (t, rec, float(dps))
    if not latest:
        print(f"[{name}] no journal-known parses in the specmeta cohort; "
              f"block omitted")
        return None

    groups: dict[tuple, list] = {}
    for ck, (t, rec, dps) in latest.items():
        groups.setdefault(ck[:2], []).append((rec, dps, best[ck]))

    spec_out: dict[str, dict] = {}
    for (cls, spec), chars in sorted(groups.items()):
        n = len(chars)
        if n < SPECSTATS_MIN_CHARS:
            continue                           # thin spec: omit, never guess
        cut = float(np.quantile([c[2] for c in chars],
                                SPECMETA_TOP_QUANTILE))
        top = [c for c in chars if c[2] >= cut]
        bands = [("all", chars)]
        if len(top) >= SPECSTATS_MIN_CHARS:
            bands.append(("top", top))
        pool: list[str] = []
        pidx: dict[str, int] = {}

        def vid(v: str) -> int:
            if v not in pidx:
                pidx[v] = len(pool)
                pool.append(v)
            return pidx[v]

        dims_out: dict[str, dict] = {}
        for dname, extract, keep in SPECMETA_DIMS:
            per_band: dict[str, dict] = {}
            for bname, members in bands:
                d = 0
                cnt: dict[str, int] = {}
                dvals: dict[str, list] = {}
                for rec, dps, _ in members:
                    vals = extract(rec)
                    if vals is None:
                        continue               # dimension unobservable here
                    d += 1
                    for v in set(vals):
                        cnt[v] = cnt.get(v, 0) + 1
                        dvals.setdefault(v, []).append(dps)
                ranked = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))
                entries = [{"v": vid(v), "n": c,
                            "dps": int(round(float(np.median(dvals[v]))))}
                           for v, c in ranked[:keep]
                           if c >= SPECMETA_ENTRY_MIN]
                if entries:
                    per_band[bname] = {"d": d, "e": entries}
            # a "top" band cannot outlive "all": its counts are a subset
            if per_band.get("all"):
                dims_out[dname] = per_band
        if not dims_out:
            continue
        entry = {"n": n, "ntop": len(top), "vals": pool, "dims": dims_out}
        # what kind of value the builds dim carries: "t:"-prefixed tree
        # hashes cannot be pasted into the game, so the client suppresses
        # copy affordances off this flag (per-value, "t:" itself decides)
        bvals = [pool[e["v"]] for band in dims_out.get("builds", {}).values()
                 for e in band["e"]]
        if bvals:
            entry["bkind"] = ("hash" if all(v.startswith("t:")
                                            for v in bvals) else "string")
        spec_out[f"{cls}|{spec}"] = entry
    if not spec_out:
        print(f"[{name}] every spec below {SPECSTATS_MIN_CHARS} journal-known "
              f"characters; specmeta block omitted")
        return None

    cohort = (f"timed +{SPECSTATS_MIN_KEY}s and higher from the last "
              f"{SPECSTATS_WINDOW_DAYS} days of data; one record per "
              f"character (their latest parse); builds/gear known for "
              f"{n_hit:,} of {n_cohort:,} parses ({n_hit / n_cohort:.0%}); "
              f"the top band is the top quartile of each spec's characters "
              f"by their best parse's DPS within that spec")
    block = {"cohort": cohort, "bands": ["all", "top"],
             "dims": [dn for dn, _, _ in SPECMETA_DIMS], "specs": spec_out}
    size = len(json.dumps(block, separators=(",", ":")))
    print(f"[{name}] specmeta: {len(spec_out)} specs, "
          f"{sum(len(s['vals']) for s in spec_out.values())} pooled values "
          f"({n_hit:,} journal-known parses; {size / 1024:.1f} KB)")
    return block


# --- builds sidecar (site/builds.json.gz) — blueprint §1, pinned ------------
# The Character Screen's data layer: per-row slot items, enchants and talent
# build as tiny per-spec-vocab indices, column-major, plus the vocabularies
# themselves (names resolved from the committed caches scripts/fetch_names.py
# maintains — this file NEVER fetches; missing caches degrade to null names).
# Interface is change-controlled by fleet/blueprints/builds_tab.md §1: emit
# exactly that shape.
BUILDS_SLOTS = (0, 1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16)
BUILDS_BIG_SLOTS = frozenset((12, 13, 15, 16))   # trinkets + weapons
BUILDS_ITEM_CAP, BUILDS_ITEM_CAP_BIG = 24, 40
BUILDS_ENCH_CAP, BUILDS_BUILD_CAP = 15, 40       # 15 = nibble-bound
BUILDS_ESLOT_MIN_SHARE = 0.01   # a slot ships an enchant column when >=1%
                                # of gear-known records carry an ench there
# Sizing (§1.4). The 3.0 MB target was UNREACHABLE: the lowest non-refusing
# rung measured 3.29 MB gz at level 6, so the ladder bottomed out on every run
# and shipped the most degraded document it can build — halved item caps AND no
# enchants — while still overshooting. Every slot's vocabulary then saturated at
# 12/20 and the truncated remainder (14-31% of wearers, larger than the top
# named item on the diverse slots) became the tile's "winner". Measured against
# the live document, re-serialised byte-exactly and with the whole zero bucket
# spread over the restored tail (an upper bound): full caps 24/40 = 3.90 MB gz
# L6, 18/30 = 3.76 MB, 12/20 = 3.29 MB. 4.3 MB clears full caps with 0.40 MB of
# slack and still leaves 1.10 MB (22%) under the hard cap, so the ladder stays a
# live safety net rather than dead code. The hard cap does NOT move: it is what
# keeps the character screen from vanishing entirely.
BUILDS_GZ_TARGET = 4_300_000
BUILDS_GZ_CAP = 5_000_000

NAMES_ITEMS = ROOT / "data" / "names_items.json"
NAMES_ENCHANTS = ROOT / "data" / "names_enchants.json"
CRAFTED_IDS = ROOT / "data" / "crafted_ids.json"
EMB_MARKERS = ROOT / "data" / "emb_markers.json"
EMB_IDENTITY = ROOT / "data" / "emb_identity.json"
EMB_ITEMS = ROOT / "data" / "emb_items.json"
EMB_OVERRIDES = ROOT / "data" / "emb_overrides.json"
NAMES_ICONS = ROOT / "data" / "names_icons.json"
ICONS_SRC = ROOT / "data" / "processed" / "icons"


def _load_json(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _name_caches():
    """The fetch_names.py caches, id-keyed by int. Missing or torn files
    degrade to empty — every name then ships null and the client falls back
    to #id links; the build itself never fetches and never fails on this."""
    items = {int(k): v for k, v in _load_json(NAMES_ITEMS, {}).items()
             if str(k).isdigit() and isinstance(v, dict)}
    enchs = {int(k): v for k, v in _load_json(NAMES_ENCHANTS, {}).items()
             if str(k).isdigit()}
    crafted = {v for v in _load_json(CRAFTED_IDS, []) if isinstance(v, int)}
    markers = {int(v) for v in _load_json(EMB_MARKERS, [])
               if isinstance(v, int) or str(v).isdigit()}
    icons = {int(k): v for k, v in _load_json(NAMES_ICONS, {}).items()
             if str(k).isdigit() and isinstance(v, str) and v}
    return items, enchs, crafted, _emb_cfg(), markers, icons


def _emb_cfg() -> dict:
    """The embellishment identity map (v3): the db2-derived id set, its
    names, the intrinsically-embellished items, and the run diagnostics
    fetch_names recorded -- with data/emb_overrides.json (HUMAN ONLY) at
    top precedence over both ids and names. Every part degrades to empty:
    no identity map simply means every embellished item falls into the one
    generic bucket, which is the previous behaviour, never a wrong name."""
    doc = _load_json(EMB_IDENTITY, {})
    if not isinstance(doc, dict):
        doc = {}
    ov = _load_json(EMB_OVERRIDES, {})
    if not isinstance(ov, dict):
        ov = {}
    names = {int(k): v for k, v in (doc.get("names") or {}).items()
             if str(k).isdigit() and isinstance(v, str) and v}
    ids = {v for v in (doc.get("ids") or []) if isinstance(v, int)}
    for k, v in (ov.get("names") or {}).items():
        if str(k).isdigit() and isinstance(v, str) and v:
            names[int(k)] = v                  # human wins
            ids.add(int(k))
    ids |= {v for v in (ov.get("ids") or []) if isinstance(v, int)}
    intrinsic = {int(k) for k in _load_json(EMB_ITEMS, {})
                 if str(k).isdigit()}
    run = doc.get("run") if isinstance(doc.get("run"), dict) else {}
    return {"ids": ids, "names": names, "intrinsic": intrinsic, "run": run,
            "overrides": len(ov.get("names") or {}) + len(ov.get("ids") or [])}


def sync_icons(name: str) -> None:
    """Publish the collector-downloaded icon images under site/ and docs/.

    Copies only new or changed files and NEVER deletes: the icon set is
    grow-only (like the caches feeding it), and a deletion here would 404
    tiles on pages already open in browsers. Missing source directory means
    icon collection has not run yet -- nothing to do, not an error.
    """
    if not ICONS_SRC.is_dir():
        return
    srcs = sorted(p for p in ICONS_SRC.iterdir() if p.is_file())
    copied = 0
    for d in SITE_DIRS:
        dest_dir = d / "icons"
        dest_dir.mkdir(parents=True, exist_ok=True)
        for src in srcs:
            dest = dest_dir / src.name
            if dest.exists() and dest.stat().st_size == src.stat().st_size:
                continue
            shutil.copyfile(src, dest)
            copied += 1
    if copied:
        print(f"[{name}] icons: {copied} new/changed copies published "
              f"({len(srcs)} in the store)")


# --- talent trees (site/talents.json.gz) — blueprint §1.7 -------------------
TRAIT_GEOMETRY = ROOT / "data" / "trait_geometry.json"
NAMES_SPELLS = ROOT / "data" / "names_spells.json"


def _trait_caches():
    """fetch_traits.py's caches: (geometry, spells). Missing files degrade
    to empty -- no talents doc, no sel annotations, never an error."""
    geo = _load_json(TRAIT_GEOMETRY, {})
    if not isinstance(geo, dict) or not isinstance(geo.get("trees"), dict):
        geo = {}
    spells = {k: v for k, v in _load_json(NAMES_SPELLS, {}).items()
              if isinstance(v, dict)}
    return geo, spells


def _trait_journal_pass(wanted: dict[str, set]) -> dict[str, dict]:
    """One extra walk over the raw journal for talent-tree material.

    Per "Class|Spec": the modal WCL spec id, the union of TraitNodeENTRY ids
    its players ever allocated (the journal's talents.tree ids are entry
    ids -- verified against hero_talent_map and wago's TraitNodeXTraitNode-
    Entry), and, for the build identities listed in `wanted`, the canonical
    selection blob. For hash-identified builds every record of a hash
    serializes to the same blob by construction (the hash IS the blob's
    md5); a string-identified build could vary, so the modal blob wins and
    any variance is counted and reported by the caller.
    """
    src = GEAR_JOURNAL if GEAR_JOURNAL.exists() else GEAR_EXPORT
    out: dict[str, dict] = {}
    if not src.exists():
        return out
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
            tal = rec.get("talents")
            if not isinstance(tal, dict):
                continue
            tree = tal.get("tree")
            blob = _tree_blob(tree)
            sk = f"{rec.get('class')}|{rec.get('spec')}"
            o = out.setdefault(sk, {"specid": Counter(), "entries": set(),
                                    "sel": {}})
            spec_id = tal.get("specID")
            if isinstance(spec_id, int) and spec_id:
                o["specid"][spec_id] += 1
            if blob is None:
                continue
            for part in blob.split("|"):
                o["entries"].add(int(part.split(":", 1)[0]))
            if sk in wanted:
                build = _record_build_id(tal)
                if build in wanted[sk]:
                    o["sel"].setdefault(build, Counter())[blob] += 1
    for o in out.values():
        o["specid"] = (o["specid"].most_common(1)[0][0]
                       if o["specid"] else None)
    return out


def _node_entries(entries: dict) -> dict[str, list]:
    """node id -> its entry ids sorted numerically. THE one ordering both
    the talents doc's per-node "es" lists and _sel_pairs' entry indexes are
    defined over -- they must never disagree."""
    out: dict[str, list] = {}
    for eid in sorted(entries, key=int):
        out.setdefault(str(entries[eid][0]), []).append(eid)
    return out


def _sel_pairs(blob: str, entries: dict, node_entries: dict) -> list:
    """Canonical selection blob -> [[nodeId, rank], ...] via the geometry
    entry->node mapping. A CHOICE node (>1 entries) carries a third element:
    the picked entry's index within _node_entries order, matching the
    talents doc's "es" list. Unmapped entry ids are dropped (the client dims
    what it cannot light) and duplicate nodes keep the higher rank."""
    nodes: dict[int, tuple] = {}
    for part in blob.split("|"):
        eid, rank = part.split(":", 1)
        ent = entries.get(eid)
        if ent:
            nid = int(ent[0])
            eids = node_entries.get(str(nid), [])
            idx = eids.index(eid) if len(eids) > 1 else None
            old = nodes.get(nid)
            if old is None or int(rank) > old[0]:
                nodes[nid] = (int(rank), idx)
    return [[n, r] if i is None else [n, r, i]
            for n, (r, i) in sorted(nodes.items())]


def talents_doc(name: str, usage: dict | None = None) -> str | None:
    """The lazy talent-tree document (caller gzips it), or None.

    Per spec key: the class pane, the spec pane and the observed hero trees
    of its class tree, each as {"nodes":[{"id","x","y","r","n","ic","t"}],
    "edges":[[a,b],...]} with true db2 grid positions. Node membership is
    the union of nodes the spec's players ever allocated (journal-wide;
    with hundreds of thousands of records the viable tree is covered) --
    hero nodes split off by their TraitSubTreeID, the rest split class-vs-
    spec at the largest PosX gap, which is how the two pages are laid out
    in the data. Names/icons come from the spell cache (icons live in the
    shared self-hosted store); missing names ship null.

    A "classes" + "classRef" indirection dedupes the class pane when it
    saves real gzipped bytes -- both variants are measured and the smaller
    ships, loudly.
    """
    geo, spells = _trait_caches()
    if not geo:
        print(f"[{name}] no trait geometry cache; talents doc omitted")
        return None
    if usage is None:
        usage = _trait_journal_pass({})
    if not usage:
        print(f"[{name}] no talent trees in the journal; talents doc omitted")
        return None
    entries = geo.get("entries", {})
    subtrees = geo.get("subtrees", {})
    node_entries = _node_entries(entries)  # node -> its entry ids, stable

    def tree_obj(tid: str, nids: list[int]) -> dict:
        tgeo = geo["trees"][tid]
        nodes_out = []
        for nid in sorted(nids):
            g = tgeo["nodes"][str(nid)]
            eids = node_entries.get(str(nid), [])
            r = max((int(entries[e][1]) for e in eids), default=1)
            n = ic = None
            if eids:
                e0 = entries[eids[0]]
                sp = spells.get(str(e0[2])) or {}
                n = e0[3] or sp.get("n")
                ic = sp.get("ic")
            node = {"id": nid, "x": g[0], "y": g[1], "r": r, "n": n,
                    "ic": ic, "t": g[2]}
            if eids:
                # wowhead /spell= link target; choice nodes list every
                # option in _node_entries order (sel's third element indexes
                # into this list)
                node["s"] = int(e0[2])
                if len(eids) > 1:
                    node["es"] = [
                        {"s": int(entries[e][2]),
                         "n": entries[e][3]
                              or (spells.get(str(entries[e][2])) or {})
                              .get("n"),
                         "ic": (spells.get(str(entries[e][2])) or {})
                               .get("ic")}
                        for e in eids]
            nodes_out.append(node)
        keep = set(nids)
        edges = [e for e in tgeo["edges"] if e[0] in keep and e[1] in keep]
        return {"nodes": nodes_out, "edges": edges}

    # Which tree does a spec belong to? The journal's talents payload carries
    # ONLY "tree" in practice -- 430,507/430,507 records have exactly that key,
    # no specID and no import string -- so the WCL spec id the geometry's
    # "specs" map is keyed by is simply never there, and keying off it omitted
    # the whole document. Identify the tree by ENTRY MEMBERSHIP instead: the
    # tree holding the most of the entries this spec's players actually
    # allocated. specID stays a fast path for journals that do carry it.
    node_of = {eid: str(ent[0]) for eid, ent in entries.items()}

    def tree_for(u) -> str | None:
        sid = u.get("specid")
        if sid:
            tid = str(geo.get("specs", {}).get(str(sid), ""))
            if tid in geo["trees"]:
                return tid
        best, best_n = None, 0
        for tid, tg in geo["trees"].items():
            n = sum(1 for e in u["entries"] if node_of.get(str(e)) in tg["nodes"])
            if n > best_n:
                best, best_n = tid, n
        return best

    per_spec: dict[str, dict] = {}
    class_panes: dict[str, tuple] = {}     # cls -> (tid, set of node ids)
    n_by_overlap = 0
    for sk in sorted(usage):
        u = usage[sk]
        tid = tree_for(u)
        if tid and not u.get("specid"):
            n_by_overlap += 1
        tgeo = geo["trees"].get(tid) if tid else None
        if not tgeo or not u["entries"]:
            continue
        used_nodes = {int(entries[str(e)][0]) for e in u["entries"]
                      if str(e) in entries
                      and str(entries[str(e)][0]) in tgeo["nodes"]}
        if not used_nodes:
            continue
        hero: dict[int, list] = {}
        rest = []
        for nid in used_nodes:
            st = tgeo["nodes"][str(nid)][3]
            if st:
                hero.setdefault(st, []).append(nid)
            else:
                rest.append(nid)
        # class page left, spec page right, separated by the largest X gap
        xs = sorted({tgeo["nodes"][str(n)][0] for n in rest})
        cut = None
        if len(xs) > 1:
            gaps = [(xs[i + 1] - xs[i], xs[i + 1]) for i in range(len(xs) - 1)]
            g, at = max(gaps)
            cut = at if g >= 900 else None
        cls_nodes = [n for n in rest
                     if cut is None or tgeo["nodes"][str(n)][0] < cut]
        spec_nodes = [n for n in rest if n not in set(cls_nodes)]
        cls = sk.split("|", 1)[0]
        ck = class_panes.setdefault(cls, (tid, set()))
        if ck[0] == tid:
            ck[1].update(cls_nodes)
        per_spec[sk] = {"tid": tid, "cls_nodes": set(cls_nodes),
                        "spec": tree_obj(tid, spec_nodes),
                        "hero": {subtrees.get(str(st), f"#{st}"):
                                 tree_obj(tid, nids)
                                 for st, nids in sorted(hero.items())}}
    if not per_spec:
        print(f"[{name}] no spec matched the trait geometry "
              f"({len(usage)} specs in the journal pass, "
              f"{len(geo.get('trees', {}))} trees, "
              f"{len(entries)} entries in the geometry); talents doc omitted")
        return None
    if n_by_overlap:
        print(f"[{name}] talent trees: {n_by_overlap}/{len(per_spec)} specs "
              f"identified by entry overlap (no specID in the journal)")

    # variant A: each spec carries its own class pane
    doc_a = {"v": 1, "trees": {
        sk: {"class": tree_obj(v["tid"], sorted(v["cls_nodes"])),
             "spec": v["spec"], "hero": v["hero"]}
        for sk, v in per_spec.items()}}
    # variant B: one shared class pane per class, referenced by name
    doc_b = {"v": 1, "classes": {
        cls: tree_obj(tid, sorted(nids))
        for cls, (tid, nids) in sorted(class_panes.items())},
        "trees": {sk: {"classRef": sk.split("|", 1)[0],
                       "spec": v["spec"], "hero": v["hero"]}
                  for sk, v in per_spec.items()}}
    ja = json.dumps(doc_a, separators=(",", ":"))
    jb = json.dumps(doc_b, separators=(",", ":"))
    gz_a, gz_b = (len(gzip.compress(ja.encode(), 6)),
                  len(gzip.compress(jb.encode(), 6)))
    print(f"[{name}] talents doc: {len(per_spec)} specs; per-spec class "
          f"panes {gz_a / 1024:.0f} KB gz vs classRef {gz_b / 1024:.0f} KB "
          f"gz -> {'classRef' if gz_b < gz_a else 'per-spec'}")
    return jb if gz_b < gz_a else ja


def builds_sidecar(df, journal, name: str, enc: str | None = None,
                   target: int = BUILDS_GZ_TARGET,
                   cap: int = BUILDS_GZ_CAP) -> str | None:
    """The Builds sidecar document (caller gzips it), or None when nothing
    is coverable or the ladder refuses to ship.

    One walk over df in payload row order — the same alignment discipline as
    stats_sidecar, never a separate join. A covered row is a journal record
    (meta_from_gear_journal) holding a gear list or an import string; its
    fl byte says which (bit0 gear, bit1 build). Row values are 1-based
    indices into the row's OWN spec vocabulary (0 = other/empty), enchants
    nibble-packed over the measured eslots. Vocab entries are split by
    embellishment identity — an item id worn plain and embellished is two
    entries — and annotated cr/emb/ilvl/n from the fetch_names caches.

    Ladder, loud at every rung: ship the smaller of dense/sparse at full
    caps; over the target, drop the enchant columns and vocab (eslots ships
    [] so the client's array check passes and the enchant section simply
    feature-detects off); still over, step the item caps down 24/40 -> 18/30
    -> 12/20; over the hard cap, ship nothing. Enchants go BEFORE the item
    vocabulary because a truncated item vocabulary does not merely hide
    entries — it pools them into an "other / none" bucket that can outrank
    every real item on a slot.
    """
    builds_sidecar.usage = None      # trait-pass result, reused by build()
    if not journal:
        print(f"[{name}] no builds/gear in the gear journal; "
              f"builds sidecar omitted")
        return None
    item_names, ench_names, crafted, embc, emb_markers, icon_names = \
        _name_caches()
    emb_ids, emb_names = embc["ids"], embc["names"]
    emb_intrinsic = embc["intrinsic"]
    EMB: Counter = Counter()
    emb_labels: Counter = Counter()
    emb_cfgs: set[tuple] = set()       # distinct (item id, bonus tuple)

    def emb_of(item: dict):
        """Embellishment identity for a journaled item, or None.

        Embellished iff the bonus list hits a MARKER id (the Embellished
        limit-category bonus, data/emb_markers.json) or the item is one of
        the intrinsically-embellished items. IDENTITY is then set
        membership against the db2-derived identity set -- never a walk,
        never a guess:
          * exactly one identity id, named  -> that bonus id (the vocab
            entry splits per embellishment, which is the point)
          * an identity id present but unnamed, or a marker with no
            identity id -> -1, the one generic bucket ("embellished")
          * TWO identity ids -> -1 and a CONFLICT count. Two is impossible
            under ItemLimitCategory 512 Quantity=2, so it is evidence the
            model broke, not a tie to break; v2 picked sorted()[0] here,
            and a smallest-id pick is a miniature of the v1 bug.
        Non-identity bonus ids (stat missives, sparks, quality and ilvl
        bonuses) can never split identity because they are not in the set.
        """
        bonus = item.get("bonus")
        if not isinstance(bonus, list):
            bonus = []          # an intrinsic item needs no bonus list
        marked = any(b in emb_markers for b in bonus)
        if crafted and item.get("id") in crafted:
            emb_cfgs.add((item["id"], tuple(bonus)))     # AUDIT population
        if not marked and item.get("id") not in emb_intrinsic:
            return None
        hits = [b for b in bonus if b in emb_ids]
        if len(hits) > 1:
            EMB["conflict"] += 1
            EMB["marked"] += 1
            return -1
        EMB["marked"] += 1
        if len(hits) == 1 and emb_names.get(hits[0]):
            EMB["named"] += 1
            emb_labels[emb_names[hits[0]]] += 1
            return hits[0]
        EMB["known_unnamed" if hits else "unidentified"] += 1
        return -1

    # ---- the one df-order walk: parse each covered row once
    n = len(df)
    rows_c = []          # (payload i, "Class|Spec", slotkeys, enchs, build, fl)
    gear_known = 0
    ench_hits: Counter = Counter()
    gkey: Counter = Counter()      # gear-item key presence, for the eslots diagnostic
    for i, (code, fid, ch, sv, cls, spec) in enumerate(zip(
            df["report_code"], df["fight_id"], df["character"],
            df["server"], df["class"], df["spec"])):
        rec = journal.get(_gear_key(code, fid, ch, sv))
        if rec is None:
            continue
        gear = rec.get("gear")
        build = rec.get("build")
        fl = (1 if isinstance(gear, list) else 0) | (2 if build else 0)
        if not fl:
            continue
        slotkeys: list[tuple | None] = [None] * len(BUILDS_SLOTS)
        enchs: dict[int, int] = {}
        if isinstance(gear, list):
            gear_known += 1
            for k, s in enumerate(BUILDS_SLOTS):
                it = gear[s] if s < len(gear) else None
                if isinstance(it, dict) and it.get("id"):
                    slotkeys[k] = (it["id"], emb_of(it), it.get("ilvl"))
            for s, it in enumerate(gear):
                if not isinstance(it, dict):
                    continue
                gkey["items"] += 1
                for _k in ("ilvl", "set", "ench", "gems", "bonus"):
                    if it.get(_k):
                        gkey[_k] += 1
                if it.get("ench"):
                    ench_hits[s] += 1
                    enchs[s] = it["ench"]
        rows_c.append((i, f"{cls}|{spec}", slotkeys, enchs, build, fl))
    if not rows_c:
        print(f"[{name}] no journal-covered payload rows; "
              f"builds sidecar omitted")
        return None
    eslots = sorted(s for s, c in ench_hits.items()
                    if c >= BUILDS_ESLOT_MIN_SHARE * max(gear_known, 1))
    # The Enchants pane has been silently empty in production. eslots is
    # MEASURED, so an empty list has two very different causes and the log
    # never said which: the journal carries no enchant at all (the collector's
    # field is absent from WCL's payload -- the same class of finding as the
    # missing talent import strings and specIDs), or it carries some but no
    # slot clears the 1% bar. Say which, with the numbers, whenever the pane
    # would ship empty.
    if not eslots:
        floor = BUILDS_ESLOT_MIN_SHARE * max(gear_known, 1)
        top = ", ".join(f"slot {s}: {c:,}"
                        for s, c in ench_hits.most_common(5)) or "none"
        health(f"[{name}] NO ENCHANT COLUMNS -- the Enchants pane will be empty. "
              f"{gkey['items']:,} gear items over {gear_known:,} gear-known rows; "
              f"per-item key presence ilvl={gkey['ilvl']:,} set={gkey['set']:,} "
              f"ench={gkey['ench']:,} gems={gkey['gems']:,} bonus={gkey['bonus']:,}. "
              f"Slot floor is {BUILDS_ESLOT_MIN_SHARE:.0%} = {floor:,.0f}; "
              f"best slots: {top}")

    # ---- per-spec tallies over ALL journal-known rows of the spec (per the
    # contract: vocab counts are df-wide, not lens- or cohort-sliced)
    tallies: dict[str, dict] = {}
    for _, sk, slotkeys, enchs, build, fl in rows_c:
        t = tallies.setdefault(sk, {
            "it": [Counter() for _ in BUILDS_SLOTS],
            "flat": [Counter() for _ in BUILDS_SLOTS],
            "ilvl": [{} for _ in BUILDS_SLOTS],
            "en": {s: Counter() for s in eslots},
            "bld": Counter()})
        for k, key in enumerate(slotkeys):
            if key is None:
                continue
            ident = key[:2]                       # (item id, emb identity)
            t["it"][k][ident] += 1
            # the SAME tally with embellishment identity collapsed back to
            # one generic bucket: the only way to predict, rather than
            # discover, which doll tiles the identity split moves
            t["flat"][k][(key[0], None if key[1] is None else -1)] += 1
            if key[2]:
                t["ilvl"][k].setdefault(ident, []).append(key[2])
        for s in eslots:
            if s in enchs:
                t["en"][s][enchs[s]] += 1
        if build:
            t["bld"][build] += 1

    # §1.7: selection sets for the emitted top builds -- one extra journal
    # pass keyed by the full-cap ranking (a superset of every ladder rung);
    # emission needs the geometry's entry->node mapping, so no geometry
    # cache simply means no sel keys (feature-detected client-side)
    geo, _ = _trait_caches()
    wanted = {sk: {s for s, _ in
                   sorted(t["bld"].items(),
                          key=lambda kv: (-kv[1], kv[0]))[:BUILDS_BUILD_CAP]}
              for sk, t in tallies.items() if t["bld"]}
    usage = _trait_journal_pass(wanted) if wanted else {}
    builds_sidecar.usage = usage
    sel_by: dict[tuple, list] = {}
    geo_entries = geo.get("entries", {}) if geo else {}
    if geo_entries:
        n_var = 0
        geo_node_entries = _node_entries(geo_entries)
        for sk, o in usage.items():
            for build, blobs in o["sel"].items():
                if len(blobs) > 1:
                    n_var += 1
                pairs = _sel_pairs(blobs.most_common(1)[0][0], geo_entries,
                                   geo_node_entries)
                if pairs:
                    sel_by[(sk, build)] = pairs
        if n_var:
            print(f"[{name}] WARNING: {n_var} build identities carry more "
                  f"than one selection variant (modal set shipped) - "
                  f"expected only for string-identified builds")

    def make_doc(item_cap: int, item_cap_big: int, build_cap: int,
                 with_en: bool) -> str:
        """Both encodings at these caps; the smaller gz wins, loudly."""
        # vocabularies + the (id, emb) -> 1-based index lookups
        specs_out: dict[str, dict] = {}
        lookups: dict[str, dict] = {}
        for sk in sorted(tallies):
            t = tallies[sk]
            items_v, it_lk = [], []
            for k, s in enumerate(BUILDS_SLOTS):
                capk = item_cap_big if s in BUILDS_BIG_SLOTS else item_cap
                ranked = sorted(t["it"][k].items(),
                                key=lambda kv: (-kv[1], kv[0][0],
                                                kv[0][1] or 0))[:capk]
                it_lk.append({ident: j + 1
                              for j, (ident, _) in enumerate(ranked)})
                col = []
                for (iid, emb), _cnt in ranked:
                    ilvls = t["ilvl"][k].get((iid, emb))
                    e: dict = {"id": iid,
                               "n": (item_names.get(iid) or {}).get("n"),
                               "ilvl": (int(round(float(np.median(ilvls))))
                                        if ilvls else None)}
                    ic = icon_names.get(iid)
                    if ic:                      # §1.6: optional, self-hosted
                        e["ic"] = ic
                    if iid in crafted:
                        e["cr"] = 1
                    if emb is not None:
                        # get(-1) is None by construction: the generic
                        # bucket. Never a "#<id>" placeholder -- the client
                        # would swallow it and render the section EMPTIER.
                        e["emb"] = emb_names.get(emb) or "embellished"
                    col.append(e)
                items_v.append(col)
            en_v, en_lk = [], []
            for s in eslots:
                ranked = sorted(t["en"][s].items(),
                                key=lambda kv: (-kv[1], kv[0]))
                ranked = ranked[:BUILDS_ENCH_CAP]
                en_lk.append({eid: j + 1 for j, (eid, _) in enumerate(ranked)})
                en_v.append([{"id": eid, "n": ench_names.get(eid)}
                             for eid, _ in ranked])
            b_ranked = sorted(t["bld"].items(),
                              key=lambda kv: (-kv[1], kv[0]))[:build_cap]
            entry = {"items": items_v}
            if with_en:
                entry["ench"] = en_v
            b_out = []
            for s, c in b_ranked:
                b: dict = {"s": s, "n": c}
                sel = sel_by.get((sk, s))
                if sel:                        # §1.7 selection widening
                    b["sel"] = sel
                b_out.append(b)
            entry["builds"] = b_out
            if b_ranked:
                # §1.5 addendum: "t:" values are tree hashes, not pasteable
                # import strings; the block-level flag lets the client
                # suppress copy affordances wholesale (per-value, the "t:"
                # prefix itself is the rule and wins in mixed data)
                entry["bkind"] = ("hash" if all(s.startswith("t:")
                                                for s, _ in b_ranked)
                                  else "string")
            specs_out[sk] = entry
            lookups[sk] = {"it": it_lk, "en": en_lk,
                           "bld": {s: j + 1
                                   for j, (s, _) in enumerate(b_ranked)}}

        # columns over covered rows, scattered to full length for dense
        m = len(rows_c)
        the_eslots = eslots if with_en else []
        n_en = (len(the_eslots) + 1) // 2
        fl_a = np.zeros(m, dtype="u1")
        it_a = np.zeros((len(BUILDS_SLOTS), m), dtype="u1")
        en_a = np.zeros((n_en, m), dtype="u1")
        bld_a = np.zeros(m, dtype="u1")
        idx_a = np.zeros(m, dtype="<u4")
        for j, (i, sk, slotkeys, enchs, build, fl) in enumerate(rows_c):
            lk = lookups[sk]
            idx_a[j] = i
            fl_a[j] = fl
            for k, key in enumerate(slotkeys):
                if key is not None:
                    it_a[k, j] = lk["it"][k].get(key[:2], 0)
            if with_en:
                for jj, s in enumerate(the_eslots):
                    v = lk["en"][jj].get(enchs.get(s), 0) if s in enchs else 0
                    if jj % 2:
                        en_a[jj >> 1, j] |= v << 4
                    else:
                        en_a[jj >> 1, j] |= v
            if build:
                bld_a[j] = lk["bld"].get(build, 0)

        def doc_for(enc: str) -> str:
            if enc == "dense":
                def scat(a):
                    full = np.zeros(n, dtype=a.dtype)
                    full[idx_a] = a
                    return full
                fl_c, bld_c = scat(fl_a), scat(bld_a)
                it_c = [scat(it_a[k]) for k in range(len(BUILDS_SLOTS))]
                en_c = [scat(en_a[k]) for k in range(n_en)]
            else:
                fl_c, bld_c = fl_a, bld_a
                it_c = [it_a[k] for k in range(len(BUILDS_SLOTS))]
                en_c = [en_a[k] for k in range(n_en)]

            def b64(a):
                return base64.b64encode(a.tobytes()).decode()
            obj: dict = {"v": 1, "n": n, "enc": enc,
                         "slots": list(BUILDS_SLOTS),
                         "eslots": list(the_eslots)}
            if enc == "sparse":
                obj["idx"] = b64(idx_a)
            cols: dict = {"fl": b64(fl_c), "it": [b64(a) for a in it_c]}
            if with_en:
                cols["en"] = [b64(a) for a in en_c]
            cols["bld"] = b64(bld_c)
            obj["cols"] = cols
            obj["specs"] = specs_out
            return json.dumps(obj, separators=(",", ":"))

        if enc in ("dense", "sparse"):        # forced, for tests
            return doc_for(enc)
        dense, sparse = doc_for("dense"), doc_for("sparse")
        gz_d = len(gzip.compress(dense.encode(), 6))
        gz_s = len(gzip.compress(sparse.encode(), 6))
        print(f"[{name}] builds sidecar (caps {item_cap}/{item_cap_big}/"
              f"{build_cap}, en={'y' if with_en else 'n'}): dense "
              f"{gz_d / 1e6:.2f} MB gz vs sparse {gz_s / 1e6:.2f} MB gz -> "
              f"{'dense' if gz_d <= gz_s else 'sparse'}")
        return dense if gz_d <= gz_s else sparse

    health(f"[{name}] builds sidecar: {len(rows_c):,}/{n:,} rows covered "
          f"({len(rows_c) / n:.0%}), {len(tallies)} specs, "
          f"eslots {eslots}")
    # Degradation ladder (§1.4), a STAIRCASE rather than a cliff. Two ordering
    # rules, both learned from production:
    #  * the enchant block is traded away BEFORE the item vocabulary degrades.
    #    The item vocabulary is what the gear pane is made of; when it is
    #    truncated, the leftovers collapse into one "other / none" bucket that
    #    can outrank every real item on a slot. The enchant section merely
    #    feature-detects off, losing a section but never lying about one.
    #  * the item rungs step 24 -> 18 -> 12 instead of halving straight to 12,
    #    so a small overshoot costs a little tail rather than all of it.
    ladder = [(BUILDS_ITEM_CAP, BUILDS_ITEM_CAP_BIG, BUILDS_BUILD_CAP, True),
              (BUILDS_ITEM_CAP, BUILDS_ITEM_CAP_BIG, BUILDS_BUILD_CAP, False),
              (18, 30, 32, False),
              (BUILDS_ITEM_CAP // 2, BUILDS_ITEM_CAP_BIG // 2, 24, False)]
    # Measure at the level the file is actually WRITTEN at (9), not 6. The
    # ladder decides which features ship, so it must weigh the bytes that go
    # on the wire; level 6 reads ~1.3% heavy here, which is enough to trade
    # away a whole section on a near-miss.
    def gz(d: str) -> int:
        return len(gzip.compress(d.encode(), 9))

    rung = ladder[0]
    doc = make_doc(*rung)
    sizes = [(rung, gz(doc))]
    for nxt in ladder[1:]:
        if sizes[-1][1] <= target:
            break
        ic, icb, bc, wen = nxt
        health(f"[{name}] builds sidecar {sizes[-1][1] / 1e6:.2f} MB gz is over "
               f"the {target / 1e6:.1f} MB target; stepping down to caps "
               f"{ic}/{icb}, builds {bc}, en={'y' if wen else 'n'}"
               + ("" if wen else " (the enchant block feature-detects off)"))
        rung = nxt
        doc = make_doc(*rung)
        sizes.append((rung, gz(doc)))
    # The whole ladder, on the record: what each rung would have cost and what
    # actually shipped. Without this the only way to learn why a section is
    # missing is to reproduce the build.
    health(f"[{name}] ladder: "
           + " | ".join(f"caps {r[0]}/{r[1]} builds {r[2]} "
                        f"en={'y' if r[3] else 'n'} -> {sz / 1e6:.2f} MB"
                        for r, sz in sizes)
           + f" || SHIPPED caps {rung[0]}/{rung[1]} builds {rung[2]} "
             f"en={'y' if rung[3] else 'n'} at {sizes[-1][1] / 1e6:.2f} MB "
             f"(target {target / 1e6:.1f}, hard cap {cap / 1e6:.1f})")
    _emb_health(name, embc, emb_markers, crafted, EMB, emb_labels, emb_cfgs,
                tallies, rung)
    if sizes[-1][1] > cap:
        health(f"[{name}] builds sidecar over the {cap / 1e6:.1f} MB hard "
               f"cap even without enchants; NOT shipped")
        return None
    return doc


def _emb_health(name, embc, markers, crafted, EMB, labels, cfgs, tallies,
                rung) -> None:
    """The embellishment proof block, published in site/build_health.txt.

    Two versions of this feature failed in production and BOTH were
    invisible, because the only thing the build ever said about
    embellishments was nothing. Every line here is computed from data this
    run already holds; the first is a greppable verdict so a human (or the
    workflow) can check one token. v2's signature would have read
    "verdict: DEAD" with "named 0 (0.0%)"; v1's would have surfaced on the
    AUDIT line as a non-identity bonus id scoring ratio 1.000 at real
    support.
    """
    run = embc.get("run") or {}
    ids, names = embc["ids"], embc["names"]
    gaps = sorted(b for b in ids if not names.get(b))
    bad = list(run.get("bad_children") or [])
    leaked = list(run.get("leaked") or [])
    unbacked = list(run.get("unbacked") or [])
    marked = EMB["marked"]
    named = EMB["named"]
    share = (100.0 * named / marked) if marked else 0.0
    problems = []
    if not ids:
        problems.append("no identity map (db2 never answered)")
    if marked and not named:
        problems.append(f"{marked:,} marked carries, 0 named")
    elif share < 50.0 and marked:
        problems.append(f"only {share:.1f}% of embellished carries named")
    elif marked >= 50 and len(labels) <= 1:
        # v2's exact signature: healthy detection, ONE label everywhere.
        # Volume-guarded so a fixture with one embellishment is not an alarm.
        problems.append("one distinct label across every spec")
    if EMB["conflict"]:
        problems.append(f"{EMB['conflict']:,} CONFLICT carries")
    if bad:
        problems.append(f"{len(bad)} marker trees with != 2 children")
    if leaked:
        problems.append(f"leak guard dropped {len(leaked)}")
    if ids & markers:
        problems.append("identity set intersects the marker set")
    if run.get("empty_derivation"):
        problems.append("derivation returned 0 ids; cached map retained")
    if not run.get("ok"):
        problems.append("db2 did not answer this run (map is the cached one)")
    # ---- AUDIT: journal co-occurrence, the validator (never the classifier).
    # Computed BEFORE the verdict because it is evidence the verdict must
    # weigh: the realistic v1 shape (optional reagents wrongly in the identity
    # set, all named, no conflicts) trips nothing else, so a verdict blind to
    # this audit printed "ok" over exactly the failure it was built to catch.
    seen: Counter = Counter()
    withmk: Counter = Counter()
    items_of: dict = {}
    for iid, tup in cfgs:
        s = set(tup)
        mk = bool(s & markers)
        for b in s:
            if b in markers:
                continue
            seen[b] += 1
            items_of.setdefault(b, set()).add(iid)
            if mk:
                withmk[b] += 1
    SUP, NITEM = 12, 3
    qual = [b for b in seen
            if seen[b] >= SUP and len(items_of[b]) >= NITEM]
    id_r = [(withmk[b] / seen[b], b) for b in qual if b in ids]
    no_r = [(withmk[b] / seen[b], b) for b in qual if b not in ids]
    warn = []
    if id_r and min(id_r)[0] < 1.0:
        warn.append(f"identity id {min(id_r)[1]} rides un-embellished items "
                    f"(ratio {min(id_r)[0]:.3f}) -- the db2 model broke")
    hot = sorted(b for r, b in no_r if r >= 1.0)
    if hot:
        warn.append(f"non-identity ids at ratio 1.000 with support: {hot} -- "
                    f"a new embellishment, or a false name from the other side")
    if warn:
        problems.append("co-occurrence audit: " + "; ".join(warn))

    verdict = ("ok" if not problems
               else ("DEAD" if marked and not named else "DEGRADED"))
    health(f"[{name}] [emb] verdict: {verdict}"
           + ("" if verdict == "ok" else " -- " + "; ".join(problems)))
    bym = run.get("by_marker") or {}
    health(f"[{name}] [emb] db2 map: {run.get('marker_trees', '?')} marker "
           f"trees ("
           + ", ".join(f"{k}:{v}" for k, v in sorted(bym.items()))
           + f", nested:{(run.get('marker_trees') or 0) - (run.get('direct') or 0)}"
           f"), {run.get('backed', '?')} reagent-backed -> {len(ids)} "
           f"identity ids, {len(names)} named, {len(gaps)} unnamed "
           f"{gaps if gaps else '[]'} | fetched {run.get('fetched', 0)}, "
           f"failures {run.get('failures', 0)} | wago "
           f"{'OK' if run.get('ok') else 'UNAVAILABLE'}")
    health(f"[{name}] [emb] invariants: direct marker trees with != 2 "
           f"children {len(bad)}/{run.get('direct', 0)} | max recursion "
           f"depth {run.get('depth', 0)} | identity&markers "
           f"{'EMPTY' if not (ids & markers) else sorted(ids & markers)} | "
           f"leak-guard dropped {len(leaked)}{leaked if leaked else ''} | "
           f"{len(unbacked)} unbacked trees skipped | intrinsic "
           f"{len(embc['intrinsic'])} | overrides {embc['overrides']}")
    health(f"[{name}] [emb] migrated {run.get('migrated', 0)} names from "
           f"names_bonus_emb2 (dropped {run.get('dropped_nulls', 0)} nulls)")

    # ---- vocab entries, pre-cap (the shipped counts are a subset of these)
    ent_emb = ent_cr = 0
    for t in tallies.values():
        for col in t["it"]:
            for (iid, emb) in col:
                if iid in crafted:
                    ent_cr += 1
                if emb is not None:
                    ent_emb += 1
    top = ", ".join(f"{n} {c:,}" for n, c in labels.most_common(5)) or "none"
    health(f"[{name}] [emb] journal: {ent_cr:,} crafted vocab entries "
           f"(pre-cap), {ent_emb:,} embellished | {marked:,} embellished "
           f"carries -> named {named:,} ({share:.1f}%), known-unnamed "
           f"{EMB['known_unnamed']:,}, unidentified {EMB['unidentified']:,}, "
           f"CONFLICT {EMB['conflict']:,} | labels {len(labels)} distinct | "
           f"top {top}")

    # ---- AUDIT: journal co-occurrence, the validator (never the classifier)
    # An identity bonus is the marker's SIBLING in one tree, so it can only
    # ever ride an embellished item: ratio exactly 1.000. A missive or spark
    # spreads over a partly-embellished recipe family and lands near the base
    # rate. Counted per DISTINCT crafted configuration, not per carry, so one
    # popular item cannot dominate. WARN BOTH WAYS.
    if not qual:
        health(f"[{name}] [emb] AUDIT co-occurrence: no bonus id reaches "
               f"support>={SUP} over >={NITEM} items -- not evaluated "
               f"({len(cfgs):,} crafted configurations seen)")
    else:
        health(f"[{name}] [emb] AUDIT co-occurrence: identity ids min "
               f"withMarker/seen "
               f"{(min(id_r)[0] if id_r else float('nan')):.3f} "
               f"({len(id_r)} ids, support>={SUP}, >={NITEM} items) | "
               f"non-identity max "
               f"{(max(no_r)[0] if no_r else 0.0):.3f} ({len(no_r)} ids) -> "
               + ("CLEAN" if not warn else "WARN: " + "; ".join(warn)))

    # ---- what the identity split costs, PREDICTED rather than discovered
    item_cap, item_cap_big = rung[0], rung[1]
    sat = sat_emb = evicted = 0
    tail = []
    winners = win_emb = flips = 0
    for t in tallies.values():
        for k, s in enumerate(BUILDS_SLOTS):
            capk = item_cap_big if s in BUILDS_BIG_SLOTS else item_cap
            col = t["it"][k]
            if not col:
                continue
            ranked = sorted(col.items(),
                            key=lambda kv: (-kv[1], kv[0][0], kv[0][1] or 0))
            tot = sum(col.values())
            if len(ranked) > capk:
                sat += 1
                if any(e[0][1] is not None for e in ranked[:capk]):
                    sat_emb += 1
                for (iid, emb), c in ranked[capk:]:
                    if emb is not None:
                        evicted += 1
                        tail.append(100.0 * c / max(tot, 1))
            winners += 1
            if ranked[0][0][1] is not None:
                win_emb += 1
            fr = sorted(t["flat"][k].items(),
                        key=lambda kv: (-kv[1], kv[0][0], kv[0][1] or 0))
            if fr and fr[0][0][0] != ranked[0][0][0]:
                flips += 1
    med = float(np.median(tail)) if tail else 0.0
    health(f"[{name}] [emb] vocab: {sat}/{winners} columns saturated at caps "
           f"{item_cap}/{item_cap_big}, {sat_emb} of them hold an emb entry | "
           f"the cap evicted {evicted} emb entries (median tail share "
           f"{med:.2f}%) | rank-1 entries carrying emb {win_emb}/{winners}, "
           f"{flips} of them differ from the un-split identity (doll tiles "
           f"that move)")


def build(name: str, cfg: dict) -> None:
    csv = ROOT / "data" / cfg["csv"]
    if not csv.exists():                       # tolerate an un-gzipped copy
        csv = csv.with_suffix("")
    if not csv.exists():
        print(f"[{name}] {cfg['csv']} missing — skipped")
        return
    df = pd.read_csv(csv)
    df = use_keystone_clock(df, name)
    df = sample_runs(df, name)
    for col in ("class", "spec", "hero_talent", "role", "region", "dungeon"):
        df[col] = df[col].fillna("Unknown").replace("", "Unknown")
    unknown_before = int((df["hero_talent"] == "Unknown").sum())
    hero_filled = resolve_hero_talents(df)
    if hero_filled:
        print(f"[{name}] hero talent recovered from abilities for "
              f"{hero_filled:,} of {unknown_before:,} Unknown parses")

    started = pd.to_datetime(pd.to_numeric(df["started_at"], errors="coerce"),
                             unit="ms", errors="coerce")
    day = ((started - EPOCH).dt.days).fillna(-1).astype(int)

    def enc(col):
        cats = sorted(df[col].unique())
        idx = {c: i for i, c in enumerate(cats)}
        return cats, df[col].map(idx).astype(int).tolist()

    classes, cls_arr = enc("class")
    specs, spec_arr = enc("spec")
    heroes, hero_arr = enc("hero_talent")
    dungeons, dun_arr = enc("dungeon")
    regions, reg_arr = enc("region")
    roles, role_arr = enc("role")
    run_ids = (df["report_code"].astype(str) + ":" + df["fight_id"].astype(str))
    run_arr = pd.factorize(run_ids)[0].tolist()
    tier = tier_pieces(df, name)

    # character identity (name@server@region), for distinct-player counts
    char_ids = (df["character"].fillna("?").astype(str) + "@"
                + df["server"].fillna("?").astype(str) + "@" + df["region"])
    char_codes, char_keys = pd.factorize(char_ids)
    char_arr = char_codes.tolist()
    # Player rating is a property of the character, not the parse, so it ships
    # once per character rather than once per row -- an array parallel to the
    # factorize codes. Rounded to whole points: season scores run to four
    # digits and the decimal is noise at that scale. -1 = not rated.
    rio = player_scores()
    charscore = [int(round(rio.get(k, -1))) if rio.get(k) is not None else -1
                 for k in char_keys]
    rated = sum(1 for v in charscore if v >= 0)
    if charscore:
        print(f"[{name}] player rating: {rated:,} of {len(charscore):,} "
              f"characters rated ({rated / len(charscore) * 100:.1f}%)")
    # beat-the-timer flag from the run's medal: 1 = timed (any chest count;
    # from the ranking medal), 0 = over timer, -1 = unknown
    timed = df["medal"].map(MEDAL_TIMED).fillna(-1).astype(int)
    patch = latest_tuning()
    post = post_tuning_flag(started, df["region"], patch)
    # per-parse projected-tuning multiplier. This is a property of the parse
    # itself — derived from that player's own ability breakdown — so the client
    # can apply it row by row and every aggregate stays exact under any filter.
    tmul, proj = tuning_multipliers(df, post)
    # per-spec secondary-stat quantiles and best-player meta aggregates for
    # the spec frame; each absent until the gear journal carries its inputs,
    # feature-detected client-side like tier/rating. The stats journal is
    # loaded once and shared with the per-parse sidecar below.
    stats_journal = stats_from_gear_journal()
    specstats = spec_stats_block(df, started, timed, name,
                                 journal=stats_journal)
    # one meta-journal read serves both specmeta and the builds sidecar
    meta_journal = meta_from_gear_journal()
    specmeta = spec_meta_block(df, started, timed, name,
                               journal=meta_journal)

    payload = {
        "built": pd.Timestamp.now("UTC").strftime("%Y-%m-%d %H:%M UTC"),
        "season": cfg["season"],
        "epoch": str(EPOCH.date()),
        "tuning": ({"label": patch.get("label"), "date": patch.get("date"),
                    "regions": patch.get("regions"),
                    "note": patch.get("note", ""),
                    "runs": int((post == 1).sum())} if patch else None),
        "classes": classes, "specs": specs, "heroes": heroes,
        "dungeons": dungeons, "regions": regions, "roles": roles,
        "pars": derive_pars(df, dungeons),   # keystone timer per dungeon, seconds

        "rows": {
            "cls": cls_arr, "spec": spec_arr, "hero": hero_arr,
            "dun": dun_arr, "reg": reg_arr, "role": role_arr,
            "key": df["key_level"].astype(int).tolist(),
            "deaths": df["deaths"].astype(int).tolist(),
            "dps": df["dps"].round(0).astype(int).tolist(),
            "dur": pd.to_numeric(df["duration_s"], errors="coerce")
                     .fillna(0).round(0).astype(int).tolist(),
            "kdur": (pd.to_numeric(df["keystone_s"], errors="coerce")
                     if "keystone_s" in df.columns
                     else pd.Series(0, index=df.index))
                    .fillna(0).round(0).astype(int).tolist(),
            "timed": timed.tolist(),
            "post": post.tolist(),
            "day": day.tolist(),
            "run": run_arr,
            "char": char_arr,
            # season tier pieces: -1 = report carried no gear, else 0-5
            "tier": tier.tolist(),
            **({"tmul": tmul} if tmul is not None else {}),
        },
        "charscore": charscore,
    }
    if proj:
        payload["projection"] = proj
    if specstats:
        payload["specstats"] = specstats
    if specmeta:
        payload["specmeta"] = specmeta
    blob = json.dumps(payload, separators=(",", ":"))
    # Big datasets ship pre-compressed: GitHub Pages' deploy step has a hard
    # 10-minute publish budget and a multi-tens-of-MB artifact blows it. The
    # client inflates via DecompressionStream, falling back to the plain file.
    gz = len(blob) > 8_000_000
    for d in SITE_DIRS:
        d.mkdir(exist_ok=True)
        out = d / cfg["out"]
        plain, packed = out, out.with_suffix(out.suffix + ".gz")
        if gz:
            plain.unlink(missing_ok=True)
            with gzip.open(packed, "wb", compresslevel=9) as fh:
                fh.write(blob.encode())
            print(f"[{name}] {len(df):,} rows -> {packed} "
                  f"({packed.stat().st_size / 1e6:.1f} MB gz, "
                  f"{len(blob) / 1e6:.1f} MB raw)")
        else:
            packed.unlink(missing_ok=True)
            plain.write_text(blob)
            print(f"[{name}] {len(df):,} rows -> {plain} "
                  f"({plain.stat().st_size / 1e6:.1f} MB raw)")
    # per-parse stats sidecar, row-aligned with the rows arrays above; it is
    # rewritten (or removed) in the same build as the payload, so a stale
    # copy can never sit next to a fresh data.json and misalign
    sidecar = stats_sidecar(df, stats_journal, name)
    for d in SITE_DIRS:
        out = d / "stats.json.gz"
        if sidecar is None:
            out.unlink(missing_ok=True)
        else:
            with gzip.open(out, "wt", encoding="utf-8",
                           compresslevel=9) as fh:
                fh.write(sidecar)
    if sidecar is not None:
        sz = (SITE_DIRS[0] / "stats.json.gz").stat().st_size
        print(f"[{name}] stats sidecar -> stats.json.gz "
              f"({sz / 1e6:.2f} MB gz, {len(sidecar) / 1e6:.1f} MB raw)")
    # builds sidecar, same discipline: rewritten or unlinked with the payload
    builds = builds_sidecar(df, meta_journal, name)
    for d in SITE_DIRS:
        out = d / "builds.json.gz"
        if builds is None:
            out.unlink(missing_ok=True)
        else:
            with gzip.open(out, "wt", encoding="utf-8",
                           compresslevel=9) as fh:
                fh.write(builds)
    if builds is not None:
        sz = (SITE_DIRS[0] / "builds.json.gz").stat().st_size
        print(f"[{name}] builds sidecar -> builds.json.gz "
              f"({sz / 1e6:.2f} MB gz, {len(builds) / 1e6:.1f} MB raw)")
    # lazy talent-tree document, same rewritten-or-unlinked discipline; the
    # trait journal pass from the sidecar is reused rather than re-walked
    talents = talents_doc(name, usage=getattr(builds_sidecar, "usage", None))
    for d in SITE_DIRS:
        out = d / "talents.json.gz"
        if talents is None:
            out.unlink(missing_ok=True)
        else:
            with gzip.open(out, "wt", encoding="utf-8",
                           compresslevel=9) as fh:
                fh.write(talents)
    if talents is not None:
        sz = (SITE_DIRS[0] / "talents.json.gz").stat().st_size
        print(f"[{name}] talents doc -> talents.json.gz "
              f"({sz / 1024:.0f} KB gz, {len(talents) / 1024:.0f} KB raw)")
    sync_icons(name)
    write_health()


# --------------------------------------------------------------------------
# LLM-accessible export (/llms.txt + /llms/*.csv)
# --------------------------------------------------------------------------
# The dashboard aggregates in JavaScript, which LLM web-fetch tools cannot
# execute — they read static text. This emits the season dataset as a
# self-describing llms.txt index plus pre-aggregated CSVs and chunked raw
# per-parse rows, so an LLM given ONE url can pull any cut of the data.

BASE_URL = "https://st331.github.io/wowlogs"
CHUNK = 6000  # raw parse rows per file (~500 KB — safely inside fetch limits)
# The raw chunks are the only llms/ output that scales with the dataset: the
# aggregates are fixed-size no matter how many parses back them. Chunking the
# whole population would be ~209 files and ~140 MB per copy, so the raw dump is
# a bounded sample while every aggregate stays computed on everything.
LLM_MAX_PARSE_ROWS = 120_000
COMPS_PER_SUBSET = 2_000   # ranked by runs; the long tail is all singletons

# Weekly reset schedule, mirroring the dashboard's client-side rules so the
# exported reset buckets line up exactly with what the site shows.
RESET_RULES = {"US": (1, 15), "EU": (2, 4)}   # (weekday, hour UTC), Mon = 0
RESET_DEFAULT = (2, 22)


def reset_bounds(now, regions):
    """Day index (from EPOCH) of each region's most recent weekly reset."""
    out = {}
    for reg in regions:
        wd, hh = RESET_RULES.get(reg, RESET_DEFAULT)
        b = now.replace(hour=hh, minute=0, second=0, microsecond=0, nanosecond=0)
        b -= pd.Timedelta(days=(b.weekday() - wd + 7) % 7)
        if b > now:
            b -= pd.Timedelta(days=7)
        out[reg] = (b.normalize() - EPOCH.tz_localize("UTC")).days
    return out


def build_llms() -> None:
    csv = ROOT / "data" / SEASON["csv"]
    if not csv.exists():
        csv = csv.with_suffix("")
    if not csv.exists():
        print("[llms] season csv missing — skipped")
        return
    df = pd.read_csv(csv)
    for col in ("class", "spec", "hero_talent", "role", "region", "dungeon"):
        df[col] = df[col].fillna("Unknown").replace("", "Unknown")
    resolve_hero_talents(df)
    df["timed"] = df["medal"].map(MEDAL_TIMED).fillna(-1).astype(int)
    # Same clock as the dashboard. Without this every llms DPS figure -- the
    # spec aggregates, set_bonus.csv medians, the raw chunks -- sat ~2% off
    # the site's for the same parse, because the site recomputes on the
    # keystone clock and these files were still on the fight clock.
    df = use_keystone_clock(df, "llms")
    # Per-run score is still collected and exported -- it rides free in the
    # rankings journal -- but nothing consumes it since the run-score metric
    # was dropped. Normalised here so the column is well-formed if it returns.
    # a CSV predating score collection has no column at all; make it explicit
    # rather than letting the aggregation KeyError
    if "score" not in df.columns:
        df["score"] = pd.NA
    df["score"] = pd.to_numeric(df["score"], errors="coerce")
    # Season tier pieces and Raider.IO season score, so the static files can
    # answer set-bonus and player-rating questions the dashboard already can.
    # Blank rather than -1 where unknown: a CSV consumer reading -1 as a real
    # count is a likelier mistake than one handling an empty cell.
    tier = tier_pieces(df, "llms")
    df["set_pieces"] = tier.where(tier >= 0).astype("Int64")
    rio = player_scores()
    if rio:
        key = (df["character"].fillna("?").astype(str) + "@"
               + df["server"].fillna("?").astype(str) + "@" + df["region"])
        df["player_rating"] = key.map(rio).round(0).astype("Int64")
    else:
        df["player_rating"] = pd.NA
    started = pd.to_datetime(pd.to_numeric(df["started_at"], errors="coerce"),
                             unit="ms", errors="coerce")
    df["date"] = started.dt.strftime("%Y-%m-%d")
    # reset buckets: 0 = the reset now in progress, 1 = the one before it, ...
    # Each row is measured against ITS OWN region's reset, exactly as the
    # dashboard does, because US/EU/other regions roll over on different days.
    now = pd.Timestamp.now("UTC")
    bounds = reset_bounds(now, sorted(df["region"].unique()))
    day = (started.dt.normalize() - EPOCH).dt.days
    b0 = df["region"].map(bounds).astype("float")
    bucket = pd.Series(0, index=df.index, dtype="float")
    behind = day < b0
    bucket[behind] = np.ceil((b0[behind] - day[behind]) / 7)
    df["reset_bucket"] = bucket.fillna(-1).astype(int)
    # calendar label for a bucket, anchored on the US reset (other regions roll
    # over within ~1.5 days of it) — handy for reasoning about dates
    us_b0 = bounds.get("US", next(iter(bounds.values())))
    df["reset_start"] = [
        (EPOCH + pd.Timedelta(days=us_b0 - 7 * b)).strftime("%Y-%m-%d")
        if b >= 0 else "" for b in df["reset_bucket"]]
    patch = latest_tuning()
    df["post_tuning"] = post_tuning_flag(started, df["region"], patch)
    # per-parse projected-tuning multiplier, identical to the dashboard's.
    # Publishing it per row is what lets a reader reproduce the projection for
    # ANY subset exactly, instead of applying a spec-level average.
    tmul, proj_meta = tuning_multipliers(df, df["post_tuning"])
    if tmul is not None:
        df["tuning_mult"] = [t / 10000 for t in tmul]
        df["projected_dps"] = (df["dps"] * df["tuning_mult"]).round(0).astype(int)
    else:
        proj_meta = None
    # keystone clock and how far under the dungeon timer each run finished —
    # the only fair way to compare runs across dungeons and key levels
    pars = dict(zip(sorted(df["dungeon"].unique()),
                    derive_pars(df, sorted(df["dungeon"].unique()))))
    ks = pd.to_numeric(df.get("keystone_s"), errors="coerce") \
        if "keystone_s" in df.columns else pd.Series(np.nan, index=df.index)
    df["keystone_s"] = ks.round(0)
    par_s = df["dungeon"].map(pars).replace(0, np.nan)
    df["par_s"] = par_s
    df["pct_under_timer"] = ((par_s - ks) / par_s * 100).round(1)
    df["run_id"] = pd.factorize(df["report_code"].astype(str) + ":"
                                + df["fight_id"].astype(str))[0]
    df["char_id"] = pd.factorize(df["character"].fillna("?").astype(str) + "@"
                                 + df["server"].fillna("?").astype(str) + "@"
                                 + df["region"])[0]
    df["dps"] = df["dps"].round(0).astype(int)
    df["item_level"] = pd.to_numeric(df["item_level"], errors="coerce")

    def agg(frame, by):
        g = frame.groupby(by, dropna=False)
        out = g.agg(
            parses=("dps", "size"),
            runs=("run_id", "nunique"),
            characters=("char_id", "nunique"),
            avg_dps=("dps", "mean"),
            median_dps=("dps", "median"),
            p90_dps=("dps", lambda s: s.quantile(0.9)),
            avg_deaths=("deaths", "mean"),
            deathless_pct=("deaths", lambda s: (s == 0).mean() * 100),
            avg_item_level=("item_level", "mean"),
            # gear-known parses only; blank set_pieces drops out of all four
            gear_known=("set_pieces", "count"),
            pct_no_set=("set_pieces", lambda s: (s < 2).mean() * 100
                        if s.notna().any() else float("nan")),
            pct_2set=("set_pieces", lambda s: ((s >= 2) & (s < 4)).mean() * 100
                      if s.notna().any() else float("nan")),
            pct_4set=("set_pieces", lambda s: (s >= 4).mean() * 100
                      if s.notna().any() else float("nan")),
            avg_player_rating=("player_rating", "mean"),
        ).reset_index()
        for c, r in (("avg_dps", 0), ("median_dps", 0), ("p90_dps", 0),
                     ("avg_deaths", 2), ("deathless_pct", 1),
                     ("avg_item_level", 1), ("pct_no_set", 1),
                     ("pct_2set", 1), ("pct_4set", 1),
                     ("avg_player_rating", 0)):
            out[c] = out[c].round(r)
        for c in ("avg_dps", "median_dps", "p90_dps"):
            out[c] = out[c].astype("Int64")
        return out

    def with_subsets(by, tuning=False, sets=False):
        parts = [agg(df, by).assign(subset="all"),
                 agg(df[df["timed"] == 1], by).assign(subset="timed")]
        if sets and "set_pieces" in df.columns:
            # Pre-aggregated tier cohorts. Without these the only way to get a
            # 4-piece figure was to pull all 21 raw chunks and group them,
            # which is more than a browsing reader can do -- so it proxies with
            # high key levels instead and reports something else. Timed only,
            # matching the dashboard default and keeping depleted runs out.
            gear = df[(df["timed"] == 1) & df["set_pieces"].notna()]
            if len(gear):
                sp = gear["set_pieces"]
                parts += [
                    agg(gear[sp < 2], by).assign(subset="set0_timed"),
                    agg(gear[(sp >= 2) & (sp < 4)], by).assign(subset="set2_timed"),
                    agg(gear[sp >= 4], by).assign(subset="set4_timed"),
                ]
        if tuning and (df["post_tuning"] == 1).any():
            post = df[df["post_tuning"] == 1]
            parts += [agg(post, by).assign(subset="post_tuning"),
                      agg(post[post["timed"] == 1], by)
                      .assign(subset="post_tuning_timed")]
        out = pd.concat(parts, ignore_index=True)
        return out[["subset"] + [c for c in out.columns if c != "subset"]]

    spec_summary = pd.concat([
        with_subsets(["class", "spec", "hero_talent", "role"], True, sets=True),
        with_subsets(["class", "spec", "role"], True, sets=True)
        .assign(hero_talent="(all merged)"),
    ], ignore_index=True)

    # Matched set-bonus gains, the same computation the dashboard shows.
    # Cannot be derived from the aggregates above: comparing a spec's 4-piece
    # mean against its no-set mean measures key level and item level as much as
    # the bonus, because the players who have the set are further along. This
    # holds dungeon and key level fixed instead, and is published ready-made
    # because doing it from the raw chunks means grouping ~120k rows.
    SB_CELL_MIN, SB_CELLS_MIN, SB_COHORT_MIN = 5, 3, 30

    def set_bonus_table() -> pd.DataFrame | None:
        if "set_pieces" not in df.columns:
            return None
        g = df[(df["timed"] == 1) & df["set_pieces"].notna()].copy()
        if g.empty:
            return None
        sp = g["set_pieces"]
        g["cohort"] = np.where(sp < 2, 0, np.where(sp < 4, 1, 2))
        rows = []
        for (cls, spec, role), grp in g.groupby(["class", "spec", "role"]):
            n = [int((grp["cohort"] == c).sum()) for c in (0, 1, 2)]
            med = [grp.loc[grp["cohort"] == c, "dps"].median() for c in (0, 1, 2)]

            def gain(a, b):
                """Weighted mean of per-cell median ratios, a -> b."""
                if n[a] < SB_COHORT_MIN or n[b] < SB_COHORT_MIN:
                    return None, 0
                acc = wsum = cells = 0
                for _, cell in grp.groupby(["dungeon", "key_level"]):
                    ca = cell.loc[cell["cohort"] == a, "dps"]
                    cb = cell.loc[cell["cohort"] == b, "dps"]
                    if len(ca) < SB_CELL_MIN or len(cb) < SB_CELL_MIN:
                        continue
                    ma, mb = ca.median(), cb.median()
                    if not ma > 0:
                        continue
                    w = min(len(ca), len(cb))
                    acc += w * (mb / ma - 1)
                    wsum += w
                    cells += 1
                if cells < SB_CELLS_MIN or not wsum:
                    return None, cells
                return round(100 * acc / wsum, 2), cells

            p2, c2 = gain(0, 1)
            p4, c4 = gain(1, 2)
            pt, ct = gain(0, 2)
            # rows keep their cohort counts and medians even when every gain
            # fails its threshold -- the counts are evidence in their own
            # right; only a spec with no cohort worth reading drops entirely
            if max(n) < SB_COHORT_MIN:
                continue
            tot = sum(n) or 1
            rows.append(dict(
                **{"class": cls}, spec=spec, role=role,
                parses_no_set=n[0], parses_2set=n[1], parses_4set=n[2],
                pct_no_set=round(100 * n[0] / tot, 1),
                pct_2set=round(100 * n[1] / tot, 1),
                pct_4set=round(100 * n[2] / tot, 1),
                median_dps_no_set=None if pd.isna(med[0]) else int(med[0]),
                median_dps_2set=None if pd.isna(med[1]) else int(med[1]),
                median_dps_4set=None if pd.isna(med[2]) else int(med[2]),
                gain_pct_2set=p2, gain_pct_4set=p4, gain_pct_total=pt,
                matched_cells=max(c2, c4, ct)))
        if not rows:
            return None
        return (pd.DataFrame(rows)
                .sort_values("gain_pct_total", ascending=False, na_position="last")
                .reset_index(drop=True))

    set_bonus = set_bonus_table()

    # same block the dashboard payload ships, flattened to CSV rows; absent
    # (no file, no doc line) until the gear journal carries stats
    specstats = spec_stats_block(df, started, df["timed"], "llms")

    files: list[tuple[str, pd.DataFrame | str]] = [
        ("spec_summary.csv", spec_summary),
        *([("set_bonus.csv", set_bonus)] if set_bonus is not None else []),
        *([("spec_stats.csv", spec_stats_frame(specstats))]
          if specstats else []),
        ("spec_by_key.csv",
         with_subsets(["class", "spec", "role", "key_level"], True, sets=True)),
        ("spec_by_dungeon.csv",
         with_subsets(["class", "spec", "role", "dungeon"], True, sets=True)),
        ("spec_by_reset.csv",
         with_subsets(["class", "spec", "role", "reset_bucket", "reset_start"])),
        ("spec_by_day.csv", with_subsets(["class", "spec", "role", "date"])),
        ("dungeon_summary.csv", df.groupby("dungeon").agg(
            runs=("run_id", "nunique"), parses=("dps", "size"),
            characters=("char_id", "nunique"),
            timer_s=("par_s", "max"),
            timed_run_pct=("timed", lambda s: round(
                (s == 1).sum() / max((s >= 0).sum(), 1) * 100, 1)),
            avg_duration_s=("duration_s", lambda s: round(s.mean(), 0)),
            avg_key=("key_level", lambda s: round(s.mean(), 1)),
            min_key=("key_level", "min"), max_key=("key_level", "max"),
            avg_deaths_per_player=("deaths", lambda s: round(s.mean(), 2)),
        ).reset_index()),
    ]
    # ---- compositions: one row per distinct 5-player comp, per subset ----
    ROLE_ORDER = {"Tank": 0, "Healer": 1, "DPS": 2}
    df["_ro"] = df["role"].map(ROLE_ORDER).fillna(3)
    df["_who"] = df["role"].str[0] + ":" + df["spec"] + " " + df["class"]
    ordered = df.sort_values(["run_id", "_ro", "class", "spec"])
    comp_of = ordered.groupby("run_id")["_who"].apply(" | ".join)
    runs = ordered.groupby("run_id").agg(
        dungeon=("dungeon", "first"), key_level=("key_level", "first"),
        keystone_s=("keystone_s", "first"), pct=("pct_under_timer", "first"),
        deaths=("deaths", "sum"), date=("date", "first"),
        timed=("timed", "first"), post=("post_tuning", "first"),
        chars=("char_id", "nunique"), players=("char_id", "size"),
    ).join(comp_of.rename("composition"))
    runs = runs[runs["players"].between(4, 6)]

    SHRINK_K = 5   # keep in step with the dashboard's Strength column

    def comp_agg(frame):
        """One row per comp, ranked by a key-normalised, difficulty-credited
        margin. Mirrors renderComps() in site/index.html exactly."""
        frame = frame.dropna(subset=["pct"])
        if frame.empty:
            return None
        # The keystone timer does not move with key level, so the margin under
        # it falls steadily as keys rise. Fit that line and score each run
        # against what is typical AT ITS KEY, so +20 and +12 start level...
        n = len(frame)
        x, y = frame["key_level"].astype(float), frame["pct"].astype(float)
        den = n * (x * x).sum() - x.sum() ** 2
        slope = ((n * (x * y).sum() - x.sum() * y.sum()) / den
                 if n >= 10 and abs(den) > 1e-9 else 0.0)
        icpt = (y.sum() - slope * x.sum()) / n
        ref_key, credit = x.mean(), abs(slope)
        # ...then deliberately tip it back: every key level above the mean is
        # worth `credit` points, the going rate for a level of difficulty, so
        # timing a harder key counts for more.
        scored = frame.assign(
            _s=(y - (icpt + slope * x)) + credit * (x - ref_key))
        g = scored.sort_values("pct", ascending=False).groupby("composition")
        out = g.agg(
            runs=("pct", "size"),
            score_sum=("_s", "sum"),
            avg_key=("key_level", "mean"),
            avg_pct_under=("pct", "mean"),
            best_pct_under=("pct", "max"),
            median_pct_under=("pct", "median"),
            best_time_s=("keystone_s", "first"),
            best_dungeon=("dungeon", "first"),
            best_key=("key_level", "first"),
            best_date=("date", "first"),
            median_time_s=("keystone_s", "median"),
            avg_deaths_per_run=("deaths", "mean"),
            timed_pct=("timed", lambda s: (s == 1).mean() * 100),
        ).reset_index()
        # shrink toward 0 (= a typical run at a typical key) by however few
        # runs support the comp, so a single lucky pull cannot top the table
        out["strength"] = out["score_sum"] / (out["runs"] + SHRINK_K)
        out = out.drop(columns=["score_sum"])
        out["key_slope"] = round(slope, 3)
        out["ref_key"] = round(ref_key, 2)
        for c, r in (("strength", 2), ("avg_key", 1), ("avg_pct_under", 1),
                     ("median_pct_under", 1), ("avg_deaths_per_run", 2),
                     ("timed_pct", 1), ("median_time_s", 0)):
            out[c] = out[c].round(r)
        cols = ["composition", "runs", "strength", "avg_key",
                "avg_pct_under", "best_pct_under", "median_pct_under",
                "key_slope", "ref_key"]
        out = out[cols + [c for c in out.columns if c not in cols]]
        return out.sort_values("strength", ascending=False)

    comp_parts = []
    # key15plus matters: the dungeon timer is the SAME at +2 and +20, so a
    # trivial low key posts a huge margin. Restricting to real keys makes the
    # ranking mean something.
    for label, frame in (("all", runs), ("timed", runs[runs["timed"] == 1]),
                         ("post_tuning", runs[runs["post"] == 1]),
                         # matches the dashboard's default view exactly
                         ("post_tuning_timed", runs[(runs["post"] == 1)
                                                    & (runs["timed"] == 1)]),
                         ("key15plus", runs[(runs["key_level"] >= 15)
                                            & (runs["timed"] == 1)])):
        got = comp_agg(frame)
        if got is not None and len(got):
            comp_parts.append(got.assign(subset=label))
    if comp_parts:
        comps = pd.concat(comp_parts, ignore_index=True)
        # Most distinct comps are one-offs -- the median comp has a single run
        # -- and they are both unrankable and the bulk of the file. Keep the
        # best-sampled ones per subset so the table stays a ranking rather than
        # a census that grows without bound as the dataset does.
        before = len(comps)
        comps = (comps.sort_values(["subset", "runs", "strength"],
                                   ascending=[True, False, False])
                      .groupby("subset", sort=False)
                      .head(COMPS_PER_SUBSET)
                      .reset_index(drop=True))
        if len(comps) < before:
            print(f"[llms] comps trimmed to the {COMPS_PER_SUBSET:,} "
                  f"best-sampled per subset: {before:,} -> {len(comps):,} rows",
                  flush=True)
        comps_trimmed = before - len(comps)
        files.append(("comps.csv",
                      comps[["subset"] + [c for c in comps.columns
                                          if c != "subset"]]))

    # ---- projected tuning: recorded vs projected, per spec x subset ----
    if proj_meta:
        pt_parts = []
        base = df[(df["post_tuning"] == 1) & (df["role"] == "DPS")]
        for label, frame in (("post_tuning", base),
                             ("post_tuning_timed", base[base["timed"] == 1])):
            if frame.empty:
                continue
            g = frame.groupby(["class", "spec"]).agg(
                characters=("char_id", "nunique"), parses=("dps", "size"),
                median_dps=("dps", "median"),
                projected_median_dps=("projected_dps", "median"),
                avg_dps=("dps", "mean"),
                projected_avg_dps=("projected_dps", "mean"),
            ).reset_index()
            g["median_change_pct"] = (100 * (g["projected_median_dps"]
                                             / g["median_dps"] - 1)).round(2)
            g["avg_change_pct"] = (100 * (g["projected_avg_dps"]
                                          / g["avg_dps"] - 1)).round(2)
            g["tuned"] = (g["spec"] + " " + g["class"]).isin(proj_meta["specs"])
            for c in ("median_dps", "projected_median_dps",
                      "avg_dps", "projected_avg_dps"):
                g[c] = g[c].round(0).astype(int)
            pt_parts.append(g.assign(subset=label))
        if pt_parts:
            pj = pd.concat(pt_parts, ignore_index=True)
            files.append(("tuning_projection.csv",
                          pj[["subset"] + [c for c in pj.columns
                                           if c != "subset"]]
                          .sort_values(["subset", "median_change_pct"],
                                       ascending=[True, False])))

    raw_cols = (["tuning_mult", "projected_dps"] if proj_meta else []) + [
                "run_id", "char_id", "class", "spec", "hero_talent", "role",
                "region", "dungeon", "key_level", "timed", "duration_s",
                "dps", "deaths", "item_level", "date", "reset_bucket",
                "post_tuning", "keystone_s", "pct_under_timer",
                "set_pieces", "player_rating"]
    # Ordered by a hash of the run id, not the id itself. Sorting by run_id
    # groups the season chronologically, and anything collected late -- gear,
    # most obviously -- then lands entirely in the last chunk or two, so a
    # reader who fetches parses_1.csv sees none of it and concludes there is
    # none. Hashing spreads every such column evenly across the chunks while
    # still keeping a run's five rows adjacent, which is what the chunking
    # needs. Stable across builds, since it is a hash and not a shuffle.
    _order = df["run_id"].map(
        lambda r: hashlib.md5(str(r).encode()).hexdigest())
    raw = (df[raw_cols].assign(_o=_order).sort_values(["_o", "run_id"])
           .drop(columns="_o").reset_index(drop=True))
    raw_total = len(raw)
    if raw_total > LLM_MAX_PARSE_ROWS:
        # whole runs, so a chunk still shows complete 5-player rosters, and by
        # a hash of the run id so the published rows are stable across builds
        cut = int((LLM_MAX_PARSE_ROWS / raw_total) * (1 << 32))
        keep = raw["run_id"].map(
            lambda r: int(hashlib.md5(str(r).encode()).hexdigest()[:8], 16) < cut)
        raw = raw[keep].reset_index(drop=True)
        print(f"[llms] raw parse dump sampled to {len(raw):,} of "
              f"{raw_total:,} rows ({raw['run_id'].nunique():,} whole runs); "
              f"aggregates still use every row", flush=True)
    raw_sampled = len(raw) < raw_total
    chunks = [(f"parses_{i // CHUNK + 1}.csv", raw.iloc[i:i + CHUNK])
              for i in range(0, len(raw), CHUNK)]

    built = pd.Timestamp.now("UTC").strftime("%Y-%m-%d %H:%M UTC")
    n_runs = df["run_id"].nunique()
    cur = df[df["reset_bucket"] == 0]
    cur_days = cur["date"].nunique()
    cur_dates = (f"{cur['date'].min()} to {cur['date'].max()}"
                 if cur_days else "no runs logged yet")
    cur_start = (EPOCH + pd.Timedelta(days=us_b0)).strftime("%Y-%m-%d")
    # figures that make the double-counting traps concrete in the index
    naive_sum = int(spec_summary["parses"].sum())
    runs_naive = int(spec_summary[(spec_summary["subset"] == "all")
                                  & (spec_summary["hero_talent"]
                                     == "(all merged)")]["runs"].sum())
    if patch:
        cut = patch["regions"].get("default", "?")
        n_post = int((df["post_tuning"] == 1).sum())
        tuning_para = (
            f"**Tuning cutoff.** The latest class-tuning pass is "
            f"{patch['label']}, taken as live at {cut} "
            f"({n_post:,} of {len(df):,} parses fall after it). Each run is "
            f"classified by its exact start instant, and the raw chunks carry "
            f"a `post_tuning` column (1 = after, 0 = before) so you can slice "
            f"it yourself. {patch.get('note','')} "
            f"{patch.get('why_not_per_region','')} {patch.get('robustness','')}")
    else:
        tuning_para = ("No class-tuning pass is currently recorded, so no "
                       "post_tuning subsets are published.")
    tsplit = df["timed"].value_counts().to_dict()
    region_line = ", ".join(f"{r} {n:,}" for r, n
                            in df["region"].value_counts().items())
    lines = [
        "# Midnight Mythic+ Season 2 — dataset for LLM analysis",
        "",
        f"> Per-player performance data for Mythic+ keystone runs from "
        f"Warcraft Logs' Season 2 fight rankings (zone 55). "
        f"{n_runs:,} runs / {len(df):,} player parses, keystone levels "
        f"+{df.key_level.min()}-+{df.key_level.max()}, "
        f"{df['date'].min()} to {df['date'].max()}. Generated {built}.",
        "",
        "The interactive dashboard at "
        f"{BASE_URL}/ is JavaScript-only and not machine-readable; "
        "use the static files below instead. All URLs are absolute — fetch "
        "any of them directly. CSVs are comma-separated with a header row.",
        "",
        f"If your fetcher only handles HTML, everything here is mirrored as "
        f"pages: {BASE_URL}/llms/ carries this same documentation, and each "
        f"CSV below has an .html twin at the same path (for example "
        f"{BASE_URL}/llms/spec_summary.html). The content is identical.",
        "",
        "## Start here (pre-aggregated)",
        "",
        "Every file below carries the same metric block: parses, runs, "
        "**characters** (distinct players), avg_dps, median_dps, p90_dps, "
        "avg_deaths, deathless_pct, avg_item_level, and — where the report "
        "carried gear — gear_known (parses whose gear is visible), "
        "pct_no_set / pct_2set / pct_4set (that spec's Season 2 tier-set "
        "adoption, shares of gear_known summing to 100) and "
        "avg_player_rating (mean Raider.IO season score of the players in "
        "those parses).",
        "",
        "Gear was not collected for the whole season, so gear_known is well "
        "below parses on most rows and is much higher at +12 and above, which "
        "is where the backfill ran. Treat a small gear_known as a small "
        "sample. Set counts are of the CURRENT season's tier only: a player "
        "still wearing last season's set counts as no set.",
        "",
        f"- {BASE_URL}/llms/spec_summary.csv — one row per class/spec/"
        "hero-talent/role (plus hero_talent=\"(all merged)\" rollups) × "
        "subset. The whole-dataset view.",
        f"- {BASE_URL}/llms/spec_by_key.csv — split by keystone level.",
        f"- {BASE_URL}/llms/spec_by_dungeon.csv — split by dungeon.",
        f"- {BASE_URL}/llms/spec_by_reset.csv — split by weekly reset "
        "(reset_bucket 0 = the reset now in progress, 1 = the one before "
        "it, ...; reset_start is that bucket's US reset date).",
        f"- {BASE_URL}/llms/spec_by_day.csv — split by calendar day (UTC). "
        "Use this for day-granular questions such as \"the last 3 days\".",
        f"- {BASE_URL}/llms/dungeon_summary.csv — per-dungeon runs, players, "
        "timed %, average duration/key/deaths, and `timer_s`: that dungeon's "
        "keystone timer.",
        # only when it exists: no tuning pass means no file, and a dead link
        # is worse than a missing one for a fetcher working down this list
        *([f"- {BASE_URL}/llms/tuning_projection.csv — recorded vs "
           "**projected** median/average DPS per class+spec under the next "
           "announced class tuning, for the post_tuning and post_tuning_timed "
           "subsets. Columns: subset, class, spec, characters, parses, "
           "median_dps, projected_median_dps, avg_dps, projected_avg_dps, "
           "median_change_pct, avg_change_pct, tuned."]
          if any(f[0] == "tuning_projection.csv" for f in files) else []),
        f"- {BASE_URL}/llms/set_bonus.csv — what each spec gains from the "
        "2-piece and 4-piece set bonuses, already computed. Columns: class, "
        "spec, role, parses_no_set / parses_2set / parses_4set, pct_no_set / "
        "pct_2set / pct_4set, median_dps_no_set / median_dps_2set / "
        "median_dps_4set, gain_pct_2set (no-set to 2-set), gain_pct_4set "
        "(2-set to 4-set), gain_pct_total (no-set to 4-set), matched_cells. "
        "The gain columns are MATCHED comparisons: median against median "
        "within the same spec, dungeon and key level, pooled across cells and "
        "weighted by the smaller cohort. Comparing the raw median_dps columns "
        "instead measures key level and item level as much as the bonus, "
        "because the players who have the set are further into the season — "
        "so quote the gain columns for the effect and the median columns only "
        "as the underlying figures. A spec needs 30+ parses per cohort and 3+ "
        "matched cells or its gain is blank rather than guessed; item level "
        "and player skill still travel with having the set, so these remain an "
        "upper bound on the bonus.",
        # only when it exists, for the same dead-link reason as above
        *([f"- {BASE_URL}/llms/spec_stats.csv — stat rating quantiles per "
           "class+spec, hero talents merged. Columns: class, spec, "
           "characters, stat, p25, p50, p75. Values are ratings, not "
           "percentages. Cohort: " + specstats["cohort"] + "."]
          if specstats else []),
        f"- {BASE_URL}/llms/comps.csv — the {COMPS_PER_SUBSET:,} "
        f"best-sampled per subset, one row per distinct 5-player "
        "composition, ranked by `strength`. Columns: subset, composition, "
        "runs, strength, avg_key, avg_pct_under, best_pct_under, "
        "median_pct_under, key_slope, ref_key, best_time_s, best_dungeon, "
        "best_key, best_date, median_time_s, avg_deaths_per_run, timed_pct.",
        "",
        "The `subset` column is \"all\" (every completed run), \"timed\" (runs "
        "that beat the timer), and — on spec_summary / spec_by_key / "
        "spec_by_dungeon — \"post_tuning\" and \"post_tuning_timed\", which "
        "restrict to runs started after the most recent class-tuning pass.",
        "",
        "**Tier-set cohorts are pre-aggregated. Do not group the raw parse "
        "chunks to get them.** spec_summary, spec_by_key and spec_by_dungeon "
        "carry \"set0_timed\", \"set2_timed\" and \"set4_timed\": timed runs "
        "whose report showed gear, split by how many pieces of this season's "
        "tier set the player wore (under 2, 2-3, 4+). Every metric in the "
        "block is there, so a question like \"rank specs by 4-piece DPS\" is "
        "one file and one filter — subset == \"set4_timed\" — with no need to "
        "proxy it with high key levels or to fetch parses_*.csv at all.",
        "",
        tuning_para,
        "",
        "**Compositions.** comps.csv holds the best-sampled comps per subset "
        f"(top {COMPS_PER_SUBSET:,} by run count; the tail is almost entirely "
        "one-run comps, which cannot be ranked). It is a ranking, not a "
        "census: do not count distinct comps from it. One row per comp, "
        "ranked by `strength` (see below). The underlying measure is how far "
        "under that dungeon's keystone timer a run finished (negative means "
        "the key was depleted). Dungeon timers differ, so the "
        "margin is what makes runs in different dungeons comparable; the "
        "timers themselves are in dungeon_summary.csv and are derived from "
        "where timed and depleted runs separate on the clock, not published "
        "by the API.",
        "",
        "Subsets: all / timed / post_tuning / post_tuning_timed / key15plus. "
        "`post_tuning_timed` is what the dashboard shows by default, so use "
        "that to reproduce the site; `key15plus` (timed, +15 and above) "
        "narrows to serious keys if you want that on top of the key "
        "normalisation described next.",
        "",
        "Rank comps by `strength`, not by `best_pct_under` or `runs`. "
        "Strength answers \"how well did this comp play for the difficulty it "
        "played at\", and then rewards difficulty. It is built in three "
        "steps, per subset:",
        "",
        "  1. *Normalise for key level.* The keystone timer is the SAME at +2 "
        "and +20, so the margin under it falls steadily as keys rise — about "
        "a point per level. A least-squares line margin = intercept + "
        "`key_slope` * key_level is fitted over the subset, and each run is "
        "scored as its margin MINUS that fitted expectation. A typical run at "
        "any key therefore scores 0, and a +20 is no longer punished for "
        "being a +20.",
        "  2. *Credit difficulty.* abs(`key_slope`) points are then added per "
        "key level above `ref_key` (the subset's mean key level). That is the "
        "going rate a level of difficulty costs in margin, so timing a harder "
        "key counts for more: a typical +20 outscores a typical +15 by "
        "roughly what those five levels cost.",
        "  3. *Shrink thin evidence.* Each comp's strength is the SUM of its "
        "run scores divided by (runs + 5), i.e. its mean pulled toward 0 by "
        "however few runs support it. Ranking on the single best run rewards "
        "one lucky pull, so a comp seen twice would outrank one proven over "
        "twenty; this pulls thin evidence back toward typical while leaving "
        "well-sampled comps near their own mean.",
        "",
        "Read strength as points of margin relative to a typical run at a "
        "typical key: 0 is par, +5 is five points better than expected for "
        "its difficulty, negative is worse. `key_slope` and `ref_key` are "
        "published per subset so the figure is reproducible, and `avg_key`, "
        "`avg_pct_under`, `best_pct_under` and `median_pct_under` are the raw "
        "unadjusted numbers if you want to rank differently. Note that "
        "because of step 1 you no longer need `key15plus` to keep trivial "
        "keys off the top — a +2 that merely beats the timer by the usual "
        "huge margin for a +2 scores near 0.",
        "",
        "The `composition` string is role-ordered and "
        "pipe-separated, e.g. \"T:Blood DeathKnight | H:Holy Paladin | "
        "D:Arcane Mage | ...\"; the raw chunks carry keystone_s and "
        "pct_under_timer per row so any other cut can be rebuilt by grouping "
        "on run_id.",
        "",
        "**Projected tuning.** " + (
            f"The next announced tuning pass is {proj_meta['label']} "
            f"({proj_meta['date']}), and {proj_meta['parses']:,} post-tuning "
            "parses are affected by it. Every parse in the raw chunks carries "
            "`tuning_mult` (its projected/current damage ratio) and "
            "`projected_dps` = dps * tuning_mult. The multiplier is derived "
            "from THAT parse's own per-ability damage breakdown, re-scored "
            "line by line against the announced changes — it is not a "
            "spec-level average. So to project any cut you like, filter the "
            "raw chunks however you want and take the median of "
            "`projected_dps`; the result is exact for that subset, which is "
            "what the dashboard's projection toggle does. tuning_mult is 1.0 "
            "for pre-tuning runs and for specs the pass does not touch. "
            "The projection also folds in changes that are ALREADY live but "
            "post-date some of the data: the Aug 14 hotfix halving Warrior "
            "Slayer's Executioner bonus applies only to parses recorded "
            "before it, since later parses already reflect it. Its size was "
            "measured from the data either side of the cutoff (Arms Slayer "
            "Execute share fell 11.99% to 9.94%, Mann-Whitney p=4.4e-4), not "
            "read off a patch note. Spec-wide aura changes and named-ability "
            "changes are computed exactly; set bonuses that ride on top of an "
            "ability the log reports as a single number are parameterised at "
            "a central estimate, so treat those specs as approximate. Specs "
            "modelled "
            "exactly: " + ", ".join(proj_meta["exact"]) + ". Specs with a "
            "modelling caveat: " + ", ".join(sorted(proj_meta["caveats"]))
            + ".") if proj_meta else "",
        "",
        "**Combining rows — read this before adding anything up.** These "
        "files deliberately contain overlapping views of the same data, so "
        "naive sums inflate badly. Three separate traps:",
        "",
        f"1. *Overlapping row sets.* spec_summary.csv holds both per-hero-"
        f"talent rows and hero_talent=\"(all merged)\" rollups, and both the "
        f"\"all\" and \"timed\" subsets. Adding up every row gives "
        f"{naive_sum:,} parses against a true {len(df):,}. Always pick ONE "
        f"subset and EITHER the merged rollups OR the per-hero rows — each "
        f"of those slices sums to exactly {len(df):,}. \"timed\" is a subset "
        f"of \"all\", never a separate population to add on.",
        f"2. *Non-additive columns.* Only `parses` is additive. `runs` counts "
        f"distinct runs a spec appeared in and one run holds 5 players of "
        f"different specs, so summing it across specs over-counts by up to "
        f"5x — in this dataset the {n_runs:,} real runs sum to "
        f"{runs_naive:,} across spec_summary rows. `characters` counts "
        f"distinct players, who recur across dungeons, days and resets, so "
        f"it double-counts the same way.",
        "3. *Non-poolable statistics.* Medians and p90s are exact within a "
        "row but cannot be averaged across rows; avg_dps can only be "
        "combined as a parses-weighted mean.",
        "",
        "When a question spans rows, recompute from the raw chunks "
        "(distinct run_id / char_id, or the raw dps values) rather than "
        "summing aggregates.",
        "",
        (f"## Raw per-parse data ({len(raw):,} of {raw_total:,} rows, "
         f"a uniform sample)" if raw_sampled else
         f"## Raw per-parse data ({len(df):,} rows, complete)"),
        "",
    ]
    if raw_sampled:
        lines += [
            f"These chunks are a uniform random sample of {len(raw):,} parses "
            f"drawn from all {raw_total:,}, selected by whole run so every "
            f"sampled run still shows its complete 5-player roster. Use them "
            f"for distributions, medians and per-parse reasoning, which the "
            f"sample preserves. Do NOT use them for totals or counts -- "
            f"\"how many runs happened\" or \"how many players parsed\" must "
            f"come from the aggregate CSVs above, which are computed on every "
            f"row. Scaling a count off these chunks will understate it by "
            f"roughly {raw_total / max(len(raw), 1):.1f}x.",
            "",
        ]
    for i, (name, chunk) in enumerate(chunks):
        lines.append(f"- {BASE_URL}/llms/{name} — rows "
                     f"{i * CHUNK + 1:,}-{i * CHUNK + len(chunk):,}")
    lines += [
        "",
        "One row per player per run; a run's 5 players share a run_id, so "
        "team compositions, per-run death totals etc. can be reconstructed. "
        "Columns: " + ", ".join(raw_cols) + ".",
        "",
        "## Column dictionary",
        "",
        "- run_id / char_id: stable anonymous integer ids within this "
        "dataset version.",
        "- class / spec / hero_talent: e.g. Warlock / Demonology / "
        "Diabolist; hero_talent may be \"Unknown\" where the talent tree "
        "could not be resolved (~5% of rows).",
        "- role: DPS, Healer or Tank.",
        "- key_level: keystone level (+N).",
        "- timed: 1 = beat the timer, 0 = completed over timer, -1 = "
        "unknown (keys below +10, where the in-game rating cannot "
        "distinguish the two).",
        "- duration_s: fight duration in seconds; dps = total damage done ÷ "
        "duration (WCL \"Overall DPS\"); deaths: that player's deaths in "
        "the run, parsed from the report's death events.",
        "- item_level: player max item level during the run (may be blank).",
        "- date: UTC calendar day the run started. reset_bucket: which weekly "
        "reset it falls in, 0 = the reset now in progress.",
        "",
        "## Time periods and the current reset",
        "",
        f"Resets are regional: US rolls over Tuesday 15:00 UTC, EU Wednesday "
        f"04:00 UTC, other regions ~Wednesday 22:00 UTC. Each row's "
        f"reset_bucket is measured against its OWN region's reset, so bucket "
        f"0 always means \"the reset that region is currently in\". Buckets "
        f"present: {', '.join(str(b) for b in sorted(set(df.reset_bucket)) )}.",
        "",
        f"The reset now in progress (bucket 0) started {cur_start} and covers "
        f"{cur_days} day(s) of data so far — {cur_dates}. For \"the last N "
        "days\" questions use spec_by_day.csv, or filter the raw chunks on "
        "`date`; for \"this reset vs last reset\" use spec_by_reset.csv.",
        "",
        "## Reproducing what the dashboard shows",
        "",
        "- Its default view is DPS-role only, hero talents merged into the "
        "spec — that is the hero_talent=\"(all merged)\" rows of "
        "spec_summary.csv filtered to role=DPS.",
        "- Its \"⏱ Timed only\" switch is the subset=\"timed\" rows.",
        "- It hides thin specs with a minimum-distinct-characters threshold, "
        "so mirror that by filtering on the `characters` column rather than "
        "`parses` — a spec carried by a handful of grinders has many parses "
        "but few players.",
        "- Its \"this reset, last N days\" zoom = reset_bucket 0 rows whose "
        "`date` is within the newest N dates present.",
        "",
        "These recipes are checked on every build: the exported rows are "
        "compared against the dashboard's own in-browser aggregation for the "
        "whole dataset, for individual reset buckets and for the day zoom, "
        "and they reproduce its parse, run and character counts exactly.",
        "",
        "## Dataset shape at a glance",
        "",
        f"- Composition: a standard run is 1 tank + 1 healer + 3 DPS, so "
        f"parses run ~5x the run count. {n_runs:,} runs, {len(df):,} parses.",
        f"- Regions by parses: {region_line}.",
        f"- Timed split: {tsplit.get(1,0):,} timed, {tsplit.get(0,0):,} over "
        f"timer, {tsplit.get(-1,0):,} unknown (keys under +10).",
        f"- {df.dungeon.nunique()} dungeons, keystone levels "
        f"+{df.key_level.min()}-+{df.key_level.max()}, median +"
        f"{int(df.key_level.median())}.",
        "- Sample size: early in a season many class/spec/dungeon cells are "
        "thin. Treat a row with fewer than ~30 "
        "characters as indicative only, and prefer `characters` over "
        "`parses` when judging whether a number is broadly based.",
        "",
        "## Worked example",
        "",
        "\"Which DPS specs perform best on timed +18 keys?\" — fetch "
        "spec_by_key.csv, keep subset=\"timed\", role=\"DPS\", "
        "key_level=18, drop rows with a low `characters` count, then sort by "
        "avg_dps (or median_dps, which is less swayed by outliers). Do not "
        "add those rows to anything else; for a multi-key answer, pull the "
        "raw chunks and recompute.",
        "",
        "## What is not in this export",
        "",
        "- Any season before Midnight Season 2. Earlier seasons are not "
        "collected or exported.",
        "- Player names and realms: characters are exposed only as opaque "
        "char_id integers.",
        "",
        "## Provenance and caveats",
        "",
        "- Source: Warcraft Logs API v2 fight rankings for the Season 2 "
        "Mythic+ zone, swept per dungeon x keystone bracket. WCL serves at "
        "most 20 pages x 50 runs per bracket, so this is a top-of-"
        "leaderboard sample rather than a census: aggregate DPS here runs "
        "above a full-population mean.",
        "- Timed status comes from the ranking medal.",
        "- Duplicate uploads are collapsed. Several members of a group often "
        "each upload the same fight, so one real run arrives under multiple "
        "report codes; a run is identified by dungeon + key level + keystone "
        "clock + exact roster, and only one copy is kept. This removed about "
        "a quarter of apparent runs, so run counts here are lower — and "
        "correct — versus anything computed before the fix. Start timestamps "
        "cannot be used for this: each uploader's report begins at a "
        "different moment, tens of seconds apart for the same fight.",
        "- Hero talents come from an offline SimulationCraft trait-tree "
        "mapping.",
        "- Dataset regenerates on each data refresh; row counts and ids "
        "change between versions.",
    ]
    index_txt = "\n".join(lines) + "\n"

    # HTML mirrors: some agent fetchers only handle text/html reliably, so the
    # same documentation and tables are published as pages too. (llms.txt
    # stays canonical for the ones that read plain text.)
    def html_doc(title, body):
        return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width,"
                "initial-scale=1\"><meta name=\"robots\" content=\"index,follow\">"
                f"<title>{title}</title><style>body{{font:15px/1.5 -apple-system,"
                "Segoe UI,Roboto,sans-serif;max-width:60rem;margin:2rem auto;"
                "padding:0 1rem;color:#111}table{border-collapse:collapse;"
                "font-size:.85rem}th,td{border:1px solid #ccc;padding:.2rem .45rem;"
                "text-align:right}th{background:#f2f2f2}td:nth-child(-n+5),"
                "th:nth-child(-n+5){text-align:left}code{background:#f2f2f2;"
                "padding:0 .2rem}</style></head><body>" + body + "</body></html>\n")

    def md_to_html(md_lines):
        out, in_list = [], False
        for ln in md_lines:
            esc = (ln.replace("&", "&amp;").replace("<", "&lt;")
                     .replace("**", "\x00"))
            while "\x00" in esc:  # bold pairs
                esc = esc.replace("\x00", "<b>", 1).replace("\x00", "</b>", 1)
            esc = re.sub(r"`([^`]+)`", r"<code>\1</code>", esc)
            esc = re.sub(r"(https?://[^\s,)]+)", r'<a href="\1">\1</a>', esc)
            if esc.startswith("# "):
                item, tag = esc[2:], "h1"
            elif esc.startswith("## "):
                item, tag = esc[3:], "h2"
            elif esc.startswith("> ") or esc.startswith("- ") or re.match(r"^\d+\. ", esc):
                item, tag = re.sub(r"^(> |- |\d+\. )", "", esc), "li"
            elif esc.strip():
                item, tag = esc, "p"
            else:
                item, tag = "", None
            if tag == "li" and not in_list:
                out.append("<ul>"); in_list = True
            elif tag != "li" and in_list:
                out.append("</ul>"); in_list = False
            if tag:
                out.append(f"<{tag}>{item}</{tag}>")
        if in_list:
            out.append("</ul>")
        return "\n".join(out)

    tables_html = ["<h2>Data tables (HTML mirrors of the CSVs)</h2><ul>"]
    for name, frame in files:
        stem = name[:-4]
        tables_html.append(f'<li><a href="{BASE_URL}/llms/{stem}.html">'
                           f'{stem}.html</a> — {len(frame):,} rows</li>')
    tables_html.append("</ul>")
    doc_html = html_doc("Midnight M+ Season 2 — data for LLMs",
                        md_to_html(lines) + "\n".join(tables_html))
    # Explicitly welcome AI crawlers. NOTE: crawlers only honour robots.txt at
    # the DOMAIN root (st331.github.io/robots.txt), which a project page cannot
    # publish — this copy exists so nothing here looks disallowed to fetchers
    # that do check the path, and it documents the intent either way.
    robots = ("# Everything here is public data, deliberately open to AI agents.\n"
              "User-agent: *\nAllow: /\n\n"
              + "".join(f"User-agent: {ua}\nAllow: /\n\n" for ua in
                        ("Google-Extended", "Googlebot", "GPTBot", "OAI-SearchBot",
                         "ChatGPT-User", "ClaudeBot", "Claude-User", "anthropic-ai",
                         "PerplexityBot", "CCBot", "Bingbot"))
              + f"Sitemap: {BASE_URL}/sitemap.xml\n")
    pages = [f"{BASE_URL}/", f"{BASE_URL}/llms.txt", f"{BASE_URL}/llms/"] + \
            [f"{BASE_URL}/llms/{n[:-4]}.html" for n, _ in files] + \
            [f"{BASE_URL}/llms/{n}" for n, _ in files]
    today = pd.Timestamp.now("UTC").strftime("%Y-%m-%d")
    sitemap = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
               + "".join(f"  <url><loc>{u}</loc><lastmod>{today}</lastmod></url>\n"
                         for u in pages) + "</urlset>\n")
    # Anything previously published but not part of THIS build is stale and
    # must go: the dataset can shrink (fewer raw chunks) or a file can be
    # renamed, and a leftover keeps being served and silently double-counts.
    expected = {"index.html"}
    for name, _ in files:
        expected |= {name, name[:-4] + ".html"}
    expected |= {name for name, _ in chunks}
    for d in SITE_DIRS:
        (d / "llms").mkdir(parents=True, exist_ok=True)
        for old in (d / "llms").iterdir():
            if old.is_file() and old.name not in expected:
                old.unlink()
                print(f"[llms] removed stale {old.relative_to(d.parent)}",
                      flush=True)
        (d / "llms.txt").write_text(index_txt)
        (d / "llms" / "index.html").write_text(doc_html)
        (d / "robots.txt").write_text(robots)
        (d / "sitemap.xml").write_text(sitemap)
        (d / ".nojekyll").write_text("")
        for name, frame in files:
            frame.to_csv(d / "llms" / name, index=False)
            (d / "llms" / f"{name[:-4]}.html").write_text(html_doc(
                name[:-4],
                f"<h1>{name[:-4]}</h1><p>Midnight M+ Season 2, generated "
                f"{built}. CSV: <a href=\"{BASE_URL}/llms/{name}\">{name}</a> · "
                f"docs: <a href=\"{BASE_URL}/llms.txt\">llms.txt</a></p>"
                + frame.to_html(index=False, border=0, na_rep="")))
        for name, chunk in chunks:
            chunk.to_csv(d / "llms" / name, index=False)
    total = sum(f.stat().st_size for f in (SITE_DIRS[0] / "llms").iterdir()) / 1e6
    print(f"[llms] llms.txt + {len(files) + len(chunks)} data files + "
          f"{len(files) + 1} HTML pages + robots/sitemap ({total:.1f} MB)")


STAMP_FILE = ROOT / "data" / ".build_stamp"


def inputs_fingerprint() -> str:
    """Hash of everything a build's output depends on.

    Every output embeds a "generated at" timestamp, so rebuilding unchanged
    data still rewrites ~45 MB of files that differ only by that line. During
    a long backfill the build runs often, and committing that churn is worse
    than useless. Fingerprinting the inputs makes a rebuild a no-op when
    nothing that matters moved.
    """
    h = hashlib.md5()
    for f in (ROOT / "data" / SEASON["csv"],
              ROOT / "data" / "tuning_patches.json",
              ROOT / "data" / "raw" / "abilities.jsonl",
              # the gear journal feeds the tier cohorts and the specstats
              # block; live copy first, committed export as the cold-start
              # fallback, matching the read order in the builders above
              GEAR_JOURNAL, GEAR_EXPORT,
              ROOT / "site" / "index.html",
              pathlib.Path(__file__)):
        h.update(f.name.encode())
        h.update(str(f.stat().st_size if f.exists() else 0).encode())
        if f.exists() and f.stat().st_size < 4_000_000:
            h.update(f.read_bytes())              # small inputs: hash content
        elif f.exists():
            h.update(str(int(f.stat().st_mtime)).encode())
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true",
                    help="rebuild even when no input has changed")
    args = ap.parse_args()
    fp = inputs_fingerprint()
    if not args.force and STAMP_FILE.exists() and \
            STAMP_FILE.read_text().strip() == fp:
        print("[build] inputs unchanged since the last build; nothing to do "
              "(--force to rebuild anyway)")
        return
    build("season", SEASON)
    # docs/ mirrors site/ for repo browsing; the payload is already written to
    # both, but the page itself was not, so the mirror served stale UI
    shutil.copyfile(ROOT / "site" / "index.html", ROOT / "docs" / "index.html")
    build_llms()
    index = ROOT / "site" / "index.html"
    docs_index = ROOT / "docs" / "index.html"
    docs_index.write_text(index.read_text())
    print(f"mirrored {index} -> {docs_index}")
    STAMP_FILE.write_text(inputs_fingerprint())


if __name__ == "__main__":
    main()
