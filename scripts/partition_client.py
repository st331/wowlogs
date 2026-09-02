#!/usr/bin/env python3
"""partition_client -- the reader side of the partitioned data path, in Python.

The Python mirror of the loader protocol of fleet/blueprints/
partitioned_payload.md §8 that the builder (window statistics, §6.2-3) and
the equivalence tests (§9.1) share; PR-2's JavaScript client is written
against the same contracts:

  * `expand_day()` -- a `rows` container -> the client's per-row columns
    (the run block expanded through `run`, `R.day` filled per block);
  * `load_site()` -- `d/current.json` + the manifest + every listed day
    file (§8.2-1b: the client fetches EVERY file `manifest.days` names) ->
    the legacy payload shape (`D`, `R`) `sitecalc.init_data` consumes, with
    `CHARSCORE` from the `pairs` base + delta files and `rbase[day]` offsets;
  * `join_blocks()` -- the per-block sidecar join of §4.1: a block is
    accepted only if its header `rows_sha` equals the `<h>` of the day file
    actually loaded; `map[rowBase[d] + pos[k]] = shardBase + k`; a relayout
    (a day dropped or replaced) re-derives `rowBase`/`map` from the retained
    blocks.

No network: everything reads from a directory (the builder's `out/` mirror
or `site/d/`).
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field

import numpy as np

import partition_format as pf

RUN_COLS = ("dun", "key", "reg", "timed", "post", "hr", "dur", "kdur")
ROW_COLS = ("cls", "spec", "hero", "role", "deaths", "tier", "dps", "char", "run")


def expand_day(c: pf.Container) -> dict:
    """§2.2: the run block expanded to row length through `run`; `day` per
    block. Returns {col: int64 array} with the client's names."""
    h = c.header
    out = {}
    for k in ROW_COLS:
        if k in c.cols:
            out[k] = c.cols[k].astype(np.int64)
    if "tmul" in c.cols:
        out["tmul"] = c.cols["tmul"].astype(np.int64)
    run = out["run"]
    for k in RUN_COLS:
        out[k] = c.cols["r_" + k].astype(np.int64)[run]
    out["day"] = np.full(h["n"], int(h["day"]) if h["day"] != "undated" else -1, dtype=np.int64)
    return out


def _read_json(path: pathlib.Path):
    if path.suffix == ".gz":
        import gzip
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class Loaded:
    manifest: dict
    D: dict                      # the legacy payload's top level (minus rows)
    R: dict                      # concatenated window columns
    days: list                   # manifest.days entries in load order
    rbase: dict                  # day -> first global run index
    row_base: dict               # day -> first row index
    day_n: dict                  # day -> rows
    day_sha: dict                # day -> <h> of the loaded file
    charscore: np.ndarray        # Int16-like array, -1 = unrated
    root: pathlib.Path = None
    slug_dir: pathlib.Path = None
    containers: dict = field(default_factory=dict)
    proj_pending: int = 0            # listed days whose rules_sha != manifest.projection.rules_sha
    proj_caption: str | None = None  # "projection updating · N of M days" when greyed (§3.3)


def charscore_array(slug_dir: pathlib.Path, manifest: dict) -> np.ndarray:
    """CHARSCORE = Int16Array(char_max+1) filled -1, the base `pairs` file
    then the delta applied over it (§5)."""
    arr = np.full(int(manifest["char_max"]) + 1, -1, dtype=np.int64)
    cs = manifest.get("charscore")
    if not cs:
        return arr
    for entry in (cs, cs.get("delta")):
        if not entry or not entry.get("f"):
            continue
        c = pf.read(slug_dir / entry["f"], expect_kind="pairs")
        ch = c["char"].astype(np.int64)
        arr[ch] = c["score"].astype(np.int64)
    return arr


