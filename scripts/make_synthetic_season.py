#!/usr/bin/env python3
"""make_synthetic_season -- the perf-test season of partitioned_payload.md §9.3.

    python scripts/make_synthetic_season.py --out data/processed/fixtures/season4m [--rows 4000000] [--weeks 8]
    python scripts/make_synthetic_season.py --out ... --rows 13000000 --weeks 23

Generates `--rows` player rows over `--weeks` reset weeks ending at NOW
(2026-09-17T14:20:00Z, the same frozen clock as the equivalence fixture)
with the committed CSV's marginal distributions -- the same `Factory` as
scripts/make_eq_fixture.py, so every journal record has the collector's
shape -- and writes them as arrival chunks of `--chunk-minutes` (20) of play
each, rankings as a FULL per-chunk snapshot (§6.2-1), exactly what
tests/perf_partitions.py replays through partition_build.py:

  chunks/NNNNN/players.jsonl.gz   gear.jsonl.gz   abilities.jsonl.gz
  chunks/NNNNN/rankings.jsonl.gz  (the whole snapshot at that instant)
  chunks/NNNNN/now.txt            the frozen clock for that run
  season.json                     rows, runs, weeks, chunk count, marginals used

Journal parts are gzipped because 4M rows are ~1.8 GB of JSON; the replay
inflates them as it appends. Clock-less runs (15%), same-region rosters and
the tier/hero material are drawn exactly as in the fixture; no edge cases
are planted -- the perf test measures the ordinary path (§9.3: an ordinary
run <= 90 s / 2 GB with zero dirty days from the rankings snapshot, a full
replay <= 12 min at 13M with four workers, the per-file byte budgets).

Rows per chunk are Poisson around rows/chunk, so a "20-minute" chunk holds
~330 runs at 4M/8 weeks (≈ the live rate) and ~1,000 at 13M/23 weeks.
"""
from __future__ import annotations

import argparse
import gzip
import json
import pathlib
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import make_eq_fixture as mef                                    # noqa: E402
import sitecalc as sc                                            # noqa: E402

ROWS_PER_RUN = 5.0003          # 18 six-member rosters per 111,900 runs [measured]


def _write_gz_jsonl(path, records):
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=1) as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def build_season(out: pathlib.Path, rows: int, weeks: int, chunk_minutes: int = 20,
                 seed: int = 7, snapshot_every: int = 1) -> dict:
    out = pathlib.Path(out)
    (out / "chunks").mkdir(parents=True, exist_ok=True)
    marg = mef.Marginals(ROOT / "data" / "mythic_runs.csv.gz")
    f = mef.Factory(marg, seed)
    rng = f.rng
    # the span: `weeks` US reset weeks ending at the current US reset + 2 days
    us_w = sc.week_of(mef.NOW_MS, "US", mef.EPOCH)
    start_ms = sc.anchor_ms("US", mef.EPOCH) + (us_w - weeks) * sc.WEEK_MS
    end_ms = mef.NOW_MS
    chunk_ms = chunk_minutes * 60_000
    n_chunks = int((end_ms - start_ms + chunk_ms - 1) // chunk_ms)
    runs_total = int(rows / ROWS_PER_RUN)
    runs_per_chunk = runs_total / n_chunks
    known: list[tuple] = []            # (enc, key, ranking) for the snapshot
    t0 = time.perf_counter()
    made_rows = made_runs = 0
    meta = []
    for ci in range(n_chunks):
        c_start = start_ms + ci * chunk_ms
        c_end = min(c_start + chunk_ms, end_ms)
        d = out / "chunks" / f"{ci:05d}"
        d.mkdir(exist_ok=True)
        n = int(rng.poisson(runs_per_chunk))
        players, gear, abil = [], [], []
        for _ in range(n):
            st = int(rng.integers(c_start, max(c_start + 1, c_end)))
            day = int((st - mef.EPOCH_MS) // mef.DAY_MS)
            r = f.run(day, start_ms=st, chunk_tag=1)
            players.extend(r["rows"])
            gear.extend(r["gear"])
            abil.extend(r["abil"])
            known.append((r["enc"], r["key"], r["ranking"]))
        _write_gz_jsonl(d / "players.jsonl.gz", players)
        _write_gz_jsonl(d / "gear.jsonl.gz", gear)
        _write_gz_jsonl(d / "abilities.jsonl.gz", abil)
        if ci % snapshot_every == 0 or ci == n_chunks - 1:
            tmp = d / "rankings.jsonl"
            mef._write_rankings(tmp, known)
            with open(tmp, "rb") as src, gzip.open(d / "rankings.jsonl.gz", "wb", compresslevel=1) as dst:
                dst.write(src.read())
            tmp.unlink()
        (d / "now.txt").write_text(mef._iso(c_end) + "\n")
        made_rows += len(players)
        made_runs += n
        meta.append({"chunk": ci, "now": mef._iso(c_end), "runs": n, "rows": len(players)})
        if ci % 50 == 0 or ci == n_chunks - 1:
            el = time.perf_counter() - t0
            print(f"[season] chunk {ci + 1}/{n_chunks}: {made_rows:,} rows, {made_runs:,} runs, "
                  f"{el:.0f} s", flush=True)
    season = {"now": mef.NOW_ISO, "rows": made_rows, "runs": made_runs, "weeks": weeks,
              "chunks": n_chunks, "chunk_minutes": chunk_minutes, "seed": seed,
              "start": mef._iso(start_ms), "end": mef._iso(end_ms),
              "csv_marginals": str(ROOT / "data" / "mythic_runs.csv.gz"),
              "clockless_share": 0.15, "chunk_meta": meta}
    (out / "season.json").write_text(json.dumps(season, indent=1))
    return season


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(ROOT / "data" / "processed" / "fixtures" / "season4m"))
    ap.add_argument("--rows", type=int, default=4_000_000, help="player rows (4M; 13M on demand)")
    ap.add_argument("--weeks", type=int, default=8)
    ap.add_argument("--chunk-minutes", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--snapshot-every", type=int, default=1,
                    help="write the rankings snapshot every N chunks (1 = every run)")
    a = ap.parse_args(argv)
    s = build_season(pathlib.Path(a.out), a.rows, a.weeks, a.chunk_minutes, a.seed, a.snapshot_every)
    print(json.dumps({k: s[k] for k in ("rows", "runs", "weeks", "chunks", "start", "end")}))


if __name__ == "__main__":
    main()
