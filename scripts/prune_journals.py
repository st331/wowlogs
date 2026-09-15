#!/usr/bin/env python3
"""Prune the collection journals to the retention window (owner, 2026-09-14).

    "don't keep data for longer than 2 weeks ... it is fine to keep a day or
    two extra of data."

WHAT IT KEEPS. Every run whose players row is DATED on or after the cut, plus
every run that cannot be dated (undated / implausible start, torn line, no
players row at all). The cut is RETENTION_DISK_DAYS back from the NEWEST
PLAUSIBLE ROW IN players.jsonl -- the same data anchor the builder and the
fetch window use -- never from the wall clock: a stalled collector freezes
this prune instead of eating rows the page still publishes, and because the
builder's ceiling is measured from the wall clock (15 d) while this is
measured from the newest row (16 d, newest <= now), disk is strictly wider
than the page in every case.

WHAT IT TOUCHES, BY NAME (never data/processed/* by glob):
  players.jsonl      the ONLY dated journal -> the date oracle; rewritten LAST
  gear.jsonl         undated; pruned by run key against the drop set
  procs.jsonl        undated; same
  procs_failed.txt   "code:fid:character\\t..."; same, by the code:fid prefix
  discovered.jsonl   dated by start_time, or by first_seen (SECONDS) when undated
EXEMPT: summaries_done.txt (no date, the only thing that stops an all-season
board re-buying a refused run; 0.2% of the cache), rankings.jsonl (--resweep
rewrites it every run), keystone_times.json (export() prunes it to the runs it
exports), rio_scores.csv.gz (fetch_rio prunes it on save).

DELETE ONLY ON PROOF OF AGE. Every non-players journal is pruned by
membership in the DROP set (runs positively dated before the cut), never by
absence from a keep set: a line whose key cannot be parsed, or whose run has
no players row, is kept. A parser bug can therefore make this prune do
nothing; it can never make it delete everything.

CRASH-ATOMIC, NO-OP UNTOUCHED. Each file is streamed to <name>.prune.tmp in
the same directory, fsynced and os.replace()d. A journal with nothing to drop
is not rewritten at all (same inode), so the byte-offset checkpoints over
gear.jsonl (build_site_data's trait union, fetch_names' scan) only rebuild on
a run that actually removed something -- their head/tail hashes detect the
rewrite and rebuild from the pruned file, which is the CORRECT answer.
Non-players journals are rewritten before players.jsonl, so a crash between
files leaves them a SUBSET of what players still describes, never the other
way round.

REFUSALS (print, write the state, delete nothing, exit 0):
  * players.jsonl missing or empty (cold start, tripped guard);
  * the anchor is more than MAX_STALL_DAYS behind the wall clock (a stall that
    long is a human decision, not a cron's);
  * the drop set is more than MAX_DROP_SHARE of the dated runs (a date parser
    regression looks exactly like "everything is old").

CADENCE. State in data/processed/prune_state.json. The first ever invocation
is a DRY RUN (counts only); the next invocation applies; after that it applies
at most every MIN_INTERVAL_H hours (--force overrides). The state file is what
the builder folds into payload.retention.disk and the health channel, so the
page can say what disk holds; retention.txt beside it is the same for humans
and for the journal guard's log line.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from retention import RETENTION_DISK_DAYS, DAY_MS, DATED, UNDATED, date_class, iso  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "data" / "processed"

MIN_INTERVAL_H = 20.0
MAX_STALL_DAYS = 30          # anchor this far behind the wall clock -> refuse
MAX_DROP_SHARE = 0.85        # dated runs that would leave -> refuse

_RC = re.compile(rb'"report_code": "([^"]+)"')
_FID = re.compile(rb'"fight_id": (\d+)')
_ST = re.compile(rb'"started_at": (-?\d+(?:\.\d+)?)')

STATE = "prune_state.json"
NOTE = "retention.txt"


def _key(raw: bytes):
    """b'code:fid' from a players/gear/procs line, or None when unparseable."""
    m = _RC.search(raw)
    if not m:
        return None
    f = _FID.search(raw)
    if not f:
        return None
    return m.group(1) + b":" + f.group(1)


def scan_players(path: pathlib.Path, now_ms: float):
    """One pass: {key: newest dated start} and the set of keys with an
    undated/implausible row. A key with any DATED row is dated by its newest."""
    dated: dict[bytes, float] = {}
    undated: set[bytes] = set()
    lines = 0
    with path.open("rb") as fh:
        for raw in fh:
            lines += 1
            k = _key(raw)
            if k is None:
                continue
            m = _ST.search(raw)
            t = float(m.group(1)) if m else None
            if date_class(t, now_ms) == DATED:
                if k not in dated or t > dated[k]:
                    dated[k] = t
            else:
                undated.add(k)
    return dated, undated, lines


def rewrite(path: pathlib.Path, keep_line, dry: bool):
    """Stream `path` keeping lines for which keep_line(raw) is True.
    Returns (kept, dropped). Untouched when nothing is dropped."""
    if not path.exists():
        return 0, 0
    kept = dropped = 0
    tmp = path.with_name(path.name + ".prune.tmp")
    out = None if dry else tmp.open("wb")
    try:
        with path.open("rb") as fh:
            for raw in fh:
                if keep_line(raw):
                    kept += 1
                    if out is not None:
                        out.write(raw if raw.endswith(b"\n") else raw + b"\n")
                else:
                    dropped += 1
        if out is not None:
            out.flush()
            os.fsync(out.fileno())
            out.close()
            out = None
            if dropped:
                os.replace(tmp, path)
            else:
                tmp.unlink(missing_ok=True)       # nothing to remove: same inode
    finally:
        if out is not None:
            out.close()
            tmp.unlink(missing_ok=True)
    return kept, dropped


def load_state(d: pathlib.Path) -> dict | None:
    try:
        return json.loads((d / STATE).read_text())
    except (OSError, ValueError):
        return None


def save_state(d: pathlib.Path, state: dict) -> None:
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (STATE + ".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    os.replace(tmp, d / STATE)
    lines = [f"retention: disk keeps the newest {state['days']} days measured from the newest row",
             f"mode: {state['mode']} at {state['at']}",
             f"anchor: {state.get('anchor')}  cut: {state.get('cut')}"]
    if state.get("refused"):
        lines.append(f"refused: {state['refused']}")
    for k, v in (state.get("counts") or {}).items():
        lines.append(f"{k}: {v}")
    (d / NOTE).write_text("\n".join(lines) + "\n")


def decide_mode(state: dict | None, now_s: float, force: bool, dry_flag: bool,
                min_interval_h: float) -> tuple[str, str]:
    """('dry'|'apply'|'skip', reason)."""
    if dry_flag:
        return "dry", "--dry-run"
    if force:
        return "apply", "--force"
    if not state:
        return "dry", "first invocation is a dry run"
    if state.get("mode") == "dry" and not state.get("refused"):
        return "apply", "the previous invocation was the dry run"
    last = float(state.get("at_s") or 0)
    if now_s - last >= min_interval_h * 3600:
        return "apply", f"{(now_s - last) / 3600:.1f} h since the last prune"
    return "skip", f"pruned {(now_s - last) / 3600:.1f} h ago (< {min_interval_h:g} h)"


def prune(d: pathlib.Path, now_s: float | None = None, days: int = RETENTION_DISK_DAYS,
          dry: bool = False, force: bool = False, min_interval_h: float = MIN_INTERVAL_H) -> dict:
    now_s = time.time() if now_s is None else now_s
    now_ms = now_s * 1000
    state = load_state(d)
    mode, why = decide_mode(state, now_s, force, dry, min_interval_h)
    out = {"v": 1, "days": days, "mode": mode, "why": why, "at": iso(now_ms), "at_s": now_s,
           "anchor": None, "cut": None, "counts": {}, "refused": None}
    if mode == "skip":
        print(f"[prune] skip: {why}", flush=True)
        # keep the previous decision on file; only refresh the note line
        if state:
            state["last_check"] = out["at"]
            save_state(d, state)
        return state or out

    players = d / "players.jsonl"
    if not players.exists() or players.stat().st_size == 0:
        out["refused"] = "players.jsonl missing or empty (cold start or tripped guard)"
        print(f"[prune] refused: {out['refused']}", flush=True)
        save_state(d, out)
        return out

    t0 = time.perf_counter()
    dated, undated, lines = scan_players(players, now_ms)
    if dated:
        anchor = min(max(dated.values()), now_ms)
    else:
        anchor = now_ms
    cut = anchor - days * DAY_MS
    out["anchor"], out["cut"] = iso(anchor), iso(cut)
    drop = {k for k, t in dated.items() if t < cut}
    n_dated = len(dated)
    stall_days = (now_ms - anchor) / DAY_MS
    counts = {"players_lines": lines, "runs_dated": n_dated,
              "runs_undated_kept": len(undated - set(dated)),
              "runs_to_drop": len(drop), "stall_days": round(stall_days, 2)}
    out["counts"] = counts
    if stall_days > MAX_STALL_DAYS:
        out["refused"] = (f"anchor {stall_days:.1f} days behind the wall clock "
                          f"(> {MAX_STALL_DAYS}): a stall that long is a human decision")
    elif n_dated and len(drop) / n_dated > MAX_DROP_SHARE:
        out["refused"] = (f"{len(drop):,} of {n_dated:,} dated runs would leave "
                          f"(> {MAX_DROP_SHARE:.0%}): a date parser regression looks exactly like this")
    if out["refused"]:
        print(f"[prune] refused: {out['refused']}", flush=True)
        save_state(d, out)
        return out
    print(f"[prune] {mode}: window {days} d back from {out['anchor']} -> cut {out['cut']}; "
          f"{len(drop):,} of {n_dated:,} dated runs are older ({len(undated - set(dated)):,} "
          f"undated runs kept); scanned {lines:,} players lines in {time.perf_counter() - t0:.1f}s "
          f"({why})", flush=True)
    dry_run = mode == "dry"

    def by_key(raw: bytes) -> bool:
        k = _key(raw)
        return k is None or k not in drop        # unparseable -> keep

    cut_s = cut / 1000

    def ledger_keep(raw: bytes) -> bool:
        try:
            rec = json.loads(raw)
        except ValueError:
            return True
        k = f"{rec.get('code')}:{rec.get('fid')}".encode()
        if k in drop:
            return False
        st = rec.get("start_time")
        cls = date_class(st, now_ms)
        if cls == DATED:
            return st >= cut
        fs = rec.get("first_seen")
        if isinstance(fs, (int, float)) and 0 < fs < cut_s:
            return False                          # first seen before the cut: proven old
        return True

    def failed_keep(raw: bytes) -> bool:
        head = raw.split(b"\t", 1)[0]
        parts = head.split(b":")
        if len(parts) < 2:
            return True
        return parts[0] + b":" + parts[1] not in drop

    # non-players journals FIRST (a crash leaves them a subset of players)
    for name, fn in (("gear.jsonl", by_key), ("procs.jsonl", by_key),
                     ("procs_failed.txt", failed_keep), ("discovered.jsonl", ledger_keep)):
        k, dr = rewrite(d / name, fn, dry_run)
        counts[f"{name}_kept"], counts[f"{name}_dropped"] = k, dr
        if (d / name).exists():
            print(f"[prune]   {name}: {'would drop' if dry_run else 'dropped'} {dr:,}, kept {k:,}", flush=True)
    k, dr = rewrite(players, by_key, dry_run)
    counts["players.jsonl_kept"], counts["players.jsonl_dropped"] = k, dr
    print(f"[prune]   players.jsonl: {'would drop' if dry_run else 'dropped'} {dr:,}, kept {k:,}", flush=True)
    counts["wall_s"] = round(time.perf_counter() - t0, 1)
    if not dry_run and any(counts.get(f"{n}_dropped") for n in ("gear.jsonl",)):
        print("[prune]   gear.jsonl was rewritten: the trait union and the names scan "
              "rebuild from the pruned file on the next build (their checkpoints detect it)",
              flush=True)
    save_state(d, out)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default=str(DEFAULT_DIR), help="journal directory (default data/processed)")
    ap.add_argument("--days", type=int, default=RETENTION_DISK_DAYS)
    ap.add_argument("--dry-run", action="store_true", help="count only; write nothing but the state")
    ap.add_argument("--force", action="store_true", help="apply now, ignoring the cadence")
    ap.add_argument("--min-interval-h", type=float, default=MIN_INTERVAL_H)
    ap.add_argument("--now", type=float, default=None, help="epoch seconds (tests)")
    a = ap.parse_args(argv)
    st = prune(pathlib.Path(a.dir), now_s=a.now, days=a.days, dry=a.dry_run,
               force=a.force, min_interval_h=a.min_interval_h)
    # the fetch step rewrote fetch_health earlier in the run; appending is safe
    # and the build folds it in (prune_state.json is the durable copy)
    try:
        with (pathlib.Path(a.dir) / "fetch_health.txt").open("a") as fh:
            fh.write(f"prune.mode={st.get('mode')}\nprune.cut={st.get('cut')}\n"
                     + (f"prune.refused={st.get('refused')}\n" if st.get("refused") else ""))
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
