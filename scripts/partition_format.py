#!/usr/bin/env python3
"""WLP1 -- the typed container every partitioned `.bin` is written in.

Reference implementation of fleet/blueprints/partitioned_payload.md §2.1; the
client decoder (PR-2) is written against this file and
tests/test_partition_format.py round-trips every dtype and encoding.

    gzip(level 9, mtime 0) of:
      0..3      "WLP1"
      4..7      u32 LE  H = header length in bytes
      8..8+H    UTF-8 JSON header, space-padded so 8+H is a multiple of 8
      8+H..     data area; column i starts at header.cols[i].off (relative to
                the data start, always a multiple of 8) and spans
                cols[i].n * itemsize bytes

Header, common part:
    {"v":1, "kind":<kind>, "season":<slug>, "n":<primary row count>,
     "cols":[{"k","t","n","off","p","d"}, ...], ...kind-specific fields...}

  t   in {u8,i8,u16,i16,u32,i32}, little-endian throughout. A logical `u64`
      column is written as two u32 planar columns `<k>_lo`, `<k>_hi`; the
      reader recombines them (every u64 here is a sum below 2^53).
  p:1 byte-planar: an item size s column is laid out as s planes of n bytes,
      plane 0 = the least-significant byte of every item.
  d:1 delta-coded (u32 only): item i is stored as v[i] - v[i-1] with u32
      wraparound; the running value resets to 0 at every group start. Groups
      are the file's `coff` column when it has one (kind `dist`), else the
      single group starting at 0.

Generation fields (part of the common header, checked before any byte of the
data area is used): kind `rows` carries `rows_sha` and `rules_sha`; kinds
`cells`/`dist`/`chars`/`comps` carry `week` and `cube_sha`; kind `shard`
carries the `rows_sha` of its day. `pairs` has none.

Content-hashed names: `<base>.<sha1[:10]>.<ext>`. For every kind but `rows`
the hash is the sha1 of the gzip payload. A `rows` header carries its own
`rows_sha`, which therefore cannot be the sha1 of the stream that contains
it: `rows_sha` = sha1 of the canonical header JSON with `rows_sha` blanked
followed by the data area, and the day file is named by its first ten hex
digits, so a shard's `rows_sha[:10]` equals the `<h>` of the day file it was
built against (§4.1). Both are pure functions of the content.

Clamps: a column may declare `clamp=(lo, hi)`; values outside are clipped and
COUNTED, and `clamp_health_lines()` turns the counts into the
`parts.clamped.<file>.<col>=<n>` health lines of §2.1. A value outside the
dtype's range with no clamp declared is a data fault and raises.

No timestamp lives inside any file: identical inputs give identical bytes and
identical names (§6.3). No I/O here beyond reading and writing files.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import pathlib
import struct
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

MAGIC = b"WLP1"
CONTAINER_VERSION = 1
FORMAT_VERSION = 3          # bumped whenever the byte layout of any kind changes
NAME_HASH_LEN = 10

KINDS = ("rows", "shard", "cells", "dist", "chars", "comps", "pairs")

DTYPES = {
    "u8": np.dtype("<u1"), "i8": np.dtype("<i1"),
    "u16": np.dtype("<u2"), "i16": np.dtype("<i2"),
    "u32": np.dtype("<u4"), "i32": np.dtype("<i4"),
}
U64 = "u64"

# The header fields a reader must see before it touches the data area (§2.1
# "Generation fields"). Required at write time so a file can never ship
# without the field the client rejects it on.
GENERATION_FIELDS = {
    "rows": ("rows_sha", "rules_sha"),
    "shard": ("rows_sha",),
    "cells": ("week", "cube_sha"),
    "dist": ("week", "cube_sha"),
    "chars": ("week", "cube_sha"),
    "comps": ("week", "cube_sha"),
    "pairs": (),
}
# Further header fields each kind's contract names (§2.2, §3.2, §4.2). Also
# required, so a builder cannot forget one; the client reads them.
KIND_FIELDS = {
    "rows": ("day", "runs", "inputs_sha", "flags"),
    "shard": ("spec", "spec_code", "day", "m", "slots", "eslots", "stats"),
    "cells": ("n_cells",),
    "dist": (),
    "chars": (),
    "comps": ("K",),
    "pairs": (),
}


class FormatError(ValueError):
    """A file that is not a WLP1 container, or a header its kind forbids."""


@dataclass
class Column:
    """One column to write. `values` may be any sequence; it is converted."""
    k: str
    t: str
    values: object
    p: bool = False          # byte-planar
    d: bool = False          # delta-coded (u32 only)
    clamp: tuple | None = None   # (lo, hi) inclusive; outside -> clipped + counted


@dataclass
class Encoded:
    header: dict
    payload: bytes           # the inflated container (magic + header + data)
    gz: bytes                # what goes on disk / on the wire
    sha: str                 # full 40-hex content hash the name is cut from
    clamped: dict = field(default_factory=dict)   # column -> clipped count


@dataclass
class Written(Encoded):
    path: pathlib.Path = None
    name: str = ""


@dataclass
class Container:
    header: dict
    cols: dict               # k -> numpy array (u64 recombined under its own k)
    path: pathlib.Path | None = None

    @property
    def kind(self):
        return self.header["kind"]

    @property
    def n(self):
        return self.header["n"]

    def __getitem__(self, k):
        return self.cols[k]


# ----------------------------------------------------------------- helpers
def canonical_json(obj) -> str:
    """The one JSON spelling a hash is taken over (sorted keys, no spaces)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def hashed_name(base: str, sha: str, ext: str = "bin") -> str:
    return f"{base}.{sha[:NAME_HASH_LEN]}.{ext}"


