#!/usr/bin/env python3
"""tests/test_partition_build_rerun.py (partitioned_payload.md §6.3)

(Every cube withheld, like parts_root: the cubed path is test_cube_equivalence
and test_incremental_idempotent.)

Idempotence and determinism of the stage-B builder on the §9 fixture:
a rerun over unchanged journals writes nothing new (seq does not advance,
zero dirty days, the manifest is byte-identical); and a two-step run
(chunks 1-2, then 3-8, rankings as the per-run snapshot) reproduces the
one-shot run's day files, shard blocks, vocab, charscore, specstats,
window block, week counts and character registry byte for byte -- with
the cross-day duplicate collapse recorded on the neighbour day.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import parts_util as pu                                          # noqa: E402
from fixture_util import FIXTURE_DIR, fixture                    # noqa: E402


def _manifest(root):
    return json.loads((root / "site" / "d" / "s2" / "manifest.json").read_text())


def test_rerun_is_a_noop():
    fx = fixture()
    root = pu.parts_root()
    before = (root / "site" / "d" / "s2" / "manifest.json").read_bytes()
    pu.run_parts(root, fx["now"], pins=FIXTURE_DIR / "pins.json", extra=["--withhold-cubes", "*"])
    h = pu.parts_health(root)
    assert (root / "site" / "d" / "s2" / "manifest.json").read_bytes() == before
    assert h["parts.dirty_days"] == ["0"] and h["parts.rebuilt_days"] == ["0"]
    assert h["parts.rankings"] == ["unchanged"] and h["parts.manifest"] == ["unchanged"]
    assert h["parts.chars_new"] == ["0"] and h["parts.status"] == ["ok"]
    print("rerun: no-op, seq", _manifest(root)["seq"])


def test_two_step_matches_one_shot():
    fx = fixture()
    one = _manifest(pu.parts_root())
    inc = FIXTURE_DIR / "parts_inc"
    if inc.exists():
        shutil.rmtree(inc)
    pu.common_root(inc)
    pu.concat_journals(inc, upto=2)
    pu.run_parts(inc, fx["chunks"][1]["now"], pins=FIXTURE_DIR / "pins.json", extra=["--withhold-cubes", "*"])
    m1 = _manifest(inc)
    assert m1["seq"] == 1 and m1["window"]["rows"] < one["window"]["rows"]
    pu.concat_journals(inc, upto=8)
    pu.run_parts(inc, fx["now"], pins=FIXTURE_DIR / "pins.json", extra=["--withhold-cubes", "*"])
    h = pu.parts_health(inc)
    two = _manifest(inc)
    assert two["seq"] == 2
    # the midnight duplicate's twin sits in a day built in step 1: the copy
    # arriving in step 2 dirtied that neighbour day and the loser is gone
    twin_day = fx["notes"]["dup_pair"]["twin"][2]
    assert any(l.startswith(f"{twin_day}:") for l in h.get("parts.collapse.neighbour", [])), h.get("parts.collapse.neighbour")
    assert h["parts.invalidated_days"][0].startswith("1/")
    for k in ("weeks", "window", "spec_vocab", "specstats", "char_max", "emb", "pars", "flags", "tuning"):
        assert one[k] == two[k], k
    # `frozen` follows the arrival history (72 h quiescence, §6.2-4): the
    # two-step run saw its step-1 days go quiet, the one-shot run did not.
    # Everything else about a day is a pure function of its inputs.
    strip = lambda e: {k: v for k, v in e.items() if k != "frozen"}
    assert [strip(e) for e in one["days"]] == [strip(e) for e in two["days"]]
    aged = {e["d"] for e in one["days"] if e["frozen"]}
    assert aged <= {e["d"] for e in two["days"] if e["frozen"]}
    assert one["charscore"]["f"] == two["charscore"]["f"]
    reg = "data/processed/parts/s2/ids/chars.bin"
    assert (pu.parts_root() / reg).read_bytes() == (inc / reg).read_bytes()
    print(f"two-step == one-shot: {len(two['days'])} day entries, seq {two['seq']}")


def test_rebuild_all_rebuilds_every_day_in_one_run():
    """refresh.yml's rebuild_all dispatch runs the builder WITHOUT --max-days
    (the workflow provisions a 110-minute job and PARTS_DEADLINE_S=5400 for
    it): --rebuild-all must lift the per-run cap and rebuild every day in
    the one run, dirtying nothing for later cycles, and reproduce the same
    files (nothing but `frozen` may differ)."""
    fx = fixture()
    src = pu.parts_root()
    ra = FIXTURE_DIR / "parts_rebuild_all"
    if ra.exists():
        shutil.rmtree(ra)
    shutil.copytree(src, ra)
    before = _manifest(ra)
    args = [sys.executable, str(ROOT / "scripts" / "partition_build.py"), "--data-root", str(ra / "data"),
            "--site-dir", str(ra / "site"), "--now", fx["now"], "--rebuild-all", "--withhold-cubes", "*"]
    env = dict(os.environ, REBUILD_ALL="true")
    env.pop("WOWLOGS_PINS", None)
    env.pop("PARTS_MAX_DAYS", None)
    r = subprocess.run(args, env=env, capture_output=True, text=True, timeout=1800)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    h = pu.parts_health(ra)
    n_days = sum(1 for e in before["days"] if e.get("f"))
    assert h["parts.dirty_days"] == [str(n_days)], (h["parts.dirty_days"], n_days)
    assert h["parts.rebuilt_days"] == [str(n_days)], (h["parts.rebuilt_days"], n_days)
    assert h["parts.days_left"] == ["0"], h["parts.days_left"]
    order = [int(x) for x in h["parts.rebuilt_order"][0].split(",") if x]
    assert order[0] == max(order) and order[-1] == -1 and len(order) == n_days, order
    after = _manifest(ra)
    strip = lambda e: {k: v for k, v in e.items() if k != "frozen"}
    assert [strip(e) for e in after["days"]] == [strip(e) for e in before["days"]]
    assert after["seq"] == before["seq"], "an identical rebuild must not advance seq"
    print(f"rebuild-all: {n_days} days in one run, manifest unchanged")


if __name__ == "__main__":
    test_rerun_is_a_noop()
    test_two_step_matches_one_shot()
    test_rebuild_all_rebuilds_every_day_in_one_run()
    print("test_partition_build_rerun: all green")
