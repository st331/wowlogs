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
     a day only when a stored value actually changes (absence = no change);
  2. rebuild dirty days, newest first, at most 8 per run: today's build()
     pipeline on one day's frame (dedup keep=last, overlay, keystone clock,
     the GLOBAL duplicate-upload collapse through the signature table --
     a loser in a neighbour day dirties that day --, hero resolution with
     the pinned markers, post/tmul with the pinned items + the current rule
     tables, tier against the pin), the content-deterministic sort (§2.2),
     the `rows` file, the per-(spec, day) shard blocks (§4.2), thin.npz
     (the cube partial, §3.2) and keys.npz; checkpoint state.json after
     every completed day;
  3. window-level, every run: spec/vocab, meta/specstats, meta/charscore
     (daily base + per-run delta), window.refchars / keys, per-week counts
     -- skipped when no window block changed;
  then the manifest (§2.6; seq advances only when something changed),
  out/ pruned to three generations, copied to site/d/<slug>/, and the
  health lines in data/processed/parts/health.txt.

State lives under data/processed/parts/<slug>/ (§6.1). season_pins.json
there is the authoritative pins file (§2.5): seeded on the first run,
injected from --pins / WOWLOGS_PINS in the equivalence tests, and changed
only through a recorded upgrade; a human edit of the git mirror is adopted
as one.

