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
import json
import pathlib

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
# site/ is canonical; docs/ mirrors it because GitHub Pages can only serve
# from the repo root or /docs on branch-based deploys
SITE_DIRS = [ROOT / "site", ROOT / "docs"]

SOURCES = {
    "live": {"csv": "mythic_runs.csv", "out": "data.json",
             "season": "Midnight Season 1"},
    # score suppressed on PTR: the tiny tester population makes per-character
    # totals meaningless there, so the dashboard hides all score UI (the run
    # ratings still live in the CSV — the timed flag is derived from them)
    "ptr": {"csv": "mythic_runs_ptr.csv", "out": "data_ptr.json",
            "season": "Midnight Season 2 (PTR)", "score": False},
}

EPOCH = pd.Timestamp("2026-01-01")


def build(name: str, cfg: dict) -> None:
    csv = ROOT / "data" / cfg["csv"]
    if not csv.exists():
        print(f"[{name}] {csv.name} missing — skipped")
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

    payload = {
        "built": pd.Timestamp.now("UTC").strftime("%Y-%m-%d %H:%M UTC"),
        "season": cfg["season"],
        "epoch": str(EPOCH.date()),
        "classes": classes, "specs": specs, "heroes": heroes,
        "dungeons": dungeons, "regions": regions, "roles": roles,
        "rows": {
            "cls": cls_arr, "spec": spec_arr, "hero": hero_arr,
            "dun": dun_arr, "reg": reg_arr, "role": role_arr,
            "key": df["key_level"].astype(int).tolist(),
            "deaths": df["deaths"].astype(int).tolist(),
            "dps": df["dps"].round(0).astype(int).tolist(),
            "score": score.tolist(),
            "timed": timed.tolist(),
            "day": day.tolist(),
            "run": run_arr,
            "char": char_arr,
        },
    }
    blob = json.dumps(payload, separators=(",", ":"))
    for d in SITE_DIRS:
        d.mkdir(exist_ok=True)
        out = d / cfg["out"]
        out.write_text(blob)
        print(f"[{name}] {len(df):,} rows -> {out} "
              f"({out.stat().st_size / 1e6:.1f} MB raw)")


# --------------------------------------------------------------------------
# LLM-accessible PTR export (/llms.txt + /llms/*.csv)
# --------------------------------------------------------------------------
# The dashboard aggregates in JavaScript, which LLM web-fetch tools cannot
# execute — they read static text. This emits the PTR dataset as a
# self-describing llms.txt index plus pre-aggregated CSVs and chunked raw
# per-parse rows, so an LLM given ONE url can pull any cut of the data.

BASE_URL = "https://st331.github.io/wowlogs"
CHUNK = 6000  # raw parse rows per file (~500 KB — safely inside fetch limits)


