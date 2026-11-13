#!/usr/bin/env python3
"""Diagnostic (diagnose.yml): is the dashboard's per-player DPS the number Warcraft Logs ranks?

Owner, 2026-11-13: the dashboard shows Assassination Rogue far above Subtlety at
high keys while archon.gg (built on Warcraft Logs rankings) shows them level.
The dashboard's DPS is Summary-table damageDone.total / totalTime
(fetch_data.parse_summary). For a sample of recent high-key rogue runs -- both
specs, Ruby Life Pools and the other seven dungeons -- this reads

  * report.rankings(fightIDs, playerMetric: dps)   the DPS Warcraft Logs ranks
  * report.table(dataType: Summary)                what the collector reads
  * report.table(dataType: DamageDone)             the site's Damage Done table
  * report.fights                                  the fight's own clock

and prints, for EVERY player of each sampled run, the three DPS figures and
their ratios, then the median ratio per rogue group and per class-spec. A ratio
at 1.00 for both rogue specs says the metric is the one Warcraft Logs ranks and
any gap to archon.gg is aggregation; a ratio that differs BY SPEC (or by
dungeon) says the collector counts damage the rankings do not.

Targets come from data/mythic_runs.csv.gz in the checkout (the newest daily
export): the newest runs at or above MIN_KEY, distinct reports, PER_GROUP per
(spec, Ruby Life Pools | other dungeons, round-robin over the dungeons).
Cost: one request per 4 runs, roughly 8 points a run (~300 points for 36 runs).
`--dry` prints the picks and exits without touching the API.
"""
import csv
import gzip
import json
import os
import pathlib
import statistics
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

CSV = ROOT / "data" / "mythic_runs.csv.gz"
MIN_KEY = int(os.environ.get("MIN_KEY", "20"))
PER_GROUP = int(os.environ.get("PER_GROUP", "9"))
BATCH = 4
SPECS = ("Assassination", "Subtlety")
RLP = "Ruby Life Pools"


