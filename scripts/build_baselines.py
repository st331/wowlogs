#!/usr/bin/env python3
"""Population baselines and the per-run store for the Key Level Logs site.

Contract: keylevel_addon/design/baselines-from-wowlogs.md §2 (baselines) and
§3 (run store); §5 is this builder. Two outputs, rebuilt with every site
build from the SAME frame build_site_data.py publishes (its retention
window, applied by its own apply_retention -- never re-implemented here) plus
the run-level journal the collector writes beside the player rows:

  site/baselines.json.gz   per spec x dungeon x key-level cell, the quantiles
                           of every measure over timed leaderboard runs at
                           three tiers (exact level, 2-level band, band pooled
                           over dungeons), each shipped when n >= CELL_MIN_N;
                           the population's per-dungeon priority (Interrupts)
                           and dispellable (Dispels) tables.
  site/runs/<hh>.json.gz   every run in the window at LEVEL_MIN and up,
                           whatever its medal, in 256 shards keyed by a hash
                           of the report code (shard_of: h = (h*31 + ord(ch))
                           mod 256 over the code's first four characters, as
                           two lowercase hex digits; the client computes the
                           same with charCodeAt), with the per-player rows
                           the collector stored -- the vetting site reads a
                           run from here instead of pulling it from Warcraft
                           Logs a second time. Every shard is written, an
                           empty one as {"runs": {}}, so a fetch never 404s.
                           Fields the collector did not fetch are omitted
                           (the client reads a missing field as null);
                           "exec": false says the bundle was not fetched.
                           The run carries its dispel_spells; int_spells
                           stay out (kicks_by plus the population priority
                           table are what the client needs).

Rates use the run's fight duration (`duration_s`, the Summary totalTime) --
NOT the keystone clock the dashboard recomputes DPS on -- because the site
measures an applicant's run against the same `totalTime`, and `dps` is the
collector's raw value for the same reason (it matches WCL's own amount).

kick_prio weights each kicked spell by the population's kick rate for it in
this window (interrupted / begun from the priority table; 0.5 when the spell
is not in the table), the same weight the client applies (measures.js).

Health lines are appended to site/build_health.txt (this runs after
build_site_data.py, which writes that file) and printed.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import pathlib
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import build_site_data as B          # noqa: E402  (retention, SEASON, SITE_DIRS)
import execution as ex               # noqa: E402
from retention import RETENTION_RESETS   # noqa: E402

ROOT = B.ROOT
RUNS_JOURNAL = ROOT / "data" / "processed" / "runs.jsonl"
QUANTILES = [5, 10, 25, 50, 75, 90, 95]
CELL_MIN_N = 20              # a cell (and a measure within it) ships at this many rows
LEVEL_MIN, LEVEL_MAX = 10, 30
MEASURES = {                 # unit + direction, so the client never guesses (§2)
    "dps":           {"unit": "per_s",   "better": "high"},
    "deaths_30m":    {"unit": "per_30m", "better": "low"},
    "chain_30m":     {"unit": "per_30m", "better": "low"},
    "kicks_min":     {"unit": "per_min", "better": "high"},
    "kick_prio":     {"unit": "per_min", "better": "high"},
    "dispels_min":   {"unit": "per_min", "better": "high"},
    "avoid_dmg_min": {"unit": "per_min", "better": "low"},
    "def_casts_min": {"unit": "per_min", "better": "high"},
    "pots":          {"unit": "per_run", "better": "high"},
    "heal_eff_s":    {"unit": "per_s",   "better": "high"},
}
EXEC_MEASURES = ("kicks_min", "kick_prio", "dispels_min", "avoid_dmg_min",
                 "def_casts_min", "heal_eff_s")
TIMED_MEDALS = {"gold", "silver", "bronze", "timed"}
POPULATION = ("timed leaderboard runs (fightRankings pages 1-20 by score per "
              "dungeon x level)")
N_SHARDS = 256                   # runs/00.json.gz .. runs/ff.json.gz, every one written
SHARD_CHARS = 4                  # characters of the report code the hash reads
SIZE_BUDGET_GZ = 40_000_000      # baselines + every shard, gzipped (13 MB today at 0 % bundled; ~27 MB projected at 55 %)
ISO_Z = "%Y-%m-%dT%H:%M:%SZ"

_HEALTH: list[str] = []


def health(line: str) -> None:
    _HEALTH.append(line)
    print(line, flush=True)


def append_health(dirs) -> None:
    body = "\n".join(_HEALTH) + "\n"
    for d in dirs:
        try:
            d.mkdir(parents=True, exist_ok=True)
            with (d / "build_health.txt").open("a", encoding="utf-8") as fh:
                fh.write(body)
        except OSError:
            pass          # a health note must never be able to fail a build


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------

def load_frame(csv: pathlib.Path, name: str = "baselines", now=None) -> pd.DataFrame:
    """The frame the site build publishes: the CSV under apply_retention (and
    sample_runs, a no-op unless MAX_RUNS is ever set). The keystone-clock DPS
    rewrite is deliberately NOT applied (see the module docstring)."""
    df = pd.read_csv(csv)
    df = B.apply_retention(df, name, now=now)
    df = B.sample_runs(df, name)
    for col in ("class", "spec", "role", "region", "dungeon"):
        if col in df.columns:
            df[col] = df[col].fillna("Unknown").replace("", "Unknown")
        else:
            df[col] = "Unknown"
    for col in ex.EXEC_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan
    return df


def lists_version() -> str:
    for p in (ex.LISTS_CACHE, ex.LISTS_FILE):
        try:
            return str(json.loads(p.read_text()).get("version", "?"))
        except (OSError, ValueError, AttributeError):
            continue
    return "none"


def run_key_series(df: pd.DataFrame) -> pd.Series:
    return df["report_code"].astype(str) + ":" + df["fight_id"].astype(str)


def load_runs_journal(path: pathlib.Path, keys: set[str]) -> dict[str, dict]:
    """{run key: last record} for the runs in `keys` (the window, deduped).
    Torn or corrupt lines are skipped; the last copy of a key wins, as in
    every other journal."""
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with path.open("rb") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            k = f"{rec.get('report_code')}:{rec.get('fight_id')}"
            if k in keys:
                out[k] = rec
    return out


def spell_tables(recs: dict[str, dict], dungeon_of: dict[str, str]):
    """(priority, dispellable): per dungeon, per spell id, the sums over the
    window's runs of begun/completed/interrupted and applied/dispelled/expired."""
    prio: dict[str, dict] = {}
    disp: dict[str, dict] = {}
    for k, rec in recs.items():
        if not rec.get("exec"):
            continue
        dun = dungeon_of.get(k) or rec.get("dungeon") or "Unknown"
        for src, dst, fields in (("int_spells", prio, ("begun", "completed", "interrupted")),
                                 ("dispel_spells", disp, ("applied", "dispelled", "expired"))):
            table = rec.get(src)
            if not isinstance(table, dict):
                continue
            d = dst.setdefault(dun, {})
            for guid, s in table.items():
                if not isinstance(s, dict):
                    continue
                e = d.setdefault(str(guid), {"name": s.get("name"), **{f: 0 for f in fields}})
                if s.get("name"):
                    e["name"] = s["name"]
                for f in fields:
                    v = s.get(f)
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        e[f] += int(v)
    return prio, disp


