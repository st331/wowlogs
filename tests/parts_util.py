"""Shared harness for the §9.1 equivalence tests: the legacy `build()` and
the partition builder run over the SAME fixture journals in a scratch
repo root, both under the frozen clock and the fixture's pins.

  legacy_root()  -> a root where scripts/build_site_data.build() ran with
                    MAX_RUNS=0, WOWLOGS_PINS=<fixture pins>, WOWLOGS_NOW;
                    site/data.json(.gz), stats/builds/talents.json.gz
  parts_root()   -> a root where partition_build ran once over the eight
                    chunks concatenated (rankings = the final snapshot),
                    --pins <fixture pins>; site/d/ + the parts state

Both are cached under data/processed/fixtures/eq/ keyed by the fixture
parameters and the sha of the builder sources, so a test file re-run costs
seconds, and are rebuilt whenever a source changes.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
from fixture_util import FIXTURE_DIR, fixture               # noqa: E402

CHUNKS = 8


def _stamp(*paths) -> str:
    h = hashlib.sha1()
    for p in paths:
        h.update(pathlib.Path(p).read_bytes())
    return h.hexdigest()[:16]


def concat_journals(dst_root: pathlib.Path, upto: int = CHUNKS) -> None:
    """players/gear -> data/processed, abilities -> data/raw, rankings =
    the LAST chunk's full snapshot (the journal is rewritten every run)."""
    raw, proc = dst_root / "data" / "raw", dst_root / "data" / "processed"
    raw.mkdir(parents=True, exist_ok=True)
    proc.mkdir(parents=True, exist_ok=True)
    for name, out in (("players.jsonl", proc / "players.jsonl"), ("gear.jsonl", proc / "gear.jsonl"),
                      ("abilities.jsonl", raw / "abilities.jsonl")):
        with open(out, "wb") as fh:
            for k in range(1, upto + 1):
                src = FIXTURE_DIR / "chunks" / f"{k:02d}" / name
                if src.exists():
                    fh.write(src.read_bytes())
    shutil.copyfile(FIXTURE_DIR / "chunks" / f"{upto:02d}" / "rankings.jsonl", raw / "rankings.jsonl")


def common_root(dst_root: pathlib.Path) -> None:
    d = dst_root / "data"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE_DIR / "tuning_patches.json", d / "tuning_patches.json")
    shutil.copyfile(FIXTURE_DIR / "legacy" / "rio_scores.csv.gz", d / "rio_scores.csv.gz")
    shutil.copyfile(FIXTURE_DIR / "legacy" / "keystone_times.json", d / "keystone_times.json")
    (d / "processed").mkdir(exist_ok=True)
    shutil.copyfile(FIXTURE_DIR / "legacy" / "rio_scores.csv.gz", d / "processed" / "rio_scores.csv.gz")
    shutil.copyfile(ROOT / "data" / "season.json", d / "season.json")
    shutil.copyfile(ROOT / "data" / "keystone_pars.json", d / "keystone_pars.json")
    shutil.copyfile(ROOT / "data" / "hero_talent_map.json", d / "hero_talent_map.json")


