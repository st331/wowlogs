#!/usr/bin/env python3
"""tests/test_sitecalc_cube.py -- the oracle's cube accumulator agrees with
its row accumulator (partitioned_payload.md §3.2–§3.4).

`sitecalc.cube_from_rows()` is the reference definition of a frozen week's
four tables; `sitecalc.aggregate(..., cubes=)` is the client's two-accumulator
aggregate. On the §9 fixture, week 33 (bucket 3) served from its cube must
equal the same week served from rows: n avg adeaths deathless chars med q30
q85 qb qdA qdB arating mrating dmin dmax EXACT; runs exact when neither a hero
split nor a tier box splits the run-level cell, else within Σ dup_rl; Trends'
per-bucket points exact for all five metrics; comps strength/best/avgkey/
deaths/n/kdur from the comps cube against renderComps' scoring; the §3.3
projection rule (cube weeks contribute nothing under proj=1); the serving
rule (a week whose cube is withheld is row-served); the file invariants
(dist sorted within cells, coff/doff/chars alignment).
"""
import gzip
import json
import math
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import sitecalc as sc                                            # noqa: E402
from fixture_util import FIXTURE_DIR, SET_KEYS, fixture           # noqa: E402

EXACT = ("n", "avg", "adeaths", "deathless", "chars", "med", "q30", "q85", "qb", "qdA", "qdB",
         "arating", "mrating", "rn")


def _eq(a, b):
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return a == b


def _site():
    fx = fixture()
    payload = json.load(gzip.open(FIXTURE_DIR / "payload.json.gz", "rt"))
    payload["spec_role"] = json.loads((ROOT / "data" / "season.json").read_text())["spec_role"]
    # the fixture's spec vocabulary is the season's minus what never occurred
    season = json.loads((ROOT / "data" / "season.json").read_text())
    sr = dict(zip(season["vocab"]["specs"], season["spec_role"]))
    payload["spec_role"] = [sr[s] for s in payload["specs"]]
    site = sc.init_data(payload, fx["now_ms"])
    return fx, site


def _set(site, base, **spec):
    site.state.clear()
    site.state.update({k: (set(v) if isinstance(v, set) else v) for k, v in base.items()})
    for k, v in spec.items():
        site.state[k] = set(v) if k in SET_KEYS else v
    site.refMemo = {}


def test_cube_tables_invariants():
    fx, site = _site()
    idx = np.nonzero(site.W == 33)[0]
    assert len(idx) > 1000
    cube = sc.cube_from_rows(site, idx, 33)
    c = cube.cells
    n_cells = cube.n_cells
    assert len(cube.dist["coff"]) == n_cells + 1
    assert cube.dist["coff"][-1] == len(idx) == len(cube.dist["dps"]) == len(cube.chars)
    assert np.array_equal(cube.dist["coff"][:-1], c["doff"])
    assert np.array_equal(np.diff(cube.dist["coff"]), c["n"])
    # cells sorted lexicographically by the dims, dps ascending inside
    keys = list(zip(*[c[d] for d in sc.CELL_DIMS]))
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    for a, b in zip(cube.dist["coff"][:-1], cube.dist["coff"][1:]):
        sl = cube.dist["dps"][a:b]
        assert np.all(sl[1:] >= sl[:-1])
    assert int(c["n"].sum()) == len(idx)
    assert int(c["dsum"].sum()) == int(site.R["dps"][idx].sum())
    # every row's char is in the chars file exactly as often as it occurs
    assert np.array_equal(np.sort(cube.chars), np.sort(site.R["char"][idx]))
    # rl/rg: distinct runs
    assert int(cube.rg["nrun"].sum()) == len(np.unique(site.R["run"][idx]))
    assert cube.comps["K"] == 6          # the six-member roster is a 6-comp
    assert any(len(cp) == 6 for cp in cube.comps["comps"])
    # comps hold clocked runs only
    runs = np.unique(site.R["run"][idx])
    clocked = sum(1 for r in runs if site.RUNS[r]["kdur"] > 0)
    assert int(cube.comps["n"].sum()) == clocked < len(runs)


