#!/usr/bin/env python3
"""partition_build.py -- the incremental partition builder
(fleet/blueprints/partitioned_payload.md §6; stage B: steps 1-3 + manifest).

    python scripts/partition_build.py [--deadline S] [--now ISO] [--pins PATH]
                                      [--data-root DIR] [--site-dir DIR] [--daily]
    python scripts/partition_build.py --deadline-default      # prints PARTS_DEADLINE_S

Runs every cycle in parallel with the legacy builder, opens NO network
socket, and stops cleanly at the first day/stage boundary past
`deadline - 30 s` (SIGTERM is the backstop). Per run (§6.2):

  1. tail the three append-only journals (players, gear, abilities) from
     the stored offsets, each verified by the sha256 of the 64 KiB
     preceding it (a mismatch = a rewritten journal = replay from byte 0,
     idempotent by §6.3); route every record to a UTC day; assign
     character ids in arrival order (§2.4); treat rankings.jsonl as the
     per-run SNAPSHOT it is: parse it whole when its sha changed, derive
     the per-run overlay (score, medal, keystone clock) as legacy
     load_fights()/export() do, diff it against the overlay table and dirty
     a day only when a SERVED value changes: the clock accumulates, score
     and medal are served from the current snapshot alone (a run off the
     pages falls back to the row's own values, exactly export()'s rule);
     every tail batch is a checkpoint (offset, counters, dirty marks), so a
     kill loses nothing and a season replay drains across cycles;
  2. rebuild dirty days, newest first, at most 8 per run (every day under
     --rebuild-all), re-deriving the queue after each so a neighbour
     re-dirtied by a collapse is rebuilt in the same run: today's build()
     pipeline on one day's frame (dedup keep=last, overlay, keystone clock,
     the GLOBAL duplicate-upload collapse through the signature table --
     a loser in a neighbour day dirties that day --, hero resolution with
     the pinned markers, post/tmul with the pinned items + the current rule
     tables, tier against the pin), the content-deterministic sort (§2.2),
     the `rows` file, the per-(spec, day) shard blocks (§4.2), thin.npz
     (the cube partial, §3.2) and keys.npz; checkpoint state.json after
     every completed day;
  4. freeze: a day is frozen when it is not dirty and quiescent (72 h) or
     aged (day end + 7 d); once every UTC day touching an absolute reset
     week is frozen the week's four cube files (cells/dist/chars/comps,
     §3.2) are emitted from the thin.npz partials under ONE cube_sha per
     generation; a rebuilt day of a cubed week re-emits its cube under a
     new cube_sha (§6.4); a week's days stay listed until its cube is
     named by the manifest (§3.1); PARTS_WITHHOLD_CUBES / --withhold-cubes
     keeps a week row-served (the cube gap of the tests);
  3. window-level, every run: spec/vocab, meta/specstats, meta/charscore
     (daily base + per-run delta), window.refchars / keys, per-week counts
     -- skipped when no window block changed;
  then the manifest (§2.6; seq advances only when something changed),
  out/ pruned to three generations or files younger than 15 minutes
  (§6.5), copied to site/d/<slug>/, and the health lines in
  data/processed/parts/health.txt.

State lives under data/processed/parts/<slug>/ (§6.1). season_pins.json
there is the authoritative pins file (§2.5): seeded on the first run,
injected from --pins / WOWLOGS_PINS in the equivalence tests, and changed
only through a recorded upgrade; a human edit of the git mirror is adopted
as one.

Stage C adds: freeze packing to upload/, Release staging, the reseed and
the refuse-to-publish guard.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import pathlib
import shutil
import signal
import sqlite3
import statistics
import struct
import sys
import time

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parent

import partition_format as pf                                    # noqa: E402
import partition_client as pc                                    # noqa: E402
import sitecalc as sc                                            # noqa: E402
import build_site_data as bsd                                    # noqa: E402
import project_tuning as pt                                      # noqa: E402
from hero_from_abilities import HeroResolver                     # noqa: E402

DAY_MS = 86_400_000
WEEK_MS = 604_800_000
MAX_DAYS_PER_RUN = int(os.environ.get("PARTS_MAX_DAYS", "8"))
TAIL_BATCH = int(os.environ.get("PARTS_TAIL_BATCH", "100000"))   # journal records held at once (§6.2-1)
FREEZE_QUIET_H = 72
FREEZE_AGE_D = 7
PENDING_DAYS = 7
NSLOTS = 19                       # inventory slots retained per gear record
BLOCK_TRIPWIRE = 1_000_000        # §4.2: a block over 1 MB is a health warning
RETENTION_GENERATIONS = 3         # §6.5
RETENTION_YOUNG_S = 15 * 60
DEADLINE_MARGIN_S = 30
DEFAULT_LEGACY_WALL_S = 420
# §5 pin rules; env-tunable so the fixture (300 runs/day) can exercise them
TIER_MIN_PARSES = int(os.environ.get("PARTS_TIER_MIN_PARSES", "20000"))
PIN_SLOTS = int(os.environ.get("PARTS_PIN_SLOTS", "3"))
# the marker-learning in-tree share (HeroResolver.MIN_IN_TREE, 0.85): the
# fixture plants its chunk-4 marker in five days of a three-week window
LEARN_MIN_IN = os.environ.get("PARTS_LEARN_MIN_IN")
PAR_MIN_RUNS = 500
DAILY_SLOT_H = 20                 # a run this long after the last daily slot is one
DEFAULT_ESLOTS = [0, 4, 6, 7, 8, 10, 11, 14, 15, 16]
ROLE_RANK = {"Tank": 0, "Healer": 1, "DPS": 2}
JOURNAL_SHA_SPAN = 65536
# §3.1 cube gap for the tests / an operator: "*" withholds every cube, else a
# comma list of absolute weeks whose cube is not emitted (the days stay listed)
WITHHOLD_CUBES = os.environ.get("PARTS_WITHHOLD_CUBES", "")
# test hook for test_build_step_exit: sleep this long between days, so a
# stalled builder can be driven against the Build step's deadline
TEST_STALL_S = float(os.environ.get("PARTS_TEST_STALL_S", "0") or 0)
# test hook for the crash cases: "<journal>:<where>:<n>" kills the process
# (os._exit 137) inside the n-th batch of that journal's tail -- where =
# "pending" (the batch's records are appended to the day pending files and
# the run table, the checkpoint NOT yet written) or "batch" (right after the
# batch's checkpoint) -- and "day:after_save:<n>" kills it inside the n-th
# day rebuild of the run, after the three cache saves and before the pending
# files are unlinked. The resumed run must reproduce the clean replay byte
# for byte (§6.3).
TEST_CRASH_AT = os.environ.get("PARTS_TEST_CRASH_AT", "")
CELL_DIMS = tuple(sc.CELL_DIMS)
RL_DIMS = tuple(sc.RL_DIMS)
RG_DIMS = tuple(sc.RG_DIMS)

STOP = [False]


def _handle_term(signum, frame):
    STOP[0] = True


# ------------------------------------------------------------------ utils
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_atomic(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def write_json_atomic(path: pathlib.Path, obj) -> None:
    write_atomic(path, (json.dumps(obj, indent=1, sort_keys=True) + "\n").encode("utf-8"))


def gz_json(obj) -> bytes:
    return gzip.compress(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
                         compresslevel=9, mtime=0)


def hashed_write(directory: pathlib.Path, base: str, ext: str, data: bytes) -> tuple[str, str]:
    """Content-hashed non-container file (<base>.<sha1[:10]>.<ext>)."""
    sha = hashlib.sha1(data).hexdigest()
    name = pf.hashed_name(base, sha, ext)
    path = directory / name
    if not path.exists():
        write_atomic(path, data)
    return name, sha


def arrays_digest(arrays: dict) -> str:
    """sha256 over named arrays in name order (npz bytes are not
    deterministic, their content is)."""
    h = hashlib.sha256()
    for k in sorted(arrays):
        a = np.ascontiguousarray(arrays[k])
        h.update(k.encode())
        h.update(str(a.dtype).encode())
        h.update(str(a.shape).encode())
        h.update(a.tobytes())
    return h.hexdigest()


def save_npz(path: pathlib.Path, arrays: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp.npz")
    np.savez_compressed(tmp, **arrays)
    os.replace(tmp, path)


def load_npz(path: pathlib.Path) -> dict | None:
    if not path.exists():
        return None
    with np.load(path, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


def read_jsonl(path: pathlib.Path) -> list:
    out = []
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def append_jsonl(path: pathlib.Path, records: list) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")


def ustr(v) -> str:
    return "" if v is None else str(v)


STAMP_KEYS = ("_seq", "_gseq", "_aseq")


def record_stamp(r: dict):
    """The record's arrival number (_seq / _gseq / _aseq): assigned once from
    the checkpointed counter, unique per journal record by construction, and
    the ONE projection of a record that survives the cache round trip
    unchanged (gear.npz/abil.npz keep it as gseq/aseq; the journal-shaped
    fields do not -- `flask`, `actor_id`, a guid, an int that comes back as
    a float -- so a whole-record key can never recognise a cached twin)."""
    for k in STAMP_KEYS:
        v = r.get(k)
        if v:
            return k, int(v)
    return None


def dedupe_records(recs: list) -> list:
    """Drop duplicates by ARRIVAL STAMP, keeping the first: a batch
    re-appended after a kill between the pending append and its checkpoint
    (the counter was checkpointed, so the re-tailed records carry the same
    stamps), or a pending file that survived a kill between the cache save
    and its unlink (the cache already holds those stamps), must leave every
    cache as one clean pass would (§6.3) -- and the key is computed on the
    same canonical projection on both sides of the cache round trip. Records
    that merely share their content are NOT collapsed (distinct stamps): the
    legacy readers' last-wins rules and the arrival-order tie-breaks see
    every one. A record without a stamp (none is written today) falls back
    to its whole content minus the parking stamp."""
    seen: set = set()
    out = []
    for r in recs:
        key = record_stamp(r)
        if key is None:
            key = json.dumps({k: v for k, v in r.items() if k != "_parked"},
                             sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


# ------------------------------------------------------------------ paths
class Paths:
    def __init__(self, data_root: pathlib.Path, site_dir: pathlib.Path, slug: str):
        self.data = pathlib.Path(data_root)
        self.site = pathlib.Path(site_dir)
        self.slug = slug
        self.raw = self.data / "raw"
        self.processed = self.data / "processed"
        self.players = self.processed / "players.jsonl"
        self.gear = self.processed / "gear.jsonl"
        self.abil = self.raw / "abilities.jsonl"
        self.rankings = self.raw / "rankings.jsonl"
        self.parts = self.processed / "parts"
        self.health = self.parts / "health.txt"
        self.wall_hist = self.parts / "legacy_wall.txt"
        self.state_dir = self.parts / slug
        self.state = self.state_dir / "state.json"
        self.ids = self.state_dir / "ids"
        self.learned = self.state_dir / "learned"
        self.pins = self.state_dir / "season_pins.json"
        self.days = self.state_dir / "days"
        self.out = self.state_dir / "out"
        self.upload = self.state_dir / "upload"
        self.pending = self.state_dir / "pending"
        self.site_d = self.site / "d"
        self.git_pins = self.data / "season_pins.json"
        self.legacy_health = self.site / "build_health.txt"

    def data_or_repo(self, name: str) -> pathlib.Path:
        p = self.data / name
        return p if p.exists() else ROOT / "data" / name

    @property
    def tuning(self):
        return self.data_or_repo("tuning_patches.json")

    @property
    def pars_seed(self):
        return self.data_or_repo("keystone_pars.json")

    @property
    def hero_map(self):
        return self.data_or_repo("hero_talent_map.json")

    @property
    def rio(self):
        p = self.processed / "rio_scores.csv.gz"
        if p.exists():
            return p
        return self.data_or_repo("rio_scores.csv.gz")

    def day_dir(self, day) -> pathlib.Path:
        return self.days / f"d{day}"


def season_path(data_root: pathlib.Path) -> pathlib.Path:
    p = pathlib.Path(data_root) / "season.json"
    return p if p.exists() else ROOT / "data" / "season.json"


# ----------------------------------------------------------------- season
class Season:
    def __init__(self, path: pathlib.Path):
        self.raw_text = path.read_text(encoding="utf-8")
        s = json.loads(self.raw_text)
        self.doc = s
        self.slug = s["slug"]
        self.name = s["name"]
        self.epoch = s["epoch"]
        self.epoch_ms = sc.parse_iso_ms(self.epoch + "T00:00:00Z")
        self.epoch_ts = pd.Timestamp(self.epoch)
        V = s["vocab"]
        self.vocab = {k: list(v) for k, v in V.items()}
        self.codes = {k: {name: i for i, name in enumerate(v)} for k, v in self.vocab.items()}
        self.spec_pairs = [tuple(p) for p in s["spec_pairs"]]
        self.spec_role = list(s["spec_role"])
        rules = {k: tuple(v) for k, v in s["reset_rules"].items()}
        self.default_rule = rules.pop("*", tuple(sc.RESET_DEFAULT))
        self.rules = rules
        self.reset_rules = s["reset_rules"]
        self.anchors = {r: sc.anchor_ms(r, self.epoch, self.rules, self.default_rule)
                        for r in self.vocab["regions"]}
        self.vocab_sha = sha256_bytes(canon(self.vocab).encode())
        self.unknown_counts: collections.Counter = collections.Counter()

    def code(self, col: str, value) -> int:
        m = self.codes[col]
        v = "Unknown" if value is None or value == "" else str(value)
        i = m.get(v)
        if i is None:
            self.unknown_counts[(col, v)] += 1
            return m["Unknown"]
        return i

    def code_series(self, col: str, values) -> np.ndarray:
        return np.array([self.code(col, v) for v in values], dtype=np.int64)

    def anchor(self, reg_name: str) -> int:
        a = self.anchors.get(reg_name)
        if a is None:
            a = sc.anchor_ms(reg_name, self.epoch, self.rules, self.default_rule)
        return a

    def week_of_ms(self, started_ms: np.ndarray, reg_names) -> np.ndarray:
        anchors = np.array([self.anchor(r) for r in reg_names], dtype=np.int64)
        return (np.asarray(started_ms, dtype=np.int64) - anchors) // WEEK_MS

    def cur_week(self, reg_name: str, now_ms: int) -> int:
        """W(now, reg) (§3.1): the week every row at or after the current
        reset instant belongs to -- computeResetBuckets gives bucket 0 to a
        row started after `now` too, so W(row) is clamped to this."""
        return int((now_ms - self.anchor(reg_name)) // WEEK_MS)


# ------------------------------------------------------------------ state
def default_state() -> dict:
    return {"fmt": pf.FORMAT_VERSION, "status": "ok", "seq": 0, "built": None,
            "journals": {"players": {}, "gear": {}, "abilities": {}},
            "rankings": {"snapshot_sha": None},
            "days": {}, "weeks": {}, "pins": None, "pins_history": [],
            "pins_mirrored_sha": None, "projection": {"rules_sha": None, "has_tmul": False},
            "last_manifest_sha": None, "char_registry_size": 0, "deadline_log": [],
            "invalidations": [], "pending_collapse": [], "daily": {"last": None},
            "pin_candidates": {}, "learned_candidates": {}, "window": {},
            "manifest_refs": [], "arrival_seq": 0, "charscore": {}}


class State:
    def __init__(self, path: pathlib.Path):
        self.path = path
        self.d = default_state()
        if path.exists():
            try:
                self.d.update(json.loads(path.read_text(encoding="utf-8")))
            except ValueError:
                pass
        for k, v in default_state().items():
            self.d.setdefault(k, v)

    def save(self) -> None:
        write_json_atomic(self.path, self.d)

    def day(self, day) -> dict:
        key = str(day)
        e = self.d["days"].get(key)
        if e is None:
            e = self.d["days"][key] = {"dirty": True, "reasons": [], "n": 0, "runs": 0,
                                       "rows_sha": None, "inputs_sha": None, "rules_sha": None,
                                       "frozen": False, "caches": "local", "tar": None,
                                       "f": None, "b": 0, "specs": {}, "w": {},
                                       "last_arrival": None, "built_seq": None, "bytes": {}}
        return e

    def mark_dirty(self, day, reason: str) -> None:
        e = self.day(day)
        e["dirty"] = True
        if reason not in e["reasons"]:
            e["reasons"].append(reason)


# ------------------------------------------------------- char registry
class CharRegistry:
    """§2.4: append-only `ids/chars.bin` (u32 id ‖ u16 len ‖ utf8) plus the
    consolidated `chars.idx` (sorted u64 name hash -> u32 id). Ids are
    assigned in arrival order and never reused; a from-scratch replay of
    the same journals reproduces the log byte for byte."""
    IDX_MAGIC = b"WLCI"

    def __init__(self, ids_dir: pathlib.Path):
        self.dir = ids_dir
        self.log = ids_dir / "chars.bin"
        self.idx = ids_dir / "chars.idx"
        self.size = 0
        self.hashes = np.zeros(0, dtype=np.uint64)
        self.ids = np.zeros(0, dtype=np.uint32)
        self.new: dict[str, int] = {}
        self.new_order: list[str] = []
        self.appended_from = 0
        self._load()

    @staticmethod
    def h64(name: str) -> int:
        return int.from_bytes(hashlib.sha1(name.encode("utf-8")).digest()[:8], "little")

    def _load(self) -> None:
        n_log = 0
        if self.log.exists():
            n_log = self._count_log()
        self.size = n_log
        self.appended_from = self.log.stat().st_size if self.log.exists() else 0
        if self.idx.exists():
            b = self.idx.read_bytes()
            if b[:4] == self.IDX_MAGIC:
                (n,) = struct.unpack("<I", b[4:8])
                if n == n_log:
                    self.hashes = np.frombuffer(b[8:8 + 8 * n], dtype="<u8").astype(np.uint64)
                    self.ids = np.frombuffer(b[8 + 8 * n:8 + 12 * n], dtype="<u4").astype(np.uint32)
                    return
        if n_log:
            self._rebuild_idx()

    def _count_log(self) -> int:
        n = 0
        with open(self.log, "rb") as fh:
            data = fh.read()
        off = 0
        while off + 6 <= len(data):
            (ln,) = struct.unpack_from("<H", data, off + 4)
            off += 6 + ln
            n += 1
        return n

    def _iter_log(self):
        data = self.log.read_bytes()
        off = 0
        while off + 6 <= len(data):
            cid, ln = struct.unpack_from("<IH", data, off)
            name = data[off + 6:off + 6 + ln].decode("utf-8")
            off += 6 + ln
            yield cid, name

    def _rebuild_idx(self) -> None:
        pairs = [(self.h64(name), cid) for cid, name in self._iter_log()]
        pairs.sort()
        self.hashes = np.array([p[0] for p in pairs], dtype=np.uint64)
        self.ids = np.array([p[1] for p in pairs], dtype=np.uint32)

    def lookup(self, name: str) -> int | None:
        v = self.new.get(name)
        if v is not None:
            return v
        if not len(self.hashes):
            return None
        h = np.uint64(self.h64(name))
        i = int(np.searchsorted(self.hashes, h))
        if i < len(self.hashes) and self.hashes[i] == h:
            return int(self.ids[i])
        return None

    def get_or_assign(self, name: str) -> int:
        v = self.lookup(name)
        if v is not None:
            return v
        v = self.size + len(self.new_order)
        self.new[name] = v
        self.new_order.append(name)
        return v

    @property
    def total(self) -> int:
        return self.size + len(self.new_order)

    def flush(self) -> tuple[int, int]:
        """Append the new names to the log, rewrite the idx; returns the
        appended byte range (for the Release part of §7.1)."""
        if not self.new_order:
            return (self.appended_from, self.appended_from)
        self.dir.mkdir(parents=True, exist_ok=True)
        start = self.log.stat().st_size if self.log.exists() else 0
        with open(self.log, "ab") as fh:
            for i, name in enumerate(self.new_order):
                b = name.encode("utf-8")
                fh.write(struct.pack("<IH", self.size + i, len(b)) + b)
        end = self.log.stat().st_size
        hs = np.concatenate([self.hashes, np.array([self.h64(n) for n in self.new_order], dtype=np.uint64)])
        ids = np.concatenate([self.ids, np.array([self.new[n] for n in self.new_order], dtype=np.uint32)])
        order = np.argsort(hs, kind="stable")
        self.hashes, self.ids = hs[order], ids[order]
        self.size += len(self.new_order)
        self.new, self.new_order = {}, []
        body = self.IDX_MAGIC + struct.pack("<I", self.size) + self.hashes.astype("<u8").tobytes() \
            + self.ids.astype("<u4").tobytes()
        write_atomic(self.idx, body)
        self.appended_from = end
        return (start, end)


# ------------------------------------------------------------ runs.sqlite
class RunDB:
    """§2.4/§6.1: run routing, the signature table of the global duplicate
    collapse, the losers it produced and the rankings overlay table."""

    def __init__(self, path: pathlib.Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(path))
        self.con.execute("PRAGMA journal_mode=TRUNCATE")
        self.con.execute("PRAGMA synchronous=OFF")
        c = self.con
        c.execute("CREATE TABLE IF NOT EXISTS runs(code TEXT, fid INTEGER, day INTEGER, "
                  "first_seen INTEGER, PRIMARY KEY(code, fid))")
        c.execute("CREATE TABLE IF NOT EXISTS sigs(sig TEXT PRIMARY KEY, day INTEGER, code TEXT, "
                  "fid INTEGER, roster_n INTEGER)")
        c.execute("CREATE TABLE IF NOT EXISTS losers(code TEXT, fid INTEGER, day INTEGER, "
                  "wcode TEXT, wfid INTEGER, PRIMARY KEY(code, fid))")
        c.execute("CREATE TABLE IF NOT EXISTS overlay(code TEXT, fid INTEGER, score REAL, medal TEXT, "
                  "kms INTEGER, first_seen INTEGER, PRIMARY KEY(code, fid))")
        c.execute("CREATE INDEX IF NOT EXISTS losers_day ON losers(day)")
        # the row's own score/medal per run (what legacy serves when the run
        # is not on the current pages) and the overlay's presence flag
        self._add_column("runs", "rscore", "REAL")
        self._add_column("runs", "rmedal", "TEXT")
        self._add_column("overlay", "present", "INTEGER NOT NULL DEFAULT 1")

    def _add_column(self, table: str, col: str, decl: str) -> None:
        have = {r[1] for r in self.con.execute(f"PRAGMA table_info({table})")}
        if col not in have:
            self.con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    def route(self, code: str, fid: int) -> int | None:
        r = self.con.execute("SELECT day FROM runs WHERE code=? AND fid=?", (code, fid)).fetchone()
        return None if r is None else int(r[0])

    def add_runs(self, rows: list) -> None:
        """rows = (code, fid, day, seq, row_score, row_medal): the routing is
        first-seen (a run's day never moves); the row's own score/medal
        follow the newest players record (legacy's keep="last")."""
        self.con.executemany("INSERT OR IGNORE INTO runs(code, fid, day, first_seen) VALUES(?,?,?,?)",
                             [r[:4] for r in rows])
        self.con.executemany("UPDATE runs SET rscore=?, rmedal=? WHERE code=? AND fid=?",
                             [(r[4], r[5], r[0], r[1]) for r in rows])

    def overlay_for(self, keys: list) -> dict:
        """(code, fid) -> (score, medal, kms, present)."""
        out = {}
        for i in range(0, len(keys), 400):
            chunk = keys[i:i + 400]
            q = " OR ".join("(code=? AND fid=?)" for _ in chunk)
            params = [x for k in chunk for x in k]
            for code, fid, score, medal, kms, present in self.con.execute(
                    f"SELECT code, fid, score, medal, kms, present FROM overlay WHERE {q}", params):
                out[(code, int(fid))] = (score, medal, kms, bool(present))
        return out

    def losers_for_day(self, day: int) -> set:
        return {(c, int(f)) for c, f in self.con.execute("SELECT code, fid FROM losers WHERE day=?", (day,))}

    def sig_get(self, sig: str):
        return self.con.execute("SELECT day, code, fid, roster_n FROM sigs WHERE sig=?", (sig,)).fetchone()

    def sig_set(self, sig: str, day: int, code: str, fid: int, n: int) -> None:
        self.con.execute("INSERT OR REPLACE INTO sigs(sig, day, code, fid, roster_n) VALUES(?,?,?,?,?)",
                         (sig, day, code, fid, n))

    def add_loser(self, code: str, fid: int, day: int, wcode: str, wfid: int) -> None:
        self.con.execute("INSERT OR REPLACE INTO losers(code, fid, day, wcode, wfid) VALUES(?,?,?,?,?)",
                         (code, fid, day, wcode, wfid))

    def snapshot_diff(self, triples: dict, seq: int) -> tuple[list, dict]:
        """Upsert the snapshot's (code,fid) -> (score, medal, kms) triples and
        set the presence flag, mirroring what export() serves (F:1228-1278,
        §6.2-1): for a run ON the current pages the snapshot's score and
        medal are stored AS-IS -- None included -- because export() serves
        `jmap[col] if jmap[col] is not None else row's own`, so a listed entry
        whose value turned null serves the row's own value again, never the
        stored earlier revision; the clock follows legacy's `if ms:` rule
        (keystone_times.json accumulates; a null or zero duration keeps the
        old clock). Returns ([(code, fid, day|None)] whose SERVED value
        changed, stats): served = snapshot value if not None else the row's
        own (kept per run in the routing table) while present, the row's own
        otherwise -- a run leaving or re-entering the pages, or a stored
        component changing, dirties its day only when that served value or
        the clock changes."""
        c = self.con
        c.execute("CREATE TEMP TABLE IF NOT EXISTS snap(code TEXT, fid INTEGER, score REAL, medal TEXT, "
                  "kms INTEGER, PRIMARY KEY(code, fid))")
        c.execute("DELETE FROM snap")
        c.executemany("INSERT OR IGNORE INTO snap VALUES(?,?,?,?,?)",
                      [(k[0], k[1], v[0], v[1], v[2]) for k, v in triples.items()])
        # every listed run whose stored triple or presence would change
        cand = c.execute(
            "SELECT s.code, s.fid, s.score, s.medal, s.kms, o.score, o.medal, o.kms, o.present, "
            "r.day, r.rscore, r.rmedal, o.code IS NULL "
            "FROM snap s LEFT JOIN overlay o ON o.code=s.code AND o.fid=s.fid "
            "LEFT JOIN runs r ON r.code=s.code AND r.fid=s.fid "
            "WHERE o.code IS NULL OR o.present=0 OR s.score IS NOT o.score OR s.medal IS NOT o.medal "
            "OR (s.kms IS NOT NULL AND s.kms != 0 AND s.kms IS NOT o.kms)").fetchall()
        left = c.execute(
            "SELECT o.code, o.fid, o.score, o.medal, r.day, r.rscore, r.rmedal FROM overlay o "
            "LEFT JOIN snap s ON s.code=o.code AND s.fid=o.fid "
            "LEFT JOIN runs r ON r.code=o.code AND r.fid=o.fid "
            "WHERE o.present=1 AND s.code IS NULL").fetchall()
        out = []
        n_new = n_back = n_changed = 0
        for code, fid, ns, nm, nk, os_, om, ok, present, day, rs, rm, is_new in cand:
            if is_new:
                n_new += 1
            elif not present:
                n_back += 1
            else:
                n_changed += 1
            kms = nk if nk else ok
            c.execute("INSERT OR REPLACE INTO overlay(code, fid, score, medal, kms, first_seen, present) "
                      "VALUES(?,?,?,?,?, COALESCE((SELECT first_seen FROM overlay WHERE code=? AND fid=?), ?), 1)",
                      (code, fid, ns, nm, kms, code, fid, seq))
            was_served = present and not is_new
            old = (os_ if was_served and os_ is not None else rs, om if was_served and om is not None else rm, ok)
            new = (ns if ns is not None else rs, nm if nm is not None else rm, kms)
            if old != new:
                out.append((code, int(fid), None if day is None else int(day)))
        c.execute("UPDATE overlay SET present=0 WHERE present=1 AND NOT EXISTS("
                  "SELECT 1 FROM snap s WHERE s.code=overlay.code AND s.fid=overlay.fid)")
        flips = 0
        for code, fid, osc, om, day, rs, rm in left:
            # off the pages: the row's own values are served again
            if (osc is not None and osc != rs) or (om is not None and om != rm):
                flips += 1
                if day is not None:
                    out.append((code, int(fid), int(day)))
        c.execute("DELETE FROM snap")
        return out, {"changed": n_changed + n_new, "left": len(left), "back": n_back, "flips": flips}

    def commit(self) -> None:
        self.con.commit()


# ------------------------------------------------------------------- pins
class Pins:
    """season_pins.json (§2.5) local mechanics: seed, inject, human-edit
    detection against the git mirror, recorded upgrades, learned tables."""

    def __init__(self, P: Paths, st: State, season: Season, now_ms: int, log):
        self.P, self.st, self.season, self.now_ms, self.log = P, st, season, now_ms, log
        self.changed_keys: list[str] = []
        self.doc = self._load()

    def _load(self) -> dict:
        if self.P.pins.exists():
            return json.loads(self.P.pins.read_text(encoding="utf-8"))
        pars = {}
        if self.P.pars_seed.exists():
            try:
                pars = {k: int(v) for k, v in json.loads(self.P.pars_seed.read_text()).items()}
            except (ValueError, TypeError):
                pars = {}
        hero_sha = sha256_file(self.P.hero_map) if self.P.hero_map.exists() else ""
        doc = {"pars": pars, "tier_sets": {}, "learned": {}, "eslots": list(DEFAULT_ESLOTS),
               "hero_map_sha": hero_sha, "upgrades": [], "format": pf.FORMAT_VERSION}
        self.changed_keys.append("seed")
        return doc

    @property
    def sha(self) -> str:
        return sha256_bytes(canon(self.doc).encode())

    def learned_path(self, name: str) -> pathlib.Path:
        return self.P.learned / f"{name}.json"

    def learned_table(self, name: str):
        p = self.learned_path(name)
        if not p.exists():
            return None
        doc = json.loads(p.read_text(encoding="utf-8"))
        want = (self.doc.get("learned") or {}).get(name)
        if want and sha256_bytes(canon(doc).encode()) != want:
            self.log(f"parts.learned_mismatch={name}")
        return doc

    def write_learned(self, name: str, doc) -> str:
        body = canon(doc).encode()
        write_atomic(self.learned_path(name), body + b"\n")
        sha = sha256_bytes(body)
        self.doc.setdefault("learned", {})[name] = sha
        return sha

    def upgrade(self, key: str, frm, to, by: str = "auto") -> None:
        self.doc.setdefault("upgrades", []).append(
            {"at": iso(self.now_ms), "key": key, "from": frm, "to": to, "by": by})
        self.changed_keys.append(key)

    def inject(self, path: pathlib.Path) -> None:
        """--pins / WOWLOGS_PINS: the fixture's pins become authoritative
        (tier sets, learned tables, eslots, pars) -- a recorded upgrade when
        they differ from what is pinned."""
        src = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        ts = src.get("tier_sets") or {}
        for cls, v in sorted(ts.items()):
            sid = int(v["id"]) if isinstance(v, dict) else int(v)
            cur = self.doc["tier_sets"].get(cls)
            if not cur or int(cur["id"]) != sid:
                self.doc["tier_sets"][cls] = {"id": sid, "since": iso(self.now_ms)[:10],
                                              "basis": "injected", "history": (cur or {}).get("history", [])}
                self.upgrade(f"tier_sets.{cls}", None if not cur else cur["id"], sid, "inject")
        if isinstance(src.get("pars"), dict) and src["pars"] != self.doc.get("pars"):
            self.doc["pars"] = {k: int(v) for k, v in src["pars"].items()}
            self.changed_keys.append("pars")
        if isinstance(src.get("eslots"), list) and list(src["eslots"]) != self.doc.get("eslots"):
            self.doc["eslots"] = [int(x) for x in src["eslots"]]
            self.upgrade("eslots", None, self.doc["eslots"], "inject")
        if isinstance(src.get("hero_markers"), dict):
            table = {"markers": {sp: dict(sorted(m.items())) for sp, m in sorted(src["hero_markers"].items())},
                     "sole": dict(sorted((src.get("hero_sole") or {}).items()))}
            self._adopt_learned("hero_markers", table, "inject")
        if isinstance(src.get("tuning_items"), list):
            self._adopt_learned("tuning_items", sorted(set(src["tuning_items"])), "inject")

    def _adopt_learned(self, name: str, table, by: str) -> bool:
        sha = sha256_bytes(canon(table).encode())
        cur = (self.doc.get("learned") or {}).get(name)
        if cur == sha and self.learned_path(name).exists():
            return False
        self.write_learned(name, table)
        self.upgrade(f"learned.{name}", cur, sha, by)
        return True

    def detect_human_edit(self) -> None:
        """§2.5: the git copy is a deliberate human edit iff its sha is not
        one the builder ever wrote (pins_history) AND its content differs
        from the authoritative copy. A lagging mirror triggers nothing."""
        g = self.P.git_pins
        if not g.exists():
            return
        try:
            gdoc = json.loads(g.read_text(encoding="utf-8"))
        except ValueError:
            return
        gsha = sha256_bytes(canon(gdoc).encode())
        if gsha == self.sha or gsha in self.st.d["pins_history"] or gsha == self.st.d.get("pins_mirrored_sha"):
            return
        # adopt: pins keys the human may edit; upgrades history is kept ours
        before = self.sha
        for k in ("pars", "tier_sets", "eslots", "hero_map_sha"):
            if k in gdoc and gdoc[k] != self.doc.get(k):
                self.doc[k] = gdoc[k]
        if self.sha != before:
            self.upgrade("human", before[:12], self.sha[:12], "human")
            self.log("parts.pins_human_edit=1")

    def save(self) -> None:
        body = (json.dumps(self.doc, indent=1, sort_keys=True) + "\n").encode("utf-8")
        write_atomic(self.P.pins, body)
        sha = self.sha
        hist = self.st.d["pins_history"]
        if sha not in hist:
            hist.append(sha)
            del hist[:-200]
        self.st.d["pins_mirrored_sha"] = sha
        self.st.d["pins"] = {"sha": sha, "tier_sets": self.doc.get("tier_sets"),
                             "learned": self.doc.get("learned"), "eslots": self.doc.get("eslots")}
        # staged for journal_parts.py (stage C uploads it; nothing here touches the network)
        self.P.upload.mkdir(parents=True, exist_ok=True)
        write_atomic(self.P.upload / "season_pins.json", body)

    # what enters inputs_sha (§6.3): tier pins, learned shas, eslots, hero map
    def inputs_material(self) -> str:
        return canon({"tier_sets": {c: v.get("id") for c, v in (self.doc.get("tier_sets") or {}).items()},
                      "learned": self.doc.get("learned") or {}, "eslots": self.doc.get("eslots"),
                      "hero_map_sha": self.doc.get("hero_map_sha")})


# --------------------------------------------------------------- deadline
class Deadline:
    def __init__(self, seconds: float | None):
        self.t0 = time.monotonic()
        self.limit = seconds
        self.hit = False

    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def reached(self) -> bool:
        if STOP[0]:
            self.hit = True
        elif self.limit is not None and self.elapsed() > self.limit - DEADLINE_MARGIN_S:
            self.hit = True
        return self.hit


# ============================================================== the builder
class Builder:
    def __init__(self, data_root, site_dir, now_ms: int | None = None, deadline: float | None = None,
                 pins_inject: pathlib.Path | None = None, daily: bool = False, max_days: int = MAX_DAYS_PER_RUN,
                 rebuild_all: bool = False, log_fn=print, withhold_cubes: str | None = None):
        self.withhold_cubes = WITHHOLD_CUBES if withhold_cubes is None else withhold_cubes
        self.season = Season(season_path(pathlib.Path(data_root)))
        self.P = Paths(pathlib.Path(data_root), pathlib.Path(site_dir), self.season.slug)
        self.now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
        self.deadline = Deadline(deadline)
        self.pins_inject = pins_inject
        self.force_daily = daily
        # --rebuild-all means every day in ONE run (the dispatch provisions
        # the 110-minute job and PARTS_DEADLINE_S=5400 for it); the deadline
        # remains the only bound
        self.max_days = 10 ** 9 if rebuild_all else max_days
        self.rebuild_all = rebuild_all
        self._print = log_fn
        self.health_lines: list[str] = []
        self.stage_s: dict[str, float] = {}
        self.st = State(self.P.state)
        self.reg = CharRegistry(self.P.ids)
        self.db = RunDB(self.P.ids / "runs.sqlite")
        self.pins = Pins(self.P, self.st, self.season, self.now_ms, self.health)
        self.patch = self._load_patch()
        self.rules_sha = pt.rules_digest()
        self.emb_cfg = None
        self.rebuilt_days: list = []
        self.days_built_this_run = 0
        self.dirty_found = 0
        self.touched_weeks: set = set()
        self.window_stale = False
        self.rows_cache: dict = {}
        self.pending_neighbours: list = []

    # ---- logging -------------------------------------------------------
    def health(self, line: str) -> None:
        self.health_lines.append(line)
        self._print(line)

    def log(self, msg: str) -> None:
        self._print(f"[parts] {msg}")

    def _stage(self, name: str, t0: float) -> None:
        self.stage_s[name] = self.stage_s.get(name, 0.0) + (time.perf_counter() - t0)

    def _load_patch(self):
        if not self.P.tuning.exists():
            return None
        try:
            patches = json.loads(self.P.tuning.read_text(encoding="utf-8")).get("patches") or []
        except ValueError:
            return None
        return patches[0] if patches else None

    @property
    def patch_id(self) -> str:
        return canon(self.patch) if self.patch else ""

    # ---- pins / daily slot ----------------------------------------------
    def is_daily_slot(self) -> bool:
        if self.force_daily:
            return True
        last = self.st.d["daily"].get("last")
        if last is None:
            return True
        return self.now_ms - sc.parse_iso_ms(last) >= DAILY_SLOT_H * 3_600_000

    def prepare_pins(self) -> None:
        t0 = time.perf_counter()
        pins = self.pins
        if self.pins_inject:
            pins.inject(self.pins_inject)
        pins.detect_human_edit()
        if self.st.d["fmt"] != pf.FORMAT_VERSION:
            pins.changed_keys.append("format")
            self.st.d["fmt"] = pf.FORMAT_VERSION
        self.daily = self.is_daily_slot()
        if self.daily:
            self.daily_learn()
        if pins.changed_keys or (self.st.d.get("pins") or {}).get("sha") != pins.sha:
            pins.save()
        # the invalidation matrix (§6.4), scoped by what changed: a pin /
        # vocab / format change dirties every day; a tuning rule-table edit
        # dirties the LISTED days only (tmul lives in day files; cubes carry
        # none); a new tuning patch dirties the days from the earliest cutoff
        # minus one -- of the OLD patch as well as the new one: post/tmul are
        # relative to patches[0], so the rows between the two cutoffs flip
        # too (§6.4). Newest first happens in step 2's queue.
        cur = self.static_inputs()
        prev = self.st.d.get("static_inputs")
        if not isinstance(prev, dict):
            prev = {}
        all_days = [int(k) for k in self.st.d["days"]]
        if self.rebuild_all or prev.get("pins") != cur["pins"] or prev.get("vocab") != cur["vocab"] \
                or prev.get("fmt") != cur["fmt"]:
            reason = ("rebuild_all" if self.rebuild_all else "pins" if prev.get("pins") != cur["pins"]
                      else "vocab" if prev.get("vocab") != cur["vocab"] else "format")
            for d in all_days:
                self.st.mark_dirty(d, reason)
        elif prev.get("rules") != cur["rules"]:
            with_rows = sorted(d for d in all_days if d >= 0)
            for d in self.listed_days(with_rows) + [d for d in all_days if d < 0]:
                self.st.mark_dirty(d, "rules")
        elif prev.get("patch") != cur["patch"]:
            since = cur["patch_day"]
            prev_day = prev.get("patch_day")
            # a state written before patch_day was recorded: the old cutoff
            # is unknown, so every day is in scope
            since = 0 if prev_day is None else min(int(prev_day), since)
            for d in all_days:
                if d < 0 or d >= since - 1:
                    self.st.mark_dirty(d, "patch")
        self.st.d["static_inputs"] = cur
        # a day holding rows started after the reset instant of its build
        # (W clamped to W(now), §3.1) is re-queued once that reset has passed:
        # the client now buckets those rows one week later
        for key, e in self.st.d["days"].items():
            wc = e.get("w_clamp") or {}
            if any(self.season.cur_week(rn, self.now_ms) > int(w) for rn, w in wc.items()):
                self.st.mark_dirty(int(key), "future")
        if self.daily:
            self.st.d["daily"]["last"] = iso(self.now_ms)
        self._stage("pins", t0)

    def static_inputs(self) -> dict:
        return {"pins": sha256_bytes(self.pins.inputs_material().encode()), "rules": self.rules_sha,
                "patch": sha256_bytes(self.patch_id.encode()), "patch_day": self.patch_first_day(),
                "vocab": self.season.vocab_sha, "fmt": str(pf.FORMAT_VERSION)}

    def patch_first_day(self) -> int:
        """The UTC day of the earliest per-region cutoff of the current
        patch (−1 day is applied by the caller); 0 when unknown. Kept in
        state.static_inputs so the NEXT patch change knows the old cutoff."""
        if not self.patch:
            return 0
        cands = []
        regs = self.patch.get("regions") or {}
        for v in list(regs.values()) + [self.patch.get("date")]:
            if not v:
                continue
            try:
                s = str(v)
                ms = sc.parse_iso_ms(s if "T" in s else s + "T00:00:00Z")
            except (ValueError, TypeError):
                continue
            cands.append(int((ms - self.season.epoch_ms) // DAY_MS))
        return min(cands) if cands else 0

    def daily_learn(self) -> None:
        """§5 at the daily slot: tier first-write / auto-upgrade over the
        trailing 7 days' gear caches; learned tables over the window's abil
        caches with the 3-slot hysteresis."""
        pins = self.pins
        days_all = sorted(int(k) for k in self.st.d["days"] if k != "undated" and self.st.d["days"][k].get("n"))
        today = (self.now_ms - self.season.epoch_ms) // DAY_MS
        trailing = [d for d in days_all if d >= today - 7]
        tally: dict[str, collections.Counter] = {}
        parses: collections.Counter = collections.Counter()
        for d in trailing:
            g = load_npz(self.P.day_dir(d) / "gear.npz")
            if not g or not len(g["code"]):
                continue
            sets_d, _, _ = gear_meta_journal(g)
            cls_of = {}
            for i in range(len(g["code"])):
                cls_of[bsd._gear_key(str(g["code"][i]), int(g["fid"][i]), str(g["character"][i]) or None,
                                     None if g["server_null"][i] else str(g["server"][i]))] = str(g["cls"][i])
            for k, counts in sets_d.items():
                cls = cls_of.get(k, "")
                parses[cls] += 1
                for sid, n in counts.items():
                    tally.setdefault(cls, collections.Counter())[sid] += n
        for cls, counts in sorted(tally.items()):
            total = sum(counts.values())
            qual = [s for s, n in counts.items() if n >= bsd.SEASON_SET_MIN_SHARE * total and s.isdigit()]
            cur = pins.doc["tier_sets"].get(cls)
            if cur is None:
                if qual:
                    sid, basis = max(qual, key=int), "share"
                else:
                    sid, basis = max(counts, key=counts.get), "fallback"
                pins.doc["tier_sets"][cls] = {"id": int(sid), "since": iso(self.now_ms)[:10],
                                              "basis": basis, "history": []}
                pins.upgrade(f"tier_sets.{cls}", None, int(sid), "first-write")
                continue
            if not qual:
                continue
            cand = int(max(qual, key=int))
            if cand <= int(cur["id"]):
                self.st.d["pin_candidates"].pop(cls, None)
                continue
            enough = parses[cls] >= TIER_MIN_PARSES
            if cur.get("basis") == "fallback" and enough:
                self._tier_upgrade(cls, cur, cand)
                continue
            if not enough:
                continue
            pc = self.st.d["pin_candidates"].get(cls)
            if pc and pc.get("id") == cand:
                pc["count"] += 1
            else:
                pc = self.st.d["pin_candidates"][cls] = {"id": cand, "count": 1}
            if pc["count"] >= PIN_SLOTS:
                self._tier_upgrade(cls, cur, cand)
                self.st.d["pin_candidates"].pop(cls, None)
        # learned tables over the window's abil caches
        listed = self.listed_days(days_all)
        pairs, records = [], []
        for d in listed:
            raw = load_npz(self.P.day_dir(d) / "raw.npz")
            ab = load_npz(self.P.day_dir(d) / "abil.npz")
            if not raw or not ab or not len(ab["code"]):
                continue
            recs = abil_records_from_cache(ab)
            records.extend(recs)
            byk = {(r["report_code"], r["fight_id"], r["name"]): frozenset(a["name"] for a in r["abilities"])
                   for r in recs if r["abilities"]}
            for code, fid, ch, sp, cl, h in zip(raw["report_code"], raw["fight_id"], raw["character"],
                                                raw["spec"], raw["class"], raw["hero_talent"]):
                ab_set = byk.get((str(code), int(fid), str(ch)))
                if ab_set:
                    pairs.append((f"{sp} {cl}", str(h) or "Unknown", ab_set))
        if pairs:
            if LEARN_MIN_IN:
                import hero_from_abilities as _hfa
                _hfa.MIN_IN_TREE = float(LEARN_MIN_IN)
            hr = HeroResolver.learn(pairs)
            table = {"markers": {sp: dict(sorted(m.items())) for sp, m in sorted(hr.markers.items())},
                     "sole": dict(sorted(hr.sole.items()))}
            self._learned_candidate("hero_markers", table)
        if records:
            self._learned_candidate("tuning_items", sorted(pt.classify_abilities(records)))

    def _tier_upgrade(self, cls: str, cur: dict, cand: int) -> None:
        hist = list(cur.get("history") or [])
        hist.append({"id": int(cur["id"]), "since": cur.get("since"), "until": iso(self.now_ms)[:10]})
        self.pins.doc["tier_sets"][cls] = {"id": cand, "since": iso(self.now_ms)[:10], "basis": "share",
                                           "history": hist}
        self.pins.upgrade(f"tier_sets.{cls}", int(cur["id"]), cand)

    def _learned_candidate(self, name: str, table) -> None:
        sha = sha256_bytes(canon(table).encode())
        cur = (self.pins.doc.get("learned") or {}).get(name)
        if cur == sha:
            self.st.d["learned_candidates"].pop(name, None)
            return
        if cur is None:                      # the first table is pinned at once
            self.pins._adopt_learned(name, table, "first-write")
            return
        lc = self.st.d["learned_candidates"].get(name)
        if lc and lc.get("sha") == sha:
            lc["count"] += 1
        else:
            lc = self.st.d["learned_candidates"][name] = {"sha": sha, "count": 1}
        if lc["count"] >= PIN_SLOTS:
            self.pins._adopt_learned(name, table, "auto")
            self.st.d["learned_candidates"].pop(name, None)

    # ---- step 1: journals ------------------------------------------------
    def checkpoint(self) -> None:
        """The durable checkpoint, in this order: sqlite (routing, overlay,
        signatures), then the character registry log, then state.json last
        (offsets, arrival counters, dirty marks). A kill between any two
        leaves a state the next run resumes from without loss: the journal
        offset only moves in state.json, so anything not yet recorded there
        is re-tailed, and the pending caches dedupe re-appended records
        (§6.3)."""
        self.db.commit()
        self.reg.flush()
        self.st.d["char_registry_size"] = self.reg.total
        self.st.save()

    def _test_crash(self, name: str, where: str, n: int) -> None:
        if TEST_CRASH_AT and TEST_CRASH_AT == f"{name}:{where}:{n}":
            self._print(f"[parts] TEST CRASH at {TEST_CRASH_AT}")
            sys.stdout.flush()
            os._exit(137)

    def tail(self, name: str, path: pathlib.Path, on_batch, batch: int = TAIL_BATCH) -> bool:
        """Streams the records appended since the stored offset to
        `on_batch(records)` in batches (the journal is never held whole: a
        from-scratch replay of a season is gigabytes of JSON). After EVERY
        batch the consumed offset and the sha256 of the 64 KiB preceding it
        are recorded and the step-1 checkpoint is written, so a kill at any
        instant costs at most one batch of work and never a record; between
        batches the deadline is checked and the tail stops at a batch
        boundary (the rest waits for the next run; returns False). A torn
        last line is not consumed; a rewritten journal (sha mismatch /
        seeded marker) is replayed from byte 0 (idempotent by §6.3)."""
        ent = self.st.d["journals"].setdefault(name, {})
        if not path.exists():
            return True
        size = path.stat().st_size
        off = int(ent.get("offset") or 0)
        marker = path.with_name(path.name + ".seeded")
        marker_tag = None
        if marker.exists():
            marker_tag = f"{marker.stat().st_mtime_ns}:{marker.stat().st_size}"
        replay = False
        if marker_tag is not None and ent.get("seeded_marker") != marker_tag:
            replay = True
            ent["seeded_marker"] = marker_tag
        if off > size:
            replay = True
        elif off and ent.get("sha"):
            with open(path, "rb") as fh:
                fh.seek(max(0, off - JOURNAL_SHA_SPAN))
                pre = fh.read(off - max(0, off - JOURNAL_SHA_SPAN))
            if sha256_bytes(pre) != ent["sha"]:
                replay = True
        if replay:
            self.health(f"parts.journal_replay={name}")
            off = 0
        bad = 0
        consumed = off
        records: list = []
        nb = 0
        complete = True
        with open(path, "rb") as fh:
            fh.seek(off)
            for line in fh:
                if not line.endswith(b"\n"):
                    break                       # the torn last line stays unconsumed
                consumed += len(line)
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError:
                    bad += 1
                if len(records) >= batch:
                    nb += 1
                    on_batch(records)
                    records = []
                    self._test_crash(name, "pending", nb)
                    self._tail_checkpoint(name, path, consumed, marker_tag)
                    self._test_crash(name, "batch", nb)
                    if self.deadline.reached():
                        complete = False
                        break
        if complete:
            if records:
                nb += 1
                on_batch(records)
                self._test_crash(name, "pending", nb)
            self._tail_checkpoint(name, path, consumed, marker_tag)
            self._test_crash(name, "batch", nb)
        if bad:
            self.health(f"parts.journal_bad_lines.{name}={bad}")
        return complete

    def _tail_checkpoint(self, name: str, path: pathlib.Path, consumed: int, marker_tag) -> None:
        ent = self.st.d["journals"][name]
        with open(path, "rb") as fh:
            fh.seek(max(0, consumed - JOURNAL_SHA_SPAN))
            pre = fh.read(consumed - max(0, consumed - JOURNAL_SHA_SPAN))
        ent["offset"] = consumed
        ent["sha"] = sha256_bytes(pre)
        ent["seeded"] = marker_tag is not None
        self.checkpoint()

    @staticmethod
    def char_key(rec: dict) -> str:
        ch = rec.get("character")
        sv = rec.get("server")
        rg = rec.get("region")
        ch = "?" if ch is None else str(ch)
        sv = "?" if sv is None else str(sv)
        rg = "Unknown" if rg is None or rg == "" else str(rg)
        return f"{ch}@{sv}@{rg}"

    @staticmethod
    def day_of(started_at) -> int:
        try:
            ms = int(started_at)
        except (TypeError, ValueError):
            return -1
        return int((ms - EPOCH_MS_CACHE[0]) // DAY_MS)

    def step1(self) -> bool:
        """§6.2-1, streamed and checkpointed per batch: every batch of the
        players tail is routed to its UTC day and appended to that day's
        pending file (the day marked dirty) before the next batch is read,
        so a season-long replay never holds a journal in memory and a kill
        loses nothing; gear/abilities route through the run table (the runs
        this very tail named are known without a lookup) and park otherwise,
        the parked records persisted per batch too. Returns False when the
        deadline stopped a tail (the rest of step 1 waits for the next run)."""
        t0 = time.perf_counter()
        seq = [self.st.d["arrival_seq"]]
        new_runs: dict = {}
        n_players = [0]

        def touch(days: set) -> None:
            for d in days:
                self.st.mark_dirty(d, "arrival")
                self.st.day(d)["last_arrival"] = iso(self.now_ms)

        def on_players(batch: list) -> None:
            by_day: dict[int, list] = collections.defaultdict(list)
            rows = []
            for rec in batch:
                seq[0] += 1
                day = self.day_of(rec.get("started_at"))
                code, fid = ustr(rec.get("report_code")), int(rec.get("fight_id") or 0)
                rec["_cid"] = self.reg.get_or_assign(self.char_key(rec))
                rec["_seq"] = seq[0]
                sc_ = rec.get("score")
                rows.append((code, fid, day, seq[0], float(sc_) if sc_ is not None else None,
                             ustr(rec.get("medal")) or None))
                new_runs.setdefault((code, fid), day)
                by_day[day].append(rec)
            self.db.add_runs(rows)
            for d, recs in by_day.items():
                append_jsonl(self.P.day_dir(d) / "pending_players.jsonl", recs)
            touch(set(by_day))
            n_players[0] += len(batch)
            self.st.d["arrival_seq"] = seq[0]
        complete = self.tail("players", self.P.players, on_players)
        counts = {"gear": (0, 0), "abilities": (0, 0)}
        if complete:
            # gear / abilities: routed through the run table; unknown runs park
            route_cache: dict = {}

            def route(code, fid):
                k = (code, fid)
                d = new_runs.get(k)
                if d is not None:
                    return d
                if k not in route_cache:
                    route_cache[k] = self.db.route(code, fid)
                return route_cache[k]
            gseq = [int(self.st.d.get("gear_seq") or 0)]
            aseq = [int(self.st.d.get("abil_seq") or 0)]
            for name, path, pend_name, day_file in (("gear", self.P.gear, "gear.jsonl", "pending_gear.jsonl"),
                                                    ("abilities", self.P.abil, "abil.jsonl", "pending_abil.jsonl")):
                pend_path = self.P.pending / pend_name
                parked_prev = dedupe_records(read_jsonl(pend_path))
                park: list = []
                n_new = [0]

                def consume(recs, persist_park: bool) -> None:
                    by: dict[int, list] = collections.defaultdict(list)
                    newly_parked = []
                    for rec in recs:
                        if name == "gear" and "_gseq" not in rec:
                            # the gear journal's own arrival order: legacy's trait
                            # material (the modal selection blob of a build) is
                            # taken in journal order, so a tie between blob
                            # variants resolves to the first one ever journaled
                            gseq[0] += 1
                            rec["_gseq"] = gseq[0]
                        elif name == "abilities" and "_aseq" not in rec:
                            aseq[0] += 1
                            rec["_aseq"] = aseq[0]
                        d = route(ustr(rec.get("report_code")), int(rec.get("fight_id") or 0))
                        if d is None:
                            rec.setdefault("_parked", iso(self.now_ms))
                            park.append(rec)
                            newly_parked.append(rec)
                        else:
                            rec.pop("_parked", None)
                            by[d].append(rec)
                    for d, lst in by.items():
                        append_jsonl(self.P.day_dir(d) / day_file, lst)
                    if persist_park and newly_parked:
                        # a parked record must survive a kill before the
                        # end-of-tail rewrite of the pending file: appended
                        # now, deduped when read back
                        append_jsonl(pend_path, newly_parked)
                    touch(set(by))
                    n_new[0] += len(recs)
                    self.st.d["gear_seq"] = gseq[0]
                    self.st.d["abil_seq"] = aseq[0]
                consume(parked_prev, False)
                n_new[0] = 0
                complete = self.tail(name, path, lambda recs: consume(recs, True))
                if not complete:
                    counts[name] = (n_new[0], len(park))
                    break
                cutoff = self.now_ms - PENDING_DAYS * DAY_MS
                keep = [r for r in park if sc.parse_iso_ms(r["_parked"]) >= cutoff]
                if pend_path.exists():
                    pend_path.unlink()
                append_jsonl(pend_path, keep)
                if len(park) - len(keep):
                    self.health(f"parts.pending_expired.{name}={len(park) - len(keep)}")
                counts[name] = (n_new[0], len(park))
        self.health(f"parts.tail.players={n_players[0]}")
        self.health(f"parts.tail.gear={counts['gear'][0]}")
        self.health(f"parts.tail.abilities={counts['abilities'][0]}")
        self.health(f"parts.pending.gear={counts['gear'][1]}")
        self.health(f"parts.pending.abilities={counts['abilities'][1]}")
        self.health(f"parts.chars_new={self.reg.total - int(self.st.d.get('chars_at_start') or 0)}")
        self._stage("tail", t0)
        if not complete:
            self.health("parts.deadline_hit=1")
            self.health("parts.tail_partial=1")
            self.checkpoint()
            return False
        t0 = time.perf_counter()
        self.seed_clocks()
        self.rankings_snapshot()
        self._stage("rankings", t0)
        return True

    def seed_clocks(self) -> None:
        """PR-1 transition (§6.2-1): legacy's keystone clock is the union of
        every snapshot it ever saw, persisted in data/keystone_times.json; a
        builder starting mid-season (or replaying after a registry loss) has
        seen none of the earlier snapshots, so that map is seeded ONCE into
        the overlay table -- clock only, no score/medal, not present on the
        pages -- for every run the table does not know. From then on the
        per-run snapshots keep the table as complete as the file."""
        p = self.P.data / "keystone_times.json"
        if self.st.d.get("clocks_seeded") or not p.exists():
            return
        try:
            ks = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            self.health("parts.clocks_seeded=unreadable")
            return
        rows = []
        for k, v in ks.items():
            code, _, fid = str(k).rpartition(":")
            try:
                rows.append((code, int(fid), int(round(float(v) * 1000)), self.st.d["arrival_seq"]))
            except (TypeError, ValueError):
                continue
        self.db.con.executemany("INSERT OR IGNORE INTO overlay(code, fid, score, medal, kms, first_seen, present) "
                                "VALUES(?,?,NULL,NULL,?,?,0)", rows)
        self.db.commit()
        self.st.d["clocks_seeded"] = sha256_file(p)
        self.st.save()
        self.health(f"parts.clocks_seeded={len(rows)}")

    def rankings_snapshot(self) -> None:
        p = self.P.rankings
        if not p.exists():
            return
        sha = sha256_file(p)
        if sha == self.st.d["rankings"].get("snapshot_sha"):
            self.health("parts.rankings=unchanged")
            return
        triples: dict = {}
        for rec in read_jsonl(p):
            for r in rec.get("rankings") or []:
                rep = r.get("report") or {}
                code, fid = rep.get("code") or "", rep.get("fightID")
                if not code or fid is None:
                    continue
                key = (code, int(fid))
                if key in triples:
                    continue                  # first ranking entry wins (F:393)
                triples[key] = (r.get("score"), r.get("medal"), r.get("duration"))
        changed, stats = self.db.snapshot_diff(triples, self.st.d["arrival_seq"])
        dirty_days = set()
        for code, fid, day in changed:
            if day is not None:
                dirty_days.add(day)
        for d in dirty_days:
            self.st.mark_dirty(d, "overlay")
        # three phases so a kill between any two loses nothing: the dirty
        # marks land first (the sha still old: the snapshot is re-diffed next
        # run, at worst a rebuild too many), then the overlay commits, then
        # the sha that says the snapshot is consumed
        self.st.save()
        self.db.commit()
        self.st.d["rankings"]["snapshot_sha"] = sha
        self.st.save()
        self.health(f"parts.rankings=parsed:{len(triples)}:changed:{len(changed)}:days:{len(dirty_days)}")
        self.health(f"parts.rankings_pages=left:{stats['left']}:back:{stats['back']}:served_changed:{stats['flips']}")

    # ---- step 2: day rebuild --------------------------------------------
    def dirty_days(self) -> list:
        """Newest first (today's day -- any day at or past today -- first,
        pref #15), then every pending neighbour collapse, then the rest
        (§6.2-2); the undated day last."""
        days = [int(k) for k, e in self.st.d["days"].items() if e.get("dirty")]
        days.sort(reverse=True)
        today = int((self.now_ms - self.season.epoch_ms) // DAY_MS)
        first = [d for d in days if d >= today]
        pend = [d for d in days if d < today and "collapse" in self.st.d["days"][str(d)]["reasons"]]
        rest = [d for d in days if d < today and d not in pend]
        return first + pend + rest

    def step2(self) -> None:
        """Rebuild dirty days, at most max_days per run, re-deriving the
        queue after every day: a cross-day collapse found while building a
        day re-dirties its neighbour, and that neighbour is rebuilt in THIS
        run even when it was already built earlier in it (a one-shot or
        replay build meets the winner after the loser's day), as long as the
        budget allows (§6.2-2). days_left counts everything still dirty."""
        t0 = time.perf_counter()
        self.dirty_found = len(self.dirty_days())
        done = 0
        while done < self.max_days:
            if self.deadline.reached():
                self.health("parts.deadline_hit=1")
                break
            queue = self.dirty_days()
            if not queue:
                break
            d = queue[0]
            self.build_day(d)
            done += 1
            self.checkpoint()                    # the per-day checkpoint (§6.2-2)
            if TEST_STALL_S:
                stall_until = time.monotonic() + TEST_STALL_S
                while time.monotonic() < stall_until and not STOP[0]:
                    time.sleep(0.05)
        left = self.dirty_days()
        self.health(f"parts.dirty_days={self.dirty_found}")
        self.health(f"parts.rebuilt_days={done}")
        self.health(f"parts.rebuilt_order=" + ",".join(str(d) for d in self.rebuilt_days))
        self.health(f"parts.days_left={len(left)}")
        self._stage("days", t0)

    # ---- step 4: freeze + cubes -------------------------------------------
    def freeze_flags(self) -> None:
        """§6.2-4: a day is frozen when it is not dirty and either quiescent
        (no arrival for 72 h) or aged (its end + 7 d < now). A day that has
        frozen once re-freezes the moment it is rebuilt (a late upload into
        a closed day re-emits its week's cube under a new cube_sha, §6.4)."""
        for key, e in self.st.d["days"].items():
            if key == "undated" or key == "-1":
                e["frozen"] = bool(e.get("f")) and not e.get("dirty")
                continue
            d = int(key)
            if e.get("dirty"):
                e["frozen"] = False
                continue
            if e.get("frozen_once"):
                e["frozen"] = True
                continue
            la = e.get("last_arrival")
            quiet = la is None or (self.now_ms - sc.parse_iso_ms(la)) >= FREEZE_QUIET_H * 3_600_000
            aged = self.season.epoch_ms + (d + 1) * DAY_MS + FREEZE_AGE_D * DAY_MS < self.now_ms
            e["frozen"] = bool(quiet or aged)
            if e["frozen"]:
                e["frozen_once"] = True

    def withheld(self, w: int) -> bool:
        wh = self.withhold_cubes
        if not wh:
            return False
        if wh.strip() == "*":
            return True
        return str(w) in {x.strip() for x in wh.split(",") if x.strip()}

    def step4(self) -> None:
        """Emit w<W>.{cells,dist,chars,comps} for every week all of whose
        UTC days (derived from the rows' W, §3.1) are frozen, under one
        cube_sha per generation; re-emit when the partials changed; each
        week is checkpointed; the deadline is checked between weeks."""
        t0 = time.perf_counter()
        self.freeze_flags()
        emitted, skipped = [], []
        for w in sorted(int(k) for k in self.st.d["weeks"]):
            we = self.st.d["weeks"][str(w)]
            days = [d for d in we.get("days", []) if (self.P.day_dir(d) / "thin.npz").exists()]
            we["days"] = days
            if not days:
                continue
            if self.withheld(w):
                if we.get("published"):
                    we["published"] = False
                skipped.append(w)
                continue
            if not all(self.st.d["days"].get(str(d), {}).get("frozen") for d in days):
                continue
            thin, digest = week_partials(self, w, days)
            cube_sha = sha256_bytes("|".join([digest, str(pf.FORMAT_VERSION), self.pins.inputs_material()]).encode())
            if we.get("published") and we.get("cube_sha") == cube_sha \
                    and all((self.P.out / f).exists() for f in (we.get("f") or {}).values()):
                continue
            if self.deadline.reached():
                self.health("parts.deadline_hit=1")
                break
            files, byts = emit_cube(self, w, thin, cube_sha)
            if we.get("published") and we.get("cube_sha") != cube_sha:
                self.st.d["invalidations"].append({"week": w, "reason": "cube", "from": we.get("cube_sha"),
                                                   "to": cube_sha, "seq": self.st.d["seq"] + 1,
                                                   "at": iso(self.now_ms)})
            we.update({"published": True, "cube_sha": cube_sha, "f": files, "b": byts,
                       "frozen_at": iso(self.now_ms), "built_seq": self.st.d["seq"] + 1})
            self.touched_weeks.add(w)
            emitted.append(w)
            self.st.save()                       # per-week checkpoint
        if emitted:
            self.health("parts.cubes_emitted=" + ",".join(str(w) for w in emitted))
        if skipped:
            self.health("parts.cubes_withheld=" + ",".join(str(w) for w in skipped))
        self._stage("cubes", t0)

    def _frame(self, day: int):
        """The day's frame: raw.npz + pending records, dedup keep=last on
        (report, fight, character, server); losers dropped; overlay applied."""
        dd = self.P.day_dir(day)
        raw = load_npz(dd / "raw.npz")
        cols = RAW_COLS
        frames = []
        if raw is not None and len(raw["report_code"]):
            frames.append(pd.DataFrame({k: raw[k] for k in cols}))
        pend = read_jsonl(dd / "pending_players.jsonl")
        if pend:
            rows = []
            for r in pend:
                rows.append({
                    "character": ustr(r.get("character")), "server": ustr(r.get("server")),
                    "region": ustr(r.get("region")), "class": ustr(r.get("class")), "spec": ustr(r.get("spec")),
                    "hero_talent": ustr(r.get("hero_talent")), "role": ustr(r.get("role")),
                    "dungeon": ustr(r.get("dungeon")), "key_level": int(r.get("key_level") or 0),
                    "duration_s": float(r["duration_s"]) if r.get("duration_s") is not None else np.nan,
                    "damage_done": float(r["damage_done"]) if r.get("damage_done") is not None else np.nan,
                    "dps": float(r["dps"]) if r.get("dps") is not None else np.nan,
                    "deaths": int(r.get("deaths") or 0),
                    "item_level": float(r["item_level"]) if r.get("item_level") is not None else np.nan,
                    "score": float(r["score"]) if r.get("score") is not None else np.nan,
                    "medal": ustr(r.get("medal")), "affixes": ustr(r.get("affixes")),
                    "report_code": ustr(r.get("report_code")), "fight_id": int(r.get("fight_id") or 0),
                    "started_at": float(r["started_at"]) if r.get("started_at") is not None else np.nan,
                    "set_counts": ustr(r.get("set_counts")), "set_counts_null": r.get("set_counts") is None,
                    "char_id": int(r["_cid"]), "score_ov": np.nan, "medal_ov": "", "keystone_s": np.nan,
                    "seq": int(r.get("_seq") or 0),
                })
            frames.append(pd.DataFrame(rows, columns=cols))
        if not frames:
            return pd.DataFrame(columns=cols)
        df = pd.concat(frames, ignore_index=True)
        df = df.drop_duplicates(subset=["report_code", "fight_id", "character", "server"], keep="last")
        df = df.reset_index(drop=True)
        losers = self.db.losers_for_day(day)
        if losers:
            keep = [(c, int(f)) not in losers for c, f in zip(df["report_code"], df["fight_id"])]
            df = df[keep].reset_index(drop=True)
        # the rankings overlay (score, medal, keystone clock) from the table;
        # a run the table does not know keeps the values a restored raw.npz
        # carried (§6.1: the day's tar is self-contained)
        keys = sorted({(c, int(f)) for c, f in zip(df["report_code"], df["fight_id"])})
        ov = self.db.overlay_for(keys)
        score_ov, medal_ov, ks = [], [], []
        for c, f, s0, m0, sv, mv, kv in zip(df["report_code"], df["fight_id"], df["score"], df["medal"],
                                           df["score_ov"], df["medal_ov"], df["keystone_s"]):
            o = ov.get((c, int(f)))
            if o is not None:
                osc, omd, okm, present = o
                # score/medal only while the run is on the current pages
                # (export() overlays from the current snapshot alone); the
                # clock stays (keystone_times.json accumulates), §6.2-1
                score_ov.append(osc if present and osc is not None else s0)
                medal_ov.append(omd if present and omd is not None else m0)
                ks.append(round(okm / 1000, 1) if okm else np.nan)
            else:
                score_ov.append(sv if not (isinstance(sv, float) and math.isnan(sv)) else s0)
                medal_ov.append(mv if mv else m0)
                ks.append(kv)
        df["score_ov"] = np.array(score_ov, dtype=np.float64)
        df["medal_ov"] = np.array(medal_ov, dtype=object).astype(str)
        df["keystone_s"] = np.array(ks, dtype=np.float64)
        return df

    def _collapse(self, day: int, df: pd.DataFrame) -> tuple[pd.DataFrame, list]:
        """The duplicate-upload collapse (F:1132-1152), global through the
        signature table. Returns the frame without losers and the neighbour
        days a loser was found in."""
        if not len(df):
            return df, []
        runs = collections.OrderedDict()
        for i, (c, f) in enumerate(zip(df["report_code"], df["fight_id"])):
            runs.setdefault((c, int(f)), []).append(i)
        info = {}
        for (c, f), idx in runs.items():
            ks = df["keystone_s"].iat[idx[0]]
            if isinstance(ks, float) and math.isnan(ks):
                continue
            roster = "|".join(sorted(str(df["character"].iat[i]) for i in idx))
            sig = f"{df['dungeon'].iat[idx[0]]}/{int(df['key_level'].iat[idx[0]])}/{ks}/{roster}"
            info[(c, f)] = (sig, len(idx))
        # intra-day: best per sig = largest roster, then smallest code
        best: dict = {}
        for (c, f), (sig, n) in info.items():
            cand = (-n, c, f)
            if sig not in best or cand < best[sig][0]:
                best[sig] = (cand, (c, f), n)
        drop = set()
        for (c, f), (sig, n) in info.items():
            if best[sig][1] != (c, f):
                drop.add((c, f))
                self.db.add_loser(c, f, day, best[sig][1][0], best[sig][1][1])
        neighbours = []
        for sig, (cand, (c, f), n) in best.items():
            e = self.db.sig_get(sig)
            if e is None or (e[1], int(e[2])) == (c, f):
                self.db.sig_set(sig, day, c, f, n)
                continue
            eday, ecode, efid, en = int(e[0]), e[1], int(e[2]), int(e[3])
            if cand < (-en, ecode, efid):
                # we win: the other copy loses, wherever it lives
                self.db.add_loser(ecode, efid, eday, c, f)
                self.db.sig_set(sig, day, c, f, n)
                if eday != day:
                    neighbours.append((eday, ecode, efid))
            else:
                drop.add((c, f))
                self.db.add_loser(c, f, day, ecode, efid)
        if drop:
            keep = [(c, int(f)) not in drop for c, f in zip(df["report_code"], df["fight_id"])]
            df = df[keep].reset_index(drop=True)
        return df, neighbours

    def _gear_cache(self, day: int) -> dict:
        """gear.npz + pending gear records -> the merged cache (keep last per key)."""
        dd = self.P.day_dir(day)
        cache = load_npz(dd / "gear.npz")
        pend = read_jsonl(dd / "pending_gear.jsonl")
        if not pend:
            return cache if cache is not None else empty_gear_cache()
        # EVERY record in arrival order, never deduped here: the legacy
        # readers apply different last-wins rules per consumer (sets from
        # the last gear-bearing record, meta from the last record with a
        # build or gear, stats from the last torn-free one, the trait
        # material over all records) and gear_meta_journal() replays them
        recs = gear_cache_to_records(cache) if cache is not None else []
        return gear_cache_from_records(dedupe_records(recs + pend))

    def _abil_cache(self, day: int) -> dict:
        dd = self.P.day_dir(day)
        cache = load_npz(dd / "abil.npz")
        pend = read_jsonl(dd / "pending_abil.jsonl")
        if not pend:
            return cache if cache is not None else empty_abil_cache()
        # every record in arrival order (hero resolution takes the last
        # record with a non-empty breakdown, project() the last record)
        recs = abil_records_from_cache(cache) if cache is not None else []
        return abil_cache_from_records(dedupe_records(recs + pend))

    def build_day(self, day: int) -> None:
        t0 = time.perf_counter()
        st_day = self.st.day(day)
        dd = self.P.day_dir(day)
        df = self._frame(day)
        df, neighbours = self._collapse(day, df)
        gear = self._gear_cache(day)
        abil = self._abil_cache(day)
        for eday, ecode, efid in neighbours:
            self.st.mark_dirty(eday, "collapse")
            self.st.d["invalidations"].append({"day": eday, "reason": "collapse", "loser": [ecode, efid],
                                               "winner_day": day, "seq": self.st.d["seq"] + 1,
                                               "at": iso(self.now_ms)})
            self.health(f"parts.collapse.neighbour={eday}:{ecode}:{efid}")
        # persist the canonical caches (the pending files are consumed). A
        # kill between these saves and the unlinks below leaves the pending
        # files beside caches that already absorbed them: the next rebuild
        # reads both and dedupe_records drops the absorbed records by their
        # arrival stamps (§6.3) -- the crash hook lets the test stand there
        save_npz(dd / "raw.npz", frame_to_raw(df))
        save_npz(dd / "gear.npz", gear)
        save_npz(dd / "abil.npz", abil)
        self.days_built_this_run += 1
        self._test_crash("day", "after_save", self.days_built_this_run)
        for name in ("pending_players.jsonl", "pending_gear.jsonl", "pending_abil.jsonl"):
            p = dd / name
            if p.exists():
                p.unlink()
        if not len(df):
            for k in ("f", "rows_sha"):
                st_day[k] = None
            st_day.update({"n": 0, "runs": 0, "dirty": False, "reasons": [], "specs": {}, "w": {}, "b": 0,
                           "w_clamp": {}, "hero_recovered": 0, "built_seq": self.st.d["seq"] + 1})
            for name in ("thin.npz", "keys.npz"):
                p = dd / name
                if p.exists():
                    p.unlink()
            self.touched_weeks |= set(int(w) for w in st_day.get("weeks", []))
            st_day["weeks"] = []
            self.rebuilt_days.append(day)
            self._stage("day_build", t0)
            return
        out = build_day_outputs(self, day, df, gear, abil)
        old_weeks = set(int(w) for w in st_day.get("weeks", []))
        st_day.update({"n": out["n"], "runs": out["runs"], "rows_sha": out["rows_sha"],
                       "inputs_sha": out["inputs_sha"], "rules_sha": self.rules_sha,
                       "f": out["f"], "b": out["b"], "specs": out["specs"], "w": out["w"],
                       "w_clamp": out["w_clamp"], "weeks": sorted(out["weeks"]), "dirty": False,
                       "reasons": [], "built_seq": self.st.d["seq"] + 1, "bytes": out["bytes"],
                       "hero_recovered": out["hero_recovered"]})
        self.touched_weeks |= old_weeks | set(out["weeks"])
        self.rebuilt_days.append(day)
        self._stage("day_build", t0)

    # ---- step 3: window ----------------------------------------------------
    def listed_days(self, days_with_rows: list) -> list:
        """The days the manifest lists (§3.1 cube-gap invariant): every day
        of the row window plus every older day whose week's cube is not
        published -- in stage B no cube is, so every day with rows."""
        wf = self.window_from()
        listed = []
        for d in days_with_rows:
            if d < 0:
                continue                          # the undated day is appended by the caller
            if d >= wf:
                listed.append(d)
            else:
                weeks = self.st.d["days"][str(d)].get("weeks") or []
                if any(not (self.st.d["weeks"].get(str(w)) or {}).get("published") for w in weeks) or not weeks:
                    listed.append(d)
        return sorted(listed)

    def window_from(self) -> int:
        """The first UTC day that can hold a row of bucket 2 in any region."""
        earliest = None
        for reg in self.season.vocab["regions"]:
            a = self.season.anchor(reg)
            cur_w = (self.now_ms - a) // WEEK_MS
            start = a + (cur_w - 2) * WEEK_MS
            earliest = start if earliest is None else min(earliest, start)
        return int((earliest - self.season.epoch_ms) // DAY_MS)

    def load_rows(self, day: int) -> pf.Container:
        c = self.rows_cache.get(day)
        if c is None:
            c = pf.read(self.P.out / self.st.d["days"][str(day)]["f"], expect_kind="rows")
            self.rows_cache[day] = c
        return c

    def days_with_rows(self) -> list:
        """Every dated day (state key >= 0) holding a rows file."""
        return sorted(int(k) for k, e in self.st.d["days"].items() if int(k) >= 0 and e.get("n") and e.get("f"))

    def undated_listed(self) -> bool:
        und = self.st.d["days"].get("-1")
        return bool(und and und.get("f") and und.get("n"))

    def step3(self) -> dict:
        t0 = time.perf_counter()
        # the listed set: the dated days of the window / un-cubed weeks plus,
        # last, the undated day (§2.2: it counts in unfiltered totals)
        listed = self.listed_days(self.days_with_rows()) + ([-1] if self.undated_listed() else [])
        # a listed day of an older rules generation (it was unlisted while
        # the rule tables changed) is unprojected-pending: queue it (§3.3)
        for d in listed:
            e = self.st.d["days"][str(d)]
            if e.get("rules_sha") != self.rules_sha and not e.get("dirty"):
                self.st.mark_dirty(d, "rules")
        rio_sha = sha256_file(self.P.rio) if self.P.rio.exists() else ""
        fp = sha256_bytes(canon({"days": [(d, self.st.d["days"][str(d)]["rows_sha"]) for d in listed],
                                 "rio": rio_sha, "pins": self.pins.sha,
                                 "char_max": self.reg.total, "rules": self.rules_sha}).encode())
        prev = self.st.d.get("window") or {}
        # skipped when no window block changed (§6.2-3); the daily slot
        # always runs it (the charscore base is rewritten there)
        if prev.get("fp") == fp and prev.get("artifacts") and not self.daily and not self.deadline.reached():
            self.health("parts.window=unchanged")
            self._stage("window", t0)
            return prev["artifacts"]
        if self.deadline.reached() and prev.get("artifacts"):
            self.window_stale = True
            self.health("parts.window_stale=1")
            self._stage("window", t0)
            return prev["artifacts"]
        art = window_stage(self, listed, rio_sha)
        art["listed"] = listed
        self.st.d["window"] = {"fp": fp, "artifacts": art}
        self._stage("window", t0)
        return art

    # ---- manifest ---------------------------------------------------------
    def write_manifest(self, art: dict) -> dict:
        t0 = time.perf_counter()
        S = self.season
        # always the CURRENT listed set: a deadline stop reuses the previous
        # window artifacts (parts.window_stale=1) but publishes every day
        # that completed -- today's file is never held back (§6)
        listed = self.listed_days(self.days_with_rows())
        days_out = []
        for d in listed:
            e = self.st.d["days"][str(d)]
            days_out.append({"d": d, "n": e["n"], "runs": e["runs"], "frozen": bool(e.get("frozen")),
                             "w": e.get("w") or {}, "f": e["f"], "b": e["b"], "rules_sha": e["rules_sha"],
                             "specs": dict(sorted(e["specs"].items(), key=lambda kv: int(kv[0])))})
        # exactly ONE undated entry, last (§2.2/§2.6): d "undated" in the
        # manifest, "undated" in the rows and shard headers, so the client's
        # block guard (partition_client.join_blocks) sees one spelling
        und = self.st.d["days"].get("-1")
        has_und = self.undated_listed()
        days_out.append({"d": "undated", "n": und["n"] if has_und else 0,
                         "runs": und["runs"] if has_und else 0, "frozen": True,
                         "f": und["f"] if has_und else None, "b": und["b"] if has_und else 0,
                         "rules_sha": und["rules_sha"] if has_und else None,
                         "specs": dict(und["specs"]) if has_und else {}})
        weeks_out = []
        for w in sorted(int(k) for k in self.st.d["weeks"]):
            we = self.st.d["weeks"][str(w)]
            entry = {"w": w, "reg": we.get("reg") or {}}
            if we.get("published"):
                entry.update({"cube_sha": we["cube_sha"], "f": we["f"], "b": we["b"]})
            weeks_out.append(entry)
        cube_missing = [w for w in sorted(int(k) for k in self.st.d["weeks"])
                        if not self.st.d["weeks"][str(w)].get("published")
                        and any(d < self.window_from() for d in self.st.d["weeks"][str(w)].get("days", []))]
        if cube_missing:
            self.health("parts.cube_missing=" + ",".join(str(w) for w in cube_missing))
        pars = [int((self.pins.doc.get("pars") or {}).get(dn, 0) or 0) for dn in S.vocab["dungeons"]]
        projection = art.get("projection") if "projection" in art else (self.st.d["projection"] or {}).get("meta")
        if projection:
            projection = dict(projection, rules_sha=self.rules_sha)
        tuning = None
        if self.patch:
            tuning = {"label": self.patch.get("label"), "date": self.patch.get("date"),
                      "regions": self.patch.get("regions"), "note": self.patch.get("note", ""),
                      "runs": int(art["window"].get("post_rows", 0))}
        man = {
            "v": 1, "fmt": pf.FORMAT_VERSION, "slug": S.slug, "season": S.name, "epoch": S.epoch,
            "built": None, "newest_row": art["window"].get("newest_row"), "seq": None,
            "char_max": int(self.reg.total),
            "reset_rules": S.reset_rules,
            "anchors": {r: iso(S.anchor(r)) for r in ("US", "EU")} | {"*": iso(sc.anchor_ms("*", S.epoch, S.rules, S.default_rule))},
            "vocab": S.vocab, "spec_class": S.doc["spec_class"], "spec_pairs": S.doc["spec_pairs"],
            "spec_role": [S.codes["roles"].get(r, S.codes["roles"]["Unknown"]) for r in S.spec_role],
            "emb": art["emb"], "pars": pars, "tuning": tuning, "projection": projection,
            "flags": {"tier": bool(art["window"].get("has_tier")), "timed": bool(art["window"].get("has_timed")),
                      "tune": bool(tuning), "proj": bool(projection), "rating": bool(art["charscore"]["pairs"] or art["charscore"]["delta"]["pairs"])},
            "window": {k: art["window"][k] for k in ("day_from", "day_to", "rows", "runs", "keys", "refchars")},
            "days": days_out, "weeks": weeks_out,
            "spec_vocab": art["spec_vocab"], "charscore": art["charscore"],
            "specstats": art["specstats"], "talents": None,
            "legacy": {"f": "../../data.json.gz"},
        }
        body_probe = canon({k: v for k, v in man.items() if k not in ("built", "seq")})
        probe_sha = sha256_bytes(body_probe.encode())
        if probe_sha == self.st.d.get("last_manifest_sha") and self.st.d.get("built"):
            man["seq"], man["built"] = self.st.d["seq"], self.st.d["built"]
            self.health("parts.manifest=unchanged")
        else:
            man["seq"] = int(self.st.d["seq"]) + 1
            man["built"] = iso(self.now_ms)
            self.st.d["seq"], self.st.d["built"] = man["seq"], man["built"]
            self.st.d["last_manifest_sha"] = probe_sha
        # sorted keys: state.json round-trips dicts with sorted keys, so an
        # unchanged manifest must not depend on whether its parts were
        # computed this run or restored (byte-identical reruns, §6.3)
        write_atomic(self.P.out / "manifest.json",
                     json.dumps(man, separators=(",", ":"), ensure_ascii=False, sort_keys=True).encode("utf-8"))
        write_atomic(self.P.out.parent / "current.json",
                     json.dumps({"slug": S.slug, "manifest": f"{S.slug}/manifest.json"}).encode())
        # staged state snapshot for journal_parts.py (stage C uploads)
        self.P.upload.mkdir(parents=True, exist_ok=True)
        self._stage("manifest", t0)
        return man

    def manifest_files(self, man: dict) -> set:
        refs = set()
        for e in man["days"]:
            if e.get("f"):
                refs.add(e["f"])
            refs.update(e.get("specs", {}).values())
        for w in man["weeks"]:
            for f in (w.get("f") or {}).values():
                refs.add(f)
        for k in ("spec_vocab", "specstats", "talents"):
            if man.get(k) and man[k].get("f"):
                refs.add(man[k]["f"])
        if man.get("charscore"):
            if man["charscore"].get("f"):
                refs.add(man["charscore"]["f"])
            d = man["charscore"].get("delta") or {}
            if d.get("f"):
                refs.add(d["f"])
        return refs

    def prune_and_publish(self, man: dict) -> None:
        t0 = time.perf_counter()
        refs = self.manifest_files(man)
        hist = self.st.d["manifest_refs"]
        if not hist or hist[-1] != sorted(refs):
            hist.append(sorted(refs))
        del hist[:-RETENTION_GENERATIONS]
        self.st.d["manifest_refs"] = hist
        keep = set()
        for lst in hist:
            keep.update(lst)
        now = time.time()
        removed = 0
        out = self.P.out
        for p in out.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(out).as_posix()
            if rel == "manifest.json":
                continue
            if p.name.endswith(".tmp") or p.name.endswith(".tmp.npz"):
                p.unlink()
                removed += 1
                continue
            if rel in keep:
                continue
            if now - p.stat().st_mtime < RETENTION_YOUNG_S:
                continue
            p.unlink()
            removed += 1
        for d in sorted((p for p in out.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
            try:
                d.rmdir()
            except OSError:
                pass
        # mirror out/ -> site/d/<slug>/ and current.json
        dst = self.P.site_d / self.season.slug
        dst.mkdir(parents=True, exist_ok=True)
        want = {}
        for p in out.rglob("*"):
            if p.is_file():
                want[p.relative_to(out).as_posix()] = p
        # every content-hashed file first, manifest.json last: a kill inside
        # this copy never leaves a manifest naming files the mirror lacks
        for rel in sorted(want, key=lambda r: (r == "manifest.json", r)):
            src = want[rel]
            tgt = dst / rel
            if tgt.exists() and tgt.stat().st_size == src.stat().st_size and rel != "manifest.json":
                continue
            tgt.parent.mkdir(parents=True, exist_ok=True)
            tmp = tgt.with_name(tgt.name + ".tmp")
            shutil.copyfile(src, tmp)
            os.replace(tmp, tgt)
        for p in dst.rglob("*"):
            if p.is_file() and p.relative_to(dst).as_posix() not in want:
                p.unlink()
        shutil.copyfile(self.P.out.parent / "current.json", self.P.site_d / "current.json")
        self.health(f"parts.pruned={removed}")
        self._stage("publish", t0)

    # ---- health ----------------------------------------------------------
    def write_health(self, status: str, man: dict | None) -> None:
        lines = [f"parts.status={status}", f"parts.seq={self.st.d.get('seq')}",
                 f"parts.built={self.st.d.get('built')}"]
        if man:
            lines += [f"parts.newest_row={man.get('newest_row')}",
                      f"parts.window_rows={man['window']['rows']}",
                      f"parts.window_days={man['window']['day_to'] - man['window']['day_from'] + 1}",
                      f"parts.listed_days={sum(1 for e in man['days'] if e.get('f'))}",
                      f"parts.char_max={man['char_max']}"]
        pend = sum(1 for e in self.st.d["days"].values() if e.get("dirty") and "collapse" in e.get("reasons", []))
        done = sum(1 for x in self.st.d["invalidations"] if x.get("reason") == "collapse")
        lines.append(f"parts.invalidated_days={done}/{pend}")
        if self.deadline.hit:
            lines.append(f"parts.deadline_s={self.deadline.limit}")
        for k, v in sorted(self.stage_s.items()):
            lines.append(f"parts.stage.{k}={v:.1f}")
        for (col, val), n in sorted(self.season.unknown_counts.items()):
            lines.append(f"vocab.unknown={col}:{val}:{n}")
        lines += self.health_lines
        body = "\n".join(lines) + "\n"
        self.P.parts.mkdir(parents=True, exist_ok=True)
        write_atomic(self.P.health, body.encode("utf-8"))

    # ---- run -------------------------------------------------------------
    def run(self) -> int:
        signal.signal(signal.SIGTERM, _handle_term)
        EPOCH_MS_CACHE[0] = self.season.epoch_ms
        self.P.state_dir.mkdir(parents=True, exist_ok=True)
        man = None
        try:
            self.st.d["chars_at_start"] = self.reg.total
            self.prepare_pins()
            self.step1()
            self.checkpoint()
            self.step2()
            # freeze + cubes run before the window stage so this very
            # manifest lists the days of the weeks whose cube it names, and
            # no day of a week whose cube it publishes for the first time is
            # fetched by the client for nothing (§3.1 cube-gap invariant
            # holds either way: a day stays listed until its cube is named)
            self.step4()
            art = self.step3()
            man = self.write_manifest(art)
            self.prune_and_publish(man)
            self.st.save()
            status = "ok"
        except Exception as e:                       # never a half-written state
            import traceback
            traceback.print_exc()
            self.health(f"parts.error={type(e).__name__}:{str(e)[:200]}")
            status = "failed"
            try:
                self.st.save()
            except Exception:
                pass
        finally:
            try:
                self.db.commit()
            except Exception:
                pass
        self.write_health(status, man)
        return 0 if status == "ok" else 1


EPOCH_MS_CACHE = [sc.parse_iso_ms("2026-01-01T00:00:00Z")]


# ===================================================== per-day cache codecs
GEAR_STR = ("code", "character", "server", "cls", "spec", "build", "blob")
STATS = list(bsd.SIDECAR_STATS)


def gear_key(r: dict) -> tuple:
    return bsd._gear_key(r.get("report_code"), r.get("fight_id") or 0, r.get("character"), r.get("server"))


def empty_gear_cache() -> dict:
    n = 0
    return {"code": np.zeros(n, dtype="<U1"), "fid": np.zeros(n, dtype=np.int32),
            "character": np.zeros(n, dtype="<U1"), "server": np.zeros(n, dtype="<U1"),
            "cls": np.zeros(n, dtype="<U1"), "spec": np.zeros(n, dtype="<U1"),
            "has_gear": np.zeros(n, dtype=bool), "has_stats": np.zeros(n, dtype=bool),
            "item": np.zeros((n, NSLOTS), dtype=np.uint32), "ilvl": np.zeros((n, NSLOTS), dtype=np.uint16),
            "setid": np.zeros((n, NSLOTS), dtype=np.uint32), "ench": np.zeros((n, NSLOTS), dtype=np.uint32),
            "bonus_off": np.zeros(n * NSLOTS + 1, dtype=np.int64), "bonus_val": np.zeros(0, dtype=np.int64),
            "build": np.zeros(n, dtype="<U1"), "blob": np.zeros(n, dtype="<U1"),
            "specid": np.zeros(n, dtype=np.int64), "stats": np.zeros((n, len(STATS)), dtype=np.float64),
            "server_null": np.zeros(n, dtype=bool), "gseq": np.zeros(n, dtype=np.int64)}


def gear_cache_from_records(recs: list) -> dict:
    """Journal gear records -> gear.npz arrays (§6.1), last-wins per key
    already applied by the caller. Keeps exactly what the consumers read:
    per slot id/ilvl/set/ench/bonus, the build identity (import string or
    tree hash), the tree blob, specID and the ten sidecar stats."""
    n = len(recs)
    out = empty_gear_cache()
    if not n:
        return out
    code = np.array([ustr(r.get("report_code")) for r in recs])
    fid = np.array([int(r.get("fight_id") or 0) for r in recs], dtype=np.int32)
    character = np.array([ustr(r.get("character")) for r in recs])
    server = np.array([ustr(r.get("server")) for r in recs])
    server_null = np.array([r.get("server") is None for r in recs], dtype=bool)
    cls = np.array([ustr(r.get("class")) for r in recs])
    spec = np.array([ustr(r.get("spec")) for r in recs])
    has_gear = np.zeros(n, dtype=bool)
    has_stats = np.zeros(n, dtype=bool)
    item = np.zeros((n, NSLOTS), dtype=np.uint32)
    ilvl = np.zeros((n, NSLOTS), dtype=np.uint16)
    setid = np.zeros((n, NSLOTS), dtype=np.uint32)
    ench = np.zeros((n, NSLOTS), dtype=np.uint32)
    bonus_off = np.zeros(n * NSLOTS + 1, dtype=np.int64)
    bonus_val: list = []
    build = [""] * n
    blob = [""] * n
    specid = np.zeros(n, dtype=np.int64)
    stats = np.full((n, len(STATS)), np.nan, dtype=np.float64)
    core = bsd.SPECSTATS_CORE
    for i, r in enumerate(recs):
        gear = r.get("gear")
        if isinstance(gear, list):
            has_gear[i] = True
            for s, it in enumerate(gear[:NSLOTS]):
                cell = i * NSLOTS + s
                if isinstance(it, dict) and it.get("id"):
                    item[i, s] = int(it["id"])
                    v = it.get("ilvl")
                    ilvl[i, s] = min(int(v), 65535) if isinstance(v, (int, float)) and v else 0
                    sid = it.get("set")
                    if sid not in (None, 0, "0", "") and str(sid).isdigit():
                        setid[i, s] = int(sid)
                    e = it.get("ench")
                    if isinstance(e, (int, float)) and e:
                        ench[i, s] = int(e)
                    b = it.get("bonus")
                    if isinstance(b, list):
                        bonus_val.extend(int(x) for x in b if isinstance(x, int))
                bonus_off[cell + 1] = len(bonus_val)
            for s in range(len(gear[:NSLOTS]), NSLOTS):
                bonus_off[i * NSLOTS + s + 1] = len(bonus_val)
        else:
            for s in range(NSLOTS):
                bonus_off[i * NSLOTS + s + 1] = len(bonus_val)
        tal = r.get("talents")
        if isinstance(tal, dict):
            bl = bsd._tree_blob(tal.get("tree"))
            blob[i] = bl or ""
            b = tal.get("talentImportString")
            if isinstance(b, str) and b:
                build[i] = b
            elif bl is not None:
                build[i] = "t:" + hashlib.md5(bl.encode()).hexdigest()[:12]
            sid = tal.get("specID")
            if isinstance(sid, int) and sid:
                specid[i] = sid
            stv = tal.get("stats")
            if isinstance(stv, dict):
                vals = {k: v for k, v in stv.items()
                        if isinstance(v, (int, float)) and not isinstance(v, bool) and not pd.isna(v)}
                if not any(s not in vals for s in core):
                    has_stats[i] = True
                    for j, nm in enumerate(STATS):
                        if nm in vals:
                            stats[i, j] = float(vals[nm])
    out.update({"code": code, "fid": fid, "character": character, "server": server, "cls": cls, "spec": spec,
                "has_gear": has_gear, "has_stats": has_stats, "item": item, "ilvl": ilvl, "setid": setid,
                "ench": ench, "bonus_off": bonus_off, "bonus_val": np.array(bonus_val, dtype=np.int64),
                "build": np.array(build), "blob": np.array(blob), "specid": specid, "stats": stats,
                "server_null": server_null,
                "gseq": np.array([int(r.get("_gseq") or 0) for r in recs], dtype=np.int64)})
    return out


def gear_cache_to_records(c: dict) -> list:
    """The inverse, in the journal's shape (what the legacy readers see)."""
    out = []
    n = len(c["code"])
    for i in range(n):
        rec = {"report_code": str(c["code"][i]), "fight_id": int(c["fid"][i]),
               "character": str(c["character"][i]) or None,
               "server": None if c["server_null"][i] else str(c["server"][i]),
               "class": str(c["cls"][i]), "spec": str(c["spec"][i])}
        if "gseq" in c and int(c["gseq"][i]):
            rec["_gseq"] = int(c["gseq"][i])
        if c["has_gear"][i]:
            gear = []
            last = 0
            for s in range(NSLOTS):
                if c["item"][i, s]:
                    last = s + 1
            for s in range(last):
                iid = int(c["item"][i, s])
                if not iid:
                    gear.append(None)
                    continue
                it = {"id": iid}
                if c["ilvl"][i, s]:
                    it["ilvl"] = int(c["ilvl"][i, s])
                if c["setid"][i, s]:
                    it["set"] = int(c["setid"][i, s])
                if c["ench"][i, s]:
                    it["ench"] = int(c["ench"][i, s])
                a, b = int(c["bonus_off"][i * NSLOTS + s]), int(c["bonus_off"][i * NSLOTS + s + 1])
                if b > a:
                    it["bonus"] = [int(x) for x in c["bonus_val"][a:b]]
                gear.append(it)
            rec["gear"] = gear
        else:
            rec["gear"] = None
        tal = {}
        if str(c["blob"][i]):
            tal["tree"] = [{"id": int(p.split(":")[0]), "rank": int(p.split(":")[1])}
                           for p in str(c["blob"][i]).split("|")]
        b = str(c["build"][i])
        if b and not b.startswith("t:"):
            tal["talentImportString"] = b
        if c["specid"][i]:
            tal["specID"] = int(c["specid"][i])
        if c["has_stats"][i]:
            tal["stats"] = {nm: float(c["stats"][i, j]) for j, nm in enumerate(STATS)
                            if not math.isnan(c["stats"][i, j])}
        rec["talents"] = tal or None
        out.append(rec)
    return out


def gear_meta_journal(c: dict) -> tuple[dict, dict, dict]:
    """gear cache -> (sets dict, stats dict, meta dict) keyed like
    gear_journal_pass(): the legacy consumers' inputs, plus per key the
    (blob, specid, build) for the trait material."""
    sets, stats, meta = {}, {}, {}
    recs = gear_cache_to_records(c)
    for i, r in enumerate(recs):
        key = gear_key(r)
        gear = r.get("gear")
        if isinstance(gear, list):
            counts: dict = {}
            for it in gear:
                if isinstance(it, dict) and it.get("set"):
                    counts[str(it["set"])] = counts.get(str(it["set"]), 0) + 1
            sets[key] = counts
        tal = r.get("talents") or {}
        if c["has_stats"][i]:
            stats[key] = {"stats": dict(tal["stats"])}
        build = str(c["build"][i]) or None
        if build is not None or isinstance(gear, list):
            meta[key] = {"build": build, "gear": gear if isinstance(gear, list) else None,
                         "blob": str(c["blob"][i]), "i": i}
    return sets, stats, meta


def empty_abil_cache() -> dict:
    return {"code": np.zeros(0, dtype="<U1"), "fid": np.zeros(0, dtype=np.int32), "name": np.zeros(0, dtype="<U1"),
            "cls": np.zeros(0, dtype="<U1"), "sets": np.zeros(0, dtype="<U1"), "total": np.zeros(0, dtype=np.int64),
            "ilvl": np.zeros(0, dtype=np.int64), "aoff": np.zeros(1, dtype=np.int64),
            "anames": np.zeros(0, dtype="<U1"), "aid": np.zeros(0, dtype=np.int64),
            "atot": np.zeros(0, dtype=np.int64), "auses": np.zeros(0, dtype=np.int64),
            "aseq": np.zeros(0, dtype=np.int64)}


def abil_cache_from_records(recs: list) -> dict:
    if not recs:
        return empty_abil_cache()
    names: dict = {}
    aid, atot, auses, aoff = [], [], [], [0]
    for r in recs:
        for a in r.get("abilities") or []:
            nm = ustr(a.get("name"))
            j = names.get(nm)
            if j is None:
                j = names[nm] = len(names)
            aid.append(j)
            atot.append(int(a.get("total") or 0))
            auses.append(int(a.get("uses") or 0))
        aoff.append(len(aid))
    return {"code": np.array([ustr(r.get("report_code")) for r in recs]),
            "fid": np.array([int(r.get("fight_id") or 0) for r in recs], dtype=np.int32),
            "name": np.array([ustr(r.get("name")) for r in recs]),
            "cls": np.array([ustr(r.get("class")) for r in recs]),
            "sets": np.array([canon(r.get("sets") or {}) for r in recs]),
            "total": np.array([int(r.get("total") or 0) for r in recs], dtype=np.int64),
            "ilvl": np.array([int(r.get("ilvl") or 0) for r in recs], dtype=np.int64),
            "aoff": np.array(aoff, dtype=np.int64), "anames": np.array(list(names) or [""]),
            "aid": np.array(aid, dtype=np.int64), "atot": np.array(atot, dtype=np.int64),
            "auses": np.array(auses, dtype=np.int64),
            "aseq": np.array([int(r.get("_aseq") or 0) for r in recs], dtype=np.int64)}


def abil_records_from_cache(c: dict) -> list:
    out = []
    names = c["anames"]
    for i in range(len(c["code"])):
        a, b = int(c["aoff"][i]), int(c["aoff"][i + 1])
        rec = {"report_code": str(c["code"][i]), "fight_id": int(c["fid"][i]), "name": str(c["name"][i]),
               "class": str(c["cls"][i]), "total": int(c["total"][i]), "ilvl": int(c["ilvl"][i]),
               "sets": json.loads(str(c["sets"][i])),
               "abilities": [{"name": str(names[c["aid"][j]]), "total": int(c["atot"][j]),
                              "uses": int(c["auses"][j])} for j in range(a, b)]}
        if "aseq" in c and int(c["aseq"][i]):
            rec["_aseq"] = int(c["aseq"][i])
        out.append(rec)
    return out


RAW_COLS = ["character", "server", "region", "class", "spec", "hero_talent", "role", "dungeon",
            "key_level", "duration_s", "damage_done", "dps", "deaths", "item_level", "score", "medal",
            "affixes", "report_code", "fight_id", "started_at", "set_counts", "set_counts_null", "char_id",
            "score_ov", "medal_ov", "keystone_s", "seq"]


def frame_to_raw(df: pd.DataFrame) -> dict:
    out = {}
    for k in RAW_COLS:
        v = df[k]
        if k in ("key_level", "deaths", "fight_id", "seq"):
            out[k] = v.astype(np.int64).to_numpy()
        elif k == "char_id":
            out[k] = v.astype(np.uint32).to_numpy()
        elif k == "set_counts_null":
            out[k] = v.astype(bool).to_numpy()
        elif k in ("duration_s", "damage_done", "dps", "item_level", "score", "started_at", "score_ov", "keystone_s"):
            out[k] = pd.to_numeric(v, errors="coerce").astype(np.float64).to_numpy()
        else:
            out[k] = np.array([ustr(x) for x in v.tolist()])
    return out


# ============================================================ day outputs
def emb_of_factory(emb_cfg: dict, markers: set, crafted: set):
    """The legacy vocab's embellishment identity (builds_sidecar's emb_of):
    None plain; -1 the generic bucket; else the named identity id."""
    emb_ids, emb_names, intrinsic = emb_cfg["ids"], emb_cfg["names"], emb_cfg["intrinsic"]

    def emb_of(iid: int, bonus: list):
        marked = any(b in markers for b in bonus)
        if not marked and iid not in intrinsic:
            return None
        hits = [b for b in bonus if b in emb_ids]
        if len(hits) > 1:
            return -1
        if len(hits) == 1 and emb_names.get(hits[0]):
            return hits[0]
        return -1
    return emb_of


def emb_labels(emb_cfg: dict) -> list:
    return ["", "embellished"] + sorted(set(emb_cfg["names"].values()))


def build_hash64(build: str, blob: str) -> int:
    """First 64 bits of md5 over the build identity's SOURCE: the tree blob
    for a tree-hash identity (so its first 12 hex digits ARE the legacy
    `t:` id), the import string otherwise. 0 = no build."""
    if not build:
        return 0
    src = blob if build.startswith("t:") and blob else build
    return int.from_bytes(hashlib.md5(src.encode("utf-8")).digest()[:8], "big")


def build_day_outputs(B: Builder, day: int, df: pd.DataFrame, gear: dict, abil: dict) -> dict:
    """Legacy build() on one day's frame (B:2685-2842 with the §5 pins), the
    content sort, the rows file, the shard blocks, thin.npz, keys.npz."""
    S = B.season
    df = df.copy()
    # keystone clock (use_keystone_clock, B:253)
    ks = pd.to_numeric(df["keystone_s"], errors="coerce")
    ok = ks.notna() & (ks > 0) & df["damage_done"].notna()
    if ok.any():
        df.loc[ok, "dps"] = (df.loc[ok, "damage_done"] / ks[ok]).round(1)
        df.loc[ok, "duration_s"] = ks[ok].round(1)
    for col in ("class", "spec", "hero_talent", "role", "region", "dungeon"):
        df[col] = df[col].fillna("Unknown").replace("", "Unknown")
    # hero resolution with the pinned markers (B:142-178)
    hero_tab = B.pins.learned_table("hero_markers")
    heroes = df["hero_talent"].tolist()
    hero_recovered = 0                    # legacy HERO_FILLED (B:217): rows whose hero came from abilities
    if hero_tab and "Unknown" in set(heroes) and len(abil["code"]):
        hr = HeroResolver(markers={sp: dict(m) for sp, m in hero_tab.get("markers", {}).items()},
                          sole=dict(hero_tab.get("sole") or {}))
        names = abil["anames"]
        ab_by = {}
        for i in range(len(abil["code"])):
            a, b = int(abil["aoff"][i]), int(abil["aoff"][i + 1])
            if b > a:
                ab_by[(str(abil["code"][i]), int(abil["fid"][i]), str(abil["name"][i]))] = \
                    frozenset(str(names[abil["aid"][j]]) for j in range(a, b))
        for i, (c, f, ch, sp, cl, h) in enumerate(zip(df["report_code"], df["fight_id"], df["character"],
                                                      df["spec"], df["class"], heroes)):
            if h != "Unknown":
                continue
            got, _ = hr.classify(f"{sp} {cl}", ab_by.get((c, int(f), ch)))
            if got:
                heroes[i] = got
                hero_recovered += 1
        df["hero_talent"] = heroes
    started = pd.to_datetime(pd.to_numeric(df["started_at"], errors="coerce"), unit="ms", errors="coerce")
    dcol = ((started - S.epoch_ts).dt.days).fillna(-1).astype(int)
    hr_ = started.dt.hour.fillna(-1).astype(int)
    medal = df["medal_ov"].where(df["medal_ov"] != "", other=None)
    timed = medal.map(bsd.MEDAL_TIMED).fillna(-1).astype(int)
    post = bsd.post_tuning_flag(started, df["region"], B.patch)
    # tier against the pin (tier_pieces with WOWLOGS_PINS)
    sets, stats_j, meta_j = gear_meta_journal(gear)
    pin_sid = {c: str(v["id"]) for c, v in (B.pins.doc.get("tier_sets") or {}).items()}
    tier = []
    gkeys = []
    for c, f, ch, sv, cl, pv, pnull in zip(df["report_code"], df["fight_id"], df["character"], df["server"],
                                            df["class"], df["set_counts"], df["set_counts_null"]):
        k = bsd._gear_key(c, int(f), ch if ch != "" else None, sv if sv != "" else None)
        gkeys.append(k)
        cnt = sets.get(k)
        if cnt is None:
            cnt = None if pnull else bsd.unpack_sets(pv)
        if cnt is None:
            tier.append(-1)
        else:
            sid = pin_sid.get(cl)
            tier.append(min(cnt.get(sid, 0), 5) if sid else 0)
    tier = np.array(tier, dtype=np.int64)
    # tmul (§5): pinned items + tier, the current rule tables
    tmul = None
    if pt.RULES:
        recs = abil_records_from_cache(abil)
        work = df.copy()
        work["specname"] = work["spec"] + " " + work["class"]
        items = B.pins.learned_table("tuning_items")
        if items is not None:
            per = pt.project(work[post == 1], recs, pt.B_CENTRAL, items=set(items), tier=pin_sid)
            mult = per["mult"].reindex(df.index) if len(per) else pd.Series(np.nan, index=df.index)
            tuned = work["specname"].isin(pt.RULES) & (post == 1)
            for sname, rule in pt.RULES.items():
                if rule.get("hero_only"):
                    tuned &= ~(work["specname"].eq(sname) & work["hero_talent"].ne(rule["hero_only"]))
            unproj = tuned & mult.isna()
            mult = mult.fillna(1.0).mask(unproj, 0.0)
            tmul = mult.mul(10000).round().astype(int).to_numpy()
    # codes
    cls = S.code_series("classes", df["class"])
    spec = S.code_series("specs", df["spec"])
    hero = S.code_series("heroes", df["hero_talent"])
    role = S.code_series("roles", df["role"])
    dun = S.code_series("dungeons", df["dungeon"])
    reg = S.code_series("regions", df["region"])
    # role purity (§2.2)
    pure = collections.defaultdict(set)
    for c_, s_, r_ in zip(cls, spec, role):
        pure[(int(c_), int(s_))].add(int(r_))
    for (c_, s_), rs in pure.items():
        if len(rs) > 1:
            B.health(f"parts.role_impure={S.vocab['classes'][c_]}|{S.vocab['specs'][s_]}")
    # content sort (§2.2)
    role_rank = np.array([ROLE_RANK.get(r, 3) for r in df["role"]], dtype=np.int64)
    st_ms = pd.to_numeric(df["started_at"], errors="coerce").fillna(-1).astype(np.int64).to_numpy()
    order = np.lexsort([df["server"].to_numpy().astype(str), df["character"].to_numpy().astype(str), role_rank,
                        df["fight_id"].to_numpy().astype(np.int64), df["report_code"].to_numpy().astype(str), st_ms])
    df = df.iloc[order].reset_index(drop=True)
    perm = order
    cls, spec, hero, role, dun, reg = (a[perm] for a in (cls, spec, hero, role, dun, reg))
    tier = tier[perm]
    if tmul is not None:
        tmul = tmul[perm]
    gkeys = [gkeys[i] for i in perm]
    timed = timed.to_numpy()[perm]
    post = post.to_numpy()[perm]
    dcol = dcol.to_numpy()[perm]
    hr_ = hr_.to_numpy()[perm]
    started = started.iloc[perm].reset_index(drop=True)
    st_ms = st_ms[perm]
    run_ids = (df["report_code"].astype(str) + ":" + df["fight_id"].astype(str)).tolist()
    run = np.zeros(len(df), dtype=np.int64)
    ridx: dict = {}
    for i, k in enumerate(run_ids):
        j = ridx.get(k)
        if j is None:
            j = ridx[k] = len(ridx)
        run[i] = j
    n_runs = len(ridx)
    first = np.zeros(n_runs, dtype=np.int64)
    seen = np.zeros(n_runs, dtype=bool)
    for i in range(len(df)):
        if not seen[run[i]]:
            seen[run[i]] = True
            first[run[i]] = i
    dps_i = df["dps"].round(0).astype(int).to_numpy()
    dur_i = pd.to_numeric(df["duration_s"], errors="coerce").fillna(0).round(0).astype(int).to_numpy()
    kdur_i = pd.to_numeric(df["keystone_s"], errors="coerce").fillna(0).round(0).astype(int).to_numpy()
    deaths = df["deaths"].astype(int).to_numpy()
    key = df["key_level"].astype(int).to_numpy()
    char = df["char_id"].astype(np.int64).to_numpy()
    # run block
    r_dun, r_key, r_reg = dun[first], key[first], reg[first]
    r_timed, r_post, r_hr = timed[first], post[first], hr_[first]
    r_dur, r_kdur = dur_i[first], kdur_i[first]
    run_t = "u32" if n_runs >= 65536 else "u16"
    flags = {"tier": bool((tier >= 0).any()), "timed": bool((timed >= 0).any()),
             "post": bool((post >= 0).any()), "tmul": bool(tmul is not None and (tmul != 0).any())}
    # inputs_sha (§6.3): canonical rows ‖ gear ‖ abil ‖ FORMAT ‖ pins ‖ rules ‖ patch ‖ vocab.
    # The patch enters only for the days it can touch (day >= earliest cutoff
    # - 1, and the undated day) -- exactly the invalidation scope of §6.4 --
    # so a day before every cutoff keeps a name a from-scratch replay under
    # the new patch reproduces byte for byte (its post/tmul cannot differ)
    patch_scope = B.patch_id if (day < 0 or day >= B.patch_first_day() - 1) else ""
    canon_rows = hashlib.sha256()
    for tup in zip(df["report_code"], df["fight_id"], df["character"], df["server"], df["region"], df["class"],
                   df["spec"], df["hero_talent"], df["role"], df["dungeon"], key, df["duration_s"], df["damage_done"],
                   df["dps"], deaths, df["score_ov"], df["medal_ov"], st_ms, df["set_counts"], df["set_counts_null"],
                   char, df["keystone_s"]):
        canon_rows.update(repr(tuple(x.item() if hasattr(x, "item") else x for x in tup)).encode())
        canon_rows.update(b"\n")
    inputs_sha = sha256_bytes("|".join([
        canon_rows.hexdigest(), arrays_digest(gear), arrays_digest(abil), str(pf.FORMAT_VERSION),
        B.pins.inputs_material(), B.rules_sha, patch_scope, S.vocab_sha]).encode())
    cols = [pf.Column("cls", "u8", cls), pf.Column("spec", "u8", spec), pf.Column("hero", "u8", hero),
            pf.Column("role", "u8", role), pf.Column("deaths", "u8", deaths, clamp=(0, 255)),
            pf.Column("tier", "i8", tier), pf.Column("dps", "u32", dps_i, p=True),
            pf.Column("char", "u32", char, p=True), pf.Column("run", run_t, run, p=True)]
    if tmul is not None:
        cols.append(pf.Column("tmul", "u16", tmul, p=True, clamp=(0, 65535)))
    cols += [pf.Column("r_dun", "u8", r_dun), pf.Column("r_key", "u8", r_key), pf.Column("r_reg", "u8", r_reg),
             pf.Column("r_timed", "i8", r_timed), pf.Column("r_post", "i8", r_post), pf.Column("r_hr", "i8", r_hr),
             pf.Column("r_dur", "u16", r_dur, clamp=(0, 65535)), pf.Column("r_kdur", "u16", r_kdur, clamp=(0, 65535))]
    day_key = "undated" if day < 0 else f"d{day}"
    header = {"day": day if day >= 0 else "undated", "runs": n_runs, "inputs_sha": inputs_sha,
              "rules_sha": B.rules_sha, "flags": flags}
    w = pf.write(B.P.out / "rows", day_key, "rows", S.slug, len(df), cols, header)
    rows_rel = f"rows/{w.name}"
    B.health_lines.extend(pf.clamp_health_lines(w.name, w.clamped))
    rows_sha = w.sha
    byts = {"rows": len(w.gz), "spec": 0}
    # ---- shard blocks (§4.2)
    if B.emb_cfg is None:
        B.emb_cfg = bsd._name_caches()
    _items, _enchs, crafted, embc, markers, _icons = B.emb_cfg
    emb_of = emb_of_factory(embc, markers, crafted)
    labels = emb_labels(embc)
    label_code = {lab: i for i, lab in enumerate(labels)}
    eslots = [int(x) for x in (B.pins.doc.get("eslots") or DEFAULT_ESLOTS)]
    slots = list(bsd.BUILDS_SLOTS)
    covered = []
    for pos, k in enumerate(gkeys):
        m_ = meta_j.get(k)
        s_ = stats_j.get(k)
        if m_ is None and s_ is None:
            continue
        fl = 0
        if m_ is not None:
            fl |= (1 if isinstance(m_.get("gear"), list) else 0) | (2 if m_.get("build") else 0)
        if s_ is not None:
            fl |= 4
        if fl:
            covered.append((pos, k, fl))
    specs_out: dict = {}
    by_spec: dict = collections.defaultdict(list)
    for pos, k, fl in covered:
        by_spec[(int(cls[pos]), int(spec[pos]))].append((pos, k, fl))
    for (c_, s_), lst in sorted(by_spec.items()):
        m = len(lst)
        posv = np.array([x[0] for x in lst], dtype=np.int64)
        flv = np.array([x[2] for x in lst], dtype=np.int64)
        it = np.zeros((16, m), dtype=np.int64)
        em = np.zeros((16, m), dtype=np.int64)
        en = np.zeros((len(eslots), m), dtype=np.int64)
        bld = np.zeros(m, dtype=np.uint64)
        stv = np.zeros((len(STATS), m), dtype=np.int64)
        for j, (pos, k, fl) in enumerate(lst):
            m_ = meta_j.get(k)
            if fl & 1:
                glist = m_["gear"]
                for si, s in enumerate(slots):
                    itm = glist[s] if s < len(glist) else None
                    if not isinstance(itm, dict) or not itm.get("id"):
                        continue
                    iid = int(itm["id"])
                    it[si, j] = iid
                    bonus = itm.get("bonus") if isinstance(itm.get("bonus"), list) else []
                    e = emb_of(iid, bonus)
                    if e is None:
                        em[si, j] = 0
                    elif e == -1 or not embc["names"].get(e):
                        em[si, j] = 1
                    else:
                        em[si, j] = label_code.get(embc["names"][e], 1)
                for ei, s in enumerate(eslots):
                    itm = glist[s] if s < len(glist) else None
                    if isinstance(itm, dict) and itm.get("ench"):
                        en[ei, j] = int(itm["ench"])
            if fl & 2:
                bld[j] = build_hash64(m_["build"], m_.get("blob") or "")
            if fl & 4:
                stt = stats_j[k]["stats"]
                for si, nm in enumerate(STATS):
                    v = stt.get(nm)
                    stv[si, j] = 0 if v is None else min(max(int(round(v)), 0), 0xFFFF)
        cols_s = [pf.Column("pos", "u32", posv, p=True), pf.Column("fl", "u8", flv)]
        cols_s += [pf.Column(f"it{si}", "u32", it[si], p=True) for si in range(16)]
        cols_s += [pf.Column(f"em{si}", "u8", em[si]) for si in range(16)]
        cols_s += [pf.Column(f"en{ei}", "u16", en[ei], p=True, clamp=(0, 65535)) for ei in range(len(eslots))]
        # a 64-bit HASH, not a sum: written as the explicit lo/hi u32 pair
        # (the same bytes a u64 column lays out; the reader recombines it),
        # because the u64 writer guards the client-Number 2^53 limit and a
        # client keys builds by the (hi, lo) pair / BigInt, never a Number
        cols_s += [pf.Column("bld_lo", "u32", (bld & np.uint64(0xFFFFFFFF)).astype(np.int64), p=True),
                   pf.Column("bld_hi", "u32", (bld >> np.uint64(32)).astype(np.int64), p=True)]
        cols_s += [pf.Column(f"st{si}", "u16", stv[si], p=True) for si in range(len(STATS))]
        cn, sn = S.vocab["classes"][c_], S.vocab["specs"][s_]
        hdr = {"spec": f"{cn}|{sn}", "spec_code": c_ * 100 + s_, "cls": c_, "spec_index": s_,
               "day": day if day >= 0 else "undated", "rows_sha": rows_sha, "m": m, "slots": slots,
               "eslots": eslots, "stats": STATS}
        sub = pc.spec_dir_name(cn, sn)
        ws = pf.write(B.P.out / "spec" / sub, day_key, "shard", S.slug, m, cols_s, hdr)
        B.health_lines.extend(pf.clamp_health_lines(f"{sub}/{ws.name}", ws.clamped))
        if len(ws.gz) > BLOCK_TRIPWIRE:
            B.health(f"parts.block_over_1mb={sub}/{ws.name}:{len(ws.gz)}")
        specs_out[str(c_ * 100 + s_)] = f"spec/{sub}/{ws.name}"
        byts["spec"] += len(ws.gz)
    # ---- the window partials (§6.2-3): what spec/vocab and specstats need
    # from this day, so the window stage merges ≤ 24 small tables instead of
    # walking a million rows through the legacy sidecar code every run
    seq_arr = df["seq"].astype(np.int64).to_numpy()
    day_partials(B, day, df, covered, meta_j, stats_j, gear, cls, spec, char, seq_arr, st_ms, timed, key,
                 emb_of, slots)
    # ---- thin.npz (the cube partial, §3.2) and keys.npz
    reg_names = df["region"].tolist()
    W_true = np.where(st_ms >= 0, S.week_of_ms(np.where(st_ms >= 0, st_ms, 0), reg_names), -10 ** 6)
    # §3.1 identity with computeResetBuckets: a row at or after the current
    # reset instant is bucket 0, however far ahead its uploader's clock ran,
    # so W(row) is clamped to W(now, reg); the day remembers the clamp
    # (w_clamp) and is re-queued once that reset has passed (prepare_pins)
    cur_w_code = {int(c_): S.cur_week(S.vocab["regions"][int(c_)], B.now_ms) for c_ in np.unique(reg)}
    cur_w = np.array([cur_w_code[int(c_)] for c_ in reg], dtype=np.int64)
    W = np.where(W_true > -10 ** 6, np.minimum(W_true, cur_w), W_true)
    w_clamp = {}
    for c_ in np.unique(reg[W_true > cur_w]):
        w_clamp[S.vocab["regions"][int(c_)]] = cur_w_code[int(c_)]
    tb = np.where(tier < 0, -1, np.where(tier < 2, 0, np.where(tier < 4, 1, 2)))
    comp_sig = []
    roster_n = np.zeros(n_runs, dtype=np.int64)
    members: dict = collections.defaultdict(list)
    for i in range(len(df)):
        members[int(run[i])].append((int(cls[i]), int(spec[i]), int(role[i])))
    roles_v, classes_v, specs_v = S.vocab["roles"], S.vocab["classes"], S.vocab["specs"]
    for r in range(n_runs):
        mem = members[r]
        roster_n[r] = len(mem)
        mem = sc.js_sort(mem, lambda a, b: (ROLE_RANK.get(roles_v[a[2]], 3) - ROLE_RANK.get(roles_v[b[2]], 3))
                         or sc.locale_cmp(classes_v[a[0]], classes_v[b[0]]) or sc.locale_cmp(specs_v[a[1]], specs_v[b[1]]))
        comp_sig.append(",".join(str(c[0] * 100 + c[1]) for c in mem))
    thin = {"reg": reg, "W": W, "cls": cls, "spec": spec, "hero": hero, "role": role, "dun": dun, "key": key,
            "timed": timed, "post": post, "tb": tb, "dps": dps_i, "deaths": deaths, "char": char, "run": run,
            "day": dcol, "r_kdur": r_kdur, "r_roster_n": roster_n, "r_comp": np.array(comp_sig),
            "r_reg": r_reg, "r_dun": r_dun, "r_key": r_key, "r_timed": r_timed, "r_post": r_post,
            "r_deaths": np.bincount(run, weights=deaths, minlength=n_runs).astype(np.int64),
            "r_day": dcol[first]}
    save_npz(B.P.day_dir(day) / "thin.npz", thin)
    save_npz(B.P.day_dir(day) / "keys.npz", {
        "code": df["report_code"].to_numpy().astype(str), "fid": df["fight_id"].astype(np.int64).to_numpy(),
        "character": df["character"].to_numpy().astype(str), "server": df["server"].to_numpy().astype(str),
        "started_at": st_ms, "char_id": char, "raw_idx": np.asarray(perm, dtype=np.int64)})
    # per-region [Wlo, Whi] and the weeks this day touches
    wmap = {}
    weeks = set()
    for r_, w_ in zip(reg, W):
        if w_ <= -10 ** 6:
            continue
        rn = S.vocab["regions"][int(r_)]
        lo, hi = wmap.get(rn, (int(w_), int(w_)))
        wmap[rn] = (min(lo, int(w_)), max(hi, int(w_)))
        weeks.add(int(w_))
    for w_ in weeks:
        we = B.st.d["weeks"].setdefault(str(w_), {"published": False, "days": []})
        if day not in we["days"]:
            we["days"].append(day)
            we["days"].sort()
    return {"n": len(df), "runs": n_runs, "rows_sha": rows_sha, "inputs_sha": inputs_sha, "f": rows_rel,
            "b": len(w.gz), "specs": specs_out, "w": {k: list(v) for k, v in sorted(wmap.items())},
            "w_clamp": w_clamp, "weeks": sorted(weeks), "bytes": byts, "hero_recovered": hero_recovered}


# ================================================= per-day window partials
EMB_NONE = -2          # emb identity codes in the pair tables: None / generic / id
PAIR_KEY_MUL = 1 << 21  # emb ids and item ids fit 21 bits each in the combined key


def pair_key(spec_code, slot, iid, emb):
    """One int64 per (spec, slot, item id, emb identity)."""
    spec_code = np.asarray(spec_code, dtype=np.int64)
    slot = np.asarray(slot, dtype=np.int64)
    iid = np.asarray(iid, dtype=np.int64)
    emb = np.asarray(emb, dtype=np.int64) + 2          # -2..  -> 0..
    return ((spec_code * 16 + slot) * PAIR_KEY_MUL + iid) * PAIR_KEY_MUL + emb


def unpair_key(k):
    k = np.array(k, dtype=np.int64, copy=True)      # never the caller's array: //= below is in place
    emb = k % PAIR_KEY_MUL - 2
    k //= PAIR_KEY_MUL
    iid = k % PAIR_KEY_MUL
    k //= PAIR_KEY_MUL
    return k // 16, k % 16, iid, emb


def day_partials(B: Builder, day: int, df, covered: list, meta_j: dict, stats_j: dict, gear: dict,
                 cls, spec, char, seq_arr, st_ms, timed, key, emb_of, slots) -> None:
    """vocab.npz / traits.json.gz / stats.npz for one day, in the day's
    ARRIVAL order (seq) where legacy's first-observation rules depend on it."""
    dd = B.P.day_dir(day)
    sk_row = (np.asarray(cls, dtype=np.int64) * 100 + np.asarray(spec, dtype=np.int64))
    p_spec, p_slot, p_iid, p_emb, p_ilvl, p_who, p_seq = [], [], [], [], [], [], []
    en_spec, en_slot, en_id = [], [], []
    ench_hits: collections.Counter = collections.Counter()
    gear_known = 0
    bld: collections.Counter = collections.Counter()
    for pos, k, fl in covered:
        m_ = meta_j.get(k)
        sk = int(sk_row[pos])
        if fl & 1:
            gear_known += 1
            glist = m_["gear"]
            for si, s in enumerate(slots):
                itm = glist[s] if s < len(glist) else None
                if not isinstance(itm, dict) or not itm.get("id"):
                    continue
                iid = int(itm["id"])
                bonus = itm.get("bonus") if isinstance(itm.get("bonus"), list) else []
                e = emb_of(iid, bonus)
                v = itm.get("ilvl")
                p_spec.append(sk)
                p_slot.append(si)
                p_iid.append(iid)
                p_emb.append(EMB_NONE if e is None else int(e))
                p_ilvl.append(int(v) if v else 0)
                p_who.append(int(char[pos]))
                p_seq.append(int(seq_arr[pos]))
            for s, itm in enumerate(glist):
                if isinstance(itm, dict) and itm.get("ench"):
                    ench_hits[s] += 1
                    en_spec.append(sk)
                    en_slot.append(s)
                    en_id.append(int(itm["ench"]))
        if fl & 2:
            bld[(sk, m_["build"])] += 1
    key_all = pair_key(p_spec, p_slot, p_iid, p_emb) if p_spec else np.zeros(0, dtype=np.int64)
    # counts per entry over every (row, slot) occurrence
    c_key, c_n = np.unique(key_all, return_counts=True)
    # wearer tables: (entry, who) once per day -- first seq with an item
    # level > 0 (legacy's ilvl observation), and any-row presence for `w`
    who = np.asarray(p_who, dtype=np.int64)
    seqs = np.asarray(p_seq, dtype=np.int64)
    ilv = np.asarray(p_ilvl, dtype=np.int64)
    if len(key_all):
        order = np.lexsort([seqs, who, key_all])
        kk, ww = key_all[order], who[order]
        first = np.concatenate([[True], (kk[1:] != kk[:-1]) | (ww[1:] != ww[:-1])])
        w_key, w_who = kk[first], ww[first]
        m = ilv > 0
        order2 = np.lexsort([seqs[m], who[m], key_all[m]])
        k2, w2, s2, i2 = key_all[m][order2], who[m][order2], seqs[m][order2], ilv[m][order2]
        first2 = np.concatenate([[True], (k2[1:] != k2[:-1]) | (w2[1:] != w2[:-1])]) if len(k2) else np.zeros(0, dtype=bool)
        i_key, i_who, i_seq, i_ilvl = k2[first2], w2[first2], s2[first2], i2[first2]
    else:
        w_key = w_who = i_key = i_who = i_seq = i_ilvl = np.zeros(0, dtype=np.int64)
    en_key = pair_key(en_spec, en_slot, en_id, np.full(len(en_id), EMB_NONE)) if en_id else np.zeros(0, dtype=np.int64)
    e_key, e_n = np.unique(en_key, return_counts=True)
    hit_slot = np.array(sorted(ench_hits), dtype=np.int64)
    hit_n = np.array([ench_hits[s] for s in hit_slot], dtype=np.int64)
    b_items = sorted(bld.items())
    save_npz(dd / "vocab.npz", {
        "c_key": c_key.astype(np.int64), "c_n": c_n.astype(np.int64),
        "w_key": w_key.astype(np.int64), "w_who": w_who.astype(np.uint32),
        "i_key": i_key.astype(np.int64), "i_who": i_who.astype(np.uint32), "i_seq": i_seq.astype(np.int64),
        "i_ilvl": i_ilvl.astype(np.uint16),
        "e_key": e_key.astype(np.int64), "e_n": e_n.astype(np.int64), "hit_slot": hit_slot, "hit_n": hit_n,
        "gear_known": np.array([gear_known], dtype=np.int64),
        "b_spec": np.array([k[0] for k, _ in b_items], dtype=np.int64),
        "b_build": np.array([k[1] for k, _ in b_items] or [""]),
        "b_n": np.array([n for _, n in b_items], dtype=np.int64)})
    # traits over EVERY gear record of the day (the legacy trait material)
    traits: dict = {}
    gs = gear["gseq"] if "gseq" in gear else np.zeros(len(gear["code"]), dtype=np.int64)
    for i in range(len(gear["code"])):
        sk = f"{gear['cls'][i]}|{gear['spec'][i]}"
        o = traits.setdefault(sk, {"specid": collections.Counter(), "entries": set(), "sel": {}})
        if gear["specid"][i]:
            o["specid"][int(gear["specid"][i])] += 1
        bl = str(gear["blob"][i])
        bd = str(gear["build"][i])
        if bl:
            for part in bl.split("|"):
                o["entries"].add(int(part.split(":", 1)[0]))
            e = o["sel"].setdefault(bd, {}).get(bl)
            if e is None:
                o["sel"][bd][bl] = [1, int(gs[i])]
            else:
                e[0] += 1
                e[1] = min(e[1], int(gs[i]))
    tdoc = {sk: {"specid": {str(k): v for k, v in sorted(o["specid"].items())},
                 "entries": sorted(o["entries"]),
                 "sel": {b: dict(sorted(c.items())) for b, c in sorted(o["sel"].items())}}
            for sk, o in sorted(traits.items())}
    write_atomic(dd / "traits.json.gz", gz_json(tdoc))
    # specstats: the cohort-eligible rows (timed, key >= floor, dated);
    # the window cut and the latest-per-character rule are applied at merge
    key_arr = np.asarray(key, dtype=np.int64)
    elig = (np.asarray(timed) == 1) & (key_arr >= bsd.SPECSTATS_MIN_KEY) & (np.asarray(st_ms) >= 0)
    ss_all_t = np.asarray(st_ms, dtype=np.int64)[elig]
    rows_idx, vals = [], []
    for pos, k, fl in covered:
        if fl & 4 and elig[pos]:
            rows_idx.append(pos)
            stt = stats_j[k]["stats"]
            vals.append([float(stt[nm]) if nm in stt else np.nan for nm in STATS])
    ri = np.array(rows_idx, dtype=np.int64)
    save_npz(dd / "stats.npz", {
        "all_t": ss_all_t,
        "t": np.asarray(st_ms, dtype=np.int64)[ri], "seq": seq_arr[ri], "cls": np.asarray(cls)[ri].astype(np.int64),
        "spec": np.asarray(spec)[ri].astype(np.int64), "char": np.asarray(char)[ri].astype(np.int64),
        "stats": np.array(vals, dtype=np.float64).reshape(len(ri), len(STATS))})


def js_round_half_even(x: float) -> int:
    return int(round(x))


def vocab_and_specstats(B: Builder, listed: list) -> tuple[dict | None, dict | None]:
    """spec/vocab (§4.3) and meta/specstats from the merged per-day
    partials: the legacy builds_sidecar()/spec_stats_block() documents,
    entry for entry, over the listed days."""
    S, P = B.season, B.P
    _items, _enchs, crafted, embc, markers, icon_names = B.emb_cfg
    emb_names = embc["names"]
    item_names, ench_names = _items, _enchs
    # ---- merge the count tables
    c_keys, c_ns, e_keys, e_ns, hits = [], [], [], [], collections.Counter()
    gear_known = 0
    bld: collections.Counter = collections.Counter()
    days_v = []
    for d in listed:
        v = load_npz(P.day_dir(d) / "vocab.npz")
        if v is None:
            continue
        days_v.append(d)
        c_keys.append(v["c_key"])
        c_ns.append(v["c_n"])
        e_keys.append(v["e_key"])
        e_ns.append(v["e_n"])
        for s_, n_ in zip(v["hit_slot"], v["hit_n"]):
            hits[int(s_)] += int(n_)
        gear_known += int(v["gear_known"][0])
        for sp_, b_, n_ in zip(v["b_spec"], v["b_build"], v["b_n"]):
            if str(b_):
                bld[(int(sp_), str(b_))] += int(n_)
    if not days_v or not sum(len(k) for k in c_keys):
        return None, None

    def merge_counts(keys, ns):
        if not keys:
            return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
        k = np.concatenate(keys)
        n = np.concatenate(ns)
        u, inv = np.unique(k, return_inverse=True)
        return u, np.bincount(inv, weights=n).astype(np.int64)
    ck, cn = merge_counts(c_keys, c_ns)
    ek, en_n = merge_counts(e_keys, e_ns)
    spec_c, slot_c, iid_c, emb_c = unpair_key(ck)
    # eslots are MEASURED over the window (>= 1% of gear-known rows), then
    # intersected with the pinned list (a slot the blocks do not carry
    # cannot be displayed)
    eslots_m = sorted(s for s, c in hits.items() if c >= bsd.BUILDS_ESLOT_MIN_SHARE * max(gear_known, 1))
    pinned_es = [int(x) for x in (B.pins.doc.get("eslots") or DEFAULT_ESLOTS)]
    eslots = [s for s in eslots_m if s in pinned_es]
    # ---- the ranking per (spec, slot): (-n, id, emb or 0), capped
    embkey = np.where(emb_c == EMB_NONE, 0, emb_c)
    cdf = pd.DataFrame({"sk": spec_c, "k": slot_c, "iid": iid_c, "emb": emb_c, "ek": embkey, "n": cn, "key": ck})
    cdf = cdf.sort_values(["sk", "k", "n", "iid", "ek"], ascending=[True, True, False, True, True], kind="stable")
    caps = np.where(np.isin(np.array([bsd.BUILDS_SLOTS[k] for k in cdf["k"]]), list(bsd.BUILDS_BIG_SLOTS)),
                    bsd.BUILDS_ITEM_CAP_BIG, bsd.BUILDS_ITEM_CAP)
    rank = cdf.groupby(["sk", "k"], sort=False).cumcount().to_numpy()
    top = cdf[rank < caps]
    top_keys = top["key"].to_numpy()
    # the iup gate over the FULL tallies: (id, emitted emb label) unique per (spec, slot)
    labels = np.array([None if e == EMB_NONE else (emb_names.get(int(e)) or "embellished") for e in emb_c], dtype=object)
    gate = pd.DataFrame({"sk": spec_c, "k": slot_c, "iid": iid_c, "lab": labels})
    iup_ok = not gate.duplicated(["sk", "k", "iid", "lab"]).any()
    # ---- wearer tables for the top entries: first ilvl observation per
    # (entry, wearer) in arrival order across days; distinct wearers for w
    i_parts, w_parts = [], []
    for d in days_v:
        v = load_npz(P.day_dir(d) / "vocab.npz")
        m = np.isin(v["i_key"], top_keys)
        i_parts.append((v["i_key"][m], v["i_who"][m].astype(np.int64), v["i_seq"][m], v["i_ilvl"][m].astype(np.int64)))
        m2 = np.isin(v["w_key"], top_keys)
        w_parts.append((v["w_key"][m2], v["w_who"][m2].astype(np.int64)))
    ik = np.concatenate([p[0] for p in i_parts]) if i_parts else np.zeros(0, dtype=np.int64)
    iw = np.concatenate([p[1] for p in i_parts]) if i_parts else np.zeros(0, dtype=np.int64)
    isq = np.concatenate([p[2] for p in i_parts]) if i_parts else np.zeros(0, dtype=np.int64)
    iil = np.concatenate([p[3] for p in i_parts]) if i_parts else np.zeros(0, dtype=np.int64)
    ilvls_of: dict = {}
    if len(ik):
        o = np.lexsort([isq, iw, ik])
        ik, iw, iil = ik[o], iw[o], iil[o]
        first = np.concatenate([[True], (ik[1:] != ik[:-1]) | (iw[1:] != iw[:-1])])
        ik, iil = ik[first], iil[first]
        starts = np.concatenate([[0], np.nonzero(ik[1:] != ik[:-1])[0] + 1])
        ends = np.concatenate([starts[1:], [len(ik)]])
        for a, b in zip(starts, ends):
            ilvls_of[int(ik[a])] = iil[a:b]
    wk = np.concatenate([p[0] for p in w_parts]) if w_parts else np.zeros(0, dtype=np.int64)
    wwho = np.concatenate([p[1] for p in w_parts]) if w_parts else np.zeros(0, dtype=np.int64)
    w_of: dict = {}
    if len(wk):
        pairs = np.unique(wk * (1 << 32) + wwho)
        u, c = np.unique(pairs // (1 << 32), return_counts=True)
        w_of = dict(zip(u.tolist(), c.tolist()))
    # ---- traits (sel) for the wanted builds
    traits: dict = {}
    sel_acc: dict = {}                # (sk, build) -> {blob: [count, first gseq]}
    for d in days_v:
        p = P.day_dir(d) / "traits.json.gz"
        if not p.exists():
            continue
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            tdoc = json.load(fh)
        for sk, o in tdoc.items():
            t = traits.setdefault(sk, {"specid": collections.Counter(), "entries": set(), "sel": {}})
            for k_, v_ in o["specid"].items():
                t["specid"][int(k_)] += int(v_)
            t["entries"].update(o["entries"])
            for b_, cnt in o["sel"].items():
                acc = sel_acc.setdefault((sk, b_), {})
                for blob, v_ in cnt.items():
                    n_, g_ = (int(v_[0]), int(v_[1])) if isinstance(v_, list) else (int(v_), 0)
                    e = acc.get(blob)
                    if e is None:
                        acc[blob] = [n_, g_]
                    else:
                        e[0] += n_
                        e[1] = min(e[1], g_)
    # Counters inserted in journal order, so most_common() breaks a tie
    # between blob variants exactly as legacy's whole-journal walk does
    for (sk, b_), acc in sel_acc.items():
        c_ = traits[sk]["sel"].setdefault(b_, collections.Counter())
        for blob, (n_, _g) in sorted(acc.items(), key=lambda kv: kv[1][1]):
            c_[blob] += n_
    names = {code: f"{S.vocab['classes'][code // 100]}|{S.vocab['specs'][code % 100]}"
             for code in set(int(x) for x in spec_c) | {k[0] for k in bld}}
    b_by: dict = collections.defaultdict(list)
    for (sp_, b_), n_ in bld.items():
        b_by[sp_].append((b_, n_))
    b_ranked = {sp_: sorted(lst, key=lambda kv: (-kv[1], kv[0]))[:bsd.BUILDS_BUILD_CAP] for sp_, lst in b_by.items()}
    wanted = {names[sp_]: {s for s, _ in lst} for sp_, lst in b_ranked.items() if lst}
    usage = bsd._trait_journal_pass(wanted, traits) if wanted else {}
    geo, _ = bsd._trait_caches()
    sel_by: dict = {}
    geo_entries = geo.get("entries", {}) if geo else {}
    if geo_entries:
        geo_node_entries = bsd._node_entries(geo_entries)
        for sk, o in usage.items():
            for build, blobs in o["sel"].items():
                pairs_ = bsd._sel_pairs(blobs.most_common(1)[0][0], geo_entries, geo_node_entries)
                if pairs_:
                    sel_by[(sk, build)] = pairs_
    blob_of = {(sk, b): c.most_common(1)[0][0] for sk, o in traits.items() for b, c in o["sel"].items() if c}
    # ---- the document
    specs_v: dict = {}
    top_by = {}
    for sk_, k_, iid_, emb_, n_, key_ in zip(top["sk"], top["k"], top["iid"], top["emb"], top["n"], top["key"]):
        top_by.setdefault(int(sk_), {}).setdefault(int(k_), []).append((int(iid_), int(emb_), int(n_), int(key_)))
    en_spec_, en_slot_, en_id_, _ = unpair_key(ek)
    en_by: dict = {}
    for sp_, s_, eid_, n_ in zip(en_spec_, en_slot_, en_id_, en_n):
        en_by.setdefault(int(sp_), {}).setdefault(int(s_), []).append((int(eid_), int(n_)))
    keep_idx = [i for i, s in enumerate(eslots_m) if s in pinned_es]
    for code in sorted(set(top_by) | set(b_ranked), key=lambda c: names[c]):
        sk = names[code]
        items_v = []
        for k_ in range(len(bsd.BUILDS_SLOTS)):
            col = []
            for iid_, emb_, cnt_, key_ in top_by.get(code, {}).get(k_, []):
                il = ilvls_of.get(key_)
                il_list = il.tolist() if il is not None else []
                e: dict = {"id": iid_, "n": (item_names.get(iid_) or {}).get("n"),
                           "ilvl": (int(round(float(np.median(il_list)))) if il_list else None)}
                if len(il_list) >= bsd.BUILDS_IUP_MIN_WEARERS and iup_ok:
                    cnts = collections.Counter(il_list)
                    tp = max(cnts.values())
                    mode = max(v for v, c in cnts.items() if c == tp)
                    e["iup"] = int(round(100.0 * sum(1 for v in il_list if v > mode) / len(il_list)))
                ic = icon_names.get(iid_)
                if ic:
                    e["ic"] = ic
                if iid_ in crafted:
                    e["cr"] = 1
                if emb_ != EMB_NONE:
                    e["emb"] = emb_names.get(emb_) or "embellished"
                e["w"] = int(w_of.get(key_, 0))
                col.append(e)
            items_v.append(col)
        en_v = []
        for s_ in eslots_m:
            ranked = sorted(en_by.get(code, {}).get(s_, []), key=lambda kv: (-kv[1], kv[0]))[:bsd.BUILDS_ENCH_CAP]
            en_v.append([{"id": eid_, "n": ench_names.get(eid_)} for eid_, _ in ranked])
        entry = {"items": items_v, "ench": [en_v[i] for i in keep_idx]}
        b_out = []
        for s_, c_ in b_ranked.get(code, []):
            b = {"s": s_, "n": c_}
            sel = sel_by.get((sk, s_))
            if sel:
                b["sel"] = sel
            b["h"] = "%016x" % build_hash64(s_, blob_of.get((sk, s_), ""))
            b_out.append(b)
        entry["builds"] = b_out
        if b_ranked.get(code):
            entry["bkind"] = "hash" if all(s_.startswith("t:") for s_, _ in b_ranked[code]) else "string"
        specs_v[sk] = entry
    vocab_doc = {"v": 1, "slots": list(bsd.BUILDS_SLOTS), "eslots": eslots, "specs": specs_v}
    B.log(f"vocab: {len(specs_v)} specs, {len(top)} entries over {len(days_v)} days, eslots {eslots} "
          f"(measured {eslots_m}), iup {'on' if iup_ok else 'OFF'}")
    # ---- specstats (spec_stats_block over the window, from the partials)
    block = None
    all_t, parts = [], []
    for d in listed:
        s = load_npz(P.day_dir(d) / "stats.npz")
        if s is None:
            continue
        all_t.append(s["all_t"])
        parts.append(s)
    if parts:
        at = np.concatenate(all_t)
        if len(at):
            cutoff = int(at.max()) - bsd.SPECSTATS_WINDOW_DAYS * DAY_MS
            n_cohort = int((at >= cutoff).sum())
            t = np.concatenate([p["t"] for p in parts])
            seqv = np.concatenate([p["seq"] for p in parts])
            clsv = np.concatenate([p["cls"] for p in parts])
            specv = np.concatenate([p["spec"] for p in parts])
            chv = np.concatenate([p["char"] for p in parts])
            stv = np.concatenate([p["stats"] for p in parts]) if parts else np.zeros((0, len(STATS)))
            m = t >= cutoff
            n_hit = int(m.sum())
            if n_hit:
                t, seqv, clsv, specv, chv, stv = t[m], seqv[m], clsv[m], specv[m], chv[m], stv[m]
                # latest per character (ties: the first in arrival order, like `t > prev`)
                o = np.lexsort([seqv, -t, chv, specv, clsv])
                ck_ = np.stack([clsv[o], specv[o], chv[o]], axis=1)
                first = np.concatenate([[True], np.any(ck_[1:] != ck_[:-1], axis=1)])
                sel_i = o[first]
                spec_out: dict = {}
                groups = collections.defaultdict(list)
                for i in sel_i:
                    groups[(int(clsv[i]), int(specv[i]))].append(i)
                for (c_, s_) in sorted(groups, key=lambda cs: (S.vocab["classes"][cs[0]], S.vocab["specs"][cs[1]])):
                    idx = groups[(c_, s_)]
                    n = len(idx)
                    if n < bsd.SPECSTATS_MIN_CHARS:
                        continue
                    rows = stv[idx]
                    stats = list(bsd.SPECSTATS_CORE) + [
                        s2 for s2 in bsd.SPECSTATS_EXTRA
                        if int((~np.isnan(rows[:, STATS.index(s2)])).sum()) >= bsd.SPECSTATS_EXTRA_MIN_SHARE * n]
                    q = {}
                    for s2 in stats:
                        vals = rows[:, STATS.index(s2)]
                        vals = vals[~np.isnan(vals)].tolist()
                        if vals:
                            q[s2] = [int(round(v)) for v in np.percentile(vals, (25, 50, 75))]
                    spec_out[f"{S.vocab['classes'][c_]}|{S.vocab['specs'][s_]}"] = {"n": n, "q": q}
                if spec_out:
                    cohort = (f"timed +{bsd.SPECSTATS_MIN_KEY}s and higher from the last "
                              f"{bsd.SPECSTATS_WINDOW_DAYS} days of data; one record per "
                              f"character (their latest parse); stats known for {n_hit:,} of "
                              f"{n_cohort:,} parses ({n_hit / n_cohort:.0%}); values are "
                              f"stat ratings as the character sheet read at the pull — "
                              f"active consumables (flask, food) included; not percentages")
                    block = {"cohort": cohort, "keyMin": bsd.SPECSTATS_MIN_KEY,
                             "windowDays": bsd.SPECSTATS_WINDOW_DAYS, "specs": spec_out}
    return vocab_doc, block


# ============================================================ window stage
def window_stage(B: Builder, listed: list, rio_sha: str) -> dict:
    """§6.2-3: spec/vocab, specstats, charscore base+delta, window.refchars /
    keys / counts, per-week counts -- numpy over the listed days' caches."""
    S = B.season
    P = B.P
    art: dict = {}
    t0 = time.perf_counter()
    # ---- the client's window columns (what refChars scans)
    cols: dict = collections.defaultdict(list)
    keys_frames = []
    rbase = 0
    for d in listed:
        c = B.load_rows(d)
        ex = pc.expand_day(c)
        ex["run"] = ex["run"] + rbase
        rbase += int(c.header["runs"])
        for k, v in ex.items():
            cols[k].append(v)
        kz = load_npz(P.day_dir(d) / "keys.npz")
        keys_frames.append(kz)
    R = {k: np.concatenate(v) for k, v in cols.items()} if cols else {}
    if "tmul" in R and len(cols["tmul"]) != len(listed):
        R.pop("tmul")
    n_rows = int(len(R["dps"])) if R else 0
    B._stage("window_load", t0)
    t0 = time.perf_counter()
    # ---- charscore (kind pairs): every rated character with a registry id
    rio = read_rio(P.rio)
    pairs = []
    for name, v in rio.items():
        cid = B.reg.lookup(name)
        if cid is not None:
            pairs.append((cid, int(round(v))))
    pairs.sort()
    ch = np.array([p[0] for p in pairs], dtype=np.int64)
    scv = np.array([min(max(p[1], 0), 65535) for p in pairs], dtype=np.int64)
    cs_state = B.st.d.get("charscore") or {}
    base_name = cs_state.get("base_f")
    base_at = cs_state.get("base_at")
    need_base = (not base_name) or not (P.out / base_name).exists() or B.daily or \
        (base_at and B.now_ms - sc.parse_iso_ms(base_at) >= DAILY_SLOT_H * 3_600_000)
    if need_base:
        wb = pf.write(P.out / "meta", "charscore", "pairs", S.slug, len(ch),
                      [pf.Column("char", "u32", ch, p=True, d=True), pf.Column("score", "u16", scv)], {})
        base_name = f"meta/{wb.name}"
        cs_state = {"base_f": base_name, "base_at": iso(B.now_ms), "base_pairs": len(ch)}
        base_map = dict(zip(ch.tolist(), scv.tolist()))
    else:
        cb = pf.read(P.out / base_name, expect_kind="pairs")
        base_map = dict(zip(cb["char"].astype(np.int64).tolist(), cb["score"].astype(np.int64).tolist()))
    delta = [(c_, s_) for c_, s_ in zip(ch.tolist(), scv.tolist()) if base_map.get(c_) != s_]
    dch = np.array([x[0] for x in delta], dtype=np.int64)
    dsc = np.array([x[1] for x in delta], dtype=np.int64)
    wd = pf.write(P.out / "meta", "charscore.delta", "pairs", S.slug, len(dch),
                  [pf.Column("char", "u32", dch, p=True, d=True), pf.Column("score", "u16", dsc)], {})
    art["charscore"] = {"f": base_name, "pairs": int(cs_state.get("base_pairs", len(base_map))),
                        "delta": {"f": f"meta/{wd.name}", "pairs": int(len(dch))}}
    B.st.d["charscore"] = cs_state
    charscore_arr = np.full(B.reg.total + 1, -1, dtype=np.int64)
    if len(ch):
        charscore_arr[ch] = scv
    B._stage("charscore", t0)
    t0 = time.perf_counter()
    # ---- the sitecalc Site: refchars for all 24 keys, keys, counts
    pars = [int((B.pins.doc.get("pars") or {}).get(dn, 0) or 0) for dn in S.vocab["dungeons"]]
    tuning = {"label": B.patch.get("label")} if B.patch else None
    win = {"day_from": B.window_from(), "day_to": (B.now_ms - S.epoch_ms) // DAY_MS,
           "rows": n_rows, "runs": int(rbase), "keys": [], "refchars": {},
           "has_tier": False, "has_timed": False, "post_rows": 0, "newest_row": None}
    if n_rows:
        D = {"epoch": S.epoch, "classes": S.vocab["classes"], "specs": S.vocab["specs"],
             "heroes": S.vocab["heroes"], "dungeons": S.vocab["dungeons"], "regions": S.vocab["regions"],
             "roles": S.vocab["roles"], "spec_role": S.spec_role, "pars": pars, "tuning": tuning,
             "projection": None, "charscore": []}
        site = sc.init_data(D, B.now_ms, R=R)
        site.stamp = sc.Stamp(max(B.reg.total, int(R["char"].max())))
        win["refchars"] = sc.all_ref_chars(site)
        win["keys"] = sorted(int(k) for k in np.unique(R["key"]))
        win["has_tier"] = bool((R["tier"] >= 0).any())
        win["has_timed"] = bool((R["timed"] >= 0).any())
        win["post_rows"] = int((R["post"] == 1).sum())
        newest = -1
        for kz in keys_frames:
            if kz is not None and len(kz["started_at"]):
                sa = kz["started_at"]
                sa = sa[sa <= B.now_ms]
                if len(sa):
                    newest = max(newest, int(sa.max()))
        win["newest_row"] = iso(newest) if newest >= 0 else None
        win["day_to"] = max(win["day_to"], max(listed))
    art["window"] = win
    # projection (§3.3): a single-generation, row-window feature. The column
    # is present on every listed day iff the rule tables define a projection;
    # the manifest's object is legacy's (tuning_multipliers' meta) plus the
    # generation's rules_sha, and null when nothing is covered (legacy hides
    # the toggle rather than shipping a column of 1.0s).
    art["projection"] = None
    if n_rows and "tmul" in R and pt.RULES:
        tm = R["tmul"]
        covered = int(((tm != 10000) & (tm != 0)).sum())
        if covered:
            art["projection"] = {
                "label": pt.PROJECTION_LABEL, "url": getattr(pt, "PROJECTION_URL", None),
                "date": pt.PROJECTION_DATE, "parses": covered, "unprojectable": int((tm == 0).sum()),
                # legacy counts the recovered heroes of its whole frame (the
                # season's CSV): every day with rows, cubed or listed
                "hero_recovered": int(sum(int(e.get("hero_recovered") or 0) for e in B.st.d["days"].values()
                                          if e.get("n") and e.get("f"))),
                "specs": sorted(pt.RULES),
                "exact": sorted(s for s, r in pt.RULES.items()
                                if not r.get("set_bonus") and not r.get("share_scale") and not r.get("caveats")),
                "caveats": {s: r["caveats"] for s, r in pt.RULES.items() if r.get("caveats")},
                "rules_sha": B.rules_sha}
    B.st.d["projection"] = {"rules_sha": B.rules_sha, "has_tmul": bool(n_rows and "tmul" in R),
                            "meta": art["projection"]}
    B._stage("refchars", t0)
    t0 = time.perf_counter()
    # ---- per-week counts (weekCounts / availWeeks / weekTitle inputs)
    weeks_touch = set(B.touched_weeks)
    for w_, we in B.st.d["weeks"].items():
        if not we.get("reg"):
            weeks_touch.add(int(w_))
    for w_ in sorted(weeks_touch):
        we = B.st.d["weeks"].setdefault(str(w_), {"published": False, "days": []})
        acc: dict = {}
        for d in we.get("days", []):
            th = load_npz(P.day_dir(d) / "thin.npz")
            if th is None:
                continue
            m = th["W"] == w_
            if not m.any():
                continue
            for ri in np.unique(th["reg"][m]):
                mm = m & (th["reg"] == ri)
                rn = S.vocab["regions"][int(ri)]
                a = acc.setdefault(rn, {"n": 0, "runs": 0, "chars": set(), "dmin": 10 ** 9, "dmax": -1})
                a["n"] += int(mm.sum())
                a["runs"] += len(np.unique(th["run"][mm]))
                a["chars"].update(th["char"][mm].tolist())
                a["dmin"] = min(a["dmin"], int(th["day"][mm].min()))
                a["dmax"] = max(a["dmax"], int(th["day"][mm].max()))
        we["reg"] = {rn: {"n": a["n"], "runs": a["runs"], "chars": len(a["chars"]), "dmin": a["dmin"],
                          "dmax": a["dmax"]} for rn, a in sorted(acc.items())}
        we["days"] = [d for d in we.get("days", []) if (P.day_dir(d) / "thin.npz").exists()]
    B._stage("weeks", t0)
    t0 = time.perf_counter()
    # ---- pars for un-pinned dungeons (§5): derive_pars over the window
    # once a dungeon has >= 500 clocked runs with both outcomes
    pins_pars = B.pins.doc.setdefault("pars", {})
    missing = [dn for dn in S.vocab["dungeons"] if dn != "Unknown" and not pins_pars.get(dn)]
    if missing:
        frames = []
        for d in listed:
            raw = load_npz(P.day_dir(d) / "raw.npz")
            if raw is None or not len(raw["report_code"]):
                continue
            m = np.isin(raw["dungeon"], missing)
            if m.any():
                frames.append(pd.DataFrame({
                    "report_code": raw["report_code"][m], "fight_id": raw["fight_id"][m],
                    "dungeon": raw["dungeon"][m], "medal": raw["medal_ov"][m], "keystone_s": raw["keystone_s"][m]}))
        sub = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
            columns=["report_code", "fight_id", "dungeon", "medal", "keystone_s"])
        if len(sub):
            per_run = sub.drop_duplicates(["report_code", "fight_id"])
            derived = dict(zip(missing, bsd.derive_pars(sub, missing)))
            for dn in missing:
                g = per_run[per_run["dungeon"] == dn]
                ks_ok = pd.to_numeric(g["keystone_s"], errors="coerce").notna()
                timed_ok = g["medal"].isin(["timed", "gold", "silver", "bronze"])
                if ks_ok.sum() >= PAR_MIN_RUNS and timed_ok[ks_ok].nunique() == 2 and derived.get(dn):
                    pins_pars[dn] = int(derived[dn])
                    B.pins.upgrade(f"pars.{dn}", None, int(derived[dn]), "derive")
            if B.pins.changed_keys:
                B.pins.save()
    # ---- spec/vocab (§4.3) + specstats from the per-day partials
    _items, _enchs, crafted, embc, markers, _icons = B.emb_cfg or bsd._name_caches()
    B.emb_cfg = B.emb_cfg or (_items, _enchs, crafted, embc, markers, _icons)
    art["emb"] = emb_labels(embc)
    vocab_doc, block = vocab_and_specstats(B, listed)
    if vocab_doc is not None:
        body = gz_json(vocab_doc)
        name, _ = hashed_write(P.out / "spec", "vocab", "json.gz", body)
        art["spec_vocab"] = {"f": f"spec/{name}", "b": len(body)}
    else:
        art["spec_vocab"] = None
    B._stage("vocab", t0)
    t0 = time.perf_counter()
    art["specstats"] = None
    if block:
        body = gz_json(block)
        name, _ = hashed_write(P.out / "meta", "specstats", "json.gz", body)
        art["specstats"] = {"f": f"meta/{name}", "b": len(body)}
    B._stage("specstats", t0)
    B.health(f"parts.bytes.rows={sum(B.st.d['days'][str(d)]['b'] for d in listed)}")
    B.health(f"parts.bytes.spec={sum((B.st.d['days'][str(d)].get('bytes') or {}).get('spec', 0) for d in listed)}")
    if art["spec_vocab"]:
        B.health(f"parts.bytes.vocab={art['spec_vocab']['b']}")
    return art


# ============================================================ cubes (§3.2)
THIN_ROW_KEYS = ("reg", "cls", "spec", "hero", "role", "dun", "key", "timed", "post", "tb",
                 "dps", "deaths", "char", "day")
THIN_RUN_KEYS = ("r_kdur", "r_comp", "r_reg", "r_dun", "r_key", "r_timed", "r_post", "r_deaths", "r_day")


def week_partials(B: Builder, w: int, days: list) -> tuple[dict, str]:
    """The W == w rows of the days' thin.npz partials concatenated in day
    order (run ids made week-global by a per-day offset), plus the per-run
    arrays of the runs those rows belong to, and the sha256 of the
    day-local partials that enters cube_sha."""
    cols: dict = collections.defaultdict(list)
    h = hashlib.sha256()
    run_off = 0
    for d in sorted(days):
        th = load_npz(B.P.day_dir(d) / "thin.npz")
        if th is None:
            continue
        m = th["W"] == w
        n_runs_day = len(th["r_kdur"])
        if not m.any():
            run_off += n_runs_day
            continue
        rmask = np.zeros(n_runs_day, dtype=bool)
        rmask[np.unique(th["run"][m])] = True
        part = {k: th[k][m] for k in THIN_ROW_KEYS}
        part["run"] = th["run"][m]
        for k in THIN_RUN_KEYS:
            part[k] = th[k][rmask]
        h.update(f"d{d}:".encode())
        h.update(arrays_digest(part).encode())
        for k, v in part.items():
            cols[k].append(v if k != "run" else v + run_off)
        cols["r_id"].append(np.nonzero(rmask)[0] + run_off)
        run_off += n_runs_day
    thin = {k: np.concatenate(v) for k, v in cols.items()}
    return thin, h.hexdigest()


def emit_cube(B: Builder, w: int, T: dict, cube_sha: str) -> tuple[dict, dict]:
    """Write w<W>.{cells,dist,chars,comps} from the week's partials; the
    tables are exactly sitecalc.cube_from_rows()'s (the reference
    definition), computed vectorised. Returns (files, bytes)."""
    S = B.season
    n = len(T["dps"])
    order = np.lexsort([T["dps"]] + [T[d] for d in reversed(CELL_DIMS)])
    keys = np.stack([T[d][order].astype(np.int64) for d in CELL_DIMS], axis=1)
    change = np.any(keys[1:] != keys[:-1], axis=1)
    starts = np.concatenate([[0], np.nonzero(change)[0] + 1]).astype(np.int64)
    ends = np.concatenate([starts[1:], [n]])
    nc = len(starts)
    cnt = ends - starts
    cells = {d: keys[starts, j] for j, d in enumerate(CELL_DIMS)}
    dps_o = T["dps"][order].astype(np.int64)
    dth_o = T["deaths"][order].astype(np.int64)
    ch_o = T["char"][order].astype(np.int64)
    run_o = T["run"][order].astype(np.int64)
    day_o = T["day"][order].astype(np.int64)
    dsum = np.add.reduceat(dps_o, starts)
    dth = np.add.reduceat(dth_o, starts)
    dz = np.add.reduceat((dth_o == 0).astype(np.int64), starts)
    cell_id = np.repeat(np.arange(nc, dtype=np.int64), cnt)
    rmax = int(run_o.max()) + 1
    pair = np.unique(cell_id * rmax + run_o)
    nr = np.bincount(pair // rmax, minlength=nc)
    dmin = np.minimum.reduceat(day_o, starts)
    dmax = np.maximum.reduceat(day_o, starts)
    # Table B / C through pandas (sorted groupby == sorted int tuples)
    df = pd.DataFrame({d: T[d].astype(np.int64) for d in RL_DIMS})
    df["run"] = T["run"].astype(np.int64)
    g = df.groupby(list(RL_DIMS) + ["run"], sort=True).size().reset_index(name="c")
    g["dup"] = (g["c"] >= 2).astype(np.int64)
    rl = g.groupby(list(RL_DIMS), sort=True).agg(nr_rl=("c", "size"), dup_rl=("dup", "sum")).reset_index()
    rg = df[list(RG_DIMS) + ["run"]].drop_duplicates().groupby(list(RG_DIMS), sort=True).size() \
        .reset_index(name="nrun")
    # comps over clocked runs, in first-appearance (content) order
    keep = T["r_kdur"] > 0
    comp_codes, uniques = pd.factorize(pd.Series(T["r_comp"][keep]).astype(str), sort=False)
    rd = pd.DataFrame({"comp": comp_codes.astype(np.int64), "dun": T["r_dun"][keep].astype(np.int64),
                       "key": T["r_key"][keep].astype(np.int64), "reg": T["r_reg"][keep].astype(np.int64),
                       "timed": T["r_timed"][keep].astype(np.int64), "post": T["r_post"][keep].astype(np.int64),
                       "kdur": T["r_kdur"][keep].astype(np.int64), "deaths": T["r_deaths"][keep].astype(np.int64),
                       "day": T["r_day"][keep].astype(np.int64)})
    comp_dims = ["comp", "dun", "key", "reg", "timed", "post"]
    if len(rd):
        grp = rd.groupby(comp_dims, sort=True)
        cc = grp.agg(n=("kdur", "size"), ksum=("kdur", "sum"), dsum=("deaths", "sum"),
                     kmin=("kdur", "min"), best=("kdur", "idxmin")).reset_index()
        cc["bday"] = rd["day"].to_numpy()[cc["best"].to_numpy()]
        cc["bdeaths"] = rd["deaths"].to_numpy()[cc["best"].to_numpy()]
    else:
        cc = pd.DataFrame({k: np.zeros(0, dtype=np.int64) for k in comp_dims + ["n", "ksum", "dsum", "kmin", "bday", "bdeaths"]})
    comp_list = [tuple(int(x) for x in str(s).split(",") if x != "") for s in uniques]
    K = max((len(c) for c in comp_list), default=0)
    C = len(comp_list)
    cmat = np.full((K, C), 0xFFFF, dtype=np.int64)
    for j, comp in enumerate(comp_list):
        for i, code in enumerate(comp):
            cmat[i, j] = code
    clen = np.array([len(c) for c in comp_list], dtype=np.int64)
    out_dir = B.P.out / "cube"
    hdr = {"week": int(w), "cube_sha": cube_sha}
    cols_cells = [pf.Column(d, "u8", cells[d]) for d in ("reg", "cls", "spec", "hero", "role", "dun", "key")]
    cols_cells += [pf.Column(d, "i8", cells[d]) for d in ("timed", "post", "tb")]
    cols_cells += [pf.Column("n", "u32", cnt), pf.Column("dsum", "u64", dsum), pf.Column("dth", "u32", dth),
                   pf.Column("dz", "u32", dz), pf.Column("nr", "u32", nr),
                   pf.Column("dmin", "u16", dmin), pf.Column("dmax", "u16", dmax), pf.Column("doff", "u32", starts)]
    cols_cells += [pf.Column(f"rl_{d}", "u8", rl[d].to_numpy()) for d in ("cls", "spec", "dun", "key", "reg")]
    cols_cells += [pf.Column(f"rl_{d}", "i8", rl[d].to_numpy()) for d in ("timed", "post")]
    cols_cells += [pf.Column("nr_rl", "u32", rl["nr_rl"].to_numpy()), pf.Column("dup_rl", "u32", rl["dup_rl"].to_numpy())]
    cols_cells += [pf.Column(f"rg_{d}", "u8", rg[d].to_numpy()) for d in ("dun", "key", "reg")]
    cols_cells += [pf.Column(f"rg_{d}", "i8", rg[d].to_numpy()) for d in ("timed", "post")]
    cols_cells += [pf.Column("nrun", "u32", rg["nrun"].to_numpy())]
    wc = pf.write(out_dir, f"w{w}.cells", "cells", S.slug, nc, cols_cells,
                  dict(hdr, n_cells=int(nc), n_rl=int(len(rl)), n_rg=int(len(rg))))
    coff = np.concatenate([starts, [n]]).astype(np.int64)
    wd = pf.write(out_dir, f"w{w}.dist", "dist", S.slug, n,
                  [pf.Column("coff", "u32", coff), pf.Column("dps", "u32", dps_o, p=True, d=True),
                   pf.Column("deaths", "u8", dth_o, clamp=(0, 255))], dict(hdr))
    wh = pf.write(out_dir, f"w{w}.chars", "chars", S.slug, n, [pf.Column("char", "u32", ch_o, p=True)], dict(hdr))
    cols_comps = [pf.Column(f"c{i}", "u16", cmat[i]) for i in range(K)] + [pf.Column("clen", "u8", clen)]
    cols_comps += [pf.Column("comp", "u32", cc["comp"].to_numpy())]
    cols_comps += [pf.Column(d, "u8", cc[d].to_numpy()) for d in ("dun", "key", "reg")]
    cols_comps += [pf.Column(d, "i8", cc[d].to_numpy()) for d in ("timed", "post")]
    cols_comps += [pf.Column("n", "u32", cc["n"].to_numpy()), pf.Column("ksum", "u32", cc["ksum"].to_numpy()),
                   pf.Column("kmin", "u16", cc["kmin"].to_numpy(), clamp=(0, 65535)),
                   pf.Column("bday", "u16", cc["bday"].to_numpy()),
                   pf.Column("bdeaths", "u8", cc["bdeaths"].to_numpy(), clamp=(0, 255)),
                   pf.Column("dsum", "u32", cc["dsum"].to_numpy())]
    wm = pf.write(out_dir, f"w{w}.comps", "comps", S.slug, int(len(cc)), cols_comps,
                  dict(hdr, K=int(K), n_comps=int(C)))
    for wr in (wc, wd, wh, wm):
        B.health_lines.extend(pf.clamp_health_lines(f"cube/{wr.name}", wr.clamped))
    files = {"cells": f"cube/{wc.name}", "dist": f"cube/{wd.name}", "chars": f"cube/{wh.name}",
             "comps": f"cube/{wm.name}"}
    byts = {"cells": len(wc.gz), "dist": len(wd.gz), "chars": len(wh.gz), "comps": len(wm.gz)}
    B.health(f"parts.bytes.cube.w{w}={sum(byts.values())}:rows:{n}:cells:{nc}")
    return files, byts


def read_rio(path: pathlib.Path) -> dict:
    """player_scores() (B:315) against an explicit path."""
    import csv as _csv
    out: dict = {}
    if not path or not path.exists():
        return out
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        for row in _csv.reader(fh):
            if len(row) != 5:
                continue
            name, realm, region, score, _day = row
            try:
                v = float(score)
            except ValueError:
                continue
            if v > 0:
                out[f"{name}@{realm}@{region}"] = v
    return out


# ================================================================ CLI
def deadline_default(P: Paths) -> int:
    """PARTS_DEADLINE_S: the legacy builder's rolling 7-run median wall
    (from build.wall_s in site/build_health.txt, kept in a history file
    under parts/) minus 60 s, never below 120 s; 420 s when unknown."""
    hist = []
    if P.wall_hist.exists():
        hist = [float(x) for x in P.wall_hist.read_text().split() if x.strip()]
    if P.legacy_health.exists():
        for line in P.legacy_health.read_text(errors="replace").splitlines():
            if line.startswith("build.wall_s="):
                try:
                    v = float(line.split("=", 1)[1])
                except ValueError:
                    continue
                if not hist or hist[-1] != v:
                    hist.append(v)
    hist = hist[-20:]
    P.parts.mkdir(parents=True, exist_ok=True)
    P.wall_hist.write_text(" ".join(f"{v:.1f}" for v in hist) + ("\n" if hist else ""))
    med = statistics.median(hist[-7:]) if hist else DEFAULT_LEGACY_WALL_S
    return int(max(120, med - 60))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-root", default=str(ROOT / "data"))
    ap.add_argument("--site-dir", default=str(ROOT / "site"))
    ap.add_argument("--now", default=os.environ.get("WOWLOGS_NOW"))
    ap.add_argument("--deadline", type=float, default=None)
    ap.add_argument("--deadline-default", action="store_true")
    ap.add_argument("--pins", default=os.environ.get("WOWLOGS_PINS"))
    ap.add_argument("--daily", action="store_true")
    ap.add_argument("--max-days", type=int, default=MAX_DAYS_PER_RUN)
    ap.add_argument("--rebuild-all", action="store_true")
    ap.add_argument("--withhold-cubes", default=None,
                    help="'*' or a comma list of absolute weeks whose cube is not emitted (PARTS_WITHHOLD_CUBES)")
    a = ap.parse_args(argv)
    if a.deadline_default:
        season = Season(season_path(pathlib.Path(a.data_root)))
        print(deadline_default(Paths(pathlib.Path(a.data_root), pathlib.Path(a.site_dir), season.slug)))
        return 0
    now_ms = sc.parse_iso_ms(a.now) if a.now else None
    b = Builder(a.data_root, a.site_dir, now_ms=now_ms, deadline=a.deadline,
                pins_inject=pathlib.Path(a.pins) if a.pins else None, daily=a.daily,
                max_days=a.max_days, rebuild_all=a.rebuild_all, withhold_cubes=a.withhold_cubes)
    return b.run()


if __name__ == "__main__":
    sys.exit(main())
