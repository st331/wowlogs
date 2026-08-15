#!/usr/bin/env python3
"""Per-ability damage breakdown for PTR runs, for tuning-impact projection.

The main pipeline stores only each player's *total* damage, which is enough
for DPS but not for asking "what happens if Blizzard changes one ability by
12%".  This collector fills that gap:

  pass 1  one unfiltered `DamageDone` table per fight  -> actor ids + equipped
          gear (tier-set detection via setID) + per-player totals
  pass 2  one `DamageDone` table per PLAYER (sourceID) -> the FULL ability
          list.  The unfiltered table truncates `abilities` to the top 5;
          filtering by sourceID returns every ability and the totals then
          reconcile exactly to the player's damage done.

Journalled to data/raw/abilities_ptr.jsonl, one line per player per fight, so
re-running resumes.  Restricted to runs at or after the newest tuning patch.
"""
from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from wcl_client import WCLClient  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "data" / "mythic_runs_ptr.csv.gz"
OUT = ROOT / "data" / "raw" / "abilities_ptr.jsonl"
TUNING = ROOT / "data" / "tuning_patches.json"

FIGHT_BATCH = 6     # unfiltered tables aliased per HTTP request
PLAYER_BATCH = 12   # sourceID tables aliased per HTTP request
WORKERS = 4

_tls = threading.local()


def client() -> WCLClient:
    if not hasattr(_tls, "c"):
        _tls.c = WCLClient(verbose=False)
    return _tls.c


def _rows(table):
    """Table payloads come back as either a bare list or {entries: [...]}."""
    if isinstance(table, list):
        return [r for r in table if isinstance(r, dict)]
    if isinstance(table, dict):
        return [r for r in table.get("entries") or [] if isinstance(r, dict)]
    return []


def fight_pass(batch):
    """Unfiltered tables -> {(code, fid): [{id, name, type, total, sets}]}."""
    parts = [
        f'a{i}: report(code: "{c}") '
        f'{{ table(fightIDs: [{f}], dataType: DamageDone) }}'
        for i, (c, f) in enumerate(batch)
    ]
    data = client().query("{ reportData { " + " ".join(parts) + " } }",
                          est_cost=2.0 * len(batch))
    out = {}
    for i, (c, f) in enumerate(batch):
        rep = (data.get("reportData") or {}).get(f"a{i}") or {}
        tbl = ((rep.get("table") or {}).get("data")) if rep else None
        players = []
        for e in _rows(tbl):
            sets = {}
            for g in e.get("gear") or []:
                if isinstance(g, dict) and g.get("setID"):
                    sets[str(g["setID"])] = sets.get(str(g["setID"]), 0) + 1
            players.append({"id": e.get("id"), "name": e.get("name"),
                            "type": e.get("type"), "total": e.get("total", 0),
                            "ilvl": e.get("itemLevel"), "sets": sets})
        if players:
            out[(c, f)] = players
    return out


def player_pass(batch):
    """sourceID tables -> full ability lists. batch: [(code, fid, pid)]."""
    parts = [
        f'a{i}: report(code: "{c}") '
        f'{{ table(fightIDs: [{f}], dataType: DamageDone, sourceID: {p}) }}'
        for i, (c, f, p) in enumerate(batch)
    ]
    data = client().query("{ reportData { " + " ".join(parts) + " } }",
                          est_cost=1.6 * len(batch))
    out = {}
    for i, key in enumerate(batch):
        rep = (data.get("reportData") or {}).get(f"a{i}") or {}
        tbl = ((rep.get("table") or {}).get("data")) if rep else None
        ab = [{"guid": r.get("guid"), "name": r.get("name"),
               "total": r.get("total", 0), "uses": r.get("uses")}
              for r in _rows(tbl) if r.get("total")]
        if ab:
            out[key] = ab
    return out


def main() -> None:
    limit = None
    for i, a in enumerate(sys.argv):
        if a == "--limit":
            limit = int(sys.argv[i + 1])

    cut = pd.Timestamp(json.loads(TUNING.read_text())["patches"][0]
                       ["regions"]["default"]).value // 10 ** 6
    df = pd.read_csv(CSV)
    runs = (df[df["started_at"] >= cut]
            .groupby(["report_code", "fight_id"]).size().reset_index())
    want = [(c, int(f)) for c, f in zip(runs["report_code"], runs["fight_id"])]

    done = set()
    if OUT.exists():
        with OUT.open() as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    done.add((r["report_code"], r["fight_id"]))
                except Exception:
                    pass
    todo = [k for k in want if k not in done]
    if limit:
        todo = todo[:limit]
    print(f"[abilities] {len(want)} post-tuning runs, {len(want) - len(todo)} "
          f"already journalled, {len(todo)} to go", flush=True)
    if not todo:
        return

    OUT.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    n_done = [0]

    def work(batch):
        try:
            meta = fight_pass(batch)
        except Exception as e:                       # noqa: BLE001
            print(f"[abilities] fight batch failed: {e}", flush=True)
            return
        keys = [(c, f, p["id"]) for (c, f), ps in meta.items()
                for p in ps if p.get("id") is not None]
        abil = {}
        for i in range(0, len(keys), PLAYER_BATCH):
            chunk = keys[i:i + PLAYER_BATCH]
            try:
                abil.update(player_pass(chunk))
            except Exception as e:                   # noqa: BLE001
                print(f"[abilities] player batch failed: {e}", flush=True)
        lines = []
        for (c, f), ps in meta.items():
            for p in ps:
                lines.append(json.dumps({
                    "report_code": c, "fight_id": f, "actor_id": p["id"],
                    "name": p["name"], "class": p["type"], "total": p["total"],
                    "ilvl": p["ilvl"], "sets": p["sets"],
                    "abilities": abil.get((c, f, p["id"]), []),
                }, ensure_ascii=False))
        if not lines:                 # every fight in the batch came back
            return                    # empty (deleted/private report)
        with lock:
            with OUT.open("a") as fh:
                fh.write("\n".join(lines) + "\n")
            n_done[0] += len(meta)
            if n_done[0] % 30 < FIGHT_BATCH:
                print(f"[abilities] {n_done[0]}/{len(todo)} runs | "
                      f"{client().spent:.0f} pts this window", flush=True)

    batches = [todo[i:i + FIGHT_BATCH]
               for i in range(0, len(todo), FIGHT_BATCH)]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(work, batches))
    print(f"[abilities] done, {n_done[0]} runs journalled -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