def parse_name(name: str) -> tuple[str, str, str]:
    """'d245.3fa9c1e2ab.bin' -> ('d245', '3fa9c1e2ab', 'bin')."""
    base, _, rest = name.rpartition(".")
    stem, _, sha = base.rpartition(".")
    if not stem or len(sha) != NAME_HASH_LEN:
        raise FormatError(f"not a content-hashed name: {name}")
    return stem, sha, rest


def _dtype_range(t: str) -> tuple[int, int]:
    info = np.iinfo(DTYPES[t])
    return int(info.min), int(info.max)


def _pad8(n: int) -> int:
    return (-n) % 8


def _planarize(arr: np.ndarray) -> bytes:
    """s planes of n bytes, plane 0 = least-significant byte (LE)."""
    s = arr.dtype.itemsize
    if s == 1:
        return arr.tobytes()
    b = arr.view(np.uint8).reshape(len(arr), s)
    return np.ascontiguousarray(b.T).tobytes()


def _unplanarize(buf: memoryview, n: int, dt: np.dtype) -> np.ndarray:
    s = dt.itemsize
    if s == 1:
        return np.frombuffer(buf, dtype=dt, count=n).copy()
    planes = np.frombuffer(buf, dtype=np.uint8, count=n * s).reshape(s, n)
    out = np.empty((n, s), dtype=np.uint8)
    out[:] = planes.T
    return out.reshape(-1).view(dt).copy()


def _delta_encode(v: np.ndarray, groups: Sequence[int]) -> np.ndarray:
    """v[i] - v[i-1] with u32 wraparound; the running value resets to 0 at
    every offset in `groups` (a group start means v[start] - 0)."""
    v = v.astype(np.uint32, copy=False)
    prev = np.empty_like(v)
    if len(v):
        prev[0] = 0
        prev[1:] = v[:-1]
        for g in groups:
            if 0 <= g < len(v):
                prev[g] = 0
    return (v - prev).astype(np.uint32)          # numpy wraps unsigned


