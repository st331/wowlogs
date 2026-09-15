#!/usr/bin/env python3
"""apply_retention() — the one place rows leave the PUBLISHED frame
(build_site_data.py, called once inside build()). Disk retention is a second,
separate rail (scripts/prune_journals.py, strictly wider, measured from the
newest row on disk); this suite pins the builder half.

Owner, 2026-09-14: "don't keep data for longer than 2 weeks ... remove any
features that allow looking at data older than 2 weeks." This function is the
only code in the pipeline that deletes rows from the published frame, so it is
pinned here:

  * rows inside the newest two resets survive, older rows do not;
  * the cut is PER REGION (US Tue 15:00 UTC, EU Wed 04:00 UTC) — a flat cut
    would take an extra half-day off one region and leave it on another;
  * UNDATED rows are KEPT, because a row whose started_at will not parse
    cannot be shown to be old and a parser bug must not become data loss;
  * the window is anchored to the DATA, not the wall clock, so a collection
    outage does not slide the window past every row there is (the pipeline
    was down 21 hours on 2026-09-13 — a now-anchored window would have
    published an empty page);
  * it is IDEMPOTENT: building twice does not shrink the frame again;
  * RETENTION_INFO's counters equal what was actually dropped, because the
    page prints them and the owner's standing rule is that nothing the
    reader does not see may go unsaid.
"""
import copy
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_site_data as B  # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


def ms(ts):
    return int(pd.Timestamp(ts, tz="UTC").timestamp() * 1000)


def frame(rows):
    """rows: (region, started_at_ms_or_None) -> a minimal builder frame."""
    return pd.DataFrame({
        "region": [r for r, _ in rows],
        "started_at": [t for _, t in rows],
        "report_code": [f"R{i}" for i in range(len(rows))],
        "fight_id": list(range(len(rows))),
    })


# The anchor is the newest plausible row. Pin it at a Wednesday so US and EU
# sit in different places in their weeks — the case a flat cut gets wrong.
ANCHOR = "2026-09-09T12:00:00Z"          # Wednesday
# US reset Tue 15:00 -> newest US reset is 2026-09-08 15:00; two resets back
# starts 2026-09-01 15:00.
US_CUT = pd.Timestamp("2026-09-01T15:00:00Z")
# EU reset Wed 04:00 -> newest EU reset is 2026-09-09 04:00 (04:00 < 12:00);
# two resets back starts 2026-09-02 04:00.
EU_CUT = pd.Timestamp("2026-09-02T04:00:00Z")

rows = [
    ("US", ms(ANCHOR)),                          # the anchor itself
    ("US", ms("2026-09-01T15:00:01Z")),          # one second inside the US cut
    ("US", ms("2026-09-01T14:59:59Z")),          # one second outside it
    ("US", ms("2026-08-20T00:00:00Z")),          # far outside
    ("EU", ms("2026-09-02T04:00:01Z")),          # inside the EU cut
    ("EU", ms("2026-09-02T03:59:59Z")),          # outside it
    ("EU", ms("2026-09-05T00:00:00Z")),          # comfortably inside
    ("KR", ms("2026-09-08T00:00:00Z")),          # a region with no explicit rule
    ("US", None),                                # undated
]
df = frame(rows)
out = B.apply_retention(df.copy(), "test")
kept = set(out["report_code"])

check(kept == {"R0", "R1", "R4", "R6", "R7", "R8"},
      f"kept exactly the in-window rows and the undated one: {sorted(kept)}")
check("R2" not in kept and "R3" not in kept,
      "a US row one second before the US cut is dropped, and so is an older one")
check("R5" not in kept, "an EU row one second before the EU cut is dropped")
check("R8" in kept, "the undated row is KEPT")

# RETENTION_INFO is a module dict mutated in place: SNAPSHOT it, never alias it
info = copy.deepcopy(B.RETENTION_INFO)
check(info["rows_in"] == 9 and info["rows_out"] == 6 and info["dropped"] == 3,
      f"counters: in {info['rows_in']} out {info['rows_out']} dropped {info['dropped']}")
check(info["undated"] == 1, f"undated counted: {info['undated']}")
check(info["runs_dropped"] == 3, f"runs dropped counted: {info['runs_dropped']}")
check(info["cut"]["US"] == US_CUT.strftime("%Y-%m-%dT%H:%M:%SZ"),
      f"US cut is its own reset boundary: {info['cut']['US']}")