def test_cube_week_equals_row_week():
    fx, site = _site()
    base = {k: (set(v) if isinstance(v, set) else v) for k, v in site.state.items()}
    cube = sc.cube_from_rows(site, np.nonzero(site.W == 33)[0], 33)
    cubes = {33: cube}
    D = site.D
    # the fixture's tuning cutoff is in week b1: week b3 is pre-tuning, so the
    # cases over it alone switch the post-tuning default off (a mixed period
    # keeps it, and then the cube week contributes nothing on either side)
    pre = dict(postTune=False)
    cases = [
        dict(weeksA=[3], **pre), dict(weeksA=[0, 1, 2, 3]), dict(weeksA=[0, 1, 2, 3], **pre),
        dict(weeksA=[1, 3], **pre), dict(weeksA=[3, 4], **pre),
        dict(weeksA=[3], merge=False, **pre), dict(weeksA=[3], tier0=True, tier2=True, tier4=False, **pre),
        dict(weeksA=[3], role=[], reg=["EU"], **pre), dict(weeksA=[3], role=["Tank", "Healer"], melee=True, **pre),
        dict(weeksA=[3], cls=[D["classes"][5]], spec=[D["specs"][1]], **pre),
        dict(weeksA=[3], postTune=False, timedOnly=False, klo=2, khi=30),
        dict(weeksA=[0, 1, 2, 3], pctl=85, pctlB=30, dun=[D["dungeons"][0], D["dungeons"][3]], **pre),
        dict(weeksA=[3], ranged=True, tier4=False, **pre),
        dict(weeksA=[], klo=2, khi=30, **pre),
    ]
    for spec in cases:
        _set(site, base, **spec)
        hero_split = not site.state["merge"]
        tier_split = site.state["tier0"] or site.state["tier2"] or site.state["tier4"]
        A_rows = sc.aggregate(site, site.state["weeksA"])
        _set(site, base, **spec)
        A_cube = sc.aggregate(site, site.state["weeksA"], cubes)
        assert set(A_rows["groups"]) == set(A_cube["groups"]), spec
        if not site.state["postTune"]:
            assert A_rows["groups"], spec
        for k, gr in A_rows["groups"].items():
            gc = A_cube["groups"][k]
            for f in EXACT:
                assert _eq(gr[f], gc[f]), (spec, k, f, gr[f], gc[f])
            if not hero_split and not tier_split:
                assert gr["runs"] == gc["runs"] and gc["runs_bound"] == 0, (spec, k)
            else:
                assert gr["runs"] <= gc["runs"] <= gr["runs"] + gc["runs_bound"], (spec, k, gr["runs"], gc["runs"], gc["runs_bound"])
        for f in ("parses", "chars", "dmin", "dmax"):
            assert A_rows[f] == A_cube[f], (spec, f)
        roster_filter = bool(site.state["cls"] or site.state["spec"] or site.state["role"]
                             or site.state["melee"] != site.state["ranged"]
                             or (site.state["hero"] and not site.state["merge"]))
        if not roster_filter and not tier_split:
            assert A_rows["runs"] == A_cube["runs"] and A_cube["runs_exact"], spec
        else:
            assert A_rows["runs"] <= A_cube["runs"], spec
        # the gate and the chart order follow
        gr = sc.render_gate(site, A_rows, None)
        gc = sc.render_gate(site, A_cube, None)
        assert gr["effMinA"] == gc["effMinA"]
        assert gr["CHART_KEYS"] == gc["CHART_KEYS"], spec


def test_trend_points_from_cube():
    fx, site = _site()
    base = {k: (set(v) if isinstance(v, set) else v) for k, v in site.state.items()}
    cubes = {33: sc.cube_from_rows(site, np.nonzero(site.W == 33)[0], 33)}
    for metric in ("avg", "med", "adeaths", "deathless", "chars"):
        for norm in ("dps", "rank", "share"):
            kw = dict(trendMetric=metric, trendNorm=norm, klo=2, khi=30, weeksA=[0, 1, 2, 3, 4],
                      postTune=False)
            _set(site, base, **kw)
            t_rows = sc.trend(site)
            _set(site, base, **kw)
            t_cube = sc.trend(site, cubes)
            # the window rule: gate and rank over rows of buckets 0-2 only
            assert t_cube["buckets"] == t_rows["buckets"] == [4, 3, 2, 1, 0]
            pts_rows = {(s["key"], p["b"]): (p["v"], p["n"]) for s in t_rows["series"] for p in s["pts"]}
            pts_cube = {(s["key"], p["b"]): (p["v"], p["n"]) for s in t_cube["series"] for p in s["pts"]}
            shared = set(pts_rows) & set(pts_cube)
            assert any(b == 3 for _, b in shared), (metric, norm)
            for k in shared:
                if norm == "rank":
                    continue        # ranks depend on the eligible set, which the §3.3 rule changes
                assert pts_rows[k] == pts_cube[k], (metric, norm, k, pts_rows[k], pts_cube[k])


