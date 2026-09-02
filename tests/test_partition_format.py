#!/usr/bin/env python3
"""tests/test_partition_format.py (partitioned_payload.md §9.1)

Writer/reader round trips for every dtype, planar, delta, u64; clamp
counters; generation fields present per kind; determinism and the
content-hashed name contract of §2.1.
"""
import gzip
import hashlib
import pathlib
import struct
import sys
import tempfile

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import partition_format as pf                                   # noqa: E402

rng = np.random.default_rng(7)


def _rows_header(**over):
    h = {"day": 245, "runs": 3, "inputs_sha": "a" * 64, "rules_sha": "b" * 64,
         "flags": {"tier": True, "timed": True, "post": False, "tmul": False}}
    h.update(over)
    return h


def _cube_header(kind):
    h = {"week": 33, "cube_sha": "c" * 64}
    if kind == "cells":
        h["n_cells"] = 4
    if kind == "comps":
        h["K"] = 5
    return h


def _shard_header():
    return {"spec": "Mage|Arcane", "spec_code": 1, "day": 245, "rows_sha": "d" * 40,
            "m": 5, "slots": list(range(16)), "eslots": [0, 4, 6],
            "stats": ["Crit", "Haste"]}


def test_every_dtype_round_trips():
    cols = []
    want = {}
    for t, dt in pf.DTYPES.items():
        info = np.iinfo(dt)
        v = rng.integers(info.min, info.max, size=257, endpoint=True).astype(dt)
        v[0], v[1] = info.min, info.max              # the extremes survive
        for p in (False, True):
            k = f"{t}_{'p' if p else 'i'}"
            cols.append(pf.Column(k, t, v, p=p))
            want[k] = v
    u = rng.integers(0, 1 << 53, size=257, endpoint=False).astype(np.uint64)
    u[0], u[1] = 0, (1 << 53) - 1
    cols.append(pf.Column("big", "u64", u))
    want["big"] = u
    enc = pf.encode("pairs", "s2", 257, cols)
    c = pf.decode(enc.gz)
    assert c.kind == "pairs" and c.n == 257
    for k, v in want.items():
        assert c[k].dtype == v.dtype, (k, c[k].dtype, v.dtype)
        assert np.array_equal(c[k], v), k
    # the u64 halves are what is on the wire, both u32 planar
    meta = {m["k"]: m for m in c.header["cols"]}
    assert meta["big_lo"]["t"] == "u32" and meta["big_lo"]["p"] == 1
    assert meta["big_hi"]["t"] == "u32" and meta["big_hi"]["p"] == 1
    assert "big" not in meta
    assert np.array_equal((c["big_hi"].astype(np.uint64) << np.uint64(32))
                          | c["big_lo"].astype(np.uint64), u)


def test_layout_alignment_and_planar_bytes():
    v16 = np.array([0x0102, 0xA0B0, 7], dtype=np.uint16)
    enc = pf.encode("pairs", "s2", 3, [pf.Column("a", "u8", [1, 2, 3]),
                                      pf.Column("b", "u16", v16, p=True),
                                      pf.Column("c", "u32", [1, 2, 3], p=True)])
    payload = enc.payload
    assert payload[:4] == b"WLP1"
    (H,) = struct.unpack("<I", payload[4:8])
    assert (8 + H) % 8 == 0
    hdr = enc.header
    for m in hdr["cols"]:
        assert m["off"] % 8 == 0
    data = payload[8 + H:]
    off_b = next(m["off"] for m in hdr["cols"] if m["k"] == "b")
    # plane 0 = low bytes of every item, plane 1 = high bytes
    assert data[off_b:off_b + 6] == bytes([0x02, 0xB0, 0x07, 0x01, 0xA0, 0x00])
    # a planar u32 column of three items is 3 bytes x 4 planes, then padding to 8
    off_c = next(m["off"] for m in hdr["cols"] if m["k"] == "c")
    assert data[off_c:off_c + 12] == bytes([1, 2, 3] + [0] * 9)
    assert len(data) % 8 == 0
    # the on-disk stream is gzip and inflates to the payload
    assert gzip.decompress(enc.gz) == payload