def load_site(root: pathlib.Path, slug: str | None = None, days: list | None = None) -> Loaded:
    """Load `d/` at `root` the way the client does. `days` restricts the
    loaded set (default: every listed day, buckets 0-2 first in the client;
    the order does not matter for the numbers because rbase comes from the
    manifest's per-day `runs`)."""
    root = pathlib.Path(root)
    cur = _read_json(root / "current.json")
    slug = slug or cur["slug"]
    man = _read_json(root / cur["manifest"]) if slug == cur["slug"] else \
        _read_json(root / slug / "manifest.json")
    slug_dir = root / slug
    listed = [e for e in man["days"] if e.get("f")]
    if days is not None:
        want = set(days)
        listed = [e for e in listed if e["d"] in want]
    # rbase / row_base are stable regardless of load order: in ascending day
    # order over the manifest's listed days (the client keeps the same rule)
    order = sorted(listed, key=lambda e: (e["d"] == "undated", e["d"] if e["d"] != "undated" else 0))
    rbase, row_base, day_n = {}, {}, {}
    rb = rr = 0
    for e in order:
        rbase[e["d"]] = rb
        row_base[e["d"]] = rr
        day_n[e["d"]] = e["n"]
        rb += e["runs"]
        rr += e["n"]
    cols: dict[str, list] = {}
    containers, day_sha = {}, {}
    for e in order:
        c = pf.read(slug_dir / e["f"], expect_kind="rows")
        containers[e["d"]] = c
        day_sha[e["d"]] = pf.parse_name(pathlib.Path(e["f"]).name)[1]
        assert c.header["rows_sha"][:pf.NAME_HASH_LEN] == day_sha[e["d"]]
        ex = expand_day(c)
        ex["run"] = ex["run"] + rbase[e["d"]]
        for k, v in ex.items():
            cols.setdefault(k, []).append(v)
    R = {k: (np.concatenate(v) if v else np.zeros(0, dtype=np.int64)) for k, v in cols.items()}
    # a day of an older generation may lack tmul while another has it: the
    # client greys the toggle (§3.3); here the column is dropped unless every
    # loaded day carries it, which is the same "no projected number" state
    if "tmul" in R and len(cols["tmul"]) != len(order):
        R.pop("tmul")
    # §3.3: the projection toggle is available iff every resident day's
    # rules_sha equals the manifest's generation; otherwise a day is
    # unprojected-pending, no projected number is rendered (D.projection is
    # null for the oracle, exactly the greyed-toggle state) and
    # proj_pending counts the days still to catch up
    proj = man.get("projection")
    proj_pending = 0
    if proj:
        want = proj.get("rules_sha")
        proj_pending = sum(1 for e in order if e.get("rules_sha") != want)
        if proj_pending or "tmul" not in R:
            proj = None
    V = man["vocab"]
    D = {"season": man.get("season"), "epoch": man["epoch"], "built": man["built"],
         "classes": V["classes"], "specs": V["specs"], "heroes": V["heroes"],
         "dungeons": V["dungeons"], "regions": V["regions"], "roles": V["roles"],
         "spec_role": man.get("spec_role"), "pars": man["pars"],
         "tuning": man.get("tuning"), "projection": proj,
         "charscore": None}
    arr = charscore_array(slug_dir, man)
    D["charscore"] = arr.tolist()
    return Loaded(manifest=man, D=D, R=R, days=order, rbase=rbase, row_base=row_base,
                  day_n=day_n, day_sha=day_sha, charscore=arr, root=root,
                  slug_dir=slug_dir, containers=containers, proj_pending=proj_pending,
                  proj_caption=(f"projection updating · {proj_pending} of {len(order)} days"
                                if proj_pending else None))


# ---- cubes (§3.2 files -> sitecalc.CubeWeek, with the generation guard) -----
def _cube_week_from_files(week: int, cells_c: pf.Container, dist_c, chars_c, comps_c):
    import sitecalc as sc
    h = cells_c.header
    cells = {d: cells_c[d].astype(np.int64) for d in sc.CELL_DIMS}
    for k in ("n", "dsum", "dth", "dz", "nr", "dmin", "dmax", "doff"):
        cells[k] = cells_c[k].astype(np.int64)
    rl = {d: cells_c["rl_" + d].astype(np.int64) for d in sc.RL_DIMS}
    rl["nr_rl"] = cells_c["nr_rl"].astype(np.int64)
    rl["dup_rl"] = cells_c["dup_rl"].astype(np.int64)
    rg = {d: cells_c["rg_" + d].astype(np.int64) for d in sc.RG_DIMS}
    rg["nrun"] = cells_c["nrun"].astype(np.int64)
    dist = chars = comps = None
    if dist_c is not None:
        dist = {"coff": dist_c["coff"].astype(np.int64), "dps": dist_c["dps"].astype(np.int64),
                "deaths": dist_c["deaths"].astype(np.int64)}
    if chars_c is not None:
        chars = chars_c["char"].astype(np.int64)
    if comps_c is not None:
        K, C = int(comps_c.header["K"]), int(comps_c.header["n_comps"])
        clen = comps_c["clen"].astype(np.int64)
        mat = np.stack([comps_c[f"c{i}"].astype(np.int64) for i in range(K)], axis=0) if K else np.zeros((0, C))
        comp_list = [tuple(int(mat[i, j]) for i in range(int(clen[j]))) for j in range(C)]
        comps = {d: comps_c[d].astype(np.int64) for d in sc.COMP_DIMS}
        for k in ("n", "ksum", "kmin", "bday", "bdeaths", "dsum"):
            comps[k] = comps_c[k].astype(np.int64)
        comps["comps"] = comp_list
        comps["clen"] = clen
        comps["K"] = K
    return sc.CubeWeek(week=week, cells=cells, rl=rl, rg=rg, dist=dist, chars=chars, comps=comps,
                       cube_sha=h["cube_sha"])


