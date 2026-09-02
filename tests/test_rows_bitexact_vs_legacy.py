#!/usr/bin/env python3
"""tests/test_rows_bitexact_vs_legacy.py (partitioned_payload.md §9.1)

Legacy build() with MAX_RUNS=0 and WOWLOGS_PINS vs the day files, joined
on (report_code, fight_id, character, server): every column equal
INCLUDING hero, tier and tmul (names mapped through both vocabularies;
durations against round()), run/char bijections, charscore[char] equal,
and the surviving copy of the midnight-straddling duplicate pair is the
same on both sides.
"""
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import partition_client as pc                                    # noqa: E402
import partition_format as pf                                    # noqa: E402
import parts_util as pu                                          # noqa: E402
from fixture_util import fixture                                 # noqa: E402

NAME_COLS = {"cls": "classes", "spec": "specs", "hero": "heroes", "dun": "dungeons",
             "reg": "regions", "role": "roles"}
VALUE_COLS = ("key", "deaths", "dps", "dur", "kdur", "timed", "post", "day", "hr", "tier")


def parts_keys(root: pathlib.Path, loaded: pc.Loaded) -> dict:
    """(code, fid, character, server) per concatenated window row, in the
    client's row order, from the builder's keys.npz caches."""
    out = {}
    days_dir = root / "data" / "processed" / "parts" / loaded.manifest["slug"] / "days"
    for e in loaded.days:
        dkey = -1 if e["d"] == "undated" else e["d"]          # the undated day's state key is -1
        with np.load(days_dir / f"d{dkey}" / "keys.npz", allow_pickle=False) as z:
            code, fid, ch, sv = z["code"], z["fid"], z["character"], z["server"]
        assert len(code) == e["n"]
        base = loaded.row_base[e["d"]]
        for i in range(len(code)):
            out[(str(code[i]), int(fid[i]), str(ch[i]), str(sv[i]))] = base + i
    return out


def test_rows_bitexact_vs_legacy():
    fx = fixture()
    lroot = pu.legacy_root()
    proot = pu.parts_root()
    L = pu.legacy_payload(lroot)
    LR = L["rows"]
    lk = pu.legacy_keys(lroot)
    loaded = pc.load_site(proot / "site" / "d")
    R, D = loaded.R, loaded.D
    N = len(LR["dps"])
    assert len(R["dps"]) == N, (len(R["dps"]), N)
    assert loaded.manifest["window"]["rows"] == N
    pk = parts_keys(proot, loaded)
    assert len(pk) == N
    # the join
    order = np.empty(N, dtype=np.int64)
    for i, (c, f, ch, sv) in enumerate(zip(lk["report_code"], lk["fight_id"], lk["character"], lk["server"])):
        sv = "" if isinstance(sv, float) else str(sv)
        ch = "" if isinstance(ch, float) else str(ch)
        order[i] = pk[(str(c), int(f), ch, sv)]
    assert len(set(order.tolist())) == N
    # names through both vocabularies
    for col, vocab in NAME_COLS.items():
        ln = np.array(L[vocab])[np.asarray(LR[col])]
        pn = np.array(D[vocab])[R[col][order]]
        bad = np.nonzero(ln != pn)[0]
        assert not len(bad), (col, len(bad), ln[bad[:5]], pn[bad[:5]])
    # values (durations are round()ed on both sides: legacy stores ints too)
    for col in VALUE_COLS:
        lv = np.asarray(LR[col], dtype=np.int64)
        pv = R[col][order]
        bad = np.nonzero(lv != pv)[0]
        assert not len(bad), (col, len(bad), lv[bad[:5]], pv[bad[:5]])
    # tmul: present on both sides or neither (RULES is empty today -> neither)
    assert ("tmul" in LR) == ("tmul" in R)
    if "tmul" in LR:
        assert np.array_equal(np.asarray(LR["tmul"]), R["tmul"][order])
    # run bijection
    lr = np.asarray(LR["run"])
    pr = R["run"][order]
    m1, m2 = {}, {}
    for a, b in zip(lr.tolist(), pr.tolist()):
        assert m1.setdefault(a, b) == b and m2.setdefault(b, a) == a
    assert len(m1) == loaded.manifest["window"]["runs"] == int(lr.max()) + 1
    # char bijection + charscore
    lc = np.asarray(LR["char"])
    pcs = R["char"][order]
    c1, c2 = {}, {}
    for a, b in zip(lc.tolist(), pcs.tolist()):
        assert c1.setdefault(a, b) == b and c2.setdefault(b, a) == a
    assert int(pcs.max()) < loaded.manifest["char_max"]
    lscore = np.asarray(L["charscore"])
    for a, b in c1.items():
        assert int(lscore[a]) == int(loaded.charscore[b]), (a, b, lscore[a], loaded.charscore[b])
    # the duplicate pair: the same survivor on both sides
    twin, copy = fx["notes"]["dup_pair"]["twin"], fx["notes"]["dup_pair"]["copy"]
    lcodes = set(zip(lk["report_code"], lk["fight_id"]))
    pcodes = {(k[0], k[1]) for k in pk}
    assert (copy[0], copy[1]) in lcodes and (copy[0], copy[1]) in pcodes
    assert (twin[0], twin[1]) not in lcodes and (twin[0], twin[1]) not in pcodes
    # the late character (registered last) is present once on both sides
    late = fx["notes"]["late_character"]["run"]
    assert (late[0], late[1]) in pcodes and (late[0], late[1]) in lcodes
    print(f"rows bit-exact: {N} rows, {len(m1)} runs, {len(c1)} chars, "
          f"{len(loaded.days)} day files")


