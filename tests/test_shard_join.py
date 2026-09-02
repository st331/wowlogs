#!/usr/bin/env python3
"""tests/test_shard_join.py (partitioned_payload.md §9.1, §4)

For every legacy-covered row, the values resolved through block + vocab
maps equal the legacy sidecar's (item id, emb), enchant id, build hash
and ten stats; `spec_vocab` equals the legacy `specs` object entry for
entry on this fixture (season = window; the added `w`/`h` annotations
excepted); a mutated `rows_sha` makes the block guard drop exactly that
block; a mid-window invalidation (a day dropped) re-derives `map` and the
synthesised columns correctly.
"""
import base64
import gzip
import json
import math
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import partition_client as pc                                    # noqa: E402
import partition_format as pf                                    # noqa: E402
import parts_util as pu                                          # noqa: E402
from test_rows_bitexact_vs_legacy import parts_keys              # noqa: E402


def dec(b, dt):
    return np.frombuffer(base64.b64decode(b), dtype=dt)


def legacy_builds(root: pathlib.Path) -> dict:
    with gzip.open(root / "site" / "builds.json.gz", "rt") as fh:
        doc = json.load(fh)
    N = doc["n"]
    if doc["enc"] == "sparse":
        idx = dec(doc["idx"], "<u4")
        mp = np.full(N, -1, np.int64)
        mp[idx] = np.arange(len(idx))
    else:
        mp = np.arange(N)
    cols = doc["cols"]
    return {"doc": doc, "mp": mp, "fl": dec(cols["fl"], "u1"), "it": [dec(b, "u1") for b in cols["it"]],
            "en": [dec(b, "u1") for b in cols.get("en", [])], "bld": dec(cols["bld"], "u1")}


def legacy_stats(root: pathlib.Path):
    p = root / "site" / "stats.json.gz"
    if not p.exists():
        return None
    with gzip.open(p, "rt") as fh:
        doc = json.load(fh)
    N = doc["n"]
    vals = dec(doc["data"], "<u2").reshape(-1, len(doc["stats"]))
    if doc["enc"] == "sparse":
        idx = dec(doc["idx"], "<u4")
        mp = np.full(N, -1, np.int64)
        mp[idx] = np.arange(len(idx))
    else:
        mp = np.arange(N)
    return {"stats": doc["stats"], "mp": mp, "vals": vals}


