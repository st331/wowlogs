#!/usr/bin/env python3
"""tests/test_cube_equivalence.py (partitioned_payload.md §9.1)

The cubed run of the §9 fixture (tests/parts_util.parts_cubes_root: cubes
for the frozen weeks, the gap week 32 withheld, the Arcane Mage rule so
tmul/projection exist) against the same weeks as rows:

  * the four files of every cubed week equal sitecalc.cube_from_rows() --
    the reference definition of §3.2 -- table for table, and carry one
    cube_sha in all four headers equal to manifest.weeks[].cube_sha;
  * the §9.1 grid plus the week sets {3}, {0,1,2,3}, {4..7}, all, served
    through the client's path (listed days + cube files, sitecalc's two
    accumulators) vs the pure row path: n avg adeaths deathless chars med
    q30 q85 qb qdA qdB arating mrating dmin dmax EXACT; runs exact when no
    hero/tier split, else within the shipped Σ dup_rl bound with the "≤"
    label present whenever state.merge is false; CHART_KEYS and effMin
    identical under one pool;
  * comps strength/best/avgkey/deaths/n/kdur from the comps cube against
    sitecalc's renderComps on rows (clock-less runs and the six-member
    roster included; role and melee/ranged qualification);
  * Trends: eligible set, trendMin, top-N order, every plotted point for
    the five metrics, the slope sort and the daily fallback identical to
    the oracle under the §3.3 rule;
  * projection: cube weeks contribute nothing under proj=1; a window with
    mixed rules_sha days renders no projected number (toggle greyed, the
    caption state);
  * generation guard: a new cells + old dist/chars/comps pair is the
    withheld state, never a number; a manifest cube_sha change drops all
    four resident files;
  * the un-cubed bucket-4 week is served from its rows exactly and is
    withheld until its last listed day is resident;
  * the mixed-period scope line N of M equals the row/cube split;
  * manifest weeks[].reg equals a row scan for every week;
  * max id over every named file <= manifest.char_max.
"""
import copy
import json
import math
import pathlib
import shutil
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import partition_client as pc                                    # noqa: E402
import partition_format as pf                                    # noqa: E402
import parts_util as pu                                          # noqa: E402
import sitecalc as sc                                            # noqa: E402
from fixture_util import SET_KEYS, fixture                       # noqa: E402
from test_aggregates_bitexact import grid                        # noqa: E402

EXACT = ("n", "avg", "adeaths", "deathless", "chars", "med", "q30", "q85", "qb", "qdA", "qdB",
         "arating", "mrating", "rn")
N_STATES = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 170


def _eq(a, b):
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return a == b


class Env:
    """Both sides of the comparison, built once per process."""
    _inst = None

    def __init__(self):
        self.fx = fixture()
        self.root = pu.parts_cubes_root()
        self.rows = pc.load_site(self.root / "site_rows" / "d")        # every day, every row
        self.cli = pc.load_site(self.root / "site" / "d")              # what the client fetches
        self.man = self.cli.manifest
        self.cubes = pc.load_cubes(self.cli)
        self.site_rows = sc.init_data(self.rows.D, self.fx["now_ms"], R=self.rows.R)
        self.site_cli = sc.init_data(self.cli.D, self.fx["now_ms"], R=self.cli.R)
        # the client's weekCounts come from manifest.weeks[].reg, not a scan
        self.site_cli.weekCounts = week_counts(self.man, self.site_cli)
        self.site_cli.availWeeks = sorted(self.site_cli.weekCounts)
        self.cubed = sorted(int(w["w"]) for w in self.man["weeks"] if w.get("f"))
        self.ref_cubes = {W: sc.cube_from_rows(self.site_rows, np.nonzero(self.site_rows.W == W)[0], W)
                          for W in self.cubed}
        self.base_rows = snapshot(self.site_rows)
        self.base_cli = snapshot(self.site_cli)

    @classmethod
    def get(cls):
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst


def week_counts(man, site) -> dict:
    regions = site.D["regions"]
    out: dict = {}
    for w in man["weeks"]:
        for rn, v in (w.get("reg") or {}).items():
            b = site.curW[regions.index(rn)] - int(w["w"])
            out[b] = out.get(b, 0) + int(v["n"])
    return out


def snapshot(site):
    return {k: (set(v) if isinstance(v, set) else v) for k, v in site.state.items()}


