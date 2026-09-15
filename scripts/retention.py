"""Retention policy -- the ONE place the numbers live (owner, 2026-09-14).

    "don't keep data for longer than 2 weeks. only data in the last two weeks
    is ever relevant, beyond that is useless. remove any features that allow
    looking at data older than 2 weeks. Just to give it a grace period and
    mitigate boundary conditions, it is fine to keep a day or two extra of
    data."

Three numbers, imported by every consumer and never retyped:

  RETENTION_RESETS        what the PAGE shows: the newest two weekly resets per
                          region (build_site_data.apply_retention). Two resets
                          is 7.00 to just under 14.00 days of wall clock,
                          depending where a region sits in its own week.
  RETENTION_MAX_AGE_DAYS  the ceiling on any published row, measured from the
                          wall clock: 14 + one day of grace. The reset cut is
                          anchored to the data, so a long stall would drag it
                          backwards; this is the policy, the reset alignment
                          only the shape.
  RETENTION_DISK_DAYS     what DISK keeps (the journals, the committed seed,
                          the keystone map, the Raider.IO journal): a flat
                          window measured back from the NEWEST ROW ON DISK,
                          never from the wall clock. It is strictly wider than
                          anything the builder can want in every case:
                            normal   builder wants >= now - 15 d; disk keeps
                                     >= newest - 16 d, newest <= now
                            stalled  builder falls back to the reset cut,
                                     >= anchor - 14 d; disk keeps >= anchor - 16 d
                          so the build's oldest day is never half-present, and a
                          stall freezes the prune instead of eating the window.

Dating rule, shared by every side: a run is DATED when it carries a plausible
start (0 < t <= now + a day of clock skew), UNDATED when it carries none, and
IMPLAUSIBLE otherwise. Only DATED-and-older is ever dropped or refused. Undated
and implausible entries are KEPT and COUNTED separately -- "no timestamp" and
"timestamp says 1970" are different bugs, and a missing field must never be
able to delete data (the builder's rule since 821a9e6).

No pandas at import: fetch_data imports pandas lazily inside export() and the
pruner must start in milliseconds on a 5 GB journal.
"""
from __future__ import annotations

import time

RETENTION_RESETS = 2
RETENTION_MAX_AGE_DAYS = 15
RETENTION_DISK_DAYS = 16

assert RETENTION_MAX_AGE_DAYS >= 7 * RETENTION_RESETS + 1, "the ceiling must clear two resets"
assert RETENTION_DISK_DAYS > RETENTION_MAX_AGE_DAYS, "disk must be strictly wider than the page"

DAY_MS = 86_400_000
SKEW_MS = DAY_MS            # an uploader's PC clock may run ahead; one day is skew, more is a bug

UNDATED, IMPLAUSIBLE, DATED = "undated", "implausible", "dated"


def date_class(t, now_ms: float) -> str:
    """UNDATED (None / not a number), IMPLAUSIBLE (<= 0, or past now + skew),
    else DATED. Epoch MILLISECONDS."""
    if t is None or isinstance(t, bool) or not isinstance(t, (int, float)):
        return UNDATED
    if t != t:                            # NaN
        return UNDATED
    if t <= 0 or t > now_ms + SKEW_MS:
        return IMPLAUSIBLE
    return DATED


def data_anchor_ms(times, now_ms: float | None = None) -> float:
    """The newest PLAUSIBLE start in `times` (epoch ms), clamped to now; now
    when there is none. The same clamp as build_site_data.apply_retention
    (`plaus = st[st <= now]; anchor = min(plaus.max(), now)`): one bad clock
    must not drag the window forward, and a stalled collector anchors on what
    it has rather than sliding past it."""
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    best = None
    for t in times:
        if date_class(t, now_ms) == DATED and t <= now_ms and (best is None or t > best):
            best = t
    return now_ms if best is None else min(best, now_ms)


def disk_cut_ms(times, now_ms: float | None = None, days: int = RETENTION_DISK_DAYS):
    """(cut_ms, anchor_ms): the flat disk window measured back from the data
    anchor. Everything DATED before cut_ms is out of the window."""
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    anchor = data_anchor_ms(times, now_ms)
    return anchor - days * DAY_MS, anchor


def in_window(t, cut_ms: float, now_ms: float) -> bool:
    """False ONLY for a DATED start before the cut. Undated and implausible
    entries are in the window by construction (kept, counted elsewhere)."""
    return date_class(t, now_ms) != DATED or t >= cut_ms


def iso(ms: float | None) -> str:
    if ms is None:
        return "none"
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000))