class CubeCache:
    """Residency keyed by (W, cube_sha) (§3.2 generation guard): a file
    whose header cube_sha differs from the manifest entry -- or, for
    dist/chars/comps, from the resident cells of the same week -- is
    rejected unread (never sliced) and the week stays withheld for the
    views that need it; a manifest whose cube_sha changed for W drops all
    four resident files at once."""

    def __init__(self, slug_dir: pathlib.Path):
        self.slug_dir = pathlib.Path(slug_dir)
        self.weeks: dict = {}            # W -> CubeWeek
        self.rejected: list = []         # (W, part, reason)

    def refresh(self, manifest: dict) -> list:
        """Apply a (polled) manifest: returns the weeks dropped."""
        want = {int(w["w"]): w.get("cube_sha") for w in manifest.get("weeks", []) if w.get("f")}
        dropped = []
        for W in list(self.weeks):
            if want.get(W) != self.weeks[W].cube_sha:
                del self.weeks[W]
                dropped.append(W)
        return dropped

    def load(self, manifest: dict, weeks=None, parts=("cells", "dist", "chars", "comps"),
             override: dict | None = None) -> dict:
        """Load the named parts for the cubed weeks (all by default).
        `override` = {(W, part): relative path} lets a test point a part at
        another generation's file."""
        override = override or {}
        for w in manifest.get("weeks", []):
            W = int(w["w"])
            f = w.get("f")
            if not f or (weeks is not None and W not in weeks):
                continue
            cells_c = None
            cur = self.weeks.get(W)
            if cur is not None and cur.cube_sha == w.get("cube_sha"):
                continue
            rel = override.get((W, "cells"), f["cells"])
            try:
                cells_c = pf.read(self.slug_dir / rel, expect_kind="cells")
            except (OSError, pf.FormatError) as e:
                self.rejected.append((W, "cells", f"unreadable: {e}"))
                continue
            if cells_c.header.get("cube_sha") != w.get("cube_sha") or int(cells_c.header.get("week", -1)) != W:
                self.rejected.append((W, "cells", "cube_sha != manifest"))
                continue
            got = {}
            for part in ("dist", "chars", "comps"):
                if part not in parts:
                    got[part] = None
                    continue
                rel = override.get((W, part), f[part])
                try:
                    c = pf.read(self.slug_dir / rel, expect_kind=part)
                except (OSError, pf.FormatError) as e:
                    self.rejected.append((W, part, f"unreadable: {e}"))
                    got[part] = None
                    continue
                if c.header.get("cube_sha") != cells_c.header["cube_sha"] or int(c.header.get("week", -1)) != W:
                    self.rejected.append((W, part, "cube_sha != resident cells"))
                    got[part] = None
                    continue
                got[part] = c
            self.weeks[W] = _cube_week_from_files(W, cells_c, got["dist"], got["chars"], got["comps"])
        return self.weeks


def load_cubes(loaded: Loaded, weeks=None) -> dict:
    """{W: CubeWeek} for every cubed week the manifest names (all four
    parts), through the generation guard."""
    cc = CubeCache(loaded.slug_dir)
    return cc.load(loaded.manifest, weeks=weeks)


def unresident_weeks(loaded: Loaded) -> set:
    """Absolute weeks touched by a LISTED day that is not resident (§3.1
    loader obligation: a period touching an un-cubed week whose days are
    not all resident is withheld). Uses days[].w, which is informational
    for the builder but is exactly what a loader needs here."""
    have = {e["d"] for e in loaded.days}
    out = set()
    for e in loaded.manifest["days"]:
        if e["d"] == "undated" or not e.get("f") or e["d"] in have:
            continue
        for lo_hi in (e.get("w") or {}).values():
            out.update(range(int(lo_hi[0]), int(lo_hi[1]) + 1))
    return out


def period_check(site, loaded: Loaded, cubes: dict, buckets, need=("cells", "dist", "chars")) -> None:
    """Raise ValueError('withheld ...') when a period (set of buckets)
    touches a week the client cannot serve yet: an un-cubed week with an
    unresident listed day, or a cubed week whose needed parts are not
    resident (§3.1/§3.3 pre-arrival state). Buckets map to absolute weeks
    per region through site.curW."""
    cubed = {int(w["w"]) for w in loaded.manifest.get("weeks", []) if w.get("f")}
    missing = unresident_weeks(loaded)
    for reg, curW in site.curW.items():
        for b in buckets:
            if b >= 999:
                continue
            W = curW - b
            if b >= 3 and W in cubed:
                cw = cubes.get(W)
                if cw is None:
                    raise ValueError(f"withheld: week {W} cube not resident")
                if "dist" in need and cw.dist is None:
                    raise ValueError(f"withheld: week {W} dist not resident")
                if "chars" in need and cw.chars is None:
                    raise ValueError(f"withheld: week {W} chars not resident")
                if "comps" in need and cw.comps is None:
                    raise ValueError(f"withheld: week {W} comps not resident")
            elif W in missing:
                raise ValueError(f"withheld: week {W} has an unresident listed day")