def test_day_file_contracts():
    """§2.2: content-deterministic row order, dense day-local runs, the
    header fields, and every listed file's name hash = its content."""
    proot = pu.parts_root()
    loaded = pc.load_site(proot / "site" / "d")
    man = loaded.manifest
    for e in loaded.days:
        c = loaded.containers[e["d"]]
        h = c.header
        assert h["kind"] == "rows" and h["day"] == e["d"] and h["n"] == e["n"] and h["runs"] == e["runs"]
        assert h["rules_sha"] == e["rules_sha"] and len(h["inputs_sha"]) == 64
        assert set(h["flags"]) == {"tier", "timed", "post", "tmul"}
        run = c["run"].astype(np.int64)
        assert run[0] == 0 and np.all(np.diff(run) >= 0) and np.all(np.diff(run) <= 1)
        assert int(run.max()) + 1 == e["runs"]
        assert c["run"].dtype == np.uint16 if e["runs"] < 65536 else np.uint32
        for k in ("r_dun", "r_key", "r_reg", "r_timed", "r_post", "r_hr", "r_dur", "r_kdur"):
            assert len(c[k]) == e["runs"], k
    assert man["days"][-1]["d"] == "undated"
    assert sum(1 for e in man["days"] if e["d"] == "undated") == 1
    assert [e["d"] for e in man["days"][:-1]] == sorted(e["d"] for e in man["days"][:-1])
    # the undated run of the fixture is served once, from rows/undated.<h>.bin,
    # and its shard blocks carry the same spelling as the manifest entry (§2.2)
    und = man["days"][-1]
    assert und["f"] and und["f"].startswith("rows/undated.") and und["n"] == 5, und
    for rel in und["specs"].values():
        assert pf.read(loaded.slug_dir / rel, expect_kind="shard").header["day"] == "undated", rel
    assert int((loaded.R["day"] == -1).sum()) == und["n"]
    assert man["char_max"] >= int(loaded.R["char"].max()) + 1
    print("day file contracts ok")


if __name__ == "__main__":
    test_rows_bitexact_vs_legacy()
    test_day_file_contracts()
    print("test_rows_bitexact_vs_legacy: all green")
