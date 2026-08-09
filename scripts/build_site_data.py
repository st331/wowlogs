#!/usr/bin/env python3
"""Pack data/mythic_runs*.csv into compact columnar JSON for the static site.

The static dashboard (site/index.html) filters and aggregates client-side, so
it needs per-parse rows, not pre-aggregates (medians can't be merged). Columns
are dictionary-encoded ints; the whole file compresses to a few MB over the
wire and parses in ~100 ms.

One JSON per data source: data.json (live season) and data_ptr.json (PTR),
each self-describing via its "season" label. Sources whose CSV is missing are
skipped, so the live build never blocks on PTR data existing.
"""
import argparse
import gzip
import json
import pathlib
import re

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
# site/ is canonical; docs/ mirrors it because GitHub Pages can only serve
# from the repo root or /docs on branch-based deploys
SITE_DIRS = [ROOT / "site", ROOT / "docs"]

SOURCES = {
    "live": {"csv": "mythic_runs.csv.gz", "out": "data.json",
             "season": "Midnight Season 1"},
    # score suppressed on PTR: the tiny tester population makes per-character
    # totals meaningless there, so the dashboard hides all score UI (the run
    # ratings still live in the CSV — the timed flag is derived from them)
    "ptr": {"csv": "mythic_runs_ptr.csv.gz", "out": "data_ptr.json",
            "season": "Midnight Season 2 (PTR)", "score": False},
}

EPOCH = pd.Timestamp("2026-01-01")
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


def build(name: str, cfg: dict) -> None:
    csv = ROOT / "data" / cfg["csv"]
    if not csv.exists():                       # tolerate an un-gzipped copy
        csv = csv.with_suffix("")
    if not csv.exists():
        print(f"[{name}] {cfg['csv']} missing — skipped")
        return
    df = pd.read_csv(csv)
    for col in ("class", "spec", "hero_talent", "role", "region", "dungeon"):
        df[col] = df[col].fillna("Unknown").replace("", "Unknown")

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
    # character identity (name@server@region) for per-character score totals
    char_ids = (df["character"].fillna("?").astype(str) + "@"
                + df["server"].fillna("?").astype(str) + "@" + df["region"])
    char_arr = pd.factorize(char_ids)[0].tolist()
    # WCL M+ score (per run; -1 = absent, which hides all score UI client-side)
    score = pd.to_numeric(df["score"], errors="coerce").fillna(-1).round(1)
    if not cfg.get("score", True):
        score = pd.Series(-1.0, index=df.index)
    # beat-the-timer flag from the run's medal: 1 = timed (any chest count;
    # "timed" is the PTR rating-derived value), 0 = over timer, -1 = unknown
    timed = df["medal"].map({"gold": 1, "silver": 1, "bronze": 1, "timed": 1,
                             "none": 0}).fillna(-1).astype(int)
    patch = latest_tuning() if name == "ptr" else None
    post = post_tuning_flag(started, df["region"], patch)

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
            "score": score.tolist(),
            "timed": timed.tolist(),
            "post": post.tolist(),
            "day": day.tolist(),
            "run": run_arr,
            "char": char_arr,
        },
    }
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
# LLM-accessible PTR export (/llms.txt + /llms/*.csv)
# --------------------------------------------------------------------------
# The dashboard aggregates in JavaScript, which LLM web-fetch tools cannot
# execute — they read static text. This emits the PTR dataset as a
# self-describing llms.txt index plus pre-aggregated CSVs and chunked raw
# per-parse rows, so an LLM given ONE url can pull any cut of the data.