# ---- the per-block sidecar join (§4.1) --------------------------------------
@dataclass
class Joined:
    map: np.ndarray                 # Int32Array(N): row -> shard row or -1
    blocks: list                    # accepted (day, spec_code, container, shard_base)
    dropped: list                   # (day, spec_code, reason)
    cols: dict                      # concatenated shard columns over accepted blocks


def join_blocks(loaded: Loaded, blocks: list, day_sha: dict | None = None) -> Joined:
    """`blocks` = [(day, spec_code, Container)] in any order. A block whose
    header `rows_sha[:10]` differs from the `<h>` of the day file the client
    loaded is dropped with a reason (§4.1). Re-running on a relayouted
    `loaded` (a day gone or replaced) re-derives map and columns from the
    retained blocks."""
    N = len(loaded.R["dps"])
    shas = loaded.day_sha if day_sha is None else day_sha
    mp = np.full(N, -1, dtype=np.int64)
    accepted, dropped = [], []
    base = 0
    cols: dict[str, list] = {}
    for day, code, c in sorted(blocks, key=lambda b: (str(b[0]), b[1])):
        want = shas.get(day)
        if want is None:
            dropped.append((day, code, "day not loaded"))
            continue
        if c.header["rows_sha"][:pf.NAME_HASH_LEN] != want:
            dropped.append((day, code, "rows_sha mismatch"))
            continue
        if c.header["day"] != day:
            dropped.append((day, code, "day mismatch"))
            continue
        pos = c["pos"].astype(np.int64)
        m = int(c.header["m"])
        if len(pos) != m or (len(pos) and int(pos.max()) >= loaded.day_n[day]):
            dropped.append((day, code, "malformed"))
            continue
        mp[loaded.row_base[day] + pos] = base + np.arange(m)
        for k, v in c.cols.items():
            cols.setdefault(k, []).append(v)
        accepted.append((day, code, c, base))
        base += m
    out = {k: np.concatenate(v) for k, v in cols.items()} if cols else {}
    return Joined(map=mp, blocks=accepted, dropped=dropped, cols=out)


def spec_dir_name(cls_name: str, spec_name: str) -> str:
    """`spec/<cls>-<spec>/`: lower-case names, non-alphanumerics dropped
    (Mage|Arcane -> mage-arcane, Death Knight-style names stay one token)."""
    def slug(s):
        return "".join(ch for ch in s.lower() if ch.isalnum())
    return f"{slug(cls_name)}-{slug(spec_name)}"


def load_blocks(loaded: Loaded, spec_codes=None) -> list:
    """Every shard block the manifest names for the loaded days (optionally
    restricted to composite spec codes), as (day, code, Container)."""
    out = []
    for e in loaded.days:
        for code, rel in (e.get("specs") or {}).items():
            code = int(code)
            if spec_codes is not None and code not in spec_codes:
                continue
            c = pf.read(loaded.slug_dir / rel, expect_kind="shard")
            out.append((e["d"], code, c))
    return out


def vocab_maps(spec_vocab_entry: dict, emb: list) -> dict:
    """The client's per-spec lookup maps (§4.3): per slot
    Map<(id, emb label|None) -> index+1>, per eslot Map<enchId -> idx+1>,
    Map<hash64 -> idx+1> for builds. `emb` = manifest.emb."""
    it = []
    for col in spec_vocab_entry["items"]:
        it.append({(e["id"], e.get("emb")): j + 1 for j, e in enumerate(col)})
    en = [{e["id"]: j + 1 for j, e in enumerate(col)} for col in spec_vocab_entry.get("ench", [])]
    bld = {int(b["h"], 16): j + 1 for j, b in enumerate(spec_vocab_entry.get("builds", [])) if b.get("h")}
    return {"it": it, "en": en, "bld": bld, "emb": emb}


if __name__ == "__main__":     # pragma: no cover - inspector
    import sys
    L = load_site(pathlib.Path(sys.argv[1]))
    print(L.manifest["slug"], "seq", L.manifest["seq"], "rows", len(L.R["dps"]),
          "days", [e["d"] for e in L.days])