Stage C adds: freeze packing + cube emission (step 4), Release staging,
the reseed and the refuse-to-publish guard.
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
PAR_MIN_RUNS = 500
DAILY_SLOT_H = 20                 # a run this long after the last daily slot is one
DEFAULT_ESLOTS = [0, 4, 6, 7, 8, 10, 11, 14, 15, 16]
ROLE_RANK = {"Tank": 0, "Healer": 1, "DPS": 2}
JOURNAL_SHA_SPAN = 65536

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

    def route(self, code: str, fid: int) -> int | None:
        r = self.con.execute("SELECT day FROM runs WHERE code=? AND fid=?", (code, fid)).fetchone()
        return None if r is None else int(r[0])

    def add_run(self, code: str, fid: int, day: int, seq: int) -> None:
        self.con.execute("INSERT OR IGNORE INTO runs(code, fid, day, first_seen) VALUES(?,?,?,?)",
                         (code, fid, day, seq))

    def overlay_for(self, keys: list) -> dict:
        out = {}
        for i in range(0, len(keys), 400):
            chunk = keys[i:i + 400]
            q = " OR ".join("(code=? AND fid=?)" for _ in chunk)
            params = [x for k in chunk for x in k]
            for code, fid, score, medal, kms in self.con.execute(
                    f"SELECT code, fid, score, medal, kms FROM overlay WHERE {q}", params):
                out[(code, int(fid))] = (score, medal, kms)
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

    def snapshot_diff(self, triples: dict, seq: int) -> list:
        """Upsert the snapshot's (code,fid) -> (score, medal, kms) triples;
        return [(code, fid, day|None)] whose stored value changed (a None
        component in the snapshot is no information, never a change)."""
        c = self.con
        c.execute("CREATE TEMP TABLE IF NOT EXISTS snap(code TEXT, fid INTEGER, score REAL, medal TEXT, kms INTEGER)")
        c.execute("DELETE FROM snap")
        c.executemany("INSERT INTO snap VALUES(?,?,?,?,?)",
                      [(k[0], k[1], v[0], v[1], v[2]) for k, v in triples.items()])
        changed = c.execute(
            "SELECT s.code, s.fid, s.score, s.medal, s.kms, o.score, o.medal, o.kms, r.day "
            "FROM snap s LEFT JOIN overlay o ON o.code=s.code AND o.fid=s.fid "
            "LEFT JOIN runs r ON r.code=s.code AND r.fid=s.fid "
            "WHERE o.code IS NULL OR (s.score IS NOT NULL AND s.score IS NOT o.score) "
            "OR (s.medal IS NOT NULL AND s.medal IS NOT o.medal) "
            "OR (s.kms IS NOT NULL AND s.kms IS NOT o.kms)").fetchall()
        out = []
        for code, fid, ns, nm, nk, os_, om, ok, day in changed:
            score = ns if ns is not None else os_
            medal = nm if nm is not None else om
            kms = nk if nk is not None else ok
            c.execute("INSERT OR REPLACE INTO overlay(code, fid, score, medal, kms, first_seen) "
                      "VALUES(?,?,?,?,?, COALESCE((SELECT first_seen FROM overlay WHERE code=? AND fid=?), ?))",
                      (code, fid, score, medal, kms, code, fid, seq))
            out.append((code, int(fid), None if day is None else int(day)))
        c.execute("DELETE FROM snap")
        return out

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
                 rebuild_all: bool = False, log_fn=print):
        self.season = Season(season_path(pathlib.Path(data_root)))
        self.P = Paths(pathlib.Path(data_root), pathlib.Path(site_dir), self.season.slug)
        self.now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
        self.deadline = Deadline(deadline)
        self.pins_inject = pins_inject
        self.force_daily = daily
        self.max_days = max_days
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
        # a pin/rules/vocab change dirties every day (newest first, §6.4)
        inputs_static = self.static_inputs_sha()
        if self.st.d.get("static_inputs") != inputs_static or self.rebuild_all:
            reason = "rebuild_all" if self.rebuild_all else "pins" if pins.changed_keys else "rules"
            for key in list(self.st.d["days"]):
                self.st.mark_dirty(int(key) if key != "undated" else -1, reason)
            self.st.d["static_inputs"] = inputs_static
        if self.daily:
            self.st.d["daily"]["last"] = iso(self.now_ms)
        self._stage("pins", t0)

    def static_inputs_sha(self) -> str:
        return sha256_bytes((self.pins.inputs_material() + "|" + self.rules_sha + "|" + self.patch_id
                             + "|" + self.season.vocab_sha + "|" + str(pf.FORMAT_VERSION)).encode())

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
    def tail(self, name: str, path: pathlib.Path) -> list:
        """Records appended since the stored offset; a torn last line is not
        consumed; a rewritten journal (sha mismatch / seeded marker) is
        replayed from byte 0."""
        ent = self.st.d["journals"].setdefault(name, {})
        if not path.exists():
            return []
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
        records = []
        with open(path, "rb") as fh:
            fh.seek(off)
            data = fh.read()
        end = len(data)
        if end and not data.endswith(b"\n"):
            end = data.rfind(b"\n") + 1        # the torn last line stays unconsumed
        bad = 0
        for line in data[:end].split(b"\n"):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                bad += 1
        new_off = off + end
        with open(path, "rb") as fh:
            fh.seek(max(0, new_off - JOURNAL_SHA_SPAN))
            pre = fh.read(new_off - max(0, new_off - JOURNAL_SHA_SPAN))
        ent["offset"] = new_off
        ent["sha"] = sha256_bytes(pre)
        ent["seeded"] = marker_tag is not None
        if bad:
            self.health(f"parts.journal_bad_lines.{name}={bad}")
        return records

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

    def step1(self) -> None:
        t0 = time.perf_counter()
        seq = self.st.d["arrival_seq"]
        by_day: dict[int, list] = collections.defaultdict(list)
        players = self.tail("players", self.P.players)
        for rec in players:
            seq += 1
            day = self.day_of(rec.get("started_at"))
            code, fid = ustr(rec.get("report_code")), int(rec.get("fight_id") or 0)
            rec["_cid"] = self.reg.get_or_assign(self.char_key(rec))
            rec["_seq"] = seq
            self.db.add_run(code, fid, day, seq)
            by_day[day].append(rec)
        # gear / abilities: routed through the run table; unknown runs park
        pend_g = read_jsonl(self.P.pending / "gear.jsonl")
        pend_a = read_jsonl(self.P.pending / "abil.jsonl")
        gear_new = pend_g + self.tail("gear", self.P.gear)
        abil_new = pend_a + self.tail("abilities", self.P.abil)
        gear_by: dict[int, list] = collections.defaultdict(list)
        abil_by: dict[int, list] = collections.defaultdict(list)
        park_g, park_a = [], []
        route_cache: dict = {}

        def route(code, fid):
            k = (code, fid)
            if k not in route_cache:
                route_cache[k] = self.db.route(code, fid)
            return route_cache[k]
        for rec in gear_new:
            d = route(ustr(rec.get("report_code")), int(rec.get("fight_id") or 0))
            if d is None:
                rec.setdefault("_parked", iso(self.now_ms))
                park_g.append(rec)
            else:
                rec.pop("_parked", None)
                gear_by[d].append(rec)
        for rec in abil_new:
            d = route(ustr(rec.get("report_code")), int(rec.get("fight_id") or 0))
            if d is None:
                rec.setdefault("_parked", iso(self.now_ms))
                park_a.append(rec)
            else:
                rec.pop("_parked", None)
                abil_by[d].append(rec)
        cutoff = self.now_ms - PENDING_DAYS * DAY_MS
        for name, lst in (("gear.jsonl", park_g), ("abil.jsonl", park_a)):
            keep = [r for r in lst if sc.parse_iso_ms(r["_parked"]) >= cutoff]
            p = self.P.pending / name
            if p.exists():
                p.unlink()
            append_jsonl(p, keep)
            if len(lst) - len(keep):
                self.health(f"parts.pending_expired.{name.split('.')[0]}={len(lst) - len(keep)}")
        touched = set(by_day) | set(gear_by) | set(abil_by)
        for d in touched:
            append_jsonl(self.P.day_dir(d) / "pending_players.jsonl", by_day.get(d, []))
            append_jsonl(self.P.day_dir(d) / "pending_gear.jsonl", gear_by.get(d, []))
            append_jsonl(self.P.day_dir(d) / "pending_abil.jsonl", abil_by.get(d, []))
            self.st.mark_dirty(d, "arrival")
            self.st.day(d)["last_arrival"] = iso(self.now_ms)
        self.st.d["arrival_seq"] = seq
        self.health(f"parts.tail.players={len(players)}")
        self.health(f"parts.tail.gear={len(gear_new) - len(pend_g)}")
        self.health(f"parts.tail.abilities={len(abil_new) - len(pend_a)}")
        self.health(f"parts.pending.gear={len(park_g)}")
        self.health(f"parts.pending.abilities={len(park_a)}")
        self.health(f"parts.chars_new={len(self.reg.new_order)}")
        self._stage("tail", t0)
        t0 = time.perf_counter()
        self.rankings_snapshot()
        self._stage("rankings", t0)
        self.db.commit()

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
        changed = self.db.snapshot_diff(triples, self.st.d["arrival_seq"])
        dirty_days = set()
        for code, fid, day in changed:
            if day is not None:
                dirty_days.add(day)
        for d in dirty_days:
            self.st.mark_dirty(d, "overlay")
        self.st.d["rankings"]["snapshot_sha"] = sha
        self.health(f"parts.rankings=parsed:{len(triples)}:changed:{len(changed)}:days:{len(dirty_days)}")

    # ---- step 2: day rebuild --------------------------------------------
    def dirty_days(self) -> list:
        """Newest first; a pending neighbour collapse right after today."""
        days = [int(k) for k, e in self.st.d["days"].items() if e.get("dirty") and k != "undated"]
        days.sort(reverse=True)
        pend = [d for d in days if "collapse" in self.st.d["days"][str(d)]["reasons"]]
        if pend and days:
            first = days[0]
            rest = [d for d in days if d != first and d not in pend]
            pend = [d for d in pend if d != first]
            days = [first] + sorted(pend, reverse=True) + rest
        return days

    def step2(self) -> None:
        t0 = time.perf_counter()
        queue = self.dirty_days()
        self.dirty_found = len(queue)
        done = 0
        i = 0
        while i < len(queue) and done < self.max_days:
            if self.deadline.reached():
                self.health("parts.deadline_hit=1")
                break
            d = queue[i]
            i += 1
            self.build_day(d)
            done += 1
            self.st.d["char_registry_size"] = self.reg.total
            self.db.commit()
            self.reg.flush()
            self.st.save()
            # a collapse may have queued a neighbour: re-derive the queue
            newq = self.dirty_days()
            if newq != queue[i:]:
                queue = queue[:i] + [x for x in newq if x not in queue[:i]]
        left = [d for d in queue[i:] if self.st.d["days"][str(d)].get("dirty")]
        self.health(f"parts.dirty_days={self.dirty_found}")
        self.health(f"parts.rebuilt_days={done}")
        self.health(f"parts.days_left={len(left)}")
        self._stage("days", t0)

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
                osc, omd, okm = o
                score_ov.append(osc if osc is not None else s0)
                medal_ov.append(omd if omd is not None else m0)
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
        return gear_cache_from_records(recs + pend)

    def _abil_cache(self, day: int) -> dict:
        dd = self.P.day_dir(day)
        cache = load_npz(dd / "abil.npz")
        pend = read_jsonl(dd / "pending_abil.jsonl")
        if not pend:
            return cache if cache is not None else empty_abil_cache()
        # every record in arrival order (hero resolution takes the last
        # record with a non-empty breakdown, project() the last record)
        recs = abil_records_from_cache(cache) if cache is not None else []
        return abil_cache_from_records(recs + pend)

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
        # persist the canonical caches (the pending files are consumed)
        save_npz(dd / "raw.npz", frame_to_raw(df))
        save_npz(dd / "gear.npz", gear)
        save_npz(dd / "abil.npz", abil)
        for name in ("pending_players.jsonl", "pending_gear.jsonl", "pending_abil.jsonl"):
            p = dd / name
            if p.exists():
                p.unlink()
        if not len(df):
            for k in ("f", "rows_sha"):
                st_day[k] = None
            st_day.update({"n": 0, "runs": 0, "dirty": False, "reasons": [], "specs": {}, "w": {}, "b": 0,
                           "built_seq": self.st.d["seq"] + 1})
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
                       "weeks": sorted(out["weeks"]), "dirty": False, "reasons": [],
                       "built_seq": self.st.d["seq"] + 1, "bytes": out["bytes"]})
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

    def step3(self) -> dict:
        t0 = time.perf_counter()
        days_all = sorted(int(k) for k, e in self.st.d["days"].items()
                          if k != "undated" and e.get("n") and e.get("f"))
        listed = self.listed_days(days_all)
        undated = self.st.d["days"].get("-1") if "-1" in self.st.d["days"] else None
        # frozen flags (§6.2-4 rule; packing and cubes are stage C)
        for d in days_all:
            e = self.st.d["days"][str(d)]
            la = e.get("last_arrival")
            quiet = la is None or (self.now_ms - sc.parse_iso_ms(la)) >= FREEZE_QUIET_H * 3_600_000
            aged = self.season.epoch_ms + (d + 1) * DAY_MS + FREEZE_AGE_D * DAY_MS < self.now_ms
            e["frozen"] = bool(not e.get("dirty") and (quiet or aged))
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
        days_all = sorted(int(k) for k, e in self.st.d["days"].items()
                          if k != "undated" and e.get("n") and e.get("f"))
        listed = art.get("listed") or self.listed_days(days_all)
        days_out = []
        for d in listed:
            e = self.st.d["days"][str(d)]
            days_out.append({"d": d, "n": e["n"], "runs": e["runs"], "frozen": bool(e.get("frozen")),
                             "w": e.get("w") or {}, "f": e["f"], "b": e["b"], "rules_sha": e["rules_sha"],
                             "specs": dict(sorted(e["specs"].items(), key=lambda kv: int(kv[0])))})
        und = self.st.d["days"].get("-1")
        days_out.append({"d": "undated", "n": und["n"] if und and und.get("f") else 0,
                         "runs": und["runs"] if und and und.get("f") else 0, "frozen": True,
                         "f": und["f"] if und else None, "b": und["b"] if und and und.get("f") else 0,
                         "rules_sha": und["rules_sha"] if und else None,
                         "specs": dict(und["specs"]) if und and und.get("f") else {}})
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
        proj = self.st.d["projection"]
        projection = None
        if proj.get("has_tmul") and proj.get("meta"):
            projection = dict(proj["meta"], rules_sha=self.rules_sha)
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
        write_atomic(self.P.out / "manifest.json", json.dumps(man, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
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
        for rel, src in want.items():
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
            self.prepare_pins()
            self.step1()
            self.reg.flush()
            self.st.d["char_registry_size"] = self.reg.total
            self.st.save()
            self.step2()
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
            "server_null": np.zeros(n, dtype=bool)}


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
                "server_null": server_null})
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
            "atot": np.zeros(0, dtype=np.int64), "auses": np.zeros(0, dtype=np.int64)}


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
            "auses": np.array(auses, dtype=np.int64)}


