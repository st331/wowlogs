#!/usr/bin/env python3
"""The page's weekly-reset buckets against the builder's retention window, on the same rows.

The builder (build_site_data.py _window_mask) keeps or drops a RUN by the reset boundary of its region: the region
most of its rows carry, a row with no region casting no vote. The page (site/index.html computeResetBuckets) puts
every row in one of the resets the policy keeps (0 = this reset, 1 = last reset, ...) or past the window. The two
must agree, or the page hides rows the builder shipped: until 2026-11-26 the page read each row's own region, so the
one member of an EU run that was filed under no region took the default boundary (Wed 22:00 UTC instead of Wed 04:00)
and was marked past the window while its four group mates were shown. 1 row on that day's payload, and nothing on
the page said so.

This runs the page's own function in node over a synthetic payload and the builder's own function over the same
rows, and requires: a row is past the window on the page exactly when the builder drops it; every row of a run
sits in one bucket; the count of rows past the window is the count the page would print.

Nothing here is a literal reset count. The cut is the current reset less 7 x (RETENTION_RESETS - 1) days and every
boundary between two kept buckets is probed, so the file holds for two resets (the policy until 2026-11-28) and
for three (since), and would for four.
"""
import json
import pathlib
import re
import subprocess
import sys
import tempfile

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_site_data as b  # noqa: E402

html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
m = re.search(r"^function computeResetBuckets\(\)\{.*?^\}\n", html, re.S | re.M)
assert m, "computeResetBuckets() not found in the page"
FN = m.group(0)
m = re.search(r"^const RESET_RULES = [^\n]*\n", html, re.M)
assert m, "RESET_RULES not found in the page"
RULES = m.group(0)
assert "US:[1,15]" in RULES and "EU:[2,4]" in RULES and "RESET_DEFAULT=[2,22]" in RULES, "the page's reset table moved: " + RULES.strip()
assert b.RESET_RULES == {"US": (1, 15), "EU": (2, 4)} and b.RESET_DEFAULT == (2, 22), "the builder's reset table moved"

EPOCH = pd.Timestamp("2026-01-01T00:00:00Z")
NOW = pd.Timestamp("2026-11-26T07:32:00Z")          # a Thursday: US reset Tue 24 15:00, EU Wed 25 04:00, others Wed 25 22:00
REGIONS = ["CN", "EU", "KR", "US", "Unknown"]
N = b.RETENTION_RESETS
assert N >= 2, "the retention window is off, or holds a single reset"
LAST = N - 1                                        # the oldest bucket the policy keeps
WEEK, H = pd.Timedelta(days=7), pd.Timedelta(hours=1)
RESET = {"US": pd.Timestamp("2026-11-24T15:00:00Z"), "EU": pd.Timestamp("2026-11-25T04:00:00Z"),
         "KR": pd.Timestamp("2026-11-25T22:00:00Z"), "CN": pd.Timestamp("2026-11-25T22:00:00Z")}
CUT = {r: t - WEEK * LAST for r, t in RESET.items()}
DEF_CUT = CUT["KR"]                                 # a run nobody can place is cut on the default boundary

runs = []   # (label, start, [region or None per row])
def run(label, start, regs):
    runs.append((label, pd.Timestamp(start), regs))

# the case that was hidden: an EU run after EU's cut and before the default cut, one member filed under no region
between = CUT["EU"] + 6 * H
assert CUT["EU"] < between < DEF_CUT
run("EU run, one row with no region, between the EU cut and the default cut", between, ["EU"] * 4 + [None])
run("US run, one row with no region, just after the US cut", CUT["US"], ["US"] * 4 + [None])
run("KR run, one row with no region, last reset", "2026-11-20T08:00:00Z", ["KR", None, "KR", "KR", "KR"])
# a row with no region casts no vote, however many there are: one known region decides the run
run("one EU row and four with no region, between the EU cut and the default cut", between, ["EU"] + [None] * 4)
# a run nobody can place: every row without a region
run("no region at all, before the default cut", between, [None] * 5)
run("no region at all, after the default cut", DEF_CUT, [None] * 5)
# the same orphan row at every nearer boundary: a per-row rule would not hide it there, it would file it a reset older
for k in range(1, N):
    run(f"EU run, one row with no region, between the EU and default boundaries of bucket {LAST - k}", between + WEEK * k, ["EU"] * 4 + [None])
