#!/usr/bin/env python3
"""The collector's half of retention (scripts/retention.py; owner, 2026-09-14).

Pinned here, on the real functions over temp journals:

  * load_fights() refuses a listed run DATED before the disk window (flat
    RETENTION_DISK_DAYS back from the newest plausible listed start -- the
    DATA anchor, like the builder), keeps UNDATED and IMPLAUSIBLE entries and
    counts the three apart;
  * merge_ledger() never date-filters its dedupe set (an old run the boards
    still list is NOT re-appended), drops a ledger-only row that is dated
    before the window, bounds an undated ledger row by first_seen in SECONDS,
    keeps the rest, and refuses to append an out-of-window run handed to it;
  * backlog_size() inherits the window;
  * release_failed() HOLDS a matching marker whose run is outside the window
    (or unknown to the snapshot) and releases the rest;
  * regear_candidates() never re-opens a run past the window;
  * export() writes a seed that holds only the window (undated rows kept), and
    prunes the keystone map to the union of the frame's runs and the listed
    runs -- or refuses when the key overlap is implausible;
  * seed_from_csv() prints the span it seeded.
"""
import gzip
import json
import pathlib
import re
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fetch_data as fd          # noqa: E402
import retention as R            # noqa: E402

fails = 0
def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1

DAY = R.DAY_MS
NOW = int(time.time() * 1000)
NEWEST = NOW - 2 * DAY                   # the newest listed run: two days ago (a stall)
CUT = NEWEST - R.RETENTION_DISK_DAYS * DAY

def entry(code, fid, st, region="US", score=300.0):
    r = {"report": {"code": code, "fightID": fid}, "score": score, "medal": "gold",
         "duration": 1_500_000, "bracketData": 12, "affixes": [9],
         "server": {"region": region} if region else None}
    if st is not None:
        r["startTime"] = st
    return r

