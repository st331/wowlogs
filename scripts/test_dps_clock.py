#!/usr/bin/env python3
"""DPS stands on the FIGHT clock: the builder publishes the collector's own figure, untouched.

Until 2026-11-13 the builder recomputed every DPS on the keystone clock (the timer, which adds 15 s
for every party death), so a figure read about 1% under the one Warcraft Logs ranks at +18 and up
(and a little over it at low keys, where the lead-in outweighs the penalties). The pin in
test_site_contract.py only checks that the old function's text is gone; a rewrite in another shape
would pass it. This test runs the REAL builder, in a temporary copy of scripts/ and data/, on the
repository's own export, and checks what it published:

  * every published ROW (dps, dur, kdur, key, deaths, class, spec) is a row of the CSV, rounded as the builder
    rounds: nothing rewrote a column, and no row carries another row's DPS
  * each column on its own, so a failure says which one moved
  * the two clocks differ in nearly every row            -- (they were identical in all of them)
  * the payload and build_health.txt both say which clock the figures stand on, and the ratio in
    build_health.txt is the CSV's own median of keystone_s / duration_s

Retention, sampling and de-duplication only drop rows, so a sub-multiset is exactly what an
untouched table looks like. Needs pandas and the export (data/mythic_runs.csv.gz); about 15 s.
Without the export the test says so and exits with status 3 (SKIP), never 0.
"""
import collections, gzip, json, os, pathlib, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSV = ROOT / "data" / "mythic_runs.csv.gz"
if not CSV.exists():
    print("SKIP: data/mythic_runs.csv.gz is not in this checkout, so the builder was NOT run")
    sys.exit(3)
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
df = pd.read_csv(CSV, usecols=["dps", "duration_s", "keystone_s", "key_level", "deaths", "class", "spec"])

def multiset(series):
    return collections.Counter(pd.to_numeric(series, errors="coerce").fillna(0).round(0).astype(int).tolist())

def missing(published, source):
    """How many published values have no counterpart left in the source column."""
    pub = collections.Counter(published)
    return sum(max(0, c - source.get(v, 0)) for v, c in pub.items())

check(n > 1000, f"the builder published {n:,} rows from the repository's export")
num = lambda c: pd.to_numeric(df[c], errors="coerce").fillna(0).round(0).astype(int).tolist()
src_rows = collections.Counter(zip(num("dps"), num("duration_s"), num("keystone_s"), num("key_level"), num("deaths"),
                                   df["class"].astype(str).tolist(), df["spec"].astype(str).tolist()))
m = missing(zip(R["dps"], R["dur"], R["kdur"], R["key"], R["deaths"],
                (payload["classes"][c] for c in R["cls"]), (payload["specs"][x] for x in R["spec"])), src_rows)
check(m == 0, f"every published row (dps, dur, kdur, key, deaths, class, spec) is a row of the CSV: {m:,} of {n:,} are not "
              "(a DPS moved to another row fails here even when the column as a whole is intact)")
m = missing(R["dps"], multiset(df["dps"]))
check(m == 0, f"rows.dps is the collector's dps column, untouched: {m:,} of {n:,} published values are not in the CSV")
m = missing(R["dur"], multiset(df["duration_s"]))
check(m == 0, f"rows.dur is the fight clock (duration_s): {m:,} of {n:,} published values are not in the CSV")
m = missing(R["kdur"], multiset(df["keystone_s"]))
check(m == 0, f"rows.kdur is the keystone clock (keystone_s): {m:,} of {n:,} published values are not in the CSV")
same = sum(1 for a, b in zip(R["dur"], R["kdur"]) if a == b)
check(same < 0.05 * n, f"the two clocks are different columns: equal in {same:,} of {n:,} rows (in every row while DPS was rewritten)")
check(payload.get("dps_clock") == "fight", f"the payload says which clock its DPS stands on: dps_clock={payload.get('dps_clock')!r}")
ks = pd.to_numeric(df["keystone_s"], errors="coerce"); du = pd.to_numeric(df["duration_s"], errors="coerce")
okc = ks.notna() & (ks > 0) & du.notna() & (du > 0)
want = float((ks[okc] / du[okc]).median())
got = next((float(l.split("=", 1)[1]) for l in health.splitlines() if l.startswith("dps.keystone_over_fight_median=")), None)
check("\ndps.clock=fight\n" in health and got is not None and abs(got - want) < 5e-4,
      f"build_health.txt carries dps.clock=fight and the median of keystone / fight clock: {got} (the CSV's own: {want:.4f})")

print("\nPASS" if not fails else f"\nFAILED ({fails} failures)")
sys.exit(1 if fails else 0)
