#!/usr/bin/env python3
"""sitecalc -- the Python oracle for the dashboard's numbers.

A line-by-line port of the client's statistics (site/index.html, line
references `C:` below are to the 2026-09-02 file, 7,490 lines) plus the
partitioned-path rules of fleet/blueprints/partitioned_payload.md §3.1–§3.3
that the client of PR-2 implements:

  * computeResetBuckets (C:1879–1925) and the absolute reset week `W()`
    of §3.1 with the `now = max(client now, manifest.built)` clamp;
  * buildRuns (C:1851–1873), rowPass/tierPass/groupKey (C:2597–2632),
    refChars/effMinFor (C:2643–2670);
  * aggregate (C:2759–2822) as TWO accumulators into one per-group record:
    the row part is the client's code; the cube part appends each passing
    cell's `dist` slice, adds n/dsum/dth/dz, extends dmin/dmax and adds the
    run counts of §3.4-1 — with the group-major STAMP pass for every
    distinct-character count (§3.3);
  * aggregateElite (C:2684–2757), setBonusRows (C:6742–6797);
  * renderPulse's bags (C:6549–6613), renderTrend's bags / calc / gate /
    top-N / slope / daily fallback (C:7057–7200) under the §3.3 rule (gate
    and rank over the row window, per-bucket points from per-week bags);
  * renderComps' scoring (C:6872–6937) over `RUNS`, and the same numbers
    from a `comps` cube (§3.2);
  * render()'s gate: `CHART_KEYS` and `effMin` (C:6192–6290).

Everything is computed in the client's iteration order with sequential
floating-point adds, so a value here is the value the browser prints, bit for
bit; tests/test_sitecalc_matches_js.py runs the real functions under node on
the same fixture and asserts identity for 200 random states.

Inputs are the legacy payload shape (`D` = the top-level dict, `R` = `D["rows"]`
as arrays) or, for the partitioned path, the same columns concatenated from
day files plus cube tables (`CubeWeek`). `cube_from_rows()` is the reference
definition of the four cube tables of §3.2, written from rows.
"""
from __future__ import annotations

import datetime as _dt
import functools
import json
import math
import pathlib
from dataclasses import dataclass, field

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
SEASON_FILE = ROOT / "data" / "season.json"

# ---- constants copied from the client -----------------------------------
# C:1533–1543
MELEE = {"DeathKnight|Blood", "DeathKnight|Frost", "DeathKnight|Unholy",
         "DemonHunter|Devourer", "DemonHunter|Havoc", "DemonHunter|Vengeance",
         "Druid|Feral", "Druid|Guardian", "Hunter|Survival", "Monk|Brewmaster",
         "Monk|Mistweaver", "Monk|Windwalker", "Paladin|Holy", "Paladin|Protection",
         "Paladin|Retribution", "Rogue|Assassination", "Rogue|Outlaw",
         "Rogue|Subtlety", "Shaman|Enhancement", "Warrior|Arms", "Warrior|Fury",
         "Warrior|Protection"}
RANGED = {"Druid|Balance", "Druid|Restoration", "Evoker|Augmentation",
          "Evoker|Devastation", "Evoker|Preservation", "Hunter|BeastMastery",
          "Hunter|Marksmanship", "Mage|Arcane", "Mage|Fire", "Mage|Frost",
          "Priest|Discipline", "Priest|Holy", "Priest|Shadow", "Shaman|Elemental",
          "Shaman|Restoration", "Warlock|Affliction", "Warlock|Demonology",
          "Warlock|Destruction"}
# C:1544 -- [weekday Mon=0, hour UTC]; test_reset_rule_tables_match pins these
# against season.json, which pins them against the client.
RESET_RULES = {"US": (1, 15), "EU": (2, 4)}
RESET_DEFAULT = (2, 22)
# C:1546–1571 (id, betterUp) -- the five Trends metrics + the two rating tabs
TABS = {"avg": True, "med": True, "adeaths": False, "deathless": True,
        "arating": True, "mrating": True, "chars": True}
CHART_MAX = 40          # C:1575
TREND_MAX = 16          # C:1576
PULSE_MAX, PULSE_THIN = 12, 25          # C:1577
ELITE_KEEP, ELITE_PCTL, ELITE_MIN, ELITE_DAYS = 0.10, 0.85, 50, 14   # C:2682
SET_CELL_MIN, SET_CELLS_MIN, SET_BUCKET_MIN = 5, 3, 30             # C:6726
COMPS_K = 5             # C:6887 `const K=5`
ROLE_RANK = {"Tank": 0, "Healer": 1, "DPS": 2}     # C:1862
NAN = float("nan")
WEEK_MS = 604_800_000
HOUR_MS = 3_600_000