def test_shard_join():
    lroot, proot = pu.legacy_root(), pu.parts_root()
    L = pu.legacy_payload(lroot)
    lb = legacy_builds(lroot)
    ls = legacy_stats(lroot)
    lk = pu.legacy_keys(lroot)
    loaded = pc.load_site(proot / "site" / "d")
    man = loaded.manifest
    with gzip.open(loaded.slug_dir / man["spec_vocab"]["f"], "rt") as fh:
        vocab = json.load(fh)
    # ---- the vocab equals the legacy specs object entry for entry
    lspecs = lb["doc"]["specs"]
    assert set(lspecs) == set(vocab["specs"]), set(lspecs) ^ set(vocab["specs"])
    assert vocab["slots"] == lb["doc"]["slots"] and vocab["eslots"] == lb["doc"]["eslots"]
    for sk, entry in lspecs.items():
        v = vocab["specs"][sk]
        strip = lambda col: [{k: x for k, x in e.items() if k not in ("w", "h")} for e in col]
        assert [strip(c) for c in v["items"]] == entry["items"], sk
        assert v.get("ench") == entry.get("ench"), sk
        assert strip(v["builds"]) == entry["builds"], sk
        assert v.get("bkind") == entry.get("bkind")
        for b in v["builds"]:
            assert len(b["h"]) == 16 and (not b["s"].startswith("t:") or b["s"][2:] == b["h"][:12])
    # ---- the join: every legacy-covered row resolves to the same values
    pk = parts_keys(proot, loaded)
    blocks = pc.load_blocks(loaded)
    J = pc.join_blocks(loaded, blocks)
    assert not J.dropped, J.dropped
    maps = {sk: pc.vocab_maps(v, man["emb"]) for sk, v in vocab["specs"].items()}
    emb = man["emb"]
    n_cov = n_stats = 0
    slots = lb["doc"]["slots"]
    eslots = lb["doc"]["eslots"]
    for i in range(len(lk)):
        j = lb["mp"][i]
        # dense: every row has an index; a row is covered iff fl != 0
        if j < 0 or int(lb["fl"][j]) == 0:
            continue
        sk = f"{L['classes'][L['rows']['cls'][i]]}|{L['specs'][L['rows']['spec'][i]]}"
        c, f, ch, sv = lk["report_code"][i], lk["fight_id"][i], lk["character"][i], lk["server"][i]
        sv = "" if isinstance(sv, float) else str(sv)
        ch = "" if isinstance(ch, float) else str(ch)
        row = pk[(str(c), int(f), ch, sv)]
        r = J.map[row]
        assert r >= 0, (i, sk)
        n_cov += 1
        lfl = int(lb["fl"][j])
        pfl = int(J.cols["fl"][r])
        assert (pfl & 3) == lfl, (i, sk, lfl, pfl)
        m = maps[sk]
        entry = lspecs[sk]
        for si in range(len(slots)):
            li = int(lb["it"][si][j])
            lv = None if li == 0 else (entry["items"][si][li - 1]["id"], entry["items"][si][li - 1].get("emb"))
            iid = int(J.cols[f"it{si}"][r])
            lab = emb[int(J.cols[f"em{si}"][r])]
            key = (iid, None if lab == "" else lab)
            pi = m["it"][si].get(key, 0) if iid else 0
            pv = None if pi == 0 else key
            assert lv == pv, (i, sk, si, lv, pv, iid, lab)
        for jj, s in enumerate(eslots):
            b = int(lb["en"][jj >> 1][j])
            le = (b >> 4) if jj % 2 else (b & 15)
            lv = None if le == 0 else entry["ench"][jj][le - 1]["id"]
            ei = vocab["eslots"].index(s)
            eid = int(J.cols[f"en{ei}"][r])
            pi = m["en"][jj].get(eid, 0) if eid else 0
            pv = None if pi == 0 else eid
            assert lv == pv, (i, sk, s, lv, pv)
        lbi = int(lb["bld"][j])
        lv = None if lbi == 0 else entry["builds"][lbi - 1]["s"]
        h = int(J.cols["bld"][r])
        pi = m["bld"].get(h, 0) if h else 0
        pv = None if pi == 0 else vocab["specs"][sk]["builds"][pi - 1]["s"]
        assert lv == pv, (i, sk, lv, pv)
        if ls is not None:
            # dense: every row has an index and an unknown row is all zeros;
            # sparse: unknown rows have no index. Either way the ten values
            # the legacy sidecar resolves for the row equal the block's st*
            sj = ls["mp"][i]
            lvals = [int(x) for x in ls["vals"][sj]] if sj >= 0 else [0] * len(ls["stats"])
            known = sj >= 0 and any(lvals)
            n_stats += known
            if known:
                assert pfl & 4, (i, sk)
            for si, nm in enumerate(ls["stats"]):
                k = J.blocks[0][2].header["stats"].index(nm)
                assert int(J.cols[f"st{k}"][r]) == lvals[si], (i, sk, nm, lvals[si], int(J.cols[f"st{k}"][r]))
    assert n_cov == int((lb["fl"] != 0).sum()) and n_cov > 1000
    # nothing extra covered on the new side beyond bit2-only rows
    assert int((J.map >= 0).sum()) == n_cov + sum(1 for r in range(len(J.cols["fl"])) if J.cols["fl"][r] == 4)
    # ---- the block guard: a mutated rows_sha drops exactly that block
    day0, code0, c0 = blocks[0]
    bad = pf.Container(header=dict(c0.header, rows_sha="0" * 40), cols=c0.cols)
    J2 = pc.join_blocks(loaded, [(day0, code0, bad)] + blocks[1:])
    assert J2.dropped == [(day0, code0, "rows_sha mismatch")]
    assert int((J2.map >= 0).sum()) == int((J.map >= 0).sum()) - int(c0.header["m"])
    # ---- relayout: drop a mid-window day, the map is re-derived
    mid = loaded.days[len(loaded.days) // 2]["d"]
    L2 = pc.load_site(proot / "site" / "d", days=[e["d"] for e in loaded.days if e["d"] != mid])
    J3 = pc.join_blocks(L2, blocks)
    assert all(d == mid and why == "day not loaded" for d, _, why in J3.dropped) and J3.dropped
    assert len(L2.R["dps"]) == len(loaded.R["dps"]) - loaded.day_n[mid]
    # every retained row keeps its values through the new map
    pk2 = parts_keys(proot, L2)
    for key, row2 in list(pk2.items())[:2000]:
        row1 = pk[key]
        a, b = J.map[row1], J3.map[row2]
        assert (a >= 0) == (b >= 0)
        if a >= 0:
            for k in ("fl", "it0", "it12", "em0", "en0", "bld", "st0"):
                assert J.cols[k][a] == J3.cols[k][b], (key, k)
    print(f"shard join: {n_cov} covered rows equal ({n_stats} with stats), {len(blocks)} blocks, "
          f"{len(vocab['specs'])} vocab specs")


def test_specstats_equals_legacy():
    """meta/specstats.<h>.json.gz (merged from the per-day partials) equals
    legacy spec_stats_block() -- payload["specstats"] -- on this fixture
    (season = window), cohort string included."""
    lroot, proot = pu.legacy_root(), pu.parts_root()
    L = pu.legacy_payload(lroot)
    loaded = pc.load_site(proot / "site" / "d")
    man = loaded.manifest
    assert ("specstats" in L) == bool(man.get("specstats"))
    if not man.get("specstats"):
        return
    with gzip.open(loaded.slug_dir / man["specstats"]["f"], "rt") as fh:
        block = json.load(fh)
    assert block == L["specstats"], [k for k in set(block) | set(L["specstats"])
                                     if block.get(k) != L["specstats"].get(k)]
    print(f"specstats: {len(block['specs'])} specs equal to legacy")


if __name__ == "__main__":
    test_shard_join()
    test_specstats_equals_legacy()
    print("test_shard_join: all green")
