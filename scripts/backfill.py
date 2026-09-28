#!/usr/bin/env python3
"""Backfill mode: bundle the trailing two weeks' unbundled runs, most useful
first, at the whole hourly budget.

Owner, 2026-09-27: "use the next 18 hours to completely use up my API limit
and backfill all the data you need. use only the last 2 weeks of data. try
to get everything done as quickly as possible. ignore quota rules."

Switched on by data/backfill.json (execution.backfill_mode): fetch_data.main
sets the governor's ceiling to the switch's share of the limit and a
wall-clock deadline, the quota gate admits every newly swept run, and after
the ordinary sweep it calls run() here.

SELECTION (the budget buys ~40,000 of the window's ~100,000 runs, so order
is everything). From the players frame the collector already has (the
journals, seeded from data/mythic_runs.csv.gz, last copy of a row wins):
runs started in the trailing WINDOW_DAYS, key level LEVEL_MIN and up, with
no bundled row (exec != 1) and no FAILED / EMPTY marker from an earlier
attempt. Greedy and ONLINE -- the counters of bundled rows per band cell
(spec|dungeon|b<band>) and per exact cell (spec|dungeon|level) start from
the frame's bundled rows and grow as runs are taken:

    pass 1  any of the run's five band cells under 20 rows
    pass 2  any under 100
    pass 3  any of the five exact cells under 20, then under 100
    pass 4  the rest

newest first within every pass.

FETCHING reuses the summary stage's machinery (_fetch_batch / batch_query
with _bundle=True and _lean=True -- the Summary under the alias `table`
plus Interrupts and Dispels, execution.EST_COST_BUNDLE_LEAN a run --
parse_node), so the parse path is the tested one; re-fetching the Summary
costs about one point per run and is accepted. The rows journaled for a
backfilled run REPLACE the run's earlier rows -- the journal is append-only
and every reader resolves a duplicate identity last-wins (export(),
seed_from_csv via the exported CSV, BundleGate.rebuild) -- so the frame
ends with exec = 1 rows carrying the bundle columns, the gear journal gets
the run's gear rows again, and runs.jsonl its run-level record. A run whose report is gone (a permanent
GraphQL error) or whose bundle came back with no table at all is marked in
data/processed/backfill_done.txt and never selected again.

STOPPING. Batches are submitted only while the wall clock is before the
deadline; when the hourly budget is spent the client sleeps to the reset if
that comes before the deadline and raises QuotaDeadline otherwise, which
ends the backfill cleanly (stop=budget). Everything journaled stays; the
next run picks the selection up again from the frame.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import execution as ex                       # noqa: E402
import fetch_data as _fetch_data_module      # noqa: E402

# The collector runs as `python scripts/fetch_data.py`, so `import fetch_data`
# from here would load a SECOND copy of the module -- one whose
# fetch_summaries.hero is None, whose STOP flag never trips and whose health
# outputs never reach fetch_health.txt (run 4269: every parse failed on the
# copy's None resolver). Bind to the running module when that is fetch_data.
_main = sys.modules.get("__main__")
fd = (_main if getattr(_main, "fetch_summaries", None) is not None
      and str(getattr(_main, "__file__", "")).endswith("fetch_data.py")
      else _fetch_data_module)
from retention import DAY_MS, iso as _iso    # noqa: E402
from wcl_client import QuotaDeadline, QUOTA  # noqa: E402


class ParseStreak(Exception):
    """Too many parse failures in a row: a bug in this code, not the data."""

WINDOW_DAYS = 14
LEVEL_MIN = 10
PASSES = (("band_lt20", "band", 20), ("band_lt100", "band", 100),
          ("exact_lt20", "exact", 20), ("exact_lt100", "exact", 100),
          ("rest", None, None))
# v2: the first backfill run (2026-09-27 18:01Z) wrote 1,312 FAILED markers
# in two minutes against an exhausted hourly window -- every alias came
# back with an error, none of them about the report -- so v1 markers are
# not read any more; a marker is only FAILED when the message names the
# report as gone (PERMANENT_REPORT below).
MARKERS_NAME = "backfill_done_v2.txt"
IDENT = ["report_code", "fight_id", "character", "server"]
# a per-alias error that really means the report is gone: it must mention
# the report and say it does not exist / is private / was deleted
PERMANENT_REPORT = re.compile(
    r"report.*(do(es)? not exist|not found|private|deleted|invalid)|"
    r"(do(es)? not exist|not found|private|deleted|invalid).*report", re.IGNORECASE)
LOG_FAILURES = 8            # the first failure messages of a run go to the log
PARSE_STREAK_ABORT = 20     # this many parse failures in a row = our bug: stop the run


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------

def load_markers(path: pathlib.Path) -> set[str]:
    """Run keys an earlier attempt marked EMPTY, or FAILED because the report
    is gone. A FAILED marker with any other reason (a parse error is our
    bug, not the report's: run 4269 wrote 2,267 of them with a None
    resolver) is not honoured, so the run is selected again."""
    out: set[str] = set()
    if not path.exists():
        return out
    with path.open() as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2 or ":" not in parts[0]:
                continue
            if parts[1] == "EMPTY":
                out.add(parts[0])
            elif parts[1] == "FAILED" and len(parts) >= 3 and PERMANENT_REPORT.search(parts[2] or ""):
                out.add(parts[0])
    return out


def _affixes(v) -> list[int]:
    if isinstance(v, (list, tuple)):
        return [int(a) for a in v if str(a).strip().lstrip("-").isdigit()]
    if v is None or (isinstance(v, float) and v != v):
        return []
    return [int(a) for a in str(v).split("|") if a.strip().lstrip("-").isdigit()]


def runs_from_frame(df, now_ms: float, days: int = WINDOW_DAYS, level_min: int = LEVEL_MIN,
                    skip=()) -> tuple[list[dict], Counter, Counter, dict]:
    """(candidate runs, band counts, exact counts, facts) from a players frame.

    Candidates: runs dated in the trailing `days` at `level_min` and up with
    no exec = 1 row and no marker in `skip`. The counters hold the bundled
    rows of the same window per band cell and per exact cell.
    """
    import pandas as pd
    facts = {"frame_rows": int(len(df))}
    df = df.drop_duplicates(subset=IDENT, keep="last")
    st = pd.to_numeric(df["started_at"], errors="coerce") if "started_at" in df.columns else pd.Series(float("nan"), index=df.index)
    lvl = pd.to_numeric(df["key_level"], errors="coerce")
    ex_ = (pd.to_numeric(df["exec"], errors="coerce").fillna(0) if "exec" in df.columns
           else pd.Series(0.0, index=df.index))
    cut = now_ms - days * DAY_MS
    dated = st.notna() & (st > 0) & (st <= now_ms + DAY_MS)
    win = dated & (st >= cut) & (lvl >= level_min)
    spec = df["class"].astype(str) + "-" + df["spec"].astype(str)
    dun = df["dungeon"].astype(str)
    bundled = win & (ex_ > 0)
    band = (lvl // ex.BAND * ex.BAND)
    band_counts = Counter(zip(spec[bundled], dun[bundled], band[bundled].astype(int)))
    exact_counts = Counter(zip(spec[bundled], dun[bundled], lvl[bundled].astype(int)))
    has_exec = set(zip(df.loc[bundled, "report_code"].astype(str), df.loc[bundled, "fight_id"]))
    facts["window_rows"] = int(win.sum())
    facts["window_bundled_rows"] = int(bundled.sum())
    sub = df[win & ~(ex_ > 0)].copy()
    sub["_spec"] = spec[sub.index]
    sub["_key"] = sub["report_code"].astype(str) + ":" + sub["fight_id"].astype(str)
    runs: list[dict] = []
    if len(sub):
        first = {c: "first" for c in ("started_at", "key_level", "dungeon", "region", "score",
                                      "medal", "affixes", "keystone_s") if c in sub.columns}
        g = sub.groupby(["report_code", "fight_id"], sort=False)
        agg = g.agg(**{c: (c, f) for c, f in first.items()}, _specs=("_spec", list))
        skip = set(skip)
        for (code, fid), r in agg.iterrows():
            code = str(code)
            key = f"{code}:{fid}"
            if key in skip or (code, fid) in has_exec:
                continue
            try:
                fid_i = int(fid)
            except (TypeError, ValueError):
                continue
            ks = r.get("keystone_s")
            ks = float(ks) if ks is not None and ks == ks and str(ks).strip() not in ("", "nan") else None
            runs.append({
                "key": key, "code": code, "fid": fid_i,
                "start": int(r["started_at"]), "level": int(r["key_level"]),
                "dungeon": str(r["dungeon"]), "specs": list(r["_specs"]),
                "region": ("" if r.get("region") is None or r.get("region") != r.get("region")
                           else str(r.get("region") or "")),
                "score": r.get("score"), "medal": r.get("medal"),
                "affixes": _affixes(r.get("affixes")),
                "rank_duration_ms": int(ks * 1000) if ks and ks > 0 else None,
            })
    facts["window_runs_unbundled"] = len(runs)
    facts["window_runs_bundled"] = len(has_exec)
    return runs, band_counts, exact_counts, facts


def select_runs(runs: list[dict], band_counts: Counter, exact_counts: Counter,
                passes=PASSES) -> tuple[list[dict], dict]:
    """The fetch order (greedy, online) and how many each pass took."""
    remaining = sorted(runs, key=lambda r: (-(r.get("start") or 0), r["key"]))
    order: list[dict] = []
    taken: dict = {}

    def cells(r):
        b = [(sk, r["dungeon"], ex.band_of(r["level"])) for sk in r["specs"]]
        e = [(sk, r["dungeon"], int(r["level"])) for sk in r["specs"]]
        return b, e

    for name, kind, limit in passes:
        keep = []
        n = 0
        for r in remaining:
            b, e = cells(r)
            if kind == "band":
                ok = any(band_counts[c] < limit for c in b)
            elif kind == "exact":
                ok = any(exact_counts[c] < limit for c in e)
            else:
                ok = True
            if ok:
                order.append(r)
                n += 1
                for c in b:
                    band_counts[c] += 1
                for c in e:
                    exact_counts[c] += 1
            else:
                keep.append(r)
        remaining = keep
        taken[name] = n
    return order, taken


def _clean(v):
    return None if v is None or (isinstance(v, float) and v != v) else v


def fight_of(r: dict, listed: dict | None = None) -> dict:
    """The fight dict the batch machinery expects, from a selected run; the
    leaderboard's own entry (when a board still lists the run) wins for the
    fields it carries."""
    f = {"code": r["code"], "fid": r["fid"], "dungeon": r["dungeon"],
         "key_level": r["level"], "region": r.get("region") or "",
         "score": _clean(r.get("score")), "medal": _clean(r.get("medal")),
         "affixes": list(r.get("affixes") or []), "start_time": r.get("start"),
         "rank_duration_ms": r.get("rank_duration_ms"), "specs": list(r["specs"])}
    if listed:
        for k in ("dungeon", "key_level", "region", "score", "medal", "affixes",
                  "start_time", "rank_duration_ms"):
            v = listed.get(k)
            if v not in (None, "", []):
                f[k] = v
    f["_bundle"] = True
    f["_lean"] = True      # Summary + Interrupts + Dispels: kicks, stops, dispels -- what the site reads
    return f


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

def run(regions=None, deadline_s: float | None = None, now_ms: float | None = None,
        listed: dict | None = None, log=print) -> dict:
    """Bundle the window's unbundled runs in selection order until the
    deadline or the budget. Returns the stats it also hands to write_outputs."""
    t0 = time.time()
    now_ms = time.time() * 1000 if now_ms is None else float(now_ms)
    deadline_s = float("inf") if deadline_s is None else float(deadline_s)
    stats: Counter = Counter()
    out: dict = {}

    def finish(stop: str, selected: int, facts: dict | None = None):
        wall = time.time() - t0
        left = max(0, selected - stats["bundled_runs"] - stats["failed"] - stats["empty"])
        rec = {"backfill.selected": selected,
               "backfill.bundled_runs": int(stats["bundled_runs"]),
               "backfill.rows": int(stats["rows"]),
               "backfill.failed": int(stats["failed"]),
               "backfill.empty": int(stats["empty"]),
               "backfill.transient": int(stats["transient"]),
               "backfill.left": left, "backfill.stop": stop,
               "backfill.wall_s": f"{wall:.1f}"}
        for name, _k, _l in PASSES:
            rec[f"backfill.pass_{name}"] = int(out.get("passes", {}).get(name, 0))
        for k, v in (facts or {}).items():
            rec[f"backfill.{k}"] = v
        fd.write_outputs(**rec)
        log(f"[backfill] {stop}: {stats['bundled_runs']:,} runs bundled "
            f"({stats['rows']:,} rows) of {selected:,} selected; {stats['failed']:,} failed, "
            f"{stats['empty']:,} empty, {stats['transient']:,} transient; {left:,} left; "
            f"{wall / 60:.1f} min", flush=True)
        rec["stats"] = dict(stats)
        return rec

    if time.time() >= deadline_s:
        return finish("wall", 0)
    df = fd.load_players_frame(fd.PLAYERS_FILE)
    if df is None:
        log("[backfill] no player rows yet; nothing to backfill", flush=True)
        return finish("done", 0)
    markers = fd.PROCESSED / MARKERS_NAME
    skip = load_markers(markers)
    runs, band_counts, exact_counts, facts = runs_from_frame(df, now_ms, skip=skip)
    del df
    order, taken = select_runs(runs, band_counts, exact_counts)
    out["passes"] = taken
    log(f"[backfill] window: {facts['window_runs_unbundled']:,} unbundled runs at +{LEVEL_MIN} "
        f"and up in the trailing {WINDOW_DAYS} days ({facts['window_runs_bundled']:,} bundled "
        f"already, {len(skip):,} marked); selected {len(order):,}: "
        + ", ".join(f"{k} {v:,}" for k, v in taken.items())
        + f"; frame in {time.time() - t0:.1f}s", flush=True)
    if listed is None:
        try:
            listed = {f"{f['code']}:{f['fid']}": f for f in fd.load_fights(regions).values()}
        except Exception as e:                       # noqa: BLE001 -- the frame suffices
            log(f"[backfill] leaderboard snapshot unavailable ({type(e).__name__}); "
                f"fights built from the frame alone", flush=True)
            listed = {}
    fights = [fight_of(r, listed.get(r["key"])) for r in order]
    if not fights:
        return finish("done", 0, facts)

    fd.PROCESSED.mkdir(parents=True, exist_ok=True)
    for pth in (fd.PLAYERS_FILE, fd.GEAR_FILE, fd.RUNS_FILE, markers):
        fd._repair_tail(pth)
    gate = ex.BundleGate.load(fd.EXEC_GATE_FILE)
    rows_fh = fd.PLAYERS_FILE.open("a")
    gear_fh = fd.GEAR_FILE.open("a")
    runs_fh = fd.RUNS_FILE.open("a")
    mark_fh = markers.open("a")
    batches = [fights[i:i + fd.SUMMARY_BATCH] for i in range(0, len(fights), fd.SUMMARY_BATCH)]
    it = iter(batches)
    stop = "done"
    n_done = 0
    t_fetch = time.time()
    parse_streak = 0
    # the sweep's resolver when the sweep ran in this process, else our own
    # (run 4269: fetch_summaries.hero was None here and every parse failed)
    hero = getattr(fd.fetch_summaries, "hero", None) or fd.HeroResolver()
    # The sweep may have spent the hour already. When one batch no longer
    # fits under the ceiling and the reset lies past this run's deadline,
    # there is nothing to gain from sending anything: stop here (the first
    # run churned through 1,312 runs against an exhausted window).
    if batches and deadline_s is not None:
        need = fd.batch_est_cost(batches[0])
        if QUOTA.spent + need > QUOTA.ceiling and time.time() + QUOTA.reset_in + 20 > deadline_s:
            it = iter(())
            stop = "budget"
            log(f"[backfill] budget: the hour is spent ({QUOTA.spent:.0f}/{QUOTA.ceiling:.0f} pts) "
                f"and the reset ({QUOTA.reset_in:.0f}s away) lies past the deadline; nothing sent",
                flush=True)
    try:
        with ThreadPoolExecutor(max_workers=fd.SUMMARY_WORKERS) as pool:
            futures = set()

            def submit() -> bool:
                if fd.STOP or time.time() >= deadline_s:
                    return False
                b = next(it, None)
                if b is None:
                    return False
                futures.add(pool.submit(fd._fetch_batch, b))
                return True

            for _ in range(fd.SUMMARY_WORKERS * 2):
                if not submit():
                    break
            try:
                while futures:
                    fut = next(as_completed(futures))
                    futures.remove(fut)
                    batch, rep, errmap, _spent = fut.result()
                    if rep is None:
                        stats["transient"] += len(batch)
                    else:
                        # every alias failing with the same message is the
                        # request's problem (budget, auth, a bad filter), not
                        # the reports': transient, and logged
                        msgs = {errmap.get(f"a{i}", "") for i in range(len(batch))}
                        uniform = len(batch) > 1 and len(msgs) == 1 and next(iter(msgs))
                        if uniform and stats["logged"] < LOG_FAILURES:
                            stats["logged"] += 1
                            log(f"[backfill] whole batch errored: {uniform[:160]}", flush=True)
                        for i, f in enumerate(batch):
                            key = f"{f['code']}:{f['fid']}"
                            node = rep.get(f"a{i}")
                            if not node or not node.get("table"):
                                msg = errmap.get(f"a{i}", "")
                                if msg and not uniform and PERMANENT_REPORT.search(msg):
                                    mark_fh.write(f"{key}\tFAILED\t{msg[:100]}\n")
                                    stats["failed"] += 1
                                    if stats["logged"] < LOG_FAILURES:
                                        stats["logged"] += 1
                                        log(f"[backfill] failed {key}: {msg[:160]}", flush=True)
                                else:
                                    stats["transient"] += 1
                                    if msg and stats["logged"] < LOG_FAILURES:
                                        stats["logged"] += 1
                                        log(f"[backfill] transient {key}: {msg[:160]}", flush=True)
                                continue
                            run_rec: dict = {}
                            try:
                                rows, gear_rows = fd.parse_node(f, node, hero, run_rec)
                            except (ValueError, KeyError, TypeError, AttributeError) as e:
                                # a parse error is this code's problem, never the
                                # report's: no marker, the run stays selectable
                                stats["parse_failed"] += 1
                                stats["transient"] += 1
                                parse_streak += 1
                                if stats["logged"] < LOG_FAILURES:
                                    stats["logged"] += 1
                                    log(f"[backfill] parse failed {key}: {type(e).__name__}: {str(e)[:120]}", flush=True)
                                if parse_streak >= PARSE_STREAK_ABORT:
                                    raise ParseStreak(f"{parse_streak} parse failures in a row "
                                                      f"({type(e).__name__}: {str(e)[:80]})")
                                continue
                            parse_streak = 0
                            if not rows or not rows[0].get("exec"):
                                # asked for, nothing came back: not a bundle,
                                # and the old rows stand
                                mark_fh.write(f"{key}\tEMPTY\n")
                                stats["empty"] += 1
                                continue
                            for row in rows:
                                rows_fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                            for row in gear_rows:
                                gear_fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                            if run_rec:
                                runs_fh.write(json.dumps(run_rec, ensure_ascii=False) + "\n")
                            mark_fh.write(f"{key}\tOK\n")
                            gate.record_rows(rows)
                            stats["bundled_runs"] += 1
                            stats["rows"] += len(rows)
                        rows_fh.flush()
                        gear_fh.flush()
                        runs_fh.flush()
                        mark_fh.flush()
                    n_done += 1
                    if n_done % 25 == 0:
                        rate = stats["bundled_runs"] / max(time.time() - t_fetch, 1)
                        log(f"[backfill] {stats['bundled_runs']:,} bundled | {rate * 3600:,.0f} runs/h | "
                            f"{len(fights) - n_done * fd.SUMMARY_BATCH:,} to go | "
                            f"{(deadline_s - time.time()) / 60:.0f} min to the deadline", flush=True)
                    if n_done % 200 == 0:
                        gate.save()
                    if not submit():
                        if fd.STOP:
                            stop = "signal"
                        elif time.time() >= deadline_s:
                            stop = "wall"
            except QuotaDeadline as e:
                stop = "budget"
                log(f"[backfill] budget: {e}; stopping this run's backfill "
                    f"(the next run continues from the frame)", flush=True)
                pool.shutdown(wait=False, cancel_futures=True)
            except ParseStreak as e:
                stop = "parse"
                log(f"[backfill] ABORT: {e}; the parser is broken for this data, nothing is "
                    f"marked, the next run tries again", flush=True)
                pool.shutdown(wait=False, cancel_futures=True)
    finally:
        rows_fh.close()
        gear_fh.close()
        runs_fh.close()
        mark_fh.close()
        gate.save()
    if stop == "done" and n_done < len(batches):
        stop = "wall" if time.time() >= deadline_s else "signal"
    return finish(stop, len(fights), facts)