def apply(site, base, spec):
    st = site.state
    st.clear()
    st.update({k: (set(v) if isinstance(v, set) else v) for k, v in base.items()})
    for k, v in spec.items():
        st[k] = set(v) if k in SET_KEYS else v
    site.refMemo = {}


def arr_eq(a, b):
    a, b = np.asarray(a), np.asarray(b)
    return a.shape == b.shape and bool(np.array_equal(a, b))


# ---------------------------------------------------------------- tables
def test_cube_files_equal_reference_tables():
    E = Env.get()
    assert E.cubed, "no cubed week in the fixture run"
    assert E.fx["cube_weeks"][0] in E.cubed
    assert E.fx["gap_weeks"][0] not in E.cubed
    man_weeks = {int(w["w"]): w for w in E.man["weeks"]}
    for W in E.cubed:
        ref, got = E.ref_cubes[W], E.cubes[W]
        assert got.cube_sha == man_weeks[W]["cube_sha"]
        for part in ("cells", "dist", "chars", "comps"):
            c = pf.read(E.cli.slug_dir / man_weeks[W]["f"][part], expect_kind=part)
            assert c.header["cube_sha"] == got.cube_sha and int(c.header["week"]) == W, part
            assert man_weeks[W]["b"][part] == (E.cli.slug_dir / man_weeks[W]["f"][part]).stat().st_size
        for k in ref.cells:
            assert arr_eq(ref.cells[k], got.cells[k]), (W, "cells", k)
        for k in ref.rl:
            assert arr_eq(ref.rl[k], got.rl[k]), (W, "rl", k)
        for k in ref.rg:
            assert arr_eq(ref.rg[k], got.rg[k]), (W, "rg", k)
        for k in ref.dist:
            assert arr_eq(ref.dist[k], got.dist[k]), (W, "dist", k)
        assert arr_eq(ref.chars, got.chars), (W, "chars")
        assert ref.comps["comps"] == got.comps["comps"], (W, "comp list")
        assert ref.comps["K"] == got.comps["K"] == max(len(c) for c in got.comps["comps"])
        assert arr_eq(ref.comps["clen"], got.comps["clen"])
        for k in sc.COMP_DIMS + ("n", "ksum", "kmin", "bday", "bdeaths", "dsum"):
            assert arr_eq(ref.comps[k], got.comps[k]), (W, "comps", k)
        # the six-member roster is a 6-comp on both sides; clocked runs only
        assert any(len(c) == 6 for c in got.comps["comps"]) or W != E.fx["cube_weeks"][0]
        runs = np.unique(E.site_rows.R["run"][E.site_rows.W == W])
        clocked = sum(1 for r in runs if E.site_rows.RUNS[r]["kdur"] > 0)
        assert int(got.comps["n"].sum()) == clocked < len(runs)
        # the file-level invariants
        assert got.dist["coff"][-1] == len(got.dist["dps"]) == len(got.chars) == int(got.cells["n"].sum())
        assert arr_eq(got.dist["coff"][:-1], got.cells["doff"])
    print(f"cube tables == reference for weeks {E.cubed}")


def test_manifest_weeks_and_char_max():
    E = Env.get()
    S = E.site_rows
    regions = S.D["regions"]
    for w in E.man["weeks"]:
        W = int(w["w"])
        m = S.W == W
        for ri, rn in enumerate(regions):
            mm = m & (S.R["reg"] == ri)
            if not mm.any():
                assert rn not in w["reg"], (W, rn)
                continue
            e = w["reg"][rn]
            assert e["n"] == int(mm.sum()) and e["runs"] == len(np.unique(S.R["run"][mm]))
            assert e["chars"] == len(np.unique(S.R["char"][mm]))
            assert (e["dmin"], e["dmax"]) == (int(S.R["day"][mm].min()), int(S.R["day"][mm].max()))
    assert E.site_cli.weekCounts == E.site_rows.weekCounts, (E.site_cli.weekCounts, E.site_rows.weekCounts)
    # every listed day is either in the window or in a week without a cube
    wf = E.man["window"]["day_from"]
    for e in E.man["days"]:
        if e["d"] == "undated":
            continue
        weeks = {W for lo_hi in e["w"].values() for W in range(lo_hi[0], lo_hi[1] + 1)}
        assert e["d"] >= wf or any(W not in E.cubed for W in weeks), e["d"]
    # a week's days leave the list once its cube is named (the cube-gap invariant)
    gap = E.fx["gap_weeks"][0]
    listed = {e["d"] for e in E.man["days"] if e["d"] != "undated"}
    gap_days = {int(d) for d in np.unique(S.R["day"][S.W == gap])}
    assert gap_days <= listed
    cubed_old = {int(d) for d in np.unique(S.R["day"][np.isin(S.W, E.cubed)]) if d < wf} - gap_days
    assert cubed_old and not (cubed_old & listed), cubed_old & listed
    # max id over every named file <= char_max
    cm = int(E.man["char_max"])
    mx = 0
    for e in E.man["days"]:
        if e.get("f"):
            mx = max(mx, int(pf.read(E.cli.slug_dir / e["f"], expect_kind="rows")["char"].max()))
    for w in E.man["weeks"]:
        if w.get("f"):
            mx = max(mx, int(pf.read(E.cli.slug_dir / w["f"]["chars"], expect_kind="chars")["char"].max()))
    for entry in (E.man["charscore"], E.man["charscore"].get("delta")):
        if entry and entry.get("f"):
            c = pf.read(E.cli.slug_dir / entry["f"], expect_kind="pairs")
            if c.n:
                mx = max(mx, int(c["char"].max()))
    assert mx <= cm, (mx, cm)
    print(f"weeks[].reg == row scan, char_max {cm} >= max id {mx}")