# each region one hour either side of EVERY boundary: the cut (k = 0), each inner boundary, the current reset (k = N - 1)
for reg in ("US", "EU", "KR", "CN"):
    for k in range(N):
        t = CUT[reg] + WEEK * k
        run(f"{reg} one hour before boundary {k}", t - H, [reg] * 5)
        run(f"{reg} at boundary {k}", t, [reg] * 5)
        run(f"{reg} one hour after boundary {k}", t + H, [reg] * 5)
# two known regions in one run (none exists in the collected data; the rule still has to be one rule)
two = CUT["US"] + 5 * H
assert CUT["US"] < two < CUT["EU"]
run("3 US + 2 EU after the US cut, before the EU cut", two, ["US", "US", "US", "EU", "EU"])
run("3 EU + 2 US after the US cut, before the EU cut", two, ["EU", "EU", "EU", "US", "US"])
run("2 US + 2 EU + none: the tie goes to the earlier cut", two, ["US", "EU", "US", "EU", None])
run("4 KR + 1 EU after the EU reset, before KR's", "2026-11-25T10:00:00Z", ["KR"] * 4 + ["EU"])
run("old, every region", CUT["US"] - WEEK, ["US", "EU", "KR", "CN", None])
run("newest", "2026-11-26T07:05:00Z", ["EU"] * 5)

