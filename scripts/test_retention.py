#!/usr/bin/env python3
"""apply_retention() — the ONE place rows are dropped (fleet/checklist.md §Y).

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

info = B.RETENTION_INFO
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

# ---- the constant is the policy: two resets, never more
check(B.RETENTION_RESETS == 2, f"RETENTION_RESETS is 2 (got {B.RETENTION_RESETS})")

print("FAILED" if fails else "PASS", f"({fails} failures)")
sys.exit(1 if fails else 0)
