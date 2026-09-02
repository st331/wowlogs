#!/usr/bin/env python3
"""tests/perf_partitions.py -- the §9.3 perf test (manual + weekly CI job).

    python tests/perf_partitions.py [--rows 4000000] [--weeks 8] [--season DIR] [--root DIR]
                                    [--tail 3] [--replay-deadline 420] [--keep]

Generates the synthetic season (scripts/make_synthetic_season.py, cached
under --season) and replays it through scripts/partition_build.py the way
production runs it, asserting the §9.3 budgets that belong to the partition
builder:

  * full replay: every chunk but the last `--tail` concatenated into the
    journals, rankings = that instant's snapshot, run ONCE under
    --deadline <replay-deadline> (the interrupted replay), then resumed
    without a deadline: the resumed run rebuilds no day the first one
    completed; the total replay wall is reported (<= 25 min single-core is
    the documented fallback; the 4-worker figure is stage C's reseed);
  * ordinary runs: each tail chunk appended with its snapshot -> wall
    <= 90 s and RSS <= 2 GB per run;
  * the rankings snapshot dirties nothing by itself: the same snapshot
    rewritten in another byte order -> parsed, changed:0, days:0, zero
    dirty days, seq unchanged;
  * byte budgets from the final manifest: rows <= 7.5 B/row gz, dist
    <= 2.5 B/row, chars <= 2.2 B/row, cells <= 0.3 MB/week, comps <= 0.8
    MB/week, largest block <= 1 MB; the Pages data footprint is reported
    (<= 200 MB is asserted at 13M rows only).

The legacy gear_journal_pass()/export() budgets of §9.3 are the legacy
suites' (test_legacy_single_pass / test_export_stream); the client-side
budgets are PR-2's. Exit status 1 when a budget is missed.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import pathlib
import random
import resource
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

BUDGET = {"ordinary_wall_s": 90, "ordinary_rss_mb": 2048, "rows_b_per_row": 7.5, "dist_b_per_row": 2.5,
          "chars_b_per_row": 2.2, "cells_mb": 0.3, "comps_mb": 0.8, "block_mb": 1.0,
          "replay_single_core_s": 25 * 60, "footprint_mb_13m": 200}
FAILS: list = []


def check(name, value, limit, unit=""):
    ok = value <= limit
    print(f"[perf] {'ok  ' if ok else 'FAIL'} {name} = {value:.3f}{unit} (budget {limit}{unit})", flush=True)
    if not ok:
        FAILS.append(name)


def append_gz(src: pathlib.Path, dst: pathlib.Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(src, "rb") as fi, open(dst, "ab") as fo:
        shutil.copyfileobj(fi, fo, 1 << 20)


def apply_chunk(season: pathlib.Path, root: pathlib.Path, ci: int) -> str:
    d = season / "chunks" / f"{ci:05d}"
    append_gz(d / "players.jsonl.gz", root / "data" / "processed" / "players.jsonl")
    append_gz(d / "gear.jsonl.gz", root / "data" / "processed" / "gear.jsonl")
    append_gz(d / "abilities.jsonl.gz", root / "data" / "raw" / "abilities.jsonl")
    snap = d / "rankings.jsonl.gz"
    if snap.exists():
        tgt = root / "data" / "raw" / "rankings.jsonl"
        with gzip.open(snap, "rb") as fi, open(tgt, "wb") as fo:
            shutil.copyfileobj(fi, fo, 1 << 20)
    return (d / "now.txt").read_text().strip()


def run_builder(root: pathlib.Path, now: str, extra=(), pins=None, deadline=None) -> dict:
    env = dict(os.environ)
    env.pop("WOWLOGS_PINS", None)
    args = [sys.executable, "-u", str(ROOT / "scripts" / "partition_build.py"), "--data-root", str(root / "data"),
            "--site-dir", str(root / "site"), "--now", now, "--max-days", "1000"]
    if pins:
        args += ["--pins", str(pins)]
    if deadline:
        args += ["--deadline", str(deadline)]
    args += list(extra)
    before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    t0 = time.monotonic()
    r = subprocess.run(args, env=env, capture_output=True, text=True)
    wall = time.monotonic() - t0
    after = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    if r.returncode:
        print(r.stdout[-3000:], r.stderr[-3000:])
        raise SystemExit("partition_build failed")
    health = {}
    for line in (root / "data" / "processed" / "parts" / "health.txt").read_text().splitlines():
        k, _, v = line.partition("=")
        health.setdefault(k, []).append(v)
    # ru_maxrss of children is a running maximum; a run smaller than an
    # earlier one reports that earlier peak (an upper bound, fine for a budget)
    return {"wall": wall, "rss_mb": after / 1024.0, "rss_grew": after > before, "health": health,
            "stdout": r.stdout}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rows", type=int, default=4_000_000)
    ap.add_argument("--weeks", type=int, default=8)
    ap.add_argument("--season", default=None)
    ap.add_argument("--root", default=None)
    ap.add_argument("--tail", type=int, default=3)
    ap.add_argument("--replay-deadline", type=int, default=420)
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args(argv)
    season = pathlib.Path(a.season or ROOT / "data" / "processed" / "fixtures" / f"season{a.rows // 1_000_000}m")
    root = pathlib.Path(a.root or ROOT / "data" / "processed" / "fixtures" / f"perf{a.rows // 1_000_000}m")
    sj = season / "season.json"
    if not sj.exists() or json.loads(sj.read_text()).get("chunks", 0) != len(list((season / "chunks").glob("*"))):
        print(f"[perf] generating the synthetic season under {season} ...", flush=True)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_synthetic_season.py"), "--out", str(season),
                        "--rows", str(a.rows), "--weeks", str(a.weeks)], check=True)
    S = json.loads(sj.read_text())
    n_chunks = int(S["chunks"])
    print(f"[perf] season: {S['rows']:,} rows, {S['runs']:,} runs, {n_chunks} chunks, weeks {S['weeks']}")
    if root.exists():
        shutil.rmtree(root)
    (root / "data" / "processed").mkdir(parents=True)
    (root / "data" / "raw").mkdir(parents=True)
    (root / "site").mkdir()
    for name in ("season.json", "keystone_pars.json", "hero_talent_map.json", "tuning_patches.json"):
        shutil.copyfile(ROOT / "data" / name, root / "data" / name)
    # the fixture's pins (the same Factory's tier sets and learned tables), so
    # hero/tier are pinned from the first run like a reseeded runner's
    from fixture_util import FIXTURE_DIR, fixture
    fixture()
    pins = FIXTURE_DIR / "pins.json"
    # ---- full replay: everything but the tail, interrupted then resumed --
    head = n_chunks - a.tail
    t0 = time.monotonic()
    now = None
    for ci in range(head):
        now = apply_chunk(season, root, ci)
    print(f"[perf] journals for {head} chunks written in {time.monotonic() - t0:.0f} s", flush=True)
    r1 = run_builder(root, now, pins=pins, deadline=a.replay_deadline)
    h1 = r1["health"]
    done1 = [int(x) for x in h1.get("parts.rebuilt_order", [""])[0].split(",") if x]
    print(f"[perf] replay run 1: {r1['wall']:.0f} s, rss {r1['rss_mb']:.0f} MB, {len(done1)} days, "
          f"deadline_hit={h1.get('parts.deadline_hit', ['0'])[0]}, days_left={h1.get('parts.days_left', ['?'])[0]}",
          flush=True)
    total = r1["wall"]
    done2: list = []
    if h1.get("parts.deadline_hit") == ["1"]:
        r2 = run_builder(root, now, pins=None)
        h2 = r2["health"]
        done2 = [int(x) for x in h2.get("parts.rebuilt_order", [""])[0].split(",") if x]
        total += r2["wall"]
        print(f"[perf] replay run 2 (resume): {r2['wall']:.0f} s, {len(done2)} days, "
              f"days_left={h2.get('parts.days_left', ['?'])[0]}", flush=True)
        both = set(done1) & set(done2)
        print(f"[perf] {'ok  ' if not both else 'FAIL'} resume recomputed no completed day ({len(both)} overlap)")
        if both:
            FAILS.append("resume_recompute")
        if h2.get("parts.days_left") != ["0"]:
            r3 = run_builder(root, now, pins=None)
            total += r3["wall"]
    check("replay_wall_s (single core)", total, BUDGET["replay_single_core_s"], " s")
    # ---- ordinary runs over the tail chunks ------------------------------
    for ci in range(head, n_chunks):
        now = apply_chunk(season, root, ci)
        r = run_builder(root, now, deadline=360)
        h = r["health"]
        stages = {k[len("parts.stage."):]: float(v[0]) for k, v in h.items() if k.startswith("parts.stage.")}
        print(f"[perf] ordinary run (chunk {ci}): wall {r['wall']:.1f} s, rss {r['rss_mb']:.0f} MB, "
              f"dirty {h['parts.dirty_days'][0]}, rebuilt {h['parts.rebuilt_days'][0]}, "
              f"rankings {h['parts.rankings'][0]}, stages {json.dumps({k: round(v, 1) for k, v in stages.items()})}",
              flush=True)
        check(f"ordinary_wall_s[{ci}]", r["wall"], BUDGET["ordinary_wall_s"], " s")
        check(f"ordinary_rss_mb[{ci}]", r["rss_mb"], BUDGET["ordinary_rss_mb"], " MB")
        for k, v in stages.items():
            if v > 60:
                print(f"[perf] note: stage {k} took {v:.0f} s (> 60 s ordinary budget of §10)")
    # ---- the snapshot alone dirties nothing -------------------------------
    snap = root / "data" / "raw" / "rankings.jsonl"
    lines = snap.read_bytes().split(b"\n")
    lines = [l for l in lines if l]
    random.Random(3).shuffle(lines)
    snap.write_bytes(b"\n".join(lines) + b"\n")
    r = run_builder(root, now)
    h = r["health"]
    rk = h["parts.rankings"][0]
    ok = rk.startswith("parsed:") and rk.endswith(":changed:0:days:0") and h["parts.dirty_days"] == ["0"] \
        and h["parts.manifest"] == ["unchanged"]
    print(f"[perf] {'ok  ' if ok else 'FAIL'} rewritten snapshot: {rk}, dirty {h['parts.dirty_days'][0]}, "
          f"manifest {h.get('parts.manifest')} ({r['wall']:.1f} s)")
    if not ok:
        FAILS.append("snapshot_dirties")
    # ---- byte budgets -----------------------------------------------------
    man = json.loads((root / "site" / "d" / "s2" / "manifest.json").read_text())
    days = [e for e in man["days"] if e.get("f") and e["d"] != "undated"]
    rows_b = sum(e["b"] for e in days)
    rows_n = sum(e["n"] for e in days)
    check("rows_b_per_row", rows_b / max(1, rows_n), BUDGET["rows_b_per_row"], " B")
    slug_dir = root / "site" / "d" / "s2"
    biggest = 0
    for e in days:
        for rel in e["specs"].values():
            biggest = max(biggest, (slug_dir / rel).stat().st_size)
    check("largest_block_mb", biggest / 1e6, BUDGET["block_mb"], " MB")
    cubed = [w for w in man["weeks"] if w.get("f")]
    print(f"[perf] cubed weeks: {[w['w'] for w in cubed]}")
    import partition_format as pf
    for w in cubed:
        n = sum(v["n"] for v in w["reg"].values())
        c = pf.read(slug_dir / w["f"]["cells"], expect_kind="cells")
        check(f"dist_b_per_row[w{w['w']}]", w["b"]["dist"] / max(1, n), BUDGET["dist_b_per_row"], " B")
        check(f"chars_b_per_row[w{w['w']}]", w["b"]["chars"] / max(1, n), BUDGET["chars_b_per_row"], " B")
        check(f"cells_mb[w{w['w']}]", w["b"]["cells"] / 1e6, BUDGET["cells_mb"], " MB")
        check(f"comps_mb[w{w['w']}]", w["b"]["comps"] / 1e6, BUDGET["comps_mb"], " MB")
        print(f"[perf]      w{w['w']}: {n:,} rows, {c.header['n_cells']:,} cells")
    foot = sum(p.stat().st_size for p in (root / "site" / "d").rglob("*") if p.is_file()) / 1e6
    print(f"[perf] Pages data footprint: {foot:.1f} MB (window {man['window']['rows']:,} rows, "
          f"{len(days)} listed days, spec vocab {man['spec_vocab']['b'] / 1e6:.2f} MB)")
    if a.rows >= 13_000_000:
        check("footprint_mb", foot, BUDGET["footprint_mb_13m"], " MB")
    if not a.keep:
        shutil.rmtree(root, ignore_errors=True)
    if FAILS:
        print(f"[perf] FAILED: {FAILS}")
        return 1
    print("[perf] all budgets met")
    return 0


if __name__ == "__main__":
    sys.exit(main())