def pick_targets() -> list[dict]:
    csv.field_size_limit(10 ** 9)
    cand = []
    with gzip.open(CSV, "rt", newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("class") != "Rogue" or r.get("spec") not in SPECS or r.get("role") != "DPS":
                continue
            try:
                key = int(float(r["key_level"]))
                started = int(r["started_at"])
                dps = float(r["dps"])
            except (TypeError, ValueError):
                continue
            if key < MIN_KEY:
                continue
            cand.append({"spec": r["spec"], "dungeon": r["dungeon"], "key": key,
                         "code": r["report_code"], "fid": int(r["fight_id"]),
                         "character": r["character"], "region": r["region"],
                         "csv_dps": dps, "csv_dur": float(r["duration_s"]),
                         "started": started})
    cand.sort(key=lambda c: -c["started"])
    picks, used = [], set()
    for spec in SPECS:
        # Ruby Life Pools: the newest PER_GROUP from distinct reports
        n = 0
        for c in cand:
            if c["spec"] == spec and c["dungeon"] == RLP and c["code"] not in used:
                used.add(c["code"]); picks.append({**c, "group": f"{spec} / {RLP}"}); n += 1
                if n >= PER_GROUP:
                    break
        # the other dungeons: round-robin so no single dungeon fills the group
        by_dun = defaultdict(list)
        for c in cand:
            if c["spec"] == spec and c["dungeon"] != RLP:
                by_dun[c["dungeon"]].append(c)
        n, progressed = 0, True
        while n < PER_GROUP and progressed:
            progressed = False
            for dun in sorted(by_dun):
                while by_dun[dun] and by_dun[dun][0]["code"] in used:
                    by_dun[dun].pop(0)
                if by_dun[dun] and n < PER_GROUP:
                    c = by_dun[dun].pop(0)
                    used.add(c["code"]); picks.append({**c, "group": f"{spec} / other dungeons"})
                    n += 1; progressed = True
    return picks


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def fmt(x, spec="{:.3f}"):
    return "    -" if x is None else spec.format(x)


def main() -> None:
    picks = pick_targets()
    print(f"[diag] {len(picks)} runs at +{MIN_KEY} and up, {PER_GROUP} per group, from {CSV.name}")
    for p in picks:
        print(f"  pick {p['group']:34s} {p['dungeon']:22s} +{p['key']} {p['region']} {p['code']}#{p['fid']} "
              f"{p['character']} csv_dps={p['csv_dps']:.0f}")
    if "--dry" in sys.argv:
        return
    from wcl_client import WCLClient  # noqa: E402  (after --dry: no credentials needed for a dry run)
    client = WCLClient(verbose=False)
    rows, shown_keys = [], False
    for b0 in range(0, len(picks), BATCH):
        batch = picks[b0:b0 + BATCH]
        parts = [f'a{i}: report(code: "{p["code"]}") {{ '
                 f'rk: rankings(fightIDs: [{p["fid"]}], playerMetric: dps) '
                 f'sm: table(fightIDs: [{p["fid"]}], dataType: Summary) '
                 f'dd: table(fightIDs: [{p["fid"]}], dataType: DamageDone) '
                 f'fights(fightIDs: [{p["fid"]}]) {{ id startTime endTime keystoneLevel keystoneTime kill }} }}'
                 for i, p in enumerate(batch)]
        try:
            data = client.query("{ reportData { " + " ".join(parts) + " } }", est_cost=9.0 * len(batch))
        except Exception as e:  # noqa: BLE001
            print(f"[diag] request failed for {[p['code'] for p in batch]}: {str(e)[:300]}")
            continue
        rd = (data or {}).get("reportData") or {}
        for i, p in enumerate(batch):
            node = rd.get(f"a{i}") or {}
            sm = ((node.get("sm") or {}).get("data")) if isinstance(node.get("sm"), dict) else None
            dd = ((node.get("dd") or {}).get("data")) if isinstance(node.get("dd"), dict) else None
            rk = node.get("rk")
            fights = node.get("fights") or []
            if not isinstance(sm, dict) or not sm.get("damageDone"):
                print(f"[diag] {p['code']}#{p['fid']}: no Summary data (private or deleted report?)")
                continue
            rk_fights = (rk or {}).get("data") if isinstance(rk, dict) else None
            rkf = rk_fights[0] if rk_fights else {}
            if not shown_keys:
                shown_keys = True
                print(f"\n[diag] Summary keys: {sorted(sm)}")
                print(f"[diag] DamageDone keys: {sorted(dd) if isinstance(dd, dict) else dd}")
                if isinstance(dd, dict) and dd.get("entries"):
                    print(f"[diag] DamageDone entry keys: {sorted(dd['entries'][0])}")
                print(f"[diag] rankings fight keys: {sorted(rkf)}")
                chars0 = [c for role in (rkf.get('roles') or {}).values() for c in (role.get('characters') or [])]
                if chars0:
                    print(f"[diag] rankings character: {json.dumps(chars0[0])[:600]}")
            sm_time = sm.get("totalTime") or 0
            f0 = fights[0] if fights else {}
            f_dur = (f0.get("endTime") or 0) - (f0.get("startTime") or 0)
            ranked = {}
            for role in (rkf.get("roles") or {}).values():
                for c in role.get("characters") or []:
                    ranked[c.get("name")] = c
            dd_entries = {e.get("id"): e for e in (dd or {}).get("entries") or [] if isinstance(e, dict)}
            dd_time = (dd or {}).get("totalTime") or 0
            sm_dmg = {d.get("id"): d.get("total", 0) for d in sm.get("damageDone") or [] if isinstance(d, dict)}
            print(f"\n== {p['group']} | {p['dungeon']} +{p['key']} {p['region']} {p['code']}#{p['fid']} | "
                  f"Summary totalTime {sm_time / 1000:.1f}s, DamageDone totalTime {dd_time / 1000:.1f}s, "
                  f"fight clock {f_dur / 1000:.1f}s, keystoneTime {(f0.get('keystoneTime') or 0) / 1000:.1f}s, "
                  f"rankings duration {(rkf.get('duration') or 0) / 1000:.1f}s")
            details = sm.get("playerDetails") or {}
            for role_key in ("tanks", "healers", "dps"):
                for pl in details.get(role_key) or []:
                    pid, name = pl.get("id"), pl.get("name")
                    specs = pl.get("specs") or []
                    icon = pl.get("icon") or ""
                    spec = specs[0] if specs else (icon.split("-", 1)[1] if "-" in icon else "")
                    our = sm_dmg.get(pid, 0) / (sm_time / 1000.0) if sm_time else None
                    e = dd_entries.get(pid) or {}
                    dd_total = e.get("total")
                    dd_dps = dd_total / (dd_time / 1000.0) if dd_total is not None and dd_time else None
                    rc = ranked.get(name) or {}
                    amount = rc.get("amount")
                    row = {"group": p["group"], "target": name == p["character"], "cls": pl.get("type"), "spec": spec,
                           "role": role_key, "dungeon": p["dungeon"], "our": our, "dd_dps": dd_dps, "ranked": amount,
                           "r_ranked": (amount / our) if amount and our else None,
                           "r_dd": (dd_total / sm_dmg[pid]) if dd_total is not None and sm_dmg.get(pid) else None,
                           "r_csv": (our / p["csv_dps"]) if name == p["character"] and our and p["csv_dps"] else None}
                    rows.append(row)
                    print(f"   {'*' if row['target'] else ' '} {role_key:7s} {str(pl.get('type')):12s} {spec:14s} "
                          f"summary_dps={fmt(our, '{:9.0f}')} damage_table_dps={fmt(dd_dps, '{:9.0f}')} "
                          f"ranked_dps={fmt(amount, '{:9.0f}')} | ranked/summary={fmt(row['r_ranked'])} "
                          f"table/summary dmg={fmt(row['r_dd'])}"
                          + (f" summary/csv={fmt(row['r_csv'])}" if row["target"] else "")
                          + (f" rankPercent={rc.get('rankPercent')} bracketPercent={rc.get('bracketPercent')}" if rc else " (not ranked)"))
    print("\n== median ratios for the sampled ROGUE (the target character of each run)")
    by = defaultdict(list)
    for r in rows:
        if r["target"]:
            by[r["group"]].append(r)
    for g in sorted(by):
        rs = by[g]
        print(f"  {g:36s} n={len(rs):2d} ranked/summary: median {fmt(med([r['r_ranked'] for r in rs]))} "
              f"min {fmt(min([r['r_ranked'] for r in rs if r['r_ranked']] or [None]))} "
              f"max {fmt(max([r['r_ranked'] for r in rs if r['r_ranked']] or [None]))} | "
              f"table/summary damage: median {fmt(med([r['r_dd'] for r in rs]))} | "
              f"summary/csv: median {fmt(med([r['r_csv'] for r in rs]))} | ranked {sum(1 for r in rs if r['ranked'])}/{len(rs)}")
    print("\n== median ranked/summary ratio for every class-spec seen (all five players of each run)")
    by2 = defaultdict(list)
    for r in rows:
        by2[(r["role"], r["cls"], r["spec"])].append(r)
    for k in sorted(by2, key=lambda k: (k[0], str(k[1]), k[2])):
        rs = by2[k]
        print(f"  {k[0]:7s} {str(k[1]):12s} {k[2]:14s} n={len(rs):3d} ranked/summary median {fmt(med([r['r_ranked'] for r in rs]))} "
              f"min {fmt(min([r['r_ranked'] for r in rs if r['r_ranked']] or [None]))} "
              f"max {fmt(max([r['r_ranked'] for r in rs if r['r_ranked']] or [None]))} | "
              f"table/summary damage median {fmt(med([r['r_dd'] for r in rs]))}")


if __name__ == "__main__":
    main()
