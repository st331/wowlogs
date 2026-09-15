#!/usr/bin/env python3
"""scripts/prune_journals.py -- the irreversible half of retention.

Pinned on a fixture directory with every journal it touches and the one it
must not, with an INJECTED clock (a prune that reads the wall clock cannot be
tested for idempotence):

  * the first invocation is a DRY RUN: counts, state, nothing rewritten;
  * the apply: runs dated before the cut leave players / gear / procs /
    procs_failed / the ledger TOGETHER; undated and implausible runs stay; a
    torn line and a line whose key cannot be parsed stay; a gear line whose
    run has no players row stays; summaries_done.txt is byte-identical;
  * the cut is anchored to the newest plausible ROW, not the clock: a future-
    dated row does not move it, and a stall freezes it;
  * fixed point: a second apply changes no byte; a journal with nothing to
    drop keeps its inode (no rewrite, so the byte-offset checkpoints hold);
  * the ledger's undated rows are bounded by first_seen in SECONDS;
  * refusals: no players journal; a cut that would take > 85% of dated runs;
    an anchor > 30 days behind the clock -- each writes the state, deletes
    nothing, and says why;
  * cadence: after an apply the next call within 20 h skips; --force applies.
"""
import json
import os
import pathlib
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import prune_journals as PJ    # noqa: E402
import retention as R          # noqa: E402

fails = 0
def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1

DAY = R.DAY_MS
NOW_S = time.time()
NOW = NOW_S * 1000
NEWEST = NOW - 1 * DAY
CUT = NEWEST - R.RETENTION_DISK_DAYS * DAY

def prow(code, fid, st, ch="A"):
    d = {"character": ch, "server": "S", "region": "US", "report_code": code, "fight_id": fid,
         "started_at": st, "dps": 1.0}
    return json.dumps(d)
def grow(code, fid, ch="A"):
    return json.dumps({"report_code": code, "fight_id": fid, "character": ch, "server": "S", "gear": []})

def build(d: pathlib.Path, future=True):
    runs = [("NEW", 1, NEWEST), ("IN", 2, CUT + 2 * DAY), ("EDGE", 3, CUT),
            ("OLD", 4, CUT - DAY), ("OLDER", 5, CUT - 20 * DAY),
            ("UND", 6, None), ("ZERO", 7, 0)] + ([("FUT", 8, NOW + 5 * DAY)] if future else [])
    with (d / "players.jsonl").open("w") as fh:
        for code, fid, st in runs:
            for ch in ("A", "B"):
                fh.write(prow(code, fid, st, ch) + "\n")
        # a run with one dated row and one undated row: dated by its newest, kept
        fh.write(prow("MIX", 9, CUT + DAY, "A") + "\n"); fh.write(prow("MIX", 9, None, "B") + "\n")
        fh.write(prow("TORN", 10, CUT - 5 * DAY)[:30])           # torn tail, no newline
    with (d / "gear.jsonl").open("w") as fh:
        for code, fid, _ in runs:
            fh.write(grow(code, fid) + "\n")
        fh.write(grow("ORPHAN", 99) + "\n")                       # no players row -> kept
        fh.write('{"garbage": true}\n')                            # unparseable key -> kept
    with (d / "procs.jsonl").open("w") as fh:
        for code, fid, _ in runs:
            fh.write(json.dumps({"v": 2, "report_code": code, "fight_id": fid, "character": "A"}) + "\n")
    with (d / "procs_failed.txt").open("w") as fh:
        fh.write("OLD:4:Some:Name\t221\treason\n"); fh.write("NEW:1:A\t221\treason\n"); fh.write("weird\n")
    old_seen = int((CUT - 3 * DAY) / 1000); new_seen = int(NOW_S)
    with (d / "discovered.jsonl").open("w") as fh:
        fh.write(json.dumps({"code": "OLD", "fid": 4, "start_time": CUT - DAY, "first_seen": old_seen}) + "\n")
        fh.write(json.dumps({"code": "NEW", "fid": 1, "start_time": NEWEST, "first_seen": new_seen}) + "\n")
        fh.write(json.dumps({"code": "LEDOLD", "fid": 20, "start_time": CUT - 2 * DAY, "first_seen": new_seen}) + "\n")
        fh.write(json.dumps({"code": "LEDUNDOLD", "fid": 21, "start_time": None, "first_seen": old_seen}) + "\n")
        fh.write(json.dumps({"code": "LEDUNDNEW", "fid": 22, "start_time": None, "first_seen": new_seen}) + "\n")
    (d / "summaries_done.txt").write_text("OLD:4\tOK\nNEW:1\tOK\nOLDER:5\tFAILED\tx\n")