def test_delta_with_groups_and_wraparound():
    # a dist-style file: coff gives the group starts; dps ascending per cell
    coff = np.array([0, 3, 3, 7, 9], dtype=np.uint32)     # an empty cell too
    dps = np.array([5, 9, 9, 2, 4, 6, 8, 0xFFFFFFFF, 1], dtype=np.uint32)
    enc = pf.encode("dist", "s2", 9, [pf.Column("coff", "u32", coff),
                                     pf.Column("dps", "u32", dps, p=True, d=True),
                                     pf.Column("deaths", "u8", [0] * 9)],
                    header=_cube_header("dist"))
    # what is on the wire: deltas reset at 0, 3, 7 (u32 wraparound at the end)
    c_raw = pf.decode_payload(enc.payload)
    assert np.array_equal(c_raw["dps"], dps)             # decoder undid it
    raw = np.frombuffer(enc.payload, dtype=np.uint8)
    (H,) = struct.unpack("<I", bytes(raw[4:8]))
    m = next(x for x in enc.header["cols"] if x["k"] == "dps")
    planes = raw[8 + H + m["off"]:8 + H + m["off"] + 36].reshape(4, 9)
    stored = (planes.T.copy().reshape(-1).view("<u4"))
    assert list(stored) == [5, 4, 0, 2, 2, 2, 2, 0xFFFFFFFF, 2]   # (1 - 0xFFFFFFFF) mod 2^32 == 2
    # a delta column outside u32 is refused
    try:
        pf.encode("pairs", "s2", 2, [pf.Column("x", "u16", [1, 2], d=True)])
    except pf.FormatError:
        pass
    else:
        raise AssertionError("delta on u16 accepted")
    # pairs: no coff -> one group from 0
    ch = np.array([3, 10, 11, 4_000_000], dtype=np.uint32)
    enc2 = pf.encode("pairs", "s2", 4, [pf.Column("char", "u32", ch, p=True, d=True),
                                       pf.Column("score", "u16", [1, 2, 3, 4])])
    assert np.array_equal(pf.decode(enc2.gz)["char"], ch)


def test_clamp_counters_and_range_faults():
    deaths = [0, 3, 300, 1000, 15]
    dur = [1800, 70000, 65535]
    enc = pf.encode("rows", "s2", 5,
                    [pf.Column("deaths", "u8", deaths, clamp=(0, 255)),
                     pf.Column("r_dur", "u16", dur, clamp=(0, 65535)),
                     pf.Column("tier", "i8", [-1, 0, 5, 2, 4])],
                    header=_rows_header())
    assert enc.clamped == {"deaths": 2, "r_dur": 1}
    c = pf.decode(enc.gz)
    assert list(c["deaths"]) == [0, 3, 255, 255, 15]
    assert list(c["r_dur"]) == [1800, 65535, 65535]
    lines = pf.clamp_health_lines("rows/d245.abc.bin", enc.clamped)
    assert lines == ["parts.clamped.rows/d245.abc.bin.deaths=2",
                     "parts.clamped.rows/d245.abc.bin.r_dur=1"]
    assert pf.clamp_health_lines("x", {"a": 0}) == []
    # no clamp declared and a value outside the dtype: a fault, never a wrap
    for t, bad in (("u8", [256]), ("i8", [128]), ("u16", [-1]), ("u32", [1 << 32])):
        try:
            pf.encode("pairs", "s2", 1, [pf.Column("x", t, bad)])
        except pf.FormatError:
            pass
        else:
            raise AssertionError(f"{t} {bad} accepted")
    # non-integer values are a fault too
    try:
        pf.encode("pairs", "s2", 2, [pf.Column("x", "u32", [1.5, 2.0])])
    except pf.FormatError:
        pass
    else:
        raise AssertionError("float accepted")
    # a clamp wider than the dtype is a programming error
    try:
        pf.encode("pairs", "s2", 1, [pf.Column("x", "u8", [1], clamp=(0, 300))])
    except pf.FormatError:
        pass
    else:
        raise AssertionError("clamp beyond dtype accepted")