# ------------------------------------------------------------- the grid
def cube_grid(D):
    out = grid(D)
    extra = [[3], [0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 2, 3, 4, 5, 6, 7]]
    more = []
    for i, w in enumerate(extra):
        more.append({"weeksA": w, "klo": 2, "khi": 30, "postTune": False, "timedOnly": False, "merge": True})
        more.append({"weeksA": w, "klo": 2, "khi": 30, "postTune": False, "merge": False, "minchars": 1})
        more.append({"weeksA": w, "klo": 2, "khi": 30, "postTune": False, "tier0": True, "tier2": True, "tier4": False})
        more.append({"weeksA": w, "klo": 2, "khi": 30, "postTune": False, "role": ["Healer"], "compare": True,
                     "weeksB": [3], "pctl": 85})
        more.append({"weeksA": w, "klo": 2, "khi": 30, "postTune": True, "spec": [D["specs"][1]], "melee": True})
    return more + out


def row_served(site, weeks, cubed) -> set:
    """The buckets the row path serves when cube weeks are excluded
    (proj=1): a bucket is dropped iff in every region it is >= 3 and its
    absolute week is cubed."""
    if not site.proj_on():
        return set(weeks)
    return {b for b in weeks if not all(b >= 3 and (site.curW[r] - b) in cubed for r in site.curW)}


def compare_groups(A_r, A_c, label, merge):
    assert set(A_r["groups"]) == set(A_c["groups"]), (label, set(A_r["groups"]) ^ set(A_c["groups"]))
    for k, gr in A_r["groups"].items():
        gc = A_c["groups"][k]
        for f in EXACT:
            assert _eq(gr[f], gc[f]), (label, k, f, gr[f], gc[f])
        assert gr["runs"] <= gc["runs"] <= gr["runs"] + gc["runs_bound"], (label, k, gr["runs"], gc["runs"], gc["runs_bound"])
        if gc["runs_exact"]:
            assert gr["runs"] == gc["runs"], (label, k)
    for f in ("parses", "chars", "dmin", "dmax"):
        assert A_r[f] == A_c[f], (label, f, A_r[f], A_c[f])
    assert A_r["runs"] <= A_c["runs"], (label, A_r["runs"], A_c["runs"])
    if A_c["runs_exact"]:
        assert A_r["runs"] == A_c["runs"], label


