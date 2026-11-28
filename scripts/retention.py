"""Retention policy -- the ONE place the numbers live.

Owner, 2026-11-28:

    "start keeping 3 weeks of data (+3 days to accomodate resets)."

That replaces the two weeks of 2026-09-14 ("don't keep data for longer than 2
weeks ... it is fine to keep a day or two extra of data", which became two
resets, a 15-day ceiling and 16 days on disk). The shape is unchanged: the
window is cut on each region's weekly reset, a ceiling in days sits on top,
and disk is strictly wider than both.

Three numbers, imported by every consumer and never retyped:

  RETENTION_RESETS        what the PAGE shows: the newest three weekly resets
                          per region (build_site_data.apply_retention): this
                          reset, last reset and the one before it. Three
                          resets is 14.00 to just under 21.00 days of wall
                          clock, depending where a region sits in its own week.
  RETENTION_DISK_DAYS     what is KEPT, the owner's own sum: 21 + 3 = 24 days
                          on disk (the journals, the committed seed, the
                          keystone map, the Raider.IO journal), a flat window
                          measured back from the NEWEST ROW ON DISK, never
                          from the wall clock, so the build's oldest day is
                          never half-present and a stall freezes the prune
                          instead of eating the window. In September the
                          owner's "day or two extra" was likewise the DISK
                          number (14 + 2). Regions reset up to 31 hours apart
                          (US Tue 15:00 UTC, EU Wed 04:00, the rest Wed
                          22:00); three days cover that twice over. Disk is
                          strictly wider than anything the builder can want
                          in every case:
                            normal   builder wants >= now - 23 d; disk keeps
                                     >= newest - 24 d, newest <= now
                            stalled  builder falls back to the reset cut,
                                     >= anchor - 21 d; disk keeps >= anchor - 24 d
  RETENTION_MAX_AGE_DAYS  the ceiling on any published row, measured from the
                          wall clock: one day inside disk, 23. The reset cut
                          is anchored to the data, so a long stall would drag
                          it backwards; this is the policy, the reset
                          alignment only the shape. It binds only after
                          collection has stalled for more than two days.

THE WIDENING. Until 2026-12-05 the journals are younger than the window: they
were pruned to 16 days up to 2026-11-27 and grow a day a day from there. The
oldest reset is part-filled for a region until that region's next reset (US
and EU until 2026-12-01 and 02; the others were whole on the day), and
build_site_data ships the instant the journals begin (HELD_FROM below) while
a region's cut is earlier than it, and the page prints the hours short. What
the 16-day prune removed is not bought back: summaries_done.txt remembers
every run ever fetched, so widening the window spends no quota on the days
already let go. The collector does buy the listed runs of those days that
carry no marker (141 keys on the first widened run; a re-upload of a fetched
run under another report code is one of these), so the oldest ROW held is not
where the data begins: the first three-reset build reported its oldest row at
2026-11-04 while the journals start a week later. That is why the edge is
written down here and not read off the frame.
scripts/test_widening.py runs the builder, the pruner at both widths and the
collector's admission and backlog against this; scripts/test_retention_warn.py
runs the page's sentence. The collector's window, the order of its pending
list, the quota cap and the pruner's rules are held where they always were:
test_retention_fetch.py, test_gear_parse.py, test_quota_ceiling.py and
test_prune_journals.py.

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

RETENTION_RESETS = 3
RETENTION_GRACE_DAYS = 3        # the owner's "+3 days"
RETENTION_DISK_DAYS = 7 * RETENTION_RESETS + RETENTION_GRACE_DAYS       # 24: what is KEPT, the owner's own sum
RETENTION_MAX_AGE_DAYS = RETENTION_DISK_DAYS - 1                        # 23: the page stays a day inside disk

assert (RETENTION_RESETS, RETENTION_MAX_AGE_DAYS, RETENTION_DISK_DAYS) == (3, 23, 24), \
    "three weeks and three days kept, the page a day inside that: change the owner's words above with these"
assert RETENTION_MAX_AGE_DAYS >= 7 * RETENTION_RESETS + 1, "the ceiling must clear the resets the page shows"
assert RETENTION_DISK_DAYS > RETENTION_MAX_AGE_DAYS, "disk must be strictly wider than the page"

# Where the journals begin after the widening of 2026-11-28: the cut of the last prune under the 16-day rule
# (prune_state.json, applied 2026-11-27T19:55:05Z). Nothing before it is held except runs first listed after
# their day had been pruned. A region whose cut is earlier than this has a part-filled oldest reset. Inert once
# every region's cut has passed it (after the resets of 1 and 2 December 2026); delete it then, or set it again
# if the window is ever widened again.
HELD_FROM = "2026-11-11T19:35:01Z"

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