def test_generation_fields_per_kind():
    ok = {
        "rows": _rows_header(),
        "shard": _shard_header(),
        "cells": _cube_header("cells"), "dist": _cube_header("dist"),
        "chars": _cube_header("chars"), "comps": _cube_header("comps"),
        "pairs": {},
    }
    col = [pf.Column("x", "u8", [1, 2])]
    for kind, hdr in ok.items():
        enc = pf.encode(kind, "s2", 2, col, header=hdr)
        c = pf.decode(enc.gz, expect_kind=kind)
        for f in pf.GENERATION_FIELDS[kind]:
            assert f in c.header, (kind, f)
        # every generation field is refused when absent
        for f in pf.GENERATION_FIELDS[kind] + pf.KIND_FIELDS[kind]:
            if kind == "rows" and f == "rows_sha":
                continue                   # computed by the writer
            h2 = dict(hdr)
            del h2[f]
            try:
                pf.encode(kind, "s2", 2, col, header=h2)
            except pf.FormatError:
                pass
            else:
                raise AssertionError(f"{kind} accepted without {f}")
    # a kind nobody defined
    try:
        pf.encode("blob", "s2", 2, col)
    except pf.FormatError:
        pass
    else:
        raise AssertionError("unknown kind accepted")
    # expect_kind is enforced on read
    enc = pf.encode("chars", "s2", 2, col, header=_cube_header("chars"))
    try:
        pf.decode(enc.gz, expect_kind="dist")
    except pf.FormatError:
        pass
    else:
        raise AssertionError("kind mismatch accepted")
    # season and n are part of the common header
    assert c.header["season"] == "s2" and c.header["n"] == 2 and c.header["v"] == 1


def test_names_determinism_and_disk_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        cols = [pf.Column("dps", "u32", rng.integers(0, 400_000, 1000), p=True),
                pf.Column("char", "u32", rng.integers(0, 2_000_000, 1000), p=True),
                pf.Column("cls", "u8", rng.integers(0, 13, 1000))]
        w1 = pf.write(tmp, "d245", "rows", "s2", 1000, cols, header=_rows_header())
        w2 = pf.write(tmp, "d245", "rows", "s2", 1000, cols, header=_rows_header())
        # a pure function of its inputs: same bytes, same name, one file
        assert w1.gz == w2.gz and w1.name == w2.name
        assert sorted(p.name for p in pathlib.Path(tmp).iterdir()) == [w1.name]
        assert not list(pathlib.Path(tmp).glob("*.tmp"))
        stem, h, ext = pf.parse_name(w1.name)
        assert (stem, ext) == ("d245", "bin") and len(h) == 10
        # rows: the name is rows_sha[:10], the header carries the full sha,
        # and the sha is over the content with the field blanked
        assert h == w1.header["rows_sha"][:10]
        probe = dict(w1.header)
        probe["rows_sha"] = ""
        (H,) = struct.unpack("<I", w1.payload[4:8])
        data = w1.payload[8 + H:]
        assert w1.header["rows_sha"] == hashlib.sha1(
            pf.canonical_json(probe).encode() + data).hexdigest()
        c = pf.read(w1.path, expect_kind="rows")
        for col in cols:
            assert np.array_equal(c[col.k], np.asarray(col.values))
        assert c.header["day"] == 245 and c.header["rules_sha"] == "b" * 64
        # a different rules_sha is a different file (generation in the name)
        w3 = pf.write(tmp, "d245", "rows", "s2", 1000, cols,
                      header=_rows_header(rules_sha="e" * 64))
        assert w3.name != w1.name
        # every other kind: name = sha1 of the gzip bytes
        w4 = pf.write(tmp, "w33.cells", "cells", "s2", 4,
                      [pf.Column("n", "u32", [1, 2, 3, 4]),
                       pf.Column("dsum", "u64", [10, 20, 30, 1 << 40])],
                      header=_cube_header("cells"))
        assert pf.parse_name(w4.name)[1] == hashlib.sha1(w4.gz).hexdigest()[:10]
        assert pf.content_sha(w4.path) == hashlib.sha1(w4.gz).hexdigest()
        c4 = pf.read(w4.path)
        assert list(c4["dsum"]) == [10, 20, 30, 1 << 40]
        # a renamed file (name hash != content) is rejected on read
        bad = pathlib.Path(tmp) / "w33.cells.0123456789.bin"
        bad.write_bytes(w4.gz)
        try:
            pf.read(bad)
        except pf.FormatError:
            pass
        else:
            raise AssertionError("name/content mismatch accepted")
        assert pf.read(bad, check_name=False).n == 4
        # a shard names the day it was built against by the same prefix
        w5 = pf.write(tmp, "d245", "shard", "s2", 3,
                      [pf.Column("pos", "u32", [0, 5, 9], p=True)],
                      header=dict(_shard_header(), rows_sha=w1.header["rows_sha"]))
        assert w5.header["rows_sha"][:10] == pf.parse_name(w1.name)[1]


