#!/usr/bin/env python3
"""Lightspire Core (item 250214) in the wild -- sizing and ground truth.

Client DB2 (wago.tools, 2026-09-08) gives the chain: equip aura 1250527 ->
RPPM proc (SpellProcsPerMinute 109, base 1.25) -> 1263762 "Radiant Light",
which creates area trigger 40323 for 12 s (SpellDuration 29 = 12000 ms). The
aura the beam applies to a player standing in it is serverside and cannot be
read out of client tables, so this asks Warcraft Logs directly.

Part 1 (journals): how many wearer-fights exist, by week, key band and class
        -- the number the collector's quota budget has to carry.
Part 2 (API, ~3 requests): for the two newest wearer fights, every aura on
        the wearer with its uptime, the wearer's casts of 1263762, and -- for
        auras whose name looks like the beam's blessing -- their bands, from
        which the benefit ratio (buff time / beam-available time) is computed
        once, by hand, as the reference the feature must reproduce.
"""
import json
import pathlib
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from wcl_client import WCLClient  # noqa: E402

GEAR = ROOT / "data" / "processed" / "gear.jsonl"
PLAYERS = ROOT / "data" / "processed" / "players.jsonl"
ITEM = 250214
PROC = 1263762
BEAM_MS = 12_000
NAME_RX = re.compile(r"light|spire|radiant|embrace|bless|beam|core", re.I)


def key_band(k):
    try:
        k = int(k)
    except (TypeError, ValueError):
        return "?"
    if k < 10:
        return "<10"
    if k <= 11:
        return "10-11"
    if k <= 13:
        return "12-13"
    if k <= 15:
        return "14-15"
    return "16+"


def part1():
    needle = str(ITEM).encode()
    wearers = {}
    lines = 0
    with GEAR.open("rb") as fh:
        for raw in fh:
            lines += 1
            if needle not in raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            gear = rec.get("gear") or []
            if not any(isinstance(it, dict) and it.get("id") == ITEM for it in gear):
                continue
            k = (rec.get("report_code"), int(rec.get("fight_id") or 0),
                 rec.get("character"))
            wearers[k] = {"class": rec.get("class"), "spec": rec.get("spec"),
                          "server": rec.get("server")}
    print(f"[diag] gear journal: {lines:,} records; {len(wearers):,} wear "
          f"Lightspire Core ({100 * len(wearers) / max(1, lines):.2f}%)")
    by_week, by_band, by_class, chars = Counter(), Counter(), Counter(), set()
    codes = {k[0] for k in wearers}
    with PLAYERS.open("rb") as fh:
        for raw in fh:
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if rec.get("report_code") not in codes:
                continue
            k = (rec.get("report_code"), int(rec.get("fight_id") or 0),
                 rec.get("character"))
            w = wearers.get(k)
            if w is None:
                continue
            w["started_at"] = rec.get("started_at")
            w["key_level"] = rec.get("key_level")
            w["dungeon"] = rec.get("dungeon")
            w["region"] = rec.get("region")
            st = rec.get("started_at")
            if st:
                d = datetime.fromtimestamp(int(st) / 1000, tz=timezone.utc)
                by_week[d.strftime("%G-W%V")] += 1
            by_band[key_band(rec.get("key_level"))] += 1
            by_class[f"{rec.get('class')}/{rec.get('spec')}"] += 1
            chars.add((rec.get("character"), rec.get("server")))
    print(f"[diag] wearer-fights with a players row: {sum(by_band.values()):,}; "
          f"distinct characters {len(chars):,}")
    print("  by ISO week: " + ", ".join(f"{w}:{n:,}" for w, n in sorted(by_week.items())))
    print("  by key band: " + ", ".join(f"{b}:{n:,}" for b, n in
                                        sorted(by_band.items())))
    print("  top specs: " + ", ".join(f"{c}:{n:,}" for c, n in by_class.most_common(12)))
    dated = [(v["started_at"], k) for k, v in wearers.items() if v.get("started_at")]
    dated.sort(reverse=True)
    return [k for _, k in dated[:2]], wearers


def union(iv):
    out = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def inter_len(a, b):
    i = j = 0
    tot = 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if hi > lo:
            tot += hi - lo
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return tot


