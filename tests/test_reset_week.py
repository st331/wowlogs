#!/usr/bin/env python3
"""tests/test_reset_week.py (partitioned_payload.md §3.1, §9.1)

`W()` vs `computeResetBuckets` for every hour of 2026 under all three rules,
on both sides of each boundary, and with a client clock up to 6 h behind
`manifest.built` (the `now` clamp).
"""
import datetime as dt
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import sitecalc as sc                                           # noqa: E402

EPOCH = "2026-01-01"
REGIONS = ["US", "EU", "KR"]          # the three rules: US, EU, default (*)
HOUR = 3_600_000


def _site_for(hours, region_idx, now_ms):
    """A minimal payload of one row per hour index, all in one region."""
    hours = np.asarray(hours, dtype=np.int64)
    n = len(hours)
    D = {"regions": REGIONS, "epoch": EPOCH,
         "classes": ["Mage"], "specs": ["Arcane"], "heroes": ["Unknown"],
         "dungeons": ["X"], "roles": ["DPS"], "pars": [1800], "charscore": []}
    R = {"cls": np.zeros(n, dtype=np.int64), "spec": np.zeros(n, dtype=np.int64),
         "hero": np.zeros(n, dtype=np.int64), "dun": np.zeros(n, dtype=np.int64),
         "reg": np.full(n, region_idx, dtype=np.int64), "role": np.zeros(n, dtype=np.int64),
         "key": np.full(n, 12, dtype=np.int64), "deaths": np.zeros(n, dtype=np.int64),
         "dps": np.full(n, 100, dtype=np.int64), "day": hours // 24, "hr": hours % 24,
         "run": np.arange(n, dtype=np.int64), "char": np.arange(n, dtype=np.int64),
         "timed": np.ones(n, dtype=np.int64), "post": np.ones(n, dtype=np.int64)}
    site = sc.Site(D=D, R=R, N=n)
    sc.compute_reset_buckets(site, now_ms)
    return site


def test_anchors_match_the_blueprint():
    assert sc.anchor_ms("US", EPOCH) == sc.parse_iso_ms("2026-01-06T15:00:00Z")
    assert sc.anchor_ms("EU", EPOCH) == sc.parse_iso_ms("2026-01-07T04:00:00Z")
    assert sc.anchor_ms("KR", EPOCH) == sc.parse_iso_ms("2026-01-07T22:00:00Z")
    assert sc.anchor_ms("*", EPOCH) == sc.parse_iso_ms("2026-01-07T22:00:00Z")


