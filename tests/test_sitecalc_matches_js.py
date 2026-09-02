#!/usr/bin/env python3
"""tests/test_sitecalc_matches_js.py (partitioned_payload.md §9.1)

The real functions of site/index.html run under node (tests/js_oracle.js:
the whole main script, a recording DOM stub, Date frozen, render() executed
for every state) on the §9 fixture produce IDENTICAL output to
scripts/sitecalc.py for 200 random states -- the oracle cannot drift from the
client. Compared exactly (no tolerance): aggregate A/B per group (n avg med
q30 q85 qb qdA qdB adeaths deathless chars runs arating mrating rn ravg
rmed), parses/runs/chars/dates, effMin and CHART_KEYS, renderComps' rowsAll
and presence, Pulse's entries, Trends' buckets/series/points/normalisations/
trendMin, setBonusRows and aggregateElite (eliteHidden, floors).
"""
import gzip
import json
import pathlib
import random
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import sitecalc as sc                                            # noqa: E402
from fixture_util import FIXTURE_DIR, SET_KEYS, diff, fixture, norm   # noqa: E402

N_STATES = 200
CLIENTS = [ROOT / "site" / "index.html"]
if (ROOT / "site" / "next" / "index.html").exists():
    CLIENTS.append(ROOT / "site" / "next" / "index.html")