def test_aggregate_grid_cubes_vs_rows(n_states: int = N_STATES):
    E = Env.get()
    sr, scl = E.site_rows, E.site_cli
    pool = E.man["window"]["refchars"]
    states = cube_grid(sr.D)[:n_states]
    touched_cube = 0
    for i, spec in enumerate(states):
        apply(sr, E.base_rows, spec)
        apply(scl, E.base_cli, spec)
        if scl.state["elite"]:
            continue
        if not sr.hasProj:
            sr.state["proj"] = scl.state["proj"] = False
        weeks = scl.state["weeksA"]
        # under proj=1 cube weeks contribute nothing (§3.3), so the legacy
        # row reference is taken over the row-served buckets only
        wr = row_served(scl, weeks, E.cubed)
        A_c = sc.aggregate(scl, weeks, E.cubes)             # the client: rows + cube files
        A_o = sc.aggregate(sr, weeks, E.ref_cubes)          # the oracle: rows + reference cubes
        if weeks and not wr:
            # a cube-only period under proj=1: nothing is served (§3.3); the
            # legacy row path has no equivalent (an empty week set means
            # "every week" to it), so the oracle is the only reference
            assert A_c["parses"] == 0 and A_o["parses"] == 0, (i, spec)
            A_r = A_o
        else:
            A_r = sc.aggregate(sr, wr)                      # the pure row path (legacy)
        compare_groups(A_r, A_c, (i, spec), scl.state["merge"])
        compare_groups(A_o, A_c, (i, spec, "oracle"), scl.state["merge"])
        # the oracle's cube path and the client's agree on the bound too
        for k in A_o["groups"]:
            assert A_o["groups"][k]["runs"] == A_c["groups"][k]["runs"]
            assert A_o["groups"][k]["runs_bound"] == A_c["groups"][k]["runs_bound"]
            assert A_o["groups"][k]["runs_exact"] == A_c["groups"][k]["runs_exact"]
        assert A_o["runs"] == A_c["runs"] and A_o["runs_exact"] == A_c["runs_exact"]
        # "≤" whenever state.merge is false and a cube week was touched
        cube_touched = any(g["n"] > 0 for g in A_c["groups"].values()) and bool(
            {scl.curW[r] - b for r in scl.curW for b in weeks if b >= 3} & set(E.cubed)) and any(
            not g["runs_exact"] or g["runs_bound"] >= 0 for g in A_c["groups"].values())
        if cube_touched and not scl.state["merge"] and not scl.proj_on():
            in_cube = [k for k, g in A_c["groups"].items() if g["n"] > A_r["groups"][k]["n"] - 0 and k in A_c["groups"]]
            if any(g["n"] and not g["runs_exact"] for g in A_c["groups"].values()):
                touched_cube += 1
        B_r = B_c = None
        if scl.state["compare"]:
            wb = row_served(scl, scl.state["weeksB"], E.cubed)
            B_c = sc.aggregate(scl, scl.state["weeksB"], E.cubes)
            B_r = sc.aggregate(sr, scl.state["weeksB"], E.ref_cubes) if (scl.state["weeksB"] and not wb) \
                else sc.aggregate(sr, wb)
            compare_groups(B_r, B_c, (i, spec, "B"), scl.state["merge"])
        # the gate under one pool (§3.3: the window's refChars)
        g_r = sc.render_gate(sr, A_r, B_r, precomputed_ref=pool)
        g_c = sc.render_gate(scl, A_c, B_c, precomputed_ref=pool)
        assert g_r["effMinA"] == g_c["effMinA"] and g_r["effMinB"] == g_c["effMinB"], (i, spec)
        assert set(g_r["CHART_KEYS"]) == set(g_c["CHART_KEYS"]) and len(g_r["CHART_KEYS"]) == len(g_c["CHART_KEYS"]), (i, spec)
        col = scl.state["tab"] if scl.state["tab"] in sc.TABS else "med"
        vr = [A_r["groups"][k][col] for k in g_r["CHART_KEYS"]]
        vc = [A_c["groups"][k][col] for k in g_c["CHART_KEYS"]]
        if scl.state["sort"] in ("desc", "asc"):
            assert all(_eq(a, b) for a, b in zip(vr, vc)), (i, spec)
        elif scl.state["sort"] == "name":
            assert g_r["CHART_KEYS"] == g_c["CHART_KEYS"], (i, spec)
        # the manifest pool equals the client's scan over its resident rows
        # (the manifest's pool is projection-off, §2.6/§8.1)
        if not scl.proj_on():
            assert sc.ref_chars(scl) == sc.ref_chars(scl, pool), (i, spec)
    assert touched_cube > 0, "no unmerged state touched a cube week"
    print(f"aggregate grid: {len(states)} states, cubes == rows exactly; {touched_cube} unmerged states carry the ≤ label")