def test_projection_excludes_cube_weeks_and_gap_is_row_served():
    fx, site = _site()
    base = {k: (set(v) if isinstance(v, set) else v) for k, v in site.state.items()}
    cubes = {33: sc.cube_from_rows(site, np.nonzero(site.W == 33)[0], 33)}
    _set(site, base, proj=True, postTune=True, weeksA=[0, 1, 2, 3], klo=2, khi=30)
    A = sc.aggregate(site, site.state["weeksA"], cubes)
    _set(site, base, proj=True, postTune=True, weeksA=[0, 1, 2], klo=2, khi=30)
    A_win = sc.aggregate(site, site.state["weeksA"], cubes)
    assert A["parses"] == A_win["parses"] and A["chars"] == A_win["chars"]
    # week 32 has no cube: bucket 4 is served from rows exactly as legacy
    _set(site, base, weeksA=[4], klo=2, khi=30, postTune=False)
    A_gap = sc.aggregate(site, site.state["weeksA"], cubes)
    _set(site, base, weeksA=[4], klo=2, khi=30, postTune=False)
    A_leg = sc.aggregate(site, site.state["weeksA"])
    assert A_gap["parses"] == A_leg["parses"] > 0
    assert {k: v["med"] for k, v in A_gap["groups"].items()} == {k: v["med"] for k, v in A_leg["groups"].items()}
    # a missing dist is the withheld state, never a number
    partial = {33: sc.CubeWeek(33, cubes[33].cells, cubes[33].rl, cubes[33].rg, dist=None, chars=None)}
    _set(site, base, weeksA=[3], klo=2, khi=30, postTune=False)
    try:
        sc.aggregate(site, site.state["weeksA"], partial)
    except ValueError as e:
        assert "withheld" in str(e)
    else:
        raise AssertionError("aggregate produced a number without dist/chars")


def test_comps_from_cube():
    fx, site = _site()
    base = {k: (set(v) if isinstance(v, set) else v) for k, v in site.state.items()}
    cube = sc.cube_from_rows(site, np.nonzero(site.W == 33)[0], 33)
    # tier boxes are not in the comp key (§3.4-2: hero and tier filters are
    # ignored by the cube's qualification), so they are off here; the
    # fixture's tuning cutoff is in week b1, so week b3 is pre-tuning and the
    # post-tuning default would empty both sides
    off = dict(tier0=False, tier2=False, tier4=False, postTune=False)
    for spec in (dict(weeksA=[3], klo=2, khi=30, compMin=1), dict(weeksA=[3], compMin=1, role=[], reg=["US"]),
                 dict(weeksA=[3], compMin=2, klo=10, khi=16, dun=[site.D["dungeons"][2]]),
                 dict(weeksA=[3], compMin=1, melee=True, role=["DPS"]),
                 dict(weeksA=[3], compMin=1, role=["Healer"], postTune=False, timedOnly=False)):
        spec = dict(spec, **off)
        _set(site, base, **spec)
        A = sc.aggregate(site, site.state["weeksA"])
        rows = sc.comps(site, A)
        _set(site, base, **spec)
        cc = sc.comps_from_cube(site, site.state["weeksA"], {33: cube})
        assert cc["nQual"] == rows["nQual"] > 0, spec
        assert math.isclose(cc["slope"], rows["slope"], rel_tol=1e-9, abs_tol=1e-9)
        by_rows = {r["key_"]: r for r in rows["rowsAll"]}
        by_cube = {r["key_"]: r for r in cc["rowsAll"]}
        assert set(by_rows) == set(by_cube), spec
        for k, r in by_rows.items():
            c = by_cube[k]
            assert (r["n"], r["kdur"], r["key"], r["dun"]) == (c["n"], c["kdur"], c["key"], c["dun"]), (spec, k)
            for f in ("strength", "best", "avgkey", "deaths"):
                assert math.isclose(r[f], c[f], rel_tol=1e-9, abs_tol=1e-9), (spec, k, f, r[f], c[f])
            # bday equals except on an exact-kdur tie (§3.2)
            assert r["day"] == c["day"] or r["kdur"] == c["kdur"]
        # the ranking agrees up to strengths equal to 1e-9 (the cube sums
        # are affine rearrangements of the row sums: same value, last-bit
        # float noise, so exact ties can swap)
        order = lambda rs: sorted(((round(r["strength"], 6), r["key_"]) for r in rs), key=lambda t: (-t[0], t[1]))
        assert order(rows["rows"]) == order(cc["rows"])
        assert {r["key_"] for r in rows["rows"]} == {r["key_"] for r in cc["rows"]}


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("test_sitecalc_cube: all green")
