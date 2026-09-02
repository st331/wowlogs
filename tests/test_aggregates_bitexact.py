#!/usr/bin/env python3
"""tests/test_aggregates_bitexact.py (partitioned_payload.md §9.1)

`sitecalc.aggregate` over a grid (specs x merged/unmerged x timed x key
bands x dungeons x US/EU/any x tier boxes x p in {30,50,85} x week sets
{0},{1},{0,1},{0,1,2}, compare on/off, post on/off, proj on/off) on the
LEGACY rows (real build() with the pins) vs the concatenated window rows
of the day files: identical n runs chars avg med q30 q85 qb qdA qdB
adeaths deathless arating mrating, weekCounts, per-run comp key_ pct
deaths, RUNS count, set-bonus cells, elite floors, KPI dates, CHART_KEYS
(the gated set) and effMin.

Group keys are numeric codes into each side's own vocabulary, so groups
are matched by (class, spec, hero) NAME; runs by their (report, fight) key.
"""
import math
import pathlib
import random
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import partition_client as pc                                    # noqa: E402
import parts_util as pu                                          # noqa: E402
import sitecalc as sc                                            # noqa: E402
from fixture_util import SET_KEYS, fixture                       # noqa: E402
from test_rows_bitexact_vs_legacy import parts_keys              # noqa: E402

FIELDS = ("n", "runs", "chars", "avg", "med", "q30", "q85", "qb", "qdA", "qdB", "adeaths",
          "deathless", "arating", "mrating", "rn")
N_STATES = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 160


def _eq(a, b):
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return a == b


def grid(D: dict, seed: int = 11) -> list:
    """The §9.1 grid, walked rather than crossed: every spec appears, every
    other dimension rotates through its values, plus the unfiltered cases."""
    rnd = random.Random(seed)
    pairs = sorted({(c, s) for c, s in zip(D["classes"], D["specs"])})
    specs = sorted(set(D["specs"]))
    bands = [(2, 30), (10, 14), (12, 16), (16, 22)]
    duns = [None] + list(D["dungeons"])
    regs = [None, ["US"], ["EU"]]
    tiers = [(False, False, True), (True, False, False), (True, True, True), (False, False, False)]
    pcts = [30, 50, 85]
    weeks = [[0], [1], [0, 1], [0, 1, 2], [0, 1, 2, 3], [3], [4], [0, 1, 2, 3, 4]]
    out = []
    for i, sp in enumerate(specs):
        st = {"spec": [sp], "merge": i % 2 == 0, "timedOnly": i % 3 != 0}
        st["klo"], st["khi"] = bands[i % len(bands)]
        d = duns[i % len(duns)]
        if d:
            st["dun"] = [d]
        r = regs[i % len(regs)]
        if r:
            st["reg"] = r
        st["tier0"], st["tier2"], st["tier4"] = tiers[i % len(tiers)]
        st["pctl"] = pcts[i % len(pcts)]
        st["weeksA"] = weeks[i % len(weeks)]
        st["compare"] = i % 4 == 1
        st["weeksB"] = weeks[(i + 3) % len(weeks)]
        st["postTune"] = i % 5 != 0
        st["proj"] = i % 6 == 0
        st["minchars"] = [1, 10, 50, 250][i % 4]
        out.append(st)
    for tiers_ in tiers:
        for pc_ in pcts:
            for w in weeks:
                st = {"tier0": tiers_[0], "tier2": tiers_[1], "tier4": tiers_[2], "pctl": pc_, "weeksA": w,
                      "klo": 2, "khi": 30, "postTune": rnd.random() < 0.5, "timedOnly": rnd.random() < 0.7,
                      "merge": rnd.random() < 0.6, "compare": rnd.random() < 0.4, "weeksB": rnd.choice(weeks),
                      "minchars": rnd.choice([1, 10, 100]), "role": rnd.choice([[], ["DPS"], ["Tank", "Healer"]]),
                      "proj": rnd.random() < 0.2, "elite": rnd.random() < 0.1,
                      "tab": rnd.choice(["avg", "med", "adeaths", "deathless", "arating", "mrating", "chars"]),
                      "sort": rnd.choice(["desc", "asc", "name", "gain", "loss"])}
                if rnd.random() < 0.3:
                    st["melee"] = True
                elif rnd.random() < 0.3:
                    st["ranged"] = True
                out.append(st)
    out.insert(0, {})
    out.insert(1, {"compare": True})
    out.insert(2, {"elite": True, "klo": 2, "khi": 30})
    return out