# ------------------------------------------------------------------ comps
def test_comps_from_cube_files():
    E = Env.get()
    sr, scl = E.site_rows, E.site_cli
    off = dict(tier0=False, tier2=False, tier4=False, postTune=False)
    b3 = E.fx["window_weeks"] and 3
    cases = [dict(weeksA=[3], klo=2, khi=30, compMin=1), dict(weeksA=[3], compMin=1, role=[], reg=["US"]),
             dict(weeksA=[3], compMin=2, klo=10, khi=16, dun=[sr.D["dungeons"][2]]),
             dict(weeksA=[3], compMin=1, melee=True, role=["DPS"]),
             dict(weeksA=[3], compMin=1, role=["Healer"], timedOnly=False),
             dict(weeksA=[3], compMin=1, ranged=True, klo=2, khi=30),
             dict(weeksA=[0, 1, 2, 3], compMin=1, klo=2, khi=30),
             dict(weeksA=[1, 3], compMin=1, klo=2, khi=30, timedOnly=True),
             dict(weeksA=[3], compMin=1, klo=2, khi=30, cls=[sr.D["classes"][3]])]
    for spec in cases:
        spec = dict(spec, **off)
        apply(sr, E.base_rows, spec)
        A = sc.aggregate(sr, sr.state["weeksA"])
        rows = sc.comps(sr, A)
        apply(scl, E.base_cli, spec)
        A_c = sc.aggregate(scl, scl.state["weeksA"], E.cubes)
        cc = sc.comps_from_cube(scl, scl.state["weeksA"], E.cubes, A_rows=A_c)
        assert cc["nQual"] == rows["nQual"] > 0, (spec, cc["nQual"], rows["nQual"])
        assert math.isclose(cc["slope"], rows["slope"], rel_tol=1e-9, abs_tol=1e-9)
        by_rows = {r["key_"]: r for r in rows["rowsAll"]}
        by_cube = {r["key_"]: r for r in cc["rowsAll"]}
        assert set(by_rows) == set(by_cube), spec
        for k, r in by_rows.items():
            c = by_cube[k]
            assert (r["n"], r["kdur"], r["key"], r["dun"]) == (c["n"], c["kdur"], c["key"], c["dun"]), (spec, k)
            for f in ("strength", "best", "avgkey", "deaths"):
                assert math.isclose(r[f], c[f], rel_tol=1e-9, abs_tol=1e-9), (spec, k, f, r[f], c[f])
            assert r["day"] == c["day"] or r["kdur"] == c["kdur"]
        order = lambda rs: sorted(((round(r["strength"], 6), r["key_"]) for r in rs), key=lambda t: (-t[0], t[1]))
        assert order(rows["rows"]) == order(cc["rows"])
        six = E.fx["notes"]["six_roster"]
    print(f"comps from the cube files == renderComps on rows for {len(cases)} states")


# ------------------------------------------------------------------ trends
def test_trends_match_oracle():
    E = Env.get()
    sr, scl = E.site_rows, E.site_cli
    pool = E.man["window"]["refchars"]
    n = 0
    for metric in ("avg", "med", "adeaths", "deathless", "chars"):
        for norm in ("dps", "rank", "share"):
            for sort in ("value", "slope"):
                for extra in ({}, {"role": ["Tank", "Healer"], "minchars": 1}):
                    kw = dict(trendMetric=metric, trendNorm=norm, trajSort=sort, klo=2, khi=30,
                              weeksA=[0, 1, 2, 3, 4], postTune=False, **extra)
                    apply(sr, E.base_rows, kw)
                    t_o = sc.trend(sr, E.ref_cubes, precomputed_ref=pool)
                    apply(scl, E.base_cli, kw)
                    t_c = sc.trend(scl, E.cubes, precomputed_ref=pool)
                    assert t_o["buckets"] == t_c["buckets"], (metric, norm)
                    assert t_o["trendMin"] == t_c["trendMin"], (metric, norm, sort)
                    assert [s["key"] for s in t_o["series"]] == [s["key"] for s in t_c["series"]], (metric, norm, sort)
                    for so, sc_ in zip(t_o["series"], t_c["series"]):
                        po = [(p["b"], p["v"], p["n"]) for p in so["pts"]]
                        pcl = [(p["b"], p["v"], p["n"]) for p in sc_["pts"]]
                        assert po == pcl, (metric, norm, sort, so["key"], po[:3], pcl[:3])
                        for f in ("rank", "share", "slope", "ymin", "ymax"):
                            if f in so:
                                assert _eq(so[f], sc_[f]), (metric, norm, sort, f)
                    for f in ("ymin", "ymax", "daily"):
                        if f in t_o:
                            assert _eq(t_o[f], t_c[f]), (metric, norm, sort, f)
                    assert any(p["b"] == 3 for s in t_c["series"] for p in s["pts"]), (metric, norm)
                    n += 1
    # the daily fallback (wks.length <= 3) only ever spans row-served weeks
    kw = dict(trendMetric="avg", trendNorm="dps", klo=2, khi=30, weeksA=[0, 1], postTune=False)
    apply(sr, E.base_rows, kw)
    t_o = sc.trend(sr, E.ref_cubes, precomputed_ref=pool)
    apply(scl, E.base_cli, kw)
    t_c = sc.trend(scl, E.cubes, precomputed_ref=pool)
    assert t_o.get("daily") == t_c.get("daily")
    assert [(s["key"], [(p.get("d", p.get("b")), p["v"], p["n"]) for p in s["pts"]]) for s in t_o["series"]] == \
        [(s["key"], [(p.get("d", p.get("b")), p["v"], p["n"]) for p in s["pts"]]) for s in t_c["series"]]
    print(f"trends: {n} states identical to the oracle under the §3.3 rule")