def build_llms() -> None:
    csv = ROOT / "data" / SOURCES["ptr"]["csv"]
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
    df["week_start"] = (started - pd.to_timedelta(
        started.dt.dayofweek, unit="D")).dt.strftime("%Y-%m-%d")
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

    def with_subsets(by):
        both = [agg(df, by).assign(subset="all"),
                agg(df[df["timed"] == 1], by).assign(subset="timed")]
        out = pd.concat(both, ignore_index=True)
        return out[["subset"] + [c for c in out.columns if c != "subset"]]

    spec_summary = pd.concat([
        with_subsets(["class", "spec", "hero_talent", "role"]),
        with_subsets(["class", "spec", "role"]).assign(hero_talent="(all merged)"),
    ], ignore_index=True)

    files: list[tuple[str, pd.DataFrame | str]] = [
        ("spec_summary.csv", spec_summary),
        ("spec_by_key.csv", with_subsets(["class", "spec", "role", "key_level"])),
        ("spec_by_dungeon.csv", with_subsets(["class", "spec", "role", "dungeon"])),
        ("spec_by_week.csv", with_subsets(["class", "spec", "role", "week_start"])),
        ("dungeon_summary.csv", df.groupby("dungeon").agg(
            runs=("run_id", "nunique"), parses=("dps", "size"),
            timed_run_pct=("timed", lambda s: round(
                (s == 1).sum() / max((s >= 0).sum(), 1) * 100, 1)),
            avg_duration_s=("duration_s", lambda s: round(s.mean(), 0)),
            avg_key=("key_level", lambda s: round(s.mean(), 1)),
            min_key=("key_level", "min"), max_key=("key_level", "max"),
            avg_deaths_per_player=("deaths", lambda s: round(s.mean(), 2)),
        ).reset_index()),
    ]
    raw_cols = ["run_id", "char_id", "class", "spec", "hero_talent", "role",
                "region", "dungeon", "key_level", "timed", "duration_s",
                "dps", "deaths", "item_level", "date"]
    raw = df[raw_cols].sort_values(["run_id"]).reset_index(drop=True)
    chunks = [(f"parses_{i // CHUNK + 1}.csv", raw.iloc[i:i + CHUNK])
              for i in range(0, len(raw), CHUNK)]

    built = pd.Timestamp.now("UTC").strftime("%Y-%m-%d %H:%M UTC")
    n_runs = df["run_id"].nunique()
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
        "## Start here (pre-aggregated)",
        "",
        f"- {BASE_URL}/llms/spec_summary.csv — one row per class/spec/"
        "hero-talent/role (plus hero_talent=\"(all merged)\" rollups) × "
        "subset. Columns: subset, class, spec, hero_talent, role, parses, "
        "runs, avg_dps, median_dps, p90_dps, avg_deaths, deathless_pct, "
        "avg_item_level.",
        f"- {BASE_URL}/llms/spec_by_key.csv — same metrics split by "
        "keystone level.",
        f"- {BASE_URL}/llms/spec_by_dungeon.csv — same metrics split by "
        "dungeon.",
        f"- {BASE_URL}/llms/spec_by_week.csv — same metrics split by week "
        "(week_start = Monday, UTC).",
        f"- {BASE_URL}/llms/dungeon_summary.csv — per-dungeon run counts, "
        "timed %, average duration/key/deaths.",
        "",
        "The `subset` column is \"all\" (every completed run) or \"timed\" "
        "(runs that beat the timer only). Medians and p90s are exact within "
        "each row — never average them across rows; recompute from the raw "
        "chunks instead.",
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
        "",
        "## Provenance and caveats",
        "",
        "- Source: Warcraft Logs API v2 report data for the PTR Mythic+ "
        "zone; every *completed* keystone fight (kill == true) is included "
        "— wipes and abandoned keys are not. This is a census of what "
        "testers logged, not a leaderboard sample.",
        "- The PTR population is small and self-selected; expect noisy "
        "numbers, especially for rare specs — check the `parses` column "
        "before trusting a row.",
        "- Timed status is inferred from Blizzard's in-game rating "
        "(depleted keys are rating-capped at 320 regardless of level).",
        "- Hero talents come from an offline SimulationCraft trait-tree "
        "mapping.",
        "- Dataset regenerates on each data refresh; row counts and ids "
        "change between versions.",
    ]
    index_txt = "\n".join(lines) + "\n"

    redirect = ('<!doctype html><meta http-equiv="refresh" '
                f'content="0;url={BASE_URL}/llms.txt">'
                f'<a href="{BASE_URL}/llms.txt">llms.txt</a>\n')
    for d in SITE_DIRS:
        (d / "llms").mkdir(parents=True, exist_ok=True)
        (d / "llms.txt").write_text(index_txt)
        (d / "llms" / "index.html").write_text(redirect)
        (d / ".nojekyll").write_text("")
        for name, frame in files:
            frame.to_csv(d / "llms" / name, index=False)
        for name, chunk in chunks:
            chunk.to_csv(d / "llms" / name, index=False)
    total = sum((SITE_DIRS[0] / "llms" / n).stat().st_size
                for n, _ in files + chunks) / 1e6
    print(f"[llms] llms.txt + {len(files) + len(chunks)} files "
          f"({total:.1f} MB) -> site/llms + docs/llms")


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