def apply(site, base, spec):
    st = site.state
    st.clear()
    st.update({k: (set(v) if isinstance(v, set) else v) for k, v in base.items()})
    for k, v in spec.items():
        st[k] = set(v) if k in SET_KEYS else v
    site.refMemo = {}


def name_of(g):
    return (g["cls"], g["spec"], g["hero"])


def compare_agg(A, B, label):
    ga = {name_of(g): g for g in A["groups"].values()}
    gb = {name_of(g): g for g in B["groups"].values()}
    assert set(ga) == set(gb), (label, set(ga) ^ set(gb))
    for k in ga:
        for f in FIELDS:
            if f in ga[k]:
                assert _eq(ga[k][f], gb[k][f]), (label, k, f, ga[k][f], gb[k][f])
        if "floorK" in ga[k]:
            assert ga[k]["floorK"] == gb[k]["floorK"]
    for f in ("parses", "runs", "chars", "dmin", "dmax"):
        assert A[f] == B[f], (label, f, A[f], B[f])


def test_aggregates_bitexact(n_states: int = N_STATES):
    fx = fixture()
    lroot, proot = pu.legacy_root(), pu.parts_root()
    L = pu.legacy_payload(lroot)
    loaded = pc.load_site(proot / "site" / "d")
    # the per-run identity on both sides
    lk = pu.legacy_keys(lroot)
    pk = parts_keys(proot, loaded)
    inv_pk = {v: k for k, v in pk.items()}
    sl = sc.init_data(L, fx["now_ms"])
    sp = sc.init_data(loaded.D, fx["now_ms"], R=loaded.R)
    assert sl.weekCounts == sp.weekCounts and sl.availWeeks == sp.availWeeks
    assert sl.runCount == sp.runCount == loaded.manifest["window"]["runs"]
    assert (sl.hasTier, sl.hasRating, sl.hasTune, sl.hasProj, sl.hasTimed) == \
        (sp.hasTier, sp.hasRating, sp.hasTune, sp.hasProj, sp.hasTimed)
    assert sl.DEF == sp.DEF, (sl.DEF, sp.DEF)
    # RUNS: comp (names), key_, pct, deaths per (report, fight)
    lrun_key = {}
    for i, (c, f) in enumerate(zip(lk["report_code"], lk["fight_id"])):
        lrun_key.setdefault(int(sl.R["run"][i]), (str(c), int(f)))
    prun_key = {}
    for i in range(sp.N):
        prun_key.setdefault(int(sp.R["run"][i]), inv_pk[i][:2])
    by_p = {prun_key[r]: o for r, o in enumerate(sp.RUNS) if o}
    assert len(by_p) == len(sl.RUNS)
    for r, o in enumerate(sl.RUNS):
        q = by_p[lrun_key[r]]
        assert [(L["classes"][c], L["specs"][s], L["roles"][ro]) for c, s, ro in o["comp"]] == \
            [(loaded.D["classes"][c], loaded.D["specs"][s], loaded.D["roles"][ro]) for c, s, ro in q["comp"]]
        assert (o["pct"], o["deaths"], o["kdur"], o["dur"], o["key"], o["day"]) == \
            (q["pct"], q["deaths"], q["kdur"], q["dur"], q["key"], q["day"]), lrun_key[r]
        assert L["dungeons"][o["dun"]] == loaded.D["dungeons"][q["dun"]]
    base_l = {k: (set(v) if isinstance(v, set) else v) for k, v in sl.state.items()}
    base_p = {k: (set(v) if isinstance(v, set) else v) for k, v in sp.state.items()}
    states = grid(L)[:n_states]
    for i, spec in enumerate(states):
        apply(sl, base_l, spec)
        apply(sp, base_p, spec)
        A_l = sc.aggregate(sl, sl.state["weeksA"])
        A_p = sc.aggregate(sp, sp.state["weeksA"])
        compare_agg(A_l, A_p, (i, spec))
        B_l = B_p = None
        if sl.state["compare"]:
            B_l = sc.aggregate(sl, sl.state["weeksB"])
            B_p = sc.aggregate(sp, sp.state["weeksB"])
            compare_agg(B_l, B_p, (i, spec, "B"))
        g_l = sc.render_gate(sl, A_l, B_l)
        g_p = sc.render_gate(sp, A_p, B_p)
        assert g_l["effMinA"] == g_p["effMinA"] and g_l["effMinB"] == g_p["effMinB"], (i, spec)
        names_l = [name_of(A_l["groups"][k]) for k in g_l["CHART_KEYS"]]
        names_p = [name_of(A_p["groups"][k]) for k in g_p["CHART_KEYS"]]
        # the gated set and the ranking are identical; among EXACT ties of
        # the sort value (e.g. every arating NaN under elite) the client's
        # stable sort keeps insertion order, which is the row order of each
        # side, so ties may permute
        assert set(names_l) == set(names_p) and len(names_l) == len(names_p), (i, spec)
        col = sl.state["tab"] if sl.state["tab"] in sc.TABS else "med"
        vl = [A_l["groups"][k][col] for k in g_l["CHART_KEYS"]]
        vp = [A_p["groups"][k][col] for k in g_p["CHART_KEYS"]]
        if sl.state["sort"] in ("desc", "asc"):
            assert all(_eq(a, b) for a, b in zip(vl, vp)), (i, spec, vl[:5], vp[:5])
        elif sl.state["sort"] == "name":
            assert names_l == names_p, (i, spec)
        rank_l = {name_of(A_l["groups"][r["key"]]): r["rank"] for r in g_l["rows"]}
        rank_p = {name_of(A_p["groups"][r["key"]]): r["rank"] for r in g_p["rows"]}
        assert set(rank_l) == set(rank_p)
        by_val_l = {}
        for r in g_l["rows"]:
            by_val_l.setdefault(repr(A_l["groups"][r["key"]][col]), set()).add(name_of(A_l["groups"][r["key"]]))
        by_val_p = {}
        for r in g_p["rows"]:
            by_val_p.setdefault(repr(A_p["groups"][r["key"]][col]), set()).add(name_of(A_p["groups"][r["key"]]))
        assert by_val_l == by_val_p, (i, spec)
        assert sl.eliteHidden == sp.eliteHidden
        # set-bonus cells
        sb_l = {(r["cls"], r["spec"], r["hero"]): r for r in sc.set_bonus_rows(sl)}
        sb_p = {(r["cls"], r["spec"], r["hero"]): r for r in sc.set_bonus_rows(sp)}
        assert set(sb_l) == set(sb_p), (i, spec)
        for k, r in sb_l.items():
            for f, v in r.items():
                if f in ("p2", "p4", "pt") and isinstance(v, float) and not math.isnan(v):
                    # the weighted mean of per-cell ratios is accumulated in
                    # the client's row order, which is the CSV order for the
                    # legacy payload and the content order for day files:
                    # same cells, same medians, last-bit float noise
                    assert math.isclose(v, sb_p[k][f], rel_tol=1e-12, abs_tol=1e-12), (i, spec, k, f, v, sb_p[k][f])
                else:
                    assert _eq(v, sb_p[k][f]), (i, spec, k, f, v, sb_p[k][f])
    print(f"aggregates bit-exact over {len(states)} states")


if __name__ == "__main__":
    test_aggregates_bitexact()
    print("test_aggregates_bitexact: all green")