def _delta_decode(d: np.ndarray, groups: Sequence[int]) -> np.ndarray:
    """Inverse of _delta_encode: cumulative sum per group, u32 wraparound."""
    d = d.astype(np.uint32, copy=False)
    out = np.empty_like(d)
    starts = sorted({int(g) for g in groups if 0 <= g < len(d)} | ({0} if len(d) else set()))
    bounds = starts + [len(d)]
    for a, b in zip(bounds, bounds[1:]):
        out[a:b] = np.cumsum(d[a:b], dtype=np.uint32)
    return out


def _coerce(col: Column) -> tuple[np.ndarray, int]:
    """Values -> exact dtype after clamp; returns (array, clipped count)."""
    if col.t == U64:
        v = np.asarray(col.values, dtype=np.uint64).reshape(-1)
        if col.clamp is not None:
            lo, hi = col.clamp
            bad = int(((v < lo) | (v > hi)).sum())
            v = np.clip(v, lo, hi).astype(np.uint64)
        else:
            bad = 0
        if len(v) and int(v.max()) >= (1 << 53):
            raise FormatError(f"{col.k}: u64 above 2^53 (client Number)")
        return v, bad
    if col.t not in DTYPES:
        raise FormatError(f"{col.k}: unknown dtype {col.t!r}")
    v = np.asarray(col.values).reshape(-1)
    if v.dtype.kind == "b":
        v = v.astype(np.int64)
    if v.dtype.kind not in "iu":
        vf = np.asarray(v, dtype=np.float64)
        if not np.all(np.isfinite(vf)) or not np.all(vf == np.floor(vf)):
            raise FormatError(f"{col.k}: non-integer values")
        v = vf.astype(np.int64)
    v = v.astype(np.int64, copy=False)
    lo_t, hi_t = _dtype_range(col.t)
    bad = 0
    if col.clamp is not None:
        lo, hi = col.clamp
        if lo < lo_t or hi > hi_t:
            raise FormatError(f"{col.k}: clamp {col.clamp} outside {col.t}")
        bad = int(((v < lo) | (v > hi)).sum())
        v = np.clip(v, lo, hi)
    if len(v) and (int(v.min()) < lo_t or int(v.max()) > hi_t):
        raise FormatError(f"{col.k}: value outside {col.t} range and no clamp "
                          f"declared (min {int(v.min())}, max {int(v.max())})")
    return v.astype(DTYPES[col.t]), bad


def _expand(columns: Iterable[Column]) -> tuple[list[tuple[Column, np.ndarray]], dict]:
    """Coerce every column; split u64 into <k>_lo / <k>_hi u32 planar."""
    out, clamped = [], {}
    for c in columns:
        v, bad = _coerce(c)
        if bad:
            clamped[c.k] = bad
        if c.t == U64:
            lo = (v & np.uint64(0xFFFFFFFF)).astype(np.uint32)
            hi = (v >> np.uint64(32)).astype(np.uint32)
            out.append((Column(c.k + "_lo", "u32", None, p=True), lo))
            out.append((Column(c.k + "_hi", "u32", None, p=True), hi))
        else:
            if c.d and c.t != "u32":
                raise FormatError(f"{c.k}: delta coding is u32 only")
            out.append((c, v))
    return out, clamped


def validate_header(header: dict) -> None:
    """Raise FormatError unless the header carries what its kind must."""
    if header.get("v") != CONTAINER_VERSION:
        raise FormatError(f"container version {header.get('v')!r}")
    kind = header.get("kind")
    if kind not in KINDS:
        raise FormatError(f"unknown kind {kind!r}")
    if not isinstance(header.get("season"), str) or not header["season"]:
        raise FormatError("header.season missing")
    if not isinstance(header.get("n"), int) or header["n"] < 0:
        raise FormatError("header.n missing")
    for f in GENERATION_FIELDS[kind] + KIND_FIELDS[kind]:
        if f not in header:
            raise FormatError(f"kind {kind}: header field {f!r} missing")
    if not isinstance(header.get("cols"), list):
        raise FormatError("header.cols missing")