def abil_records_from_cache(c: dict) -> list:
    out = []
    names = c["anames"]
    for i in range(len(c["code"])):
        a, b = int(c["aoff"][i]), int(c["aoff"][i + 1])
        out.append({"report_code": str(c["code"][i]), "fight_id": int(c["fid"][i]), "name": str(c["name"][i]),
                    "class": str(c["cls"][i]), "total": int(c["total"][i]), "ilvl": int(c["ilvl"][i]),
                    "sets": json.loads(str(c["sets"][i])),
                    "abilities": [{"name": str(names[c["aid"][j]]), "total": int(c["atot"][j]),
                                   "uses": int(c["auses"][j])} for j in range(a, b)]})
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
    # inputs_sha (§6.3): canonical rows ‖ gear ‖ abil ‖ FORMAT ‖ pins ‖ rules ‖ patch ‖ vocab
    canon_rows = hashlib.sha256()
    for tup in zip(df["report_code"], df["fight_id"], df["character"], df["server"], df["region"], df["class"],
                   df["spec"], df["hero_talent"], df["role"], df["dungeon"], key, df["duration_s"], df["damage_done"],
                   df["dps"], deaths, df["score_ov"], df["medal_ov"], st_ms, df["set_counts"], df["set_counts_null"],
                   char, df["keystone_s"]):
        canon_rows.update(repr(tuple(x.item() if hasattr(x, "item") else x for x in tup)).encode())
        canon_rows.update(b"\n")
    inputs_sha = sha256_bytes("|".join([
        canon_rows.hexdigest(), arrays_digest(gear), arrays_digest(abil), str(pf.FORMAT_VERSION),
        B.pins.inputs_material(), B.rules_sha, B.patch_id, S.vocab_sha]).encode())
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
    # ---- thin.npz (the cube partial, §3.2) and keys.npz
    reg_names = df["region"].tolist()
    W = np.where(st_ms >= 0, S.week_of_ms(np.where(st_ms >= 0, st_ms, 0), reg_names), -10 ** 6)
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
        "started_at": st_ms, "char_id": char})
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
            "weeks": sorted(weeks), "bytes": byts}


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
    # ---- the legacy-shaped window frame + journals for vocab / specstats
    frames, sets_all, stats_all, meta_all = [], {}, {}, {}
    traits: dict = {}
    wearers: dict = collections.defaultdict(lambda: collections.defaultdict(set))
    blob_of: dict = {}
    for d in listed:
        raw = load_npz(P.day_dir(d) / "raw.npz")
        kz = load_npz(P.day_dir(d) / "keys.npz")
        if raw is None or kz is None:
            continue
        # rows in the day file's order: keys.npz carries it; raw fields by key
        byk = {}
        for i in range(len(raw["report_code"])):
            byk[(str(raw["report_code"][i]), int(raw["fight_id"][i]), str(raw["character"][i]), str(raw["server"][i]))] = i
        idx = np.array([byk[(str(c), int(f), str(ch), str(sv))] for c, f, ch, sv in
                        zip(kz["code"], kz["fid"], kz["character"], kz["server"])], dtype=np.int64)
        cls_n = np.where(raw["class"][idx] == "", "Unknown", raw["class"][idx])
        spec_n = np.where(raw["spec"][idx] == "", "Unknown", raw["spec"][idx])
        reg_n = np.where(raw["region"][idx] == "", "Unknown", raw["region"][idx])
        frames.append(pd.DataFrame({
            "report_code": kz["code"].astype(str), "fight_id": kz["fid"].astype(int),
            "character": kz["character"].astype(str), "server": kz["server"].astype(str),
            "class": cls_n, "spec": spec_n, "region": reg_n, "key_level": raw["key_level"][idx],
            "started_at": raw["started_at"][idx], "medal": raw["medal_ov"][idx],
            "keystone_s": raw["keystone_s"][idx], "dungeon": raw["dungeon"][idx], "seq": raw["seq"][idx]}))
        g = load_npz(P.day_dir(d) / "gear.npz")
        if g is None or not len(g["code"]):
            continue
        s_, st_, m_ = gear_meta_journal(g)
        sets_all.update(s_)
        stats_all.update(st_)
        meta_all.update(m_)
        for i in range(len(g["code"])):
            sk = f"{g['cls'][i]}|{g['spec'][i]}"
            o = traits.setdefault(sk, {"specid": collections.Counter(), "entries": set(), "sel": {}})
            if g["specid"][i]:
                o["specid"][int(g["specid"][i])] += 1
            bl = str(g["blob"][i])
            bd = str(g["build"][i])
            if bl:
                for part in bl.split("|"):
                    o["entries"].add(int(part.split(":", 1)[0]))
                o["sel"].setdefault(bd, collections.Counter())[bl] += 1
            if bd:
                blob_of[(sk, bd)] = bl
    # in journal ARRIVAL order, which is the legacy df's order: builds_sidecar
    # takes a wearer's item level from the first row it sees (B:1720), so the
    # vocab is only entry-for-entry equal when the rows come in that order
    df_w = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["report_code", "fight_id", "character", "server", "class", "spec", "region", "key_level",
                 "started_at", "medal", "keystone_s", "dungeon", "seq"])
    if len(df_w):
        df_w = df_w.sort_values("seq", kind="stable").reset_index(drop=True)
    # pars for un-pinned dungeons (§5): derive_pars over the window once
    # a dungeon has >= 500 runs with both outcomes
    pins_pars = B.pins.doc.setdefault("pars", {})
    missing = [dn for dn in S.vocab["dungeons"] if dn != "Unknown" and not pins_pars.get(dn)]
    if missing and len(df_w):
        sub = df_w[df_w["dungeon"].isin(missing)]
        per_run = sub.drop_duplicates(["report_code", "fight_id"])
        derived = dict(zip(missing, bsd.derive_pars(sub.assign(medal=sub["medal"]), missing))) if len(sub) else {}
        for dn in missing:
            g = per_run[per_run["dungeon"] == dn]
            ks_ok = pd.to_numeric(g["keystone_s"], errors="coerce").notna()
            timed_ok = g["medal"].isin(["timed", "gold", "silver", "bronze"])
            if ks_ok.sum() >= PAR_MIN_RUNS and timed_ok[ks_ok].nunique() == 2 and derived.get(dn):
                pins_pars[dn] = int(derived[dn])
                B.pins.upgrade(f"pars.{dn}", None, int(derived[dn]), "derive")
        if B.pins.changed_keys:
            B.pins.save()
    # ---- spec/vocab (§4.3): the legacy specs object over the window
    _items, _enchs, crafted, embc, markers, _icons = B.emb_cfg or bsd._name_caches()
    B.emb_cfg = B.emb_cfg or (_items, _enchs, crafted, embc, markers, _icons)
    art["emb"] = emb_labels(embc)
    vocab_doc = None
    if meta_all and len(df_w):
        doc = bsd.builds_sidecar(df_w, meta_all, "parts", enc="sparse", target=10 ** 12, cap=10 ** 12,
                                 traits=traits)
        if doc:
            j = json.loads(doc)
            specs_v = j["specs"]
            pinned_es = [int(x) for x in (B.pins.doc.get("eslots") or DEFAULT_ESLOTS)]
            es = [s for s in j["eslots"] if s in pinned_es]
            keep_idx = [i for i, s in enumerate(j["eslots"]) if s in pinned_es]
            # per-entry `w` (distinct wearers per (spec, slot, id, emb
            # label), from the window frame) and per-build `h` (hash64)
            emb_of = emb_of_factory(embc, markers, crafted)
            who = {}
            for c, f, ch, sv, cl, sp in zip(df_w["report_code"], df_w["fight_id"], df_w["character"], df_w["server"],
                                            df_w["class"], df_w["spec"]):
                k = bsd._gear_key(c, int(f), ch if ch != "" else None, sv if sv != "" else None)
                rec = meta_all.get(k)
                if rec is None or not isinstance(rec.get("gear"), list):
                    continue
                sk = f"{cl}|{sp}"
                for si, s in enumerate(bsd.BUILDS_SLOTS):
                    it = rec["gear"][s] if s < len(rec["gear"]) else None
                    if isinstance(it, dict) and it.get("id"):
                        e = emb_of(it["id"], it.get("bonus") if isinstance(it.get("bonus"), list) else [])
                        lab = None if e is None else (embc["names"].get(e) or "embellished")
                        wearers[(sk, si)][(it["id"], lab)].add((ch, sv))
            for sk, entry in specs_v.items():
                for si, col in enumerate(entry["items"]):
                    for e in col:
                        e["w"] = len(wearers[(sk, si)].get((e["id"], e.get("emb")), ()))
                if "ench" in entry:
                    entry["ench"] = [entry["ench"][i] for i in keep_idx]
                for b in entry.get("builds", []):
                    b["h"] = "%016x" % build_hash64(b["s"], blob_of.get((sk, b["s"]), ""))
            vocab_doc = {"v": 1, "slots": j["slots"], "eslots": es, "specs": specs_v}
    if vocab_doc is not None:
        body = gz_json(vocab_doc)
        name, _ = hashed_write(P.out / "spec", "vocab", "json.gz", body)
        art["spec_vocab"] = {"f": f"spec/{name}", "b": len(body)}
    else:
        art["spec_vocab"] = None
    B._stage("vocab", t0)
    t0 = time.perf_counter()
    # ---- specstats (B:602), the same code over the window frame
    art["specstats"] = None
    if stats_all and len(df_w):
        started = pd.to_datetime(pd.to_numeric(df_w["started_at"], errors="coerce"), unit="ms", errors="coerce")
        timed = df_w["medal"].map(bsd.MEDAL_TIMED).fillna(-1).astype(int)
        block = bsd.spec_stats_block(df_w, started, timed, "parts", journal=stats_all)
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
    a = ap.parse_args(argv)
    if a.deadline_default:
        season = Season(season_path(pathlib.Path(a.data_root)))
        print(deadline_default(Paths(pathlib.Path(a.data_root), pathlib.Path(a.site_dir), season.slug)))
        return 0
    now_ms = sc.parse_iso_ms(a.now) if a.now else None
    b = Builder(a.data_root, a.site_dir, now_ms=now_ms, deadline=a.deadline,
                pins_inject=pathlib.Path(a.pins) if a.pins else None, daily=a.daily,
                max_days=a.max_days, rebuild_all=a.rebuild_all)
    return b.run()


if __name__ == "__main__":
    sys.exit(main())