def random_states(site: sc.Site, n: int, seed: int = 2026) -> list:
    """n state overrides: a fixed set of corner cases first, then random
    draws over every control the page has."""
    rnd = random.Random(seed)
    D = site.D
    keys = sorted(set(int(k) for k in site.R["key"]))
    weeks = list(site.availWeeks)
    tabs = ["avg", "med", "adeaths", "deathless", "arating", "mrating", "chars"]
    metrics = ["avg", "med", "adeaths", "deathless", "chars"]
    sorts = ["desc", "asc", "name", "gain", "loss"]
    corner = [
        {},
        {"compare": True},
        {"elite": True},
        {"merge": False},
        {"merge": False, "hero": [D["heroes"][3], D["heroes"][7]]},
        {"dayCut": 1}, {"dayCut": 2, "compare": True},
        {"skill": True, "tab": "adeaths"}, {"skill": True, "tab": "med", "pctlB": 30},
        {"proj": True, "postTune": True}, {"proj": True, "postTune": False, "compare": True},
        {"postTune": False}, {"timedOnly": False},
        {"tier0": True, "tier2": False, "tier4": False}, {"tier0": True, "tier2": True, "tier4": True},
        {"tier0": False, "tier2": False, "tier4": False},
        {"role": []}, {"role": ["Tank", "Healer", "DPS"]}, {"melee": True}, {"ranged": True},
        {"melee": True, "ranged": True},
        {"minchars": 1}, {"minchars": 1200}, {"klo": keys[0], "khi": keys[-1]},
        {"weeksA": weeks, "compare": True, "weeksB": weeks[:1]},
        {"weeksA": [w for w in weeks if w >= 3]},
        {"trajSort": "slope"}, {"trendNorm": "rank"}, {"trendNorm": "share"},
        {"trendMetric": "med", "trendNorm": "rank", "trajSort": "slope", "pctl": 85},
        {"trendMetric": "chars", "trendNorm": "share"},
        {"compMin": 1}, {"compMin": 3, "reg": ["EU"]},
        {"sort": "name"}, {"sort": "gain", "compare": True}, {"sort": "loss", "compare": True},
        {"tab": "arating", "compare": True}, {"tab": "chars", "sort": "asc"},
    ]
    out = list(corner)
    while len(out) < n:
        st = {}
        if rnd.random() < 0.3:
            st["cls"] = rnd.sample(D["classes"], rnd.randint(1, 3))
        if rnd.random() < 0.25:
            st["spec"] = rnd.sample(D["specs"], rnd.randint(1, 4))
        if rnd.random() < 0.3:
            st["merge"] = False
            if rnd.random() < 0.5:
                st["hero"] = rnd.sample(D["heroes"], rnd.randint(1, 3))
        if rnd.random() < 0.3:
            st["dun"] = rnd.sample(D["dungeons"], rnd.randint(1, 3))
        if rnd.random() < 0.4:
            st["role"] = rnd.sample(["DPS", "Healer", "Tank"], rnd.randint(0, 3))
        if rnd.random() < 0.3:
            st["reg"] = rnd.sample(D["regions"], rnd.randint(1, 2))
        r = rnd.random()
        if r < 0.15:
            st["melee"] = True
        elif r < 0.3:
            st["ranged"] = True
        if rnd.random() < 0.5:
            lo = rnd.choice(keys)
            hi = rnd.choice([k for k in keys if k >= lo])
            st["klo"], st["khi"] = lo, hi
        if rnd.random() < 0.6:
            st["weeksA"] = rnd.choice([[0], [1], [0, 1], [0, 1, 2], [2], [3], [0, 1, 2, 3], [4], weeks,
                                       rnd.sample(weeks, rnd.randint(1, len(weeks)))])
        if rnd.random() < 0.35:
            st["compare"] = True
            st["weeksB"] = rnd.choice([[1], [2], [1, 2], [0], [3], weeks])
        if rnd.random() < 0.15 and st.get("weeksA", [0]) == [0]:
            st["dayCut"] = rnd.randint(1, 3)
        if rnd.random() < 0.3:
            st["compMin"] = rnd.choice([1, 2, 5, 10, 20, 40])
        if rnd.random() < 0.3:
            st["postTune"] = rnd.random() < 0.5
        if rnd.random() < 0.2:
            st["proj"] = True
        if rnd.random() < 0.3:
            st["minchars"] = rnd.choice([1, 10, 50, 100, 250, 400, 800, 1200])
        if rnd.random() < 0.4:
            st["sort"] = rnd.choice(sorts)
        if rnd.random() < 0.6:
            st["tab"] = rnd.choice(tabs)
        if rnd.random() < 0.6:
            st["trendMetric"] = rnd.choice(metrics)
        if rnd.random() < 0.3:
            st["timedOnly"] = False
        if rnd.random() < 0.3:
            st["tier0"], st["tier2"], st["tier4"] = (rnd.random() < 0.5 for _ in range(3))
        if rnd.random() < 0.4:
            st["pctl"] = rnd.choice([10, 30, 50, 75, 85, 90, 95])
        if rnd.random() < 0.08:
            st["elite"] = True
        if rnd.random() < 0.2:
            st["skill"] = True
        if rnd.random() < 0.3:
            st["pctlB"] = rnd.choice([10, 30, 50, 85, 95])
        if rnd.random() < 0.4:
            st["trendNorm"] = rnd.choice(["dps", "rank", "share"])
        if rnd.random() < 0.3:
            st["trajSort"] = "slope"
        out.append(st)
    return out[:n]


def apply_state(site: sc.Site, base: dict, spec: dict) -> None:
    st = site.state
    st.clear()
    st.update({k: (set(v) if isinstance(v, set) else v) for k, v in base.items()})
    for k, v in spec.items():
        st[k] = set(v) if k in SET_KEYS else v
    site.refMemo = {}