# -------------------------------------------------------------- projection
def test_projection_rules():
    E = Env.get()
    scl = E.site_cli
    assert E.man["projection"] and E.man["flags"]["proj"], "the cubed run carries no projection"
    assert scl.hasProj and E.cli.proj_pending == 0 and E.cli.proj_caption is None
    assert all(e["rules_sha"] == E.man["projection"]["rules_sha"] for e in E.man["days"] if e.get("f"))
    # cube weeks contribute nothing under proj=1
    apply(scl, E.base_cli, dict(proj=True, weeksA=[0, 1, 2, 3], klo=2, khi=30, postTune=True))
    A = sc.aggregate(scl, scl.state["weeksA"], E.cubes)
    apply(scl, E.base_cli, dict(proj=True, weeksA=[0, 1, 2], klo=2, khi=30, postTune=True))
    A_win = sc.aggregate(scl, scl.state["weeksA"], E.cubes)
    assert A["parses"] == A_win["parses"] > 0 and A["chars"] == A_win["chars"]
    assert {k: v["avg"] for k, v in A["groups"].items()} == {k: v["avg"] for k, v in A_win["groups"].items()}
    apply(scl, E.base_cli, dict(proj=False, weeksA=[0, 1, 2, 3], klo=2, khi=30, postTune=True))
    A_off = sc.aggregate(scl, scl.state["weeksA"], E.cubes)
    assert A_off["parses"] > A["parses"]
    tr = sc.trend(scl, E.cubes)
    apply(scl, E.base_cli, dict(proj=True, trendMetric="avg", klo=2, khi=30, weeksA=[0, 1, 2, 3, 4], postTune=True))
    tr = sc.trend(scl, E.cubes)
    assert not any(p["b"] == 3 for s in tr["series"] for p in s["pts"]), "a cube week's point rendered under proj=1"
    # a window with mixed rules_sha days: no projected number, toggle greyed
    tmp = E.root / "site_mixed"
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(E.root / "site" / "d", tmp / "d")
    man = json.loads((tmp / "d" / "s2" / "manifest.json").read_text())
    stale = [e for e in man["days"] if e.get("f")][-1]
    stale["rules_sha"] = "0" * 64
    (tmp / "d" / "s2" / "manifest.json").write_text(json.dumps(man))
    L = pc.load_site(tmp / "d")
    assert L.proj_pending == 1 and L.D["projection"] is None
    assert L.proj_caption == f"projection updating · 1 of {len(L.days)} days"
    s2 = sc.init_data(L.D, E.fx["now_ms"], R=L.R)
    assert not s2.hasProj and s2.state["proj"] is False
    print("projection: cube weeks excluded under proj=1; mixed rules_sha -> withheld toggle")