def test_every_hour_of_2026_all_rules():
    """For each rule and a set of `now` instants (one per weekday, both sides
    of the boundary hour, plus minute offsets), every hour of 2026 buckets
    identically through W(now)-W(row) and computeResetBuckets."""
    year_start = sc.parse_iso_ms("2026-01-01T00:00:00Z")
    year_end = sc.parse_iso_ms("2027-01-01T00:00:00Z")
    hours = np.arange(0, (year_end - year_start) // HOUR, dtype=np.int64)
    nows = []
    for reg in REGIONS:
        a = sc.anchor_ms(reg, EPOCH)
        for wk in (30, 31, 45):
            for off_h in (-1, 0, 1, 23, 24, 100, 167):
                for mins in (0, 1, 59):
                    nows.append(a + wk * sc.WEEK_MS + off_h * HOUR + mins * 60_000)
    for ri, reg in enumerate(REGIONS):
        for now_ms in nows:
            site = _site_for(hours, ri, now_ms)
            w_now = sc.week_of(now_ms, reg, EPOCH)
            starts = year_start + hours * HOUR
            w_rows = (starts - sc.anchor_ms(reg, EPOCH)) // sc.WEEK_MS
            expect = w_now - w_rows
            got = site.rbucket
            # rows after `now` are in the future: legacy gives 0 (h0 >= b0);
            # W gives a negative bucket. Both sides of the client clamp the
            # future to 0 -- compare where the row is not after now.
            past = starts <= now_ms
            assert np.array_equal(got[past], expect[past]), (reg, now_ms, int(np.nonzero(got[past] != expect[past])[0][0]))
            assert np.all(got[~past] == 0)
            assert np.array_equal(site.W[past], w_rows[past])
    # the boundary hour itself belongs to the NEW week: one hour before is
    # the previous bucket, under every rule
    for ri, reg in enumerate(REGIONS):
        a = sc.anchor_ms(reg, EPOCH)
        now_ms = a + 40 * sc.WEEK_MS + 5 * HOUR
        h_b = (a + 40 * sc.WEEK_MS - year_start) // HOUR
        site = _site_for([h_b - 1, h_b, h_b + 1], ri, now_ms)
        assert list(site.rbucket) == [1, 0, 0], reg
        site = _site_for([h_b - 169, h_b - 168, h_b - 167], ri, now_ms)
        assert list(site.rbucket) == [2, 1, 1], reg


def test_day_fallback_matches_the_day_rule():
    """Without `hr` the client buckets by calendar day against boundsD; W()
    of the fallback is defined as W(now) - bucket so it stays consistent."""
    now_ms = sc.parse_iso_ms("2026-09-17T14:20:00Z")
    days = np.arange(200, 260, dtype=np.int64)
    for ri, reg in enumerate(REGIONS):
        site = _site_for(days * 24, ri, now_ms)
        del site.R["hr"]
        sc.compute_reset_buckets(site, now_ms)
        b0 = site.boundsD[ri]
        expect = np.where(days >= b0, 0, -((days - b0) // 7))
        assert np.array_equal(site.rbucket, expect), reg
        assert np.array_equal(site.W, site.curW[ri] - site.rbucket)


def test_client_clock_behind_manifest_built():
    """A client clock up to 6 h behind manifest.built: `now` is clamped to
    the manifest's instant, so the leading region's newest rows can never
    fall into a week the builder has already advanced past."""
    built = sc.anchor_ms("US", EPOCH) + 36 * sc.WEEK_MS + 90 * 60_000   # 90 min after a US reset
    rows_h = np.array([(built - sc.parse_iso_ms("2026-01-01T00:00:00Z")) // HOUR - k for k in range(0, 200)])
    for behind_h in range(0, 7):
        client_now = built - behind_h * HOUR
        now = sc.effective_now(client_now, built)
        assert now == built
        site = _site_for(rows_h, 0, now)
        ref = _site_for(rows_h, 0, built)
        assert np.array_equal(site.rbucket, ref.rbucket)
        # without the clamp a clock >= 2 h behind would still see the old week
        raw = _site_for(rows_h, 0, client_now)
        if behind_h >= 2:
            assert not np.array_equal(raw.rbucket, ref.rbucket)
    # the clamp never moves `now` backwards
    assert sc.effective_now(built + 5 * HOUR, built) == built + 5 * HOUR
    assert sc.effective_now(built, None) == built


def test_reset_bounds_agree_with_the_client_formula():
    """boundsH/boundsD: setUTCHours(hh) on today, shift back to the weekday,
    and back another week when that is still in the future."""
    for iso in ("2026-09-15T14:59:00Z", "2026-09-15T15:00:00Z", "2026-09-16T03:59:59Z",
                "2026-09-16T04:00:00Z", "2026-09-16T21:59:00Z", "2026-09-16T22:00:00Z",
                "2026-09-20T00:00:00Z", "2026-01-01T00:30:00Z", "2026-12-31T23:59:00Z"):
        now_ms = sc.parse_iso_ms(iso)
        now = dt.datetime.fromtimestamp(now_ms / 1000, dt.timezone.utc)
        bh, bd = sc.reset_bounds(REGIONS, now_ms, EPOCH)
        for ri, reg in enumerate(REGIONS):
            wd, hh = sc.RESET_RULES.get(reg, sc.RESET_DEFAULT)
            b = now.replace(hour=hh, minute=0, second=0, microsecond=0)
            while b.weekday() != wd:
                b -= dt.timedelta(days=1)
            if b > now:
                b -= dt.timedelta(days=7)
            assert b <= now and now - b < dt.timedelta(days=7)
            e = dt.datetime.fromisoformat(EPOCH + "T00:00:00+00:00")
            assert bh[ri] == int((b - e).total_seconds()) // 3600, (iso, reg)
            assert bd[ri] == int((b - e).total_seconds()) // 86400, (iso, reg)
            # the bound IS the absolute week's start
            assert (b.timestamp() * 1000 - sc.anchor_ms(reg, EPOCH)) % sc.WEEK_MS == 0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("test_reset_week: all green")
