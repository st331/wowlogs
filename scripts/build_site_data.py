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
import csv
import gzip
import hashlib
import json
import pathlib
import re

import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

ROOT = pathlib.Path(__file__).resolve().parent.parent
# site/ is canonical; docs/ mirrors it because GitHub Pages can only serve
# from the repo root or /docs on branch-based deploys
SITE_DIRS = [ROOT / "site", ROOT / "docs"]

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


RIO_FILE = ROOT / "data" / "rio_scores.csv.gz"


def player_scores() -> dict[str, float]:
    """Raider.IO season score per character, keyed name@server@region.

    This is the player's season total -- the sum of their best run in each of
    the eight dungeons -- and so is roughly eight times the per-run score that
    rides on each parse. The two are separate metrics on the site and must not
    be confused for one another.

    Absent journal, or a character missing from it, simply means no rating; the
    client drops those rather than counting them as zero.
    """
    if not RIO_FILE.exists():
        print("[build] no Raider.IO journal; player rating omitted")
        return {}
    out: dict[str, float] = {}
    with gzip.open(RIO_FILE, "rt", encoding="utf-8", newline="") as fh:
        for row in csv.reader(fh):
            if len(row) != 5:
                continue
            name, realm, region, score, _day = row
            try:
                v = float(score)
            except ValueError:
                continue
            if v >= 0:                      # -1 is a journalled "no answer"
                out[f"{name}@{realm}@{region}"] = v
    return out


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
    # the run's M+ score, carried on every parse of that run; -1 = absent,
    # which the client reads as "no score data" and hides the metric
    score = (pd.to_numeric(df["score"], errors="coerce").fillna(-1).round(1)
             if "score" in df.columns else pd.Series(-1.0, index=df.index))

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
            "score": score.tolist(),
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
            **({"tmul": tmul} if tmul is not None else {}),
        },
        "charscore": charscore,
    }
    if proj:
        payload["projection"] = proj
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
    # a CSV predating score collection has no column at all; make it explicit
    # rather than letting the aggregation KeyError
    if "score" not in df.columns:
        df["score"] = pd.NA
    df["score"] = pd.to_numeric(df["score"], errors="coerce")
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
            # the run's M+ score, averaged over the spec's parses. Runs the
            # leaderboard no longer lists have none, so this skips nulls
            # rather than counting them as zero.
            avg_score=("score", "mean"),
            median_score=("score", "median"),
        ).reset_index()
        for c, r in (("avg_dps", 0), ("median_dps", 0), ("p90_dps", 0),
                     ("avg_deaths", 2), ("deathless_pct", 1),
                     ("avg_item_level", 1), ("avg_score", 1),
                     ("median_score", 1)):
            out[c] = out[c].round(r)
        for c in ("avg_dps", "median_dps", "p90_dps"):
            out[c] = out[c].astype("Int64")
        return out

    def with_subsets(by, tuning=False):
        parts = [agg(df, by).assign(subset="all"),
                 agg(df[df["timed"] == 1], by).assign(subset="timed")]
        if tuning and (df["post_tuning"] == 1).any():
            post = df[df["post_tuning"] == 1]
            parts += [agg(post, by).assign(subset="post_tuning"),
                      agg(post[post["timed"] == 1], by)
                      .assign(subset="post_tuning_timed")]
        out = pd.concat(parts, ignore_index=True)
        return out[["subset"] + [c for c in out.columns if c != "subset"]]

    spec_summary = pd.concat([
        with_subsets(["class", "spec", "hero_talent", "role"], True),
        with_subsets(["class", "spec", "role"], True).assign(hero_talent="(all merged)"),
    ], ignore_index=True)

    files: list[tuple[str, pd.DataFrame | str]] = [
        ("spec_summary.csv", spec_summary),
        ("spec_by_key.csv", with_subsets(["class", "spec", "role", "key_level"], True)),
        ("spec_by_dungeon.csv", with_subsets(["class", "spec", "role", "dungeon"], True)),
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
                "post_tuning", "keystone_s", "pct_under_timer"]
    raw = df[raw_cols].sort_values(["run_id"]).reset_index(drop=True)
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
        "avg_deaths, deathless_pct, avg_item_level.",
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
        f"- {BASE_URL}/llms/tuning_projection.csv — recorded vs **projected** "
        "median/average DPS per class+spec under the next announced class "
        "tuning, for the post_tuning and post_tuning_timed subsets. Columns: "
        "subset, class, spec, characters, parses, median_dps, "
        "projected_median_dps, avg_dps, projected_avg_dps, "
        "median_change_pct, avg_change_pct, tuned.",
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
    build_llms()
    index = ROOT / "site" / "index.html"
    docs_index = ROOT / "docs" / "index.html"
    docs_index.write_text(index.read_text())
    print(f"mirrored {index} -> {docs_index}")
    STAMP_FILE.write_text(inputs_fingerprint())


if __name__ == "__main__":
    main()