def part2(picks, wearers):
    client = WCLClient(verbose=True)
    q = " ".join(
        f'a{i}: report(code: "{c}") {{ masterData {{ actors(type: "Player") '
        f'{{ id name server }} }} fights(fightIDs: [{f}]) '
        f'{{ id startTime endTime keystoneLevel }} }}'
        for i, (c, f, _) in enumerate(picks))
    rd = client.query("{ reportData { " + q + " } }",
                      est_cost=2.0 * len(picks)).get("reportData") or {}
    targets = []
    for i, (c, f, who) in enumerate(picks):
        rep = rd.get(f"a{i}") or {}
        actors = ((rep.get("masterData") or {}).get("actors")) or []
        ids = [a["id"] for a in actors if a.get("name") == who]
        fights = rep.get("fights") or []
        if not ids or not fights:
            print(f"[diag] {c}#{f} {who}: actor/fight not resolved "
                  f"({len(actors)} actors, {len(fights)} fights)")
            continue
        fg = fights[0]
        targets.append((c, f, who, ids[0], fg["startTime"], fg["endTime"],
                        fg.get("keystoneLevel")))
    if not targets:
        return
    parts = []
    for i, (c, f, who, aid, *_rest) in enumerate(targets):
        parts.append(
            f'a{i}: report(code: "{c}") {{ '
            f'b: table(fightIDs: [{f}], dataType: Buffs, targetID: {aid}) '
            f'c: events(fightIDs: [{f}], dataType: Casts, sourceID: {aid}, '
            f'abilityID: {PROC}) {{ data nextPageTimestamp }} '
            f'ct: table(fightIDs: [{f}], dataType: Casts, sourceID: {aid}) }}')
    rd = client.query("{ reportData { " + " ".join(parts) + " } }",
                      est_cost=4.0 * len(parts)).get("reportData") or {}
    followups = []
    for i, (c, f, who, aid, t0, t1, kl) in enumerate(targets):
        rep = rd.get(f"a{i}") or {}
        w = wearers.get((c, f, who), {})
        print(f"\n[diag] {c}#{f} {who} ({w.get('class')}/{w.get('spec')}) "
              f"actor {aid}, +{kl}, fight {(t1 - t0) / 1000:.0f}s")
        bt = (rep.get("b") or {}).get("data") or {}
        print(f"  buffs table keys: {sorted(bt) if isinstance(bt, dict) else type(bt)}")
        auras = bt.get("auras") or [] if isinstance(bt, dict) else []
        auras = sorted(auras, key=lambda a: -(a.get("totalUptime") or 0))
        print(f"  {len(auras)} auras on the wearer; top by uptime "
              f"(guid | name | uptime s | uses | bands):")
        for a in auras[:45]:
            flag = " <==" if NAME_RX.search(a.get("name") or "") else ""
            print(f"    {a.get('guid')} | {a.get('name')} | "
                  f"{(a.get('totalUptime') or 0) / 1000:.1f} | {a.get('totalUses')} | "
                  f"{len(a.get('bands') or [])}{flag}")
        for a in auras[45:]:
            if NAME_RX.search(a.get("name") or ""):
                print(f"    {a.get('guid')} | {a.get('name')} | "
                      f"{(a.get('totalUptime') or 0) / 1000:.1f} | {a.get('totalUses')} | "
                      f"{len(a.get('bands') or [])} <== (below top 45)")
        ev = (rep.get("c") or {}).get("data") or []
        casts = [e.get("timestamp") for e in ev if isinstance(e, dict)]
        print(f"  casts of {PROC} by the wearer: {len(casts)} events; types "
              f"{Counter(e.get('type') for e in ev if isinstance(e, dict))}; "
              f"next page {(rep.get('c') or {}).get('nextPageTimestamp')}; "
              f"first: {casts[:5]}")
        if ev and isinstance(ev[0], dict):
            print(f"  first cast event raw: {json.dumps(ev[0])[:400]}")
        ct = (rep.get("ct") or {}).get("data") or {}
        ents = ct.get("entries") or [] if isinstance(ct, dict) else []
        hit = [e for e in ents if e.get("guid") == PROC or
               NAME_RX.search(e.get("name") or "")]
        print(f"  casts table: {len(ents)} abilities; matching: "
              + ", ".join(f"{e.get('guid')} {e.get('name')} x{e.get('total')}" for e in hit))
        cands = [a for a in auras if NAME_RX.search(a.get("name") or "")
                 and a.get("guid") not in (1250527,)]
        for a in cands[:3]:
            followups.append((i, c, f, aid, a.get("guid"), a.get("name"),
                              casts, t0, t1))
    if not followups:
        print("\n[diag] no aura name matched the beam; pick the guid by hand "
              "from the list above")
        return
    parts = [f'a{n}: report(code: "{c}") {{ b: table(fightIDs: [{f}], '
             f'dataType: Buffs, targetID: {aid}, abilityID: {g}) }}'
             for n, (i, c, f, aid, g, *_r) in enumerate(followups)]
    rd = client.query("{ reportData { " + " ".join(parts) + " } }",
                      est_cost=2.0 * len(parts)).get("reportData") or {}
    for n, (i, c, f, aid, g, name, casts, t0, t1) in enumerate(followups):
        bt = ((rd.get(f"a{n}") or {}).get("b") or {}).get("data") or {}
        auras = bt.get("auras") or [] if isinstance(bt, dict) else []
        bands = []
        for a in auras:
            for b in a.get("bands") or []:
                bands.append([b["startTime"], b["endTime"]])
        buff = union(bands)
        avail = union([[t, min(t + BEAM_MS, t1)] for t in casts]) if casts else []
        bl = sum(b - a for a, b in buff)
        al = sum(b - a for a, b in avail)
        il = inter_len(buff, avail) if avail else 0
        print(f"\n[diag] {c}#{f} aura {g} '{name}': {len(bands)} bands, "
              f"buff {bl / 1000:.1f}s; beams {len(casts)} x {BEAM_MS / 1000:.0f}s -> "
              f"available {al / 1000:.1f}s (union); buff∩available {il / 1000:.1f}s; "
              f"benefit ratio {100 * il / al if al else float('nan'):.1f}%; "
              f"classic uptime {100 * bl / (t1 - t0):.1f}%")
        if bands and casts:
            # do buff windows start at beam times? (sanity: the buff should
            # never begin before a beam exists)
            early = sum(1 for a, _ in bands if not any(t <= a <= t + BEAM_MS for t in casts))
            print(f"  buff bands that start outside any beam window: {early}/{len(bands)}")
    print(f"\n[diag] points spent this hour: {client.spent:.0f}/{client.limit:.0f}")


if __name__ == "__main__":
    picks, wearers = part1()
    if not picks:
        sys.exit("[diag] no dated wearer fights")
    print(f"[diag] newest wearer fights: {picks}")
    part2(picks, wearers)