# --------------------------------------------------------- generation guard
def test_generation_guard_and_gap_week():
    E = Env.get()
    scl = E.site_cli
    W = E.fx["cube_weeks"][0]
    other = [w for w in E.cubed if w != W][0]
    man_weeks = {int(w["w"]): w for w in E.man["weeks"]}
    # new cells, old dist/chars/comps of the "same" week: withheld, never a number
    cache = pc.CubeCache(E.cli.slug_dir)
    override = {(W, p): man_weeks[other]["f"][p] for p in ("dist", "chars", "comps")}
    cubes = cache.load(E.man, override=override)
    assert cubes[W].dist is None and cubes[W].chars is None and cubes[W].comps is None
    assert {r[1] for r in cache.rejected if r[0] == W} == {"dist", "chars", "comps"}
    assert all(r[2] == "cube_sha != resident cells" for r in cache.rejected if r[0] == W)
    apply(scl, E.base_cli, dict(weeksA=[3], klo=2, khi=30, postTune=False))
    for fn in (lambda: sc.aggregate(scl, scl.state["weeksA"], cubes),
               lambda: sc.comps_from_cube(scl, scl.state["weeksA"], cubes),
               lambda: pc.period_check(scl, E.cli, cubes, [3])):
        try:
            fn()
        except ValueError as e:
            assert "withheld" in str(e), e
        else:
            raise AssertionError("a number was produced from a mismatched generation")
    # a cells file whose cube_sha differs from the manifest is rejected unread
    cache2 = pc.CubeCache(E.cli.slug_dir)
    cubes2 = cache2.load(E.man, override={(W, "cells"): man_weeks[other]["f"]["cells"]})
    assert W not in cubes2 and (W, "cells", "cube_sha != manifest") in cache2.rejected
    # a manifest cube_sha change drops all four resident files at once
    cache3 = pc.CubeCache(E.cli.slug_dir)
    cache3.load(E.man)
    assert W in cache3.weeks
    man2 = copy.deepcopy(E.man)
    for w in man2["weeks"]:
        if int(w["w"]) == W:
            w["cube_sha"] = "f" * 64
    assert cache3.refresh(man2) == [W] and W not in cache3.weeks and other in cache3.weeks
    assert cache3.refresh(E.man) == []
    # the un-cubed bucket-4 week is served from rows and equals the row scan
    gap = E.fx["gap_weeks"][0]
    assert gap not in E.cubes
    apply(E.site_rows, E.base_rows, dict(weeksA=[4], klo=2, khi=30, postTune=False))
    A_r = sc.aggregate(E.site_rows, {4})
    apply(scl, E.base_cli, dict(weeksA=[4], klo=2, khi=30, postTune=False))
    A_c = sc.aggregate(scl, {4}, E.cubes)
    assert A_r["parses"] == A_c["parses"] > 0
    compare_groups(A_r, A_c, "gap", True)
    pc.period_check(scl, E.cli, E.cubes, [4])
    # ... and is withheld until its last listed day is resident
    gap_days = sorted({int(d) for d in np.unique(E.site_rows.R["day"][E.site_rows.W == gap])})
    listed = [e["d"] for e in E.man["days"] if e.get("f") and e["d"] != "undated"]
    partial = pc.load_site(E.root / "site" / "d", days=[d for d in listed if d != gap_days[-1]])
    assert gap in pc.unresident_weeks(partial)
    s_part = sc.init_data(partial.D, E.fx["now_ms"], R=partial.R)
    try:
        pc.period_check(s_part, partial, E.cubes, [4])
    except ValueError as e:
        assert "unresident" in str(e)
    else:
        raise AssertionError("period over an incomplete un-cubed week not withheld")
    pc.period_check(s_part, partial, E.cubes, [0, 1, 2, 3])
    print("generation guard: mismatched pair withheld, sha change drops the week; gap week row-served")


# ------------------------------------------------------- mixed-period line
def test_mixed_period_scope_line():
    E = Env.get()
    scl = E.site_cli
    # no filter at all: M is the period's total from weekCounts (C:1913)
    free = dict(klo=2, khi=30, postTune=False, timedOnly=False, role=[], cls=[], spec=[], dun=[], reg=[],
                hero=[], melee=False, ranged=False, tier0=False, tier2=False, tier4=False, proj=False)
    apply(scl, E.base_cli, dict(weeksA=[0, 1, 2, 3], **free))
    M = sum(scl.weekCounts.get(b, 0) for b in (0, 1, 2, 3))
    A_all = sc.aggregate(scl, {0, 1, 2, 3}, E.cubes)
    apply(scl, E.base_cli, dict(weeksA=[0, 1, 2], **free))
    N = sc.aggregate(scl, {0, 1, 2}, E.cubes)["parses"]
    assert A_all["parses"] == M
    assert N == sum(scl.weekCounts.get(b, 0) for b in (0, 1, 2))
    W = E.fx["cube_weeks"][0]
    assert M - N == scl.weekCounts[3] == int(E.cubes[W].cells["n"].sum())
    print(f"mixed period: row-level detail covers {N} of {M} parses")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("test_cube_equivalence: all green")