# ------------------------------------------------------------------ writer
def encode(kind: str, season: str, n: int, columns: Sequence[Column],
           header: dict | None = None, groups: Sequence[int] | None = None
           ) -> Encoded:
    """Build the container in memory. `header` holds the kind-specific
    fields (generation fields included; a rows_sha for kind `rows` is
    computed here and overwrites whatever was passed)."""
    if kind not in KINDS:
        raise FormatError(f"unknown kind {kind!r}")
    hdr = {"v": CONTAINER_VERSION, "kind": kind, "season": season, "n": int(n)}
    extra = dict(header or {})
    for k in ("v", "kind", "season", "n", "cols"):
        extra.pop(k, None)
    expanded, clamped = _expand(columns)
    names = [c.k for c, _ in expanded]
    if len(set(names)) != len(names):
        raise FormatError(f"duplicate column names: {names}")
    # delta groups: the file's coff column when it has one, else [0]
    if groups is None:
        coff = next((v for c, v in expanded if c.k == "coff"), None)
        groups = [int(x) for x in coff[:-1]] if coff is not None and len(coff) else [0]
    cols_meta, blobs, off = [], [], 0
    for c, v in expanded:
        dt = DTYPES[c.t]
        arr = v
        if c.d:
            arr = _delta_encode(arr, groups)
        blob = _planarize(arr) if c.p else arr.astype(dt.newbyteorder("<")).tobytes()
        cols_meta.append({"k": c.k, "t": c.t, "n": int(len(arr)), "off": off,
                          "p": 1 if c.p else 0, "d": 1 if c.d else 0})
        blobs.append(blob)
        blobs.append(b"\0" * _pad8(len(blob)))
        off += len(blob) + _pad8(len(blob))
    hdr["cols"] = cols_meta
    hdr.update(extra)
    data = b"".join(blobs)
    if kind == "rows":
        probe = dict(hdr)
        probe["rows_sha"] = ""
        hdr["rows_sha"] = hashlib.sha1(canonical_json(probe).encode("utf-8")
                                       + data).hexdigest()
    validate_header(hdr)
    hjson = json.dumps(hdr, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    hjson += b" " * _pad8(8 + len(hjson))
    payload = MAGIC + struct.pack("<I", len(hjson)) + hjson + data
    gz = gzip.compress(payload, compresslevel=9, mtime=0)
    sha = hdr["rows_sha"] if kind == "rows" else hashlib.sha1(gz).hexdigest()
    return Encoded(header=hdr, payload=payload, gz=gz, sha=sha, clamped=clamped)


def write(directory, base: str, kind: str, season: str, n: int,
          columns: Sequence[Column], header: dict | None = None,
          groups: Sequence[int] | None = None, ext: str = "bin") -> Written:
    """Encode and write `<directory>/<base>.<sha1[:10]>.<ext>` atomically
    (temp + rename). An existing file of that name is byte-identical by
    construction and is left alone."""
    enc = encode(kind, season, n, columns, header, groups)
    directory = pathlib.Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    name = hashed_name(base, enc.sha, ext)
    path = directory / name
    if not path.exists():
        tmp = path.with_name(name + ".tmp")
        with open(tmp, "wb") as fh:
            fh.write(enc.gz)
        os.replace(tmp, path)
    return Written(header=enc.header, payload=enc.payload, gz=enc.gz,
                   sha=enc.sha, clamped=enc.clamped, path=path, name=name)


def clamp_health_lines(file_name: str, clamped: dict) -> list[str]:
    """`parts.clamped.<file>.<col>=<n>` for every non-zero counter (§2.1)."""
    return [f"parts.clamped.{file_name}.{col}={n}"
            for col, n in sorted(clamped.items()) if n]


# ------------------------------------------------------------------ reader
def decode(gz: bytes, expect_kind: str | None = None) -> Container:
    """Inflate and decode a container from its on-the-wire bytes."""
    try:
        payload = gzip.decompress(gz)
    except (OSError, EOFError) as e:
        raise FormatError(f"not a gzip stream: {e}") from e
    return decode_payload(payload, expect_kind)


def decode_payload(payload: bytes, expect_kind: str | None = None) -> Container:
    if len(payload) < 8 or payload[:4] != MAGIC:
        raise FormatError("bad magic")
    (H,) = struct.unpack("<I", payload[4:8])
    if (8 + H) % 8 or 8 + H > len(payload):
        raise FormatError("header length not a multiple of 8 / truncated")
    try:
        header = json.loads(payload[8:8 + H].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise FormatError(f"header is not JSON: {e}") from e
    validate_header(header)
    if expect_kind is not None and header["kind"] != expect_kind:
        raise FormatError(f"kind {header['kind']!r}, expected {expect_kind!r}")
    data = memoryview(payload)[8 + H:]
    cols: dict[str, np.ndarray] = {}
    raw: dict[str, np.ndarray] = {}
    for c in header["cols"]:
        t = c["t"]
        if t not in DTYPES:
            raise FormatError(f"column {c['k']}: dtype {t!r}")
        dt = DTYPES[t]
        n, off = int(c["n"]), int(c["off"])
        if off % 8:
            raise FormatError(f"column {c['k']}: offset {off} not 8-aligned")
        span = n * dt.itemsize
        if off + span > len(data):
            raise FormatError(f"column {c['k']}: data area truncated")
        buf = data[off:off + span]
        arr = _unplanarize(buf, n, dt) if c.get("p") else \
            np.frombuffer(buf, dtype=dt, count=n).copy()
        raw[c["k"]] = arr
    # delta groups come from the file's own coff column, else [0]
    coff = raw.get("coff")
    groups = [int(x) for x in coff[:-1]] if coff is not None and len(coff) else [0]
    for c in header["cols"]:
        arr = raw[c["k"]]
        if c.get("d"):
            arr = _delta_decode(arr, groups)
        cols[c["k"]] = arr
    # recombine u64 pairs under the logical name (the parts stay readable)
    for k in list(cols):
        if k.endswith("_lo") and k[:-3] + "_hi" in cols:
            base = k[:-3]
            lo = cols[k].astype(np.uint64)
            hi = cols[base + "_hi"].astype(np.uint64)
            cols[base] = (hi << np.uint64(32)) | lo
    return Container(header=header, cols=cols)


def read(path, expect_kind: str | None = None, check_name: bool = True) -> Container:
    """Read a container from disk; with check_name, the file's name hash
    must equal its content hash (rows: rows_sha[:10]; else sha1 of bytes)."""
    path = pathlib.Path(path)
    gz = path.read_bytes()
    c = decode(gz, expect_kind)
    c.path = path
    if check_name:
        try:
            _, named, _ = parse_name(path.name)
        except FormatError:
            named = None
        if named is not None:
            want = c.header["rows_sha"] if c.kind == "rows" else hashlib.sha1(gz).hexdigest()
            if want[:NAME_HASH_LEN] != named:
                raise FormatError(f"{path.name}: name hash {named} != content "
                                  f"{want[:NAME_HASH_LEN]}")
    return c


def content_sha(path) -> str:
    """The full content hash a file's name was cut from."""
    path = pathlib.Path(path)
    gz = path.read_bytes()
    c = decode(gz)
    return c.header["rows_sha"] if c.kind == "rows" else hashlib.sha1(gz).hexdigest()


if __name__ == "__main__":      # pragma: no cover - a tiny inspector
    import sys
    for p in sys.argv[1:]:
        c = read(p)
        print(p, c.kind, "n=", c.n, {k: (str(v.dtype), len(v)) for k, v in c.cols.items()})