# --------------------------------------------------------------------------
# measures and cells
# --------------------------------------------------------------------------

def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def kick_prio_column(df: pd.DataFrame, minutes: pd.Series, priority: dict) -> pd.Series:
    """Σ over kicked spells of (interrupted ÷ begun) × kicks ÷ minutes."""
    out = np.full(len(df), np.nan)
    by = df["kicks_by"].to_numpy()
    duns = df["dungeon"].to_numpy()
    mins = minutes.to_numpy()
    for i in range(len(df)):
        d = ex.unpack_by(by[i])
        if d is None or not (mins[i] > 0):
            continue
        table = priority.get(duns[i]) or {}
        s = 0.0
        for guid, n in d.items():
            p = table.get(guid)
            w = (p["interrupted"] / p["begun"]) if p and p.get("begun") else 0.5
            s += w * n
        out[i] = s / mins[i]
    return pd.Series(out, index=df.index)


def measures_frame(df: pd.DataFrame, priority: dict) -> pd.DataFrame:
    """Per row: the cell coordinates and every measure (NaN = not available)."""
    dur = _num(df["duration_s"])
    dur = dur.where(dur > 0)
    minutes = dur / 60.0
    m = pd.DataFrame(index=df.index)
    m["spec"] = df["class"].astype(str) + "-" + df["spec"].astype(str)
    m["dungeon"] = df["dungeon"].astype(str)
    lvl = _num(df["key_level"])
    m["level"] = lvl
    m["band"] = (lvl // ex.BAND) * ex.BAND
    m["exec"] = (_num(df["exec"]).fillna(0) > 0).astype(int)
    m["dps"] = _num(df["dps"])
    m["deaths_30m"] = _num(df["deaths"]) / dur * 1800.0
    m["chain_30m"] = _num(df["deaths_chain"]) / dur * 1800.0
    m["kicks_min"] = _num(df["kicks"]) / minutes
    m["kick_prio"] = kick_prio_column(df, minutes, priority)
    m["dispels_min"] = _num(df["dispels"]) / minutes
    m["avoid_dmg_min"] = _num(df["avoid_dmg"]) / minutes
    m["def_casts_min"] = _num(df["def_casts"]) / minutes
    m["pots"] = _num(df["pots"])
    heal = (_num(df["heal_total"]) - _num(df["heal_over"]).fillna(0)) / dur
    m["heal_eff_s"] = heal.where(df["role"].astype(str) == "Healer")
    return m


def round_quantiles(vals) -> list:
    """Whole numbers at 100 and above (DPS, damage), else three decimals."""
    out = []
    for v in vals:
        v = float(v)
        out.append(int(round(v)) if abs(v) >= 100 else round(v, 3))
    return out


def build_cells(m: pd.DataFrame, min_n: int = CELL_MIN_N) -> tuple[dict, dict]:
    """The three tiers (§2): spec|dungeon|level, spec|dungeon|bNN, spec|*|bNN.
    A cell ships at n >= min_n with n and n_exec; each measure's quantiles
    ship when that measure has min_n values in the cell."""
    measures = list(MEASURES)
    cells: dict[str, dict] = {}
    counts = {"exact": 0, "band": 0, "pooled": 0, "exec_ready": 0, "exec_100": 0}
    qs = [q / 100 for q in QUANTILES]
    tiers = (("exact", ["spec", "dungeon", "level"], lambda s, d, l: f"{s}|{d}|{int(l)}"),
             ("band", ["spec", "dungeon", "band"], lambda s, d, b: f"{s}|{d}|b{int(b)}"),
             ("pooled", ["spec", "band"], lambda s, b: f"{s}|*|b{int(b)}"))
    for tier, keys, fmt in tiers:
        g = m.groupby(keys, sort=False, observed=True)
        n = g.size()
        if not len(n):
            continue
        idx = n.index
        n_exec = g["exec"].sum().reindex(idx).to_numpy()
        cnt = g[measures].count().reindex(idx)
        # one vectorised quantile pass per tier; (cells x 7) arrays per measure
        qt = g[measures].quantile(qs).unstack(level=-1).reindex(idx)
        arrays = {meas: qt[meas][qs].to_numpy() for meas in measures}
        cnts = {meas: cnt[meas].to_numpy() for meas in measures}
        sizes = n.to_numpy()
        for pos, key in enumerate(idx):
            size = int(sizes[pos])
            if size < min_n:
                continue
            key_t = key if isinstance(key, tuple) else (key,)
            cell = {"n": size, "n_exec": int(n_exec[pos])}
            for meas in measures:
                if int(cnts[meas][pos]) < min_n:
                    continue
                vals = arrays[meas][pos]
                if np.isnan(vals).any():
                    continue
                cell[meas] = round_quantiles(vals)
            cells[fmt(*key_t)] = cell
            counts[tier] += 1
            if cell["n_exec"] >= min_n:
                counts["exec_ready"] += 1
            if cell["n_exec"] >= ex.QUOTA_ROWS:
                counts["exec_100"] += 1
    return cells, counts


# --------------------------------------------------------------------------
# the run store
# --------------------------------------------------------------------------

def _int_or_none(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float_or_none(v, nd=1):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        return round(float(v), nd)
    except (TypeError, ValueError):
        return None


def _str_or_none(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return str(v)


def shard_of(code) -> str | None:
    """Two lowercase hex digits: h = (h * 31 + ord(ch)) mod 256 over the first
    SHARD_CHARS characters of the report code. The client computes the same
    with charCodeAt (codes are ASCII, so ord == charCode). None only for a
    missing code, which is not a run at all."""
    if code is None or (isinstance(code, float) and math.isnan(code)):
        return None
    s = str(code)
    if not s or s == "nan":
        return None
    h = 0
    for ch in s[:SHARD_CHARS]:
        h = (h * 31 + ord(ch)) % N_SHARDS
    return f"{h:02x}"


def all_shards() -> list[str]:
    return [f"{i:02x}" for i in range(N_SHARDS)]


def build_run_store(df: pd.DataFrame, recs: dict[str, dict], built: str,
                    min_level: int = LEVEL_MIN) -> tuple[dict, dict]:
    """{shard: document} for every run at min_level and up (§3), all N_SHARDS
    shards present, an empty one with "runs": {}."""
    cols = ["report_code", "fight_id", "dungeon", "key_level", "started_at",
            "duration_s", "medal", "character", "server", "region", "class",
            "spec", "role", "dps", "deaths", "deaths_chain", "pots", "hs", "exec",
            "kicks", "kicks_by", "dispels", "dispels_by", "avoid_dmg",
            "def_casts", "heal_total", "heal_over"]
    lvl_num = _num(df["key_level"])
    sub = df.loc[(lvl_num >= min_level).to_numpy(), cols]
    shards: dict[str, dict] = {c: {"built": built, "runs": {}} for c in all_shards()}
    stats = {"runs": 0, "runs_exec": 0, "unsharded": 0, "players": 0,
             "below_level": int((~(lvl_num >= min_level)).sum())}
    seen: dict[str, dict] = {}
    for t in sub.itertuples(index=False, name=None):
        (code, fid, dun, lvl, start, dur, medal, name, server, region, cls, spec,
         role, dps, deaths, chain, pots, hs, exec_, kicks, kicks_by, dispels,
         dispels_by, avoid, defc, htot, hover) = t
        key = f"{code}:{_int_or_none(fid) if _int_or_none(fid) is not None else fid}"
        run = seen.get(key)
        if run is None:
            c = shard_of(code)
            if c is None:
                stats["unsharded"] += 1
                continue
            medal_s = _str_or_none(medal)
            run = {"dun": _str_or_none(dun), "lvl": _int_or_none(lvl),
                   "start": _int_or_none(start), "dur_s": _float_or_none(dur),
                   "timed": (medal_s in TIMED_MEDALS) if medal_s else None,
                   "exec": False, "players": []}
            rec = recs.get(key)
            if rec and rec.get("exec") and isinstance(rec.get("dispel_spells"), dict):
                run["dispel_spells"] = rec["dispel_spells"]
            seen[key] = run
            shards[c]["runs"][key] = run
            stats["runs"] += 1
        bundled = bool(_int_or_none(exec_))
        if bundled and not run["exec"]:
            run["exec"] = True
            stats["runs_exec"] += 1
        p = {"name": _str_or_none(name), "server": _str_or_none(server),
             "region": _str_or_none(region), "class": _str_or_none(cls),
             "spec": _str_or_none(spec), "role": _str_or_none(role),
             "dps": _float_or_none(dps), "deaths": _int_or_none(deaths)}
        # the optional fields ride only when the collector has them: the
        # Summary-derived three on rows written since the bundle landed, the
        # bundle's eight when it was fetched for the run; a missing field
        # reads as null on the client, so nothing is written as null
        opt = {"deaths_chain": _int_or_none(chain), "pots": _int_or_none(pots),
               "hs": _int_or_none(hs)}
        if bundled:
            opt.update({"kicks": _int_or_none(kicks), "kicks_by": ex.unpack_by(kicks_by),
                        "dispels": _int_or_none(dispels), "dispels_by": ex.unpack_by(dispels_by),
                        "avoid_dmg": _int_or_none(avoid), "def_casts": _int_or_none(defc),
                        "heal_total": _int_or_none(htot), "heal_over": _int_or_none(hover)})
        p.update({k: v for k, v in opt.items() if v is not None})
        run["players"].append(p)
        stats["players"] += 1
    return shards, stats


# --------------------------------------------------------------------------
# the build
# --------------------------------------------------------------------------

def _gz_write(path: pathlib.Path, text: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=9) as fh:
        fh.write(text)
    os.replace(tmp, path)
    return path.stat().st_size


def window_block(df: pd.DataFrame) -> dict:
    ri = B.RETENTION_INFO
    cuts = ri.get("cut") or {}
    if cuts:
        frm = min(cuts.values())[:10]
        to = (ri.get("anchor") or "")[:10] or None
    else:
        st = pd.to_datetime(_num(df["started_at"]), unit="ms", errors="coerce").dropna()
        frm = st.min().strftime("%Y-%m-%d") if len(st) else None
        to = st.max().strftime("%Y-%m-%d") if len(st) else None
    return {"from": frm, "to": to, "resets": ri.get("resets", RETENTION_RESETS)}


def build(csv: pathlib.Path = ROOT / "data" / B.SEASON["csv"],
          runs_journal: pathlib.Path = RUNS_JOURNAL,
          site_dirs=None, now=None, name: str = "baselines") -> dict:
    t0 = time.perf_counter()
    site_dirs = list(site_dirs) if site_dirs is not None else list(B.SITE_DIRS)
    _HEALTH.clear()
    if not csv.exists():
        health(f"{name}.skipped=no {csv.name}")
        append_health(site_dirs)
        return {"skipped": True}
    df = load_frame(csv, name, now=now)
    built = (pd.Timestamp(now).tz_convert("UTC") if now is not None
             else pd.Timestamp.now("UTC")).strftime(ISO_Z)
    keys = run_key_series(df)
    recs = load_runs_journal(runs_journal, set(keys.unique()))
    dungeon_of = dict(zip(keys, df["dungeon"].astype(str)))
    priority, dispellable = spell_tables(recs, dungeon_of)

    # --- baselines: timed runs at LEVEL_MIN and up ---------------------------
    lvl = _num(df["key_level"])
    medal = df["medal"].astype(str).str.lower()
    timed = medal.isin(TIMED_MEDALS)
    pop = df[timed & (lvl >= LEVEL_MIN) & (lvl <= LEVEL_MAX)]
    m = measures_frame(pop, priority)
    cells, counts = build_cells(m)
    doc = {
        "built": built,
        "season": B.SEASON["season"],
        "window": window_block(df),
        "population": POPULATION,
        "lists_version": lists_version(),
        "quantiles": QUANTILES,
        "measures": MEASURES,
        "levels": {"min": LEVEL_MIN, "max": LEVEL_MAX, "band": ex.BAND},
        "cells": cells,
        "priority": priority,
        "dispellable": dispellable,
        "notes": {
            "kick_prio": "sum over kicked spells of (interrupted / begun in this "
                         "window's priority table, 0.5 when unlisted) x kicks, per minute",
            "rates": "per duration_s (the Summary totalTime), not the keystone clock",
            "cells": f"shipped at n >= {CELL_MIN_N}; a measure's quantiles at "
                     f">= {CELL_MIN_N} values of it",
        },
    }
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    base_gz = 0
    for d in site_dirs:
        base_gz = _gz_write(d / "baselines.json.gz", text)

    # --- run store: every run in the window at LEVEL_MIN and up ---------------
    shards, sstats = build_run_store(df, recs, built)
    shard_gz = 0
    largest, largest_gz = None, 0
    for d in site_dirs:
        out = d / "runs"
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("*.json.gz"):
            old.unlink()                      # nothing from an earlier build may linger
        for c, sdoc in shards.items():
            sz = _gz_write(out / f"{c}.json.gz",
                           json.dumps(sdoc, ensure_ascii=False, separators=(",", ":")))
            shard_gz += sz
            if sz > largest_gz:
                largest, largest_gz = c, sz
    shard_gz = shard_gz // max(len(site_dirs), 1)
    empty = sum(1 for sdoc in shards.values() if not sdoc["runs"])

    # --- health --------------------------------------------------------------
    n_rows, n_exec = int(len(pop)), int(m["exec"].sum()) if len(m) else 0
    share = (n_exec / n_rows) if n_rows else 0.0
    wall = time.perf_counter() - t0
    health(f"{name}.built={built}")
    health(f"{name}.rows={n_rows}")
    health(f"{name}.rows_exec={n_exec}")
    health(f"{name}.bundled_share={share:.3f}")
    health(f"{name}.timed_share={(int(timed.sum()) / len(df)) if len(df) else 0:.3f}")
    health(f"{name}.cells_exact={counts['exact']}")
    health(f"{name}.cells_band={counts['band']}")
    health(f"{name}.cells_pooled={counts['pooled']}")
    health(f"{name}.cells_exec_ready={counts['exec_ready']}")
    health(f"{name}.cells_exec_at_quota={counts['exec_100']}")
    health(f"{name}.priority_dungeons={len(priority)}")
    health(f"{name}.priority_spells={sum(len(v) for v in priority.values())}")
    health(f"{name}.dispellable_spells={sum(len(v) for v in dispellable.values())}")
    health(f"{name}.runs_journal_records={len(recs)}")
    health(f"{name}.lists_version={doc['lists_version']}")
    health(f"{name}.size_gz={base_gz}")
    health(f"{name}.runs_total={sstats['runs']}")
    health(f"{name}.runs_exec={sstats['runs_exec']}")
    health(f"{name}.runs_unsharded={sstats['unsharded']}")
    health(f"{name}.runs_below_level_rows={sstats['below_level']}")
    health(f"{name}.runs_shards={len(shards)}")
    health(f"{name}.runs_shards_empty={empty}")
    health(f"{name}.runs_largest_shard={largest or 'none'}")
    health(f"{name}.runs_largest_shard_gz={largest_gz}")
    health(f"{name}.runs_size_gz={shard_gz}")
    total = base_gz + shard_gz
    health(f"{name}.total_size_gz={total}")
    if total > SIZE_BUDGET_GZ:
        health(f"{name}.size_over_budget=1")
        print(f"::warning::baselines + run store are {total / 1e6:.1f} MB gzipped, over the "
              f"{SIZE_BUDGET_GZ / 1e6:.0f} MB budget (see build_health.txt)", flush=True)
    health(f"{name}.wall_s={wall:.1f}")
    append_health(site_dirs)
    print(f"[{name}] {len(cells):,} cells ({counts['exact']:,} exact / {counts['band']:,} band / "
          f"{counts['pooled']:,} pooled) from {n_rows:,} timed rows ({n_exec:,} bundled, "
          f"{share:.1%}); {sstats['runs']:,} runs at +{LEVEL_MIN} and up in {len(shards)} shards "
          f"({empty} empty; largest {largest} at {largest_gz / 1e3:.0f} KB); "
          f"{base_gz / 1e6:.2f} + {shard_gz / 1e6:.2f} MB gz; {wall:.1f}s", flush=True)
    return {"cells": cells, "counts": counts, "priority": priority, "dispellable": dispellable,
            "shards": shards, "sizes": {"baselines": base_gz, "runs": shard_gz,
                                        "largest_shard": largest, "largest_shard_gz": largest_gz},
            "rows": n_rows, "rows_exec": n_exec, "doc": doc, "run_stats": sstats}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--csv", type=pathlib.Path, default=ROOT / "data" / B.SEASON["csv"])
    ap.add_argument("--runs", type=pathlib.Path, default=RUNS_JOURNAL,
                    help="the run-level journal (data/processed/runs.jsonl)")
    ap.add_argument("--out", type=pathlib.Path, action="append", default=None,
                    help="site directory to write into (repeatable; default: site/ and docs/)")
    ap.add_argument("--now", default=None, help="ISO instant for the window (tests)")
    args = ap.parse_args(argv)
    build(args.csv, args.runs, args.out, now=args.now)
    return 0


if __name__ == "__main__":
    sys.exit(main())