def keys(path, field="report_code"):
    out = set()
    for line in path.read_text().splitlines():
        try:
            out.add(json.loads(line).get(field) or json.loads(line).get("code"))
        except ValueError:
            out.add("<torn>")
    return out

with tempfile.TemporaryDirectory() as tmp:
    d = pathlib.Path(tmp); build(d)
    before = {n: (d / n).read_bytes() for n in ("players.jsonl", "gear.jsonl", "procs.jsonl", "procs_failed.txt", "discovered.jsonl", "summaries_done.txt")}
    done_ino = (d / "summaries_done.txt").stat().st_ino

    # ---- 1. first invocation: dry run
    st = PJ.prune(d, now_s=NOW_S)
    check(st["mode"] == "dry" and st["refused"] is None, f"first invocation is a dry run ({st['why']})")
    check(all((d / n).read_bytes() == b for n, b in before.items()), "the dry run rewrites nothing")
    check(st["counts"]["runs_to_drop"] == 2 and st["counts"]["players.jsonl_dropped"] == 4,
          f"…but counts what it would drop (OLD, OLDER; the torn tail has no key and is never counted): "
          f"{st['counts']['runs_to_drop']} runs / {st['counts']['players.jsonl_dropped']} rows")
    check(st["anchor"] == R.iso(NEWEST) and st["cut"] == R.iso(CUT),
          f"anchored on the newest PLAUSIBLE row (the future row does not move it): {st['anchor']}")
    check((d / "prune_state.json").exists() and (d / "retention.txt").exists(), "state + human note written")

    # ---- 2. the apply
    st = PJ.prune(d, now_s=NOW_S + 60)
    check(st["mode"] == "apply" and st["refused"] is None, f"second invocation applies ({st['why']})")
    pk = keys(d / "players.jsonl")
    check(pk == {"NEW", "IN", "EDGE", "UND", "ZERO", "FUT", "MIX", "<torn>"},
          f"players: OLD/OLDER left; edge, undated, 1970, future, mixed and the torn tail stay: {sorted(map(str, pk))}")
    gk = keys(d / "gear.jsonl")
    check(gk == {"NEW", "IN", "EDGE", "UND", "ZERO", "FUT", "ORPHAN", None},
          f"gear: pruned by run key; the orphan and the unparseable line stay: {sorted(map(str, gk))}")
    check(keys(d / "procs.jsonl") == {"NEW", "IN", "EDGE", "UND", "ZERO", "FUT"}, "procs: same run set")
    pf = (d / "procs_failed.txt").read_text()
    check("OLD:4:Some:Name" not in pf and "NEW:1:A" in pf and "weird" in pf,
          "procs_failed: OLD's marker left (code:fid prefix, colons in names tolerated), the rest stay")
    lk = keys(d / "discovered.jsonl")
    check(lk == {"NEW", "LEDUNDNEW"},
          f"ledger: dated-old rows and an undated row FIRST SEEN before the cut (seconds) leave; recent undated stays: {sorted(lk)}")
    check((d / "summaries_done.txt").read_bytes() == before["summaries_done.txt"]
          and (d / "summaries_done.txt").stat().st_ino == done_ino, "summaries_done.txt is EXEMPT: byte- and inode-identical")
    check(st["counts"]["gear.jsonl_dropped"] == 2 and st["counts"]["discovered.jsonl_dropped"] == 3,
          f"counters: gear dropped {st['counts']['gear.jsonl_dropped']}, ledger dropped {st['counts']['discovered.jsonl_dropped']}")
    check(not list(d.glob("*.prune.tmp")), "no temp files left behind")

    # ---- 3. fixed point + no-op untouched
    snap = {n: ((d / n).read_bytes(), (d / n).stat().st_ino) for n in ("players.jsonl", "gear.jsonl", "procs.jsonl", "discovered.jsonl")}
    st = PJ.prune(d, now_s=NOW_S + 120, force=True)
    check(st["mode"] == "apply" and all((d / n).read_bytes() == b for n, (b, _) in snap.items()), "a second apply changes no byte")
    check(all((d / n).stat().st_ino == i for n, (_, i) in snap.items()),
          "…and rewrites nothing (same inodes): the gear checkpoints are not invalidated by a no-op")

    # ---- 4. cadence
    st = PJ.prune(d, now_s=NOW_S + 3600)
    check(st.get("mode") == "apply" and "last_check" in st, "within 20 h the call SKIPS and leaves the last decision on file")
    st = PJ.prune(d, now_s=NOW_S + 21 * 3600)
    check(st["mode"] == "apply", "after 20 h it applies again")

    # ---- 5. a stall freezes the cut: the clock moves, the newest row does not
    # (without the future-dated run: ten days on it would be a legitimately
    # newer row and would move the anchor -- correct, but not this case)
    build(d, future=False); (d / "prune_state.json").unlink(); (d / "retention.txt").unlink()
    st = PJ.prune(d, now_s=NOW_S + 10 * 86400, force=True)
    check(st["cut"] == R.iso(CUT) and keys(d / "players.jsonl") >= {"IN", "EDGE"},
          f"ten days later with no new rows the cut is unchanged ({st['cut']}) and the edge run survives")

    # ---- 6. refusals
    build(d); (d / "prune_state.json").unlink()
    st = PJ.prune(d, now_s=NOW_S + 40 * 86400, force=True)
    check(st["refused"] and "human decision" in st["refused"] and keys(d / "players.jsonl") >= {"OLD", "OLDER"},
          f"an anchor 41 days behind the clock is refused, nothing deleted: {st['refused'][:60]}")
    # a journal whose rows are nearly all old (the shape of a parser regression)
    with (d / "players.jsonl").open("w") as fh:
        for i in range(20):
            fh.write(prow(f"R{i}", i, CUT - (i + 1) * DAY) + "\n")
        fh.write(prow("ONE", 99, NEWEST) + "\n")
    st = PJ.prune(d, now_s=NOW_S, force=True)
    check(st["refused"] and "regression" in st["refused"] and len(keys(d / "players.jsonl")) == 21,
          f"dropping 20 of 21 dated runs is refused: {st['refused'][:70]}")
    (d / "players.jsonl").unlink()
    st = PJ.prune(d, now_s=NOW_S, force=True)
    check(st["refused"] and "missing" in st["refused"] and (d / "gear.jsonl").exists(),
          "no players journal -> refused, gear untouched")

    # ---- 7. the CLI writes prune.* into fetch_health and exits 0
    build(d); (d / "prune_state.json").unlink(); (d / "fetch_health.txt").write_text("x=1\n")
    rc = PJ.main(["--dir", str(d), "--now", str(NOW_S), "--dry-run"])
    fh = (d / "fetch_health.txt").read_text()
    check(rc == 0 and "prune.mode=dry" in fh and "prune.cut=" in fh, "CLI: exit 0, prune.* appended to fetch_health")

print("FAILED" if fails else "PASS", f"({fails} failures)")
sys.exit(1 if fails else 0)