check(info["cut"]["EU"] == EU_CUT.strftime("%Y-%m-%dT%H:%M:%SZ"),
      f"EU cut is its own reset boundary: {info['cut']['EU']}")
check(info["cut"]["US"] != info["cut"]["EU"],
      "the two regions really do cut at different instants")

# ---- idempotent: the window is a fixed point once applied
again = B.apply_retention(out.copy(), "test")
check(len(again) == len(out) and set(again["report_code"]) == kept,
      f"idempotent: {len(out)} rows -> {len(again)} rows")

# ---- anchored to the DATA, not the wall clock. Every row here is months old;
# a now-anchored window would keep nothing at all.
old = frame([("US", ms("2026-05-06T12:00:00Z")),     # Wednesday
             ("US", ms("2026-05-05T15:00:01Z")),     # inside two resets of it
             ("US", ms("2026-03-01T00:00:00Z"))])    # far older
out_old = B.apply_retention(old.copy(), "test")
check(len(out_old) == 2,
      f"a stalled pipeline still publishes its newest two resets: {len(out_old)} rows")

# ---- degenerate inputs must not raise
check(len(B.apply_retention(frame([]), "test")) == 0, "an empty frame is handled")
only_undated = frame([("US", None), ("EU", None)])
check(len(B.apply_retention(only_undated.copy(), "test")) == 2,
      "a frame of only undated rows keeps every row")

# ---- a region column that still holds NA (build() cleans it later, not before)
na_reg = frame([(None, ms(ANCHOR)), ("US", ms("2026-08-01T00:00:00Z"))])
res = B.apply_retention(na_reg.copy(), "test")
check(len(res) == 1 and res.iloc[0]["report_code"] == "R0",
      f"an NA region is windowed, not crashed on: {len(res)} row(s)")

# ---- the constant is the policy: two resets, never more; every other window binds to it
check(B.RETENTION_RESETS == 2, f"RETENTION_RESETS is 2 (got {B.RETENTION_RESETS})")
check(B.RETENTION_MAX_AGE_DAYS == 15, f"the ceiling is 15 days = 14 + one day of grace (got {B.RETENTION_MAX_AGE_DAYS})")
check(B.SIDECAR_WINDOW_RESETS == B.RETENTION_RESETS and B.BUILDS_WINDOW_RESETS == B.RETENTION_RESETS,
      f"no sidecar window claims a span other than retention's (stats {B.SIDECAR_WINDOW_RESETS}, builds {B.BUILDS_WINDOW_RESETS})")
check(B.SPECSTATS_WINDOW_DAYS == 7 * B.RETENTION_RESETS + 1,
      f"the specstats rail is one day wider than two resets can reach ({B.SPECSTATS_WINDOW_DAYS})")

# ---- anchor, wall clock and the span held are SHIPPED, so the page can bucket on the
# same instants the build cut on and say how far behind the newest run is
check(info["anchor"] == "2026-09-09T12:00:00Z", f"anchor shipped: {info['anchor']}")
check(info["now"] is not None and info["lag_h"] is not None and info["lag_h"] >= 0, f"wall clock + lag shipped ({info['lag_h']} h)")
check(info["days_kept"]["US"] == round((pd.Timestamp(ANCHOR) - US_CUT).total_seconds() / 86400, 2)
      and info["days_kept"]["EU"] == round((pd.Timestamp(ANCHOR) - EU_CUT).total_seconds() / 86400, 2),
      f"days actually held per region are shipped: {info['days_kept']}")
check(info["runs_in"] == 9 and info["runs_out"] == 6, f"run counters in/out: {info['runs_in']}/{info['runs_out']}")
check(info["note"].startswith("the newest 2 weekly resets per region, nothing older than 15 days"),
      "the printed sentence LEADS with the policy: " + info["note"][:80])
check("set aside 3 older parses (3 runs)" in info["note"], "…and carries this build's counts as a trailing clause")
check(isinstance(info["exceptions"], list) and {e["key"] for e in info["exceptions"]} == {"rating", "talents"},
      "the two measurements not limited to the window are named for the page")