def legacy_root(rebuild: bool = False) -> pathlib.Path:
    fx = fixture()
    root = FIXTURE_DIR / "legacy_build"
    stamp = _stamp(FIXTURE_DIR / "fixture.json", ROOT / "scripts" / "build_site_data.py",
                   ROOT / "scripts" / "project_tuning.py")
    sf = root / "stamp.txt"
    if not rebuild and sf.exists() and sf.read_text().strip() == stamp and ((root / "site" / "data.json.gz").exists() or (root / "site" / "data.json").exists()):
        return root
    if root.exists():
        shutil.rmtree(root)
    common_root(root)
    concat_journals(root)
    shutil.copyfile(FIXTURE_DIR / "legacy" / "mythic_runs.csv.gz", root / "data" / "mythic_runs.csv.gz")
    (root / "site").mkdir(exist_ok=True)
    (root / "docs").mkdir(exist_ok=True)
    env = dict(os.environ, WOWLOGS_PINS=str(FIXTURE_DIR / "pins.json"), WOWLOGS_NOW=fx["now"],
               BUILD_LLMS="0")
    code = f"""
import sys, pathlib
sys.path.insert(0, {str(ROOT / 'scripts')!r})
import build_site_data as bsd, project_tuning as pt
R = pathlib.Path({str(root)!r})
bsd.ROOT = R
bsd.SITE_DIRS = [R / 'site', R / 'docs']
bsd.TUNING_FILE = R / 'data' / 'tuning_patches.json'
bsd.GEAR_JOURNAL = R / 'data' / 'processed' / 'gear.jsonl'
bsd.GEAR_EXPORT = R / 'data' / 'gear.jsonl.gz'
bsd.RIO_FILE = R / 'data' / 'processed' / 'rio_scores.csv.gz'
bsd.RIO_SEED = R / 'data' / 'rio_scores.csv.gz'
bsd.TRAIT_UNION = R / 'data' / 'processed' / 'trait_union.json.gz'
bsd.STAMP_FILE = R / 'data' / '.build_stamp'
bsd.ICONS_SRC = R / 'data' / 'processed' / 'icons'
bsd.MAX_RUNS = 0
pt.ABIL = R / 'data' / 'raw' / 'abilities.jsonl'
bsd.build('season', bsd.SEASON)
"""
    subprocess.run([sys.executable, "-c", code], check=True, env=env, timeout=1800)
    sf.write_text(stamp)
    return root


def parts_root(rebuild: bool = False) -> pathlib.Path:
    fx = fixture()
    root = FIXTURE_DIR / "parts_run"
    stamp = _stamp(FIXTURE_DIR / "fixture.json", ROOT / "scripts" / "partition_build.py",
                   ROOT / "scripts" / "partition_format.py", ROOT / "scripts" / "partition_client.py",
                   ROOT / "scripts" / "project_tuning.py", ROOT / "data" / "season.json")
    sf = root / "stamp.txt"
    if not rebuild and sf.exists() and sf.read_text().strip() == stamp and (root / "site" / "d" / "current.json").exists():
        return root
    if root.exists():
        shutil.rmtree(root)
    common_root(root)
    concat_journals(root)
    run_parts(root, fx["now"], pins=FIXTURE_DIR / "pins.json", max_days=400)
    sf.write_text(stamp)
    return root


def run_parts(root: pathlib.Path, now: str, pins=None, max_days: int = 400, extra=(), env_extra=None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("WOWLOGS_PINS", None)
    if env_extra:
        env.update(env_extra)
    args = [sys.executable, str(ROOT / "scripts" / "partition_build.py"), "--data-root", str(root / "data"),
            "--site-dir", str(root / "site"), "--now", now, "--max-days", str(max_days)]
    if pins:
        args += ["--pins", str(pins)]
    args += list(extra)
    r = subprocess.run(args, env=env, capture_output=True, text=True, timeout=1800)
    if r.returncode:
        print(r.stdout[-4000:])
        print(r.stderr[-4000:])
        raise RuntimeError("partition_build failed")
    return r


def legacy_payload(root: pathlib.Path) -> dict:
    gz = root / "site" / "data.json.gz"
    if gz.exists():
        with gzip.open(gz, "rt") as fh:
            return json.load(fh)
    return json.loads((root / "site" / "data.json").read_text())


def legacy_keys(root: pathlib.Path) -> pd.DataFrame:
    """The (report_code, fight_id, character, server, region) of every
    legacy payload row, in row order (build() reads the CSV in order)."""
    df = pd.read_csv(root / "data" / "mythic_runs.csv.gz",
                     usecols=["report_code", "fight_id", "character", "server", "region"])
    return df


def parts_health(root: pathlib.Path) -> dict:
    out = {}
    for line in (root / "data" / "processed" / "parts" / "health.txt").read_text().splitlines():
        k, _, v = line.partition("=")
        out.setdefault(k, []).append(v)
    return out
