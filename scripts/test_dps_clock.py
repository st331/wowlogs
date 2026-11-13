#!/usr/bin/env python3
"""DPS stands on the FIGHT clock: the builder publishes the collector's own figure, untouched.

Until 2026-11-13 the builder recomputed every DPS on the keystone clock (the timer, which adds 15 s
for every party death), so each figure read about 1% under the one Warcraft Logs ranks. The pin in
test_site_contract.py only checks that the old function's text is gone; a rewrite in another shape
would pass it. This test runs the REAL builder, in a temporary copy of scripts/ and data/, on the
repository's own export, and checks what it published:

  * rows.dps is a sub-multiset of round(CSV dps)          -- nothing rewrote the column
  * rows.dur is a sub-multiset of round(CSV duration_s)   -- the fight clock, not the keystone clock
  * rows.kdur is a sub-multiset of round(CSV keystone_s)  -- the timer still rides beside it
  * the two clocks differ in nearly every row            -- (they were identical in all of them)
  * the payload and build_health.txt both say which clock the figures stand on

Retention, sampling and de-duplication only drop rows, so a sub-multiset is exactly what an
untouched column looks like. Needs pandas and the export (data/mythic_runs.csv.gz); about 15 s.
"""
import collections, gzip, json, os, pathlib, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSV = ROOT / "data" / "mythic_runs.csv.gz"
if not CSV.exists():
    print("SKIP: data/mythic_runs.csv.gz is not in this checkout, so the builder was NOT run")
    sys.exit(0)
import pandas as pd  # noqa: E402

fails = 0
def check(ok, msg):
    global fails
    print(("ok   " if ok else "FAIL ") + msg)
    fails += 0 if ok else 1

with tempfile.TemporaryDirectory() as tmp:
    tp = pathlib.Path(tmp)
    shutil.copytree(ROOT / "scripts", tp / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "data", tp / "data")
    (tp / "site").mkdir()
    shutil.copyfile(ROOT / "site" / "index.html", tp / "site" / "index.html")
    r = subprocess.run([sys.executable, "-u", str(tp / "scripts" / "build_site_data.py"), "--force"],
                       cwd=tp, capture_output=True, text=True, timeout=1800,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    payload = json.load(gzip.open(tp / "site" / "data.json.gz"))
    health = (tp / "site" / "build_health.txt").read_text()

R = payload["rows"]
n = len(R["dps"])
df = pd.read_csv(CSV, usecols=["dps", "duration_s", "keystone_s"])

def multiset(series):
    return collections.Counter(pd.to_numeric(series, errors="coerce").fillna(0).round(0).astype(int).tolist())

def missing(published, source):
    """How many published values have no counterpart left in the source column."""
    pub = collections.Counter(published)
    return sum(max(0, c - source.get(v, 0)) for v, c in pub.items())

check(n > 1000, f"the builder published {n:,} rows from the repository's export")
m = missing(R["dps"], multiset(df["dps"]))
check(m == 0, f"rows.dps is the collector's dps column, untouched: {m:,} of {n:,} published values are not in the CSV")
m = missing(R["dur"], multiset(df["duration_s"]))
check(m == 0, f"rows.dur is the fight clock (duration_s): {m:,} of {n:,} published values are not in the CSV")
m = missing(R["kdur"], multiset(df["keystone_s"]))
check(m == 0, f"rows.kdur is the keystone clock (keystone_s): {m:,} of {n:,} published values are not in the CSV")
same = sum(1 for a, b in zip(R["dur"], R["kdur"]) if a == b)
check(same < 0.05 * n, f"the two clocks are different columns: equal in {same:,} of {n:,} rows (in every row while DPS was rewritten)")
check(payload.get("dps_clock") == "fight", f"the payload says which clock its DPS stands on: dps_clock={payload.get('dps_clock')!r}")
check("\ndps.clock=fight\n" in health and "dps.keystone_over_fight_median=" in health,
      "build_health.txt carries dps.clock=fight and how far apart the two clocks are")

print("\nPASS" if not fails else f"\nFAILED ({fails} failures)")
sys.exit(1 if fails else 0)