rows = []   # one dict per parse
for ri, (label, start, regs) in enumerate(runs):
    for reg in regs:
        rows.append({"report_code": f"r{ri}", "fight_id": 1, "started_at": int(start.value // 1_000_000), "region": reg,
                     "run": ri, "label": label, "start": start})
df = pd.DataFrame(rows)
keep, facts = b._window_mask(df, b.RETENTION_RESETS, b.RETENTION_MAX_AGE_DAYS, NOW)
assert keep is not None, "the retention window is off"
assert facts["floored"] == 0 and not facts["stale"], "the age ceiling must not move the cuts in this fixture"
anchor = facts["anchor"]

payload = {
    "D": {"epoch": "2026-01-01", "regions": REGIONS, "retention": {"anchor": anchor.strftime("%Y-%m-%dT%H:%M:%SZ"), "resets": N}},
    "R": {"day": [int((r["start"] - EPOCH) // pd.Timedelta(days=1)) for r in rows],
          "hr": [int(r["start"].hour) for r in rows],
          "reg": [REGIONS.index(r["region"] or "Unknown") for r in rows],
          "run": [r["run"] for r in rows]},
    "runCount": len(runs),
}
# KEEP_BUCKETS is read off the payload, as the page's initData does
HARNESS = """
const P=JSON.parse(require('fs').readFileSync(process.argv[2],'utf8'));
let D=P.D, R=P.R, N=R.day.length, runCount=P.runCount;
if(process.argv[3]==='days') delete R.hr;
let KEEP_BUCKETS=+P.D.retention.resets, outWindowRows=0; const OUTW=1000;
%s
let availWeeks=[], weekCounts=new Map(), usB0=0, bucketSpan=new Map(), resetHoles=[], curMinDay=0, curMaxDay=-1;
function retInfo(){ return D.retention||null }
%s
computeResetBuckets();
console.log(JSON.stringify({rb:Array.from(R.rbucket), rreg:Array.from(R.rreg), out:outWindowRows, wc:[...weekCounts], avail:availWeeks}));
""" % (RULES, FN)

def page(mode):
    with tempfile.TemporaryDirectory() as td:
        pj, js = pathlib.Path(td) / "p.json", pathlib.Path(td) / "h.js"
        pj.write_text(json.dumps(payload)); js.write_text(HARNESS)
        out = subprocess.run(["node", str(js), str(pj), mode], capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, "the page's computeResetBuckets() threw in node:\n" + out.stderr[-800:]
        return json.loads(out.stdout)

OUTW = 1000
got = page("hours")
bad = []
for i, r in enumerate(rows):
    hidden = got["rb"][i] == OUTW
    if hidden == bool(keep[i]):
        bad.append(f"{r['label']} (row region {r['region']}): builder {'keeps' if keep[i] else 'drops'}, page bucket {got['rb'][i]}")
assert not bad, "the page and the builder disagree on what is inside the window:\n  " + "\n  ".join(bad)
assert got["out"] == int((~keep).sum()), f"the page counts {got['out']} parses past the window, the builder dropped {int((~keep).sum())}"
for ri, (label, start, regs) in enumerate(runs):
    bk = {got["rb"][i] for i, r in enumerate(rows) if r["run"] == ri}
    assert len(bk) == 1, f"one run sits in more than one bucket on the page: {label}: {sorted(bk)}"
    rg = {got["rreg"][i] for i, r in enumerate(rows) if r["run"] == ri}
    assert len(rg) == 1, f"one run is bucketed by more than one region: {label}: {sorted(rg)}"

def bucket(label):
    return {got["rb"][i] for i, r in enumerate(rows) if r["label"] == label}
assert bucket("EU run, one row with no region, between the EU cut and the default cut") == {LAST}, "the row that was hidden on 2026-11-26 is in the oldest kept reset with its run"
assert bucket("US run, one row with no region, just after the US cut") == {LAST}
assert bucket("one EU row and four with no region, between the EU cut and the default cut") == {LAST}, "a row with no region casts no vote"
assert bucket("no region at all, before the default cut") == {OUTW} and bucket("no region at all, after the default cut") == {LAST}, "a run with no region at all is cut on the default boundary"
for k in range(1, N):
    assert bucket(f"EU run, one row with no region, between the EU and default boundaries of bucket {LAST - k}") == {LAST - k}, f"a row with no region is filed with its run at the boundary of bucket {LAST - k}, not a reset older"
for reg in ("US", "EU", "KR", "CN"):
    for k in range(N):
        older = OUTW if k == 0 else LAST - k + 1
        assert bucket(f"{reg} one hour before boundary {k}") == {older}, f"{reg}: the hour before boundary {k} belongs to the older side, got {bucket(f'{reg} one hour before boundary {k}')}"
        assert bucket(f"{reg} at boundary {k}") == {LAST - k}, f"{reg}: boundary {k} opens bucket {LAST - k}"
        assert bucket(f"{reg} one hour after boundary {k}") == {LAST - k}, f"{reg}: the hour after boundary {k} is in bucket {LAST - k}"
assert bucket("3 US + 2 EU after the US cut, before the EU cut") == {LAST} and bucket("3 EU + 2 US after the US cut, before the EU cut") == {OUTW}, "a run of two regions follows the one most of its rows carry"
assert bucket("2 US + 2 EU + none: the tie goes to the earlier cut") == {LAST}, "a tie goes to the earlier cut, toward keeping"
assert bucket("old, every region") == {OUTW} and bucket("newest") == {0}
assert sorted(w for w in got["avail"] if w < 999) == list(range(N)), f"the page holds one bucket per kept reset: {got['avail']}"

# without the start hour the page falls back to whole days: coarser at the boundary, but still one bucket per run
days = page("days")
for ri, (label, start, regs) in enumerate(runs):
    bk = {days["rb"][i] for i, r in enumerate(rows) if r["run"] == ri}
    assert len(bk) == 1, f"day fallback: one run sits in more than one bucket: {label}: {sorted(bk)}"

print(f"reset buckets at {N} resets (cuts " + ", ".join(f"{r} {t:%m-%d %H:%M}" for r, t in CUT.items()) + f"): {len(runs)} runs, {len(rows)} rows; "
      f"the page hides exactly the {int((~keep).sum())} rows the builder drops; buckets {sorted(w for w in got['avail'] if w < 999)}; one bucket and one region per run")
print("\nPASS")
