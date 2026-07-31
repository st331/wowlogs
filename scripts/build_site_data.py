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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", choices=[*SOURCES, "all"], default="all",
                    help="rebuild one source's JSON only (default: all); the "
                         "other file keeps its committed build untouched")
    args = ap.parse_args()
    for name, cfg in SOURCES.items():
        if args.source in ("all", name):
            build(name, cfg)
    index = ROOT / "site" / "index.html"
    docs_index = ROOT / "docs" / "index.html"
    docs_index.write_text(index.read_text())
    print(f"mirrored {index} -> {docs_index}")


if __name__ == "__main__":
    main()