# ---- the hard ceiling: an injected wall clock far past the anchor
NOW_LATE = "2026-09-20T12:00:00Z"          # 11 days after the anchor -> floor = 09-05 12:00
out_f = B.apply_retention(df.copy(), "floor", now=NOW_LATE)
info_f = copy.deepcopy(B.RETENTION_INFO)
check(info_f["cut"]["US"] == "2026-09-05T12:00:00Z" and info_f["cut"]["EU"] == "2026-09-05T12:00:00Z",
      f"the 15-day floor binds on every region when the reset cut is older: {info_f['cut']}")
check(info_f["floored"] >= 2 and not info_f["stale"], f"floored regions counted ({info_f['floored']}), not stale")
check(set(out_f["report_code"]) == {"R0", "R7", "R8"},
      f"only rows inside the floor survive (R6 at 05 Sep 00:00 is 12 h before it): {sorted(out_f['report_code'])}")
# ...and a stall longer than the ceiling is STALE: the reset cut is kept and it says so
NOW_STALE = "2026-10-15T12:00:00Z"
out_s = B.apply_retention(df.copy(), "stale", now=NOW_STALE)
info_s = copy.deepcopy(B.RETENTION_INFO)
check(info_s["stale"] and info_s["cut"]["US"] == US_CUT.strftime("%Y-%m-%dT%H:%M:%SZ") and len(out_s) == 6,
      f"past the ceiling the page is NOT blanked: stale={info_s['stale']}, {len(out_s)} rows on the reset cut")
check("STALE" in info_s["note"], "…and the sentence says so")

# ---- RETENTION_INFO is reset on every call, including the early return
B.apply_retention(frame([]), "empty")
e = copy.deepcopy(B.RETENTION_INFO)
check(e["rows_in"] == 0 and e["cut"] is None and e["anchor"] is None and e["dropped"] == 0 and e["note"],
      "an empty frame leaves no stale keys behind and still carries the policy sentence")

# ---- PER RUN, NOT PER ROW. A run is one dungeon instance: its rows pass or fail
# together, decided by the run's modal known region (ties -> the earliest cut).
def run_frame(rows):
    """rows: (run_id, region_or_None, started_at_ms_or_None)."""
    return pd.DataFrame({
        "region": [r for _, r, _ in rows],
        "started_at": [t for _, _, t in rows],
        "report_code": [rid for rid, _, _ in rows],
        "fight_id": [1] * len(rows),
    })
# 4 US rows + 1 region-less row, started 2 days BEFORE the US cut but inside EU's:
# the run is US (4 votes), so all 5 leave -- the orphan must not be published alone
t_between = ms("2026-08-30T00:00:00Z")            # inside neither US (09-01) nor EU (09-02) -> gone
t_us_only = ms("2026-09-01T20:00:00Z")            # after the US cut, before the EU cut
mixed = run_frame([("A", "US", t_us_only), ("A", "US", t_us_only), ("A", "US", t_us_only),
                   ("A", "US", t_us_only), ("A", None, t_us_only),
                   ("B", "EU", t_us_only), ("B", "EU", t_us_only), ("B", "US", t_us_only),   # EU-majority run
                   ("C", "US", t_between), ("C", None, t_between),
                   ("D", "EU", ms(ANCHOR)),                                                   # the anchor
                   ("E", "US", ms("2026-09-05T00:00:00Z")), ("E", "US", None)])               # an undated row in a kept run
out_m = B.apply_retention(mixed.copy(), "runs")
info_m = copy.deepcopy(B.RETENTION_INFO)
kept_m = out_m.groupby("report_code").size().to_dict()
check(kept_m.get("A") == 5, f"run A (US by 4 votes, after the US cut) is kept WHOLE, orphan included: {kept_m.get('A')} rows")
check("B" not in kept_m, f"run B (EU by 2 votes, before the EU cut) leaves WHOLE, its US row with it: {kept_m.get('B')}")
check("C" not in kept_m, "run C (before every cut) leaves whole")
check(kept_m.get("E") == 2, f"an undated row keeps its run's decision, and the run is kept: {kept_m.get('E')}")
check(info_m["runs_region_mixed"] == 1 and info_m["rows_region_overridden"] == 1,
      f"region disagreement is counted: mixed {info_m['runs_region_mixed']}, overridden rows {info_m['rows_region_overridden']}")