# ---- small helpers, ported --------------------------------------------------
def q50(sorted_vals):                     # C:2579
    n = len(sorted_vals)
    return sorted_vals[(n - 1) // 2] if n % 2 else \
        (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2


def qp(sorted_vals, f):                   # C:2582 linear-interpolated quantile
    x = (len(sorted_vals) - 1) * f
    i = math.floor(x)
    if i + 1 < len(sorted_vals):
        return sorted_vals[i] + (x - i) * (sorted_vals[i + 1] - sorted_vals[i])
    return sorted_vals[i]


def set_bucket(t):                        # C:2614
    return -1 if t < 0 else 0 if t < 2 else 1 if t < 4 else 2


def _js_cmp(v):
    """A JS comparator result: NaN counts as 0 (Array.prototype.sort)."""
    if v != v:
        return 0
    return -1 if v < 0 else 1 if v > 0 else 0


def js_sort(items, comparator):
    """Stable sort with a JS-style numeric comparator."""
    return sorted(items, key=functools.cmp_to_key(lambda a, b: _js_cmp(comparator(a, b))))


def locale_cmp(a: str, b: str) -> int:
    """String.prototype.localeCompare for this vocabulary. Class, spec and
    hero names are ASCII words (spaces, one apostrophe); for them ICU's root
    collation orders exactly like code points, which the JS oracle test
    confirms on every `name` sort it draws."""
    return -1 if a < b else 1 if a > b else 0


def default_season() -> dict:
    return json.loads(SEASON_FILE.read_text())


# ---- reset weeks (§3.1) and computeResetBuckets (C:1879–1925) -------------
def _epoch_ms(epoch: str) -> int:
    d = _dt.datetime.fromisoformat(epoch + "T00:00:00+00:00")
    return int(d.timestamp() * 1000)


def parse_iso_ms(s: str) -> int:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return int(_dt.datetime.fromisoformat(s).timestamp() * 1000)


def effective_now(client_now_ms: int, built_ms: int | None) -> int:
    """§3.1: now = max(Date.now(), Date.parse(manifest.built))."""
    return client_now_ms if built_ms is None else max(client_now_ms, built_ms)


def reset_bounds(regions, now_ms: int, epoch: str, rules=None, default=None):
    """boundsH/boundsD per region index, C:1889–1898."""
    rules = RESET_RULES if rules is None else rules
    default = RESET_DEFAULT if default is None else default
    epoch_ms = _epoch_ms(epoch)
    now = _dt.datetime.fromtimestamp(now_ms / 1000, _dt.timezone.utc)
    bounds_h, bounds_d = {}, {}
    for ri, reg in enumerate(regions):
        wd, hh = rules.get(reg, default)
        b = now.replace(hour=hh, minute=0, second=0, microsecond=0)   # setUTCHours
        shift = ((b.weekday()) - wd + 7) % 7        # ((getUTCDay()+6)%7 - wd + 7) % 7
        b = b - _dt.timedelta(days=shift)
        if b > now:
            b = b - _dt.timedelta(days=7)
        bms = int(b.timestamp() * 1000)
        bounds_h[ri] = (bms - epoch_ms) // HOUR_MS
        bounds_d[ri] = (bms - epoch_ms) // 86_400_000
    return bounds_h, bounds_d


def anchor_ms(reg: str, epoch: str, rules=None, default=None) -> int:
    """§3.1 anchor[reg] = first instant >= EPOCH at the region's reset
    weekday/hour (US Tue 15:00 -> 2026-01-06T15:00Z for epoch 2026-01-01)."""
    rules = RESET_RULES if rules is None else rules
    default = RESET_DEFAULT if default is None else default
    wd, hh = rules.get(reg, default)
    e = _dt.datetime.fromisoformat(epoch + "T00:00:00+00:00")
    d = e.replace(hour=hh)
    d = d + _dt.timedelta(days=(wd - d.weekday()) % 7)
    if d < e:
        d = d + _dt.timedelta(days=7)
    return int(d.timestamp() * 1000)


def week_of(started_ms: int, reg: str, epoch: str, rules=None, default=None) -> int:
    """§3.1 W(row) = floor((started_ms - anchor[reg]) / 604,800,000)."""
    return (started_ms - anchor_ms(reg, epoch, rules, default)) // WEEK_MS


def bucket_of(started_ms: int, now_ms: int, reg: str, epoch: str,
              rules=None, default=None) -> int:
    """Client bucket b = W(now, reg) - W(row) (§3.1)."""
    return week_of(now_ms, reg, epoch, rules, default) - \
        week_of(started_ms, reg, epoch, rules, default)


def compute_reset_buckets(site: "Site", now_ms: int) -> None:
    """C:1879–1925 verbatim: rbucket per row, weekCounts, availWeeks, usB0,
    curMinDay/curMaxDay. Hours when `hr` is present, whole days otherwise."""
    D, R, N = site.D, site.R, site.N
    hr = R.get("hr")
    HR = hr if hr is not None and len(hr) == N else None
    bounds_h, bounds_d = reset_bounds(D["regions"], now_ms, D["epoch"])
    site.boundsH, site.boundsD = bounds_h, bounds_d
    rb = np.full(N, 999, dtype=np.int64)
    day, reg = R["day"], R["reg"]
    for i in range(N):
        d0 = int(day[i])
        if d0 < 0:
            continue
        if HR is not None and HR[i] >= 0:
            h0, b0 = d0 * 24 + int(HR[i]), bounds_h[int(reg[i])]
            rb[i] = 0 if h0 >= b0 else -((h0 - b0) // 168)       # ceil((b0-h0)/168)
        else:
            b0 = bounds_d[int(reg[i])]
            rb[i] = 0 if d0 >= b0 else -((d0 - b0) // 7)         # ceil((b0-d0)/7)
    site.rbucket = rb
    regions = D["regions"]
    site.usB0 = bounds_d[regions.index("US")] if "US" in regions else bounds_d[0]
    wc: dict[int, int] = {}
    for i in range(N):                    # first-seen key order, like the Map
        b = int(rb[i])
        if b < 999:
            wc[b] = wc.get(b, 0) + 1
    site.weekCounts = wc
    site.availWeeks = sorted(wc)
    mn, mx = 10 ** 9, -1
    m0 = (rb == 0) & (day >= 0)
    if m0.any():
        mn, mx = int(day[m0].min()), int(day[m0].max())
    if mx < 0:
        mn = 0
    site.curMinDay, site.curMaxDay = mn, mx
    # the absolute week of every row (§3.1), consistent with rbucket by
    # construction: W(row) = W(now) - bucket
    now_h = (now_ms - _epoch_ms(D["epoch"])) // HOUR_MS
    site.curW = {}
    for ri, r in enumerate(regions):
        a_h = (anchor_ms(r, D["epoch"]) - _epoch_ms(D["epoch"])) // HOUR_MS
        site.curW[ri] = (now_h - a_h) // 168
    site.W = np.where(rb < 999,
                      np.array([site.curW[int(reg[i])] for i in range(N)], dtype=np.int64) - rb,
                      -10 ** 6)


# ---- the site: payload + client state ----------------------------------
def default_state() -> dict:              # C:1587–1610
    return {"cls": set(), "spec": set(), "hero": set(), "dun": set(),
            "role": {"DPS"}, "reg": set(), "merge": True, "melee": False,
            "ranged": False, "klo": 18, "khi": 22, "weeksA": set(),
            "weeksB": {1}, "compare": False, "dayCut": 0, "compMin": 20,
            "postTune": True, "proj": False, "minchars": 250, "sort": "desc",
            "tab": "med", "trendMetric": "avg", "trendView": "grid",
            "timedOnly": True, "tier0": False, "tier2": False, "tier4": True,
            "pctl": 50, "elite": False, "skill": False, "pctlB": 85,
            "trendNorm": "dps", "trajSort": "metric"}


@dataclass
class Site:
    D: dict
    R: dict
    N: int = 0
    runCount: int = 0
    state: dict = field(default_factory=default_state)
    DEF: dict | None = None
    refMemo: dict = field(default_factory=dict)
    RUNS: list = field(default_factory=list)
    rbucket: np.ndarray | None = None
    W: np.ndarray | None = None
    curW: dict = field(default_factory=dict)
    weekCounts: dict = field(default_factory=dict)
    availWeeks: list = field(default_factory=list)
    usB0: int = 0
    curMinDay: int = 0
    curMaxDay: int = -1
    boundsH: dict = field(default_factory=dict)
    boundsD: dict = field(default_factory=dict)
    hasTier: bool = False
    hasRating: bool = False
    hasTune: bool = False
    hasProj: bool = False
    hasTimed: bool = False
    CHARSCORE: list = field(default_factory=list)
    eliteHidden: int = 0
    lastEffMin: int = 0
    stamp: "Stamp | None" = None

    # projection helpers, C:1618–1622
    def proj_on(self):
        return self.hasProj and self.state["proj"]

    def dps_at(self, i):
        return self.R["dps"][i] * (self.R["tmul"][i] / 1e4) if self.proj_on() else self.R["dps"][i]

    def proj_skip(self, i):
        return self.proj_on() and self.R["tmul"][i] == 0


class Stamp:
    """The group-major stamp pass of §3.3: one Uint32Array(char_max+1) and a
    generation counter replace a Set per group. `count(iterables)` is the
    exact size of the union of the char ids it is given."""

    def __init__(self, char_max: int):
        self.arr = np.zeros(int(char_max) + 1, dtype=np.uint32)
        self.gen = 0

    def begin(self):
        self.gen += 1
        return self.gen

    def count(self, *char_arrays) -> int:
        g = self.begin()
        n = 0
        for ch in char_arrays:
            ch = np.asarray(ch, dtype=np.int64)
            if not len(ch):
                continue
            # first occurrence of every id in this group, in order
            mask = self.arr[ch] != g
            uniq = np.unique(ch[mask])
            self.arr[uniq] = g
            n += len(uniq)
        return n


def _as_arrays(R: dict) -> dict:
    out = {}
    for k, v in R.items():
        a = np.asarray(v)
        if a.dtype.kind == "f":
            a = a.astype(np.float64)
        elif a.dtype.kind in "iub":
            a = a.astype(np.int64)
        out[k] = a
    return out


def init_data(D: dict, now_ms: int, cfg_minchars: int = 250,
              R: dict | None = None) -> Site:
    """initData (C:1670–1774) minus the DOM: the derived arrays and the
    per-source defaults of state. `now_ms` is the frozen clock
    (WOWLOGS_NOW in the tests; max(Date.now(), manifest.built) in §3.1)."""
    R = _as_arrays(D["rows"] if R is None else R)
    site = Site(D=D, R=R)
    N = len(R["dps"])
    site.N = N
    run = R["run"]
    site.runCount = int(run.max()) + 1 if N else 0            # C:1673
    atk = np.zeros(N, dtype=np.int64)                          # C:1674–1679
    key_names = [f"{c}|{s}" for c in D["classes"] for s in D["specs"]]
    ns = len(D["specs"])
    lut = np.array([1 if k in MELEE else 2 if k in RANGED else 0 for k in key_names], dtype=np.int64)
    if N:
        atk = lut[R["cls"] * ns + R["spec"]]
    R["atk"] = atk
    build_runs(site)                                           # C:1685
    compute_reset_buckets(site, now_ms)                        # C:1686
    site.hasTier = "tier" in R and bool((R["tier"] >= 0).any())      # C:1693
    site.CHARSCORE = list(D.get("charscore") or [])            # C:1695
    site.hasRating = any(v >= 0 for v in site.CHARSCORE)
    site.hasTune = bool(D.get("tuning")) and "post" in R       # C:1697
    st = site.state
    st["postTune"] = site.hasTune
    site.hasProj = bool(D.get("projection")) and "tmul" in R   # C:1699
    if not site.hasProj:
        st["proj"] = False
    site.hasTimed = "timed" in R and bool((R["timed"] >= 0).any())   # C:1718
    st["timedOnly"] = site.hasTimed
    st["weeksA"] = set()                                        # C:1736–1744
    st["dayCut"] = 0
    if 0 in site.weekCounts:
        st["weeksA"].add(0)
    elif site.availWeeks:
        st["weeksA"].add(site.availWeeks[0])
    st["weeksB"] = set()
    wb = next((w for w in site.availWeeks if w > 0), site.availWeeks[0] if site.availWeeks else None)
    if wb is not None:
        st["weeksB"].add(wb)
    keys = sorted(set(int(k) for k in R["key"]))               # C:1745–1753
    kmin, kmax = keys[0], keys[-1]
    st["khi"] = max(kmax - 1, kmin)
    st["klo"] = max(st["khi"] - 5, kmin)
    st["minchars"] = cfg_minchars
    site.DEF = {"klo": st["klo"], "khi": st["khi"], "timedOnly": st["timedOnly"],
                "postTune": st["postTune"]}                    # C:1758
    site.refMemo = {}
    st["compare"] = False
    char_max = int(R["char"].max()) if N else 0
    site.stamp = Stamp(max(char_max, len(site.CHARSCORE)))
    return site


# ---- buildRuns C:1851–1873 -------------------------------------------------
def build_runs(site: Site) -> None:
    D, R = site.D, site.R
    RUNS: list = [None] * site.runCount
    run, deaths = R["run"], R["deaths"]
    has_dur, has_kdur = "dur" in R, "kdur" in R
    for i in range(site.N):
        r = int(run[i])
        o = RUNS[r]
        if o is None:
            o = RUNS[r] = {"dur": int(R["dur"][i]) if has_dur else 0,
                           "kdur": int(R["kdur"][i]) if has_kdur else 0,
                           "dun": int(R["dun"][i]), "key": int(R["key"][i]),
                           "day": int(R["day"][i]), "deaths": 0, "comp": []}
        o["deaths"] += int(deaths[i])
        o["comp"].append((int(R["cls"][i]), int(R["spec"][i]), int(R["role"][i])))
    roles, classes, specs = D["roles"], D["classes"], D["specs"]
    pars = D.get("pars") or []
    for o in RUNS:
        if o is None:
            continue

        def cmp(a, b):
            ra = ROLE_RANK.get(roles[a[2]], 3)
            rb = ROLE_RANK.get(roles[b[2]], 3)
            return (ra - rb) or locale_cmp(classes[a[0]], classes[b[0]]) \
                or locale_cmp(specs[a[1]], specs[b[1]])
        o["comp"] = js_sort(o["comp"], cmp)
        o["key_"] = ",".join(str(c[0] * 100 + c[1]) for c in o["comp"])
        par = pars[o["dun"]] if o["dun"] < len(pars) else 0
        par = par or 0
        o["pct"] = (par - o["kdur"]) / par * 100 if (par and o["kdur"]) else None
    site.RUNS = RUNS


# ---- filters C:2575–2632 -----------------------------------------------------
def idx_set(sel, names):                  # C:2575
    if not sel:
        return None
    return {names.index(s) for s in sel}


def base_masks(site: Site) -> dict:       # C:2586
    D, st = site.D, site.state
    return {"cls": idx_set(st["cls"], D["classes"]), "spec": idx_set(st["spec"], D["specs"]),
            "hero": idx_set(st["hero"], D["heroes"]), "dun": idx_set(st["dun"], D["dungeons"]),
            "role": idx_set(st["role"], D["roles"]), "reg": idx_set(st["reg"], D["regions"]),
            "atk": (1 if st["melee"] else 2) if st["melee"] != st["ranged"] else 0}


def tier_pass(site: Site, i) -> bool:     # C:2597–2610 (labHas("tier4pc") is static true)
    st = site.state
    if not site.hasTier:
        return True
    if not (st["tier0"] or st["tier2"] or st["tier4"]):
        return True
    t = int(site.R["tier"][i]) if "tier" in site.R else -1
    if t < 0:
        return False
    b = set_bucket(t)
    return b >= 0 and [st["tier0"], st["tier2"], st["tier4"]][b]


def tier_pass_bucket(site: Site, tb: int) -> bool:
    """tierPass over a cube cell's `tb` (= setBucket(tier))."""
    st = site.state
    if not site.hasTier:
        return True
    if not (st["tier0"] or st["tier2"] or st["tier4"]):
        return True
    if tb < 0:
        return False
    return [st["tier0"], st["tier2"], st["tier4"]][tb]


def row_mask(site: Site, m: dict, any_tier: bool = False) -> np.ndarray:
    """rowPass (C:2615–2629) for every row at once; identical predicate."""
    R, st = site.R, site.state
    N = site.N
    ok = np.ones(N, dtype=bool)
    if st["postTune"]:
        ok &= R["post"] == 1
    if st["timedOnly"]:
        ok &= R["timed"] == 1
    if not any_tier and site.hasTier and (st["tier0"] or st["tier2"] or st["tier4"]):
        t = R["tier"]
        tb = np.where(t < 0, -1, np.where(t < 2, 0, np.where(t < 4, 1, 2)))
        allow = np.array([False, st["tier0"], st["tier2"], st["tier4"]])
        ok &= allow[tb + 1]
    ok &= (R["key"] >= st["klo"]) & (R["key"] <= st["khi"])
    for col, key in (("cls", "cls"), ("spec", "spec"), ("dun", "dun"),
                     ("role", "role"), ("reg", "reg")):
        if m[key]:
            ok &= np.isin(R[col], list(m[key]))
    if not st["merge"] and m["hero"]:
        ok &= np.isin(R["hero"], list(m["hero"]))
    if m["atk"]:
        ok &= R["atk"] == m["atk"]
    return ok


def group_key(site: Site, i) -> int:      # C:2630
    R = site.R
    cs = int(R["cls"][i]) * 100 + int(R["spec"][i])
    return cs if site.state["merge"] else cs * 200 + int(R["hero"][i])


def group_keys(site: Site) -> np.ndarray:
    R = site.R
    cs = R["cls"] * 100 + R["spec"]
    return cs if site.state["merge"] else cs * 200 + R["hero"]


def period_cut(site: Site, weeks) -> int:  # C:1928
    st = site.state
    return st["dayCut"] if (st["dayCut"] > 0 and len(weeks) == 1 and 0 in weeks
                            and site.curMaxDay >= 0) else 0


def period_mask(site: Site, weeks, cut) -> np.ndarray:   # C:1932
    ok = np.ones(site.N, dtype=bool)
    if weeks:
        ok &= np.isin(site.rbucket, list(weeks))
    if cut:
        ok &= site.R["day"] >= site.curMaxDay - cut + 1
    return ok


def proj_skip_mask(site: Site) -> np.ndarray:
    if site.proj_on():
        return site.R["tmul"] == 0
    return np.zeros(site.N, dtype=bool)


def dps_values(site: Site, idx: np.ndarray) -> list:
    """dpsAt(i) for the given rows, as Python floats/ints in row order."""
    dps = site.R["dps"][idx]
    if site.proj_on():
        return [d * (t / 1e4) for d, t in zip(dps.tolist(), site.R["tmul"][idx].tolist())]
    return dps.tolist()


# ---- refChars / effMinFor C:2643–2670 ---------------------------------
def ref_chars_key(site: Site) -> str:
    st = site.state
    return ",".join(sorted(st["role"])) + "|" + _js_bool(st["melee"]) + "|" + _js_bool(st["ranged"])


def _js_bool(b) -> str:
    return "true" if b else "false"


def ref_chars(site: Site, precomputed: dict | None = None) -> int:
    """refChars() (C:2643–2662). With `precomputed` = manifest.window.refchars
    the lookup uses the client's exact key string and never scans (§8.1)."""
    if not site.DEF:
        return 0
    key = ref_chars_key(site)
    if precomputed is not None and key in precomputed:
        return int(precomputed[key])
    hit = site.refMemo.get(key)
    if hit is not None:
        return hit
    D, R, DEF, st = site.D, site.R, site.DEF, site.state
    role = idx_set(st["role"], D["roles"])
    atk = (1 if st["melee"] else 2) if st["melee"] != st["ranged"] else 0
    ok = np.ones(site.N, dtype=bool)
    if DEF["postTune"]:
        ok &= R["post"] == 1
    if DEF["timedOnly"]:
        ok &= R["timed"] == 1
    ok &= (R["key"] >= DEF["klo"]) & (R["key"] <= DEF["khi"])
    if role:
        ok &= np.isin(R["role"], list(role))
    if atk:
        ok &= R["atk"] == atk
    ok &= ~proj_skip_mask(site)
    n = int(len(np.unique(R["char"][ok])))
    site.refMemo[key] = n
    return n


def all_ref_chars(site: Site) -> dict:
    """manifest.window.refchars: refChars() under DEF for ALL 24 reachable
    keys (8 role subsets x 3 attack states), projection off, keyed with the
    client's exact string (§2.6)."""
    st = site.state
    saved = {k: st[k] for k in ("role", "melee", "ranged", "proj")}
    out = {}
    roles = ["DPS", "Healer", "Tank"]
    st["proj"] = False
    for mask in range(8):
        st["role"] = {r for j, r in enumerate(roles) if mask >> j & 1}
        for melee, ranged in ((False, False), (True, False), (False, True)):
            st["melee"], st["ranged"] = melee, ranged
            site.refMemo = {}
            out[ref_chars_key(site)] = ref_chars(site)
    st.update(saved)
    site.refMemo = {}
    return out


def eff_min_for(site: Site, pool: int, precomputed: dict | None = None) -> int:   # C:2666
    ref = ref_chars(site, precomputed)
    st = site.state
    return max(1, _js_round(st["minchars"] * pool / ref)) if ref > 0 else st["minchars"]


def _js_round(x: float) -> int:
    """Math.round: half up (towards +inf)."""
    return int(math.floor(x + 0.5))


# ---- cube tables (§3.2) ------------------------------------------------------
CELL_DIMS = ("reg", "cls", "spec", "hero", "role", "dun", "key", "timed", "post", "tb")
RL_DIMS = ("cls", "spec", "dun", "key", "reg", "timed", "post")
RG_DIMS = ("dun", "key", "reg", "timed", "post")
COMP_DIMS = ("comp", "dun", "key", "reg", "timed", "post")


@dataclass
class CubeWeek:
    """The four files of one frozen week, as arrays (what the reader hands
    the client). `cells` holds the three tables of the cells file."""
    week: int
    cells: dict          # Table A: CELL_DIMS + n dsum dth dz nr dmin dmax doff
    rl: dict             # Table B: RL_DIMS + nr_rl dup_rl
    rg: dict             # Table C: RG_DIMS + nrun
    dist: dict | None = None    # coff dps deaths
    chars: np.ndarray | None = None
    comps: dict | None = None   # header comps (list of tuples), clen + COMP_DIMS + n ksum kmin bday bdeaths dsum
    cube_sha: str = ""

    @property
    def n_cells(self):
        return len(self.cells["n"])


def cube_from_rows(site: Site, idx: np.ndarray, week: int) -> CubeWeek:
    """The reference definition of §3.2 from the rows of one week (all
    regions). Row order inside a cell is `dps` ascending (stable)."""
    R = site.R
    idx = np.asarray(idx, dtype=np.int64)
    tier = R["tier"][idx] if "tier" in R else np.full(len(idx), -1)
    tb = np.where(tier < 0, -1, np.where(tier < 2, 0, np.where(tier < 4, 1, 2)))
    cols = {"reg": R["reg"][idx], "cls": R["cls"][idx], "spec": R["spec"][idx],
            "hero": R["hero"][idx], "role": R["role"][idx], "dun": R["dun"][idx],
            "key": R["key"][idx], "timed": R["timed"][idx], "post": R["post"][idx],
            "tb": tb}
    dps, deaths, ch, run, day = (R["dps"][idx], R["deaths"][idx], R["char"][idx],
                                 R["run"][idx], R["day"][idx])
    # lexicographic cell order, dps ascending within (np.lexsort: last key primary)
    order = np.lexsort([dps] + [cols[d] for d in reversed(CELL_DIMS)])
    keys = np.stack([cols[d][order] for d in CELL_DIMS], axis=1)
    if len(order):
        change = np.any(keys[1:] != keys[:-1], axis=1)
        starts = np.concatenate([[0], np.nonzero(change)[0] + 1])
    else:
        starts = np.array([], dtype=np.int64)
    ends = np.concatenate([starts[1:], [len(order)]])
    cells = {d: keys[starts, j] for j, d in enumerate(CELL_DIMS)}
    n = ends - starts
    dps_o, dth_o, ch_o, run_o, day_o = dps[order], deaths[order], ch[order], run[order], day[order]
    csum = np.concatenate([[0], np.cumsum(dps_o.astype(np.int64))])
    dsum = csum[ends] - csum[starts]
    dcs = np.concatenate([[0], np.cumsum(dth_o.astype(np.int64))])
    dth = dcs[ends] - dcs[starts]
    zcs = np.concatenate([[0], np.cumsum((dth_o == 0).astype(np.int64))])
    dz = zcs[ends] - zcs[starts]
    nr = np.array([len(np.unique(run_o[a:b])) for a, b in zip(starts, ends)], dtype=np.int64)
    dmin = np.array([day_o[a:b].min() for a, b in zip(starts, ends)], dtype=np.int64)
    dmax = np.array([day_o[a:b].max() for a, b in zip(starts, ends)], dtype=np.int64)
    cells.update({"n": n, "dsum": dsum, "dth": dth, "dz": dz, "nr": nr,
                  "dmin": dmin, "dmax": dmax, "doff": starts.astype(np.int64)})
    dist = {"coff": np.concatenate([starts, [len(order)]]).astype(np.int64),
            "dps": dps_o.astype(np.int64), "deaths": dth_o.astype(np.int64)}
    chars = ch_o.astype(np.int64)
    # Table B: per (cls,spec,dun,key,reg,timed,post): distinct runs, runs with
    # the spec twice
    rl_keys = np.stack([cols[d] for d in RL_DIMS], axis=1)
    rl: dict = {}
    per = {}
    for j in range(len(idx)):
        k = tuple(int(x) for x in rl_keys[j])
        per.setdefault(k, {}).setdefault(int(run[j]), 0)
        per[k][int(run[j])] += 1
    rl_sorted = sorted(per)
    rl = {d: np.array([k[i] for k in rl_sorted], dtype=np.int64) for i, d in enumerate(RL_DIMS)}
    rl["nr_rl"] = np.array([len(per[k]) for k in rl_sorted], dtype=np.int64)
    rl["dup_rl"] = np.array([sum(1 for c in per[k].values() if c >= 2) for k in rl_sorted], dtype=np.int64)
    # Table C
    rg_keys = np.stack([cols[d] for d in RG_DIMS], axis=1)
    perg: dict = {}
    for j in range(len(idx)):
        perg.setdefault(tuple(int(x) for x in rg_keys[j]), set()).add(int(run[j]))
    rg_sorted = sorted(perg)
    rg = {d: np.array([k[i] for k in rg_sorted], dtype=np.int64) for i, d in enumerate(RG_DIMS)}
    rg["nrun"] = np.array([len(perg[k]) for k in rg_sorted], dtype=np.int64)
    # comps: clocked runs only (kdur > 0); region = the run's first row's
    RUNS = site.RUNS
    comp_index: dict = {}
    comp_list: list = []
    ccells: dict = {}
    first_row: dict = {}
    for j in range(len(idx)):
        r = int(run[j])
        if r not in first_row:
            first_row[r] = j
    for r, j in first_row.items():
        o = RUNS[r]
        if not o or o["kdur"] <= 0:
            continue
        comp = tuple(c[0] * 100 + c[1] for c in o["comp"])
        ci = comp_index.get(comp)
        if ci is None:
            ci = comp_index[comp] = len(comp_list)
            comp_list.append(comp)
        k = (ci, o["dun"], o["key"], int(cols["reg"][j]), int(cols["timed"][j]), int(cols["post"][j]))
        e = ccells.get(k)
        if e is None:
            e = ccells[k] = {"n": 0, "ksum": 0, "kmin": None, "bday": None, "bdeaths": None, "dsum": 0}
        e["n"] += 1
        e["ksum"] += o["kdur"]
        e["dsum"] += o["deaths"]
        if e["kmin"] is None or o["kdur"] < e["kmin"]:     # first in content order on a tie
            e["kmin"], e["bday"], e["bdeaths"] = o["kdur"], o["day"], o["deaths"]
    ck = sorted(ccells)
    comps = {d: np.array([k[i] for k in ck], dtype=np.int64) for i, d in enumerate(COMP_DIMS)}
    for f in ("n", "ksum", "kmin", "bday", "bdeaths", "dsum"):
        comps[f] = np.array([ccells[k][f] for k in ck], dtype=np.int64)
    comps["comps"] = comp_list
    comps["clen"] = np.array([len(c) for c in comp_list], dtype=np.int64)
    comps["K"] = max((len(c) for c in comp_list), default=0)
    return CubeWeek(week=week, cells=cells, rl=rl, rg=rg, dist=dist, chars=chars, comps=comps)


def _cell_mask(site: Site, cube: CubeWeek, m: dict, weeks, any_tier=False,
               tier_any_known=False) -> np.ndarray:
    """rowPass + periodPass over Table A cells: bucket per cell is
    curW[reg] - W (§3.1), used iff >= 3 and in the period."""
    c, st = cube.cells, site.state
    n = cube.n_cells
    ok = np.ones(n, dtype=bool)
    b = np.array([site.curW[int(r)] for r in c["reg"]], dtype=np.int64) - cube.week
    ok &= b >= 3
    if weeks:
        ok &= np.isin(b, list(weeks))
    if st["postTune"]:
        ok &= c["post"] == 1
    if st["timedOnly"]:
        ok &= c["timed"] == 1
    if tier_any_known:
        ok &= c["tb"] >= 0
    elif not any_tier and site.hasTier and (st["tier0"] or st["tier2"] or st["tier4"]):
        allow = np.array([False, st["tier0"], st["tier2"], st["tier4"]])
        ok &= allow[c["tb"] + 1]
    ok &= (c["key"] >= st["klo"]) & (c["key"] <= st["khi"])
    for key in ("cls", "spec", "dun", "role", "reg"):
        if m[key]:
            ok &= np.isin(c[key], list(m[key]))
    if not st["merge"] and m["hero"]:
        ok &= np.isin(c["hero"], list(m["hero"]))
    if m["atk"]:
        ns = len(site.D["specs"])
        keys = [f"{a}|{s}" for a in site.D["classes"] for s in site.D["specs"]]
        lut = np.array([1 if k in MELEE else 2 if k in RANGED else 0 for k in keys])
        ok &= lut[c["cls"] * ns + c["spec"]] == m["atk"]
    return ok


def _rl_mask(site: Site, cube: CubeWeek, m: dict, weeks) -> np.ndarray:
    """The run-level cells (no hero, no tier dimension) passing the filters."""
    c, st = cube.rl, site.state
    ok = np.ones(len(c["nr_rl"]), dtype=bool)
    b = np.array([site.curW[int(r)] for r in c["reg"]], dtype=np.int64) - cube.week
    ok &= b >= 3
    if weeks:
        ok &= np.isin(b, list(weeks))
    if st["postTune"]:
        ok &= c["post"] == 1
    if st["timedOnly"]:
        ok &= c["timed"] == 1
    ok &= (c["key"] >= st["klo"]) & (c["key"] <= st["khi"])
    for key in ("cls", "spec", "dun", "reg"):
        if m[key]:
            ok &= np.isin(c[key], list(m[key]))
    if m["role"] or m["atk"]:
        ns = len(site.D["specs"])
        keys = [f"{a}|{s}" for a in site.D["classes"] for s in site.D["specs"]]
        if m["atk"]:
            lut = np.array([1 if k in MELEE else 2 if k in RANGED else 0 for k in keys])
            ok &= lut[c["cls"] * ns + c["spec"]] == m["atk"]
        if m["role"]:
            sr = site.D.get("spec_role")
            if sr is None:
                raise ValueError("D.spec_role is needed for a role filter over run-level cells")
            roles = site.D["roles"]
            # manifest.spec_role carries role CODES (§2.6); a legacy-shaped
            # payload in the tests carries names -- accept both
            role_code = np.array([x if isinstance(x, (int, np.integer)) else (roles.index(x) if x in roles else -1)
                                  for x in sr])
            ok &= np.isin(role_code[c["spec"]], list(m["role"]))
    return ok


def _rg_mask(site: Site, cube: CubeWeek, weeks) -> np.ndarray:
    c, st = cube.rg, site.state
    ok = np.ones(len(c["nrun"]), dtype=bool)
    b = np.array([site.curW[int(r)] for r in c["reg"]], dtype=np.int64) - cube.week
    ok &= b >= 3
    if weeks:
        ok &= np.isin(b, list(weeks))
    if st["postTune"]:
        ok &= c["post"] == 1
    if st["timedOnly"]:
        ok &= c["timed"] == 1
    ok &= (c["key"] >= st["klo"]) & (c["key"] <= st["khi"])
    m = base_masks(site)
    for key in ("dun", "reg"):
        if m[key]:
            ok &= np.isin(c[key], list(m[key]))
    return ok


def cubed_weeks(cubes) -> set:
    return set(cubes) if cubes else set()


def rows_served_mask(site: Site, cubes) -> np.ndarray:
    """§3.1 serving rule: a row is used iff its bucket <= 2, or its week is
    not cubed. Without cubes (legacy) every row is served."""
    if not cubes:
        return np.ones(site.N, dtype=bool)
    cw = np.array(sorted(cubed_weeks(cubes)), dtype=np.int64)
    return (site.rbucket <= 2) | ~np.isin(site.W, cw)


# ---- aggregate C:2759–2822 (+ the cube accumulator of §3.3) ----------------
def aggregate(site: Site, weeks, cubes: dict | None = None) -> dict:
    """Returns {"groups": {key: rec}, "parses", "runs", "chars", "dmin",
    "dmax", "runSeen", "runs_bound", "runs_exact"}; rec has the client's
    fields (C:2808–2819). `cubes` = {W: CubeWeek} for cube-served weeks.
    Under state.proj cube weeks contribute nothing (§3.3)."""
    if site.state["elite"]:
        return aggregate_elite(site)
    D, R, st = site.D, site.R, site.state
    m = base_masks(site)
    cut = period_cut(site, weeks)
    ok = row_mask(site, m) & period_mask(site, weeks, cut) & ~proj_skip_mask(site) \
        & rows_served_mask(site, cubes)
    idx = np.nonzero(ok)[0]
    gk = group_keys(site)[idx]
    dv = dps_values(site, idx)
    raw = R["dps"][idx].tolist()
    dth = R["deaths"][idx].tolist()
    chs = R["char"][idx].tolist()
    runs_ = R["run"][idx].tolist()
    days = R["day"][idx]
    parses = int(len(idx))
    run_seen = np.zeros(site.runCount, dtype=np.uint8)
    if len(idx):
        run_seen[R["run"][idx]] = 1
    runs = int(run_seen.sum())
    dmin, dmax = 10 ** 9, -1
    dd = days[days >= 0]
    if len(dd):
        dmin, dmax = int(dd.min()), int(dd.max())
    groups: dict = {}
    for j in range(len(idx)):
        key = int(gk[j])
        g = groups.get(key)
        if g is None:
            i = int(idx[j])
            g = groups[key] = {"cls": int(R["cls"][i]), "spec": int(R["spec"][i]),
                               "hero": -1 if st["merge"] else int(R["hero"][i]),
                               "n": 0, "sum": 0, "dsum": 0, "dzero": 0, "dps": [],
                               "dth": [], "rsum": 0, "rdps": [], "chars": [],
                               "cube_chars": [], "runs": set(),
                               "nr_sum": 0, "nr_rl_sum": 0, "dup_rl_sum": 0,
                               "cube_runs_exact": True}
        v = dv[j]
        g["n"] += 1
        g["sum"] += v
        g["dsum"] += dth[j]
        if dth[j] == 0:
            g["dzero"] += 1
        g["dps"].append(v)
        g["dth"].append(dth[j])
        g["rsum"] += raw[j]
        g["rdps"].append(raw[j])
        g["chars"].append(chs[j])
        g["runs"].add(runs_[j])
    all_chars = [R["char"][idx]]
    cube_runs_total, cube_runs_exact = 0, True
    if cubes and not site.proj_on():
        single_spec = bool(m["spec"]) and len(m["spec"]) == 1 and \
            (not m["cls"] or len(m["cls"]) == 1)
        tier_split = site.hasTier and (st["tier0"] or st["tier2"] or st["tier4"])
        roster_filter = bool(m["cls"] or m["spec"] or m["hero"] and not st["merge"]
                             or m["role"] or m["atk"])
        for W in sorted(cubes):
            cube = cubes[W]
            cm = _cell_mask(site, cube, m, weeks)
            c = cube.cells
            for ci in np.nonzero(cm)[0]:
                ci = int(ci)
                cs = int(c["cls"][ci]) * 100 + int(c["spec"][ci])
                key = cs if st["merge"] else cs * 200 + int(c["hero"][ci])
                g = groups.get(key)
                if g is None:
                    g = groups[key] = {"cls": int(c["cls"][ci]), "spec": int(c["spec"][ci]),
                                       "hero": -1 if st["merge"] else int(c["hero"][ci]),
                                       "n": 0, "sum": 0, "dsum": 0, "dzero": 0, "dps": [],
                                       "dth": [], "rsum": 0, "rdps": [], "chars": [],
                                       "cube_chars": [], "runs": set(),
                                       "nr_sum": 0, "nr_rl_sum": 0, "dup_rl_sum": 0,
                                       "cube_runs_exact": True}
                n, doff = int(c["n"][ci]), int(c["doff"][ci])
                if cube.dist is None or cube.chars is None:
                    raise ValueError(f"week {W}: dist/chars not resident -- withheld")
                sl = cube.dist["dps"][doff:doff + n].tolist()
                g["n"] += n
                g["sum"] += int(c["dsum"][ci])
                g["dsum"] += int(c["dth"][ci])
                g["dzero"] += int(c["dz"][ci])
                g["dps"].extend(sl)
                g["dth"].extend(cube.dist["deaths"][doff:doff + n].tolist())
                g["rsum"] += int(c["dsum"][ci])
                g["rdps"].extend(sl)
                g["cube_chars"].append(cube.chars[doff:doff + n])
                g["nr_sum"] += int(c["nr"][ci])
                parses += n
                dmin = min(dmin, int(c["dmin"][ci]))
                dmax = max(dmax, int(c["dmax"][ci]))
                all_chars.append(cube.chars[doff:doff + n])
            # run counts: exact from rl when nothing splits the run-level cell
            rm = _rl_mask(site, cube, m, weeks)
            rl = cube.rl
            for ri in np.nonzero(rm)[0]:
                ri = int(ri)
                cs = int(rl["cls"][ri]) * 100 + int(rl["spec"][ri])
                if st["merge"]:
                    g = groups.get(cs)
                    if g is not None:
                        g["nr_rl_sum"] += int(rl["nr_rl"][ri])
                        g["dup_rl_sum"] += int(rl["dup_rl"][ri])
                        if tier_split:
                            g["cube_runs_exact"] = False
                else:
                    for key, g in groups.items():
                        if key // 200 == cs:
                            g["dup_rl_sum"] += int(rl["dup_rl"][ri])
                            g["cube_runs_exact"] = False
            # the global k-runs KPI (§3.4-1): exact from rg under no
            # roster-dimension filter, from rl for a single spec; a tier box
            # splits every run-level table, so it falls back to Σ nr over the
            # passing Table-A cells (bounded by the same dup_rl) and is "≤"
            if tier_split:
                cube_runs_total += int(c["nr"][cm].sum())
                cube_runs_exact = False
            elif not roster_filter:
                cube_runs_total += int(cube.rg["nrun"][_rg_mask(site, cube, weeks)].sum())
            elif single_spec:
                cube_runs_total += int(rl["nr_rl"][rm].sum())
            else:
                cube_runs_total += int(rl["nr_rl"][rm].sum())
                cube_runs_exact = False
    out: dict = {}
    pctl, pctlB = st["pctl"] / 100, st["pctlB"] / 100
    for key, g in groups.items():
        g["dps"].sort()
        med = qp(g["dps"], pctl)
        arating, mrating, rn = NAN, NAN, 0
        gchars = site.stamp.count(np.asarray(g["chars"], dtype=np.int64), *g["cube_chars"])
        if site.hasRating:
            # ratings off the character SET (each character once); the union
            # order does not matter, rs is sorted
            uniq = np.unique(np.concatenate([np.asarray(g["chars"], dtype=np.int64)]
                                            + [np.asarray(x, dtype=np.int64) for x in g["cube_chars"]]))
            rs = [site.CHARSCORE[c] for c in uniq.tolist()
                  if c < len(site.CHARSCORE) and site.CHARSCORE[c] >= 0]
            if rs:
                rs.sort()
                rn = len(rs)
                arating = _seq_sum(rs) / len(rs)
                mrating = q50(rs)
        ravg = rmed = None
        if st["proj"]:
            g["rdps"].sort()
            ravg = g["rsum"] / g["n"]
            rmed = qp(g["rdps"], pctl)
        g["dth"].sort()
        if g["cube_runs_exact"] and (st["merge"]):
            runs_g = len(g["runs"]) + g["nr_rl_sum"]
            bound = 0
        else:
            runs_g = len(g["runs"]) + g["nr_sum"]
            bound = g["dup_rl_sum"]
        out[key] = {"cls": D["classes"][g["cls"]], "spec": D["specs"][g["spec"]],
                    "hero": "" if g["hero"] < 0 else D["heroes"][g["hero"]],
                    "n": g["n"], "avg": g["sum"] / g["n"], "med": med,
                    "ravg": ravg, "rmed": rmed,
                    "q30": qp(g["dps"], .30), "q85": qp(g["dps"], .85), "qb": qp(g["dps"], pctlB),
                    "qdA": qp(g["dth"], pctl), "qdB": qp(g["dth"], pctlB),
                    "adeaths": g["dsum"] / g["n"], "deathless": 100 * g["dzero"] / g["n"],
                    "chars": gchars, "runs": runs_g, "runs_bound": bound,
                    # False = the Runs column carries the "≤" label (§3.4-1)
                    "runs_exact": bool(g["cube_runs_exact"] and st["merge"]) or not g["cube_chars"],
                    "arating": arating, "mrating": mrating, "rn": rn}
    chars_all = site.stamp.count(*all_chars)
    return {"groups": out, "parses": parses, "runs": runs + cube_runs_total,
            "runs_exact": cube_runs_exact, "chars": chars_all,
            "dmin": dmin, "dmax": dmax, "runSeen": run_seen}


def _seq_sum(vals) -> float:
    s = 0
    for v in vals:
        s += v
    return s


# ---- aggregateElite C:2684–2757 ---------------------------------------------
def aggregate_elite(site: Site) -> dict:
    D, R, st = site.D, site.R, site.state
    m = base_masks(site)
    max_day = site.curMaxDay
    if max_day < 0:
        max_day = int(R["day"].max()) if site.N else -1
    day_lo = max_day - (ELITE_DAYS - 1)
    ok = (R["day"] >= day_lo) & ~proj_skip_mask(site)
    for col in ("cls", "spec", "dun", "role", "reg"):
        if m[col]:
            ok &= np.isin(R[col], list(m[col]))
    if not st["merge"] and m["hero"]:
        ok &= np.isin(R["hero"], list(m["hero"]))
    if m["atk"]:
        ok &= R["atk"] == m["atk"]
    idx = np.nonzero(ok)[0]
    gk = group_keys(site)[idx]
    keys = R["key"][idx]
    hist: dict = {}
    for j in range(len(idx)):
        k = int(gk[j])
        h = hist.get(k)
        if h is None:
            h = hist[k] = [np.zeros(40, dtype=np.int64), 0]
        h[0][min(int(keys[j]), 39)] += 1
        h[1] += 1
    floor: dict = {}
    for k, (h, n) in hist.items():
        if n < ELITE_MIN:
            continue
        cum = 0
        for lv in range(39, -1, -1):
            cum += int(h[lv])
            if cum >= ELITE_KEEP * n:
                floor[k] = lv
                break
    dv = dps_values(site, idx)
    raw = R["dps"][idx].tolist()
    dth = R["deaths"][idx].tolist()
    chs = R["char"][idx].tolist()
    runs_ = R["run"][idx].tolist()
    days = R["day"][idx].tolist()
    groups: dict = {}
    parses = runs = 0
    dmin, dmax = 10 ** 9, -1
    run_seen = np.zeros(site.runCount, dtype=np.uint8)
    all_chars: list = []
    for j in range(len(idx)):
        k = int(gk[j])
        fl = floor.get(k)
        if fl is None or keys[j] < fl:
            continue
        parses += 1
        if not run_seen[runs_[j]]:
            run_seen[runs_[j]] = 1
            runs += 1
        if days[j] >= 0:
            dmin, dmax = min(dmin, days[j]), max(dmax, days[j])
        g = groups.get(k)
        if g is None:
            i = int(idx[j])
            g = groups[k] = {"cls": int(R["cls"][i]), "spec": int(R["spec"][i]),
                             "hero": -1 if st["merge"] else int(R["hero"][i]),
                             "n": 0, "sum": 0, "dsum": 0, "dzero": 0, "dps": [],
                             "rsum": 0, "rdps": [], "chars": [], "runs": set(), "floorK": fl}
        v = dv[j]
        g["n"] += 1
        g["sum"] += v
        g["dsum"] += dth[j]
        if dth[j] == 0:
            g["dzero"] += 1
        g["dps"].append(v)
        g["rsum"] += raw[j]
        g["rdps"].append(raw[j])
        g["chars"].append(chs[j])
        all_chars.append(chs[j])
        g["runs"].add(runs_[j])
    site.eliteHidden = 0
    out: dict = {}
    for k, g in groups.items():
        if g["n"] < ELITE_MIN:
            site.eliteHidden += 1
            continue
        g["dps"].sort()
        med = qp(g["dps"], ELITE_PCTL)
        ravg = rmed = None
        if st["proj"]:
            g["rdps"].sort()
            ravg = g["rsum"] / g["n"]
            rmed = qp(g["rdps"], ELITE_PCTL)
        out[k] = {"cls": D["classes"][g["cls"]], "spec": D["specs"][g["spec"]],
                  "hero": "" if g["hero"] < 0 else D["heroes"][g["hero"]],
                  "n": g["n"], "runs": len(g["runs"]), "avg": g["sum"] / g["n"], "med": med,
                  "ravg": ravg, "rmed": rmed,
                  "q30": qp(g["dps"], .30), "q85": qp(g["dps"], .85), "qb": qp(g["dps"], st["pctlB"] / 100),
                  "adeaths": g["dsum"] / g["n"], "deathless": 100 * g["dzero"] / g["n"],
                  "chars": len(set(g["chars"])), "arating": NAN, "mrating": NAN, "rn": 0,
                  "floorK": g["floorK"]}
    return {"groups": out, "parses": parses, "runs": runs, "chars": len(set(all_chars)),
            "dmin": dmin, "dmax": dmax, "runSeen": run_seen, "runs_exact": True}


# ---- render() gate C:6192–6290 ----------------------------------------------
def skill_on(site: Site) -> bool:         # C:1625
    st = site.state
    return bool(st["skill"] and not st["compare"] and not st["elite"])


def render_gate(site: Site, A: dict, B: dict | None, precomputed_ref=None) -> dict:
    """The roster gate, ranking and chart order of render(): returns
    effMinA/effMinB, the gated rows (key, rank) and CHART_KEYS."""
    st = site.state
    tab = st["tab"] if st["tab"] in TABS else "med"
    better_up = TABS[tab]
    skill_tab = skill_on(site) and tab in ("med", "adeaths")
    eff_a = eff_min_for(site, A["chars"], precomputed_ref)
    eff_b = eff_min_for(site, B["chars"], precomputed_ref) if B else eff_a
    site.lastEffMin = eff_a
    rows = []
    for key, a0 in A["groups"].items():
        a, b = a0, None
        if st["compare"]:
            b = B["groups"].get(key) if B else None
        elif skill_tab:
            if tab == "med":
                b = dict(a0, med=a0["qb"])
            else:
                a = dict(a0, adeaths=a0["qdA"])
                b = dict(a0, adeaths=a0["qdB"])
        if not st["elite"] and a0["chars"] < eff_a:
            continue
        rows.append({"key": key, "cls": a0["cls"], "spec": a0["spec"], "hero": a0["hero"],
                     "a": a, "b": b})
    col = tab
    best_dir = 1 if better_up else -1
    ranked = js_sort(rows, lambda x, y: (y["a"][col] - x["a"][col]) * best_dir)
    for i, r in enumerate(ranked):
        r["rank"] = i + 1
    view = ranked[:CHART_MAX]

    def pct(r):
        b = r["b"]
        if b and abs(b[col]) > 1e-9:
            return (r["a"][col] - b[col]) / abs(b[col])
        return None
    if st["sort"] == "asc":
        view = js_sort(view, lambda x, y: (x["a"][col] - y["a"][col]) * best_dir)
    elif st["sort"] == "name":
        view = js_sort(view, lambda x, y: locale_cmp(x["cls"] + x["spec"] + x["hero"],
                                                    y["cls"] + y["spec"] + y["hero"]))
    elif st["sort"] == "gain":
        view = js_sort(view, lambda x, y: (pct(y) if pct(y) is not None else -1e9)
                       - (pct(x) if pct(x) is not None else -1e9))
    elif st["sort"] == "loss":
        view = js_sort(view, lambda x, y: (pct(x) if pct(x) is not None else 1e9)
                       - (pct(y) if pct(y) is not None else 1e9))
    return {"effMinA": eff_a, "effMinB": eff_b,
            "rows": [{"key": r["key"], "rank": r["rank"]} for r in ranked],
            "CHART_KEYS": [r["key"] for r in view]}


# ---- setBonusRows C:6742–6797 ------------------------------------------------
def set_bonus_rows(site: Site) -> list:
    if not site.hasTier:
        return []
    D, R, st = site.D, site.R, site.state
    m = base_masks(site)
    cut = period_cut(site, st["weeksA"])
    tier = R["tier"]
    tb = np.where(tier < 0, -1, np.where(tier < 2, 0, np.where(tier < 4, 1, 2)))
    ok = (tb >= 0) & period_mask(site, st["weeksA"], cut) & ~proj_skip_mask(site) \
        & row_mask(site, m, any_tier=True)
    idx = np.nonzero(ok)[0]
    gk = group_keys(site)[idx]
    dv = dps_values(site, idx)
    ck = (R["dun"][idx] * 100 + R["key"][idx]).tolist()
    tbs = tb[idx].tolist()
    G: dict = {}
    for j in range(len(idx)):
        k = int(gk[j])
        g = G.get(k)
        if g is None:
            i = int(idx[j])
            g = G[k] = {"cls": int(R["cls"][i]), "spec": int(R["spec"][i]),
                        "hero": -1 if st["merge"] else int(R["hero"][i]),
                        "n": [0, 0, 0], "cells": {}}
        b = tbs[j]
        g["n"][b] += 1
        c = g["cells"].get(ck[j])
        if c is None:
            c = g["cells"][ck[j]] = [[], [], []]
        c[b].append(dv[j])
    out = []
    pctl = st["pctl"] / 100
    for g in G.values():
        def gain(a, b, g=g):
            if g["n"][a] < SET_BUCKET_MIN or g["n"][b] < SET_BUCKET_MIN:
                return NAN, 0, 0
            wsum = acc = cells = pairs = 0
            for c in g["cells"].values():
                if len(c[a]) < SET_CELL_MIN or len(c[b]) < SET_CELL_MIN:
                    continue
                ma, mb = q50(sorted(c[a])), q50(sorted(c[b]))
                if not (ma > 0):
                    continue
                w = min(len(c[a]), len(c[b]))
                acc += w * (mb / ma - 1)
                wsum += w
                cells += 1
                pairs += w
            return (NAN, cells, pairs) if cells < SET_CELLS_MIN else (100 * acc / wsum, cells, pairs)

        def med(b, g=g):
            v = []
            for c in g["cells"].values():
                v.extend(c[b])
            return qp(sorted(v), pctl) if v else NAN
        tot = g["n"][0] + g["n"][1] + g["n"][2]
        g2, g4, gt = gain(0, 1), gain(1, 2), gain(0, 2)
        if not math.isfinite(g2[0]) and not math.isfinite(g4[0]) and not math.isfinite(gt[0]):
            continue
        out.append({"cls": D["classes"][g["cls"]], "spec": D["specs"][g["spec"]],
                    "hero": "" if g["hero"] < 0 else D["heroes"][g["hero"]],
                    "n0": g["n"][0], "n2": g["n"][1], "n4": g["n"][2], "tot": tot,
                    "s0": 100 * g["n"][0] / tot if tot else NAN,
                    "s2": 100 * g["n"][1] / tot if tot else NAN,
                    "s4": 100 * g["n"][2] / tot if tot else NAN,
                    "m0": med(0), "m2": med(1), "m4": med(2),
                    "p2": g2[0], "p4": g4[0], "pt": gt[0],
                    "cells": max(g2[1], g4[1], gt[1])})
    return out


# ---- renderPulse bags C:6549–6613 --------------------------------------------
def pulse(site: Site, gated_keys, presence: dict | None = None) -> dict:
    """The board's numbers: entries (key, dps, prevMed, thin, nNow, nPrev,
    adeaths, delta, isNew, spark, rn, rp), the window bounds and the
    'left the sample' list. `gated_keys` = the keys of render()'s rows."""
    D, R, st = site.D, site.R, site.state
    if st["elite"]:
        return {"entries": [], "elite": True}
    now_b = 0 if 0 in site.weekCounts else (site.availWeeks[0] if site.availWeeks else 0)
    prev_b = next((w for w in site.availWeeks if w > now_b), None)
    m = base_masks(site)
    ok = row_mask(site, m) & ~proj_skip_mask(site) & (site.rbucket < 999)
    idx = np.nonzero(ok)[0]
    gk = group_keys(site)[idx].tolist()
    ws = site.rbucket[idx].tolist()
    dv = dps_values(site, idx)
    dth = R["deaths"][idx].tolist()
    days = R["day"][idx].tolist()
    G: dict = {}
    wk_bag: dict = {}
    n_now = n_prev = 0
    n_min, n_max, p_min, p_max = 10 ** 9, -1, 10 ** 9, -1
    for j in range(len(idx)):
        key, w, v = gk[j], ws[j], dv[j]
        g = G.get(key)
        if g is None:
            i = int(idx[j])
            g = G[key] = {"cls": int(R["cls"][i]), "spec": int(R["spec"][i]),
                          "hero": -1 if st["merge"] else int(R["hero"][i]),
                          "now": [], "prev": [], "dsum": 0}
        wk_bag.setdefault(key * 1000 + w, []).append(v)
        if w == now_b:
            g["now"].append(v)
            g["dsum"] += dth[j]
            n_now += 1
            if days[j] >= 0:
                n_min, n_max = min(n_min, days[j]), max(n_max, days[j])
        elif w == prev_b:
            g["prev"].append(v)
            n_prev += 1
            if days[j] >= 0:
                p_min, p_max = min(p_min, days[j]), max(p_max, days[j])
    gate = set(gated_keys)
    pres_map = (presence or {}).get("map", {})
    entries = []
    pctl = st["pctl"] / 100
    weeks_desc = sorted(site.availWeeks, reverse=True)
    for key, g in G.items():
        if key not in gate or not g["now"]:
            continue
        g["now"].sort()
        dps = qp(g["now"], pctl)
        prev_med, thin = None, False
        if g["prev"]:
            g["prev"].sort()
            prev_med = qp(g["prev"], pctl)
            thin = len(g["prev"]) < PULSE_THIN
        spark = []
        for w in weeks_desc:
            bag = wk_bag.get(key * 1000 + w)
            if bag and len(bag) >= PULSE_THIN:
                bag.sort()
                spark.append(qp(bag, pctl))
        entries.append({"key": key, "cls": D["classes"][g["cls"]], "spec": D["specs"][g["spec"]],
                        "hero": "" if g["hero"] < 0 else D["heroes"][g["hero"]],
                        "dps": dps, "prevMed": prev_med, "thin": thin,
                        "nNow": len(g["now"]), "nPrev": len(g["prev"]),
                        "adeaths": g["dsum"] / len(g["now"]),
                        "delta": (100 * (dps - prev_med) / abs(prev_med)
                                  if prev_med is not None and abs(prev_med) > 1e-9 else None),
                        "isNew": prev_b is not None and not g["prev"],
                        "pres": pres_map.get(g["cls"] * 100 + g["spec"], 0),
                        "spark": spark, "rp": None})
    entries = js_sort(entries, lambda a, b: b["dps"] - a["dps"])
    for i, e in enumerate(entries):
        e["rn"] = i + 1
    withprev = js_sort([e for e in entries if e["prevMed"] is not None],
                       lambda a, b: b["prevMed"] - a["prevMed"])
    for i, e in enumerate(withprev):
        e["rp"] = i + 1
    left = [D["specs"][g["spec"]] + " " + D["classes"][g["cls"]]
            for g in G.values() if len(g["prev"]) >= PULSE_THIN and not g["now"]]
    return {"entries": entries, "nowB": now_b, "prevB": prev_b,
            "nNow": n_now, "nPrev": n_prev, "nMin": n_min, "nMax": n_max,
            "pMin": p_min, "pMax": p_max, "left": left}


# ---- renderComps scoring C:6872–6937 -----------------------------------------
def comps(site: Site, A: dict) -> dict:
    """Every distinct comp's row (rowsAll), the qualifying set, the presence
    map and the fit (slope, icpt, refKey, W)."""
    D, st = site.D, site.state
    RUNS, run_seen = site.RUNS, A["runSeen"]
    qual = []
    for r in range(site.runCount):
        if not run_seen[r]:
            continue
        o = RUNS[r]
        if o and o["pct"] is not None:
            qual.append(o)
    n = 0
    sx = sy = sxx = sxy = 0
    for o in qual:
        n += 1
        sx += o["key"]
        sy += o["pct"]
        sxx += o["key"] * o["key"]
        sxy += o["key"] * o["pct"]
    den = n * sxx - sx * sx
    slope = (n * sxy - sx * sy) / den if (n >= 10 and abs(den) > 1e-9) else 0
    icpt = (sy - slope * sx) / n if n else 0
    ref_key = sx / n if n else 0
    Wt = abs(slope)

    def score(o):
        return (o["pct"] - (icpt + slope * o["key"])) + Wt * (o["key"] - ref_key)
    by_comp: dict = {}
    for o in qual:
        e = by_comp.get(o["key_"])
        if e is None:
            e = by_comp[o["key_"]] = {"best": o, "scores": [], "pcts": [], "keys": [], "deaths": 0}
        e["scores"].append(score(o))
        e["pcts"].append(o["pct"])
        e["keys"].append(o["key"])
        e["deaths"] += o["deaths"]
        if score(o) > score(e["best"]):
            e["best"] = o
    pres_map: dict = {}
    dn = 0
    for e in by_comp.values():
        if len(e["scores"]) >= st["compMin"]:
            dn += 1
            for s in sorted({c[0] * 100 + c[1] for c in e["best"]["comp"]}):
                pres_map[s] = pres_map.get(s, 0) + 1
    rows_all = []
    for key_, e in by_comp.items():
        m_ = len(e["scores"])
        rows_all.append({"key_": key_, "strength": _seq_sum(e["scores"]) / (m_ + COMPS_K),
                         "best": max(e["pcts"]), "median": q50(sorted(e["pcts"])),
                         "avgkey": _seq_sum(e["keys"]) / m_, "n": m_,
                         "kdur": e["best"]["kdur"], "dun": D["dungeons"][e["best"]["dun"]],
                         "key": e["best"]["key"], "deaths": e["deaths"] / m_,
                         "day": e["best"]["day"],
                         "comp": [list(c) for c in e["best"]["comp"]]})
    rows = [r for r in rows_all if r["n"] >= st["compMin"]]
    rows = js_sort(rows, lambda a, b: b["strength"] - a["strength"])
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return {"rowsAll": rows_all, "rows": rows, "presence": {"den": dn, "map": pres_map},
            "slope": slope, "icpt": icpt, "refKey": ref_key, "W": Wt, "nQual": n}


def comps_from_cube(site: Site, weeks, cubes: dict, A_rows: dict | None = None) -> dict:
    """The same numbers from `comps` cube cells (§3.2/§3.4-2): qualification
    honours class/spec/role/melee-ranged (any member passes), region,
    dungeon, key, timed, post; cells of a dungeon with no par are skipped
    client-side. Row-served weeks are supplied through `A_rows` (the
    aggregate over the row-served part) and merged exactly as the client
    merges the two sources: both feed one regression."""
    D, st = site.D, site.state
    m = base_masks(site)
    pars = D.get("pars") or []
    ns = len(D["specs"])
    keys_all = [f"{a}|{s}" for a in D["classes"] for s in D["specs"]]
    atk_lut = [1 if k in MELEE else 2 if k in RANGED else 0 for k in keys_all]
    sr = D.get("spec_role")
    roles = D["roles"]
    role_code = [x if isinstance(x, (int, np.integer)) else (roles.index(x) if x in roles else -1)
                 for x in sr] if sr else None

    def member_pass(code):
        c, s = divmod(code, 100)
        if m["cls"] and c not in m["cls"]:
            return False
        if m["spec"] and s not in m["spec"]:
            return False
        if m["role"]:
            if role_code is None:
                raise ValueError("D.spec_role needed")
            if role_code[s] not in m["role"]:
                return False
        if m["atk"] and atk_lut[c * ns + s] != m["atk"]:
            return False
        return True
    # per (comp key_) accumulate sums; regression over all clocked cells
    n = 0
    sx = sy = sxx = sxy = 0
    cells = []
    for W in sorted(cubes):
        cube = cubes[W]
        if cube.comps is None:
            raise ValueError(f"week {W}: comps not resident -- withheld")
        c = cube.comps
        comp_list = c["comps"]
        b = np.array([site.curW[int(r)] for r in c["reg"]], dtype=np.int64) - cube.week
        for ci in range(len(c["n"])):
            if b[ci] < 3 or (weeks and int(b[ci]) not in weeks):
                continue
            if st["postTune"] and c["post"][ci] != 1:
                continue
            if st["timedOnly"] and c["timed"][ci] != 1:
                continue
            key, dun, reg = int(c["key"][ci]), int(c["dun"][ci]), int(c["reg"][ci])
            if key < st["klo"] or key > st["khi"]:
                continue
            if m["dun"] and dun not in m["dun"]:
                continue
            if m["reg"] and reg not in m["reg"]:
                continue
            comp = comp_list[int(c["comp"][ci])]
            if not any(member_pass(code) for code in comp):
                continue
            par = pars[dun] if dun < len(pars) else 0
            if not par:
                continue
            cn, ksum, kmin = int(c["n"][ci]), int(c["ksum"][ci]), int(c["kmin"][ci])
            spct = cn * 100 - 100 * ksum / par          # Σ (par-kdur)/par*100
            n += cn
            sx += cn * key
            sy += spct
            sxx += cn * key * key
            sxy += key * spct
            cells.append((",".join(str(x) for x in comp), key, dun, par, cn, ksum, kmin,
                          int(c["bday"][ci]), int(c["bdeaths"][ci]), int(c["dsum"][ci])))
    row_runs: list = []
    if A_rows is not None:
        # the row-served part of a mixed period: legacy's own qualification
        # (pct !== null, i.e. par && kdur) over the runs the row accumulator
        # saw; they feed the SAME regression and the same per-comp rows
        for o in (site.RUNS[r] for r in range(site.runCount) if A_rows["runSeen"][r]):
            if o and o["pct"] is not None:
                row_runs.append(o)
                n += 1
                sx += o["key"]
                sy += o["pct"]
                sxx += o["key"] * o["key"]
                sxy += o["key"] * o["pct"]
    den = n * sxx - sx * sx
    slope = (n * sxy - sx * sy) / den if (n >= 10 and abs(den) > 1e-9) else 0
    icpt = (sy - slope * sx) / n if n else 0
    ref_key = sx / n if n else 0
    Wt = abs(slope)
    by: dict = {}
    # per comp: `best` (the pct column) is max(pcts) over the comp's runs
    # (C:6925), which per cell is the min-kdur run's pct; the run identity
    # shown beside it (kdur/dun/key/day) is the max-SCORE run's (C:6900)
    for o in row_runs:
        e = by.setdefault(o["key_"], {"m": 0, "sscore": 0, "best": None, "bpct": -math.inf, "keys": 0, "deaths": 0})
        sc_ = (o["pct"] - (icpt + slope * o["key"])) + Wt * (o["key"] - ref_key)
        e["m"] += 1
        e["sscore"] += sc_
        e["keys"] += o["key"]
        e["deaths"] += o["deaths"]
        e["bpct"] = max(e["bpct"], o["pct"])
        if e["best"] is None or sc_ > e["best"][0]:
            e["best"] = (sc_, o["pct"], o["kdur"], o["dun"], o["key"], o["day"], o["deaths"])
    for key_, key, dun, par, cn, ksum, kmin, bday, bdeaths, dsum in cells:
        e = by.setdefault(key_, {"m": 0, "sscore": 0, "best": None, "bpct": -math.inf, "keys": 0, "deaths": 0})
        spct = cn * 100 - 100 * ksum / par
        e["m"] += cn
        e["sscore"] += spct - cn * icpt - slope * cn * key + Wt * (cn * key - cn * ref_key)
        e["keys"] += cn * key
        e["deaths"] += dsum
        bpct = (par - kmin) / par * 100
        e["bpct"] = max(e["bpct"], bpct)
        bscore = (bpct - (icpt + slope * key)) + Wt * (key - ref_key)
        if e["best"] is None or bscore > e["best"][0]:
            e["best"] = (bscore, bpct, kmin, dun, key, bday, bdeaths)
    rows_all = []
    for key_, e in by.items():
        _, _, kmin, dun, key, bday, _ = e["best"]
        rows_all.append({"key_": key_, "strength": e["sscore"] / (e["m"] + COMPS_K),
                         "best": e["bpct"], "median": None, "avgkey": e["keys"] / e["m"],
                         "n": e["m"], "kdur": kmin, "dun": D["dungeons"][dun], "key": key,
                         "deaths": e["deaths"] / e["m"], "day": bday})
    rows = [r for r in rows_all if r["n"] >= st["compMin"]]
    rows = js_sort(rows, lambda a, b: b["strength"] - a["strength"])
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return {"rowsAll": rows_all, "rows": rows, "slope": slope, "icpt": icpt,
            "refKey": ref_key, "W": Wt, "nQual": n}


# ---- renderTrend C:7057–7200 under the §3.3 rule -----------------------------
def trend(site: Site, cubes: dict | None = None, precomputed_ref=None) -> dict:
    """Bags, calc, gate, top-N, slope sort, daily fallback and the
    normalisations. With `cubes`, the gate (charsAll, per-group chars) and
    the ranking bag (`tot.vals`) are computed over the ROW WINDOW only and the
    per-bucket points of cube weeks come from cells/dist/chars (§3.3)."""
    D, R, st = site.D, site.R, site.state
    mt = st["trendMetric"] if st["trendMetric"] in TABS else "avg"
    better_up = TABS[mt]
    use_deaths = mt in ("adeaths", "deathless")
    use_chars = mt == "chars"
    pctl = st["pctl"] / 100

    def calc(vals):                       # C:7062
        n = len(vals)
        if not n:
            return NAN
        if mt == "chars":
            return len(set(vals))
        if mt in ("avg", "adeaths"):
            return _seq_sum(vals) / n
        if mt == "med":
            return qp(sorted(vals), pctl)
        if mt == "deathless":
            return 100 * sum(1 for v in vals if v == 0) / n
        return None                       # rating tabs: switch falls through
    m = base_masks(site)
    ok = row_mask(site, m) & ~proj_skip_mask(site) & (site.rbucket < 999) \
        & rows_served_mask(site, cubes)
    idx = np.nonzero(ok)[0]
    gk = group_keys(site)[idx].tolist()
    ws = site.rbucket[idx].tolist()
    chs = R["char"][idx].tolist()
    if use_chars:
        vals = chs
    elif use_deaths:
        vals = R["deaths"][idx].tolist()
    else:
        vals = dps_values(site, idx)
    days = R["day"][idx].tolist()
    tot: dict = {}
    by_week: dict = {}
    by_day: dict = {}
    w_tot: dict = {}
    d_tot: dict = {}
    weeks_seen: set = set()
    days_seen: set = set()
    chars_all: set = set()
    for j in range(len(idx)):
        key, w, v = gk[j], ws[j], vals[j]
        chars_all.add(chs[j])
        t = tot.get(key)
        if t is None:
            i = int(idx[j])
            t = tot[key] = {"cls": int(R["cls"][i]), "spec": int(R["spec"][i]),
                            "hero": -1 if st["merge"] else int(R["hero"][i]),
                            "n": 0, "vals": [], "chars": set()}
        t["n"] += 1
        t["vals"].append(v)
        t["chars"].add(chs[j])
        weeks_seen.add(w)
        w_tot[w] = w_tot.get(w, 0) + 1
        by_week.setdefault(key * 1000 + w, []).append(v)
        d0 = days[j]
        if d0 >= 0:
            days_seen.add(d0)
            d_tot[d0] = d_tot.get(d0, 0) + 1
            by_day.setdefault(key * 1000 + d0, []).append(v)
    # cube weeks: per-bucket bags only (gate/rank stay on the window)
    if cubes and not site.proj_on():
        for W in sorted(cubes):
            cube = cubes[W]
            cm = _cell_mask(site, cube, m, set())
            c = cube.cells
            for ci in np.nonzero(cm)[0]:
                ci = int(ci)
                w = int(site.curW[int(c["reg"][ci])] - cube.week)
                cs = int(c["cls"][ci]) * 100 + int(c["spec"][ci])
                key = cs if st["merge"] else cs * 200 + int(c["hero"][ci])
                if key not in tot:
                    tot[key] = {"cls": int(c["cls"][ci]), "spec": int(c["spec"][ci]),
                                "hero": -1 if st["merge"] else int(c["hero"][ci]),
                                "n": 0, "vals": [], "chars": set()}
                n_, doff = int(c["n"][ci]), int(c["doff"][ci])
                weeks_seen.add(w)
                w_tot[w] = w_tot.get(w, 0) + n_
                bag = by_week.setdefault(key * 1000 + w, [])
                if use_chars:
                    if cube.chars is None:
                        raise ValueError(f"week {W}: chars not resident -- withheld")
                    bag.extend(cube.chars[doff:doff + n_].tolist())
                elif use_deaths:
                    if cube.dist is None:
                        raise ValueError(f"week {W}: dist not resident -- withheld")
                    bag.extend(cube.dist["deaths"][doff:doff + n_].tolist())
                elif mt == "med":
                    if cube.dist is None:
                        raise ValueError(f"week {W}: dist not resident -- withheld")
                    bag.extend(cube.dist["dps"][doff:doff + n_].tolist())
                else:   # avg from cells: n copies are not needed -- a (sum, n) pair
                    bag.append(("cell", int(c["dsum"][ci]), n_))
    dir_ = -1 if better_up else 1
    trend_min = eff_min_for(site, len(chars_all), precomputed_ref)
    cand = [[key, t, calc(t["vals"])] for key, t in tot.items() if len(t["chars"]) >= trend_min]

    def bag_calc(g):
        """calc over a per-bucket bag that may carry cell pairs (avg only)."""
        if g and isinstance(g[0], tuple):
            s = _seq_sum(x[1] for x in g)
            n_ = sum(x[2] for x in g)
            return s / n_
        return calc(g)

    def bag_len(g):
        if g and isinstance(g[0], tuple):
            return sum(x[2] for x in g)
        return len(g)
    if st["trajSort"] == "slope":
        wks_asc = sorted([w for w in site.availWeeks if w in weeks_seen] +
                         [w for w in weeks_seen if w not in site.availWeeks], reverse=True)

        def slope_of(key):
            xs, ys = [], []
            for i2, w in enumerate(wks_asc):
                g = by_week.get(key * 1000 + w)
                if g and bag_len(g) >= 25:
                    xs.append(i2)
                    ys.append(bag_calc(g))
            if len(xs) < 2:
                return -math.inf
            nn = len(xs)
            sx, sy = _seq_sum(xs), _seq_sum(ys)
            sxx = sxy = 0
            for i2 in range(nn):
                sxx += xs[i2] * xs[i2]
                sxy += xs[i2] * ys[i2]
            dn = nn * sxx - sx * sx
            return -math.inf if abs(dn) < 1e-9 else ((nn * sxy - sx * sy) / dn) * (1 if better_up else -1)
        sm = {c[0]: slope_of(c[0]) for c in cand}
        cand = js_sort(cand, lambda a, b: _inf_sub(sm[b[0]], sm[a[0]]))
    else:
        cand = js_sort(cand, lambda a, b: (a[2] - b[2]) * dir_ if a[2] is not None and b[2] is not None else NAN)
    top = cand[:TREND_MAX]
    out = {"trendMin": trend_min, "eligible": [c[0] for c in cand], "top": [c[0] for c in top],
           "charsAll": len(chars_all), "series": [], "buckets": [], "daily": False}
    if not top:
        return out
    wks = sorted([w for w in site.availWeeks if w in weeks_seen] +
                 [w for w in weeks_seen if w not in site.availWeeks], reverse=True)
    daily = len(wks) <= 3 and len(days_seen) > 2
    buckets = sorted(days_seen) if daily else wks
    store = by_day if daily else by_week
    minp = 8 if daily else 25
    out["daily"], out["buckets"], out["MINP"] = daily, buckets, minp
    if not buckets:
        return out
    series = []
    for key, t, _ in top:
        pts = []
        for xi, b in enumerate(buckets):
            g = store.get(key * 1000 + b)
            if g and bag_len(g) >= minp:
                pts.append({"xi": xi, "b": b, "v": bag_calc(g), "n": bag_len(g)})
        if pts:
            series.append({"key": key, "cls": D["classes"][t["cls"]],
                           "name": D["specs"][t["spec"]] + " " + D["classes"][t["cls"]]
                           + (" — " + D["heroes"][t["hero"]] if t["hero"] >= 0 and not st["merge"] else ""),
                           "pts": pts})
    norm = st["trendNorm"]
    if norm == "rank":
        eligible = [k for k in tot if len(tot[k]["chars"]) >= trend_min]
        for b in buckets:
            lst = []
            for k in eligible:
                g = store.get(k * 1000 + b)
                if g and bag_len(g) >= minp:
                    lst.append([k, bag_calc(g)])
            lst = js_sort(lst, lambda x, y: (y[1] - x[1]) * (1 if better_up else -1))
            rk = {e[0]: i + 1 for i, e in enumerate(lst)}
            for s in series:
                for p in s["pts"]:
                    if p["b"] == b and s["key"] in rk:
                        p["v"] = rk[s["key"]]
    elif norm == "share":
        for s in series:
            for p in s["pts"]:
                T = (d_tot if daily else w_tot).get(p["b"]) or 1
                p["v"] = 100 * p["n"] / T
    ymin, ymax = 1e18, -1e18
    for s in series:
        for p in s["pts"]:
            ymin, ymax = min(ymin, p["v"]), max(ymax, p["v"])
    pad = (ymax - ymin) * 0.08 or 1
    ymin -= pad
    ymax += pad
    if norm == "dps":
        if (use_deaths or use_chars) and ymin < 0:
            ymin = 0
        if mt == "deathless" and ymax > 100:
            ymax = 100
    if norm == "rank" and ymin < 1:
        ymin = 1 - pad
    if norm == "share" and ymin < 0:
        ymin = 0
    out.update({"series": series, "ymin": ymin, "ymax": ymax})
    return out


def _inf_sub(a, b):
    """JS `a - b` with infinities: (-inf) - (-inf) is NaN -> comparator 0."""
    if a == b and math.isinf(a):
        return NAN
    return a - b


# ---- one-call convenience for the tests -------------------------------------
def render_all(site: Site, cubes: dict | None = None) -> dict:
    """What render() computes for the current state, in one dict: A, B,
    gate, comps, pulse, trend, setbonus."""
    st = site.state
    A = aggregate(site, st["weeksA"], cubes)
    B = aggregate(site, st["weeksB"], cubes) if st["compare"] else None
    gate = render_gate(site, A, B)
    cm = comps(site, A)
    pl = pulse(site, [r["key"] for r in gate["rows"]], cm["presence"])
    tr = trend(site, cubes)
    sb = set_bonus_rows(site)
    return {"A": A, "B": B, "gate": gate, "comps": cm, "pulse": pl, "trend": tr, "setbonus": sb}
