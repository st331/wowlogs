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
    V = man["vocab"]
    D = {"season": man.get("season"), "epoch": man["epoch"], "built": man["built"],
         "classes": V["classes"], "specs": V["specs"], "heroes": V["heroes"],
         "dungeons": V["dungeons"], "regions": V["regions"], "roles": V["roles"],
         "spec_role": man.get("spec_role"), "pars": man["pars"],
         "tuning": man.get("tuning"), "projection": man.get("projection"),
         "charscore": None}
    arr = charscore_array(slug_dir, man)
    D["charscore"] = arr.tolist()
    return Loaded(manifest=man, D=D, R=R, days=order, rbase=rbase, row_base=row_base,
                  day_n=day_n, day_sha=day_sha, charscore=arr, root=root,
                  slug_dir=slug_dir, containers=containers)


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
