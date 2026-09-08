#!/usr/bin/env python3
"""Lightspire Core, pass 2: what exactly does the log record per beam?

Pass 1 (2026-09-08) found the standing-in-the-light aura is 1263768
"Light's Blessing": 23 bands / 23 casts on a 1060 s Marksmanship fight and
27 / 27 on a 1082 s Arcane fight -- one band per proc, ~3.5-3.8 s average
against a 12 s beam -- while the area-trigger spell 1263762 never appears
(0 cast events). So the CAST of 1263768 is the only logged marker of a beam.
This pass pulls the raw event stream for the same two fights to settle:
  * does every cast coincide with an applybuff (cast == beam spawn with the
    player inside it), or do casts outrun bands (spawns the player ignored)?
  * are bands capped at the 12 s beam lifetime? any refreshbuff pulses?
  * anything at all logged under 1263762, from any source?
and computes the benefit ratio under the "cast = spawn" model once, by hand.
No journal needed; ~3 requests.
"""
import json
import sys
import pathlib
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from wcl_client import WCLClient  # noqa: E402

BUFF = 1263768
AREA = 1263762
BEAM_MS = 12_000
FIGHTS = [("1g9zArwpd3tGWYNQ", 2, 9, "Paandorra MM"),
          ("wzaYvVZdJQhFWNgB", 18, 505, "Arcane")]


def union(iv):
    out = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def inter_len(a, b):
    i = j = tot = 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if hi > lo:
            tot += hi - lo
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return tot


def main():
    client = WCLClient(verbose=True)
    parts = []
    for i, (c, f, aid, _) in enumerate(FIGHTS):
        parts.append(
            f'a{i}: report(code: "{c}") {{ '
            f'fights(fightIDs: [{f}]) {{ startTime endTime }} '
            f'bev: events(fightIDs: [{f}], dataType: Buffs, abilityID: {BUFF}, '
            f'targetID: {aid}, limit: 5000) {{ data nextPageTimestamp }} '
            f'cev: events(fightIDs: [{f}], dataType: Casts, abilityID: {BUFF}, '
            f'sourceID: {aid}, limit: 5000) {{ data nextPageTimestamp }} '
            f'aev: events(fightIDs: [{f}], dataType: All, limit: 5000, '
            f'filterExpression: "ability.id = {AREA}") {{ data nextPageTimestamp }} '
            f'}}')
    rd = client.query("{ reportData { " + " ".join(parts) + " } }",
                      est_cost=4.0 * len(parts)).get("reportData") or {}
    for i, (c, f, aid, who) in enumerate(FIGHTS):
        rep = rd.get(f"a{i}") or {}
        fg = (rep.get("fights") or [{}])[0]
        t0, t1 = fg.get("startTime"), fg.get("endTime")
        bev = (rep.get("bev") or {}).get("data") or []
        cev = (rep.get("cev") or {}).get("data") or []
        aev = (rep.get("aev") or {}).get("data") or []
        print(f"\n[diag] {c}#{f} {who} actor {aid}: fight {t0}..{t1} "
              f"({(t1 - t0) / 1000:.0f}s)")
        print(f"  buff events: {len(bev)} {Counter(e.get('type') for e in bev)}; "
              f"more? {(rep.get('bev') or {}).get('nextPageTimestamp')}")
        print(f"  cast events: {len(cev)} {Counter(e.get('type') for e in cev)}; "
              f"more? {(rep.get('cev') or {}).get('nextPageTimestamp')}")
        print(f"  events under {AREA} (any source): {len(aev)} "
              f"{Counter((e.get('type'), e.get('sourceID')) for e in aev)}")
        for e in (bev[:3] + cev[:2] + aev[:3]):
            print(f"    raw: {json.dumps(e)[:300]}")
        casts = sorted(e["timestamp"] for e in cev if e.get("type") == "cast")
        # bands from apply/remove pairs
        bands, open_t = [], None
        for e in sorted(bev, key=lambda e: e["timestamp"]):
            ty = e.get("type")
            if ty in ("applybuff", "applybuffstack") and open_t is None:
                open_t = e["timestamp"]
            elif ty == "removebuff" and open_t is not None:
                bands.append([open_t, e["timestamp"]])
                open_t = None
        if open_t is not None:
            bands.append([open_t, t1])
        durs = sorted((b - a) / 1000 for a, b in bands)
        print(f"  bands from events: {len(bands)}; durations s: "
              f"min {durs[0] if durs else None} p50 {durs[len(durs) // 2] if durs else None} "
              f"max {durs[-1] if durs else None}; over 12.0s: "
              f"{sum(1 for d in durs if d > 12.0)}")
        print(f"  all durations: {[round(d, 1) for d in durs]}")
        # cast <-> applybuff alignment
        applies = sorted(e["timestamp"] for e in bev if e.get("type") == "applybuff")
        deltas = []
        for ct in casts:
            near = min(applies, key=lambda a: abs(a - ct)) if applies else None
            deltas.append(None if near is None else near - ct)
        print(f"  casts {len(casts)} vs applybuff {len(applies)}; "
              f"applybuff minus cast (ms) per cast: {deltas}")
        unmatched = [ct for ct, d in zip(casts, deltas) if d is None or abs(d) > 500]
        print(f"  casts with NO applybuff within 500 ms: {len(unmatched)}")
        # gaps between consecutive casts (RPPM sanity) and any cast inside a
        # still-live beam (overlapping beams)
        gaps = [(b - a) / 1000 for a, b in zip(casts, casts[1:])]
        print(f"  cast gaps s: min {min(gaps) if gaps else None:.1f} "
              f"p50 {sorted(gaps)[len(gaps) // 2] if gaps else None:.1f}; "
              f"casts within 12 s of the previous: {sum(1 for g in gaps if g < 12)}")
        avail = union([[ct, min(ct + BEAM_MS, t1)] for ct in casts])
        buff = union(bands)
        al = sum(b - a for a, b in avail)
        il = inter_len(buff, avail)
        bl = sum(b - a for a, b in buff)
        print(f"  MODEL cast=spawn: available {al / 1000:.1f}s, buff {bl / 1000:.1f}s, "
              f"buff∩available {il / 1000:.1f}s -> benefit ratio "
              f"{100 * il / al if al else float('nan'):.1f}%; buff outside any beam "
              f"window {(bl - il) / 1000:.1f}s; classic uptime {100 * bl / (t1 - t0):.1f}%")
    print(f"\n[diag] points spent this hour: {client.spent:.0f}/{client.limit:.0f}")


if __name__ == "__main__":
    main()