check(info_m["runs_dropped"] == 2 and info_m["undated"] == 1, f"counters: runs dropped {info_m['runs_dropped']}, undated {info_m['undated']}")
# a run with NO known region at all is decided as "Unknown" (the default rule), never crashed on
nr = run_frame([("Z", None, ms(ANCHOR)), ("Z", None, ms(ANCHOR))])
check(len(B.apply_retention(nr.copy(), "noreg")) == 2, "a run with no known region is kept under the default rule")

# ---- THE BOUNDARY the stalled case does not reach: the window advances when the FIRST ROW
# of a new reset lands, not when the reset instant passes. Deliberate (data anchor) and pinned
# from both ends.
before = [("US", ms("2026-09-08T14:59:59Z")),      # newest row, 1 s BEFORE the US reset
          ("US", ms("2026-09-02T00:00:00Z")),      # inside A's window, not B's
          ("US", ms("2026-08-26T00:00:00Z")),      # inside A's window (cut 08-25 15:00)
          ("US", ms("2026-08-20T00:00:00Z")),      # outside both
          ("EU", ms("2026-09-06T00:00:00Z"))]      # a region the US row must not move
outA = B.apply_retention(frame(before).copy(), "boundary-A", now="2026-09-09T00:00:00Z")
infoA, keptA = copy.deepcopy(B.RETENTION_INFO), set(outA["report_code"])
after = before + [("US", ms("2026-09-08T15:00:00Z"))]          # the first row of the new reset
outB = B.apply_retention(frame(after).copy(), "boundary-B", now="2026-09-09T00:00:00Z")
infoB, keptB = copy.deepcopy(B.RETENTION_INFO), set(outB["report_code"])
check(infoA["cut"]["US"] == "2026-08-25T15:00:00Z" and infoB["cut"]["US"] == "2026-09-01T15:00:00Z",
      f"the US cut jumps exactly one week when the first row of the new reset lands: {infoA['cut']['US']} -> {infoB['cut']['US']}")
check(infoA["cut"]["EU"] == infoB["cut"]["EU"], "…and the EU cut does not move")
check(keptA == {"R0", "R1", "R2", "R4"} and keptB == {"R0", "R1", "R4", "R5"},
      f"R2 (26 Aug) leaves with the advance, counted: dropped {infoA['dropped']} -> {infoB['dropped']}")
check(infoB["dropped"] == infoA["dropped"] + 1, "the row that left is in the counter")

# ---- the sidecar window is the SAME rule (it used to drop undated rows silently)
mask = B._sidecar_window(df, 2, now="2026-09-09T12:00:00Z")
check(mask is not None and bool(mask[8]) and not bool(mask[2]),
      "the sidecar mask keeps the undated row and drops the one before the cut, like apply_retention")
check(B._sidecar_window(na_reg, 2) is not None, "the sidecar mask survives an NA region")
hdr = B._retention_header(df, 2)
check(hdr and hdr["resets"] == 2 and hdr["cut"] and hdr["max_age_days"] == 15, f"sidecar header carries retention's window: {hdr}")
check(B._window_cuts(df, 0) is None and B._sidecar_window(df, 0) is None, "resets=0 switches the window off")

# ---- outage annotations: filtered to the window at the payload boundary, fail open
B.apply_retention(df.copy(), "gaps")                # cuts: US 09-01 15:00, EU 09-02 04:00
gaps = [{"from": "2026-08-20T00:00:00Z", "to": "2026-08-28T00:00:00Z", "note": "old"},
        {"from": "2026-08-30T00:00:00Z", "to": "2026-09-01T16:00:00Z", "note": "straddles the US cut"},
        {"from": "2026-09-05T00:00:00Z", "to": None, "note": "open-ended"},
        {"from": "2026-09-05T00:00:00Z", "to": "not a date", "note": "unparseable end"}]
pub, sup = B._gaps_in_window(gaps)
check([g["note"] for g in pub] == ["straddles the US cut", "open-ended", "unparseable end"] and sup == 1,
      f"a gap wholly before the earliest cut is suppressed ({sup}); straddling, open-ended and unparseable ends are kept")
B.apply_retention(frame([]), "nocut")
pub2, sup2 = B._gaps_in_window(gaps)
check(len(pub2) == 4 and sup2 == 0, "with no cut known the filter FAILS OPEN and publishes every entry")

print("FAILED" if fails else "PASS", f"({fails} failures)")
sys.exit(1 if fails else 0)