BASE_URL = "https://st331.github.io/wowlogs"
CHUNK = 6000  # raw parse rows per file (~500 KB — safely inside fetch limits)

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
    csv = ROOT / "data" / SOURCES["ptr"]["csv"]
    if not csv.exists():
        csv = csv.with_suffix("")
    if not csv.exists():
        print("[llms] PTR csv missing — skipped")
        return
    df = pd.read_csv(csv)
    for col in ("class", "spec", "hero_talent", "role", "region", "dungeon"):
        df[col] = df[col].fillna("Unknown").replace("", "Unknown")
    df["timed"] = df["medal"].map({"timed": 1, "none": 0}).fillna(-1).astype(int)
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
        ).reset_index()
        for c, r in (("avg_dps", 0), ("median_dps", 0), ("p90_dps", 0),
                     ("avg_deaths", 2), ("deathless_pct", 1),
                     ("avg_item_level", 1)):
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
        frame = frame.dropna(subset=["pct"])
        if frame.empty:
            return None
        global_mean = frame["pct"].mean()
        g = frame.sort_values("pct", ascending=False).groupby("composition")
        out = g.agg(
            runs=("pct", "size"),
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
        # evidence-weighted ranking: a comp's mean margin pulled toward the
        # overall mean by however few runs support it, so a single lucky run
        # cannot top the table
        out["strength"] = ((out["avg_pct_under"] * out["runs"]
                            + SHRINK_K * global_mean)
                           / (out["runs"] + SHRINK_K))
        out["shrunk_toward"] = round(global_mean, 2)
        for c, r in (("strength", 2), ("avg_pct_under", 1),
                     ("median_pct_under", 1), ("avg_deaths_per_run", 2),
                     ("timed_pct", 1), ("median_time_s", 0)):
            out[c] = out[c].round(r)
        cols = ["composition", "runs", "strength", "avg_pct_under",
                "best_pct_under", "median_pct_under", "shrunk_toward"]
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
        files.append(("comps.csv",
                      comps[["subset"] + [c for c in comps.columns
                                          if c != "subset"]]))

    raw_cols = ["run_id", "char_id", "class", "spec", "hero_talent", "role",
                "region", "dungeon", "key_level", "timed", "duration_s",
                "dps", "deaths", "item_level", "date", "reset_bucket",
                "post_tuning", "keystone_s", "pct_under_timer"]
    raw = df[raw_cols].sort_values(["run_id"]).reset_index(drop=True)
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
        "# Midnight Mythic+ Season 2 (PTR) — dataset for LLM analysis",
        "",
        f"> Per-player performance data for every completed Mythic+ keystone "
        f"run logged to Warcraft Logs' PTR zone (zone 56). "
        f"{n_runs:,} runs / {len(df):,} player parses, keystone levels "
        f"+{df.key_level.min()}-+{df.key_level.max()}, "
        f"{df['date'].min()} to {df['date'].max()}. Generated {built}.",
        "",
        "The interactive dashboard at "
        f"{BASE_URL}/#ptr is JavaScript-only and not machine-readable; "
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
        f"- {BASE_URL}/llms/comps.csv — one row per distinct 5-player "
        "composition, ranked by `strength`. Columns: subset, composition, "
        "runs, strength, avg_pct_under, best_pct_under, median_pct_under, "
        "shrunk_toward, best_time_s, best_dungeon, best_key, best_date, "
        "median_time_s, avg_deaths_per_run, timed_pct.",
        "",
        "The `subset` column is \"all\" (every completed run), \"timed\" (runs "
        "that beat the timer), and — on spec_summary / spec_by_key / "
        "spec_by_dungeon — \"post_tuning\" and \"post_tuning_timed\", which "
        "restrict to runs started after the most recent class-tuning pass.",
        "",
        tuning_para,
        "",
        "**Compositions.** comps.csv has one row per distinct 5-player comp, "
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
        "that to reproduce the site; `key15plus` (timed, +15 and above) is "
        "the most meaningful ranking overall.",
        "",
        "Rank comps by `strength`, not by `best_pct_under`. Strength is the "
        "comp's mean margin shrunk toward the overall mean of its subset: "
        "(runs*avg_pct_under + 5*shrunk_toward) / (runs + 5). Ranking on the "
        "single best run rewards one lucky pull, so a comp seen twice would "
        "outrank one proven over twenty runs; the shrunk figure pulls thin "
        "evidence back toward average while leaving well-sampled comps near "
        "their own mean. `shrunk_toward` is the subset mean used, so the "
        "figure is reproducible, and avg/best/median are all published so you "
        "can rank differently if you want.",
        "",
        "IMPORTANT: the timer does NOT change with key level, so a +2 posts a "
        "far bigger margin than a +20 of equal quality and the unrestricted "
        "ranking is topped by trivial keys. Use the `key15plus` subset (timed "
        "runs at +15 and above) for a meaningful ranking, or filter on "
        "`best_key`. The `composition` string is role-ordered and "
        "pipe-separated, e.g. \"T:Blood DeathKnight | H:Holy Paladin | "
        "D:Arcane Mage | ...\"; the raw chunks carry keystone_s and "
        "pct_under_timer per row so any other cut can be rebuilt by grouping "
        "on run_id.",
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
        f"## Raw per-parse data ({len(df):,} rows, complete)",
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
        "- Sample size: this is a PTR tester population, so many "
        "class/spec/dungeon cells are thin. Treat a row with fewer than ~30 "
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
        "- Live Season 1 data. The dashboard carries it (a much larger, "
        "leaderboard-sampled dataset) but only the PTR season is exported "
        "here.",
        "- M+ score/rating. WCL computes no ranking score for PTR zones; the "
        "in-game rating is collected but only used to derive the `timed` "
        "flag, and is deliberately not published as a metric because the "
        "tester population makes per-character totals meaningless.",
        "- Player names and realms: characters are exposed only as opaque "
        "char_id integers.",
        "",
        "## Provenance and caveats",
        "",
        "- Source: Warcraft Logs API v2 report data for the PTR Mythic+ "
        "zone; every *completed* keystone fight (kill == true) is included "
        "— wipes and abandoned keys are not. This is a census of what "
        "testers logged, not a leaderboard sample.",
        "- The PTR population is small and self-selected; expect noisy "
        "numbers, especially for rare specs — check the `characters` column "
        "before trusting a row.",
        "- Timed status is inferred from Blizzard's in-game rating "
        "(depleted keys are rating-capped at 320 regardless of level).",
        "- Duplicate uploads are collapsed. Several members of a group often "
        "each upload the same fight, so one real run arrives under multiple "
        "report codes; a run is identified by dungeon + key level + keystone "
        "clock + exact roster, and only one copy is kept. This removed about "
        "27% of apparent PTR runs, so run counts here are lower — and "
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
    doc_html = html_doc("Midnight M+ Season 2 (PTR) — data for LLMs",
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
                f"<h1>{name[:-4]}</h1><p>Midnight M+ Season 2 (PTR), generated "
                f"{built}. CSV: <a href=\"{BASE_URL}/llms/{name}\">{name}</a> · "
                f"docs: <a href=\"{BASE_URL}/llms.txt\">llms.txt</a></p>"
                + frame.to_html(index=False, border=0, na_rep="")))
        for name, chunk in chunks:
            chunk.to_csv(d / "llms" / name, index=False)
    total = sum(f.stat().st_size for f in (SITE_DIRS[0] / "llms").iterdir()) / 1e6
    print(f"[llms] llms.txt + {len(files) + len(chunks)} data files + "
          f"{len(files) + 1} HTML pages + robots/sitemap ({total:.1f} MB)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", choices=[*SOURCES, "all"], default="all",
                    help="rebuild one source's JSON only (default: all); the "
                         "other file keeps its committed build untouched")
    args = ap.parse_args()
    for name, cfg in SOURCES.items():
        if args.source in ("all", name):
            build(name, cfg)
    if args.source in ("all", "ptr"):
        build_llms()
    index = ROOT / "site" / "index.html"
    docs_index = ROOT / "docs" / "index.html"
    docs_index.write_text(index.read_text())
    print(f"mirrored {index} -> {docs_index}")


if __name__ == "__main__":
    main()