def test_corruption_is_rejected():
    enc = pf.encode("pairs", "s2", 3, [pf.Column("x", "u32", [1, 2, 3], p=True)])
    for mutate in (
        lambda b: b"XXXX" + b[4:],                          # magic
        lambda b: b[:4] + struct.pack("<I", 3) + b[8:],     # unaligned header
        lambda b: b[:-5],                                   # truncated data
        lambda b: b[:8] + b"{bad json" + b[17:],            # header not JSON
    ):
        try:
            pf.decode_payload(mutate(enc.payload))
        except pf.FormatError:
            pass
        else:
            raise AssertionError("corrupt payload accepted")
    try:
        pf.decode(b"not gzip at all")
    except pf.FormatError:
        pass
    else:
        raise AssertionError("non-gzip accepted")


def test_empty_columns_and_fuzz():
    # a day with zero rows is still a valid file
    enc = pf.encode("rows", "s2", 0, [pf.Column("dps", "u32", [], p=True),
                                     pf.Column("dps2", "u32", [], p=True, d=True)],
                    header=_rows_header(runs=0))
    c = pf.decode(enc.gz)
    assert c.n == 0 and len(c["dps"]) == 0 and len(c["dps2"]) == 0
    for _ in range(40):
        n = int(rng.integers(0, 300))
        cols, want = [], {}
        for j, t in enumerate(rng.choice(list(pf.DTYPES) + ["u64"], size=4)):
            if t == "u64":
                v = rng.integers(0, 1 << 53, n, dtype=np.int64).astype(np.uint64)
            else:
                info = np.iinfo(pf.DTYPES[t])
                v = rng.integers(info.min, info.max, n, endpoint=True).astype(pf.DTYPES[t])
            p = bool(rng.integers(0, 2)) if t != "u64" else False
            d = (t == "u32") and bool(rng.integers(0, 2))
            k = f"c{j}"
            cols.append(pf.Column(k, t, v, p=p, d=d))
            want[k] = v
        groups = sorted(set(rng.integers(0, max(n, 1), size=3).tolist()))
        enc = pf.encode("pairs", "s2", n, cols, groups=groups)
        c = pf.decode(enc.gz)
        for k, v in want.items():
            # a delta column decodes with the file's coff; without one the
            # group list at write time must have been [0] to round-trip, so
            # only compare when no custom group split a delta column
            m = next(x for x in c.header["cols"] if x["k"] in (k, k + "_lo"))
            if m.get("d") and groups != [0]:
                continue
            assert np.array_equal(c[k], v), (k, n)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("test_partition_format: all green")
