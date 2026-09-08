"""Diagnostic (diagnose.yml): where on WCL's leaderboard is a run the sweep never saw?

For each target (encounter, key level, report:fight) walk fightRankings(metric: score,
bracket: key-1, page) exactly as scripts/fetch_data.py does, past the sweep's page cap,
and report: the page the target sits on (or absence), entries per page, hasMorePages at
each page, entries without a report code (anonymous logs), and the score range per page.

Owner, 2026-09-08: completed +19/+20 runs in public reports were absent from the
journals (QqKhJgCN3d2wHfb4 f2; pTcwhgPD2Zxz1J46 f2/f3/f5/f6/f33/f39).
"""
import json, os, pathlib, sys, time
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from wcl_client import WCLClient

# (encounter id, key level, report code, fight id, note)
TARGETS = [
    (12825, 19, "QqKhJgCN3d2wHfb4", 2, "Den +19 kill 31:19, never in journals"),
    (12993, 18, "QqKhJgCN3d2wHfb4", 1, "Altar +18 kill 29:52, never in journals"),
    (12923, 19, "pTcwhgPD2Zxz1J46", 2, "Voidscar +19 kill 25:27, missing; f30 same dungeon/level journaled"),
    (12813, 19, "pTcwhgPD2Zxz1J46", 3, "Murder Row +19 kill 30:43, missing"),
    (12825, 20, "pTcwhgPD2Zxz1J46", 5, "Den +20 kill 29:39, missing"),
    (12993, 19, "pTcwhgPD2Zxz1J46", 6, "Altar +19 kill 31:08, missing; f15 journaled"),
    (61877, 19, "pTcwhgPD2Zxz1J46", 33, "Temple +19 kill 32:32, missing; f7 (untimed) journaled"),
    (112521, 19, "pTcwhgPD2Zxz1J46", 39, "Ruby +19 kill 29:19, missing; f9 journaled"),
    (12993, 20, "pTcwhgPD2Zxz1J46", 35, "Altar +20 kill 32:22 (untimed) -- JOURNALED, control"),
]
MAX_PROBE_PAGE = int(os.environ.get("PROBE_PAGES", "25"))
BATCH = 10
client = WCLClient(verbose=False)
boards = sorted({(enc, key) for enc, key, *_ in TARGETS})
want = {(enc, key): {(c, f): note for e2, k2, c, f, note in TARGETS if (e2, k2) == (enc, key)} for enc, key in boards}
pages = {b: {} for b in boards}   # (enc,key) -> page -> summary
cursors = {b: 1 for b in boards}
while cursors:
    batch = list(cursors.items())[:BATCH]
    parts = [f'a{i}: encounter(id: {enc}) {{ fightRankings(metric: score, bracket: {key - 1}, page: {pg}) }}'
             for i, ((enc, key), pg) in enumerate(batch)]
    try:
        data = client.query("{ worldData { " + " ".join(parts) + " } }", est_cost=1.5 * len(batch))
    except Exception as e:  # noqa: BLE001
        print(f"[probe] query failed at {batch}: {e}"); break
    world = (data or {}).get("worldData") or {}
    for i, ((enc, key), pg) in enumerate(batch):
        fr = ((world.get(f"a{i}") or {}).get("fightRankings")) or {}
        rk = fr.get("rankings")
        if rk is None:
            pages[(enc, key)][pg] = {"n": None, "more": None, "note": "no rankings (404/empty)"}
            del cursors[(enc, key)]; continue
        codes = [((r.get("report") or {}).get("code"), (r.get("report") or {}).get("fightID")) for r in rk]
        anon = sum(1 for c, f in codes if not c)
        scores = [r.get("score") for r in rk if r.get("score") is not None]
        hits = [f"{c}:{f}" for c, f in codes if (c, f) in want[(enc, key)]]
        regions = {}
        for r in rk:
            reg = ((r.get("server") or {}).get("region") or "?").upper(); regions[reg] = regions.get(reg, 0) + 1
        pages[(enc, key)][pg] = {"n": len(rk), "more": bool(fr.get("hasMorePages")), "anon": anon,
                                 "score": (min(scores), max(scores)) if scores else None, "hits": hits, "regions": regions}
        if fr.get("hasMorePages") and pg < MAX_PROBE_PAGE:
            cursors[(enc, key)] = pg + 1
        else:
            del cursors[(enc, key)]
    print(f"[probe] {len(cursors)} boards open, {client.spent:.0f} pts", flush=True)
from fetch_data import ENCOUNTERS  # noqa: E402  (names only)
for (enc, key) in boards:
    P = pages[(enc, key)]
    print(f"\n== {ENCOUNTERS.get(enc, enc)} +{key} (bracket {key-1}) -- {len(P)} pages walked")
    total = sum(v["n"] or 0 for v in P.values()); anon = sum(v.get("anon", 0) or 0 for v in P.values())
    last = max(P) if P else 0
    print(f"   entries {total:,} ({anon:,} without a report code), last page {last} hasMorePages={P.get(last, {}).get('more')}"
          f" | page-1 score {P.get(1, {}).get('score')} | page-{last} score {P.get(last, {}).get('score')}"
          f" | regions p1 {P.get(1, {}).get('regions')}")
    for pg, v in sorted(P.items()):
        if v.get("hits") or pg in (1, 20, 21, last):
            print(f"   page {pg:>2}: n={v['n']} more={v['more']} score={v.get('score')} hits={v.get('hits')}")
    for (c, f), note in want[(enc, key)].items():
        where = [pg for pg, v in P.items() if f"{c}:{f}" in (v.get("hits") or [])]
        print(f"   target {c}:{f} -> {'page ' + str(where[0]) if where else 'NOT LISTED in ' + str(len(P)) + ' pages'}  ({note})")