def py_render(site: sc.Site) -> dict:
    """sitecalc's numbers in the shape js_oracle.js prints."""
    r = sc.render_all(site)
    A, B, g = r["A"], r["B"], r["gate"]
    fields = ("n", "avg", "med", "q30", "q85", "qb", "qdA", "qdB", "adeaths", "deathless",
              "chars", "runs", "arating", "mrating", "rn", "ravg", "rmed", "cls", "spec", "hero", "floorK")

    def agg(X):
        if X is None:
            return None
        return {"parses": X["parses"], "runs": X["runs"], "chars": X["chars"],
                "dmin": X["dmin"], "dmax": X["dmax"],
                "groups": [[k, {f: v.get(f) for f in fields}] for k, v in X["groups"].items()]}
    tr = r["trend"]
    return {
        "A": agg(A), "B": agg(B), "effMin": g["effMinA"], "CHART_KEYS": g["CHART_KEYS"],
        "eliteHidden": site.eliteHidden,
        "comps": {"rowsAll": [{f: x[f] for f in ("strength", "best", "median", "avgkey", "n", "kdur",
                                                  "dun", "key", "deaths", "day", "comp")}
                              for x in r["comps"]["rowsAll"]],
                  "presence": {"den": r["comps"]["presence"]["den"],
                               "map": [list(x) for x in sorted(r["comps"]["presence"]["map"].items())]}},
        "pulse": [{f: e[f] for f in ("key", "dps", "prevMed", "thin", "nNow", "nPrev", "adeaths",
                                     "delta", "isNew", "pres", "spark", "rn", "rp")}
                  for e in r["pulse"]["entries"]],
        "trend": {"buckets": tr["buckets"], "trendMin": tr["trendMin"],
                  "series": [{"key": s["key"], "name": s["name"], "pts": s["pts"]} for s in tr["series"]],
                  **({"ymin": tr["ymin"], "ymax": tr["ymax"]} if tr["series"] else {})},
        "setbonus": r["setbonus"],
    }


def js_render(client: pathlib.Path, payload_path: pathlib.Path, states: list, now_ms: int) -> list:
    with tempfile.TemporaryDirectory() as tmp:
        tp = pathlib.Path(tmp)
        (tp / "states.json").write_text(json.dumps(states))
        subprocess.run(["node", str(ROOT / "tests" / "js_oracle.js"), str(client), str(payload_path),
                        str(tp / "states.json"), str(now_ms), str(tp / "out.json")],
                       check=True, timeout=1800)
        return json.loads((tp / "out.json").read_text())["results"]


def normalise_js(j: dict) -> dict:
    j = dict(j)
    for k in ("weekCounts", "availWeeks", "usB0", "curMinDay", "curMaxDay", "runCount"):
        j.pop(k, None)
    j["comps"]["presence"]["map"] = [list(x) for x in sorted(map(tuple, j["comps"]["presence"]["map"]))]
    tm = j["trend"].get("trendMin")
    j["trend"]["trendMin"] = tm[1] if tm else None
    if not j["trend"]["series"]:
        j["trend"].pop("ymin", None)
        j["trend"].pop("ymax", None)
    return j


def test_sitecalc_matches_js(n_states: int = N_STATES):
    fx = fixture()
    payload_gz = FIXTURE_DIR / "payload.json.gz"
    payload = json.load(gzip.open(payload_gz, "rt"))
    now_ms = fx["now_ms"]
    site = sc.init_data(payload, now_ms)
    base = {k: (set(v) if isinstance(v, set) else v) for k, v in site.state.items()}
    states = random_states(site, n_states)
    # sanity: the fixture holds every bucket the tests draw from
    assert site.availWeeks == [0, 1, 2, 3, 4], site.availWeeks
    assert site.hasTier and site.hasRating and site.hasTune and site.hasProj and site.hasTimed
    with tempfile.TemporaryDirectory() as tmp:
        plain = pathlib.Path(tmp) / "payload.json"
        with gzip.open(payload_gz, "rb") as src, open(plain, "wb") as dst:
            shutil.copyfileobj(src, dst)
        for client in CLIENTS:
            js = js_render(client, plain, states, now_ms)
            assert len(js) == len(states)
            failures = []
            for i, (spec, jr) in enumerate(zip(states, js)):
                if "error" in jr:
                    failures.append((i, spec, ["JS error: " + jr["error"][:600]]))
                    continue
                apply_state(site, base, spec)
                py = norm(py_render(site))
                d = diff(normalise_js(norm(jr)), py)
                if d:
                    failures.append((i, spec, d))
            for i, spec, d in failures[:10]:
                print(f"state {i} {json.dumps(spec)}:")
                for line in d[:12]:
                    print("   ", line)
            assert not failures, f"{client.name}: {len(failures)} of {len(states)} states differ"
            print(f"{client.relative_to(ROOT)}: {len(states)} states identical")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else N_STATES
    test_sitecalc_matches_js(n)
    print("test_sitecalc_matches_js: all green")
