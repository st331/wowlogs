#!/usr/bin/env python3
"""Backfill keystone clock times for runs whose reports have aged out of the
zone's report listing.

The sweep only sees the most recent reports WCL will list, so a resweep cannot
recover keystone times for older runs. Their report codes are still in the CSV
though, so fetch the fight lists directly and top up the persistent map that
fetch_data's export reads.
"""
import json
import os
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wcl_client import QuotaDeadline, WCLClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
BATCH = 15


def main():
    csv = ROOT / "data" / "mythic_runs.csv.gz"
    if not csv.exists():
        csv = csv.with_suffix("")
    ks_file = ROOT / "data" / "keystone_times.json"          # committed daily
    ks_cache = ROOT / "data" / "processed" / "keystone_times.json"   # journal cache
    ks = {}
    for src in (ks_file, ks_cache):
        if src.exists():
            try:
                ks.update(json.loads(src.read_text()))
            except ValueError:
                pass
    # 2026-09-08: a standing low-budget step (refresh.yml) rather than a one-off:
    # --max-reports caps one run's spend (~1 point per report); newest first so
    # the current reset is whole before older weeks are topped up
    max_reports = int(os.environ.get("KS_MAX_REPORTS", "0") or 0)
    if "--max-reports" in sys.argv:
        max_reports = int(sys.argv[sys.argv.index("--max-reports") + 1])

    df = pd.read_csv(csv, usecols=["report_code", "fight_id", "started_at"]).drop_duplicates(["report_code", "fight_id"])
    missing = [(c, int(f)) for c, f in zip(df.report_code, df.fight_id)
               if f"{c}:{f}" not in ks]
    newest = df.groupby("report_code")["started_at"].max()
    codes = sorted({c for c, _ in missing}, key=lambda c: -float(newest.get(c, 0) or 0))
    total_codes = len(codes)
    if max_reports and len(codes) > max_reports:
        codes = codes[:max_reports]
    print(f"{len(ks):,} known, {len(missing):,} runs missing across "
          f"{total_codes:,} reports; this run takes {len(codes):,}", flush=True)
    if not codes:
        return

    lock = __import__("threading").Lock()
    done = [0]
    stopped = [None]          # the quota ceiling: stop launching, keep what is fetched

    def fetch(chunk):
        if stopped[0]:
            return
        client = WCLClient(verbose=False)
        parts = [f'a{i}: report(code: "{c}") '
                 f'{{ fights(killType: Kills) {{ id keystoneTime }} }}'
                 for i, c in enumerate(chunk)]
        try:
            data = client.query("{ reportData { " + " ".join(parts) + " } }",
                                est_cost=float(len(chunk)))
        except QuotaDeadline as e:
            # 2026-09-09: this escaped as an uncaught exception, the map was
            # never written and the run's fetched clocks were lost. The
            # ceiling is a normal end here, not a failure.
            if not stopped[0]:
                stopped[0] = str(e)
                print(f"  stopping at the quota ceiling: {e}", flush=True)
            return
        except RuntimeError as e:
            print(f"  batch failed: {e}", flush=True)
            return
        rd = data.get("reportData") or {}
        with lock:
            for i, c in enumerate(chunk):
                rep = rd.get(f"a{i}") or {}
                for fight in rep.get("fights") or []:
                    if fight.get("keystoneTime"):
                        ks[f"{c}:{fight['id']}"] = round(
                            fight["keystoneTime"] / 1000, 1)
            done[0] += len(chunk)
            if done[0] % (BATCH * 10) < BATCH:
                print(f"  {done[0]:,}/{len(codes):,} reports", flush=True)

    chunks = [codes[i:i + BATCH] for i in range(0, len(codes), BATCH)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(fetch, chunks))

    for dst in (ks_file, ks_cache):
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(ks, separators=(",", ":")))
        tmp.replace(dst)
    still = sum(1 for c, f in missing if f"{c}:{f}" not in ks)
    print(f"done: {len(ks):,} keystone times stored, {still:,} still missing"
          + (f" (stopped: {stopped[0]})" if stopped[0] else ""))
    # the build folds fetch_health into build_health; Fetch rewrote the file
    # earlier in this run, so appending here is safe
    try:
        with (ROOT / "data" / "processed" / "fetch_health.txt").open("a") as fh:
            fh.write(f"keystone.backfilled={len(missing) - still}\nkeystone.still_missing={still}\n"
                     + (f"keystone.stopped={stopped[0]}\n" if stopped[0] else ""))
    except OSError:
        pass


if __name__ == "__main__":
    main()
