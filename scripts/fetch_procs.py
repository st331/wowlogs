#!/usr/bin/env python3
"""Trinket beam benefit: per wearer-fight, how much of the beam's lifetime the
player actually spent inside it. Lightspire Core (item 250214) today.

Owner (2026-09-08): "of the time that the trinket was active, what percentage
of time did the player stay in the buff to get its effect ... the uptime of
the buff on the player as a percentage of when the buff was actually
available to the player." Not classic uptime.

WHAT THE LOG RECORDS (diag_lightspire.py / diag_lightspire2.py, 2026-09-08,
two fights, 50 procs): the equip aura 1250527 procs 1263762 "Radiant Light",
which creates an area trigger -- a 12 s beam, SpellDuration 29 -- and is
NEVER logged (0 events under it from any source). The beam applies 1263768
"Light's Blessing" to the wearer the instant it spawns: every logged cast of
1263768 coincides with an applybuff on the wearer to the millisecond (50 of
50), and the buff drops when the wearer steps out or the beam expires (bands
run 0.1 s .. 12.0 s, hard-capped at 12.0, no refreshbuff). So a buff band's
START is a beam spawn and the beam stays available 12 s from there:

    available = union over bands of [start, start + 12 s], clipped to the fight
    benefit   = |bands ∩ available| / |available|

Reference fights: 41.4 % (Marksmanship, 23 beams) and 35.9 % (Arcane, 27)
against classic uptimes of 8.2 % and 8.7 %. Overlapping beams merge in the
union, which is what "time the trinket was active" means.

COST: ONE buff-EVENTS sub-query per wearer-fight (applybuff/refreshbuff/
removebuff of the aura on the wearer, any source) plus the fight clock,
PROC_BATCH of them per request. Events, not the Buffs table. THE RECORD,
corrected 2026-09-08: run 827 (be9ab78) asked table(dataType: Buffs,
abilityID, targetID) -- the very shape diag pass 1 read 23/27 bands with --
and journaled 240 of 240 wearer-fights with zero bands; the difference was
this file's table parser keeping only auras whose guid == 1263768, and an
abilityID-filtered Buffs table does not key its entries by that guid. The
sourceID+targetID variant blamed at the time never ran in CI. Events
sidestep the table entirely and carry the source of every application,
which is what tells the wearer's own beams from a teammate's: a spawn is an
apply/refresh whose source is the wearer; the buff band counts whoever cast
it. Measured on run 829 (first events run): 192 wearer-fights for 430 points
-- 2.2 pts each blended with the masterData look-ups the old records need. Gear records written before 2026-09-08 carry no
actor id; those cost one masterData sub-query per REPORT on top, memoised
per run. The journal held ~73k wearer-fights on 2026-09-08 (7.6 % of
gear-known parses) and grows ~30k a week. Every run spends at most
--budget-pts points and --budget-s seconds, under the client's standing 70 %
ceiling, newest fights first, journals what it got and stops; the next run
continues.

FILES (data/processed -- they ride the journal cache between runs):
  procs.jsonl       one line per wearer-fight per tracked trinket: the bands
                    and the own-beam spawn times (ms from fight start) plus
                    the derived numbers, so a model change re-derives without
                    refetching. "v" is the record version: v1 (the table
                    query, always empty) is NOT done and is re-collected.
  procs_failed.txt  "code:fid:character\\tkey\\treason" -- never retried
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from wcl_client import WCLClient, QuotaDeadline          # noqa: E402
from fetch_data import (PROCESSED, GEAR_FILE, _iter_journal,   # noqa: E402
                        _repair_tail, load_fights, alias_error_map)

from procs_spec import TRACKED                                 # noqa: E402

PROCS_FILE = PROCESSED / "procs.jsonl"
PROCS_FAILED = PROCESSED / "procs_failed.txt"
PROC_BATCH = 12          # aliased buff-events sub-queries per request
SYSTEMIC_MIN = 20        # results held back before the systemic check
SYSTEMIC_SHARE = 0.5     # no-beam share at or above which the run is broken
ACTOR_BATCH = 10         # aliased masterData sub-queries per request
RECORD_V = 2             # journal record version; older records are redone
EVENT_LIMIT = 5000       # events per page; a 30-min fight has ~100


# --- the model ---------------------------------------------------------------
def union(iv):
    """Merge [a, b) intervals; returns sorted disjoint [a, b] pairs."""
    out: list[list[int]] = []
    for a, b in sorted((int(a), int(b)) for a, b in iv):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def inter_len(a, b) -> int:
    """Total overlap of two sorted disjoint interval lists."""
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


def benefit(bands, window_ms: int, fight_ms: int, spawns=None) -> dict:
    """The derived numbers for one wearer-fight.

    bands: [[start, end], ...] in ms from fight start -- the buff on the
    wearer, whoever's beam applied it. spawns: the wearer's OWN beam spawn
    times (an apply or refresh of the aura whose source is the wearer); when
    None, every band start is taken as a spawn (the pre-events model, kept
    for callers that only have bands). Returns n (beams), a (available ms),
    b (buff ms), i (buff inside available ms) and r (i / a, 4 dp) -- r is
    None when no beam ever spawned, which is "no evidence", never 0 %.
    """
    bb = union([[max(0, s), min(e, fight_ms)] for s, e in bands])
    starts = sorted(int(s) for s in (spawns if spawns is not None
                                     else [s for s, _ in bands]))
    avail = union([[max(0, s), min(s + window_ms, fight_ms)] for s in starts])
    a = sum(e - s for s, e in avail)
    b = sum(e - s for s, e in bb)
    i = inter_len(bb, avail)
    return {"n": len(starts), "a": a, "b": b, "i": i,
            "r": (round(i / a, 4) if a else None)}


def bands_from_events(events, aid: int, t0: int, t1: int):
    """(bands, spawns, foreign) from the aura's buff events on the wearer.

    bands: [[s, e], ...] ms from fight start, any source -- an apply opens,
    a refresh keeps it open, a remove closes; a band still open at the end
    closes at the fight end. spawns: apply/refresh timestamps whose source
    is the wearer (their own beams). foreign: apply/refresh events from any
    other source (a teammate's beam), kept as a count for the record.
    """
    bands, spawns, foreign = [], [], 0
    open_t = None
    for e in sorted((e for e in events if isinstance(e, dict)),
                    key=lambda e: e.get("timestamp") or 0):
        ty = e.get("type")
        ts = int(e.get("timestamp") or 0)
        if ty in ("applybuff", "refreshbuff", "applybuffstack"):
            if int(e.get("sourceID") or -1) == aid:
                spawns.append(max(0, min(ts, t1) - t0))
            else:
                foreign += 1
            if open_t is None:
                open_t = ts
        elif ty in ("removebuff",) and open_t is not None:
            bands.append([max(0, open_t - t0), max(0, min(ts, t1) - t0)])
            open_t = None
    if open_t is not None:
        bands.append([max(0, open_t - t0), max(0, t1 - t0)])
    return bands, sorted(spawns), foreign


# --- the work list -----------------------------------------------------------
def wearer_key(rec) -> tuple:
    return (rec.get("report_code"), int(rec.get("fight_id") or 0),
            rec.get("character"), rec.get("server") or "")


def candidates(tracked, gear_path=GEAR_FILE) -> dict[str, dict]:
    """{tracked key: {wearer key: {actor, class, spec}}} over the gear journal.

    One byte-level pass: a line is parsed only when it contains a tracked
    item id, then checked exactly. The last record for a wearer-fight wins,
    as in export_gear()."""
    needles = {t["key"]: str(t["item"]).encode() for t in tracked}
    items = {t["key"]: t["item"] for t in tracked}
    out: dict[str, dict] = {k: {} for k in needles}
    if not pathlib.Path(gear_path).exists():
        return out
    with open(gear_path, "rb") as fh:
        for raw in fh:
            hits = [k for k, nd in needles.items() if nd in raw]
            if not hits:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            gear = rec.get("gear") or []
            ids = {it.get("id") for it in gear if isinstance(it, dict)}
            for k in hits:
                if items[k] in ids:
                    out[k][wearer_key(rec)] = {
                        "actor": rec.get("actor"), "class": rec.get("class"),
                        "spec": rec.get("spec")}
    return out


def load_done(procs_path=PROCS_FILE, failed_path=PROCS_FAILED) -> dict[str, set]:
    """{tracked key: set of wearer keys} already journaled or failed."""
    done: dict[str, set] = {}
    for rec in _iter_journal(pathlib.Path(procs_path)):
        if int(rec.get("v") or 1) < RECORD_V:
            continue                    # an older model's record: redo it
        done.setdefault(rec.get("key"), set()).add(wearer_key(rec))
    p = pathlib.Path(failed_path)
    if p.exists():
        for line in p.read_text().splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            ident = parts[0].split(":")
            if len(ident) < 3:
                continue
            code, fid, ch = ident[0], ident[1], ":".join(ident[2:])
            try:
                fid = int(fid)
            except ValueError:
                continue
            done.setdefault(parts[1], set()).add((code, fid, ch, ""))
    return done


def _done_has(done: set, key: tuple) -> bool:
    # the failed file cannot carry a server (tabs and colons in names are
    # rarer than a null server); match it on the first three fields
    return key in done or (key[0], key[1], key[2], "") in done


def order_pending(keys, fights: dict) -> list[tuple]:
    """Newest fight first; fights the sweep no longer lists go last."""
    def st(k):
        f = fights.get(f"{k[0]}:{k[1]}")
        return -(f.get("start_time") or 0) if f else 0
    return sorted(keys, key=lambda k: (st(k), k[0], k[1], k[2]))


# --- the API -----------------------------------------------------------------
def resolve_actors(client: WCLClient, codes: list[str]) -> dict[str, dict]:
    """{report code: {name: id, (name, server): id}} via masterData."""
    out: dict[str, dict] = {}
    for i in range(0, len(codes), ACTOR_BATCH):
        chunk = codes[i:i + ACTOR_BATCH]
        q = " ".join(f'a{j}: report(code: "{c}") {{ masterData {{ '
                     f'actors(type: "Player") {{ id name server }} }} }}'
                     for j, c in enumerate(chunk))
        data = client.query("{ reportData { " + q + " } }",
                            est_cost=1.0 * len(chunk))
        rd = data.get("reportData") or {}
        for j, c in enumerate(chunk):
            m: dict = {}
            actors = (((rd.get(f"a{j}") or {}).get("masterData") or {})
                      .get("actors")) or []
            names: dict[str, list] = {}
            for a in actors:
                if not isinstance(a, dict) or a.get("id") is None:
                    continue
                m[(a.get("name"), a.get("server") or "")] = a["id"]
                names.setdefault(a.get("name"), []).append(a["id"])
            for nm, ids in names.items():
                if len(ids) == 1:
                    m[nm] = ids[0]
            out[c] = m
    return out


def actor_of(amap: dict, key: tuple):
    return amap.get((key[2], key[3])) or amap.get(key[2])


def fetch_batch(client: WCLClient, t: dict, batch: list[tuple[tuple, int]],
                after: dict | None = None):
    """[(wearer key, actor id)] -> (reportData, alias error map).

    Per wearer-fight: the fight's clock and the aura's buff events ON the
    wearer from any source (the source is on each event). `after` maps a
    wearer key to a page cursor for the rare fight with more than
    EVENT_LIMIT events."""
    parts = []
    for i, (k, aid) in enumerate(batch):
        st = f', startTime: {after[k]}' if after and k in after else ""
        parts.append(f'a{i}: report(code: "{k[0]}") {{ '
                     f'fights(fightIDs: [{k[1]}]) {{ startTime endTime }} '
                     f'ev: events(fightIDs: [{k[1]}], dataType: Buffs, '
                     f'abilityID: {t["buff"]}, targetID: {aid}, '
                     f'limit: {EVENT_LIMIT}{st}) {{ data nextPageTimestamp }} }}')
    data = client.query("{ reportData { " + " ".join(parts) + " } }",
                        est_cost=0.5 * len(parts))
    return data.get("reportData") or {}, alias_error_map(data.get("_errors"))


def parse_node(node, aid: int, buff: int):
    """(t0, t1, events, next page) from one alias; ValueError when unusable."""
    if not isinstance(node, dict):
        raise ValueError("no node")
    fights = node.get("fights") or []
    ev = node.get("ev")
    if not fights or not isinstance(fights[0], dict) or not isinstance(ev, dict):
        raise ValueError("no fight or events")
    t0, t1 = int(fights[0].get("startTime") or 0), int(fights[0].get("endTime") or 0)
    if t1 <= t0:
        raise ValueError("no fight clock")
    data = ev.get("data")
    if not isinstance(data, list):
        raise ValueError("no events data")
    return t0, t1, data, ev.get("nextPageTimestamp")


def run(budget_pts: float, budget_s: float, limit: int | None,
        tracked=TRACKED, gear_path=GEAR_FILE, procs_path=PROCS_FILE,
        failed_path=PROCS_FAILED, client: WCLClient | None = None) -> dict:
    t_start = time.monotonic()
    deadline = t_start + budget_s
    cands = candidates(tracked, gear_path)
    done = load_done(procs_path, failed_path)
    fights = load_fights(None)
    pathlib.Path(procs_path).parent.mkdir(parents=True, exist_ok=True)
    _repair_tail(pathlib.Path(procs_path))
    summary: dict = {}
    out_fh = open(procs_path, "a")
    fail_fh = open(failed_path, "a")
    try:
        for t in tracked:
            allk = cands[t["key"]]
            dn = done.get(t["key"], set())
            pending = order_pending([k for k in allk if not _done_has(dn, k)], fights)
            print(f"[procs] {t['name']}: {len(allk):,} wearer-fights in the gear "
                  f"journal, {len(allk) - len(pending):,} done, "
                  f"{len(pending):,} pending", flush=True)
            if limit:
                pending = pending[:limit]
            s = summary[t["key"]] = {"total": len(allk), "pending": len(pending),
                                     "ok": 0, "failed": 0, "transient": 0,
                                     "stopped": ""}
            if not pending:
                continue
            if client is None:
                client = WCLClient(verbose=True)
            # points USED this run, rollover-safe: client.spent is the hour's
            # running total and drops to ~0 when the window rolls over
            # mid-run, so spent - spent0 would go negative and the point
            # budget would never stop the loop. Only positive deltas count.
            used, last = 0.0, client.spent

            def tick():
                nonlocal used, last
                used += max(0.0, client.spent - last)
                last = client.spent

            # systemic stop: a broken query (run 827's table parser) journals
            # "no beam" for everyone and burns the budget doing it. The first
            # SYSTEMIC_MIN results are held back; if half or more of them
            # have no beam, nothing is journaled, the run warns and stops,
            # and the fights stay pending for a fixed collector.
            held: list[str] = []
            fetched = zero = 0
            systemic = False
            amaps: dict[str, dict] = {}
            i = 0
            while i < len(pending):
                if time.monotonic() >= deadline:
                    s["stopped"] = f"time budget {budget_s:.0f}s"
                    break
                if used >= budget_pts:
                    s["stopped"] = f"point budget {budget_pts:.0f}"
                    break
                chunk = pending[i:i + PROC_BATCH]
                i += PROC_BATCH
                try:
                    need = sorted({k[0] for k in chunk
                                   if not allk[k].get("actor") and k[0] not in amaps})
                    if need:
                        amaps.update(resolve_actors(client, need))
                        tick()
                    batch = []
                    for k in chunk:
                        aid = allk[k].get("actor") or actor_of(amaps.get(k[0], {}), k)
                        if aid is None:
                            fail_fh.write(f"{k[0]}:{k[1]}:{k[2]}\t{t['key']}\t"
                                          f"actor not resolved\n")
                            s["failed"] += 1
                            continue
                        batch.append((k, int(aid)))
                    if not batch:
                        continue
                    rd, errs = fetch_batch(client, t, batch)
                    tick()
                except QuotaDeadline as e:
                    s["stopped"] = f"quota: {e}"
                    break
                except RuntimeError as e:
                    print(f"[procs] request failed, left for the next run: {e}",
                          flush=True)
                    s["transient"] += len(chunk)
                    continue
                for j, (k, aid) in enumerate(batch):
                    node = rd.get(f"a{j}")
                    try:
                        t0, t1, events, nxt = parse_node(node, aid, t["buff"])
                        # the rare fight with more events than one page:
                        # follow the cursor, at most three more pages
                        pages = 0
                        while nxt and pages < 3:
                            pages += 1
                            rd2, _e2 = fetch_batch(client, t, [(k, aid)], {k: nxt})
                            _t0, _t1, more, nxt = parse_node(rd2.get("a0"), aid, t["buff"])
                            events = events + more
                    except ValueError:
                        msg = errs.get(f"a{j}", "")
                        if msg:
                            fail_fh.write(f"{k[0]}:{k[1]}:{k[2]}\t{t['key']}\t"
                                          f"{msg[:100]}\n")
                            s["failed"] += 1
                        else:
                            s["transient"] += 1
                        continue
                    except (QuotaDeadline, RuntimeError):
                        s["transient"] += 1
                        continue
                    bands, spawns, foreign = bands_from_events(events, aid, t0, t1)
                    fight_ms = t1 - t0
                    rec = {"v": RECORD_V, "report_code": k[0], "fight_id": k[1],
                           "character": k[2], "server": k[3] or None,
                           "key": t["key"], "actor": aid, "f": fight_ms,
                           "bands": bands, "sp": spawns, "x": foreign}
                    rec.update(benefit(bands, t["window_ms"], fight_ms, spawns))
                    fetched += 1
                    if not spawns:
                        zero += 1
                    line = json.dumps(rec, ensure_ascii=False) + "\n"
                    if held is not None:
                        held.append(line)
                    else:
                        out_fh.write(line)
                    s["ok"] += 1
                if held is not None and fetched >= SYSTEMIC_MIN:
                    if zero >= SYSTEMIC_SHARE * fetched:
                        systemic = True
                        s["ok"] -= len(held)
                        held = None
                        s["stopped"] = (f"systemic: {zero} of {fetched} fetched had "
                                        f"no beam -- nothing journaled")
                        print(f"::warning::trinket beam collector: {s['stopped']}; "
                              f"the query or the actor ids are wrong, not the "
                              f"players", flush=True)
                        break
                    out_fh.writelines(held)
                    held = None
                out_fh.flush()
                fail_fh.flush()
            if held:                       # fewer than SYSTEMIC_MIN this run
                if fetched and zero >= SYSTEMIC_SHARE * fetched and fetched >= 4:
                    s["ok"] -= len(held)
                    s["stopped"] = (f"systemic: {zero} of {fetched} fetched had no "
                                    f"beam -- nothing journaled")
                    print(f"::warning::trinket beam collector: {s['stopped']}",
                          flush=True)
                else:
                    out_fh.writelines(held)
                out_fh.flush()
            s["points"] = round(used)
            s["nobeam"] = zero
            print(f"[procs] {t['name']}: +{s['ok']:,} journaled, {s['failed']:,} "
                  f"failed permanently, {s['transient']:,} left for the next run, "
                  f"{s['points']:,} points, {time.monotonic() - t_start:.0f}s"
                  + (f"; stopped: {s['stopped']}" if s["stopped"] else ""),
                  flush=True)
    finally:
        out_fh.close()
        fail_fh.close()
    # the run's story for build_health.txt: the build folds fetch_health.txt
    # in with a "fetch." prefix, so these read fetch.procs.lscore.ok=192
    try:
        with (pathlib.Path(procs_path).parent / "fetch_health.txt").open("a") as fh:
            for key, sm in summary.items():
                for f in ("total", "pending", "ok", "failed", "transient", "nobeam",
                          "points", "stopped"):
                    if f in sm and sm[f] != "":
                        fh.write(f"procs.{key}.{f}={sm[f]}\n")
    except OSError:
        pass
    return summary


def status(tracked=TRACKED) -> None:
    cands = candidates(tracked)
    done = load_done()
    for t in tracked:
        allk = cands[t["key"]]
        dn = done.get(t["key"], set())
        n_done = sum(1 for k in allk if _done_has(dn, k))
        print(f"[procs] {t['name']}: {len(allk):,} wearer-fights, {n_done:,} "
              f"collected ({100 * n_done / max(1, len(allk)):.0f}%)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--budget-pts", type=float, default=400,
                    help="points this run may spend (default 400)")
    ap.add_argument("--budget-s", type=float, default=240,
                    help="wall-clock seconds this run may spend (default 240)")
    ap.add_argument("--limit", type=int, default=None,
                    help="at most this many wearer-fights (tests)")
    ap.add_argument("--status", action="store_true", help="report and exit")
    args = ap.parse_args(argv)
    if args.status:
        status()
        return 0
    run(args.budget_pts, args.budget_s, args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