with tempfile.TemporaryDirectory() as tmp:
    tp = pathlib.Path(tmp)
    fd.ROOT = tp; fd.RAW = tp / "raw"; fd.PROCESSED = tp / "processed"
    fd.RAW.mkdir(); fd.PROCESSED.mkdir()
    fd.RANKINGS_FILE = fd.RAW / "rankings.jsonl"
    fd.LEDGER_FILE = fd.PROCESSED / "discovered.jsonl"
    fd.SUMMARIES_DONE = fd.PROCESSED / "summaries_done.txt"
    fd.PLAYERS_FILE = fd.PROCESSED / "players.jsonl"
    fd.GEAR_FILE = fd.PROCESSED / "absent_gear.jsonl"
    fd.CSV_FILE = tp / "mythic_runs.csv.gz"

    # ---- a leaderboard page: newest, inside, on the edge, old, undated, 1970, future
    rankings = [entry("NEW", 1, NEWEST), entry("IN", 2, CUT + DAY),
                entry("EDGE", 3, CUT), entry("OLD", 4, CUT - DAY),
                entry("UND", 5, None), entry("ZERO", 6, 0),
                entry("FUT", 7, NOW + 3 * DAY, region=""), entry("ANC", 8, NEWEST, region="")]
    rankings[7]["report"] = {}          # anonymous: no code
    fd.RANKINGS_FILE.write_text(json.dumps({"enc": 1, "bracket": 11, "page": 1, "more": False,
                                            "rankings": rankings}) + "\n")
    fights = fd.load_fights(None)
    check(set(fights) == {"NEW:1", "IN:2", "EDGE:3", "UND:5", "ZERO:6", "FUT:7"},
          f"snapshot keeps in-window, edge, undated, implausible; refuses OLD: {sorted(fights)}")
    check(fd.load_fights.out_of_window == 1 and fd.load_fights.undated == 1 and fd.load_fights.implausible == 2,
          f"counted apart: refused {fd.load_fights.out_of_window}, undated {fd.load_fights.undated}, implausible {fd.load_fights.implausible}")
    check(abs(fd.load_fights.anchor_ms - NEWEST) < 1 and abs(fd.load_fights.cut_ms - CUT) < 1,
          f"anchored on the newest PLAUSIBLE start (the future row does not move it): anchor {R.iso(fd.load_fights.anchor_ms)}")
    check(fd.load_fights.anon_skipped == 1, "anonymous entries still counted")

    # ---- the ledger: dedupe set whole, work set windowed
    old_seen = int((CUT - 5 * DAY) / 1000)                    # first_seen is SECONDS
    ledger = [{"code": "LOLD", "fid": 1, "start_time": CUT - 3 * DAY, "first_seen": old_seen, "region": "US"},
              {"code": "LIN", "fid": 2, "start_time": CUT + 2 * DAY, "first_seen": int(NOW / 1000), "region": "EU"},
              {"code": "LUNDOLD", "fid": 3, "start_time": None, "first_seen": old_seen, "region": ""},
              {"code": "LUNDNEW", "fid": 4, "start_time": None, "first_seen": int((NOW - DAY) / 1000), "region": ""},
              {"code": "OLD", "fid": 4, "start_time": CUT - DAY, "first_seen": old_seen, "region": "US"}]
    fd.LEDGER_FILE.write_text("".join(json.dumps(r) + "\n" for r in ledger))
    out = fd.merge_ledger(dict(fights), None)
    check("LOLD:1" not in out and "OLD:4" not in out, "ledger rows dated before the window leave the work set")
    check("LIN:2" in out, "a ledger row inside the window stays pending")
    check("LUNDOLD:3" not in out and "LUNDNEW:4" in out,
          "an undated ledger row is bounded by first_seen (seconds): old first-seen leaves, recent stays")
    check(fd.merge_ledger.dropped_old == 3 and fd.merge_ledger.undated_kept == 1,
          f"counters: dropped_old {fd.merge_ledger.dropped_old}, undated_kept {fd.merge_ledger.undated_kept}")
    n_lines = sum(1 for _ in open(fd.LEDGER_FILE))
    check(n_lines == len(ledger) + 6, f"every snapshot run was appended once ({n_lines - len(ledger)} new)")
    fd.merge_ledger(dict(fights), None)
    check(sum(1 for _ in open(fd.LEDGER_FILE)) == n_lines, "a second merge appends nothing (dedupe set is NOT windowed)")
    # a hand-built out-of-window run is refused at the append gate
    fd.merge_ledger({"HAND:9": {"code": "HAND", "fid": 9, "start_time": CUT - 10 * DAY, "region": "US"}}, None)
    check("HAND" not in fd.LEDGER_FILE.read_text(), "an out-of-window run handed to merge_ledger is not appended")

    # ---- backlog inherits
    fd.SUMMARIES_DONE.write_text("NEW:1\tOK\n")
    fd.load_done.cache = None
    bl = fd.backlog_size(None)
    check(bl == len(fd.dedupe_fights(fd.merge_ledger(fd.load_fights(None), None))) - 1,
          f"backlog_size counts only in-window, not-done runs ({bl})")

    # ---- release_failed holds what the window excludes
    fd.SUMMARIES_DONE.write_text("NEW:1\tFAILED\tparse_summary() takes 3\n"
                                 "OLD:4\tFAILED\tparse_summary() takes 3\n"
                                 "GONE:9\tFAILED\tparse_summary() takes 3\n"
                                 "IN:2\tOK\n")
    pat = re.compile(r"parse_summary\(\) takes")
    started = {k: f.get("start_time") for k, f in fd.load_fights(None).items()}
    n = fd.release_failed(fd.SUMMARIES_DONE, pat, started=started, cutoff_ms=fd.load_fights.cut_ms)
    left = fd.SUMMARIES_DONE.read_text()
    check(n == 1 and fd.release_failed.held == 2 and "NEW:1" not in left and "OLD:4" in left and "GONE:9" in left,
          f"released {n} (in window), held {fd.release_failed.held} (old / not in the snapshot)")
    check(fd.release_failed(fd.SUMMARIES_DONE, pat) == 2 and fd.release_failed.held == 0,
          "without a window the old behaviour is unchanged (everything matching is released)")

    # ---- regear never re-opens past the window
    fl = fd.load_fights(None)
    done = set(fl)
    cands = fd.regear_candidates(fl, done, 10, days=60)
    check(all(fl[k].get("start_time") is None or fl[k]["start_time"] >= fd.load_fights.cut_ms - 1 for k in cands)
          and "EDGE:3" in cands, f"regear candidates are clamped to the window: {sorted(cands)}")

    # ---- export(): the seed holds the window; the keystone map is pruned to live runs
    def prow(code, fid, st, ch="A"):
        return {"character": ch + code, "server": "Srv", "region": "US", "class": "Mage", "spec": "Arcane",
                "hero_talent": "H", "role": "DPS", "dungeon": "Halls", "key_level": 12, "duration_s": 1500.0,
                "damage_done": 1, "dps": 1.0, "deaths": 0, "item_level": 700, "score": 300.0, "medal": "gold",
                "affixes": "9", "report_code": code, "fight_id": fid, "started_at": st,
                "set_pieces": "", "set_id": "", "set_counts": ""}
    rows = [prow("NEW", 1, NEWEST), prow("IN", 2, CUT + DAY), prow("EDGE", 3, CUT),
            prow("OLD", 4, CUT - DAY), prow("OLDER", 9, CUT - 20 * DAY), prow("UND", 5, None),
            prow("ZERO", 6, 0)]
    fd.PLAYERS_FILE.write_text("".join(json.dumps(r) + "\n" for r in rows))
    ks_file = tp / "data" / "keystone_times.json"; ks_file.parent.mkdir()
    ks_seed = {"NEW:1": 1000.0, "OLD:4": 900.0, "OLDER:9": 800.0, "STRAY:77": 5.0, "IN:2": 950.0}
    ks_file.write_text(json.dumps(ks_seed))
    import os; os.environ["EXPORT_GEAR"] = "0"
    fd._OUTPUTS.clear()
    fd.export()
    import pandas as pd
    csv = pd.read_csv(fd.CSV_FILE)
    kept = set(csv["report_code"])
    check(kept == {"NEW", "IN", "EDGE", "UND", "ZERO"},
          f"the seed holds the window, the edge, the undated and the 1970 row; OLD/OLDER leave: {sorted(kept)}")
    health = (fd.PROCESSED / "fetch_health.txt").read_text()
    check("export.retention_rows_dropped=2" in health and "export.retention_undated_kept=2" in health
          and f"export.retention_cut={R.iso(CUT)}" in health,
          "the export says what it dropped, kept and where it cut")
    ks_after = json.loads(ks_file.read_text())
    check(set(ks_after) >= {"NEW:1", "IN:2"} and "OLDER:9" not in ks_after and "STRAY:77" not in ks_after,
          f"keystone map pruned to live runs (frame ∪ listed); stray and older keys gone: {sorted(ks_after)}")
    check("OLD:4" not in ks_after or "OLD:4" in {f"{c}:{f}" for (c, f) in {(k.split(':')[0], int(k.split(':')[1])) for k in ks_after}},
          "OLD:4 is neither in the frame nor listed by the (windowed) snapshot, so it left the map")
    check("export.keystone_pruned=" in health, "…and the prune is on the health channel")
    # refusal: a map whose keys cannot match the frame is left alone
    ks_file.write_text(json.dumps({"x:1.0": 1.0, "y:2.0": 2.0, "z:3.0": 3.0}))
    (fd.PROCESSED / "keystone_times.json").unlink(missing_ok=True)
    fd.RANKINGS_FILE = fd.RAW / "absent.jsonl"     # no listed runs to refill the map from
    fd._OUTPUTS.clear(); fd.export()
    fd.RANKINGS_FILE = fd.RAW / "rankings.jsonl"
    check(set(json.loads(ks_file.read_text())) >= {"x:1.0", "y:2.0", "z:3.0"}
          and "export.keystone_prune=refused_low_overlap" in (fd.PROCESSED / "fetch_health.txt").read_text(),
          "an implausible overlap refuses the keystone prune and says so")

    # ---- seed_from_csv prints the span
    fd.PLAYERS_FILE.unlink(); fd.SUMMARIES_DONE.unlink(missing_ok=True)
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fd.seed_from_csv()
    check("oldest row" in buf.getvalue() and "oldest row" in buf.getvalue(),
          "seed_from_csv prints the span it seeded")

    # ---- the invariants the whole design rests on
    check(R.RETENTION_DISK_DAYS > R.RETENTION_MAX_AGE_DAYS >= 7 * R.RETENTION_RESETS + 1,
          f"disk ({R.RETENTION_DISK_DAYS}) > page ceiling ({R.RETENTION_MAX_AGE_DAYS}) >= two resets + 1")

print("FAILED" if fails else "PASS", f"({fails} failures)")
sys.exit(1 if fails else 0)
