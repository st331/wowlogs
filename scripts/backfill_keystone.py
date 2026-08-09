#!/usr/bin/env python3
"""Backfill keystone clock times for runs whose reports have aged out of the
zone's report listing.

The sweep only sees the most recent reports WCL will list, so a resweep cannot
recover keystone times for older runs. Their report codes are still in the CSV
though, so fetch the fight lists directly and top up the persistent map that
fetch_data's export reads.
"""
import json
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wcl_client import WCLClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
BATCH = 15


def main(source="ptr"):
    sfx = "" if source == "live" else f"_{source}"
    csv = ROOT / "data" / f"mythic_runs{sfx}.csv.gz"
    if not csv.exists():
        csv = csv.with_suffix("")
    ks_file = ROOT / "data" / f"keystone_times{sfx}.json"
    ks = json.loads(ks_file.read_text()) if ks_file.exists() else {}

    df = pd.read_csv(csv).drop_duplicates(["report_code", "fight_id"])
    missing = [(c, int(f)) for c, f in zip(df.report_code, df.fight_id)
               if f"{c}:{f}" not in ks]
    codes = sorted({c for c, _ in missing})
    print(f"{len(ks):,} known, {len(missing):,} runs missing across "
          f"{len(codes):,} reports", flush=True)
    if not codes:
        return

    lock = __import__("threading").Lock()
    done = [0]

    def fetch(chunk):
        client = WCLClient(verbose=False)
        parts = [f'a{i}: report(code: "{c}") '
                 f'{{ fights(killType: Kills) {{ id keystoneTime }} }}'
                 for i, c in enumerate(chunk)]
        try:
            data = client.query("{ reportData { " + " ".join(parts) + " } }",
                                est_cost=float(len(chunk)))
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

    tmp = ks_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ks, separators=(",", ":")))
    tmp.replace(ks_file)
    still = sum(1 for c, f in missing if f"{c}:{f}" not in ks)
    print(f"done: {len(ks):,} keystone times stored, {still:,} still missing")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "ptr")
